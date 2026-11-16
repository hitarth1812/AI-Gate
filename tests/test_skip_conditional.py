"""Part F2 of the command-level defects: tests.skip_added. A `skipif` with a platform or privilege condition (sys.platform, os.name,
os.geteuid, shutil.which) is a WARN for a person with the conditional-skip hint, and an AI agent's result is unchanged (FIX).
Most of these shapes already behaved so; they are pinned here as user flows (a staged test file, a person and an agent). The one new
behaviour is unittest.skipUnless, the sibling of skipIf. Unconditional skips and xfail stay FIX for everyone."""
import pytest

AGENT = {"CLAUDECODE": "1"}
HINT = "make CI fail when this skip fires"
FIX_HINT = "Fix the code instead of skipping"


def verdicts(repo, text, path="tests/test_m.py"):
    """(person actions, agent actions, person hints) of the tests.skip_added findings for the staged file."""
    repo.write(path, text)
    repo.add()
    out = []
    for env in (None, AGENT):
        res = repo.check("--offline", env=env)
        out.append([f for f in res["findings"] if f["rule"] == "tests.skip_added" and not f["allowed"]])
    person, agent = out
    return [f["action"] for f in person], [f["action"] for f in agent], [f["hint"] or "" for f in person]


CONDITIONS = {
    "sys.platform": ("import sys\n", 'sys.platform == "win32"'),
    "os.name": ("import os\n", 'os.name == "nt"'),
    "os.geteuid": ("import os\n", "os.geteuid() == 0"),
    "shutil.which": ("import shutil\n", 'shutil.which("git") is None'),
}


@pytest.mark.parametrize("name", sorted(CONDITIONS))
def test_a_pytest_skipif_on_a_platform_or_privilege_is_a_person_warning_and_an_agent_fix(repo, name):
    imp, cond = CONDITIONS[name]
    text = f'{imp}import pytest\n\n\n@pytest.mark.skipif({cond}, reason="not here")\ndef test_a():\n    assert 1\n'
    person, agent, hints = verdicts(repo, text)
    assert person == ["warn"] and agent == ["fix"]
    assert HINT in hints[0]


def test_the_mark_can_sit_in_an_alias_or_pytestmark(repo):
    alias = 'import os\nimport pytest\n\nposix = pytest.mark.skipif(os.name == "nt", reason="posix only")\n\n\n@posix\ndef test_a():\n    assert 1\n'
    assert verdicts(repo, alias)[:2] == (["warn"], ["fix"])
    module = 'import sys\nimport pytest\n\npytestmark = pytest.mark.skipif(sys.platform == "win32", reason="x")\n\n\ndef test_a():\n    assert 1\n'
    assert verdicts(repo, module)[:2] == (["warn"], ["fix"])


def test_two_stacked_conditional_skips_are_still_a_warning(repo):
    text = ('import os\nimport sys\nimport pytest\n\n\n@pytest.mark.skipif(sys.platform == "win32", reason="x")\n'
            '@pytest.mark.skipif(os.geteuid() == 0, reason="y")\ndef test_a():\n    assert 1\n')
    assert verdicts(repo, text)[:2] == (["warn"], ["fix"])


def test_a_conditional_skip_added_to_an_existing_test_is_a_warning_too(repo):
    repo.write("tests/test_m.py", "def test_a():\n    assert 1\n")
    repo.commit("a test")
    text = 'import os\nimport pytest\n\n\n@pytest.mark.skipif(os.geteuid() == 0, reason="root")\ndef test_a():\n    assert 1\n'
    assert verdicts(repo, text)[:2] == (["warn"], ["fix"])


def test_unittest_skipif_is_a_person_warning(repo):
    text = 'import sys\nimport unittest\n\n\nclass T(unittest.TestCase):\n    @unittest.skipIf(sys.platform == "win32", "posix only")\n    def test_a(self):\n        self.assertTrue(1)\n'
    person, agent, hints = verdicts(repo, text)
    assert (person, agent) == (["warn"], ["fix"]) and HINT in hints[0]


def test_unittest_skipunless_is_the_same_conditional_skip(repo):
    text = ('import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n    @unittest.skipUnless(hasattr(os, "geteuid"), "posix only")\n'
            '    def test_a(self):\n        self.assertTrue(1)\n')
    person, agent, hints = verdicts(repo, text)
    assert (person, agent) == (["warn"], ["fix"]) and HINT in hints[0]


def test_skipunless_added_to_an_existing_test_is_a_warning_too(repo):
    repo.write("tests/test_m.py", "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(1)\n")
    repo.commit("a test")
    text = ("import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n    @unittest.skipUnless(hasattr(os, 'geteuid'), 'posix')\n"
            "    def test_a(self):\n        self.assertTrue(1)\n")
    assert verdicts(repo, text)[:2] == (["warn"], ["fix"])


@pytest.mark.parametrize("decorator", ['@pytest.mark.skip(reason="flaky")', '@pytest.mark.xfail(reason="bug")',
                                       '@unittest.expectedFailure'])
def test_unconditional_skips_and_xfail_stay_a_fix_for_everyone(repo, decorator):
    text = f"import pytest\nimport unittest\n\n\n{decorator}\ndef test_a():\n    assert 1\n"
    person, agent, hints = verdicts(repo, text)
    assert person == ["fix"] and agent == ["fix"] and FIX_HINT in hints[0]


def test_a_conditional_skip_next_to_an_unconditional_one_stays_a_fix(repo):
    text = ('import sys\nimport pytest\n\n\n@pytest.mark.skip(reason="flaky")\n@pytest.mark.skipif(sys.platform == "win32", reason="x")\n'
            'def test_a():\n    assert 1\n')
    assert verdicts(repo, text)[:2] == (["fix"], ["fix"])


def test_the_agent_result_is_identical_with_and_without_the_new_softening(repo):
    unless = ('import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n    @unittest.skipUnless(hasattr(os, "geteuid"), "posix")\n'
              '    def test_a(self):\n        self.assertTrue(1)\n')
    repo.write("tests/test_m.py", unless)
    repo.add()
    res = repo.check("--offline", env=AGENT)
    rows = [f for f in res["findings"] if f["rule"] == "tests.skip_added"]
    assert [f["action"] for f in rows] == ["fix"] and res["verdict"] in ("fix", "block")
