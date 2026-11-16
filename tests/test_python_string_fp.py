"""Part F1 of the command-level defects: on Python source, code.todo, security.weak_crypto, suppress.lint, suppress.type and
secrets.generic must not fire on text INSIDE a string literal (and weak_crypto not on a comment line). This is a parsing fix, not a
softening: a person and an AI agent get the same answer. A provider-shaped key in a string still fires. Every test runs the CLI in a
temporary repository (a staged change) and compares a person with an agent."""
import pytest

from conftest import aigate, find

AGENT = {"CLAUDECODE": "1"}
TODO, FIXME, NOQA, TYPE_IGNORE = "TO" + "DO", "FIX" + "ME", "no" + "qa", "type: " + "ignore"
PRAGMA, PYLINT = "pragma: no " + "cover", "pylint: " + "disable"       # spelled in pieces: this file is not itself a finding
MD5, SHA1 = "md" + "5", "sha" + "1"
RULES = ("code.todo", "security.weak_crypto", "suppress.lint", "suppress.type", "secrets.generic", "secrets.hardcoded")


def hits(repo, path, text):
    """{rule: [lines]} for the stated rules, for a person and for an agent (they must be equal)."""
    repo.write(path, text)
    repo.add()
    out = []
    for env in (None, AGENT):
        res = repo.check("--offline", env=env)
        out.append({r: sorted(f["line"] for f in find(res, r) if not f["allowed"]) for r in RULES if find(res, r)})
    assert out[0] == out[1], out
    return out[0]


# ----------------------------------------------------------------------------------------------- text in a string: no finding

@pytest.mark.parametrize("name,text", [
    ("assignment", f'CHECKLIST = "# {TODO} update the changelog"\n'),
    ("triple-quoted", f"SNIPPET = '''\ndef stub():\n    # {TODO} fill in\n    # {FIXME} and this\n    pass\n'''\n"),
    ("docstring", f'def f():\n    """Notes.\n\n        # {TODO}: handle retries\n    """\n    return 1\n'),
    ("raw", f'RAW = r"# {TODO} raw string"\n'),
    ("bytes", f'B = b"# {FIXME} bytes"\n'),
    ("f-string", f'name = "x"\nT = f"# {TODO} {{name}}"\n'),
    ("rb-prefix", f'X = rb"# {TODO} raw bytes"\n'),
    ("continued", f'MSG = "part one # {TODO} not a comment \\\npart two"\n'),
    ("two-strings-one-line", f'A, B = "x", "# {FIXME} inside the second"\n'),
    ("string-with-hash-then-real-code", 'LABEL = "a # b"\nclass K:\n    pass\n'),
])
def test_a_todo_inside_a_string_is_not_a_todo(repo, name, text):
    assert hits(repo, "src/m.py", text) == {}


@pytest.mark.parametrize("text", [
    f'HELP = "tip: hashlib.{MD5}(password) is weak; use sha256 for a password"\n',
    f'def doc():\n    """Do not call hashlib.{SHA1}(token) on a token."""\n    return 1\n',
    f'X = f"hash with hashlib.{MD5} the password {{1}}"\n',
    f"S = '''\nimport hashlib\nhashlib.{MD5}(password)\n'''\n",
])
def test_weak_crypto_words_inside_a_string_are_not_weak_crypto(repo, text):
    assert hits(repo, "src/m.py", text) == {}


@pytest.mark.parametrize("text", [
    f"import hashlib\n\n# hashlib.{MD5}(password.encode()) was removed in 2023\n\n\ndef d(b):\n    return hashlib.sha256(b).hexdigest()\n",
    f"import hashlib\n\ndef d(password):\n    return hashlib.sha256(password).hexdigest()  # was {MD5}(password) before\n",
    "# random.random() for a token was replaced\nx = 1\n",
])
def test_weak_crypto_does_not_fire_on_a_comment(repo, text):
    assert hits(repo, "src/m.py", text) == {}


