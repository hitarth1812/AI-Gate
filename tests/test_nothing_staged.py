"""`check` with nothing staged still honours --format: machine readers get a valid empty report, not silence."""
import json

from test_ci import assert_minimal_sarif
from test_credit import AUTHOR, CHECK_KEYS, URL, assert_types


def test_json_nothing_staged_is_an_empty_report(repo):
    proc = repo.run("check", "--format", "json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert_types(data, CHECK_KEYS, {"author": AUTHOR, "url": URL})
    assert data["verdict"] == "pass" and data["findings"] == [] and data["exit_code"] == 0
    assert data["label"] == "staged changes" and data["risk"] == 0 and data["escalated"] is False
    assert data["stats"]["files"] == 0 and data["provenance"]["kind"] == "human"
    repo.write("a.txt", "hello\n")
    repo.add()
    normal = repo.check()                                 # same top-level keys as a run that found something to scan
    assert set(data) == set(normal) - {"code"}
    assert set(data["stats"]) == set(normal["stats"])


def test_sarif_nothing_staged_is_valid_and_empty(repo):
    proc = repo.run("check", "--format", "sarif")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert_minimal_sarif(doc)
    assert doc["runs"][0]["results"] == []


def test_github_nothing_staged_prints_only_the_summary(repo):
    proc = repo.run("check", "--format", "github")
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("aigate-lite verdict: PASS") and "0 findings" in lines[0]


def test_text_and_agent_nothing_staged_are_unchanged(repo):
    text = repo.run("check")
    assert text.returncode == 0 and text.stdout == "aigate-lite: nothing staged to check\n"
    agent = repo.run("check", "--format", "agent")
    assert agent.returncode == 0
    assert agent.stdout.startswith("AIGATE verdict=pass risk=0 exit=0\n") and agent.stdout.endswith("\nEND\n")
    as_agent = repo.run("check", "--format", "agent", env={"CLAUDECODE": "1"})
    assert as_agent.returncode == 0 and as_agent.stdout == agent.stdout


def test_json_nothing_staged_for_an_agent_is_the_same_pass(repo):
    proc = repo.run("check", "--format", "json", env={"CLAUDECODE": "1"})
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["verdict"] == "pass" and data["findings"] == [] and data["provenance"]["kind"] == "agent"


def test_all_with_every_file_ignored_still_reports_in_every_format(repo):
    repo.write(".aigate/lite.toml", '[paths]\nignore = ["**"]\n')
    repo.commit("ignore everything")
    data = json.loads(repo.run("check", "--all", "--format", "json").stdout)
    assert data["verdict"] == "pass" and data["stats"]["files"] == 0
    assert_minimal_sarif(json.loads(repo.run("check", "--all", "--format", "sarif").stdout))
    assert repo.run("check", "--all", "--format", "github").stdout.startswith("aigate-lite verdict: PASS")
