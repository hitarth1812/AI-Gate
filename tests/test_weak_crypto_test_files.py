"""SHA-1/MD5 of a test input inside a test file is a warning for a person, even under a sensitive path (auth/).
An AI agent's result is unchanged, and no other rule loses its sensitive-path escalation."""
import pytest

from conftest import find

GO_SHA1 = ('package x\nimport "crypto/sha1"\nimport "testing"\n'
           'func TestSum(t *testing.T) {\n\tsum := sha1.Sum([]byte(password))\n\t_ = sum\n}\n')
GO_MD5 = GO_SHA1.replace("sha1", "md5")
PY_SHA1 = "import hashlib\n\n\ndef test_digest():\n    digest = hashlib.sha1(password.encode()).hexdigest()\n    assert digest\n"
PY_MD5 = PY_SHA1.replace("sha1", "md5")
GO_SQL = ('package x\nimport "testing"\n'
          'func TestDrop(t *testing.T) {\n\tdb.Exec("DELETE FROM " + table)\n}\n')
AGENT = {"CLAUDECODE": "1"}


def weak(repo, path, text, env=None):
    repo.write(path, text)
    repo.add()
    found = [f for f in find(repo.check(env=env), "security.weak_crypto") if f["path"] == path]
    assert len(found) == 1, found
    return found[0]["action"]


CASES = [
    ("internal/auth/credentials/screener_test.go", GO_SHA1),
    ("internal/auth/credentials/screener_test.go", GO_MD5),
    ("pkg/screener_test.go", GO_SHA1),
    ("auth/test_digest.py", PY_SHA1),
    ("auth/test_digest.py", PY_MD5),
    ("tests/test_digest.py", PY_SHA1),
]


@pytest.mark.parametrize("path,text", CASES)
def test_person_gets_warn_in_test_files(repo, path, text):
    assert weak(repo, path, text) == "warn"


@pytest.mark.parametrize("path,text", CASES)
def test_agent_result_unchanged(repo, path, text):
    expected = "fix" if "auth/" in path else "warn"           # sensitive-path escalation stays for an agent
    assert weak(repo, path, text, AGENT) == expected


def test_non_test_code_under_auth_still_escalates_for_a_person(repo):
    assert weak(repo, "internal/auth/screener.go", GO_SHA1) == "fix"
    assert weak(repo, "auth/digest.py", PY_SHA1) == "fix"


def test_non_test_code_outside_sensitive_path_is_warn(repo):
    assert weak(repo, "pkg/screener.go", GO_SHA1) == "warn"


def test_other_rules_still_escalate_in_test_files_under_auth(repo):
    repo.write("internal/auth/drop_test.go", GO_SQL)
    repo.add()
    res = repo.check()
    sql = find(res, "security.sql_injection")
    assert sql and sql[0]["action"] == "fix", sql                 # person: warn -> fix by design under auth/
    res = repo.check(env=AGENT)
    assert find(res, "security.sql_injection")[0]["action"] == "block"
