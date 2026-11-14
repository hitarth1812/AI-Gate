"""0.5.2 round 1 (A1, decided by the human): a justified `//nolint:gosec` is FIX for a person; a bare or unjustified one stays BLOCK.
The agent keeps BLOCK in every case. A justification is a reason of at least 4 words that is not just "false positive", "ok",
"ignore" or "intentional". The directive still produces a finding (weak_crypto is separate and unaffected)."""
import pytest

from conftest import find

SHA1_LINE = "sum := sha1.Sum([]byte(password)) //nolint:gosec {reason}"


def go_file(reason, path="internal/auth/credentials/screener.go"):
    body = "package credentials\n\nimport \"crypto/sha1\"\n\nfunc Check(password string) [20]byte {\n\t"
    body += SHA1_LINE.format(reason=reason).replace("//nolint:gosec  ", "//nolint:gosec ") + "\n\treturn sum\n}\n"
    return path, body


def suppression(res):
    return [f for f in find(res, "suppress.security") if not f["allowed"]]


@pytest.mark.parametrize("reason", [
    "// SHA-1 is the breach-check protocol, not a security control here",
    "// the breach-check API mandates SHA-1 for k-anonymity",
], ids=["long-reason-1", "long-reason-2"])
def test_a_justified_nolint_gosec_is_a_fix_for_a_person_and_a_block_for_an_agent(repo, reason):
    path, body = go_file(reason)
    repo.write(path, body)
    repo.add()
    person = suppression(repo.check("--offline"))
    agent = suppression(repo.check("--offline", env={"CLAUDECODE": "1"}))
    assert person and person[0]["action"] == "fix", person
    assert agent and agent[0]["action"] == "block", agent


@pytest.mark.parametrize("reason", ["", "// ok", "// false positive", "// ignore", "// intentional", "// see ticket"], ids=[
    "bare", "ok", "false-positive", "ignore", "intentional", "three-words-or-fewer"])
def test_a_bare_or_unjustified_nolint_gosec_stays_a_block_for_everyone(repo, reason):
    path, body = go_file(reason)
    repo.write(path, body)
    repo.add()
    person = suppression(repo.check("--offline"))
    assert person and person[0]["action"] == "block", person


def test_the_justified_form_outside_a_sensitive_path_is_also_a_fix_for_a_person(repo):
    path, body = go_file("// SHA-1 is the breach-check protocol, not a security control here", path="internal/util/hash.go")
    repo.write(path, body)
    repo.add()
    person = suppression(repo.check("--offline"))
    assert person and person[0]["action"] == "fix", person


def test_the_directive_is_still_reported_for_a_person(repo):
    path, body = go_file("// SHA-1 is the breach-check protocol, not a security control here")
    repo.write(path, body)
    repo.add()
    assert suppression(repo.check("--offline"))         # never silently dropped
