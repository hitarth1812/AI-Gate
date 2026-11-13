import io
import json
import os
import random
import subprocess
import sys

import pytest

from conftest import SCRIPT, aigate, find
from test_hooks import REAL, fake_bin, install_vendored, needs_sh, run_hook


def friendly(proc, *wants, code=2):
    text = proc.stdout + proc.stderr
    assert proc.returncode == code, text
    assert "Traceback" not in text
    for want in wants:
        assert want in text, (want, text)


def run_in(cwd, repo, *args, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, env={**repo.env, **(env or {})},
                          text=True, capture_output=True, stdin=subprocess.DEVNULL)


def commits(repo, n):
    for i in range(n):
        repo.write(f"f{i}.txt", f"{i}\n")
        repo.commit(f"c{i}")


def test_1_not_a_git_repository(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    proc = run_in(outside, repo, "check", env={"GIT_CEILING_DIRECTORIES": str(tmp_path)})
    friendly(proc, "aigate-lite: not inside a git repository", "fix: ", "git init")


def test_2_range_in_a_repository_without_commits(tmp_path, repo):
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q", str(empty)], check=True)
    friendly(run_in(empty, repo, "check", "--range", "main..HEAD"), "no commits yet", "make a first commit")


def test_3_detached_head_works_for_range_and_push(repo):
    commits(repo, 2)
    repo.git("checkout", "-q", "--detach", "HEAD")
    assert repo.run("check", "--range", "HEAD~1..HEAD").returncode == 0
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    push = repo.run("check", "--push", input=f"HEAD {head} refs/heads/main {'0' * 40}\n")
    assert push.returncode in (0, 1) and "Traceback" not in push.stderr


def test_4_shallow_clone_missing_the_base(repo, tmp_path):
    commits(repo, 3)
    clone = tmp_path / "shallow"
    repo.git("clone", "-q", "--depth", "1", f"file://{repo.path.as_posix()}", str(clone))
    first = repo.git("rev-list", "--max-parents=0", "HEAD").stdout.strip()
    friendly(run_in(clone, repo, "check", "--range", f"{first}..HEAD"), "clone is shallow", "fetch-depth: 0",
             "GIT_DEPTH: 0", "a typo")


def test_5_unknown_ref(repo):
    commits(repo, 1)
    for ref in ("HEAD~5", "v1.0", "deadbeef" * 5, "origin/feature-x"):            # no remote yet: no fetch advice
        proc = repo.run("check", "--range", f"{ref}..HEAD")
        friendly(proc, f"{ref!r} is not a commit", "check the name")
        assert "git fetch" not in proc.stderr
    repo.git("remote", "add", "origin", "https://example.invalid/x.git")
    friendly(repo.run("check", "--range", "origin/feature-x..HEAD"),
             "run: git fetch origin feature-x:refs/remotes/origin/feature-x")


def test_5b_an_empty_side_of_a_range_means_head(repo):
    commits(repo, 2)
    assert repo.run("check", "--range", "HEAD~1..").returncode == 0


def test_6_invalid_toml_names_the_position_and_the_windows_path_fix(repo):
    repo.write(".aigate/lite.toml", '[[commands]]\nname = "t"\nrun = "C:\\Users\\me\\py.exe -m pytest"\n')
    repo.write("a.py", "x = 1\n")
    repo.add()
    msg = find(repo.check(), "gate.config_invalid")[0]["message"]
    assert "line 3" in msg and "single quotes" in msg and "forward slashes" in msg


posix_user = pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0,
                                reason="chmod 0 only takes effect on POSIX for a non-root user")


@posix_user
def test_7_unreadable_file_in_all_mode_does_not_stop_the_scan(repo):
    repo.write("secret.txt", "data\n")
    repo.write("a.py", "eval(x)\n")
    repo.commit("files")
    os.chmod(repo.path / "secret.txt", 0)
    try:
        proc = repo.run("check", "--all", "--format", "json")
    finally:
        os.chmod(repo.path / "secret.txt", 0o644)
    assert proc.returncode in (0, 1) and "Traceback" not in proc.stderr
    assert find(json.loads(proc.stdout), "security.eval")


def test_8_unreachable_registry_gives_one_specific_note(repo):
    repo.write("requirements.txt", "requests\n")
    repo.write("app.py", "import requests\n")
    repo.add()
    env = {"AIGATE_REGISTRY_FIXTURE": "", "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
           "https_proxy": "http://127.0.0.1:9", "NO_PROXY": "", "no_proxy": ""}
    res = repo.check("--no-cache", env=env)
    notes = [n for n in res["notes"] if "registry lookup" in n]
    assert len(notes) == 1 and "connection refused" in notes[0] and "--offline" in notes[0]
    assert find(res, "deps.unverified")


def test_9_git_not_on_path(repo, tmp_path):
    empty_bin = tmp_path / "nobin"
    empty_bin.mkdir()
    friendly(run_in(repo.path, repo, "check", env={"PATH": str(empty_bin)}), "git was not found on PATH",
             "fix: install git")


def test_10_interpreter_too_old(repo):
    fake = ("import sys, runpy; sys.version_info = (3, 10, 4, 'final', 0); "
            f"runpy.run_path({str(SCRIPT)!r}, run_name='__main__')")
    proc = subprocess.run([sys.executable, "-c", fake], capture_output=True, text=True)
    assert proc.returncode == 2 and "aigate-lite needs Python 3.11+; this is 3.10. Try: " in proc.stderr \
        and "-m aigate_lite" in proc.stderr and "fix: install Python" in proc.stderr
    assert len(proc.stderr.strip().splitlines()) == 1                       # 0.5.1: one line


