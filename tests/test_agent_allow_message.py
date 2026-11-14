"""0.5.3 round 2 (item 2, decided by the human): an AI agent cannot allow a finding (its config is read from HEAD and `.aigate/`
is a forbidden path for it), so a BLOCK shown to an agent never prints the [[allow]] route. It says: stop and ask the human, a person
must add the allow entry. The person's text is unchanged."""
from conftest import find

PEM = ("-----BEGIN " + "RSA PRIVATE KEY-----\n"
       + "\n".join(["MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu"] * 4) + "\n"
       + "-----END " + "RSA PRIVATE KEY-----\n")
NOSEC = "x = 1  # " + "nosec\n"                          # split: this file is not itself a finding
ASK = "stop and ask the human: a person must add the allow entry"
AGENT = {"CLAUDECODE": "1"}


def stage_key(repo):
    repo.write("keys/server.pem", PEM)
    repo.add()


def test_an_agent_text_report_for_a_key_asks_the_human_and_shows_no_allow_route(repo):
    stage_key(repo)
    out = repo.run("check", "--offline", "--color", "never", env=AGENT).stdout
    assert ASK in out
    assert "[[allow]]" not in out and "fingerprint =" not in out and 'rule = "secrets.hardcoded"' not in out


def test_the_agent_format_for_a_key_asks_the_human_and_shows_no_allow_route(repo):
    stage_key(repo)
    out = repo.run("check", "--offline", "--format", "agent", env=AGENT).stdout
    assert out.splitlines()[0].startswith("AIGATE verdict=block")
    assert ASK in out and "[[allow]]" not in out and "lite.toml:" not in out


def test_an_agent_next_line_for_a_non_secret_block_asks_the_human(repo):
    repo.write("auth/a.py", NOSEC)               # a suppress.security BLOCK (sensitive path)
    repo.add()
    out = repo.run("check", "--offline", "--color", "never", env=AGENT).stdout
    nxt = [ln for ln in out.splitlines() if ln.startswith("next:")]
    assert nxt and ASK in nxt[0] and "[[allow]]" not in nxt[0], nxt


def test_the_person_text_is_unchanged(repo):
    stage_key(repo)
    out = repo.run("check", "--offline", "--color", "never").stdout
    assert "[[allow]]" in out and 'rule = "secrets.hardcoded"' in out and ASK not in out
    repo2 = repo
    repo2.write("auth/a.py", NOSEC)
    repo2.add()
    person = repo2.run("check", "--offline", "--color", "never").stdout
    nxt = [ln for ln in person.splitlines() if ln.startswith("next:")]
    assert nxt and "[[allow]]" in nxt[0] and ASK not in nxt[0], nxt


def test_the_finding_itself_is_still_a_block_for_an_agent(repo):
    stage_key(repo)
    res = repo.check("--offline", env=AGENT)
    assert [f for f in find(res, "secrets.hardcoded") if not f["allowed"] and f["action"] == "block"]
