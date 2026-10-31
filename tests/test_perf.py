import json
import os

from conftest import find


def profile(proc) -> dict:
    lines = [ln for ln in proc.stderr.splitlines() if ln.startswith("aigate-profile: ")]
    assert len(lines) == 1, proc.stderr
    return json.loads(lines[0][len("aigate-profile: "):])


# ---------------------------------------------------------------- AIGATE_PROFILE counters

def test_profile_counts_git_calls_and_blobs(repo):
    repo.write("app/svc.py", "import json\n\n\ndef dump(x):\n    return json.dumps(x)\n")
    repo.add()
    proc = repo.run("check", "--format", "json", "--offline", env={"AIGATE_PROFILE": "1"})
    counters = profile(proc)
    assert counters["git_calls"] > 0 and counters["blobs_read"] >= 1
    assert json.loads(proc.stdout)["verdict"] == "pass"           # stdout (the JSON report) is untouched


def test_no_profile_output_by_default(repo):
    repo.write("a.py", "x = 1\n")
    repo.add()
    proc = repo.run("check", "--format", "json", "--offline")
    assert "aigate-profile" not in proc.stderr and not find(json.loads(proc.stdout), "gate.error")


def test_profile_on_version_has_zero_git_calls(repo):
    counters = profile(repo.run("--version", env={"AIGATE_PROFILE": "1"}))
    assert counters == {"blobs_read": 0, "git_calls": 0}


# ---------------------------------------------------------------- lazy imports

HEAVY = ("argparse", "concurrent.futures", "configparser", "gzip", "importlib.metadata", "shutil", "urllib.request")


def loaded_after(code: str) -> set:
    import subprocess
    import sys
    from conftest import SCRIPT
    probe = ("import importlib.util, sys\n"
             f"spec = importlib.util.spec_from_file_location('aigate_lite', {str(SCRIPT)!r})\n"
             "m = importlib.util.module_from_spec(spec); sys.modules['aigate_lite'] = m; spec.loader.exec_module(m)\n"
             + code + "\nprint(' '.join(sorted(sys.modules)))\n")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True).stdout
    return set(out.split())


def test_importing_the_gate_loads_no_heavy_modules():
    assert not loaded_after("") & set(HEAVY)


def test_heavy_modules_still_load_when_a_feature_needs_them():
    mods = loaded_after("m.build_parser().parse_args(['check', '--offline'])\n"
                        "m.parse_setup_cfg('[options]\\ninstall_requires =\\n    flask\\n', 'setup.cfg')")
    assert {"argparse", "configparser"} <= mods


def test_version_output_unchanged(repo):
    proc = repo.run("--version")
    assert proc.returncode == 0 and proc.stdout.strip() == "aigate-lite 0.1.0"


# ---------------------------------------------------------------- work avoidance

def run_inproc(repo, monkeypatch, capsys, *args):
    """Run the gate inside this process so tests can watch which helpers it calls."""
    from conftest import aigate
    monkeypatch.chdir(repo.path)
    for k, v in repo.env.items():
        monkeypatch.setenv(k, v)
    for k in [k for k in os.environ if k.startswith("AIGATE_") and k not in repo.env]:
        monkeypatch.delenv(k)
    capsys.readouterr()
    code = aigate.main(list(args))
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip().startswith("{") else out


def watch(monkeypatch, name):
    from conftest import aigate
    calls, real = [], getattr(aigate, name)
    monkeypatch.setattr(aigate, name, lambda *a, **k: calls.append(a) or real(*a, **k))
    return calls


def test_stdlib_and_local_imports_skip_the_environment_scan(repo, monkeypatch, capsys):
    repo.write("app/__init__.py", "")
    repo.write("app/util.py", "X = 1\n")
    repo.commit()
    repo.write("app/svc.py", "import json\nfrom app import util\n\n\ndef dump():\n    return json.dumps(util.X)\n")
    repo.add()
    scans, listing = watch(monkeypatch, "python_environment"), watch(monkeypatch, "python_declared")
    code, res = run_inproc(repo, monkeypatch, capsys, "check", "--format", "json")
    assert code == 0 and res["verdict"] == "pass"
    assert scans == [] and listing == []                      # no installed-package scan, no manifest walk


