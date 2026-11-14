import json

from conftest import find

AWS = "AKIA" + "qwertyuiopasdfgh".upper()                 # split so this file is not itself a finding


def committed_leak(repo):
    repo.write("app/keys.py", f'KEY = "{AWS}"\n')
    repo.commit("add a key")


def comparable(proc):
    data = json.loads(proc.stdout)
    data.pop("stats", None)
    return data


# ---------------------------------------------------------------- setup

def test_setup_installs_hooks_the_script_and_a_config(repo):
    proc = repo.run("setup")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for hook in ("pre-commit", "pre-push"):
        assert (repo.path / ".git" / "hooks" / hook).is_file()
    assert (repo.path / ".aigate" / "aigate_lite.py").is_file()
    assert (repo.path / ".aigate" / "lite.toml").is_file()
    for step in ("Setup done. Next steps:", "aigate-lite audit", "aigate-lite baseline",
                 "git add .aigate && git commit -m 'add aigate-lite'"):
        assert step in proc.stdout


def test_setup_run_twice_keeps_the_config(repo):
    assert repo.run("setup", "--preset", "strict").returncode == 0
    config = repo.path / ".aigate" / "lite.toml"
    config.write_text(config.read_text(encoding="utf-8") + "# my own edit\n", encoding="utf-8")
    before = config.read_bytes()
    second = repo.run("setup")
    assert second.returncode == 0, second.stdout + second.stderr
    assert config.read_bytes() == before


def test_setup_with_a_foreign_hook_stops_unless_forced(repo):
    hook = repo.path / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 0\n")
    assert repo.run("setup").returncode == 1
    assert repo.run("setup", "--force").returncode == 0
    assert (repo.path / ".git" / "hooks" / "pre-commit.local").exists()


# ---------------------------------------------------------------- audit

def test_audit_reports_a_committed_secret(repo):
    committed_leak(repo)
    proc = repo.run("audit", "--offline")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "secrets.hardcoded" in proc.stdout


def test_audit_quiet_and_verbose_do_not_crash(repo):
    committed_leak(repo)
    for flag in ("-q", "--quiet", "-v", "--verbose"):
        proc = repo.run("audit", "--offline", flag)
        assert proc.returncode == 1, (flag, proc.stdout, proc.stderr)
        assert "Traceback" not in proc.stderr and "internal error" not in proc.stderr


def test_audit_json_matches_check_all_json(repo):
    committed_leak(repo)
    audit = repo.run("audit", "--offline", "--format", "json")
    check = repo.run("check", "--all", "--offline", "--format", "json")
    assert audit.returncode == check.returncode == 1
    assert comparable(audit) == comparable(check)
    assert find(json.loads(audit.stdout), "secrets.hardcoded")


def test_audit_on_a_clean_repo_passes(repo):
    proc = repo.run("audit", "--offline")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_audit_does_not_escalate_and_agent_format_works(repo):
    committed_leak(repo)
    proc = repo.run("audit", "--offline", "--format", "agent")
    assert proc.returncode == 1 and proc.stdout.startswith("AIGATE verdict=") and proc.stdout.rstrip().endswith("END")


def test_bare_command_still_runs_check(repo):
    repo.write("a.py", f'KEY = "{AWS}"\n')
    repo.add()
    assert repo.run().returncode == 1


def test_setup_and_audit_are_listed_early_in_help(repo):
    text = repo.run("--help").stdout
    assert text.index("audit") < text.index("install") and text.index("setup") < text.index("install")
