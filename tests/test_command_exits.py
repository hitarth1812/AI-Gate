"""Part E of the command-level defects: config paths and errors, exit codes, a closed pipe, the pull-request base, `-q`, and unknown
commands. Every test runs the CLI in a temporary repository and reads stdout, stderr and the exit code."""
import json
import subprocess
import sys

import pytest

from conftest import SCRIPT, aigate, find

EVAL = "ev" + "al(y)"
BAD_TOML = "preset = [\n"
# GitHub sets GITHUB_BASE_REF on every pull_request run (GitLab sets the other one); the tool honours them, so a test that is
# about their ABSENCE has to say so (an empty value counts as unset)
NO_BASE = {"GITHUB_BASE_REF": "", "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": ""}


def staged(repo, files=None):
    for rel, text in (files or {"a.py": "x = 1\n"}).items():
        repo.write(rel, text)
    repo.add()


# --------------------------------------------------------------------------------------------- E1: --config must exist

@pytest.mark.parametrize("have_staged", [True, False])
def test_a_missing_config_is_an_error_that_names_the_path(repo, have_staged):
    if have_staged:
        staged(repo)
    proc = repo.run("check", "--config", "nope.toml", "--offline")
    assert proc.returncode == 2 and "nope.toml" in proc.stderr and "config" in proc.stderr
    assert proc.stdout == ""


def test_a_missing_absolute_config_path_is_named_exactly(repo, tmp_path):
    staged(repo)
    missing = str(tmp_path / "no such dir" / "lite.toml")
    proc = repo.run("check", "--config", missing, "--offline")
    assert proc.returncode == 2 and missing in proc.stderr


def test_a_directory_is_not_a_config(repo, tmp_path):
    staged(repo)
    proc = repo.run("check", "--config", str(tmp_path), "--offline")
    assert proc.returncode == 2 and str(tmp_path) in proc.stderr


def test_audit_and_review_refuse_a_missing_config_too(repo):
    assert repo.run("audit", "--config", "nope.toml", "--offline").returncode == 2
    proc = repo.run("review", "--config", "nope.toml", "--offline")
    assert proc.returncode == 2 and "nope.toml" in proc.stderr


def test_json_stdout_stays_empty_when_the_config_is_missing(repo):
    staged(repo)
    proc = repo.run("check", "--config", "nope.toml", "--format", "json", "--offline")
    assert proc.returncode == 2 and proc.stdout == ""


def test_an_existing_config_still_works(repo):
    staged(repo)
    repo.write("mine.toml", 'preset = "relaxed"\n')
    proc = repo.run("check", "--config", "mine.toml", "--offline")
    assert proc.returncode == 0 and "using defaults" not in proc.stdout


# ---------------------------------------------------------------------------------- E2: a config error names its own file

def test_an_invalid_config_names_the_file_that_was_read(repo):
    staged(repo)
    repo.write("mine.toml", BAD_TOML)
    res = repo.check("--config", "mine.toml", "--offline")
    bad = find(res, "gate.config_invalid")
    assert len(bad) == 1 and bad[0]["path"] == "mine.toml"
    assert bad[0]["message"].startswith("mine.toml is not valid TOML") and ".aigate/lite.toml" not in bad[0]["message"]


def test_the_text_report_names_it_too(repo):
    staged(repo)
    repo.write("mine.toml", BAD_TOML)
    out = repo.run("check", "--config", "mine.toml", "--offline").stdout
    assert "mine.toml is not valid TOML" in out and ".aigate/lite.toml" not in out


def test_an_absolute_config_path_is_shown_as_given(repo, tmp_path):
    staged(repo)
    cfg = tmp_path / "elsewhere.toml"
    cfg.write_text(BAD_TOML, encoding="utf-8", newline="\n")
    res = repo.check("--config", str(cfg), "--offline")
    assert find(res, "gate.config_invalid")[0]["message"].startswith(f"{cfg} is not valid TOML")


