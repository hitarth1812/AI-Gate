import os
import sys

from conftest import SCRIPT, aigate
import shutil
import subprocess

from test_hooks import BROKEN, REAL, SH, fake_bin, install_vendored, needs_sh

REAL_PY = sys.executable.replace("\\", "/").replace("'", "'\\''")
OLD = 'echo 3.10.4; exit 1'                                     # answers the probe like Python 3.10


def logged(log, name):
    """A fake interpreter that records how it was called, then runs the real Python."""
    return f'echo "{name} $1" >> \'{log.as_posix()}\'\nexec \'{REAL_PY}\' "$@"'


def write_hook(repo, py, vendored=False):
    hook = repo.path / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_bytes(aigate.hook_script("pre-commit", vendored, py, str(SCRIPT)).encode())
    hook.chmod(0o755)


def run_hook(repo, kind, bin_dir, env=None):
    hook = repo.path / ".git" / "hooks" / kind
    keep = [os.path.dirname(shutil.which("git")), os.path.dirname(SH)]
    env = {**repo.env, "PATH": os.pathsep.join([str(bin_dir), *keep]), **(env or {})}
    return subprocess.run([SH, str(hook)], cwd=repo.path, env=env, text=True, capture_output=True)


def staged_clean(repo):
    repo.write("ok.py", "def add(a, b):\n    return a + b\n")
    repo.add()


def calls(log):
    return log.read_text().splitlines() if log.exists() else []


@needs_sh
def test_a_too_old_python3_is_skipped_for_a_newer_python(repo, tmp_path):
    install_vendored(repo)
    staged_clean(repo)
    log = tmp_path / "calls.log"
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, {"python3": OLD, "python": logged(log, "python")}))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls(log) and all(c.startswith("python ") for c in calls(log))


@needs_sh
def test_store_stubs_are_skipped_for_the_py_launcher(repo, tmp_path):
    install_vendored(repo)
    staged_clean(repo)
    log = tmp_path / "calls.log"
    launcher = 'if [ "$1" = "-3" ]; then shift; fi\n' + logged(log, "py")
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, {"python3": BROKEN, "python": BROKEN, "py": launcher}))
    assert proc.returncode == 0 and calls(log), proc.stdout + proc.stderr


@needs_sh
def test_aigate_python_wins(repo, tmp_path):
    install_vendored(repo)
    staged_clean(repo)
    log = tmp_path / "calls.log"
    custom = tmp_path / "custom dir" / "mypython"
    custom.parent.mkdir()
    custom.write_bytes(("#!/bin/sh\n" + logged(log, "custom") + "\n").encode())
    custom.chmod(0o755)
    bin_dir = fake_bin(tmp_path, {"python3": logged(log, "python3")})
    proc = run_hook(repo, "pre-commit", bin_dir, env={"AIGATE_PYTHON": custom.as_posix()})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert {c.split()[0] for c in calls(log)} == {"custom"}


@needs_sh
def test_pinned_interpreter_is_used_without_probing_even_in_a_path_with_spaces(repo, tmp_path):
    log = tmp_path / "calls.log"
    pinned = tmp_path / "my python dir" / "py thon"
    pinned.parent.mkdir()
    pinned.write_bytes(("#!/bin/sh\n" + logged(log, "pinned") + "\n").encode())
    pinned.chmod(0o755)
    write_hook(repo, str(pinned))
    staged_clean(repo)
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, {"python3": logged(log, "python3")}))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert [c.split()[0] for c in calls(log)] == ["pinned"] and "-c" not in calls(log)[0].split()[1:2]


@needs_sh
def test_missing_pinned_interpreter_is_rediscovered_with_a_hint(repo, tmp_path):
    gone = tmp_path / "gone" / "python3.12"
    write_hook(repo, str(gone))
    staged_clean(repo)
    log = tmp_path / "calls.log"
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, {"python3": logged(log, "python3")}))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert gone.as_posix() in proc.stderr and "re-run aigate-lite install" in proc.stderr
    assert proc.stderr.count("aigate-lite:") == 1


@needs_sh
def test_nothing_qualifies_lists_every_candidate_and_the_fix(repo, tmp_path):
    install_vendored(repo)
    staged_clean(repo)
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, {"python3": OLD, "python": BROKEN}),
                    env={"AIGATE_PYTHON": (tmp_path / "nope").as_posix()})
    err = proc.stderr
    assert proc.returncode == 1 and "no Python 3.11+ found" in err
    for want in ("python3: 3.10.4, too old", "python: not found or not a working Python", "py -3: not found",
                 "nope: not found", "AIGATE_PYTHON", "--no-verify", "commit is blocked"):
        assert want in err, want


