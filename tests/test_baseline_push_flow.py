"""A committed baseline on a push or a range. User flows: the CLI runs in temporary repositories (the `repo` fixture).

What these tests pin:
  * a person's first push of a repository that adopted a baseline passes, and says which baseline did it;
  * in a range the baseline of the base counts, never entries the change itself adds (they raise a warning);
  * an AI agent's result is byte for byte what 0.6.1 printed (the golden comparison runs the tagged release);
  * `apply_on_push = false` gives a person 0.6.1's answer;
  * secrets, copies of a baselined pattern and several refs are never softened.
"""
import json
import os
import re
import subprocess
import sys

import pytest

from conftest import ROOT, SCRIPT, aigate, child_encoding, decode_output, find

EVAL = "ev" + "al(y)"
AWS = "AKIA" + "qwertyuiopasdfgh".upper()
ZERO = "0" * 40
BASELINE = ".aigate/baseline.json"
OLD_TAG = "v0.6.1"


def old_script(tmp_path):
    """The tool as released in 0.6.1, read from the tag (never a moving branch)."""
    proc = subprocess.run(["git", "-C", str(ROOT), "show", f"{OLD_TAG}:aigate_lite.py"], capture_output=True)
    if proc.returncode != 0:
        pytest.fail(f"the tag {OLD_TAG} is missing, so the golden comparison cannot run: git fetch --tags")
    path = tmp_path / "old_tool" / "aigate_lite.py"
    path.parent.mkdir()
    path.write_bytes(proc.stdout)
    return path


def gate(repo, *args, script=SCRIPT, env=None, input=None):
    env = {**repo.env, **(env or {})}
    enc = child_encoding(env)
    data = input.replace("\n", os.linesep).encode(enc) if input is not None else None
    proc = subprocess.run([sys.executable, str(script), *args], cwd=repo.path, env=env, capture_output=True, input=data)
    proc.stdout, proc.stderr = decode_output(proc.stdout, enc), decode_output(proc.stderr, enc)
    return proc


def sha(repo, rev="HEAD"):
    return repo.git("rev-parse", rev).stdout.strip()


def push_line(repo, ref="main", remote=ZERO):
    return f"refs/heads/{ref} {sha(repo, ref)} refs/heads/{ref} {remote}\n"


def push(repo, *extra, lines=None, script=SCRIPT, env=None, fmt="json"):
    """`check --push` as the pre-push hook feeds it; the parsed JSON (plus "code") or, for another format, the process."""
    stdin = lines if lines is not None else push_line(repo)
    proc = gate(repo, "check", "--push", "--offline", "--format", fmt, *extra, script=script, env=env, input=stdin)
    if fmt != "json":
        return proc
    assert proc.returncode in (0, 1), proc.stderr + proc.stdout
    data = json.loads(proc.stdout)
    data["code"] = proc.returncode
    return data


def span(repo, spec, *extra, script=SCRIPT, env=None):
    proc = gate(repo, "check", "--range", spec, "--offline", "--format", "json", *extra, script=script, env=env)
    assert proc.returncode in (0, 1), proc.stderr + proc.stdout
    data = json.loads(proc.stdout)
    data["code"] = proc.returncode
    return data


def active(res, rule=None):
    return [f for f in res["findings"] if not f["allowed"] and (rule is None or f["rule"] == rule)]


def evals(res):
    return sorted((f["path"], f["line"]) for f in active(res, "security.eval"))


def hidden(res):
    return [f for f in res["findings"] if f["allowed"] == "baseline"]


def entries_at(repo, rev):
    return len(json.loads(repo.git("show", f"{rev}:{BASELINE}").stdout)["entries"])


def old_project(repo, *files):
    """Files that already hold debt: each is one function (a.py: two identical ones, so a twin number is in play)."""
    for name in files or ("a.py", "b.py", "c.py"):
        body = f"def f(y):\n    return {EVAL}\n"
        if name == "a.py":
            body += f"\n\ndef g(y):\n    return {EVAL}\n"
        repo.write(name, body)
    repo.commit("old code")


def adopt(repo, *files):
    """An existing project that adopted the gate: its debt, then the committed baseline."""
    old_project(repo, *files)
    proc = repo.run("baseline")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    repo.commit("Add aigate-lite baseline")


def branch(repo, name="topic"):
    repo.git("checkout", "-q", "-b", name)


# ------------------------------------------------------------------ 1. a person's first push after adopting a baseline

def test_first_push_after_adopting_a_baseline_passes_and_names_the_commit(repo):
    adopt(repo)
    res = push(repo)
    assert res["code"] == 0 and res["verdict"] in ("pass", "warn") and not active(res, "security.eval")
    n = len(hidden(res))
    assert n >= 4                                                      # three files, one with a twin
    where = repo.git("log", "-1", "--format=%H", "--", BASELINE).stdout.strip()[:7]
    assert f"{n} findings are accepted by {BASELINE} (committed in {where}); -v lists them" in res["notes"]
    assert repo.run("audit", "--offline").returncode == 0               # the same answer as auditing the tip


def test_the_text_report_of_that_push_prints_the_note_and_lets_the_push_through(repo):
    adopt(repo)
    proc = push(repo, "--color", "never", fmt="text")
    assert proc.returncode == 0, proc.stdout
    assert re.search(r"  note: \d+ findings are accepted by \.aigate/baseline\.json \(committed in [0-9a-f]{7}\); -v lists them",
                     proc.stdout), proc.stdout
    assert "push allowed" in proc.stdout and "baseline findings hidden" in proc.stdout
    assert " FIX " not in proc.stdout and "BLOCK" not in proc.stdout