def test_the_version_pin_finding_points_at_the_file_that_was_read(repo):
    staged(repo)
    repo.write("mine.toml", '[gate]\nrequires = ">=99.0"\n')
    res = repo.check("--config", "mine.toml", "--offline")
    assert [f["path"] for f in find(res, "gate.version_too_old")] == ["mine.toml"]


def test_the_default_file_keeps_its_name(repo):
    repo.write(".aigate/lite.toml", BAD_TOML)
    staged(repo)
    bad = find(repo.check("--offline"), "gate.config_invalid")[0]
    assert bad["path"] == ".aigate/lite.toml" and bad["message"].startswith(".aigate/lite.toml is not valid TOML")


def test_an_agents_committed_config_is_named_as_the_committed_file(repo):
    repo.write(".aigate/lite.toml", BAD_TOML)
    repo.commit("bad config")
    staged(repo)
    bad = find(repo.check("--offline", env={"CLAUDECODE": "1"}), "gate.config_invalid")[0]
    assert bad["path"] == ".aigate/lite.toml" and ".aigate/lite.toml" in bad["message"]


# ------------------------------------------------------------------------------------------------- E3: unvendor

def hook_mode(repo):
    return aigate.hook_mode((repo.path / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8"))


def test_unvendor_that_cannot_switch_exits_non_zero_and_leaves_the_hooks(repo):
    assert repo.run("install", "--vendor").returncode == 0
    proc = repo.run("unvendor")                                   # this copy is not an installed package
    assert proc.returncode != 0 and "pipx install aigate-lite" in proc.stderr and hook_mode(repo) == "vendored"


@pytest.mark.parametrize("situation", ["foreign-hook", "pre-commit-framework", "hooks-path"])
def test_unvendor_that_install_refuses_exits_non_zero_and_changes_nothing(repo, monkeypatch, capsys, situation):
    hooks = repo.path / ".git" / "hooks"
    if situation == "foreign-hook":
        (hooks / "pre-commit").write_text("#!/bin/sh\necho mine\n", newline="\n", encoding="utf-8")
    else:
        assert repo.run("install", "--vendor").returncode == 0
        if situation == "pre-commit-framework":
            repo.write(".pre-commit-config.yaml", "repos: []\n")
        else:
            repo.git("config", "core.hooksPath", ".husky")
    before = (hooks / "pre-commit").read_text(encoding="utf-8")
    monkeypatch.chdir(repo.path)
    monkeypatch.setattr(aigate, "installed_package", lambda: True)           # as if run from a pip/pipx install
    code = aigate.cmd_unvendor(aigate.build_parser().parse_args(["unvendor"]))
    capsys.readouterr()
    assert code != 0 and (hooks / "pre-commit").read_text(encoding="utf-8") == before


def test_unvendor_exits_non_zero_when_the_hooks_are_not_installed_mode_afterwards(repo, monkeypatch, capsys):
    assert repo.run("install", "--vendor").returncode == 0
    monkeypatch.chdir(repo.path)
    monkeypatch.setattr(aigate, "installed_package", lambda: True)
    monkeypatch.setattr(aigate, "cmd_install", lambda args: 0)               # an install that claims success and changes nothing
    code = aigate.cmd_unvendor(aigate.build_parser().parse_args(["unvendor"]))
    err = capsys.readouterr().err
    assert code == 1 and "did not switch" in err and "pre-commit" in err


# ------------------------------------------------------------------------------------------ E4: a closed pipe

def big_report_repo(repo, n=3000):
    repo.write("big.py", "".join(f"v{i} = {EVAL}\n" for i in range(n)))
    repo.commit("many findings")


def run_and_close_after_first_line(repo, *args):
    """Start the CLI, read one line of its stdout, close the pipe (like `| head -1`), return (first line, stderr, code)."""
    proc = subprocess.Popen([sys.executable, str(SCRIPT), *args], cwd=repo.path, env=repo.env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    first = proc.stdout.readline()
    proc.stdout.close()
    err = proc.stderr.read()
    proc.stderr.close()
    return first, err, proc.wait(timeout=180)


def test_audit_piped_into_a_reader_that_stops_exits_quietly_with_the_verdict_code(repo):
    big_report_repo(repo)
    first, err, code = run_and_close_after_first_line(repo, "audit", "--offline")
    assert first and code == 1                       # the verdict is FIX: exit 1, not 2 and not a traceback
    assert err == b""                                # nothing on stderr: no BrokenPipeError, no "Exception ignored"


def test_a_json_report_piped_into_head_c_exits_with_the_verdict_code(repo):
    big_report_repo(repo)
    first, err, code = run_and_close_after_first_line(repo, "audit", "--offline", "--format", "json")
    assert first and code == 1 and err == b""


def test_a_clean_audit_piped_away_still_exits_zero(repo):
    first, err, code = run_and_close_after_first_line(repo, "audit", "--offline", "--format", "json")
    assert code == 0 and err == b""


def test_an_error_that_escapes_with_a_closed_pipe_is_still_exit_2(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.path)

    def closed(args, env):
        raise BrokenPipeError(32, "Broken pipe")
    monkeypatch.setattr(aigate, "run_check", closed)
    assert aigate.main(["check"]) == 2 and "internal error" not in capsys.readouterr().err


# ------------------------------------------------------------------------- E5: the pull-request base, and nothing staged

def feature_branch_with_change(repo):
    repo.git("switch", "-q", "-c", "feature")
    repo.write("app.py", f"def f(y):\n    return {EVAL}\n")
    repo.commit("feature work")


def test_review_pr_without_a_base_variable_says_which_base_it_used(repo):
    feature_branch_with_change(repo)
    proc = repo.run("review", "--pr", "--offline", env=NO_BASE)
    assert proc.returncode == 1
    assert "main..HEAD" in proc.stderr and "GITHUB_BASE_REF" in proc.stderr and "--range" in proc.stderr


def test_review_pr_with_the_variable_says_nothing_extra(repo):
    feature_branch_with_change(repo)
    proc = repo.run("review", "--pr", "--offline", env={"GITHUB_BASE_REF": "main"})
    assert proc.returncode == 1 and proc.stderr == ""


def test_review_pr_on_the_base_branch_itself_exits_2_and_asks_for_a_range(repo):
    proc = repo.run("review", "--pr", "--offline", env=NO_BASE)   # HEAD is main: the guessed range is empty, a silent PASS
    assert proc.returncode == 2 and "--range" in proc.stderr and "GITHUB_BASE_REF" in proc.stderr
    assert proc.stdout == ""


def test_an_explicit_range_is_never_guessed(repo):
    feature_branch_with_change(repo)
    proc = repo.run("review", "--range", "main..HEAD", "--offline")
    assert proc.returncode == 1 and proc.stderr == ""


def test_check_with_nothing_staged_keeps_its_line_and_adds_one_suggestion_on_stderr(repo):
    proc = repo.run("check", "--offline")
    assert proc.returncode == 0 and proc.stdout == "aigate-lite: nothing staged to check\n"
    assert proc.stderr.count("\n") == 1 and "--range" in proc.stderr and "aigate-lite audit" in proc.stderr


def test_review_with_nothing_staged_suggests_range_or_audit(repo):
    proc = repo.run("review", "--offline")
    assert proc.returncode == 0 and proc.stderr.count("\n") == 1
    assert "nothing staged" in proc.stderr and "--range" in proc.stderr and "aigate-lite audit" in proc.stderr


def test_a_hook_run_with_nothing_staged_adds_no_line(repo):
    proc = repo.run("check", "--staged", "--offline")
    assert proc.stderr == ""


def test_machine_formats_with_nothing_staged_are_unchanged(repo):
    for fmt in ("json", "agent", "github"):
        proc = repo.run("check", "--format", fmt, "--offline")
        assert proc.returncode == 0 and proc.stderr == "", fmt
    assert repo.run("review", "--format", "json", "--offline").stderr == ""


# ------------------------------------------------------------------------------------------------------------ E6: -q

def stopped(repo):
    staged(repo, {"c.py": f"# TODO tidy this\ndef f(y):\n    return {EVAL}\n"})


def test_quiet_uses_the_current_layout_and_never_recommends_no_verify(repo):
    stopped(repo)
    quiet = repo.run("check", "--offline", "-q", "--color", "never")
    default = repo.run("check", "--offline", "--color", "never")
    assert quiet.returncode == 1 == default.returncode
    assert "--no-verify" not in quiet.stdout and "fingerprint" not in quiet.stdout and "config:" not in quiet.stdout
    lines = quiet.stdout.splitlines()
    assert "c.py" in lines and any(ln.strip().endswith("eval() runs code built at runtime") and "FIX" in ln for ln in lines)
    assert any(ln.startswith("next:") for ln in lines)


def test_quiet_lists_only_what_stops_the_commit(repo):
    stopped(repo)
    quiet = repo.run("check", "--offline", "-q", "--color", "never").stdout
    default = repo.run("check", "--offline", "--color", "never").stdout
    assert "code.todo" in default and "code.todo" not in quiet and "security.eval" in quiet


def test_quiet_with_a_clean_change_is_still_silent(repo):
    staged(repo)
    proc = repo.run("check", "--offline", "-q")
    assert proc.returncode == 0 and proc.stdout == ""


def test_verbose_and_quiet_together_do_not_recommend_no_verify(repo):
    stopped(repo)
    out = repo.run("check", "--offline", "-q", "-v", "--color", "never").stdout
    assert "--no-verify" not in out and "fingerprint" in out and "code.todo" not in out


def test_the_pre_push_style_quiet_report_does_not_recommend_it_either(repo):
    repo.git("switch", "-q", "-c", "topic")
    repo.write("c.py", f"def f(y):\n    return {EVAL}\n")
    repo.commit("work")
    out = repo.run("check", "--range", "main..HEAD", "--offline", "-q").stdout
    assert "--no-verify" not in out and "security.eval" in out


# ------------------------------------------------------------------------------------------------ E7: an unknown command

@pytest.mark.parametrize("typo,want", [("chek", "check"), ("instal", "install"), ("audt", "audit"), ("docter", "doctor"),
                                      ("baselin", "baseline"), ("unistall", "uninstall")])
def test_an_unknown_command_suggests_the_closest_one(repo, typo, want):
    proc = repo.run(typo)
    assert proc.returncode == 2
    assert f"did you mean `aigate-lite {want}`?" in proc.stderr
    assert "invalid choice" in proc.stderr and "see: aigate-lite --help" in proc.stderr


@pytest.mark.parametrize("word", ["update", "upgrade", "self-update", "updat", "upgrad"])
def test_update_and_upgrade_point_at_pip(repo, word):
    proc = repo.run(word)
    assert proc.returncode == 2 and "pip install -U aigate-lite" in proc.stderr


def test_a_word_with_no_close_command_gets_no_guess(repo):
    proc = repo.run("zzzzzz")
    assert proc.returncode == 2 and "did you mean" not in proc.stderr and "invalid choice" in proc.stderr


def test_real_commands_and_flags_are_not_affected(repo):
    assert repo.run("explain", "secrets.hardcoded").returncode == 0
    assert repo.run("--version").returncode == 0
    assert repo.run("check", "--bogus-flag").returncode == 2
    assert "did you mean" not in repo.run("check", "--bogus-flag").stderr


def test_json_contract_still_parses_after_these_changes(repo):
    staged(repo, {"a.py": f"def f(y):\n    return {EVAL}\n"})
    res = repo.check("--offline")
    assert res["verdict"] == "fix" and json.dumps(res)
