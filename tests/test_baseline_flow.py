"""Part B of the command-level defects: an uncommitted .aigate/baseline.json is said to be unapplied (and `adopt a baseline` is not
suggested), and `baseline` ends with the two git commands. User flows: the CLI runs in a temporary repository."""
import json
import subprocess
import sys

from conftest import SCRIPT, find

EVAL = "ev" + "al(y)"
PENDING = "baseline.json exists but is not committed, so it is not applied yet: git add .aigate/baseline.json && git commit"
ADD = "git add .aigate/baseline.json"
COMMIT = 'git commit -m "Add aigate-lite baseline"'


def old_project(repo):
    repo.write("a.py", f"def f(y):\n    return {EVAL}\n")
    repo.write("b.py", f"def g(y):\n    return {EVAL}\n")
    repo.commit("old code")


def written(repo):
    old_project(repo)
    proc = repo.run("baseline")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return proc


# ---------------------------------------------------------------------------------------- B2: the two git commands

def test_baseline_output_ends_with_the_exact_two_git_commands(repo):
    # the last line still starts with "next:" (tests/test_baseline.py pins that); it holds the two commands, joined by &&
    last = written(repo).stdout.splitlines()[-1]
    assert last.startswith("next: ") and last.endswith(f"{ADD} && {COMMIT}")


def test_the_printed_commands_work_as_printed(repo):
    last = written(repo).stdout.splitlines()[-1]
    add, commit = last.split("then: ", 1)[1].split(" && ", 1)
    assert (add, commit) == (ADD, COMMIT)
    assert repo.git("add", add.split(" ", 2)[2]).returncode == 0
    done = repo.git("commit", "-q", "--no-verify", "-m", "Add aigate-lite baseline")
    assert done.returncode == 0 and ".aigate/baseline.json" in repo.git("show", "--stat", "HEAD").stdout


def test_prune_also_ends_with_the_commands(repo):
    written(repo)
    last = repo.run("baseline", "--prune").stdout.splitlines()[-1]
    assert last.endswith(f"{ADD} && {COMMIT}")


def test_from_a_subfolder_the_add_command_names_a_path_that_exists_from_there(repo):
    old_project(repo)
    (repo.path / "sub").mkdir()
    sub = subprocess.run([sys.executable, str(SCRIPT), "baseline"], cwd=repo.path / "sub", env=repo.env, capture_output=True,
                         text=True)
    assert sub.returncode == 0, sub.stderr
    assert sub.stdout.splitlines()[-1].endswith(f"git add ../.aigate/baseline.json && {COMMIT}")


# --------------------------------------------------------------------------------- B1: written, but not committed yet

def test_audit_says_an_uncommitted_baseline_is_not_applied_and_does_not_say_adopt(repo):
    written(repo)
    proc = repo.run("audit", "--offline")
    assert proc.returncode == 1
    assert PENDING in proc.stdout
    assert "adopt a baseline" not in proc.stdout
    assert "aigate-lite baseline" not in proc.stdout.replace(PENDING, "")
    assert "in baseline" not in proc.stdout                            # nothing was hidden: it really is not applied


def test_the_next_line_tells_the_person_to_commit_the_baseline(repo):
    written(repo)
    nxt = [ln for ln in repo.run("audit", "--offline").stdout.splitlines() if ln.startswith("next:")]
    assert len(nxt) == 1 and "git add .aigate/baseline.json && git commit" in nxt[0]


def test_a_staged_check_with_an_untracked_baseline_says_the_same(repo):
    written(repo)
    repo.write("c.py", f"def h(y):\n    return {EVAL}\n")
    repo.add("c.py")
    proc = repo.run("check", "--staged", "--offline")
    assert proc.returncode == 1 and PENDING in proc.stdout and "adopt a baseline" not in proc.stdout


def test_a_baseline_staged_but_not_committed_is_applied_to_a_staged_check_and_needs_no_warning(repo):
    written(repo)
    repo.add(".aigate/baseline.json")
    repo.write("c.py", "x = 1\n")
    repo.add("c.py")
    proc = repo.run("check", "--staged", "--offline")
    assert PENDING not in proc.stdout


def test_after_the_commit_the_message_is_gone_and_old_findings_are_hidden(repo):
    written(repo)
    repo.commit("baseline")
    proc = repo.run("audit", "--offline")
    assert proc.returncode == 0 and PENDING not in proc.stdout and "adopt a baseline" not in proc.stdout


def test_json_carries_the_message_in_notes_and_nothing_else_changes(repo):
    written(repo)
    res = repo.check("--all", "--offline")
    assert PENDING in res["notes"]
    assert find(res, "security.eval") and all(f["allowed"] is None for f in find(res, "security.eval"))


def test_an_agent_is_told_the_same(repo):
    written(repo)
    proc = repo.run("audit", "--offline", env={"CLAUDECODE": "1"})
    assert PENDING in proc.stdout and "adopt a baseline" not in proc.stdout


def test_without_a_baseline_file_audit_still_suggests_adopting_one(repo):
    old_project(repo)
    proc = repo.run("audit", "--offline")
    assert "adopt a baseline" in proc.stdout and PENDING not in proc.stdout


def test_a_baseline_the_repo_already_committed_is_not_reported_as_pending(repo):
    written(repo)
    repo.commit("baseline")
    data = json.loads((repo.path / ".aigate" / "baseline.json").read_text())
    assert data["entries"] and PENDING not in repo.run("check", "--all", "--offline").stdout