@pytest.mark.parametrize("text", [
    f'RULE = "E501  # {NOQA}: E501 shows up in the docs"\n',
    f"T = '''\nimport os  # {NOQA}\n'''\n",
    f'D = """\nsee # {PYLINT}=unused-import\n"""\n',
    f'EXAMPLE = "value = compute()  # {TYPE_IGNORE}[arg-type]"\n',
    f'def note():\n    """Write `x = f()  # {TYPE_IGNORE}` only with a reason."""\n    return 1\n',
    f'P = "# {PRAGMA}"\n',
])
def test_a_suppression_marker_inside_a_string_is_not_a_suppression(repo, text):
    assert hits(repo, "src/m.py", text) == {}


@pytest.mark.parametrize("text", [
    "TEMPLATE = 'password = \"hunter2hunter2\"\\napi_token = \"Zx9kQ2mT7vRw\"\\n'\n",
    'SQL = """\ndb_password = \'Sup3rS3cretValue\'\n"""\n',
    'def explain():\n    """Set it like this:\n\n        API_SECRET = "abcdefgh12345678"\n    """\n    return 1\n',
    "repo_write('a.py', 'password = \"Zx9kQ2mT7vRw\"')\n",
    "LINE = f\"secret = '{1}Zx9kQ2mT7vRw'\"\n",
])
def test_an_assignment_written_inside_a_string_is_not_a_credential(repo, text):
    assert hits(repo, "src/m.py", text) == {}


# --------------------------------------------------------------------------------- the same words in real code still fire

def test_a_todo_comment_still_fires_even_after_a_string_with_a_hash(repo):
    got = hits(repo, "src/m.py", f'def pay(o):\n    t = o.total  # {TODO} apply the discount\n    l = "a # b"  # {FIXME} fix\n    return t, l\n')
    assert got == {"code.todo": [2, 3]}


def test_weak_crypto_in_code_still_fires(repo):
    got = hits(repo, "src/m.py", f"import hashlib\n\n\ndef s(password):\n    return hashlib.{MD5}(password.encode()).hexdigest()\n")
    assert got == {"security.weak_crypto": [5]}


def test_hashlib_new_with_the_algorithm_as_a_string_still_fires(repo):
    got = hits(repo, "src/m.py", f'import hashlib\n\n\ndef s(password):\n    return hashlib.new("{MD5}", password.encode()).hexdigest()\n')
    assert got == {"security.weak_crypto": [5]}


def test_the_algorithm_only_in_a_string_of_some_other_call_does_not_fire(repo):
    assert hits(repo, "src/m.py", f'def s(password):\n    return log("hashing with {MD5}", password)\n') == {}


def test_a_weak_random_call_with_the_secret_word_in_a_string_key_still_fires(repo):
    got = hits(repo, "src/m.py", 'import random\n\n\ndef make(d):\n    d["token"] = random.randint(0, 99999999)\n')
    assert got == {"security.weak_crypto": [5]}


def test_weak_random_inside_a_string_is_not_weak_crypto(repo):
    assert hits(repo, "src/m.py", 'NOTE = "never use random.randint(1, 9) for a token"\n') == {}


def test_suppressions_in_comments_still_fire_even_after_a_string_with_a_hash(repo):
    got = hits(repo, "src/m.py", f'import os  # {NOQA}\nvalue = 1  # {TYPE_IGNORE}\nmsg = "see # docs"  # {NOQA}: E501\n')
    assert got == {"suppress.lint": [1, 3], "suppress.type": [2]}


def test_a_provider_key_inside_a_string_still_blocks(repo):
    key = "AKIA" + "qwertyuiopasdfgh".upper()                   # split so this file is not itself a finding
    got = hits(repo, "src/m.py", f'EXAMPLE_ENV = "AWS_ACCESS_KEY_ID={key}"\n')
    assert got == {"secrets.hardcoded": [1]}


