"""Part A of the command-level defects: `fix` says why it changed nothing, `fix --all`, one unsafe line does not skip the file,
and the `next:` line names `aigate-lite fix` only when it can change something. Every test runs the CLI in a temporary repository
and reads stdout, stderr and the exit code."""
import hashlib

from conftest import find

BP = "break" + "point()"                    # built at run time so this file is not itself a finding
SET_TRACE = "pdb." + "set_trace()"
EVAL = "ev" + "al(y)"


def staged(repo, files):
    for rel, text in files.items():
        p = repo.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode())
    repo.add()


def committed(repo, files):
    staged(repo, files)
    repo.commit("add files")


def state(repo):
    h = hashlib.sha256(repo.git("ls-files", "-s").stdout.encode())
    for p in sorted(repo.path.rglob("*")):
        if ".git" not in p.parts and p.is_file():
            h.update(str(p.relative_to(repo.path)).encode() + p.read_bytes())
    return h.hexdigest()


def lines(proc):
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


# ------------------------------------------------------------------------------------------ A1: say why nothing changed

def test_nothing_staged_and_a_clean_repository_says_nothing_staged(repo):
    proc = repo.run("fix")
    assert proc.returncode == 0 and "nothing staged" in proc.stdout
    assert "nothing to fix" not in proc.stdout.replace("nothing to fix automatically", "")


def test_nothing_staged_but_fix_findings_in_committed_files_names_them_and_all(repo):
    committed(repo, {"a.py": f"def f():\n    {BP}\n    return 1\n"})
    proc = repo.run("fix")
    assert proc.returncode == 0
    assert "1 finding is in committed files (fix works on staged changes; use --all)" in proc.stdout
    assert "nothing to fix\n" not in proc.stdout and not proc.stdout.startswith("nothing to fix")


def test_committed_findings_that_fix_cannot_change_are_listed_as_manual(repo):
    committed(repo, {"a.py": f"def f(y):\n    return {EVAL}\n", "b.py": f"def g(y):\n    return {EVAL}\n"})
    proc = repo.run("fix")
    assert proc.returncode == 0
    assert "2 FIX findings need a manual change: security.eval" in proc.stdout
    assert "committed files" in proc.stdout and "use --all" not in proc.stdout


def test_staged_findings_without_an_automatic_fix_are_listed_as_manual(repo):
    staged(repo, {"a.py": f"x = {EVAL}\n"})
    proc = repo.run("fix")
    assert proc.returncode == 0
    assert "nothing to fix automatically" in proc.stdout
    assert "1 FIX finding needs a manual change: security.eval" in proc.stdout


def test_a_clean_staged_change_says_the_staged_change_has_no_findings(repo):
    staged(repo, {"a.py": "x = 1\n"})
    proc = repo.run("fix")
    assert proc.returncode == 0 and "nothing to fix: the staged change has no FIX or BLOCK findings" in proc.stdout


def test_dry_run_lists_the_other_findings_before_the_next_line(repo):
    staged(repo, {"a.py": f"x = 1\n{BP}\n", "b.py": f"z = {EVAL}\n"})
    proc = repo.run("fix")
    assert proc.returncode == 1
    out = lines(proc)
    assert out[-1] == "next: aigate-lite fix --yes"
    assert any("1 FIX finding needs a manual change: security.eval" in ln for ln in out[:-1])


# ------------------------------------------------------------------------------------------------------- A2: fix --all

def test_fix_all_previews_committed_files_and_changes_nothing(repo):
    committed(repo, {"src/a.py": f"x = 1\n{BP}\ny = 2\n"})
    before = state(repo)
    proc = repo.run("fix", "--all")
    assert proc.returncode == 1
    assert f"-{BP}" in proc.stdout and "+++ b/src/a.py" in proc.stdout
    assert lines(proc)[-1] == "next: aigate-lite fix --all --yes"
    assert state(repo) == before


