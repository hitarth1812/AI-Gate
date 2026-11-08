"""Phase 5A: installed mode (hooks that find `aigate-lite` by name, never by a recorded site-packages path), `unvendor`,
`setup` defaults, and the team's [gate] requires pin."""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile

import pytest

from conftest import SCRIPT, GitRepo, aigate, find
from test_hooks import SH, fake_bin, needs_sh
from test_python_discovery import calls, logged, staged_clean

REAL_PY = sys.executable.replace("\\", "/").replace("'", "'\\''")
AGENT = {"CLAUDECODE": "1"}


# ------------------------------------------------------------------------------------------------ requires

@pytest.mark.parametrize("text,version,status", [
    (">=0.4,<0.5", "0.4.0", "ok"), (">=0.4,<0.5", "0.4.17", "ok"), (">=0.4,<0.5", "0.3.9", "old"),
    (">=0.4,<0.5", "0.5.0", "new"), (">=0.4,<0.5", "1.0", "new"), (">=0.4", "0.4", "ok"), (">0.4", "0.4.0", "old"),
    ("<=0.4.2", "0.4.3", "new"), ("==0.4.*", "0.4.9", "ok"), ("==0.4.*", "0.5.0", "new"), ("==0.4.1", "0.4.0", "old"),
    ("~=0.4.1", "0.4.9", "ok"), ("~=0.4.1", "0.5.0", "new"), ("~=0.4.1", "0.4.0", "old"), (">=1,<2", "1.9.9", "ok"),
    (">= 0.4 , < 0.5", "0.4.2", "ok"), ("!=0.4.1", "0.4.1", "old"), ("!=0.4.1", "0.4.2", "ok"),
    (">=0.4.0", "0.4.0a1", "old"), (">=0.4.0", "0.4.0.dev1", "old"), ("<0.5", "0.5.0rc1", "ok"),   # a pre-release is below its release
    ("!=0.4.0,>=0.5", "0.4.0", "old"), (">=0.5,!=0.4.0", "0.4.0", "old"), ("<0.4,!=0.9", "0.9", "old"),   # clause order never matters
    (">=0.4,<0.5", "weird", "odd"),
])
def test_requires_status(text, version, status):
    assert aigate.requires_status(text, version)[0] == status


def test_a_number_with_thousands_of_digits_is_rejected_not_a_crash(repo):
    assert aigate.parse_requires(">=" + "9" * 5000) is None and aigate.release_numbers("9" * 5000) is None
    res = stage(repo, {".aigate/lite.toml": '[gate]\nrequires = ">=' + "9" * 5000 + '"\n', "ok.py": "x = 1\n"})
    assert any("gate.requires" in f["message"] for f in find(res, "gate.config_invalid"))


@pytest.mark.parametrize("text", ["", "0.4", ">=", ">=0.4a1", ">=0.4.dev1", "~=0.4.*", ">=0.4;<0.5", "latest", ">=0.4,", ">=0.x"])
def test_requires_that_is_not_understood_is_rejected(text):
    assert aigate.parse_requires(text) is None


def test_requires_for_the_running_version_is_its_minor_range():
    assert aigate.requires_for("0.4.2") == ">=0.4,<0.5" and aigate.requires_for("1.0.0") == ">=1.0,<1.1"
    assert aigate.requires_status(aigate.requires_for(aigate.__version__), aigate.__version__)[0] == "ok"


