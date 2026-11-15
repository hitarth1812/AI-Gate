"""Round 3 (item 4): a test file that writes a module and then imports it is not importing a registry package, so a person gets no
finding. An AI agent could fake that excuse (write a file named like a made-up package, import it), so it gets a WARN that names the
excuse. The person's result is unchanged; the agent is never softer."""
import pytest

from conftest import find

AGENT = {"CLAUDECODE": "1"}
PYPROJECT = '[project]\nname = "demo"\nversion = "1"\nrequires-python = ">=3.9"\n'
CREATES = ('def test_a(tmp_path):\n    (tmp_path / "ghost_mod.py").write_text("x = 1")\n'
           '    import sys\n    sys.path.insert(0, str(tmp_path))\n    from ghost_mod import x\n    assert x == 1\n')


def run(repo, path, text):
    repo.write("pyproject.toml", PYPROJECT)
    repo.write(path, text)
    repo.add()
    return repo.check(), repo.check(env=AGENT)


def hallucinated(res):
    return [f for f in find(res, "deps.hallucinated") if not f["allowed"]]


def test_a_person_sees_nothing_and_an_agent_gets_a_warn_naming_the_excuse(repo):
    person, agent = run(repo, "tests/test_a.py", CREATES)
    assert hallucinated(person) == []
    found = hallucinated(agent)
    assert [(f["action"], f["line"]) for f in found] == [("warn", 5)]
    assert "ghost_mod" in found[0]["message"] and "writes" in found[0]["message"]
    assert agent["verdict"] == "warn" and agent["exit_code"] == 0


def test_the_warn_is_not_a_block_and_the_person_verdict_is_unchanged(repo):
    person, agent = run(repo, "tests/test_a.py", CREATES)
    assert person["verdict"] in ("pass", "warn")
    assert agent["exit_code"] == 0


def test_a_made_up_import_in_source_is_still_a_block_for_both(repo):
    person, agent = run(repo, "src/app.py", "from ghost_mod import x\n")
    assert [f["action"] for f in hallucinated(person)] == [f["action"] for f in hallucinated(agent)] == ["block"]


def test_a_test_that_does_not_create_the_module_is_still_a_block_for_both(repo):
    person, agent = run(repo, "tests/test_a.py", "def test_a():\n    from ghost_mod import x\n    assert x\n")
    assert [f["action"] for f in hallucinated(person)] == [f["action"] for f in hallucinated(agent)] == ["block"]


@pytest.mark.parametrize("name", ["ghost_pkg_one", "ghost_pkg_two"])
def test_one_warn_per_excused_import(repo, name):
    text = (f'def test_a(tmp_path):\n    (tmp_path / "{name}.py").write_text("x = 1")\n    import {name}\n    assert {name}\n')
    person, agent = run(repo, "tests/test_a.py", text)
    assert hallucinated(person) == [] and [f["action"] for f in hallucinated(agent)] == ["warn"]
