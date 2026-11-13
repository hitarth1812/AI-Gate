"""security.weak_crypto must see secret words inside camelCase identifiers (`sessionToken`, `newToken`), where a plain
\\b never matches, without starting to fire on words that merely contain one (`tokenizer`, `passwordless`)."""
import pytest

from conftest import find

AGENT = {"AI_AGENT": "some-agent"}


def stage(repo, files):
    for path, text in files.items():
        repo.write(path, text)
    repo.add()


def hit_lines(res):
    return [f["line"] for f in find(res, "security.weak_crypto") if not f["allowed"]]


@pytest.mark.parametrize("env", [None, AGENT], ids=["person", "agent"])
@pytest.mark.parametrize("path,text", [
    ("src/auth.ts", "export const newToken = () => Math.random().toString(36).slice(2);\n"),
    ("src/auth.ts", "export const sessionToken = Math.random().toString(36);\n"),
    ("src/auth.js", "const authApiKey = Math.random().toString(16)\n"),
    ("src/auth.js", "const userSecret = Math.random()\n"),
    ("src/auth.py", "import random\nsessionToken = random.random()\n"),
    ("src/auth.py", "import random\nnewToken = lambda: random.randint(0, 10**9)\n"),
    ("src/auth.ts", "const h = createHash('sha1').update(userToken).digest('hex')\n"),
    ("src/auth.py", "import hashlib\nh = hashlib.md5(userPassword.encode()).hexdigest()\n"),
])
def test_camel_case_secret_names_with_weak_primitives_are_flagged(repo, env, path, text):
    stage(repo, {path: text})
    assert hit_lines(repo.check("--offline", env=env))


@pytest.mark.parametrize("env", [None, AGENT], ids=["person", "agent"])
@pytest.mark.parametrize("text", [
    "const tokenizer = Math.random()\n",
    "const passwordless = Math.random()\n",
    "const secretary = Math.random()\n",
    "const x = Math.random()\n",
    "const TOKENIZER = Math.random()\n",
    "const sessionless = Math.random()\n",
    "const saltine = Math.random()\n",
    "const h = createHash('sha1').update(tokenizer).digest('hex')\n",
])
def test_names_that_only_contain_a_secret_word_stay_quiet(repo, env, text):
    stage(repo, {"src/auth.ts": text})
    assert not hit_lines(repo.check("--offline", env=env))


@pytest.mark.parametrize("env", [None, AGENT], ids=["person", "agent"])
def test_existing_plain_word_cases_still_fire(repo, env):
    stage(repo, {"src/a.js": "const token = Math.random()\nconst password_x = 1\n",
                 "src/b.py": "import hashlib\nh = hashlib.sha1(password)\n"})
    res = repo.check("--offline", env=env)
    assert {(f["path"], f["line"]) for f in find(res, "security.weak_crypto")} >= {("src/a.js", 1), ("src/b.py", 2)}