def test_one_accepted_finding_reads_in_the_singular(repo):
    adopt(repo, "b.py")
    res = push(repo)
    assert res["code"] == 0 and len(hidden(res)) == 1
    assert re.search(rf"^1 finding is accepted by {re.escape(BASELINE)} \(committed in [0-9a-f]{{7}}\); -v lists them$",
                     "\n".join(res["notes"]), re.M), res["notes"]


def test_a_new_branch_that_grows_from_a_commit_the_remote_has_uses_the_baseline_of_that_commit(repo):
    adopt(repo)
    repo.git("update-ref", "refs/remotes/origin/main", "HEAD")             # the remote holds the adopted state
    branch(repo)
    repo.write("a.py", f"def f(y):\n    return {EVAL}  # reviewed\n\n\ndef g(y):\n    return {EVAL}\n")   # an edited, baselined line
    repo.write("d.py", f"def h(y):\n    return {EVAL}\n")                                              # a new debt
    repo.commit("work")
    res = push(repo, lines=push_line(repo, "topic"))
    assert evals(res) == [("d.py", 2)]                                  # only the new line stops the push
    assert [f["path"] for f in hidden(res)] == ["a.py"] and res["code"] == 1


# ------------------------------------------------------------------ 2. a range: the baseline of the base

def test_range_hides_baselined_debt_on_an_edited_line_and_reports_a_new_finding(repo):
    adopt(repo)
    branch(repo)
    repo.write("a.py", f"def f(y):\n    return {EVAL}  # reviewed\n\n\ndef g(y):\n    return {EVAL}\n")
    repo.write("d.py", f"def h(y):\n    return {EVAL}\n")
    repo.commit("work")
    res = span(repo, "main..topic")
    assert evals(res) == [("d.py", 2)]
    assert [f["path"] for f in hidden(res)] == ["a.py"]
    assert not find(res, "gate.baseline_changed")                       # the change did not touch the baseline


# ------------------------------------------------------------------ 3. a range that adds baseline entries for its own findings

def debt_and_self_baseline(repo):
    """The base holds one unbaselined eval; the change touches that line, adds a second eval and baselines both."""
    repo.write("app.py", f"def f(y):\n    return {EVAL}\n")
    repo.commit("base")
    branch(repo)
    repo.write("app.py", f"def f(y):\n    return {EVAL}  # ok\n")
    repo.write("c.py", f"def g(y):\n    return {EVAL}\n")
    repo.commit("work")
    assert repo.run("baseline").returncode == 0
    repo.commit("accept them")


def test_entries_added_in_a_range_do_not_hide_that_ranges_findings(repo):
    debt_and_self_baseline(repo)
    res = span(repo, "main..topic")
    assert evals(res) == [("app.py", 2), ("c.py", 2)] and res["code"] == 1
    n = entries_at(repo, "topic")
    warn = find(res, "gate.baseline_changed")
    assert len(warn) == 1 and warn[0]["action"] == "warn" and warn[0]["path"] == BASELINE
    assert warn[0]["message"] == f"this change adds {n} baseline entries; they do not apply to this change"
    assert not hidden(res)


def test_one_added_entry_is_singular(repo):
    repo.write("app.py", f"def f(y):\n    return {EVAL}\n")
    repo.commit("base")
    branch(repo)
    repo.write("app.py", f"def f(y):\n    return {EVAL}  # ok\n")
    repo.commit("work")
    assert repo.run("baseline").returncode == 0
    repo.commit("accept it")
    msg = find(span(repo, "main..topic"), "gate.baseline_changed")[0]["message"]
    assert msg == "this change adds 1 baseline entry; it does not apply to this change"


def test_the_new_rule_is_a_warning_nobody_can_baseline_or_raise_to_a_block_by_accident(repo):
    rule = aigate.RULES["gate.baseline_changed"]
    assert rule.action == "warn" and "gate." in aigate.BASELINE_NEVER
    debt_and_self_baseline(repo)
    again = span(repo, "main..topic")
    assert [f["action"] for f in find(again, "gate.baseline_changed")] == ["warn"]


def test_the_ci_config_option_does_not_move_the_baseline_to_the_pull_request_for_a_person(repo, tmp_path):
    debt_and_self_baseline(repo)
    ci = tmp_path / "ci.toml"
    ci.write_text("version = 1\n", encoding="utf-8", newline="\n")
    res = span(repo, "main..topic", "--config", str(ci))
    assert evals(res) == [("app.py", 2), ("c.py", 2)] and find(res, "gate.baseline_changed")


def test_the_ci_config_option_does_not_soften_an_ai_agent_either(repo, tmp_path):
    debt_and_self_baseline(repo)
    ci = tmp_path / "ci.toml"
    ci.write_text("version = 1\n", encoding="utf-8", newline="\n")
    res = span(repo, "main..topic", "--config", str(ci), env={"CLAUDECODE": "1"})
    assert evals(res) == [("app.py", 2), ("c.py", 2)] and not hidden(res)
    assert find(res, "scope.forbidden_path")                            # the baseline file stays off limits for an agent


# ------------------------------------------------------------------ 4. an AI agent: byte for byte what 0.6.1 printed

def normal(text, version):
    return re.sub(r"\d+\.\d+s\b", "<t>", text.replace(version, "<v>"))


