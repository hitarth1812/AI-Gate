import hashlib
import inspect
import os
import stat
import sys

import pytest

from conftest import SCRIPT, aigate, find

BP = "break" + "point()"                    # built at run time so this file is not itself a finding
SET_TRACE = "pdb." + "set_trace()"
DEBUGGER = "debug" + "ger;"
ONLY = "it." + "only("
posix = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes and symlinks")


def state(repo):
    """Hash of the index listing and every file in the work tree (to prove a dry run changes nothing)."""
    h = hashlib.sha256(repo.git("ls-files", "-s").stdout.encode())
    for p in sorted(repo.path.rglob("*")):
        if ".git" not in p.parts and p.is_file():
            h.update(str(p.relative_to(repo.path)).encode() + p.read_bytes())
    return h.hexdigest()


def staged(repo, files):
    for rel, text in files.items():
        p = repo.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode() if isinstance(text, str) else text)
    repo.add()


def fix(repo, *args):
    return repo.run("fix", *args)


def test_dry_run_shows_a_diff_and_changes_nothing(repo):
    staged(repo, {"a.py": f"x = 1\n{BP}\ny = 2\n"})
    before = state(repo)
    proc = fix(repo)
    assert proc.returncode == 1 and f"-{BP}" in proc.stdout and "+++ b/a.py" in proc.stdout
    assert proc.stdout.splitlines()[-1] == "next: aigate-lite fix --yes"
    assert state(repo) == before


def test_yes_applies_stages_and_rechecks(repo):
    staged(repo, {"a.py": f"x = 1\n{BP}\ny = 2\n"})
    proc = fix(repo, "--yes")
    assert (repo.path / "a.py").read_text() == "x = 1\ny = 2\n"
    assert repo.git("show", ":a.py").stdout == "x = 1\ny = 2\n"                 # staged too
    assert "PASS" in proc.stdout and proc.returncode == 0
    again = fix(repo)
    assert again.returncode == 0 and "nothing to fix" in again.stdout


def test_debugger_import_goes_when_it_becomes_unused(repo):
    staged(repo, {"a.py": f"import pdb\nimport os\n\n\ndef f():\n    {SET_TRACE}\n    return os.sep\n",
                  "b.py": f"import pdb; {SET_TRACE}\nz = 3\n"})
    fix(repo, "--yes")
    assert (repo.path / "a.py").read_text() == "import os\n\n\ndef f():\n    return os.sep\n"
    assert (repo.path / "b.py").read_text() == "z = 3\n"


def test_a_line_with_other_code_or_an_emptied_block_is_left_alone(repo):
    staged(repo, {"a.py": f"x = f(); {BP}\n", "b.py": f"if x:\n    {BP}\ny = 1\n"})
    proc = fix(repo, "--yes")
    assert (repo.path / "a.py").read_text() == f"x = f(); {BP}\n"
    assert (repo.path / "b.py").read_text() == f"if x:\n    {BP}\ny = 1\n"
    assert "by hand" in proc.stdout


def test_js_debugger_and_focused_tests(repo):
    staged(repo, {"web/a.js": f"function f() {{\n  {DEBUGGER}\n  return 1\n}}\n",
                  "web/a.test.js": f"describe('a', () => {{\n  {ONLY}'b', () => {{ expect(1).toBe(1) }})\n"
                                   f"  f" + "it('c', () => { expect(2).toBe(2) })\n})\n"
                                   "f" + "describe('d', () => { it('e', () => { expect(3).toBe(3) }) })\n"})
    fix(repo, "--yes")
    assert DEBUGGER not in (repo.path / "web/a.js").read_text()
    test = (repo.path / "web/a.test.js").read_text()
    assert "it('b'" in test and "  it('c'" in test and "\ndescribe('d'" in test and ".only" not in test


def test_crlf_encoding_and_missing_final_newline_are_preserved(repo):
    staged(repo, {"a.py": f"x = 'é'\r\n{BP}\r\ny = 2".encode("utf-8")})
    fix(repo, "--yes")
    assert (repo.path / "a.py").read_bytes() == "x = 'é'\r\ny = 2".encode("utf-8")


