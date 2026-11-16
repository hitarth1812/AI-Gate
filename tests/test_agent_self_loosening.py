"""Part D of the command-level defects: an AI agent cannot loosen its own gate. `scope set/add/clear` and `uninstall` refuse for an agent
(like `baseline` and `trust`), `scope show` still works, and the hint an agent sees for scope.out_of_scope says to ask the human.
User flows: the CLI runs in a temporary repository, with and without CLAUDECODE in the environment."""
import json

AGENT = {"CLAUDECODE": "1"}
SCOPE_ADD = "aigate-lite scope add"
ASK = "stop and ask the human to widen the scope"


def scope_file(repo):
    return repo.path / ".git" / "aigate" / "scope"


def person_scope(repo, *globs):
    proc = repo.run("scope", "set", *globs)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert scope_file(repo).exists()


# ------------------------------------------------------------------------------------------------ D1: scope

def test_an_agent_cannot_set_a_scope(repo):
    proc = repo.run("scope", "set", "src/**", env=AGENT)
    assert proc.returncode == 1
    assert "AI agent" in proc.stderr and "scope" in proc.stderr and "CLAUDECODE" in proc.stderr
    assert not scope_file(repo).exists()


def test_an_agent_cannot_add_to_the_scope(repo):
    person_scope(repo, "src/**")
    proc = repo.run("scope", "add", "docs/**", env=AGENT)
    assert proc.returncode == 1 and "AI agent" in proc.stderr
    assert scope_file(repo).read_text().count("docs/**") == 0


def test_an_agent_cannot_clear_the_scope(repo):
    person_scope(repo, "src/**")
    proc = repo.run("scope", "clear", env=AGENT)
    assert proc.returncode == 1 and "AI agent" in proc.stderr
    assert scope_file(repo).exists() and "src/**" in scope_file(repo).read_text()


def test_an_agent_can_still_show_the_scope(repo):
    person_scope(repo, "src/**", "tests/**")
    proc = repo.run("scope", "show", env=AGENT)
    assert proc.returncode == 0 and proc.stdout.splitlines() == ["src/**", "tests/**"]
    assert repo.run("scope", "show", env=AGENT).stderr == ""


def test_an_agent_sees_the_message_even_without_globs(repo):
    proc = repo.run("scope", "set", env=AGENT)                      # refusal comes before the usage hint
    assert proc.returncode == 1 and "AI agent" in proc.stderr


def test_a_person_is_unchanged(repo):
    person_scope(repo, "src/**")
    assert repo.run("scope", "add", "docs/**").returncode == 0
    assert "docs/**" in repo.run("scope", "show").stdout
    cleared = repo.run("scope", "clear")
    assert cleared.returncode == 0 and "scope cleared" in cleared.stdout and not scope_file(repo).exists()


def test_the_agent_marker_in_the_config_counts_too(repo):
    repo.write(".aigate/lite.toml", '[provenance]\nagent_env = ["MY_BOT"]\n')
    repo.commit("config")
    proc = repo.run("scope", "set", "src/**", env={"MY_BOT": "1"})
    assert proc.returncode == 1 and "MY_BOT" in proc.stderr and not scope_file(repo).exists()


def stage_out_of_scope(repo):
    person_scope(repo, "src/**")
    repo.write("docs/o.py", "y = 2\n")
    repo.add("docs/o.py")


def hints(res):
    return [f["hint"] for f in res["findings"] if f["rule"] == "scope.out_of_scope"]


def test_the_agent_hint_says_to_ask_the_human_not_to_widen_the_scope_itself(repo):
    stage_out_of_scope(repo)
    text = repo.run("check", "--staged", "--offline", env=AGENT).stdout
    assert ASK in text and SCOPE_ADD not in text
    verbose = repo.run("check", "--staged", "--offline", "-v", env=AGENT).stdout
    assert ASK in verbose and SCOPE_ADD not in verbose
    agent_fmt = repo.run("check", "--staged", "--offline", "--format", "agent", env=AGENT).stdout
    assert "scope.out_of_scope" in agent_fmt and SCOPE_ADD not in agent_fmt
    res = repo.check("--offline", env=AGENT)
    assert hints(res) and all(ASK in h and SCOPE_ADD not in h for h in hints(res))


def test_the_person_hint_still_names_scope_add(repo):
    stage_out_of_scope(repo)
    text = repo.run("check", "--staged", "--offline").stdout
    assert SCOPE_ADD in text and ASK not in text
    assert all(SCOPE_ADD in h for h in hints(repo.check("--offline")))


def test_the_explain_page_is_the_same_for_everyone(repo):
    assert SCOPE_ADD in repo.run("explain", "scope.out_of_scope", env=AGENT).stdout


def test_json_keys_do_not_change_for_an_agent(repo):
    stage_out_of_scope(repo)
    res = repo.check("--offline", env=AGENT)
    row = [f for f in res["findings"] if f["rule"] == "scope.out_of_scope"][0]
    assert {"rule", "action", "path", "line", "message", "hint"} <= set(row) and json.dumps(res)


# --------------------------------------------------------------------------------------------------- D2: uninstall

def installed(repo):
    assert repo.run("install").returncode == 0
    return repo.path / ".git" / "hooks" / "pre-commit"


def test_an_agent_cannot_uninstall_the_hooks(repo):
    hook = installed(repo)
    proc = repo.run("uninstall", env=AGENT)
    assert proc.returncode == 1
    assert "AI agent" in proc.stderr and "uninstall" in proc.stderr and "CLAUDECODE" in proc.stderr
    assert hook.exists() and "removed" not in proc.stdout


def test_a_person_can_still_uninstall(repo):
    hook = installed(repo)
    proc = repo.run("uninstall")
    assert proc.returncode == 0 and "removed pre-commit hook" in proc.stdout and not hook.exists()


def test_an_agent_that_uninstalls_nothing_is_still_refused(repo):
    proc = repo.run("uninstall", env=AGENT)            # no hooks installed: the refusal does not depend on what would change
    assert proc.returncode == 1 and "AI agent" in proc.stderr
