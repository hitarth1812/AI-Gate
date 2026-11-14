"""0.5.2 round 1 (A5, decided by the human): a private key stays a BLOCK. The BLOCK prints a ready [[allow]] snippet (path, fingerprint,
reason to fill in) and an allow entry works for secrets.hardcoded. One key file gives one BLOCK: no second secrets.env_file."""
import re

from conftest import find

PEM = (
    "-----BEGIN " + "RSA PRIVATE KEY-----\n"
    + "\n".join(["MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu"] * 4) + "\n"
    + "-----END " + "RSA PRIVATE KEY-----\n"
)


def stage_key(repo, path="keys/server.pem"):
    repo.write(path, PEM)
    repo.add()


def active(res, rule):
    return [f for f in find(res, rule) if not f["allowed"]]


def test_a_private_key_file_is_one_block_not_two(repo):
    stage_key(repo)
    res = repo.check("--offline")
    assert active(res, "secrets.hardcoded"), res["findings"]
    assert not active(res, "secrets.env_file"), "one key file must give one BLOCK"


def test_the_block_prints_a_ready_allow_snippet_with_the_fingerprint_and_no_secret(repo):
    stage_key(repo)
    res = repo.check("--offline")
    key = active(res, "secrets.hardcoded")[0]
    out = repo.run("check", "--offline", "--color", "never").stdout
    assert "[[allow]]" in out and 'rule = "secrets.hardcoded"' in out
    assert 'path = "keys/server.pem"' in out and f'fingerprint = "{key["fingerprint"]}"' in out
    assert "reason = " in out
    assert not re.search(r"BEGIN RSA PRIVATE KEY-----\n[A-Za-z0-9+/=]{20,}", out)     # the key body is never printed


def test_an_allow_entry_for_the_key_removes_the_block(repo):
    stage_key(repo)
    key = active(repo.check("--offline"), "secrets.hardcoded")[0]
    repo.write(".aigate/lite.toml",
               "version = 1\n[[allow]]\nrule = \"secrets.hardcoded\"\npath = \"keys/server.pem\"\n"
               f"fingerprint = \"{key['fingerprint']}\"\nreason = \"test fixture: a throwaway key used by the TLS tests\"\n")
    repo.commit("allow the fixture key")
    res = repo.check("--offline")
    assert not active(res, "secrets.hardcoded"), res["findings"]


def test_a_key_in_a_test_fixture_directory_is_still_a_block(repo):
    stage_key(repo, path="tests/certs/server.key")
    assert active(repo.check("--offline"), "secrets.hardcoded")          # keeps BLOCK (A5 option a)
