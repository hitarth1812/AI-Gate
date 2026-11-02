import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import SCRIPT, aigate, find

AWS = "AKIA" + "QWERTYUIOPASDFGH"                 # split so this file is not itself a finding
SH = shutil.which("sh")
needs_sh = pytest.mark.skipif(SH is None, reason="no POSIX sh on this machine")


# ---------------------------------------------------------------- rendering

def test_vendored_hook_tries_python3_python_then_py_launcher():
    text = aigate.hook_script("pre-commit", True, sys.executable, str(SCRIPT))
    # F2: was "for c in python3 python 'py -3'; do". The old three stay first and in this order; versioned
    # names (newest first) and the py launcher's versions were added between them and the plain `py -3`.
    assert ("for c in python3 python python3.14 python3.13 python3.12 python3.11 "
            "'py -3.14' 'py -3.13' 'py -3.12' 'py -3.11' 'py -3'; do") in text   # first working one wins
    assert "sys.version_info >= (3, 11)" in text and "</dev/null" in text
    assert ".aigate/aigate_lite.py" in text and "\r" not in text


def test_absolute_paths_with_spaces_and_quotes_are_single_quoted():
    py, script = "C:\\Program Files\\Py 3\\python.exe", "/home/o'neil/my tools/aigate_lite.py"
    text = aigate.hook_script("pre-push", False, py, script)
    assert "PY='C:/Program Files/Py 3/python.exe'" in text
    assert "SCRIPT='/home/o'\\''neil/my tools/aigate_lite.py'" in text


# ---------------------------------------------------------------- running under sh

def fake_bin(tmp_path, stubs):
    d = tmp_path / "bin"
    d.mkdir()
    missing = 'echo "not installed" >&2; exit 127'
    versioned = {f"python{v}": missing for v in aigate.PY_VERSIONS}
    stubs = {"python3": missing, "python": missing, **versioned, "py": missing, **stubs}   # never fall through to a real one
    for name, body in stubs.items():
        p = d / name
        p.write_bytes(("#!/bin/sh\n" + body + "\n").encode())
        p.chmod(0o755)
    return d


def run_hook(repo, kind, bin_dir, stdin=""):
    hook = repo.path / ".git" / "hooks" / kind
    keep = [os.path.dirname(shutil.which("git")), os.path.dirname(SH)]
    env = {**repo.env, "PATH": os.pathsep.join([str(bin_dir), *keep])}
    return subprocess.run([SH, str(hook)], cwd=repo.path, env=env, input=stdin, text=True, capture_output=True)


REAL = "exec '" + sys.executable.replace("\\", "/").replace("'", "'\\''") + "' \"$@\""
BROKEN = 'echo "Python was not found; run without arguments to install from the Microsoft Store" >&2; exit 9009'


def unpin(repo):
    """Point every installed hook's pin at a path that does not exist, so the PATH search is what the test exercises."""
    for kind in ("pre-commit", "pre-push"):
        hook = repo.path / ".git" / "hooks" / kind
        lines = hook.read_text(encoding="utf-8").split(chr(10))
        i = next(n for n, line in enumerate(lines) if line.startswith("PY='"))
        lines[i] = "PY='/nonexistent/pinned/python'"
        hook.write_bytes(chr(10).join(lines).encode())


def install_vendored(repo, pinned=False):
    """Install the vendored hooks. Installs now pin the interpreter (F2); by default the pin is removed again so
    that the scenarios below keep exercising discovery over PATH. `pinned=True` keeps it."""
    assert repo.run("install", "--vendor").returncode == 0
    if not pinned:
        unpin(repo)


@needs_sh
def test_hook_skips_a_broken_python3_stub(repo, tmp_path):
    install_vendored(repo)
    repo.write("bad.py", f'K = "{AWS}"\n')
    repo.add()
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, {"python3": BROKEN, "python": REAL}))
    assert proc.returncode == 1 and "secrets.hardcoded" in proc.stdout, proc.stderr


@needs_sh
def test_hook_falls_back_to_py_launcher_and_passes_clean_code(repo, tmp_path):
    install_vendored(repo)
    repo.write("ok.py", "def add(a, b):\n    return a + b\n")
    repo.add()
    launcher = 'if [ "$1" = "-3" ]; then shift; fi\n' + REAL
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, {"python3": BROKEN, "python": BROKEN, "py": launcher}))
    assert proc.returncode == 0, proc.stdout + proc.stderr


@needs_sh
def test_hook_without_any_python_stops_with_a_message(repo, tmp_path):
    install_vendored(repo)
    repo.write("ok.py", "x = 1\n")
    repo.add()
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, {"python3": BROKEN}))
    assert proc.returncode == 1 and "no Python 3.11+ found" in proc.stderr


@needs_sh
def test_pre_push_hook_with_crlf_refs_and_space_in_repo_path(tmp_path):
    from conftest import GitRepo
    (tmp_path / "my repo").mkdir()
    repo = GitRepo(tmp_path / "my repo", tmp_path / "cache")
    repo.write("leak.py", f'K = "{AWS}"\n')
    repo.commit("leak")
    install_vendored(repo)
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    refs = f"refs/heads/main {head} refs/heads/main {'0' * 40}\r\n"
    proc = run_hook(repo, "pre-push", fake_bin(tmp_path, {"python": REAL}), stdin=refs)
    assert proc.returncode == 1 and "secrets.hardcoded" in proc.stdout, proc.stderr


# ---------------------------------------------------------------- Windows fixes

def test_install_force_keeps_the_old_hook(repo):
    # Guards the Windows bug (renaming the hook while it was still open); POSIX allows that rename anyway.
    hooks = repo.path / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\necho old\n")
    assert repo.run("install", "--force").returncode == 0
    assert (hooks / "pre-commit.local").read_text() == "#!/bin/sh\necho old\n"


def test_text_output_survives_a_non_utf8_pipe(repo):
    repo.write("a.py", "eval(x)\n")
    repo.add()
    proc = repo.run("check", "--color", "never", env={"PYTHONIOENCODING": "cp1252"})
    assert proc.returncode == 1 and "commit stopped" in proc.stdout and "Traceback" not in proc.stderr


def test_json_output_is_unchanged_on_a_non_utf8_pipe(repo):
    repo.write("a.py", "eval(x)\n")
    repo.add()
    proc = repo.run("check", "--format", "json", env={"PYTHONIOENCODING": "cp1252"})
    assert find(json.loads(proc.stdout), "security.eval")
