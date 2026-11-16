"""Part C of the command-level defects: the wrong repository and untracked files. Every test runs the CLI in a temporary repository
(some from a sub-folder) and reads stdout, stderr and the exit code."""
import json
import subprocess
import sys

from conftest import SCRIPT, GitRepo, child_encoding, decode_output


def run_in(repo, sub, *args, env=None, stdin=None):
    """The CLI with the current folder set to repo/sub."""
    env = {**repo.env, **(env or {})}
    enc = child_encoding(env)
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=(repo.path / sub) if sub else repo.path, env=env,
                          capture_output=True, input=stdin)
    proc.stdout, proc.stderr = decode_output(proc.stdout, enc), decode_output(proc.stderr, enc)
    return proc


def nested_project(repo, files=None):
    """A new project folder inside the parent repository, with files git does not track."""
    for rel, text in (files or {"proj/app.py": "x = 1\n", "proj/notes.txt": "hello\n"}).items():
        repo.write(rel, text)
    return "proj"


# ------------------------------------------------------------------------------------ C1: say which repository is used

def test_a_folder_inside_a_repository_says_which_repository_is_used(repo):
    sub = nested_project(repo)
    proc = run_in(repo, sub, "audit", "--offline")
    assert "using the git repository at " in proc.stderr and "(you are in " in proc.stderr
    assert repo.path.name in proc.stderr and sub in proc.stderr.split("(you are in ", 1)[1]
    assert proc.stderr.count("using the git repository at") == 1


def test_no_such_line_at_the_top_of_the_repository(repo):
    proc = run_in(repo, "", "audit", "--offline")
    assert "using the git repository" not in proc.stderr + proc.stdout


def test_the_line_goes_to_stderr_so_json_on_stdout_stays_valid(repo):
    sub = nested_project(repo)
    proc = run_in(repo, sub, "check", "--format", "json", "--offline")
    assert proc.returncode in (0, 1) and "using the git repository" in proc.stderr
    assert json.loads(proc.stdout)["tool"] == "aigate-lite"


def test_a_hook_run_prints_nothing_extra(repo):
    sub = nested_project(repo)
    proc = run_in(repo, sub, "check", "--staged", "--offline")
    assert "using the git repository" not in proc.stderr + proc.stdout


# ---------------------------------------------------------------------------------- C2: untracked files are never a bare PASS

def test_audit_says_untracked_files_were_not_scanned(repo):
    repo.write("new1.py", "x = 1\n")
    repo.write("new2.py", "y = 2\n")
    proc = run_in(repo, "", "audit", "--offline")
    assert proc.returncode == 0
    assert "2 files here are not tracked by git and were NOT scanned; run `git add -A`" in proc.stdout
    assert "git init" not in proc.stdout                              # this folder already is the repository


def test_the_message_counts_only_this_folder_and_offers_git_init_inside_a_parent_repository(repo):
    sub = nested_project(repo, {"proj/a.py": "x = 1\n", "proj/b.py": "y = 2\n", "proj/c.py": "z = 3\n", "other.py": "w = 4\n"})
    proc = run_in(repo, sub, "audit", "--offline")
    assert ("3 files here are not tracked by git and were NOT scanned; run `git add -A` "
            "(or `git init` if this folder should be its own repository)") in proc.stdout


def test_one_file_reads_in_the_singular(repo):
    repo.write("only.py", "x = 1\n")
    out = run_in(repo, "", "audit", "--offline").stdout
    assert "1 file here is not tracked by git and was NOT scanned; run `git add -A`" in out


def test_a_staged_check_says_it_too_but_not_when_the_hook_runs_it(repo):
    repo.write("new1.py", "x = 1\n")
    repo.write("staged.py", "y = 2\n")
    repo.add("staged.py")
    manual = run_in(repo, "", "check", "--offline")
    assert "1 file here is not tracked by git and was NOT scanned" in manual.stdout
    hook = run_in(repo, "", "check", "--staged", "--offline")
    assert "NOT scanned" not in hook.stdout


def test_gitignored_files_are_not_counted_and_tracked_files_are_not_mentioned(repo):
    repo.write(".gitignore", "*.log\n")
    repo.write("debug.log", "noise\n")
    repo.commit("ignore logs")
    proc = run_in(repo, "", "audit", "--offline")
    assert "NOT scanned" not in proc.stdout


