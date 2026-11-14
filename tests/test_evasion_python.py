"""Round 2: Python evasions the tool used to miss (a key split over a string operation, a skip through an alias,
eval through the builtins mapping). Each is a detection gain for a person and an AI agent alike."""
import pytest

from conftest import find

TAIL = "qwertyuiopasdfgh".upper()          # the tail of a fake AWS key id, built so this file holds no key
AGENT = {"CLAUDECODE": "1"}
WHO = [pytest.param(None, id="person"), pytest.param(AGENT, id="agent")]


def staged(repo, rel, text):
    repo.write(rel, text)
    repo.add()


def lines(res, rule):
    return sorted((f["line"], f["action"]) for f in find(res, rule))


# ---------------------------------------------------------------- a provider key split over a constant operation

KEY_SPLITS = {
    "concat": "KEY = 'AKIA' + 'QWERTYUIOPASDFGH'\n",
    "join": "KEY = ''.join(['AKIA', 'QWERTY', 'UIOPASDFGH'])\n",
    "join_tuple": "KEY = ''.join(('AKIA', 'QWERTYUIOPASDFGH'))\n",
    "fstring": "KEY = f'AKIA' + f'QWERTY{\"UIOPASDFGH\"}'\n",
    "chain_in_call": "client = Client(key='AKIA' + 'QWERTY' + 'UIOPASDFGH')\n",
    "return": "def f():\n    return 'AKIA' + 'QWERTYUIOPASDFGH'\n",
    "adjacent_lines": "KEY = ('AKIA'\n       'QWERTYUIOPASDFGH')\n",
}


@pytest.mark.parametrize("env", WHO)
def test_adv_key_split_concat(repo, env):
    staged(repo, "app.py", KEY_SPLITS["concat"])
    hits = lines(repo.check(env=env), "secrets.hardcoded")
    assert hits == [(1, "block")]


@pytest.mark.parametrize("env", WHO)
def test_adv_key_split_join(repo, env):
    staged(repo, "app.py", KEY_SPLITS["join"])
    hits = lines(repo.check(env=env), "secrets.hardcoded")
    assert hits == [(1, "block")]


@pytest.mark.parametrize("env", WHO)
@pytest.mark.parametrize("name", ["join_tuple", "fstring", "chain_in_call", "adjacent_lines"])
def test_key_split_other_shapes(repo, env, name):
    staged(repo, "app.py", KEY_SPLITS[name])
    assert lines(repo.check(env=env), "secrets.hardcoded") == [(1, "block")]


def test_key_split_inside_a_function_is_reported_at_its_statement(repo):
    staged(repo, "app.py", KEY_SPLITS["return"])
    assert lines(repo.check(), "secrets.hardcoded") == [(2, "block")]


def test_key_on_one_line_and_split_key_each_at_its_own_line(repo):
    staged(repo, "app.py", f"KEY = 'AKIA{TAIL}'\nOTHER = 'AKIA' + '{TAIL}'\n")
    assert [l for l, _ in lines(repo.check(), "secrets.hardcoded")] == [1, 2]


def test_adjacent_literals_on_one_line_and_a_whole_key_are_reported_once_per_line(repo):
    staged(repo, "app.py", f"KEY = 'AKIA{TAIL}' 'x'\nOTHER = 'AKIA' '{TAIL}'\n")
    assert [l for l, _ in lines(repo.check(), "secrets.hardcoded")] == [1, 2]


@pytest.mark.parametrize("env", WHO)
@pytest.mark.parametrize("src", [
    "NAME = 'hello' + ' ' + 'world'\n",                                   # ordinary words
    "KEY = 'AKIA' + 'short'\n",                                           # too short for a key
    "SEP = ', '.join(['alpha', 'beta', 'gamma'])\n",                      # unrelated strings
    "PATH = '/usr/' + 'local/' + 'bin'\n",
    "KEY = 'AKIA' + name + 'QWERTYUIOPASDFGH'\n",                         # a variable in the middle: not constant
    "KEY = ''.join([prefix, 'QWERTYUIOPASDFGH'])\n",
    "MSG = 'AKIA is the prefix of an AWS key id; ' + 'see the docs'\n",
    "KEY = 'AKIA' + 'EXAMPLEEXAMPLEEX'\n",                              # the documentation placeholder rule still applies
])
def test_key_split_negatives(repo, env, src):
    staged(repo, "app.py", src)
    assert find(repo.check(env=env), "secrets.hardcoded") == []