def agent_output(repo, script, fmt, scenario):
    env = {"CLAUDECODE": "1"}
    if scenario == "push":
        proc = gate(repo, "check", "--push", "--offline", "--format", fmt, "--color", "never", script=script, env=env,
                    input=push_line(repo))
    else:
        proc = gate(repo, "check", "--range", "main..topic", "--offline", "--format", fmt, "--color", "never", script=script,
                    env=env)
    text = proc.stdout
    if fmt == "json":
        doc = json.loads(text)
        doc["stats"].pop("seconds")
        text = json.dumps(doc, indent=1, sort_keys=True)
    return proc.returncode, normal(text, aigate.__version__)


def scenario_repo(repo, scenario):
    if scenario == "push":
        adopt(repo)
    elif scenario == "range":
        adopt(repo)
        branch(repo)
        repo.write("a.py", f"def f(y):\n    return {EVAL}  # reviewed\n\n\ndef g(y):\n    return {EVAL}\n")
        repo.write("d.py", f"def h(y):\n    return {EVAL}\n")
        repo.commit("work")
    else:
        debt_and_self_baseline(repo)


@pytest.mark.parametrize("scenario", ["push", "range", "self_baseline"])
@pytest.mark.parametrize("fmt", ["text", "json", "agent"])
def test_an_ai_agent_gets_exactly_the_0_6_1_output(repo, tmp_path, scenario, fmt):
    scenario_repo(repo, scenario)
    old = agent_output(repo, old_script(tmp_path), fmt, "push" if scenario == "push" else "range")
    new = agent_output(repo, SCRIPT, fmt, "push" if scenario == "push" else "range")
    assert old[1] and new == old


# ------------------------------------------------------------------ 5. apply_on_push = false: 0.6.1 for a person

def findings_of(res):
    return sorted((f["rule"], f["path"], f["line"], f["action"], f["allowed"]) for f in res["findings"]
                  if f["rule"] != "gate.config_invalid")                 # 0.6.1 does not know the [baseline] table


OFF = "version = 1\n\n[baseline]\napply_on_push = false\n"


def test_apply_on_push_false_gives_a_person_the_0_6_1_answer_on_a_first_push(repo, tmp_path):
    repo.write(".aigate/lite.toml", OFF)
    adopt(repo, "b.py")
    new, old = push(repo), push(repo, script=old_script(tmp_path))
    assert new["risk"] < 40 and old["risk"] < 40
    assert findings_of(new) == findings_of(old) and new["code"] == old["code"] == 1
    assert evals(new) and not any("accepted by" in n for n in new["notes"])


def test_apply_on_push_false_gives_a_person_the_0_6_1_answer_for_a_range_that_baselines_itself(repo, tmp_path):
    repo.write(".aigate/lite.toml", OFF)
    debt_and_self_baseline(repo)
    new, old = span(repo, "main..topic"), span(repo, "main..topic", script=old_script(tmp_path))
    assert findings_of(new) == findings_of(old) and new["code"] == old["code"]
    assert not find(new, "gate.baseline_changed")


def hole_with_configs(repo, base_cfg=None, tip_cfg=None):
    """The base holds one unbaselined eval; the change touches that line and baselines it. The config text at the base
    and (optionally) the one the change itself brings."""
    if base_cfg:
        repo.write(".aigate/lite.toml", base_cfg)
    repo.write("app.py", f"def f(y):\n    return {EVAL}\n")
    repo.commit("base")
    branch(repo)
    if tip_cfg:
        repo.write(".aigate/lite.toml", tip_cfg)
    repo.write("app.py", f"def f(y):\n    return {EVAL}  # ok\n")
    repo.commit("work")
    assert repo.run("baseline").returncode == 0
    repo.commit("accept it")


def apply_cfg(text):
    return f"version = 1\n\n[baseline]\napply_on_push = {text}\n"


@pytest.mark.parametrize("tip_says", ["false", '"yes"'])
def test_a_change_cannot_switch_the_option_off_for_itself(repo, tip_says):
    hole_with_configs(repo, tip_cfg=apply_cfg(tip_says))                  # the change's own config says false, or nonsense
    res = span(repo, "main..topic")
    assert evals(res) == [("app.py", 2)] and res["code"] == 1 and not hidden(res)
    assert find(res, "gate.baseline_changed")
    pushed = push(repo, lines=push_line(repo, "topic", remote=sha(repo, "main")))     # the same for a push to a remote that has main
    assert evals(pushed) == [("app.py", 2)] and not hidden(pushed)


def test_the_option_is_read_from_the_base_and_false_there_gives_the_0_6_1_answer(repo, tmp_path):
    hole_with_configs(repo, base_cfg=apply_cfg("false"), tip_cfg=apply_cfg("true"))
    new, old = span(repo, "main..topic"), span(repo, "main..topic", script=old_script(tmp_path))
    assert findings_of(new) == findings_of(old) and not find(new, "gate.baseline_changed")


def test_an_invalid_value_at_the_base_counts_as_true_and_is_reported(repo):
    hole_with_configs(repo, base_cfg=apply_cfg('"yes"'))
    res = span(repo, "main..topic")
    assert evals(res) == [("app.py", 2)] and find(res, "gate.baseline_changed") and not hidden(res)
    assert any("baseline.apply_on_push must be true or false" in m["message"] for m in find(res, "gate.config_invalid"))
    assert any("baseline.apply_on_push must be true or false" in n for n in res["notes"])           # said for the base too