def test_partially_staged_file_is_refused(repo):
    staged(repo, {"a.py": f"x = 1\n{BP}\n"})
    (repo.path / "a.py").write_text(f"x = 1\n{BP}\nmore = 2\n")
    before = state(repo)
    proc = fix(repo, "--yes")
    assert "has unstaged changes" in proc.stdout and "stage or stash" in proc.stdout and state(repo) == before


def test_env_file_is_unstaged_and_ignored_once(repo):
    staged(repo, {".env": "DB_PASSWORD=" + "Zx9!kQ2#vB7$mN4p\n", "a.py": "x = 1\n"})
    dry = fix(repo)
    assert "git rm --cached -- .env" in dry.stdout and "+/.env" in dry.stdout
    fix(repo, "--yes")
    assert ".env" not in repo.git("ls-files").stdout.split() and (repo.path / ".env").exists()
    assert (repo.path / ".gitignore").read_text().splitlines().count("/.env") == 1
    assert ".gitignore" in repo.git("diff", "--cached", "--name-only").stdout.split()
    repo.git("add", "-f", ".env")
    fix(repo, "--yes")
    assert (repo.path / ".gitignore").read_text().splitlines().count("/.env") == 1


def test_only_limits_the_rules(repo):
    staged(repo, {"a.py": f"{BP}\nx = 1\n", "web/a.test.js": f"{ONLY}'b', () => {{ expect(1).toBe(1) }})\n"})
    fix(repo, "--yes", "--only", "tests.focused")
    assert BP in (repo.path / "a.py").read_text() and ".only" not in (repo.path / "web/a.test.js").read_text()


@posix
def test_file_mode_kept_and_symlinks_skipped(repo):
    staged(repo, {"run.py": f"{BP}\nx = 1\n"})
    os.chmod(repo.path / "run.py", 0o755)
    (repo.path / "link.py").symlink_to("run.py")
    repo.add()
    fix(repo, "--yes")
    assert stat.S_IMODE(os.stat(repo.path / "run.py").st_mode) == 0o755 and os.path.islink(repo.path / "link.py")


def test_fix_is_never_part_of_the_hook_path():
    for vendored in (True, False):
        for kind in ("pre-commit", "pre-push"):
            text = aigate.hook_script(kind, vendored, sys.executable, str(SCRIPT))
            runs = [ln for ln in text.splitlines() if '"$SCRIPT" ' in ln and not ln.lstrip().startswith("[")]
            assert runs and all(" check --" in ln and " fix" not in ln for ln in runs)
    check_code = inspect.getsource(aigate.run_check)
    assert "cmd_fix" not in check_code and "plan_fixes" not in check_code and "apply_fixes" not in check_code


def test_autocrlf_checkout_is_not_mistaken_for_unstaged_changes(repo):
    repo.git("config", "core.autocrlf", "true")
    staged(repo, {"a.py": f"x = 1\r\n{BP}\r\ny = 2\r\n".encode()})
    proc = fix(repo, "--yes")
    assert "has unstaged changes" not in proc.stdout and BP not in repo.git("show", ":a.py").stdout


def test_focused_fix_only_touches_the_statement_start(repo):
    lines = ["  " + ONLY + "'calls it(x)', () => { fitAddon.fit(); model.fit(d) })",
             "model.fit(d)", "  f" + "it('a', () => { expect(1).toBe(1) })"]
    staged(repo, {"web/a.test.js": "\n".join(lines) + "\n"})
    fix(repo, "--yes")
    out = (repo.path / "web/a.test.js").read_text().splitlines()
    assert out[0] == "  it('calls it(x)', () => { fitAddon.fit(); model.fit(d) })"
    assert out[1] == "model.fit(d)" and out[2] == "  it('a', () => { expect(1).toBe(1) })"


def test_glob_characters_in_env_paths(repo):
    secret = "DB_PASSWORD=" + "Zx9!kQ2#vB7$mN4p\n"
    staged(repo, {"cfg/x[ab].env": secret, "cfg/xa.env": "PLAIN=1\n"})
    fix(repo, "--yes", "--only", "secrets.env_file")
    tracked = repo.git("ls-files").stdout.split("\n")
    assert "cfg/xa.env" in tracked and "cfg/x[ab].env" not in tracked                   # only the finding unstaged
    assert repo.git("check-ignore", "-q", "--no-index", "cfg/x[ab].env", check=False).returncode == 0
    assert repo.git("check-ignore", "-q", "--no-index", "cfg/xa.env", check=False).returncode != 0