def test_hook_text_is_posix_sh_without_bashisms():
    for vendored in (True, False):
        text = aigate.hook_script("pre-push", vendored, "/opt/py 3/bin/python3", "/x/aigate_lite.py")
        for bashism in ("[[", "local ", "function ", "$'", "==", "<<<", "declare "):
            assert bashism not in text.replace(aigate.HOOK_LOADER, ""), bashism


def test_python_discovery_mirror_reports_the_choice(monkeypatch, tmp_path):
    seen = []

    def probe(cmd):
        seen.append(cmd)
        return {("python3",): (False, "3.10.4"), ("py", "-3"): (True, "3.12.1")}.get(tuple(cmd), (False, ""))
    choice = aigate.discover_python({}, pinned=str(tmp_path / "missing"), probe=probe, exists=os.path.exists)
    assert choice.command == ["py", "-3"] and "3.12.1" in choice.reason and choice.pinned_missing
    assert aigate.discover_python({"AIGATE_PYTHON": "/x/py"}, probe=lambda c: (True, "3.13.0")).command == ["/x/py"]
    assert aigate.discover_python({}, pinned="/p", probe=probe, exists=lambda p: True).tried == [("/p", "pinned at install")]
    assert aigate.discover_python({}, probe=lambda c: (False, "")).command == []
    assert [t[0] for t in choice.tried][:3] == [str(tmp_path / "missing"), "python3", "python"]


def test_install_reports_the_hook_interpreter_and_why(repo):
    out = repo.run("install").stdout
    line = [ln for ln in out.splitlines() if ln.startswith("hook Python: ")]
    assert line and "pinned at install" in line[0] and sys.version.split()[0] in line[0]


def test_vendored_install_reports_the_discovered_interpreter(repo):
    out = repo.run("install", "--vendor", env={"AIGATE_PYTHON": sys.executable}).stdout
    line = [ln for ln in out.splitlines() if ln.startswith("hook Python: ")]
    assert line and "set by AIGATE_PYTHON" in line[0] and sys.version.split()[0] in line[0]


AWS = "AKIA" + "QWERTYUIOPASDFGH"                 # split so this file is not itself a finding
FAKE_GATE = "echo 3.12.0; exit 0"                 # claims to be Python and lets everything through


def program(tmp_path, name, body):
    p = tmp_path / "progs" / name
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(("#!/bin/sh\n" + body + "\n").encode())
    p.chmod(0o755)
    return p.as_posix()


@needs_sh
def test_an_agent_cannot_swap_the_gate_through_aigate_python(repo, tmp_path):
    install_vendored(repo)
    repo.write("leak.py", f'K = "{AWS}"\n')
    repo.add()
    bin_dir = fake_bin(tmp_path, {"python3": REAL})
    fake = program(tmp_path, "fakepy", FAKE_GATE)
    agent = run_hook(repo, "pre-commit", bin_dir, env={"AIGATE_PYTHON": fake, "CLAUDECODE": "1"})
    assert agent.returncode == 1 and "secrets.hardcoded" in agent.stdout
    assert aigate.AIGATE_PYTHON_IGNORED in agent.stderr
    human = run_hook(repo, "pre-commit", bin_dir, env={"AIGATE_PYTHON": fake})
    assert human.returncode == 0 and aigate.AIGATE_PYTHON_IGNORED not in human.stderr   # a human's own choice


@needs_sh
def test_a_program_that_prints_no_version_is_not_python(repo, tmp_path):
    install_vendored(repo)
    repo.write("leak.py", f'K = "{AWS}"\n')
    repo.add()
    silent = program(tmp_path, "true-like", "exit 0")
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, {"python3": REAL}), env={"AIGATE_PYTHON": silent})
    assert proc.returncode == 1 and "secrets.hardcoded" in proc.stdout


@needs_sh
def test_a_pinned_python_that_is_too_old_fails_closed(repo, tmp_path):
    old = program(tmp_path, "python3.10", 'echo "Unknown option: -P" >&2; exit 2')
    write_hook(repo, old)
    staged_clean(repo)
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, {"python3": REAL}))
    assert proc.returncode != 0


