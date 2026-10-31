import json

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