def test_apply_on_push_must_be_true_or_false(repo):
    repo.write(".aigate/lite.toml", 'version = 1\n\n[baseline]\napply_on_push = "yes"\n')
    adopt(repo, "b.py")
    res = push(repo)
    msgs = [f["message"] for f in find(res, "gate.config_invalid")]
    assert any("baseline.apply_on_push must be true or false" in m for m in msgs)
    assert not evals(res) and res["code"] == 0                           # not false: false would restore the softer 0.6.1 answer


# ------------------------------------------------------------------ 6. secrets are never accepted from a baseline

def test_a_secret_in_history_or_at_the_tip_still_blocks_a_first_push(repo):
    repo.write("s.py", f'KEY = "{AWS}"\n')
    repo.commit("add a key")
    repo.git("rm", "-q", "s.py")
    repo.commit("remove it again")                                      # gone from the tip, still in history
    repo.write("t.py", f'TOKEN = "{AWS}"\n')
    repo.commit("another key at the tip")
    first = push(repo)
    tip = [f for f in first["findings"] if f["rule"].startswith("secrets.") and f["path"] == "t.py"]
    assert tip and first["code"] == 1
    rows = [{"rule": f["rule"], "path": f["path"], "fingerprint": f["fingerprint"]}
            for f in first["findings"] if f["rule"].startswith("secrets.")]
    repo.write(BASELINE, json.dumps({"version": 1, "tool": "x", "entries": rows}))      # written by hand, secrets listed
    repo.commit("a baseline that lists the secrets")
    res = push(repo)
    secrets = [f for f in res["findings"] if f["rule"].startswith("secrets.")]
    assert res["verdict"] == "block" and res["code"] == 1
    assert any(f["path"] == "t.py" and not f["allowed"] for f in secrets)
    assert any("commit" in f["message"] and not f["allowed"] for f in secrets), secrets      # the one in history
    assert not any(f["allowed"] == "baseline" for f in secrets)


# ------------------------------------------------------------------ 7. a copy of a baselined pattern is a new finding

def test_a_copy_of_a_baselined_pattern_is_reported_on_a_first_push(repo):
    adopt(repo)
    repo.write("e.py", f"def copy(y):\n    return {EVAL}\n")              # another file
    repo.write("b.py", f"def f(y):\n    return {EVAL}\n\n\ndef again(y):\n    return {EVAL}\n")   # a twin in a baselined file
    repo.commit("copies")
    res = push(repo)
    assert evals(res) == [("b.py", 6), ("e.py", 2)] and res["code"] == 1
    assert {f["path"] for f in hidden(res)} == {"a.py", "b.py", "c.py"}


def test_a_copy_of_a_baselined_pattern_is_reported_in_a_range(repo):
    adopt(repo)
    branch(repo)
    repo.write("e.py", f"def copy(y):\n    return {EVAL}\n")
    repo.write("c.py", f"def f(y):\n    return {EVAL}\n\n\ndef again(y):\n    return {EVAL}\n")
    repo.commit("copies")
    res = span(repo, "main..topic")
    assert evals(res) == [("c.py", 6), ("e.py", 2)] and res["code"] == 1


# ------------------------------------------------------------------ several refs, and a base the remote never had

def test_with_several_refs_no_ref_gets_a_baseline_it_did_not_earn(repo):
    adopt(repo)                                                          # main: a first push, with a committed baseline
    repo.git("checkout", "-q", "--orphan", "other")
    repo.git("rm", "-rfq", ".")
    repo.write("README.md", "# other\n")
    repo.commit("other root")
    remote_has = sha(repo)
    repo.git("update-ref", "refs/remotes/origin/other", remote_has)
    repo.write("x.py", f"def x(y):\n    return {EVAL}\n")
    repo.commit("a new finding")
    lines = push_line(repo, "main") + push_line(repo, "other", remote=remote_has)
    res = push(repo, lines=lines)
    assert ("x.py", 2) in evals(res) and res["code"] == 1
    assert not hidden(res)                                               # neither ref's findings are hidden
    assert not any("accepted by" in n for n in res["notes"])


def unpushed_merge(repo):
    """Two commits the remote has; a local merge of them that carries a baseline; a change on top of the merge."""
    repo.write("a.py", f"def f(y):\n    return {EVAL}\n")
    repo.commit("debt")
    root = sha(repo)
    branch(repo, "left")
    repo.write("left.txt", "left\n")
    repo.commit("left")
    left = sha(repo)
    repo.git("checkout", "-q", "-b", "right", root)
    repo.write("right.txt", "right\n")
    repo.commit("right")
    right = sha(repo)
    repo.git("update-ref", "refs/remotes/origin/left", left)
    repo.git("update-ref", "refs/remotes/origin/right", right)
    repo.git("checkout", "-q", "-b", "topic", left)
    repo.git("merge", "--no-commit", "--no-ff", "right")
    assert repo.run("baseline").returncode == 0                         # the merge carries a baseline of the debt
    repo.commit("merge")
    repo.write("a.py", f"def f(y):\n    return {EVAL}  # ok\n")
    repo.commit("touch the debt")


def test_a_merge_nobody_pushed_cannot_lend_its_baseline_to_the_push(repo):
    unpushed_merge(repo)
    res = push(repo, lines=push_line(repo, "topic"))
    assert evals(res) == [("a.py", 2)] and res["code"] == 1 and not hidden(res)