@needs_sh
def test_11_vendored_script_missing(repo, tmp_path):
    install_vendored(repo)
    (repo.path / ".aigate" / "aigate_lite.py").unlink()
    repo.write("a.py", "x = 1\n")
    repo.add()
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, {"python3": REAL}))
    assert proc.returncode == 1 and "aigate_lite.py is missing" in proc.stderr
    assert "git checkout -- .aigate/aigate_lite.py" in proc.stderr and "install --vendor" in proc.stderr


def test_12_bad_flags_point_at_help(repo):
    for args in (["check", "--bogus"], ["frobnicate"], ["check", "--format", "xml"]):
        friendly(repo.run(*args), "see: aigate-lite --help")


def test_internal_error_is_short_fails_closed_and_debug_shows_it(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.path)
    for k in ("CLAUDECODE", "AI_AGENT", "GEMINI_CLI", "AIGATE_AGENT", "AIGATE_PROVENANCE"):
        monkeypatch.delenv(k, raising=False)

    def boom(args, env):
        raise ZeroDivisionError("division by zero")
    monkeypatch.setattr(aigate, "run_check", boom)
    assert aigate.main(["check"]) == 2
    err = capsys.readouterr().err
    assert "internal error (ZeroDivisionError" in err and "--debug" in err and "git commit --no-verify" in err
    assert "Traceback" not in err
    assert aigate.main(["check", "--push"]) == 2 and "git push --no-verify" in capsys.readouterr().err
    monkeypatch.setenv("CLAUDECODE", "1")
    assert aigate.main(["check"]) == 2 and "--no-verify" not in capsys.readouterr().err   # no bypass hint for agents
    with pytest.raises(ZeroDivisionError):
        aigate.main(["check", "--debug"])


def test_a_closed_pipe_exits_non_zero_without_an_internal_error(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.path)

    def closed(args, env):
        raise BrokenPipeError(32, "Broken pipe")
    monkeypatch.setattr(aigate, "run_check", closed)
    assert aigate.main(["check"]) == 2 and "internal error" not in capsys.readouterr().err


def test_registry_failure_reasons():
    import ssl
    import urllib.error
    http = urllib.error.HTTPError("https://x", 503, "Service Unavailable", {}, None)
    assert aigate.network_reason(http) == "HTTP 503 from the registry"
    cert = urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed"))
    assert "SSL_CERT_FILE" in aigate.network_reason(cert)
    other = urllib.error.URLError(ssl.SSLError(1, "WRONG_VERSION_NUMBER"))
    assert aigate.network_reason(other).startswith("TLS error") and "SSL_CERT_FILE" not in aigate.network_reason(other)


TOKENS = ["check", "install", "uninstall", "init", "scope", "trust", "baseline", "doctor", "explain", "--staged",
          "--range", "HEAD~1..HEAD", "main..nope", "--push", "--all", "--format", "json", "text", "sarif", "github",
          "--offline", "--no-cache", "--cache", "--no-commands", "-q", "-v", "--color", "never", "always", "--preset",
          "strict", "bogus", "--print", "--force", "--prune", "--include-secrets", "set", "add", "show", "clear",
          "src/**", "--rev", "HEAD", "--vendor", "--no-pre-push", "security.eval", "--bogus", "", "-x",
          "--format=json", "--config", "nope.toml", "--remote", "origin", "-h"]


def test_fuzz_200_argument_lists_never_traceback(repo, monkeypatch, capsys):
    import urllib.error
    commits(repo, 2)
    repo.write("a.py", "x = eval(y)\n")
    repo.add()
    monkeypatch.chdir(repo.path)
    for k, v in {**repo.env, "AIGATE_OFFLINE": "1"}.items():
        monkeypatch.setenv(k, v)
    for k in ("CLAUDECODE", "AI_AGENT", "GEMINI_CLI", "AIGATE_DEBUG"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(aigate, "_default_fetch", lambda url, timeout: (_ for _ in ()).throw(
        urllib.error.URLError(OSError("offline in tests"))))
    rng = random.Random(20261101)
    groups = {
        "check": [("--offline",), ("-q",), ("-v",), ("--all",), ("--range", "HEAD~1..HEAD"), ("--range", "main..nope"),
                  ("--push",), ("--no-cache",), ("--cache",), ("--no-commands",), ("--format", "json"),
                  ("--format", "sarif"), ("--format", "github"), ("--color", "always"), ("--config", "nope.toml")],
        "doctor": [("--offline",), ("--format", "json")],
        "init": [("--print",), ("--preset", "strict"), ("--preset", "relaxed"), ("--force",)],
        "baseline": [("--prune",), ("--include-secrets",)],
        "scope": [("show",), ("set", "src/**"), ("add", "docs/**"), ("clear",)],
        "trust": [(), ("--rev", "HEAD"), ("--rev", "nope")],
        "explain": [(), ("security.eval",), ("nope.rule",)],
        "install": [(), ("--vendor",), ("--no-pre-push",), ("--force",)],
    }
    reached = 0
    for _ in range(200):
        if rng.random() < 0.8:                                 # mostly valid commands, so real code runs
            cmd = rng.choice([*groups, "check", "check"])
            argv = [cmd] + [t for g in rng.sample(groups[cmd], rng.randint(0, min(3, len(groups[cmd])))) for t in g]
        else:
            argv = [rng.choice(TOKENS) for _ in range(rng.randint(0, 4))]
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        try:
            code = aigate.main(argv)
            reached += 1
        except SystemExit as e:                                   # argparse --help / usage errors
            code = e.code
        out = capsys.readouterr()
        assert code in (0, 1, 2), (argv, code)
        assert "Traceback" not in out.out + out.err and "internal error" not in out.err, (argv, out.err)
    assert reached >= 80, reached