def test_mirror_ignores_aigate_python_for_agents():
    choice = aigate.discover_python({"AIGATE_PYTHON": "/x/py", "AI_AGENT": "1"},
                                    probe=lambda c: (c == ["python3"], "3.12.0"))
    assert choice.command == ["python3"] and choice.tried[0][0] == "AIGATE_PYTHON"
    assert aigate.discover_python({"AIGATE_PYTHON": "/x/py"}, probe=lambda c: (True, "")).command == []


# ---------------------------------------------------------------- F2: versioned names and vendored pinning

@needs_sh
def test_a_python3_that_is_too_old_gives_way_to_python3_12(repo, tmp_path):
    install_vendored(repo)
    staged_clean(repo)
    log = tmp_path / "calls.log"
    stubs = {"python3": OLD, "python": OLD, "python3.12": logged(log, "python3.12")}
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, stubs))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls(log) and all(c.startswith("python3.12 ") for c in calls(log))


@needs_sh
def test_the_newest_versioned_python_wins(repo, tmp_path):
    install_vendored(repo)
    staged_clean(repo)
    log = tmp_path / "calls.log"
    stubs = {"python3": OLD, "python3.11": logged(log, "python3.11"), "python3.13": logged(log, "python3.13")}
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, stubs))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert {c.split()[0] for c in calls(log)} == {"python3.13"}


@needs_sh
def test_py_launcher_is_asked_for_a_specific_version_before_plain_py_3(repo, tmp_path):
    install_vendored(repo)
    staged_clean(repo)
    log = tmp_path / "calls.log"
    launcher = (f'echo "py $1" >> \'{log.as_posix()}\'\n'
                'case "$1" in -3.12) shift; exec \'' + REAL_PY + '\' "$@";; *) echo "No suitable Python" >&2; exit 103;; esac')
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, {"python3": BROKEN, "python": BROKEN, "py": launcher}))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tried = [c for c in calls(log) if c.startswith("py ")]
    assert tried[:3] == ["py -3.14", "py -3.13", "py -3.12"]          # newest first, stops at the first that works
    assert "py -3.11" not in tried and "py -3" not in tried


@needs_sh
def test_vendored_install_pins_the_interpreter_and_uses_it_without_probing(repo, tmp_path):
    install_vendored(repo, pinned=True)
    hook = (repo.path / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
    assert f"PY='{sys.executable}'".replace("\\", "/") in hook and "PINNED=$PY" in hook
    assert not (repo.path / ".aigate" / "pre-commit").exists()          # the pin lives only in .git/hooks
    staged_clean(repo)
    log = tmp_path / "calls.log"
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, {"python3": logged(log, "python3")}))
    assert proc.returncode == 0 and not calls(log), proc.stdout + proc.stderr


@needs_sh
def test_vendored_pin_removed_rediscovers_with_a_hint(repo, tmp_path):
    install_vendored(repo)                                              # unpinned: the pin points nowhere
    staged_clean(repo)
    log = tmp_path / "calls.log"
    proc = run_hook(repo, "pre-commit", fake_bin(tmp_path, {"python3": logged(log, "python3")}))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "/nonexistent/pinned/python" in proc.stderr and "re-run aigate-lite install" in proc.stderr
    assert calls(log)


def test_discovery_mirror_walks_the_same_candidates_in_the_same_order():
    choice = aigate.discover_python({}, probe=lambda c: (False, ""))
    labels = [t[0] for t in choice.tried]
    assert labels == ["python3", "python", "python3.14", "python3.13", "python3.12", "python3.11",
                      "py -3.14", "py -3.13", "py -3.12", "py -3.11", "py -3"]
    text = aigate.hook_script("pre-commit", True, "/x/python", "/x/a.py")
    assert " ".join(f"'{label}'" if " " in label else label for label in labels) in text


@needs_sh
def test_aigate_python_is_ignored_for_any_spelling_of_the_agent_provenance(repo, tmp_path):
    install_vendored(repo)
    staged_clean(repo)
    log = tmp_path / "calls.log"
    custom = tmp_path / "custom"
    custom.write_bytes(("#!/bin/sh\n" + logged(log, "custom") + "\n").encode())
    custom.chmod(0o755)
    bin_dir = fake_bin(tmp_path, {"python3": logged(log, "python3")})
    proc = run_hook(repo, "pre-commit", bin_dir, env={"AIGATE_PYTHON": custom.as_posix(), "AIGATE_PROVENANCE": " Agent "})
    assert "AIGATE_PYTHON is ignored" in proc.stderr
    assert "custom" not in {c.split()[0] for c in calls(log)}