def test_the_note_is_not_printed_when_nothing_was_accepted(repo):
    repo.write("a.py", "x = 1\n")
    repo.commit("clean code")
    assert not any("accepted by" in n for n in push(repo)["notes"])


# ------------------------------------------------------------------ a remote that holds part of the history is not an empty remote

def bare_remote(repo, name="origin"):
    path = repo.path.parent / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True, env=repo.env, capture_output=True)
    repo.git("remote", "add", name, str(path))


def dated(day):
    stamp = f"2026-01-{day:02d}T00:00:00"
    return {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}


def remote_holds_main(repo):
    repo.write("app.py", f"def f(y):\n    return {EVAL}\n")
    repo.commit("remote main", env=dated(3))
    bare_remote(repo)
    repo.git("push", "-q", "--no-verify", "origin", "main")


def unrelated_root(repo, name, path, day):
    """A commit with no parent, on its own branch (what a vendored project or an imported repository looks like)."""
    repo.git("checkout", "-q", "--orphan", name)
    repo.git("rm", "-rfq", ".")
    repo.write(path, "vendored\n")
    repo.commit(f"root of {name}", env=dated(day))


def unrelated_root_merged_before_the_change(repo):
    remote_holds_main(repo)
    unrelated_root(repo, "x", "x.txt", 1)
    repo.git("checkout", "-q", "-b", "topic", "main")
    repo.git("merge", "-q", "--no-ff", "--allow-unrelated-histories", "-m", "merge x", "x", env=dated(4))
    repo.write("app.py", f"def f(y):\n    return {EVAL}  # ok\n")
    repo.write("new.py", f"def n(y):\n    return {EVAL}\n")
    repo.commit("new debt", env=dated(5))
    assert repo.run("baseline").returncode == 0
    repo.commit("accept our own debt", env=dated(6))
    return [(("check", "--push", "--remote", "origin"), push_line(repo, "topic"))]


def unrelated_root_merged_after_the_change(repo):
    remote_holds_main(repo)
    unrelated_root(repo, "x", "x.txt", 1)
    repo.git("checkout", "-q", "-b", "topic", "main")
    repo.write("new.py", f"def n(y):\n    return {EVAL}\n")
    repo.commit("new debt", env=dated(5))
    assert repo.run("baseline").returncode == 0
    repo.commit("accept our own debt", env=dated(6))
    repo.git("merge", "-q", "--no-ff", "--allow-unrelated-histories", "-m", "merge x", "x", env=dated(7))
    return [(("check", "--push", "--remote", "origin"), push_line(repo, "topic"))]


def subtree_like_merge(repo):
    remote_holds_main(repo)
    unrelated_root(repo, "vendor", "lib/x.txt", 1)
    repo.git("checkout", "-q", "-b", "topic", "main")
    repo.git("merge", "-q", "--allow-unrelated-histories", "-m", "add vendored lib", "vendor", env=dated(4))
    repo.write("new.py", f"def n(y):\n    return {EVAL}\n")
    repo.commit("new debt", env=dated(5))
    assert repo.run("baseline").returncode == 0
    repo.commit("accept", env=dated(6))
    return [(("check", "--push", "--remote", "origin"), push_line(repo, "topic"))]


@pytest.mark.parametrize("build", [unrelated_root_merged_before_the_change, unrelated_root_merged_after_the_change,
                                   subtree_like_merge])
def test_a_branch_that_joins_an_unrelated_root_to_known_history_gets_no_tip_baseline(repo, build):
    args, stdin = build(repo)[0]
    proc = gate(repo, *args, "--offline", "--format", "json", input=stdin)
    res = json.loads(proc.stdout)
    assert proc.returncode == 1 and res["verdict"] in ("fix", "block")
    assert ("new.py", 2) in evals(res) and not hidden(res)
    assert not any("accepted by" in n for n in res["notes"])


def test_a_remote_that_holds_none_of_the_history_but_has_other_refs_still_counts_as_empty(repo):
    # a tracking ref that the pushed commit does not contain says nothing about this history
    adopt(repo)
    tree = repo.git("rev-parse", "HEAD^{tree}").stdout.strip()
    other = repo.git("commit-tree", tree, "-m", "unrelated").stdout.strip()                 # a root commit of its own
    repo.git("update-ref", "refs/remotes/origin/elsewhere", other)
    res = push(repo)
    assert res["code"] == 0 and any("accepted by" in n for n in res["notes"])


# ------------------------------------------------------------------ several refs: the order cannot matter

def two_refs(repo, n2_last):
    """main is a first push with a committed baseline (no remote has it); feat edits a baselined line on top of main,
    and the remote has main."""
    adopt(repo)
    main_tip = sha(repo)
    branch(repo, "feat")
    repo.write("a.py", f"def f(y):\n    return {EVAL}  # reviewed\n\n\ndef g(y):\n    return {EVAL}\n")
    repo.commit("edit a baselined line")
    feat = push_line(repo, "feat", remote=main_tip)
    first = push_line(repo, "main")
    return feat + first if n2_last else first + feat


@pytest.mark.parametrize("n2_last", [True, False])
def test_with_two_refs_nothing_is_hidden_whatever_the_order(repo, n2_last):
    lines = two_refs(repo, n2_last)
    res = push(repo, lines=lines)
    assert res["code"] == 1 and not hidden(res)
    assert evals(res) == [("a.py", 2), ("a.py", 6), ("b.py", 2), ("c.py", 2)]
    assert not any("accepted by" in n for n in res["notes"])