def test_json_output_is_untouched_by_the_untracked_note(repo):
    repo.write("new1.py", "x = 1\n")
    res = repo.check("--all", "--offline")
    assert not any("not tracked" in n for n in res["notes"])


def test_quiet_with_a_clean_result_still_says_files_were_not_scanned(repo):
    repo.write("new1.py", "x = 1\n")
    proc = run_in(repo, "", "audit", "--offline", "-q")
    assert proc.returncode == 0 and "NOT scanned" in proc.stderr


def test_after_git_add_the_note_is_gone_and_the_files_are_scanned(repo):
    repo.write("new1.py", "x = ev" + "al(input())\n")
    assert "NOT scanned" in run_in(repo, "", "audit", "--offline").stdout
    repo.commit("add")
    again = run_in(repo, "", "audit", "--offline")
    assert "NOT scanned" not in again.stdout and again.returncode == 1


# ----------------------------------------------------------------------------- C3: a folder that git does not track at all

def test_setup_in_an_untracked_folder_inside_a_parent_repository_stops_and_asks(repo):
    sub = nested_project(repo)
    proc = run_in(repo, sub, "setup")
    assert proc.returncode == 2
    assert "no files tracked by the git repository at " in proc.stderr and "--yes" in proc.stderr and "git init" in proc.stderr
    assert not (repo.path / ".git" / "hooks" / "pre-commit").exists()
    assert not (repo.path / ".aigate").exists()


def test_install_stops_the_same_way(repo):
    sub = nested_project(repo)
    proc = run_in(repo, sub, "install")
    assert proc.returncode == 2 and "--yes" in proc.stderr
    assert not (repo.path / ".git" / "hooks" / "pre-commit").exists()


def test_yes_proceeds_into_the_parent_repository(repo):
    sub = nested_project(repo)
    proc = run_in(repo, sub, "setup", "--yes")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (repo.path / ".git" / "hooks" / "pre-commit").exists()


def test_a_folder_with_tracked_files_needs_no_question(repo):
    repo.write("pkg/mod.py", "x = 1\n")
    repo.commit("pkg")
    proc = run_in(repo, "pkg", "install")
    assert proc.returncode == 0 and (repo.path / ".git" / "hooks" / "pre-commit").exists()


def test_setup_at_the_top_of_a_repository_is_unchanged(repo):
    proc = run_in(repo, "", "setup")
    assert proc.returncode == 0 and (repo.path / ".git" / "hooks" / "pre-commit").exists()


def test_doctor_warns_about_the_same_case(repo):
    sub = nested_project(repo)
    proc = run_in(repo, sub, "doctor", "--format", "json", "--offline")
    row = [c for c in json.loads(proc.stdout) if c["id"] == "repo"]
    assert len(row) == 1 and row[0]["status"] == "warn"
    assert "no tracked files" in row[0]["detail"] and "git init" in row[0]["fix"]


def test_doctor_is_ok_for_a_normal_folder(repo):
    row = [c for c in json.loads(run_in(repo, "", "doctor", "--format", "json", "--offline").stdout) if c["id"] == "repo"]
    assert row[0]["status"] == "ok"


# ------------------------------------------------------------------- C4: project detection reads the working tree

def fresh_repo(tmp_path):
    """A repository with no commit and nothing staged yet."""
    (tmp_path / "fresh").mkdir()
    return GitRepo(tmp_path / "fresh", tmp_path / "fresh-cache")


def test_init_detects_a_new_repository_before_the_first_git_add(tmp_path):
    new = fresh_repo(tmp_path)
    new.write("app.py", "print('hi')\n")
    new.write("package.json", '{"name": "x", "scripts": {"test": "jest"}}\n')
    new.write("go.mod", "module example.com/x\n\ngo 1.22\n")
    proc = new.run("init")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    first = proc.stdout.splitlines()[0]
    assert first.startswith("Detected: ") and "Python" in first and "JavaScript" not in first   # languages come from source files
    assert "packages: " in first and "npm" in first and "go" in first.split("packages: ", 1)[1]
    assert "tests: " in first and "nothing" not in first


def test_init_respects_gitignore_when_it_reads_the_working_tree(tmp_path):
    new = fresh_repo(tmp_path)
    new.write(".gitignore", "vendor/\n")
    new.write("vendor/dep.go", "package dep\n")
    new.write("app.py", "x = 1\n")
    first = new.run("init", "--print").stdout.splitlines()[0]
    assert first == "Detected: Python"                                     # vendor/dep.go is ignored: no Go