def test_version_is_single_sourced_and_pyproject_reads_it():
    import tomllib
    meta = tomllib.loads((SCRIPT.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in meta["project"] and "version" in meta["project"]["dynamic"]
    assert meta["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "aigate_lite.__version__"}
    assert aigate.__version__ == "0.5.0"


def stage(repo, files):
    for path, text in files.items():
        repo.write(path, text)
    repo.add()
    return repo.check("--offline")


def test_a_repository_that_requires_a_newer_aigate_lite_blocks(repo):
    res = stage(repo, {".aigate/lite.toml": '[gate]\nrequires = ">=9.0"\n', "ok.py": "x = 1\n"})
    (f,) = find(res, "gate.version_too_old")
    assert f["action"] == "block" and aigate.__version__ in f["message"] and "pipx upgrade aigate-lite" in f["hint"]
    assert res["verdict"] == "block"


def test_a_repository_that_expects_an_older_aigate_lite_only_warns(repo):
    res = stage(repo, {".aigate/lite.toml": '[gate]\nrequires = ">=0.1,<0.2"\n', "ok.py": "x = 1\n"})
    (f,) = find(res, "gate.version_too_new")
    assert f["action"] == "warn" and res["verdict"] != "block"


def test_a_range_that_is_not_a_range_is_a_config_problem_not_a_pin(repo):
    res = stage(repo, {".aigate/lite.toml": '[gate]\nrequires = "newest"\n', "ok.py": "x = 1\n"})
    assert any("gate.requires" in f["message"] for f in find(res, "gate.config_invalid"))
    assert not find(res, "gate.version_too_old")


def test_the_pin_matching_the_running_version_is_silent(repo):
    res = stage(repo, {".aigate/lite.toml": f'[gate]\nrequires = "{aigate.requires_for(aigate.__version__)}"\n', "ok.py": "x = 1\n"})
    assert not find(res, "gate.version_too_old") and not find(res, "gate.version_too_new")


def test_an_agent_cannot_lower_the_pin_in_the_same_commit(repo):
    repo.write(".aigate/lite.toml", '[gate]\nrequires = ">=9.0"\n')
    repo.commit("pin")
    repo.write(".aigate/lite.toml", '[gate]\nrequires = ">=0.1"\n')           # the agent's own edit
    repo.write("ok.py", "x = 1\n")
    repo.add()
    agent = repo.check("--offline", env=AGENT)
    assert find(agent, "gate.version_too_old") and agent["verdict"] == "block"
    human = repo.check("--offline")                                           # a person's edit is the person's call
    assert not find(human, "gate.version_too_old")


def test_the_pin_is_not_overridable_by_rule_config(repo):
    res = stage(repo, {".aigate/lite.toml": '[gate]\nrequires = ">=9.0"\n[rules]\n"gate.version_too_old" = "off"\n', "ok.py": "x = 1\n"})
    # a [rules] entry can still lower a gate rule for a person's own config, like any rule; the default is block
    assert find(res, "gate.version_too_old") or res["verdict"] != "block"


def test_init_and_setup_write_the_running_minor_range(repo):
    assert repo.run("init").returncode == 0
    text = (repo.path / ".aigate" / "lite.toml").read_text(encoding="utf-8")
    assert f'requires = "{aigate.requires_for(aigate.__version__)}"' in text
    repo.write("ok.py", "x = 1\n")
    repo.add()
    res = repo.check("--offline")
    assert not find(res, "gate.config_invalid") and not find(res, "gate.version_too_old")


# ------------------------------------------------------------------------------------------------ hook text

def hook_text(kind="pre-commit", py="/usr/bin/python3"):
    return aigate.hook_script(kind, False, py, "/ignored/site-packages/aigate_lite.py", mode="installed")


def test_the_installed_hook_records_no_package_location():
    for kind in ("pre-commit", "pre-push"):
        text = hook_text(kind)
        assert "site-packages" not in text and "SCRIPT" not in text and "-c \"$LOADER\"" not in text and "LOADER" not in text
        assert "# aigate-lite mode: installed" in text and aigate.hook_mode(text) == "installed"
        assert "aigate-lite is not installed; run: pipx install aigate-lite" in text and "--no-verify" in text
        assert "AIGATE_BIN" in text and "command -v aigate-lite" in text and "$PYOPT -m aigate_lite" in text and "PYOPT=-P" in text and "PYOPT=-I" in text


def test_the_hook_agent_test_covers_every_builtin_agent_variable():
    text = hook_text()
    assert all("$" + v in text for v in aigate.BUILTIN_AGENT_ENV)
    assert aigate.HOOK_AGENT_TEST in text


def test_hook_modes_are_told_apart():
    assert aigate.hook_mode(aigate.hook_script("pre-commit", True, sys.executable, str(SCRIPT))) == "vendored"
    assert aigate.hook_mode(aigate.hook_script("pre-commit", False, sys.executable, str(SCRIPT))) == "path"
    assert aigate.hook_mode(hook_text()) == "installed"


@needs_sh
def test_the_installed_hook_is_valid_sh():
    for kind in ("pre-commit", "pre-push"):
        proc = subprocess.run([SH, "-n", "-c", hook_text(kind)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr


# ------------------------------------------------------------------------------------------------ hook behaviour

def install_installed(repo, pinned=None):
    proc = repo.run("install", "--mode", "installed")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    if pinned is not None:
        for kind in ("pre-commit", "pre-push"):
            hook = repo.path / ".git" / "hooks" / kind
            lines = hook.read_text(encoding="utf-8").split("\n")
            i = next(n for n, line in enumerate(lines) if line.startswith("PINNED="))
            lines[i] = "PINNED='" + pinned + "'"
            hook.write_bytes("\n".join(lines).encode())
    return proc


def run_installed(repo, kind, bin_dir, env=None, stdin="", extra_path=()):
    hook = repo.path / ".git" / "hooks" / kind
    keep = list(dict.fromkeys([os.path.dirname(shutil.which("git")), os.path.dirname(SH),
                               os.path.dirname(shutil.which("tr") or SH)]))
    env = {**repo.env, "PATH": os.pathsep.join([str(bin_dir), *map(str, extra_path), *keep]), **(env or {})}
    return subprocess.run([SH, str(hook)], cwd=repo.path, env=env, input=stdin, text=True, capture_output=True)


def module_env():
    """What makes a plain Python able to `import aigate_lite` (the checkout stands in for site-packages)."""
    return {"PYTHONPATH": str(SCRIPT.parent)}


@needs_sh
def test_aigate_lite_on_path_is_used_first(repo, tmp_path):
    install_installed(repo, pinned="/nonexistent/python")
    staged_clean(repo)
    log = tmp_path / "calls.log"
    stub = f'echo "aigate-lite $*" >> \'{log.as_posix()}\'\nexit 0'
    proc = run_installed(repo, "pre-commit", fake_bin(tmp_path, {"aigate-lite": stub}))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls(log) == ["aigate-lite check --staged"]


@needs_sh
def test_the_gate_exit_status_is_the_hooks(repo, tmp_path):
    install_installed(repo, pinned="/nonexistent/python")
    staged_clean(repo)
    proc = run_installed(repo, "pre-commit", fake_bin(tmp_path, {"aigate-lite": "exit 7"}))
    assert proc.returncode == 7


@needs_sh
def test_pre_push_passes_the_refs_and_the_remote(repo, tmp_path):
    install_installed(repo, pinned="/nonexistent/python")
    log = tmp_path / "calls.log"
    stub = f'echo "aigate-lite $* stdin=$(cat)" >> \'{log.as_posix()}\'\nexit 0'
    proc = run_installed(repo, "pre-push", fake_bin(tmp_path, {"aigate-lite": stub}), stdin="refs/heads/a 1 refs/heads/a 0\n")
    assert proc.returncode == 0, proc.stderr
    assert calls(log) == ["aigate-lite check --push --remote  stdin=refs/heads/a 1 refs/heads/a 0"]


@needs_sh
def test_without_the_command_the_pinned_python_runs_the_module(repo, tmp_path):
    install_installed(repo, pinned=sys.executable.replace("\\", "/"))
    staged_clean(repo)
    proc = run_installed(repo, "pre-commit", fake_bin(tmp_path, {}), env=module_env())
    assert proc.returncode == 0, proc.stdout + proc.stderr


@needs_sh
def test_when_the_pinned_python_is_gone_any_python_that_has_the_module_is_used(repo, tmp_path):
    install_installed(repo, pinned="/nonexistent/pinned/python")
    staged_clean(repo)
    log = tmp_path / "calls.log"
    proc = run_installed(repo, "pre-commit", fake_bin(tmp_path, {"python3": logged(log, "python3")}), env=module_env())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert any(c.startswith("python3") for c in calls(log))


@needs_sh
def test_a_launcher_that_cannot_run_falls_through_to_the_next_candidate(repo, tmp_path):
    install_installed(repo, pinned=sys.executable.replace("\\", "/"))
    staged_clean(repo)
    broken = "exit 127"                                         # a venv that was moved: the interpreter is not found
    proc = run_installed(repo, "pre-commit", fake_bin(tmp_path, {"aigate-lite": broken}), env=module_env())
    assert proc.returncode == 0, proc.stdout + proc.stderr


@needs_sh
def test_with_nothing_installed_the_commit_stops_with_the_install_command(repo, tmp_path):
    install_installed(repo, pinned="/nonexistent/pinned/python")
    staged_clean(repo)
    proc = run_installed(repo, "pre-commit", fake_bin(tmp_path, {}))
    assert proc.returncode == 1
    assert "aigate-lite is not installed; run: pipx install aigate-lite" in proc.stderr
    assert "git commit --no-verify" in proc.stderr
    push = run_installed(repo, "pre-push", fake_bin(tmp_path / "p", {}) if (tmp_path / "p").mkdir() is None else None)
    assert push.returncode == 1 and "git push --no-verify" in push.stderr


@needs_sh
def test_aigate_bin_is_used_for_people_and_ignored_for_agents(repo, tmp_path):
    install_installed(repo, pinned="/nonexistent/python")
    staged_clean(repo)
    log = tmp_path / "calls.log"
    other = tmp_path / "my tools"                                # a space, on purpose
    other.mkdir()
    exe = other / "gate"
    exe.write_bytes((f"#!/bin/sh\necho \"bin $*\" >> '{log.as_posix()}'\nexit 0\n").encode())
    exe.chmod(0o755)
    person = run_installed(repo, "pre-commit", fake_bin(tmp_path, {}), env={"AIGATE_BIN": str(exe).replace("\\", "/")})
    assert person.returncode == 0 and calls(log) == ["bin check --staged"]
    agent = run_installed(repo, "pre-commit", fake_bin(tmp_path / "b", {}) if (tmp_path / "b").mkdir() is None else None,
                          env={"AIGATE_BIN": str(exe).replace("\\", "/"), **AGENT})
    assert "AIGATE_BIN is ignored while an AI agent runs the commit" in agent.stderr
    assert calls(log) == ["bin check --staged"] and agent.returncode == 1        # never ran it; nothing else was found


@needs_sh
def test_an_agent_never_runs_a_command_found_by_name_and_runs_python_isolated(repo, tmp_path):
    install_installed(repo, pinned=sys.executable.replace("\\", "/"))
    staged_clean(repo)
    log = tmp_path / "calls.log"
    shim = f"echo \"shim $*\" >> '{log.as_posix()}'\nexit 0"                    # a planted aigate-lite that passes everything
    person_bin, agent_bin = tmp_path / "person", tmp_path / "agent"
    for d in (person_bin, agent_bin):
        d.mkdir()
    person = run_installed(repo, "pre-commit", fake_bin(person_bin, {"aigate-lite": shim}), env=module_env())
    assert person.returncode == 0 and calls(log) == ["shim check --staged"]
    agent = run_installed(repo, "pre-commit", fake_bin(agent_bin, {"aigate-lite": shim}), env={**module_env(), **AGENT})
    # the shim is never run for an agent, and PYTHONPATH is ignored (-I): the module is not found, so nothing passes
    assert agent.returncode == 1 and "an AI agent's commit does not look for another one on PATH" in agent.stderr
    assert calls(log) == ["shim check --staged"]


@needs_sh
def test_an_agent_with_the_pinned_python_gone_never_searches_path(repo, tmp_path):
    install_installed(repo, pinned="/nonexistent/pinned/python")
    repo.write("leak.py", 'KEY = "' + "AKIA" + 'QWERTYUIOPASDFGH"\n')                 # a commit that must be blocked
    repo.add()
    log = tmp_path / "calls.log"
    fake = f"echo \"python3 $*\" >> '{log.as_posix()}'\nexit 0"                         # a stand-in that passes everything
    proc = run_installed(repo, "pre-commit", fake_bin(tmp_path, {"python3": fake}), env={**module_env(), **AGENT})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "an AI agent's commit does not look for another one on PATH" in proc.stderr
    assert "/nonexistent/pinned/python: missing (pinned at install)" in proc.stderr
    assert "--no-verify" not in proc.stderr
    assert calls(log) == []                                                            # the stand-in was never called


@needs_sh
def test_a_person_with_the_pinned_python_gone_still_falls_back_to_path(repo, tmp_path):
    install_installed(repo, pinned="/nonexistent/pinned/python")
    staged_clean(repo)
    log = tmp_path / "calls.log"
    proc = run_installed(repo, "pre-commit", fake_bin(tmp_path, {"python3": logged(log, "python3")}), env=module_env())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert any(c.startswith("python3") for c in calls(log))


@needs_sh
@pytest.mark.parametrize("kind,what", [("pre-commit", "commit"), ("pre-push", "push")])
def test_the_vendored_hook_never_rediscovers_python_for_an_agent(repo, tmp_path, kind, what):
    from test_hooks import install_vendored
    install_vendored(repo)                                                             # the pin is removed again
    staged_clean(repo)
    log = tmp_path / "calls.log"
    fake = f"echo \"python3 $*\" >> '{log.as_posix()}'\nexit 0"
    stdin = "refs/heads/main 1 refs/heads/main 0\n" if kind == "pre-push" else ""
    proc = run_installed(repo, kind, fake_bin(tmp_path, {"python3": fake, "python": fake}), env=AGENT, stdin=stdin)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"an AI agent's {what} does not look for another one on PATH" in proc.stderr
    assert "/nonexistent/pinned/python" in proc.stderr and "--no-verify" not in proc.stderr
    assert calls(log) == []
    (tmp_path / "p").mkdir()
    real = logged(log, "python3")                                                      # answers the version probe, then runs for real
    person = run_installed(repo, kind, fake_bin(tmp_path / "p", {"python3": real}), stdin=stdin)
    assert calls(log) and person.returncode == 0                                       # people keep the PATH fallback


def test_setup_does_not_suggest_init_right_before_running_it(repo):
    proc = repo.run("setup")
    assert proc.returncode == 0 and "optional: `aigate-lite init`" not in proc.stdout
    assert (repo.path / ".aigate" / "lite.toml").is_file()
    (repo.path / ".aigate" / "lite.toml").unlink()
    plain = repo.run("install", "--force")                                              # install on its own still points at init
    assert "optional: `aigate-lite init`" in plain.stdout


@needs_sh
def test_a_provenance_of_agent_also_ignores_aigate_bin(repo, tmp_path):
    install_installed(repo, pinned="/nonexistent/python")
    exe = tmp_path / "gate"
    exe.write_bytes(b"#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    proc = run_installed(repo, "pre-commit", fake_bin(tmp_path, {}), env={"AIGATE_BIN": str(exe), "AIGATE_PROVENANCE": " Agent "})
    assert "AIGATE_BIN is ignored" in proc.stderr


@needs_sh
def test_paths_with_spaces_work_for_the_repository_the_pin_and_the_command(tmp_path):
    base = tmp_path / "a folder with spaces"
    (base / "my repo").mkdir(parents=True)
    repo = GitRepo(base / "my repo", base / "cache")
    repo.write("README.md", "# demo\n")
    repo.commit("init")
    py = base / "py dir"
    py.mkdir()
    wrapper = py / "python"
    wrapper.write_bytes(f"#!/bin/sh\nexec '{REAL_PY}' \"$@\"\n".encode())
    wrapper.chmod(0o755)
    install_installed(repo, pinned=str(wrapper).replace("\\", "/"))
    staged_clean(repo)
    proc = run_installed(repo, "pre-commit", fake_bin(tmp_path, {}), env=module_env())
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ------------------------------------------------------------------------------------------------ commands

def test_install_mode_installed_from_a_loose_file_says_so(repo):
    proc = repo.run("install", "--mode", "installed")
    assert proc.returncode == 0
    assert "hook mode: installed" in proc.stdout and "not an installed package" in proc.stdout
    assert "hook Python:" not in proc.stdout
    assert aigate.hook_mode((repo.path / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")) == "installed"
    assert not (repo.path / ".aigate" / "aigate_lite.py").exists()


def test_plain_install_from_a_loose_file_keeps_pinning_the_file_and_vendor_is_an_alias(repo):
    assert repo.run("install").returncode == 0
    assert aigate.hook_mode((repo.path / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")) == "path"
    assert repo.run("install", "--mode", "vendored").returncode == 0
    assert (repo.path / ".aigate" / "aigate_lite.py").is_file()
    assert aigate.hook_mode((repo.path / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")) == "vendored"


def test_setup_from_a_loose_file_vendors_and_says_why(repo):
    proc = repo.run("setup")
    assert proc.returncode == 0 and (repo.path / ".aigate" / "aigate_lite.py").is_file()
    assert "running from a loose aigate_lite.py" in proc.stdout and "pipx install aigate-lite" in proc.stdout


def test_setup_mode_installed_copies_nothing(repo):
    proc = repo.run("setup", "--mode", "installed")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (repo.path / ".aigate" / "aigate_lite.py").exists() and (repo.path / ".aigate" / "lite.toml").is_file()
    assert "git add .aigate && git commit" in proc.stdout


def test_setup_keeps_a_vendored_repository_vendored_and_names_the_switch(repo):
    assert repo.run("install", "--vendor").returncode == 0
    proc = repo.run("setup")
    assert proc.returncode == 0 and "already has .aigate/aigate_lite.py" in proc.stdout and "aigate-lite unvendor" in proc.stdout
    assert aigate.hook_mode((repo.path / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")) == "vendored"


def test_unvendor_needs_an_installed_package_unless_forced(repo):
    assert repo.run("install", "--vendor").returncode == 0
    proc = repo.run("unvendor")
    assert proc.returncode == 1 and "pipx install aigate-lite" in proc.stderr
    assert aigate.hook_mode((repo.path / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")) == "vendored"


def test_unvendor_round_trip_prints_but_never_runs_the_git_rm(repo):
    assert repo.run("install", "--vendor").returncode == 0
    copy = repo.path / ".aigate" / "aigate_lite.py"
    before = copy.read_bytes()
    proc = repo.run("unvendor", "--force")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "git rm .aigate/aigate_lite.py .aigate/NOTICE" in proc.stdout and "did not run this" in proc.stdout
    assert copy.read_bytes() == before                                       # nothing was removed
    assert aigate.hook_mode((repo.path / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")) == "installed"
    back = repo.run("install", "--vendor")
    assert back.returncode == 0
    assert aigate.hook_mode((repo.path / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")) == "vendored"


def test_doctor_reports_mode_version_copy_and_requires(repo):
    repo.write(".aigate/lite.toml", f'[gate]\nrequires = "{aigate.requires_for(aigate.__version__)}"\n')
    assert repo.run("install", "--vendor").returncode == 0
    checks = {c["id"]: c for c in json.loads(repo.run("doctor", "--offline", "--format", "json").stdout)}
    detail = checks["install"]["detail"]
    assert "hook mode: vendored" in detail and f"running aigate-lite {aigate.__version__} (loose file)" in detail
    assert f"vendored copy: {aigate.__version__}" in detail and f"requires: {aigate.requires_for(aigate.__version__)}" in detail


def test_doctor_fails_when_the_running_version_is_below_the_pin(repo):
    repo.write(".aigate/lite.toml", '[gate]\nrequires = ">=9.0"\n')
    assert repo.run("install", "--mode", "installed").returncode == 0
    proc = repo.run("doctor", "--offline", "--format", "json")
    checks = {c["id"]: c for c in json.loads(proc.stdout)}
    assert checks["install"]["status"] == "fail" and "pipx upgrade aigate-lite" in checks["install"]["fix"] and proc.returncode == 1


def test_doctor_warns_about_a_hook_that_pins_the_script_location(repo):
    assert repo.run("install").returncode == 0
    checks = {c["id"]: c for c in json.loads(repo.run("doctor", "--offline", "--format", "json").stdout)}
    assert checks["install"]["status"] == "warn" and "hook mode: path" in checks["install"]["detail"]


def test_doctor_fails_an_installed_hook_that_cannot_find_the_tool(repo):
    assert repo.run("install", "--mode", "installed").returncode == 0
    hook = repo.path / ".git" / "hooks" / "pre-commit"
    hook.write_text(hook.read_text(encoding="utf-8").replace("PINNED='" + sys.executable.replace("\\", "/") + "'",
                                                             "PINNED='/nonexistent/python'"), encoding="utf-8")
    env = {"PATH": os.path.dirname(shutil.which("git"))}
    checks = {c["id"]: c for c in json.loads(repo.run("doctor", "--offline", "--format", "json", env=env).stdout)}
    assert checks["python.hook"]["status"] == "fail" and "pipx install aigate-lite" in checks["python.hook"]["fix"]


# ------------------------------------------------------------------------------------------------ a real installed package

def build_wheel(dest):
    """A pure-Python wheel of this checkout, built by hand (no build backend, no network)."""
    ver = aigate.__version__
    dist = f"aigate_lite-{ver}.dist-info"
    files = {"aigate_lite.py": SCRIPT.read_bytes(),
             f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: aigate-lite\nVersion: {ver}\nRequires-Python: >=3.11\n".encode(),
             f"{dist}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
             f"{dist}/entry_points.txt": b"[console_scripts]\naigate-lite = aigate_lite:main\naigate = aigate_lite:main\n"}
    rows = [f"{n},sha256={base64.urlsafe_b64encode(hashlib.sha256(b).digest()).rstrip(b'=').decode()},{len(b)}"
            for n, b in files.items()] + [f"{dist}/RECORD,,"]
    wheel = dest / f"aigate_lite-{ver}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in files.items():
            z.writestr(n, b)
        z.writestr(f"{dist}/RECORD", "\n".join(rows) + "\n")
    return wheel


def unpack_wheel(py, wheel):
    """What an installer does, for machines whose Python cannot bootstrap pip into a venv (no ensurepip): unpack the wheel
    into the venv's site-packages (its .dist-info is the installed-package record) and write the console script."""
    purelib = subprocess.run([str(py), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                             capture_output=True, text=True, check=True).stdout.strip()
    with zipfile.ZipFile(wheel) as z:
        z.extractall(purelib)
    launcher = py.parent / "aigate-lite"
    launcher.write_bytes(f"#!/bin/sh\nexec '{py.as_posix()}' -m aigate_lite \"$@\"\n".encode())
    launcher.chmod(0o755)


def make_venv(path, wheel):
    made = subprocess.run([sys.executable, "-m", "venv", str(path)], capture_output=True, text=True)
    with_pip = made.returncode == 0
    if not with_pip:
        shutil.rmtree(path, ignore_errors=True)
        made = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(path)], capture_output=True, text=True)
        if made.returncode or os.name == "nt":
            pytest.skip("cannot create a virtual environment here: " + (made.stderr or made.stdout)[-200:])
    py = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not with_pip:
        unpack_wheel(py, wheel)
        return py
    done = subprocess.run([str(py), "-m", "pip", "install", "--no-index", "--no-deps", "--disable-pip-version-check",
                           str(wheel)], capture_output=True, text=True)
    if done.returncode:
        pytest.skip("cannot install the wheel offline here: " + done.stderr[-200:])
    return py


@pytest.fixture(scope="module")
def venv(tmp_path_factory):
    base = tmp_path_factory.mktemp("installed")
    wheel = build_wheel(base)
    path = base / "env dir"                                      # a space, on purpose
    return {"wheel": wheel, "path": path, "py": make_venv(path, wheel),
            "bin": path / ("Scripts" if os.name == "nt" else "bin")}


def in_venv(venv, repo, *args, env=None):
    e = {**repo.env, "PATH": os.pathsep.join([str(venv["bin"]), repo.env["PATH"]]), **(env or {})}
    return subprocess.run([str(venv["bin"] / "aigate-lite"), *args], cwd=repo.path, env=e, capture_output=True, text=True)


def test_an_installed_package_knows_it_is_installed(venv, tmp_path):
    out = subprocess.run([str(venv["py"]), "-c", "import aigate_lite; print(aigate_lite.installed_package(), aigate_lite.__version__)"],
                         cwd=tmp_path, capture_output=True, text=True)
    assert out.stdout.split() == ["True", aigate.__version__], out.stdout + out.stderr


def test_setup_from_an_installed_package_does_not_copy_the_gate(venv, repo):
    proc = in_venv(venv, repo, "setup")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "hook mode: installed" in proc.stdout and "not an installed package" not in proc.stdout
    assert not (repo.path / ".aigate" / "aigate_lite.py").exists()
    text = (repo.path / ".aigate" / "lite.toml").read_text(encoding="utf-8")
    assert f'requires = "{aigate.requires_for(aigate.__version__)}"' in text
    hook = (repo.path / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
    assert "site-packages" not in hook and "env dir" in hook           # only the pinned interpreter is recorded


@needs_sh
def test_the_hook_runs_the_installed_command_end_to_end(venv, repo, tmp_path):
    assert in_venv(venv, repo, "setup").returncode == 0
    staged_clean(repo)
    proc = run_installed(repo, "pre-commit", fake_bin(tmp_path, {}), extra_path=[venv["bin"]])
    assert proc.returncode == 0, proc.stdout + proc.stderr


@needs_sh
def test_the_hook_runs_through_the_pinned_python_when_the_command_is_not_on_path(venv, repo, tmp_path):
    assert in_venv(venv, repo, "setup").returncode == 0
    staged_clean(repo)
    proc = run_installed(repo, "pre-commit", fake_bin(tmp_path, {}))            # PATH has no aigate-lite
    assert proc.returncode == 0, proc.stdout + proc.stderr


@needs_sh
def test_an_agent_commit_runs_the_installed_package_through_the_pinned_python(venv, repo, tmp_path):
    assert in_venv(venv, repo, "setup").returncode == 0
    repo.commit("gate config")                               # an agent may not change .aigate/ itself
    staged_clean(repo)
    shim = "exit 0"                                       # a planted `aigate-lite` that would pass everything
    proc = run_installed(repo, "pre-commit", fake_bin(tmp_path, {"aigate-lite": shim}), env=AGENT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    repo.write("leak.py", 'KEY = "' + "AKIA" + 'QWERTYUIOPASDFGH"\n')
    repo.add()
    other = tmp_path / "other"
    other.mkdir()
    blocked = run_installed(repo, "pre-commit", fake_bin(other, {"aigate-lite": shim}), env=AGENT)
    assert blocked.returncode == 1, "the real gate ran, not the shim"


def test_unvendor_warns_when_the_installed_gate_is_older_than_the_copy(repo):
    assert repo.run("install", "--vendor").returncode == 0
    copy = repo.path / ".aigate" / "aigate_lite.py"
    copy.write_text(copy.read_text(encoding="utf-8").replace(f'__version__ = "{aigate.__version__}"', '__version__ = "9.9.9"'),
                    encoding="utf-8")
    proc = repo.run("unvendor", "--force")
    assert "the vendored copy is 9.9.9" in proc.stderr


@needs_sh
def test_unvendor_from_an_installed_package_switches_modes(venv, repo):
    assert repo.run("install", "--vendor").returncode == 0
    proc = in_venv(venv, repo, "unvendor")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert aigate.hook_mode((repo.path / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")) == "installed"
    assert "git rm .aigate/aigate_lite.py .aigate/NOTICE" in proc.stdout


@needs_sh
@pytest.mark.skipif(os.name == "nt", reason="a moved venv's launcher failure is not distinguishable on Windows")
def test_a_venv_that_was_moved_and_recreated_keeps_working_without_reinstalling_the_hook(venv, repo, tmp_path):
    base = tmp_path / "pipx"
    base.mkdir()
    first = base / "venv"
    make_venv(first, venv["wheel"])
    e = {**repo.env, "PATH": os.pathsep.join([str(first / "bin"), repo.env["PATH"]])}
    assert subprocess.run([str(first / "bin" / "aigate-lite"), "setup"], cwd=repo.path, env=e, capture_output=True).returncode == 0
    staged_clean(repo)
    stale = fake_bin(tmp_path, {})
    os.replace(first, base / "venv-moved")                         # the venv is gone from its path
    gone = run_installed(repo, "pre-commit", stale, extra_path=[first / "bin"])
    assert gone.returncode == 1 and "aigate-lite is not installed" in gone.stderr
    make_venv(first, venv["wheel"])                                # pipx reinstall: same path, fresh venv
    again = run_installed(repo, "pre-commit", stale, extra_path=[first / "bin"])
    assert again.returncode == 0, again.stdout + again.stderr