# ------------------------------------------------------------------ an AI agent is never softer than a person

RANK = {"pass": 1, "warn": 2, "fix": 3, "block": 4}


def levels(res):
    out = {}
    for f in res["findings"]:
        if not f["allowed"] and f["action"] != "pass":
            key = (f["rule"], f["path"], f["line"])
            out[key] = max(out.get(key, 0), RANK[f["action"]])
    return out


def softer(person, agent):
    """What the evaluation calls agent-softer: a person's finding the agent does not match or exceed (a stricter finding on
    the same path counts), and a milder verdict."""
    p, a = levels(person), levels(agent)
    worst = {}
    for (_rule, path, _line), rank in a.items():
        worst[path] = max(worst.get(path, 0), rank)
    lost = [k for k, rank in p.items() if a.get(k, 0) < rank and worst.get(k[1], 0) < rank]
    if RANK[agent["verdict"]] < RANK[person["verdict"]]:
        lost.append("verdict")
    return lost


def copies_on_a_first_push(repo):
    adopt(repo)
    repo.write("e.py", f"def copy(y):\n    return {EVAL}\n")
    repo.write("b.py", f"def f(y):\n    return {EVAL}\n\n\ndef again(y):\n    return {EVAL}\n")
    repo.commit("copies")
    return [(("check", "--push"), push_line(repo))]


def a_secret_on_a_first_push(repo):
    repo.write("t.py", f'TOKEN = "{AWS}"\n')
    repo.commit("a key")
    return [(("check", "--push"), push_line(repo))]


def first_push(repo):
    adopt(repo)
    return [(("check", "--push"), push_line(repo))]


def range_with_a_baselined_edit(repo):
    scenario_repo(repo, "range")
    return [(("check", "--range", "main..topic"), None)]


def range_that_baselines_itself(repo):
    debt_and_self_baseline(repo)
    return [(("check", "--range", "main..topic"), None)]


def push_that_grows_from_known_history(repo):
    adopt(repo)
    repo.git("update-ref", "refs/remotes/origin/main", "HEAD")
    branch(repo)
    repo.write("a.py", f"def f(y):\n    return {EVAL}  # reviewed\n\n\ndef g(y):\n    return {EVAL}\n")
    repo.write("d.py", f"def h(y):\n    return {EVAL}\n")
    repo.commit("work")
    return [(("check", "--push"), push_line(repo, "topic"))]


def push_from_an_unpushed_merge(repo):
    unpushed_merge(repo)
    return [(("check", "--push"), push_line(repo, "topic"))]


def two_refs_n2_last(repo):
    return [(("check", "--push"), two_refs(repo, True))]


def two_refs_n2_first(repo):
    return [(("check", "--push"), two_refs(repo, False))]


# ------------------------------------------------------------------ an agent reads what a person reads; no source means no key

def range_that_removes_an_entry(repo):
    """The change takes b.py's entry out of the baseline and edits b.py's debt line: a person sees that finding again."""
    adopt(repo)
    branch(repo)
    doc = json.loads((repo.path / BASELINE).read_text(encoding="utf-8"))
    doc["entries"] = [e for e in doc["entries"] if e["path"] != "b.py"]
    repo.write(BASELINE, json.dumps(doc, indent=2) + "\n")
    repo.write("b.py", f"def f(y):\n    return {EVAL}  # reviewed\n")
    repo.commit("drop one entry and touch its line")
    return [(("check", "--range", "main..topic"), None)]


def test_a_change_that_removes_a_baseline_entry_cannot_make_an_agent_hide_what_a_person_sees(repo):
    invocations = range_that_removes_an_entry(repo)
    person, = run_all(repo, invocations, "person")
    agent, = run_all(repo, invocations, "agent")
    assert ("b.py", 2) in evals(person)                                 # the entry is gone at the tip: the finding is back
    assert ("b.py", 2) in evals(agent) and softer(person, agent) == []
    assert {f["path"] for f in hidden(person)} == set()                 # nothing else of b.py was hidden either




def unrelated_root_on_an_older_commit_of_a_moved_branch(repo):
    """The remote's main moved on after the pushed branch started; the branch then joins an unrelated history."""
    remote_holds_main(repo)                                              # app.py with debt, pushed
    repo.write("notes.txt", "main moves on\n")
    repo.commit("main moves on", env=dated(4))
    repo.git("push", "-q", "--no-verify", "origin", "main")
    older = repo.git("rev-parse", "HEAD~1").stdout.strip()
    unrelated_root(repo, "vendor", "lib/x.txt", 1)
    repo.git("checkout", "-q", "-b", "topic", older)
    repo.git("merge", "-q", "--allow-unrelated-histories", "-m", "add vendored lib", "vendor", env=dated(5))
    repo.write("new.py", f"def n(y):\n    return {EVAL}\n")
    repo.commit("new debt", env=dated(6))
    assert repo.run("baseline").returncode == 0
    repo.commit("accept our own debt", env=dated(7))
    return [(("check", "--push", "--remote", "origin"), push_line(repo, "topic"))]


