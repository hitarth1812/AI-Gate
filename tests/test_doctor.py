import json
import ssl
import subprocess
import sys
import time
import urllib.error

from conftest import SCRIPT, aigate

STATUSES = ("fail", "warn", "ok")


def doctor(repo, *args, env=None):
    proc = repo.run("doctor", "--offline", "--format", "json", *args, env=env)
    assert proc.returncode in (0, 1), proc.stderr + proc.stdout
    checks = json.loads(proc.stdout)
    return proc.returncode, {c["id"]: c for c in checks}, checks


def in_process(repo, monkeypatch, offline=False, fetch=None, env=None):
    monkeypatch.chdir(repo.path)
    return {c["id"]: c for c in aigate.doctor_report({**repo.env, **(env or {})}, offline=offline, fetch=fetch)}


def never(url, timeout):
    raise AssertionError(f"network used: {url}")


def test_fresh_repo_without_hooks_fails(repo):
    code, by_id, _ = doctor(repo)
    assert code == 1 and by_id["hooks"]["status"] == "fail" and "aigate-lite install" in by_id["hooks"]["fix"]


def test_after_install_hooks_are_ok(repo):
    assert repo.run("install").returncode == 0
    code, by_id, _ = doctor(repo)
    assert by_id["hooks"]["status"] == "ok", by_id["hooks"]
    assert code == (1 if any(c["status"] == "fail" for c in by_id.values()) else 0)
    assert by_id["python.hook"]["status"] == "ok" and "pinned" in by_id["python.hook"]["detail"]


def test_invalid_toml_is_a_config_failure_with_the_reason(repo):
    repo.write(".aigate/lite.toml", "[gate\n")
    _, by_id, _ = doctor(repo)
    assert by_id["config"]["status"] == "fail" and "not valid TOML" in by_id["config"]["detail"]


def test_unknown_key_is_a_config_warning(repo):
    repo.write(".aigate/lite.toml", "[gate]\nbogus = 1\n")
    _, by_id, _ = doctor(repo)
    assert by_id["config"]["status"] == "warn" and "gate.bogus" in by_id["config"]["detail"]


def test_core_hookspath_is_a_warning_with_the_snippet(repo):
    repo.git("config", "core.hooksPath", ".husky")
    (repo.path / ".husky").mkdir()
    _, by_id, _ = doctor(repo)
    hp = by_id["hooks.manager"]
    assert hp["status"] == "warn" and ".husky" in hp["detail"] and "check --staged" in hp["fix"]


def test_pre_commit_framework_is_detected_with_its_snippet(repo):
    repo.write(".pre-commit-config.yaml", "repos: []\n")
    _, by_id, _ = doctor(repo)
    assert by_id["hooks.manager"]["status"] == "warn" and "id: aigate-lite" in by_id["hooks.manager"]["fix"]


def test_vendored_version_drift_is_a_warning(repo):
    assert repo.run("install", "--vendor").returncode == 0
    script = repo.path / ".aigate" / "aigate_lite.py"
    script.write_bytes(script.read_bytes().replace(f'__version__ = "{aigate.__version__}"'.encode(),
                                                   b'__version__ = "0.0.1"'))
    _, by_id, _ = doctor(repo)
    drift = by_id["version"]
    assert drift["status"] == "warn" and "0.0.1" in drift["detail"] and "install --vendor" in drift["fix"]


def test_untrusted_commands_are_a_warning(repo):
    repo.write(".aigate/lite.toml", '[[commands]]\nname = "t"\nrun = "echo hi"\n')
    _, by_id, _ = doctor(repo)
    assert by_id["commands"]["status"] == "warn" and "aigate-lite trust" in by_id["commands"]["fix"]


def test_offline_skips_the_network(repo, monkeypatch):
    by_id = in_process(repo, monkeypatch, offline=True, fetch=never)
    assert by_id["network"]["status"] == "ok" and "skipped" in by_id["network"]["detail"]


def test_reachable_registries_are_ok(repo, monkeypatch):
    seen = []

    def fetch(url, timeout):
        seen.append((url, timeout))
        return 405 if "osv" in url else 200            # any HTTP answer means reachable
    by_id = in_process(repo, monkeypatch, fetch=fetch)
    hosts = {u.split("/")[2] for u, _ in seen}
    assert by_id["network"]["status"] == "ok" and len(hosts) == 5 and all(t <= 3 for _, t in seen)


