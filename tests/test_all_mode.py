import json

from conftest import find

SHELL = "import subprocess\n\n\ndef f{i}(x):\n    return subprocess.run(x, shell=True)\n"
AWS = "AKIA" + "qwertyuiopasdfgh".upper()                 # split so this file is not itself a finding


def shells(repo, n=4):
    for i in range(n):                             # 4 fix findings: risk 64, over the escalation threshold
        repo.write(f"app/m{i}.py", SHELL.format(i=i))


def full(repo):
    proc = repo.run("check", "--all", "--format", "json")
    assert proc.returncode in (0, 1), proc.stderr + proc.stdout
    return {**json.loads(proc.stdout), "code": proc.returncode}


def test_all_mode_reports_the_risk_but_never_escalates(repo):
    shells(repo)
    repo.commit("shells")
    res = full(repo)
    assert len(find(res, "security.shell")) == 4 and res["risk"] >= 60
    assert (res["verdict"], res["escalated"], res["code"]) == ("fix", False, 1)


def test_staged_and_range_checks_still_escalate(repo):
    shells(repo)
    repo.add()
    staged = repo.check()
    assert (staged["verdict"], staged["escalated"]) == ("block", True)
    repo.commit("shells")
    ranged = repo.check("--range", "HEAD~1..HEAD")
    assert (ranged["verdict"], ranged["escalated"]) == ("block", True)


def test_all_mode_still_blocks_on_a_blocking_finding(repo):
    shells(repo)
    repo.write("cfg/keys.py", f'K = "{AWS}"\n')
    repo.commit("both")
    res = full(repo)
    assert (res["verdict"], res["escalated"]) == ("block", False) and find(res, "secrets.hardcoded")


def test_all_mode_under_the_threshold_is_unchanged(repo):
    shells(repo, 1)
    repo.commit("one")
    res = full(repo)
    assert (res["verdict"], res["escalated"]) == ("fix", False) and res["risk"] < 60


def test_all_mode_text_report_matches_the_json(repo):
    shells(repo)
    repo.commit("shells")
    text = repo.run("check", "--all", "--color", "never")
    staged = repo.run("check", "--range", "HEAD~1..HEAD", "--color", "never")
    assert text.returncode == 1 and "escalated" not in text.stdout
    assert "risk score escalated it" in staged.stdout


def test_an_agent_audit_uses_the_committed_config_not_the_worktree(repo):
    shells(repo)
    repo.write(".aigate/lite.toml", '[rules]\n"security.shell" = "warn"\n')
    repo.commit("shells + config")
    repo.write(".aigate/lite.toml", '[rules]\n"security.*" = "off"\n')         # uncommitted loosening
    proc = repo.run("check", "--all", "--format", "json", env={"CLAUDECODE": "1"})
    res = json.loads(proc.stdout)
    assert res["provenance"]["kind"] == "agent" and res["config"].startswith(".aigate/lite.toml @ ")
    assert {f["action"] for f in find(res, "security.shell")} == {"warn"} and res["verdict"] == "warn"