def force_push_over_work_this_clone_never_fetched(repo):
    """The remote has a branch tip this clone does not have (it was pushed to by URL, with no tracking refs)."""
    repo.write("app.py", f"def f(y):\n    return {EVAL}\n")
    repo.commit("shared history")
    url = repo.path.parent / "shared.git"
    subprocess.run(["git", "init", "-q", "-b", "main", "--bare", str(url)], check=True, env=repo.env, capture_output=True)
    repo.git("push", "-q", "--no-verify", str(url), "main")
    other = repo.path.parent / "other-clone"
    subprocess.run(["git", "clone", "-q", str(url), str(other)], check=True, env=repo.env, capture_output=True)
    for args in (("config", "user.email", "o@example.com"), ("config", "user.name", "Other"), ("config", "commit.gpgsign", "false")):
        subprocess.run(["git", *args], cwd=other, check=True, env=repo.env, capture_output=True)
    (other / "o.txt").write_text("other work\n", encoding="utf-8", newline="\n")
    for args in (("add", "-A"), ("commit", "-q", "--no-verify", "-m", "other work"), ("push", "-q", "--no-verify", "origin", "main")):
        subprocess.run(["git", *args], cwd=other, check=True, env=repo.env, capture_output=True)
    theirs = subprocess.run(["git", "rev-parse", "HEAD"], cwd=other, check=True, env=repo.env, capture_output=True,
                            text=True).stdout.strip()
    repo.write("new.py", f"def n(y):\n    return {EVAL}\n")
    repo.commit("new debt")
    assert repo.run("baseline").returncode == 0
    repo.commit("accept our own debt")
    return [(("check", "--push"), push_line(repo, remote=theirs))]


def two_refs_one_with_an_unknown_remote_sha(repo, unknown_first):
    """main edits a baselined line on top of a base the remote has; left has a remote sha this clone never fetched."""
    adopt(repo, "b.py")
    repo.git("branch", "left")
    url = repo.path.parent / "shared.git"
    subprocess.run(["git", "init", "-q", "-b", "main", "--bare", str(url)], check=True, env=repo.env, capture_output=True)
    repo.git("remote", "add", "origin", str(url))
    repo.git("push", "-q", "--no-verify", "origin", "main", "left")
    repo.git("fetch", "-q", "origin")
    other = repo.path.parent / "other-clone"
    subprocess.run(["git", "clone", "-q", str(url), str(other)], check=True, env=repo.env, capture_output=True)
    for args in (("config", "user.email", "o@example.com"), ("config", "user.name", "Other"), ("config", "commit.gpgsign", "false"),
                 ("checkout", "-q", "left")):
        subprocess.run(["git", *args], cwd=other, check=True, env=repo.env, capture_output=True)
    (other / "o.txt").write_text("other work\n", encoding="utf-8", newline="\n")
    for args in (("add", "-A"), ("commit", "-q", "--no-verify", "-m", "other work"), ("push", "-q", "--no-verify", "origin", "left")):
        subprocess.run(["git", *args], cwd=other, check=True, env=repo.env, capture_output=True)
    theirs = subprocess.run(["git", "rev-parse", "left"], cwd=other, check=True, env=repo.env, capture_output=True,
                            text=True).stdout.strip()
    remote_main = sha(repo, "origin/main")
    repo.write("b.py", f"def f(y):\n    return {EVAL}  # reworded\n")
    repo.commit("touch the baselined line")
    repo.git("checkout", "-q", "left")
    repo.write("l.txt", "left\n")
    repo.commit("left work")
    repo.git("checkout", "-q", "main")
    left = push_line(repo, "left", remote=theirs)
    main = push_line(repo, "main", remote=remote_main)
    return [(("check", "--push", "--remote", "origin"), left + main if unknown_first else main + left)]


def two_refs_unknown_sha_first(repo):
    return two_refs_one_with_an_unknown_remote_sha(repo, True)


def two_refs_unknown_sha_last(repo):
    return two_refs_one_with_an_unknown_remote_sha(repo, False)


MATRIX = [first_push, copies_on_a_first_push, a_secret_on_a_first_push, range_with_a_baselined_edit,
          range_that_baselines_itself, push_that_grows_from_known_history, push_from_an_unpushed_merge,
          unrelated_root_merged_before_the_change, unrelated_root_merged_after_the_change, subtree_like_merge,
          two_refs_n2_last, two_refs_n2_first, range_that_removes_an_entry,
          unrelated_root_on_an_older_commit_of_a_moved_branch, force_push_over_work_this_clone_never_fetched,
          two_refs_unknown_sha_first, two_refs_unknown_sha_last]


def run_all(repo, invocations, who, script=SCRIPT):
    results = []
    for args, stdin in invocations:
        proc = gate(repo, *args, "--offline", "--format", "json", script=script, input=stdin,
                    env={"CLAUDECODE": "1"} if who == "agent" else None)
        assert proc.returncode in (0, 1), proc.stderr + proc.stdout
        results.append({**json.loads(proc.stdout), "code": proc.returncode})
    return results


@pytest.mark.parametrize("build", MATRIX, ids=lambda fn: fn.__name__)
def test_in_every_scenario_the_agent_finds_at_least_what_the_person_finds(repo, build):
    invocations = build(repo)
    for person, agent in zip(run_all(repo, invocations, "person"), run_all(repo, invocations, "agent")):
        assert softer(person, agent) == [], (build.__name__, softer(person, agent))


def test_without_a_baseline_source_an_agent_gets_no_baseline_either_and_is_stricter_than_0_6_1_only(repo, tmp_path):
    invocations = push_from_an_unpushed_merge(repo)
    person, = run_all(repo, invocations, "person")
    agent, = run_all(repo, invocations, "agent")
    before, = run_all(repo, invocations, "agent", script=old_script(tmp_path))
    assert softer(person, agent) == [] and ("a.py", 2) in evals(agent) and not hidden(agent)
    assert not evals(before)                                           # 0.6.1: the merge's baseline hid the debt from an agent
    assert set(levels(before)) <= set(levels(agent)) and all(levels(agent)[k] >= v for k, v in levels(before).items())
    assert agent["code"] >= before["code"]


