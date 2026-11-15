"""Round 3 (r3-properties-key): a provider-shaped token is secrets.hardcoded in a .properties file exactly as in any other
file, for a person and an AI agent. The corpus token (xoxb- with the digit runs 123456789012 and the letters AbCdEfGh...) is
dropped by the documentation-placeholder rule in every language, not by anything about .properties; see eval/LABEL_CHANGES.md."""
import pytest

from conftest import find

AGENT = {"CLAUDECODE": "1"}
WHO = [pytest.param(None, id="person"), pytest.param(AGENT, id="agent")]


def build(*parts):
    """The pieces joined at run time (a call, not a constant operation), so this file holds no token the repository self-scan
    could fold into one."""
    return "".join(parts)


REAL_SHAPE = build("xo", "xb-", "987654321098-", "5432109876543-", "Zq7Lm2Xv9Kp4Rt8Wy3Nb6Hc1")   # no placeholder run in it
PLACEHOLDER_SHAPE = build("xo", "xb-", "123456789012-", "1234567890123-", "AbCdEfGhIjKlMnOpQrStUvWx")


def result(repo, rel, text, env):
    repo.write(rel, text)
    repo.add()
    return repo.check(env=env)


@pytest.mark.parametrize("env", WHO)
@pytest.mark.parametrize("rel,body", [
    ("src/main/resources/application.properties", "app.name=shop\nnotify.slack.token={tok}\n"),
    ("config/app.py", "SLACK = '{tok}'\n"),
    ("config/app.yaml", "slack_token: {tok}\n"),
])
def test_a_provider_token_blocks_in_every_kind_of_file(repo, env, rel, body):
    res = result(repo, rel, body.format(tok=REAL_SHAPE), env)
    assert [(f["path"], f["action"]) for f in find(res, "secrets.hardcoded")] == [(rel, "block")]
    assert find(res, "secrets.generic") == []                    # the provider BLOCK is not shadowed by a lesser finding


def test_the_placeholder_shaped_token_is_treated_alike_in_properties_and_python(repo):
    props = result(repo, "app.properties", f"notify.slack.token={PLACEHOLDER_SHAPE}\n", None)
    py = result(repo, "app.py", f"SLACK = '{PLACEHOLDER_SHAPE}'\n", None)
    assert [f["rule"] for f in find(props, "secrets.hardcoded")] == [f["rule"] for f in find(py, "secrets.hardcoded")]
    # and an agent is never softer than a person
    agent = result(repo, "app.properties", f"notify.slack.token={PLACEHOLDER_SHAPE}\n", AGENT)
    assert sorted((f["rule"], f["action"]) for f in props["findings"]) == sorted(
        (f["rule"], f["action"]) for f in agent["findings"])