def test_third_party_import_still_scans_and_flags(repo, monkeypatch, capsys):
    repo.write("requirements.txt", "flask\n")
    repo.commit()
    repo.write("svc.py", "import fastapi_auth_helperz\nimport flask\n")
    repo.add()
    scans = watch(monkeypatch, "python_environment")
    _, res = run_inproc(repo, monkeypatch, capsys, "check", "--format", "json")
    assert len(scans) == 1
    assert [f["line"] for f in find(res, "deps.hallucinated")] == [1] and not find(res, "deps.undeclared")


def test_local_package_in_unicode_and_space_path_with_crlf(repo):
    repo.write("my pkg/ütil/__init__.py", "")
    repo.write("my pkg/ütil/core.py", "X = 1\r\n")
    repo.commit()
    repo.write("my pkg/svc.py", "import json\r\nimport ütil.core\r\n\r\nprint(json.dumps(ütil.core.X))\r\n")
    repo.add()
    res = repo.check()
    assert not find(res, "deps.hallucinated") and not find(res, "deps.undeclared") and not find(res, "deps.unverified")


def test_environment_scan_matches_importlib_metadata():
    import importlib.metadata
    from conftest import aigate

    class Ctx:
        cache = {}
        repo = type("R", (), {"root": os.devnull})()
    names, mapping = aigate.python_environment(Ctx())
    for top, dists in importlib.metadata.packages_distributions().items():
        assert top in names, top
        assert {d for d in dists if d} <= mapping[top], top


def test_env_check_runs_one_git_process_however_many_env_files(repo):
    repo.write("a.py", "x = 1\n")
    repo.add("a.py")

    def calls():
        return profile(repo.run("check", "--format", "json", env={"AIGATE_PROFILE": "1"}))["git_calls"]
    (repo.path / ".env").write_text("X=1\n")
    one = calls()
    (repo.path / ".env.local").write_text("X=1\n")
    (repo.path / ".env.prod").write_text("X=1\n")
    (repo.path / ".gitignore").write_text(".env.prod\n")
    res = repo.check()
    assert sorted(f["path"] for f in find(res, "secrets.env_not_ignored")) == [".env", ".env.local"]
    assert calls() == one


def test_no_env_files_means_no_file_listing(repo, monkeypatch, capsys):
    repo.write("notes.md", "hello\n")
    repo.add()
    listed = watch(monkeypatch, "run_git")
    code, res = run_inproc(repo, monkeypatch, capsys, "check", "--format", "json")
    assert code == 0 and not [a for a in listed if "ls-files" in a or "ls-tree" in a]


def test_range_git_calls_do_not_grow_per_commit_beyond_the_diffs(repo):
    def calls(n):
        for i in range(n):
            repo.write(f"f{i}.py", f"x = {i}\n")
            repo.commit(f"c{i}")
        return profile(repo.run("check", "--range", f"HEAD~{n}..HEAD", "--format", "json",
                                env={"AIGATE_PROFILE": "1"}))["git_calls"]
    one, three = calls(1), calls(3)
    assert three - one == 2 * 2                    # only the two diff calls per extra commit


def test_duplicate_blocks_across_many_new_files(repo):
    block = "".join(f"    total = total + item.value_{i}\n" for i in range(8))
    for i in range(40):
        repo.write(f"m{i}.py", f"def f{i}(item):\n    total = 0\n{block}    return total\n")
    repo.add()
    dups = find(repo.check(), "code.duplicate_block")
    assert len(dups) == 39 and all(f["path"] != "m0.py" for f in dups)