# ------------------------------------------------------------------ --config: the file decides the config, for an agent too

def test_with_config_an_agent_is_judged_by_that_file_not_by_the_config_at_the_base(repo, tmp_path):
    repo.write(".aigate/lite.toml", 'version = 1\n\n[rules]\n"security.eval" = "block"\n')          # the base's own setting
    debt_and_self_baseline(repo)
    ci = tmp_path / "ci.toml"
    ci.write_text('version = 1\n\n[rules]\n"security.eval" = "warn"\n', encoding="utf-8", newline="\n")
    for who, env in (("person", None), ("agent", {"CLAUDECODE": "1"})):
        res = span(repo, "main..topic", "--config", str(ci), env=env)
        mine = [f["action"] for f in active(res, "security.eval") if f["path"] == "app.py"]
        assert mine == ["warn"], (who, mine)                            # the file's level, not the base's "block"
        plain = span(repo, "main..topic", env=env)
        assert [f["action"] for f in active(plain, "security.eval") if f["path"] == "app.py"] == ["block"], who


def test_an_agent_is_never_taken_down_the_new_path(repo):
    # the new path is a person's: no warning about added entries, no note, and the base's baseline as 0.6.1 read it
    debt_and_self_baseline(repo)
    res = span(repo, "main..topic", env={"CLAUDECODE": "1"})
    assert not find(res, "gate.baseline_changed") and not any("accepted by" in n for n in res["notes"])
    assert evals(res) == [("app.py", 2), ("c.py", 2)]


def with_the_option_off_and_the_debt_baselined_again(repo, build):
    """After the scenario, the tip's own config says apply_on_push = false and the debt is baselined once more."""
    build(repo)
    repo.write(".aigate/lite.toml", apply_cfg("false"))
    assert repo.run("baseline").returncode == 0
    repo.commit("switch it off and accept the debt again")
    return push_line(repo, "topic")


@pytest.mark.parametrize("build,remote", [(unpushed_merge, False), (unrelated_root_merged_before_the_change, True),
                                          (unrelated_root_merged_after_the_change, True), (subtree_like_merge, True)])
def test_with_no_baseline_source_a_change_cannot_bring_the_older_rule_back_by_switching_the_option_off(repo, build, remote):
    line = with_the_option_off_and_the_debt_baselined_again(repo, build)
    extra = ("--remote", "origin") if remote else ()
    res = push(repo, *extra, lines=line)
    assert evals(res) and res["code"] == 1 and not hidden(res)
    assert not any("accepted by" in n for n in res["notes"])


# ------------------------------------------------------------------ the remote has more than this clone knows

def test_a_branch_started_from_an_older_commit_of_a_branch_that_moved_on_gets_no_tip_baseline(repo, tmp_path):
    invocations = unrelated_root_on_an_older_commit_of_a_moved_branch(repo)
    person, = run_all(repo, invocations, "person")
    assert person["code"] == 1 and {("app.py", 2), ("new.py", 2)} <= set(evals(person)) and not hidden(person)
    assert not any("accepted by" in n for n in person["notes"])
    agent, = run_all(repo, invocations, "agent")
    before, = run_all(repo, invocations, "agent", script=old_script(tmp_path))
    assert levels(agent) == levels(before) and agent["code"] == before["code"]          # an agent's result did not change


def test_a_remote_sha_this_clone_does_not_know_means_the_remote_is_not_empty(repo, tmp_path):
    invocations = force_push_over_work_this_clone_never_fetched(repo)
    person, = run_all(repo, invocations, "person")
    assert person["code"] == 1 and {("app.py", 2), ("new.py", 2)} <= set(evals(person)) and not hidden(person)
    assert not any("accepted by" in n for n in person["notes"])
    before, = run_all(repo, invocations, "person", script=old_script(tmp_path))
    assert before["code"] == 1                                                                  # 0.6.1 stopped it too


def test_a_remote_with_only_unrelated_history_still_counts_as_empty(repo):
    adopt(repo)
    bare_remote(repo)
    repo.git("checkout", "-q", "--orphan", "elsewhere")
    repo.git("rm", "-rfq", ".")
    repo.write("other.txt", "someone else's project\n")
    repo.commit("an unrelated project")
    repo.git("push", "-q", "--no-verify", "origin", "elsewhere")            # a tracking ref that shares no commit with main
    repo.git("checkout", "-q", "main")
    res = push(repo, "--remote", "origin", lines=push_line(repo, "main"))
    assert res["code"] == 0 and not evals(res)
    assert any("accepted by" in n for n in res["notes"])


@pytest.mark.parametrize("build", [two_refs_unknown_sha_first, two_refs_unknown_sha_last], ids=lambda fn: fn.__name__)
def test_with_several_refs_an_agent_is_judged_by_the_same_baseline_as_a_person_in_either_order(repo, build):
    invocations = build(repo)
    person, = run_all(repo, invocations, "person")
    agent, = run_all(repo, invocations, "agent")
    assert person["code"] == 1 and ("b.py", 2) in evals(person) and not hidden(person)
    assert agent["code"] == 1 and ("b.py", 2) in evals(agent) and not hidden(agent)
    assert softer(person, agent) == []
