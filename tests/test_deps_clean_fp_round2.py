"""Round 2: false deps.hallucinated BLOCKs on clean popular repositories (attrs, express, flask), found with dated
registry snapshots. These are code-evidence fixes, so they apply to a person and to an AI agent alike: every test
runs both and compares them."""
import pytest

from conftest import find

AGENT = {"CLAUDECODE": "1"}
PYPROJECT = '[project]\nname = "demo"\nversion = "1"\nrequires-python = ">=3.9"\n'


def both(repo):
    """The deps findings (rule, path, line, action) of a person and of an agent for the staged change."""
    repo.add()
    out = []
    for env in (None, AGENT):
        res = repo.check(env=env)
        out.append(sorted((f["rule"], f["path"], f["line"], f["action"]) for f in res["findings"]
                          if f["rule"].startswith("deps.") and not f["allowed"]))
    return out


def blocked(findings, rule="deps.hallucinated"):
    return [f for f in findings if f[0] == rule]


# ---------------------------------------------------------------- 1. stdlib modules newer than the running Python

@pytest.mark.parametrize("stmt", ["import annotationlib", "from annotationlib import Format, get_annotations",
                                  "import compression.zstd", "from compression import zstd"])
def test_python_314_stdlib_module_is_not_hallucinated(repo, stmt):
    # clean-py-attrs src/attr/_compat.py:31 `import annotationlib` (3.14 stdlib; the gate may run on 3.12)
    repo.write("pyproject.toml", PYPROJECT)
    repo.write("src/attr/_compat.py", f"PY_3_14_PLUS = True\n\nif PY_3_14_PLUS:\n    {stmt}\n")
    person, agent = both(repo)
    assert person == agent == []


def test_made_up_name_next_to_a_new_stdlib_name_still_blocks(repo):
    repo.write("pyproject.toml", PYPROJECT)
    repo.write("src/app.py", "import annotationlibs\nimport compressions\n")
    person, agent = both(repo)
    assert len(blocked(person)) == 2 and person == agent