def test_unstaged_gitignore_is_not_swept_into_the_commit(repo):
    repo.write(".gitignore", "node_modules/\n")
    repo.commit("gi")
    repo.write(".gitignore", "node_modules/\nsecret-plans/\n")                         # the user's pending edit
    staged(repo, {".env": "DB_PASSWORD=" + "Zx9!kQ2#vB7$mN4p\n"})
    repo.git("reset", "-q", "--", ".gitignore")
    proc = fix(repo, "--yes")
    assert ".gitignore" in proc.stdout and "unstaged" in proc.stdout
    assert "secret-plans" not in repo.git("show", ":.gitignore").stdout


@posix
def test_symlinked_gitignore_is_refused(repo, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep\n")
    (repo.path / ".gitignore").symlink_to(victim)
    staged(repo, {".env": "DB_PASSWORD=" + "Zx9!kQ2#vB7$mN4p\n"})
    fix(repo, "--yes")
    assert victim.read_text() == "keep\n"


def test_multi_line_statements_and_pyw_are_left_alone(repo):
    staged(repo, {"a.py": f"result = (\n    {BP}\n)\nitems = [1,\n {BP}\n]\n",
                  "b.pyw": f"if x:\n    {BP}\ny = 1\n"})
    proc = fix(repo, "--yes")
    assert (repo.path / "a.py").read_text() == f"result = (\n    {BP}\n)\nitems = [1,\n {BP}\n]\n"
    assert (repo.path / "b.pyw").read_text() == f"if x:\n    {BP}\ny = 1\n" and "by hand" in proc.stdout


def test_debugger_inside_a_template_literal_is_kept(repo):
    js = "const s = `\n" + DEBUGGER + "\n`\nfunction f() {\n  " + DEBUGGER + "\n}\n"
    staged(repo, {"web/a.js": js})
    fix(repo, "--yes")
    assert (repo.path / "web/a.js").read_text() == "const s = `\n" + DEBUGGER + "\n`\nfunction f() {\n}\n"


def test_unusual_line_breaks_do_not_shift_the_edit(repo):
    test = "const t = 'a\u2028b'\n" + ONLY + "'x', () => { expect(1).toBe(1) })\nit('y', () => {})\n"
    staged(repo, {"web/a.test.js": test})
    fix(repo, "--yes")
    assert (repo.path / "web/a.test.js").read_text(encoding="utf-8") == \
        "const t = 'a\u2028b'\nit('x', () => { expect(1).toBe(1) })\nit('y', () => {})\n"


@posix
def test_unwritable_file_fails_cleanly_without_half_applied_edits(repo):
    if os.geteuid() == 0:
        return
    staged(repo, {"a.py": f"{BP}\nx = 1\n", "b.py": f"{BP}\ny = 1\n"})
    os.chmod(repo.path / "b.py", 0o444)
    proc = fix(repo, "--yes")
    assert proc.returncode == 2 and "cannot write" in proc.stderr and "Traceback" not in proc.stderr
    assert (repo.path / "a.py").read_text() == f"{BP}\nx = 1\n"                        # nothing was written


def test_dry_run_warns_when_a_tracked_env_file_will_be_deleted(repo):
    repo.write(".env", "DB_PASSWORD=" + "Zx9!kQ2#vB7$mN4p\n")
    repo.commit("oops")
    repo.write(".env", "DB_PASSWORD=" + "Zx9!kQ2#vB7$mN4q\n")
    repo.add()
    assert "already in HEAD" in fix(repo).stdout


def test_nothing_to_fix_with_findings_that_have_no_automatic_fix(repo):
    staged(repo, {"a.py": "x = eval(y)\n"})
    proc = fix(repo)
    assert proc.returncode == 0 and "nothing to fix" in proc.stdout and find(repo.check(), "security.eval")
