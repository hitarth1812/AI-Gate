"""0.5.3 round 2 (A6, decided by the human): examples/ and docs/ are not security-sensitive, so the one-step sensitive-path
escalation does not apply to a PERSON's change there, even when the path also contains `auth/`. It is a person-only softening:
an AI agent's result is unchanged (still escalated under auth/). Real auth/ code still escalates for everyone."""
import pytest

from conftest import find

COVERAGE_IGNORE = "/* istanbul " + "ignore next */\nfunction handler() { return 1; }\n"


def stage(repo, path):
    repo.write(path, COVERAGE_IGNORE)
    repo.add()


def coverage(res):
    return [f for f in find(res, "suppress.coverage") if not f["allowed"]]


@pytest.mark.parametrize("path", ["examples/auth/index.js", "docs/auth/example.js", "packages/x/examples/auth/index.js"])
def test_a_person_is_not_escalated_in_examples_or_docs(repo, path):
    stage(repo, path)
    found = coverage(repo.check("--offline"))
    assert found and found[0]["action"] == "fix", found          # the default action, not block


@pytest.mark.parametrize("path", ["examples/auth/index.js", "docs/auth/example.js"])
def test_an_agent_is_still_escalated_there(repo, path):
    stage(repo, path)
    found = coverage(repo.check("--offline", env={"CLAUDECODE": "1"}))
    assert found and found[0]["action"] == "block", found


def test_real_auth_code_still_escalates_for_a_person(repo):
    stage(repo, "src/auth/index.js")
    found = coverage(repo.check("--offline"))
    assert found and found[0]["action"] == "block", found


def test_the_exclusion_can_be_changed_in_config(repo):
    repo.write(".aigate/lite.toml", 'version = 1\n[paths]\nsensitive_exclude = []\n')
    repo.commit("config")
    stage(repo, "examples/auth/index.js")
    found = coverage(repo.check("--offline"))
    assert found and found[0]["action"] == "block", found        # an empty list turns the exclusion off