def test_setup_in_a_new_repository_prints_one_next_steps_block(tmp_path):
    new = fresh_repo(tmp_path)
    new.write("app.py", "x = 1\n")
    proc = new.run("setup")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Detected: " in proc.stdout and "nothing" not in proc.stdout.split("Detected: ", 1)[1].splitlines()[0]
    assert proc.stdout.count("Next steps:") == 1
    assert not any(ln.startswith("next:") for ln in proc.stdout.splitlines())


def test_init_alone_still_ends_with_its_next_line(repo):
    proc = repo.run("init")
    assert proc.returncode == 0 and proc.stdout.splitlines()[-1] == "next: aigate-lite doctor"


def test_init_in_an_empty_new_repository_says_no_project_files(tmp_path):
    new = fresh_repo(tmp_path)
    assert new.run("init", "--print").stdout.splitlines()[0] == "Detected: nothing (no project files found)"


# ----------------------------------------------------------------------- C5: what is the hook that is already there

def put_hook(repo, kind, body):
    hooks = repo.path / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / kind).write_text(body, newline="\n", encoding="utf-8")


def install_over(repo, kind, body):
    put_hook(repo, kind, body)
    proc = repo.run("install")
    assert proc.returncode == 1
    return proc.stdout


def test_an_existing_hook_is_identified_as_the_pre_commit_framework(repo):
    out = install_over(repo, "pre-commit", "#!/usr/bin/env bash\n# File generated by pre-commit: https://pre-commit.com\n"
                       "# ID: 138fd403232d2ddd5efb44317e38bf03\nINSTALL_PYTHON=/usr/bin/python3\nexec pre-commit hook-impl\n")
    assert "already exists" in out
    assert "first line: INSTALL_PYTHON=/usr/bin/python3" in out
    assert "looks like: the pre-commit framework" in out
    assert "Re-run with --force to keep it as pre-commit.local; it will still run first." in out


def test_husky(repo):
    out = install_over(repo, "pre-commit", '#!/usr/bin/env sh\n. "$(dirname -- "$0")/_/husky.sh"\n\nnpx lint-staged\n')
    assert "looks like: husky" in out and 'first line: . "$(dirname -- "$0")/_/husky.sh"' in out


def test_lefthook(repo):
    out = install_over(repo, "pre-commit", "#!/bin/sh\n\nif [ \"$LEFTHOOK\" = \"0\" ]; then\n  exit 0\nfi\nlefthook run pre-commit\n")
    assert "looks like: lefthook" in out and 'first line: if [ "$LEFTHOOK" = "0" ]; then' in out


def test_git_lfs(repo):
    out = install_over(repo, "pre-push", "#!/bin/sh\ncommand -v git-lfs >/dev/null 2>&1 || { echo >&2 \"git-lfs missing\"; exit 2; }\n"
                       "git lfs pre-push \"$@\"\n")
    assert "looks like: git-lfs" in out and "first line: command -v git-lfs" in out


def test_an_aigate_lite_hook_from_another_install(repo):
    out = install_over(repo, "pre-commit", "#!/bin/sh\nexec aigate-lite check --staged\n")
    assert "looks like: aigate-lite from another install" in out and "first line: exec aigate-lite check --staged" in out


def test_an_unknown_hook(repo):
    out = install_over(repo, "pre-commit", "#!/bin/sh\n# my own check\n\necho running my checks\nmake lint\n")
    assert "looks like: unknown" in out and "first line: echo running my checks" in out


def test_a_hook_with_only_comments_has_no_first_line(repo):
    out = install_over(repo, "pre-commit", "#!/bin/sh\n# nothing here\n")
    assert "looks like: unknown" in out and "first line: (none: only comments)" in out


def test_a_very_long_first_line_is_cut(repo):
    out = install_over(repo, "pre-commit", "#!/bin/sh\n" + "echo " + "x" * 400 + "\n")
    line = [ln for ln in out.splitlines() if "first line:" in ln][0]
    assert len(line) < 200 and line.endswith("...")


def test_force_still_keeps_the_old_hook(repo):
    put_hook(repo, "pre-commit", "#!/bin/sh\necho mine\n")
    proc = repo.run("install", "--force")
    assert proc.returncode == 0
    assert (repo.path / ".git" / "hooks" / "pre-commit.local").read_text() == "#!/bin/sh\necho mine\n"