def test_tls_failure_suggests_ssl_cert_file(repo, monkeypatch):
    def fetch(url, timeout):
        raise urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed"))
    by_id = in_process(repo, monkeypatch, fetch=fetch, env={"HTTPS_PROXY": "http://user:hunter2@proxy.corp:8080"})
    net = by_id["network"]
    assert net["status"] == "warn" and "SSL_CERT_FILE" in net["fix"]
    assert "proxy.corp:8080" in net["detail"] and "hunter2" not in json.dumps(net)     # credentials never shown


def test_unreachable_network_blocks_only_when_offline_block_is_set(repo, monkeypatch):
    def fetch(url, timeout):
        raise urllib.error.URLError(OSError("timed out"))
    assert in_process(repo, monkeypatch, fetch=fetch)["network"]["status"] == "warn"
    repo.write(".aigate/lite.toml", '[gate]\noffline = "block"\n')
    assert in_process(repo, monkeypatch, fetch=fetch)["network"]["status"] == "fail"


def test_agent_detection_names_the_variable(repo, monkeypatch):
    by_id = in_process(repo, monkeypatch, offline=True, env={"CLAUDECODE": "1"})
    assert "CLAUDECODE" in by_id["agent"]["detail"] and "AI-agent commits" in by_id["agent"]["detail"]
    assert "no AI agent" in in_process(repo, monkeypatch, offline=True)["agent"]["detail"]


def test_scope_and_cache_are_reported(repo, monkeypatch):
    repo.run("scope", "set", "src/**")
    by_id = in_process(repo, monkeypatch, offline=True)
    assert "src/**" in by_id["scope"]["detail"] and by_id["cache"]["status"] in ("ok", "warn")
    assert "result cache off" in by_id["cache"]["detail"]


def test_git_version_parsing_and_minimum():
    assert aigate.parse_git_version("git version 2.52.0.windows.1") == (2, 52, 0)
    assert aigate.parse_git_version("git version 2.12.5") < aigate.GIT_MIN
    assert aigate.parse_git_version("nonsense") is None


def test_shallow_clone_is_a_warning(repo, tmp_path):
    for i in range(3):
        repo.write(f"f{i}.txt", f"{i}\n")
        repo.commit(f"c{i}")
    clone = tmp_path / "shallow"
    repo.git("clone", "-q", "--depth", "1", f"file://{repo.path.as_posix()}", str(clone))
    proc = repo.run("doctor", "--offline", "--format", "json")
    assert proc.returncode in (0, 1)
    out = subprocess.run([sys.executable, str(SCRIPT), "doctor", "--offline", "--format", "json"], cwd=clone,
                         env=repo.env, capture_output=True, text=True)
    repo_check = {c["id"]: c for c in json.loads(out.stdout)}["repo"]
    assert repo_check["status"] == "warn" and "shallow" in repo_check["detail"]


def test_outside_a_repository_fails_cleanly(tmp_path, repo):
    out = subprocess.run([sys.executable, str(SCRIPT), "doctor", "--offline", "--format", "json"], cwd=tmp_path,
                         env=repo.env, capture_output=True, text=True)
    checks = json.loads(out.stdout)
    assert out.returncode == 1 and "Traceback" not in out.stderr
    assert {c["id"]: c for c in checks}["repo"]["status"] == "fail"


def test_json_shape_and_order(repo):
    _, _, checks = doctor(repo)
    assert all(set(c) == {"id", "status", "detail", "fix"} and c["status"] in STATUSES for c in checks)
    ranks = [STATUSES.index(c["status"]) for c in checks]
    assert ranks == sorted(ranks)


def test_text_output_has_one_next_line_and_ascii_fallback(repo):
    proc = repo.run("doctor", "--offline", env={"PYTHONIOENCODING": "cp1252"})
    lines = proc.stdout.splitlines()
    assert proc.returncode == 1 and sum(ln.startswith("next: ") for ln in lines) == 1 and lines[-1].startswith("next: ")
    assert "?" not in "".join(ln[:2] for ln in lines if ln and not ln.startswith(("next", " ")))


def test_offline_doctor_is_fast(repo):
    repo.run("install")
    start = time.perf_counter()
    doctor(repo)
    assert time.perf_counter() - start < 5.0     # the 1 s budget is measured separately; this guards hangs