@pytest.mark.parametrize("text,line", [
    ('db_password = "Kq7mW2xZ9pLr"\n', 1),
    ('CONFIG = {"password": "Zx9kQ2mT7vRw"}\n', 1),
    ('import os\nos.environ["API_SECRET"] = "Qw3rTy9uIoP2"\n', 2),
    ('label = "x"; api_secret = "Zx9kQ2mT7vRw"\n', 1),
    ("class C:\n    secret_key = 'Zx9kQ2mT7vRw'\n", 2),
])
def test_a_credential_assigned_in_code_still_fires(repo, text, line):
    assert hits(repo, "src/m.py", text) == {"secrets.generic": [line]}


def test_a_connection_string_with_a_password_in_a_string_still_fires(repo):
    scheme, shh = "post" + "gres", "Zq9xLm2Rt7"                  # no URL-shaped literal in this file; the staged file has one
    got = hits(repo, "src/m.py", f'DATABASE_URL = "{scheme}://app:{shh}@db.internal/app"\n')
    assert got == {"secrets.generic": [1]}


# ----------------------------------------------------------------------- a file the tokenizer cannot read: old behaviour

def test_an_untokenizable_file_keeps_the_line_based_checks(repo):
    got = hits(repo, "src/broken.py", f'def f():\n    x = 1  # {TODO} keep working\n    return """never closed\n')
    assert got.get("code.todo") == [2]


def test_a_stray_character_is_a_fallback_not_a_crash(repo):
    got = hits(repo, "src/weird.py", f"x = 1  # {TODO} fine\ny = $\n")
    assert got.get("code.todo") == [1]


# --------------------------------------------------------------------------------------------------------- the view itself

def kinds_of(text):
    lx = aigate.python_lex(text)
    assert lx is not None
    return lx


def test_the_view_classifies_code_strings_and_comments():
    lx = kinds_of('x = "a # b"  # c\ny = 1\n')
    def k(col):
        return lx.kind_at(1, col)
    assert k(0) == aigate.CODE and k(4) == aigate.STR and k(7) == aigate.STR and k(11) == aigate.CODE and k(13) == aigate.COM
    assert lx.kind_at(2, 0) == aigate.CODE


def test_triple_quoted_and_prefixed_strings_span_lines():
    lx = kinds_of('a = r"""\nx # y\n"""\nb = 1\n')
    assert lx.kind_at(2, 2) == aigate.STR and lx.kind_at(4, 0) == aigate.CODE


def test_an_fstring_is_one_string_on_every_python():
    lx = kinds_of('n = 1\nt = f"v {n} # not a comment"\nu = 2  # real\n')
    assert lx.kind_at(2, 5) == aigate.STR and lx.kind_at(2, 10) == aigate.STR and lx.kind_at(2, 16) == aigate.STR
    assert lx.kind_at(3, 8) == aigate.COM


def test_an_unterminated_string_gives_no_view():
    assert aigate.python_lex('x = """never closed\n') is None              # TokenError on every supported Python
    assert aigate.python_lex('x = "abc\ny = 1\n') is None                  # ERRORTOKEN on 3.11, TokenError on 3.12+


def test_crlf_and_a_missing_final_newline_keep_the_columns():
    lx = kinds_of('x = "a"  # c\r\ny = "b"  # d')
    assert lx.kind_at(1, 5) == aigate.STR and lx.kind_at(1, 9) == aigate.COM and lx.kind_at(2, 5) == aigate.STR
    assert lx.kind_at(2, 10) == aigate.COM


def test_py_weak_equals_weak_crypto_on_lines_without_strings_or_comments():
    lines = [f"digest = hashlib.{MD5}(password)", f"h = {SHA1}(token)", "t = random.randint(1, 9) + session_id",
             "x = random.choice(a) + 1", "x = hashlib.sha256(password)", "k = Math.random() * api_key", "n = 1 + 2",
             f"{MD5} = password", "otp = random.random()", f"salt = {SHA1}"]
    for text in lines:
        lx = aigate.python_lex(text + "\n")
        assert bool(aigate.WEAK_CRYPTO.search(text)) == bool(aigate._py_weak(lx, 1, text)), text