# ---------------------------------------------------------------- a skip through an alias

@pytest.mark.parametrize("env", WHO)
def test_adv_skip_alias_import(repo, env):
    staged(repo, "tests/test_a.py", "from pytest import mark as m\n\n\n@m.skip\ndef test_a():\n    assert 1 == 1\n")
    assert lines(repo.check(env=env), "tests.skip_added") == [(4, "fix")]


@pytest.mark.parametrize("env", WHO)
def test_adv_skip_alias_var(repo, env):
    staged(repo, "tests/test_a.py",
           "import pytest\n\nlater = pytest.mark.skip\n\n\n@later\ndef test_a():\n    assert 1 == 1\n")
    assert lines(repo.check(env=env), "tests.skip_added") == [(3, "fix")]


@pytest.mark.parametrize("env", WHO)
def test_skip_alias_var_with_arguments_and_xfail(repo, env):
    staged(repo, "tests/test_a.py",
           "import pytest\n\nlater = pytest.mark.xfail(reason='x')\n\n\n@later\ndef test_a():\n    assert 1 == 1\n")
    assert lines(repo.check(env=env), "tests.skip_added") == [(3, "fix")]


@pytest.mark.parametrize("env", WHO)
def test_skip_alias_from_import_without_as(repo, env):
    staged(repo, "tests/test_a.py", "from pytest import mark\n\n\n@mark.skipif(True, reason='x')\ndef test_a():\n    assert 1 == 1\n")
    # a conditional skip is a warning for a person, as for the dotted spelling; an agent keeps the fix
    assert [a for _, a in lines(repo.check(env=env), "tests.skip_added")] == ["warn" if env is None else "fix"]


@pytest.mark.parametrize("env", WHO)
def test_skip_alias_negatives(repo, env):
    staged(repo, "tests/test_a.py",
           "from pytest import mark as m\nimport pytest\n\nother = something_else\nslow = pytest.mark.slow\n\n\n"
           "@m.foo\n@slow\ndef test_a():\n    assert 1 == 1\n\n\n@other\ndef test_b():\n    assert 2 == 2\n")
    assert find(repo.check(env=env), "tests.skip_added") == []


def test_plain_skip_keeps_its_line(repo):
    staged(repo, "tests/test_a.py", "import pytest\n\n\n@pytest.mark.skip\ndef test_a():\n    assert 1 == 1\n")
    assert lines(repo.check(), "tests.skip_added") == [(5, "fix")]


# ---------------------------------------------------------------- eval through the builtins mapping

EVALS = {
    "subscript": "def f(s):\n    return __builtins__['eval'](s)\n",
    "exec_subscript": "def f(s):\n    return __builtins__['exec'](s)\n",
    "builtins_attr": "import builtins\n\n\ndef f(s):\n    return builtins.eval(s)\n",
    "getattr": "import builtins\n\n\ndef f(s):\n    return getattr(builtins, 'eval')(s)\n",
}


@pytest.mark.parametrize("env", WHO)
def test_adv_eval_via_builtins(repo, env):
    staged(repo, "app.py", EVALS["subscript"])
    assert lines(repo.check(env=env), "security.eval") == [(2, "fix")]


@pytest.mark.parametrize("env", WHO)
@pytest.mark.parametrize("name,line", [("exec_subscript", 2), ("builtins_attr", 5), ("getattr", 5)])
def test_eval_other_shapes(repo, env, name, line):
    staged(repo, "app.py", EVALS[name])
    assert lines(repo.check(env=env), "security.eval") == [(line, "fix")]


@pytest.mark.parametrize("env", WHO)
def test_eval_negatives(repo, env):
    staged(repo, "app.py",
           "import builtins\n\n\ndef f(d, s):\n    a = d['eval'](s)\n    b = __builtins__['len'](s)\n"
           "    c = getattr(d, 'eval')(s)\n    return a, b, c, builtins.len(s)\n")
    assert find(repo.check(env=env), "security.eval") == []