def test_fix_all_yes_edits_the_working_tree_and_stages_the_edit(repo):
    committed(repo, {"src/a.py": f"x = 1\n{BP}\ny = 2\n", "my dir/b.py": f"import pdb\n\n\ndef f():\n    {SET_TRACE}\n"})
    proc = repo.run("fix", "--all", "--yes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (repo.path / "src" / "a.py").read_text() == "x = 1\ny = 2\n"
    assert repo.git("show", ":src/a.py").stdout == "x = 1\ny = 2\n"                       # staged, not committed
    assert repo.git("log", "--oneline").stdout.count("\n") == 2                           # no commit was made
    assert "fixed:" in proc.stdout and "git commit" in proc.stdout


def test_fix_all_skips_a_file_changed_since_the_last_commit(repo):
    committed(repo, {"a.py": f"x = 1\n{BP}\n"})
    (repo.path / "a.py").write_text(f"x = 2\n{BP}\n")
    proc = repo.run("fix", "--all", "--yes")
    assert (repo.path / "a.py").read_text() == f"x = 2\n{BP}\n"
    assert "skipped a.py" in proc.stdout and "changes since the last commit" in proc.stdout


def test_fix_all_untracks_a_committed_env_file_after_the_dry_run_says_so(repo):
    committed(repo, {".env": "MODE=dev\nDB_PASSWORD=hunter2hunter2\n"})
    dry = repo.run("fix", "--all")
    assert dry.returncode == 1 and "git rm --cached -- .env" in dry.stdout and "already in HEAD" in dry.stdout
    assert repo.git("ls-files", ".env").stdout.strip() == ".env"
    done = repo.run("fix", "--all", "--yes")
    assert done.returncode == 0 and repo.git("ls-files", ".env").stdout.strip() == ""
    assert (repo.path / ".env").exists()


def test_fix_all_with_no_fixable_finding_says_so(repo):
    committed(repo, {"a.py": f"def f(y):\n    return {EVAL}\n"})
    proc = repo.run("fix", "--all")
    assert proc.returncode == 0 and "nothing to fix automatically" in proc.stdout
    assert "1 FIX finding needs a manual change: security.eval" in proc.stdout


# ----------------------------------------------------------------------- A3: one unsafe line does not skip the whole file

SOLE = f"def f(x):\n    {BP}\n    return 1\n\n\ndef g(x):\n    if x:\n        {BP}\n    return 2\n"


def test_the_safe_line_is_fixed_and_the_sole_statement_is_listed(repo):
    staged(repo, {"a.py": SOLE})
    proc = repo.run("fix", "--yes")
    assert (repo.path / "a.py").read_text() == f"def f(x):\n    return 1\n\n\ndef g(x):\n    if x:\n        {BP}\n    return 2\n"
    assert "skipped a.py:8" in proc.stdout
    assert "the only statement in its block" in proc.stdout and "--allow-pass" in proc.stdout
    assert "by hand" in proc.stdout


def test_allow_pass_replaces_a_sole_statement_with_pass(repo):
    staged(repo, {"a.py": SOLE})
    proc = repo.run("fix", "--yes", "--allow-pass")
    assert (repo.path / "a.py").read_text() == "def f(x):\n    return 1\n\n\ndef g(x):\n    if x:\n        pass\n    return 2\n"
    assert "skipped" not in proc.stdout


def test_a_block_of_only_debug_lines_keeps_one_pass_under_allow_pass(repo):
    text = f"if x:\n    {BP}\n    {SET_TRACE}\ny = 1\n"
    staged(repo, {"a.py": text})
    default = repo.run("fix", "--yes")
    assert (repo.path / "a.py").read_text() == text
    assert "skipped a.py:2" in default.stdout and "skipped a.py:3" in default.stdout
    repo.run("fix", "--yes", "--allow-pass")
    assert (repo.path / "a.py").read_text() == "if x:\n    pass\ny = 1\n"


def test_the_dry_run_shows_the_safe_edit_and_the_skipped_line(repo):
    staged(repo, {"a.py": SOLE})
    before = state(repo)
    proc = repo.run("fix")
    assert proc.returncode == 1 and "skipped a.py:8" in proc.stdout and "--- a/a.py" in proc.stdout
    assert state(repo) == before


def test_allow_pass_does_nothing_for_a_block_that_keeps_other_statements(repo):
    staged(repo, {"a.py": f"if x:\n    {BP}\n    y = 1\n"})
    repo.run("fix", "--yes", "--allow-pass")
    assert (repo.path / "a.py").read_text() == "if x:\n    y = 1\n"


# --------------------------------------------------------------------------------- A4: the `next:` line names fix honestly

def test_audit_names_fix_all_and_how_many_when_fix_can_change_something(repo):
    committed(repo, {"a.py": f"x = 1\n{BP}\n", "b.py": f"def g(y):\n    return {EVAL}\n"})
    proc = repo.run("audit", "--offline")
    assert proc.returncode == 1
    nxt = [ln for ln in proc.stdout.splitlines() if ln.startswith("next:")]
    assert any("aigate-lite fix --all" in ln and "1 of the 2" in ln for ln in nxt), nxt


def test_audit_does_not_name_fix_when_nothing_is_fixable_by_it(repo):
    committed(repo, {"b.py": f"def g(y):\n    return {EVAL}\n"})
    proc = repo.run("audit", "--offline")
    assert proc.returncode == 1 and "aigate-lite fix" not in proc.stdout


def test_a_staged_check_names_fix_and_how_many(repo):
    staged(repo, {"a.py": f"x = 1\n{BP}\n{SET_TRACE}\n", "b.py": f"z = {EVAL}\n"})
    proc = repo.run("check", "--staged", "--offline")
    assert proc.returncode == 1
    nxt = [ln for ln in proc.stdout.splitlines() if ln.startswith("next:") and "aigate-lite fix" in ln]
    assert len(nxt) == 1 and "aigate-lite fix can change 2 of the 3" in nxt[0] and "--all" not in nxt[0]


def test_the_next_line_does_not_name_fix_for_a_file_fix_would_refuse(repo):
    committed(repo, {"a.py": f"x = 1\n{BP}\n"})
    (repo.path / "a.py").write_text(f"x = 1\n{BP}\ny = 2\n")
    repo.git("add", "a.py")
    (repo.path / "a.py").write_text(f"x = 1\n{BP}\ny = 3\n")            # an unstaged edit on top: fix refuses this file
    proc = repo.run("check", "--staged", "--offline")
    assert proc.returncode == 1 and "aigate-lite fix" not in proc.stdout


def test_a_range_check_never_names_fix(repo):
    committed(repo, {"a.py": f"x = 1\n{BP}\n"})
    proc = repo.run("check", "--range", "HEAD~1..HEAD", "--offline")
    assert proc.returncode == 1 and "aigate-lite fix" not in proc.stdout


def test_json_output_is_unchanged_by_the_fix_hint(repo):
    staged(repo, {"a.py": f"x = 1\n{BP}\n"})
    res = repo.check("--offline")
    assert find(res, "code.debug_leftover") and "fixable" not in res
    assert set(res) >= {"verdict", "findings", "stats"}
