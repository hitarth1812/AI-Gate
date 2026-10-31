import sys

from conftest import find

PY = sys.executable.replace("\\", "/")


def config(name="tests", code=3, stage="commit"):
    # TOML literal string: no escapes, so Windows paths and spaces are safe; the path is quoted for the shell
    return f"[[commands]]\nname = \"{name}\"\nrun = '\"{PY}\" -c \"raise SystemExit({code})\"'\nstage = \"{stage}\"\n"


def staged_change(repo):
    repo.write("a.py", "x = 1\n")
    repo.add()


def failed(res):
    return find(res, "commands.failed")


def untrusted(res):
    return [f for f in find(res, "commands.untrusted") if f["action"] == "warn" and "aigate-lite trust" in f["message"]]


def test_repo_commands_do_not_run_until_trusted(repo):
    repo.write(".aigate/lite.toml", config())
    repo.commit()
    staged_change(repo)
    res = repo.check()
    assert not failed(res) and len(untrusted(res)) == 1 and res["code"] == 0      # warn only, nothing ran
    proc = repo.run("trust")
    assert proc.returncode == 0 and "tests" in proc.stdout and "raise SystemExit(3)" in proc.stdout
    res = repo.check()
    assert "exited with code 3" in failed(res)[0]["message"] and not untrusted(res)


def test_changed_commands_need_new_approval(repo):
    repo.write(".aigate/lite.toml", config(code=3))
    repo.commit()
    assert repo.run("trust").returncode == 0
    repo.write(".aigate/lite.toml", config(code=4))                # someone edits the command
    staged_change(repo)
    res = repo.check()
    assert not failed(res) and untrusted(res)
    assert repo.run("trust").returncode == 0
    assert "exited with code 4" in failed(repo.check())[0]["message"]


def test_switching_back_to_an_approved_list_needs_no_new_approval(repo):
    repo.write(".aigate/lite.toml", config(code=3))
    repo.commit()
    repo.run("trust")
    repo.write(".aigate/lite.toml", config(code=4))
    repo.commit()
    repo.run("trust")
    repo.write(".aigate/lite.toml", config(code=3))
    staged_change(repo)
    assert "exited with code 3" in failed(repo.check())[0]["message"]


def test_no_commands_means_no_finding_and_trust_says_so(repo):
    staged_change(repo)
    res = repo.check()
    assert not find(res, "commands.failed")
    proc = repo.run("trust")
    assert proc.returncode == 0 and "no [[commands]]" in proc.stdout


def test_command_line_config_counts_as_trusted(repo, tmp_path):
    cfg = tmp_path / "ci lite.toml"
    cfg.write_text(config(code=5))
    staged_change(repo)
    res = repo.check("--config", str(cfg))
    assert "exited with code 5" in failed(res)[0]["message"] and not untrusted(res)


def test_commands_for_another_stage_need_no_warning(repo):
    repo.write(".aigate/lite.toml", config(stage="push"))
    repo.commit()
    staged_change(repo)
    assert not find(repo.check(), "commands.failed")


def test_corrupt_trust_file_is_untrusted_not_an_error(repo):
    repo.write(".aigate/lite.toml", config())
    repo.commit()
    (repo.path / ".git" / "aigate").mkdir(exist_ok=True)
    (repo.path / ".git" / "aigate" / "trusted").write_bytes(b"\xff\xfe garbage\r\n\r\nnot-a-hash\r\n")
    staged_change(repo)
    res = repo.check()
    assert untrusted(res) and not failed(res) and not find(res, "gate.error")
    assert repo.run("trust").returncode == 0                       # rewrites around the junk
    assert failed(repo.check())


def test_any_field_change_needs_new_approval(repo):
    repo.write(".aigate/lite.toml", config())
    repo.commit()
    repo.run("trust")
    repo.write(".aigate/lite.toml", config() + 'when = ["*.py"]\n')
    staged_change(repo)
    assert untrusted(repo.check())


def test_trust_shows_hidden_characters_and_refuses_agents(repo):
    repo.write(".aigate/lite.toml", '[[commands]]\nname = "tests\\u001b[2K"\nrun = "echo hi\\rpytest"\n')
    repo.commit()
    agent = repo.run("trust", env={"CLAUDECODE": "1"})
    assert agent.returncode == 1 and "AI agent" in agent.stderr
    proc = repo.run("trust")
    assert proc.returncode == 0 and "\\x1b[2K" in proc.stdout and "\\r" in proc.stdout and "\x1b" not in proc.stdout


def test_trust_a_revision_for_push(repo):
    repo.git("checkout", "-q", "-b", "feature")
    repo.write(".aigate/lite.toml", config(code=6, stage="push"))
    repo.commit()
    repo.git("checkout", "-q", "main")
    assert repo.run("trust", "--rev", "feature").returncode == 0
    res = repo.check("--range", "main..feature")
    assert "exited with code 6" in failed(res)[0]["message"]


def test_files_cannot_inject_shell_syntax(repo):
    probe = f"'\"{PY}\" -c \"import sys; print(sys.argv[1:]); raise SystemExit(3)\" {{files}}'"
    repo.write(".aigate/lite.toml", f'[[commands]]\nname = "lint"\nrun = {probe}\nstage = "commit"\n')
    repo.commit()
    repo.run("trust")
    repo.write("a&echo INJECTED&.py", "x = 1\n")
    repo.write("-rf.py", "y = 2\n")
    repo.add()
    hint = failed(repo.check())[0]["hint"]
    assert "INJECTED" in hint and not any(ln.strip() == "INJECTED" for ln in hint.splitlines())
    assert "./-rf.py" in hint


def test_untrusted_notice_has_its_own_rule_that_repo_rules_cannot_change(repo):
    from conftest import aigate
    rule = aigate.RULES["commands.untrusted"]
    assert (rule.action, rule.severity) == ("warn", "low") and len(aigate.RULES) == 66
    for override in ('"commands.untrusted" = "off"', '"commands.untrusted" = "block"', '"commands.*" = "block"'):
        repo.write(".aigate/lite.toml", config() + f"[rules]\n{override}\n")
        staged_change(repo)
        res = repo.check()
        notice = untrusted(res)
        assert len(notice) == 1 and notice[0]["action"] == "warn" and res["code"] == 0, override
        assert res["risk"] - repo.check("--no-commands")["risk"] <= 2.0, override    # low: weight 2 at most
    problems = " ".join(f["message"] for f in find(res, "gate.config_invalid"))
    assert "commands.untrusted" in problems


def test_command_failures_are_still_overridable(repo):
    repo.write(".aigate/lite.toml", config() + '[rules]\n"commands.failed" = "warn"\n')
    repo.commit()
    repo.run("trust")
    staged_change(repo)
    res = repo.check()
    assert failed(res)[0]["action"] == "warn" and res["code"] == 0


def test_explain_lists_the_new_rule(repo):
    out = repo.run("explain").stdout
    assert "commands.untrusted" in out and "commands.failed" in out
