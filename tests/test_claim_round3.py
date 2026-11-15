"""review.claim_mismatch, round 3: a message that says tests were added, an unused thing removed, an error handled or a
nil/None check fixed, when the diff plainly shows otherwise. Each claim kind has a closed vocabulary and fires only when
the diff contradicts it; every doubt stays silent. A person and an AI agent always get the same finding."""
import pytest

from test_review_api import AGENT, base, change
from test_review_rest import claims

GO_STORE = ('package store\n\nimport "os"\n\nfunc Load(p string) ([]byte, error) {\n\tb, err := os.ReadFile(p)\n'
            '\tif err != nil {\n\t\treturn nil, err\n\t}\n\treturn b, nil\n}\n')
GO_DESCRIBE = "package shop\n\ntype Item struct{ Name string }\n\nfunc Describe(p *Item) string {\n\treturn p.Name\n}\n"
PY_TESTS = {"app/__init__.py": "", "app/util.py": "def slugify(t):\n    return t\n",
            "tests/test_util.py": "from app.util import slugify\n\n\ndef test_slugify():\n    assert slugify('a') == 'a'\n"}
TS_LIB = "export const helper = (x: number) => x * 2;\n\nexport const legacy = (x: number) => x + 1;\n"
PY_STORE = "def load(path):\n    with open(path) as fh:\n        return fh.read()\n"


def both(repo, *messages):
    """The finding messages for a person and for an AI agent; asserted equal (the rule is a warn for both)."""
    person = claims(repo, *messages)
    agent = claims(repo, *messages, env=AGENT)
    assert person == agent
    assert all(action == "warn" for _m, action in person)
    return [m for m, _a in person]


# ---------------------------------------------------------------------------------------------- the five reproducers

def test_add_tests_when_the_changed_test_file_adds_no_test_is_reported(repo):
    base(repo, PY_TESTS)
    change(repo, {"tests/conftest.py": "import pytest\n\n\n@pytest.fixture\ndef items():\n    return [1, 2, 3]\n"})
    got = both(repo, "add tests for slugify")
    assert len(got) == 1 and "no test function or assertion was added" in got[0]


def test_fix_the_nil_check_in_a_function_that_gets_no_nil_check_is_reported(repo):
    base(repo, {"go.mod": "module example.com/s\n\ngo 1.22\n", "store/store.go": GO_STORE})
    change(repo, {"store/store.go": GO_STORE.replace("os.ReadFile(p)", "os.ReadFile(filepath.Clean(p))")})
    got = both(repo, "fix the nil check in Load")
    assert len(got) == 1 and "`Load`" in got[0]


def test_remove_the_unused_helper_when_the_diff_removes_nothing_is_reported(repo):
    base(repo, {"src/lib.ts": TS_LIB})
    change(repo, {"src/lib.ts": TS_LIB + "\nexport const triple = (x: number) => x * 3;\n"})
    got = both(repo, "remove the unused helper function")
    assert len(got) == 1 and "removes no line" in got[0]


def test_handle_the_error_when_no_error_handling_is_added_is_reported_in_python(repo):
    base(repo, {"app/__init__.py": "", "app/store.py": PY_STORE})
    change(repo, {"app/store.py": PY_STORE + "\n\ndef size(path):\n    import os\n    return os.path.getsize(path)\n"})
    got = both(repo, "handle the error when the file is missing")
    assert len(got) == 1 and "adds no error handling" in got[0]


def test_handle_the_error_when_the_error_is_discarded_is_reported_in_go(repo):
    base(repo, {"go.mod": "module example.com/s\n\ngo 1.22\n", "store/store.go": GO_STORE})
    change(repo, {"shop/read.go": 'package shop\n\nimport "os"\n\nfunc Read(p string) []byte {\n\tb, _ := os.ReadFile(p)\n\treturn b\n}\n'})
    got = both(repo, "handle the error from ReadFile")
    assert len(got) == 1 and "adds no error handling" in got[0]


def test_a_python_none_check_claim_on_a_function_without_one_is_reported(repo):
    base(repo, {"app/__init__.py": "", "app/store.py": PY_STORE})
    change(repo, {"app/store.py": PY_STORE.replace("open(path)", "open(str(path))")})
    got = both(repo, "fix the None check in load")
    assert len(got) == 1 and "`load`" in got[0]


def test_a_nil_check_claim_on_an_untouched_function_is_reported(repo):
    both_files = {"go.mod": "module example.com/s\n\ngo 1.22\n", "store/store.go": GO_STORE, "shop/d.go": GO_DESCRIBE}
    base(repo, both_files)
    change(repo, {"shop/d.go": GO_DESCRIBE + "\nfunc Other() int {\n\treturn 1\n}\n"})
    assert len(both(repo, "fix the nil check in Describe")) == 1


# ---------------------------------------------------------------------------------------------- true claims stay silent

NEGATIVES = {
    "py-tests-added": (
        PY_TESTS, {"tests/test_more.py": "def test_more():\n    assert 1 == 1\n"}, "add tests for slugify"),
    "py-assertion-added-to-existing-test": (
        PY_TESTS, {"tests/test_util.py": PY_TESTS["tests/test_util.py"] + "    assert slugify('b') == 'b'\n"},
        "added tests for slugify"),
    "py-unittest-method": (
        PY_TESTS, {"tests/test_cls.py": "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n"
                                        "        self.assertEqual(1, 1)\n"}, "Add unit tests"),
    "py-parametrize-row-only": (
        {**PY_TESTS, "tests/test_p.py": "import pytest\n\n\n@pytest.mark.parametrize('a,b', [\n    ('x', 1),\n])\n"
                                        "def test_p(a, b):\n    assert b\n"},
        {"tests/test_p.py": "import pytest\n\n\n@pytest.mark.parametrize('a,b', [\n    ('x', 1),\n    ('y', 2),\n])\n"
                            "def test_p(a, b):\n    assert b\n"}, "add tests for the parser"),
    "go-tests-added": (
        {"go.mod": "module m\n\ngo 1.22\n", "a/a.go": "package a\n\nfunc F() int { return 1 }\n"},
        {"a/a_test.go": 'package a\n\nimport "testing"\n\nfunc TestF(t *testing.T) {\n\tif F() != 1 {\n\t\tt.Fatal("x")\n\t}\n}\n'},
        "add tests for F"),
    "go-table-row-only": (
        {"go.mod": "module m\n\ngo 1.22\n", "a/a_test.go": 'package a\n\nimport "testing"\n\nfunc TestF(t *testing.T) {\n'
                                                           '\tcases := []struct{ in, want int }{\n\t\t{1, 1},\n\t}\n\t_ = cases\n}\n'},
        {"a/a_test.go": 'package a\n\nimport "testing"\n\nfunc TestF(t *testing.T) {\n'
                        '\tcases := []struct{ in, want int }{\n\t\t{1, 1},\n\t\t{2, 2},\n\t}\n\t_ = cases\n}\n'},
        "add tests for F"),
    "ts-tests-added": (
        {"src/a.ts": "export const f = () => 1;\n"},
        {"src/a.test.ts": "import { f } from './a';\n\ntest('f', () => {\n  expect(f()).toBe(1);\n});\n"},
        "add tests for f"),
    "update-tests-is-not-an-add-claim": (
        PY_TESTS, {"tests/conftest.py": "import pytest\n\n\n@pytest.fixture\ndef items():\n    return [1]\n"},
        "update the tests"),
    "tests-in-another-language-are-not-judged": (
        PY_TESTS, {"tests/spec.rs": "fn helper() {}\n"}, "add tests for slugify"),
    "py-unused-function-removed": (
        {"app/__init__.py": "", "app/util.py": "def keep(x):\n    return x\n\n\ndef legacy_format(x):\n    return str(x)\n"},
        {"app/util.py": "def keep(x):\n    return x\n"}, "remove the unused legacy_format helper"),
    "ts-unused-function-removed-plain-word": (
        {"src/lib.ts": TS_LIB}, {"src/lib.ts": "export const helper = (x: number) => x * 2;\n"},
        "remove the unused helper function"),
    "go-unused-function-removed": (
        {"go.mod": "module m\n\ngo 1.22\n", "a/a.go": "package a\n\nfunc Keep() int { return 1 }\n\nfunc OldThing() int {\n\treturn 2\n}\n"},
        {"a/a.go": "package a\n\nfunc Keep() int { return 1 }\n"}, "delete the unused `OldThing` function"),
    "unused-name-exists-only-elsewhere": (
        {"app/__init__.py": "", "app/other.py": "def old_thing():\n    return 1\n", "app/a.py": "x = 1\ny = 2\n"},
        {"app/a.py": "x = 1\n"}, "remove the unused old_thing"),
    "unused-import-removed": (
        {"app/a.py": "import os\nimport sys\n\nprint(sys.argv)\n"}, {"app/a.py": "import sys\n\nprint(sys.argv)\n"},
        "remove unused imports"),
    "py-error-handled-try": (
        {"app/__init__.py": "", "app/store.py": PY_STORE},
        {"app/store.py": "def load(path):\n    try:\n        with open(path) as fh:\n            return fh.read()\n"
                         "    except FileNotFoundError:\n        return None\n"}, "handle the error when the file is missing"),
    "py-error-handled-guard": (
        {"app/__init__.py": "", "app/store.py": PY_STORE},
        {"app/store.py": "import os\n\n\ndef load(path):\n    if not os.path.exists(path):\n        return ''\n"
                         "    with open(path) as fh:\n        return fh.read()\n"}, "handle errors"),
    "go-error-handled": (
        {"shop/read.go": 'package shop\n\nimport "os"\n\nfunc Read(p string) []byte {\n\tb, _ := os.ReadFile(p)\n\treturn b\n}\n'},
        {"shop/read.go": 'package shop\n\nimport "os"\n\nfunc Read(p string) ([]byte, error) {\n\tb, err := os.ReadFile(p)\n'
                         '\tif err != nil {\n\t\treturn nil, err\n\t}\n\treturn b, nil\n}\n'}, "handle the error from ReadFile"),
    "ts-error-handled": (
        {"src/api.ts": "export async function f(u: string) {\n  return fetch(u);\n}\n"},
        {"src/api.ts": "export async function f(u: string) {\n  return fetch(u).catch(() => null);\n}\n"},
        "handle fetch failures"),
    "error-handling-only-in-docs-or-tests": (
        {"README.md": "# x\n", "app/__init__.py": ""}, {"README.md": "# x\n\nErrors.\n"}, "handle the error"),
    "error-claim-with-an-unjudged-language-changed": (
        {"app/__init__.py": ""}, {"src/lib.rs": "fn f() {}\n", "app/a.py": "x = 1\n"}, "handle the error"),
    "error-claim-is-future-or-negated": (
        {"app/__init__.py": ""}, {"app/a.py": "x = 1\n"}, "does not handle the error yet; we should handle errors later"),
    "py-none-check-added": (
        {"app/__init__.py": "", "app/a.py": "def describe(p):\n    return p.name\n"},
        {"app/a.py": "def describe(p):\n    if p is None:\n        return ''\n    return p.name\n"},
        "fix the None check in describe"),
    "go-nil-check-added": (
        {"shop/d.go": GO_DESCRIBE},
        {"shop/d.go": GO_DESCRIBE.replace("\treturn p.Name", "\tif p == nil {\n\t\treturn \"\"\n\t}\n\treturn p.Name")},
        "fix the nil check in Describe"),
    "go-nil-check-line-changed": (
        {"store/store.go": GO_STORE},
        {"store/store.go": GO_STORE.replace("err != nil", "err != nil && b == nil")}, "fix the nil check in Load"),
    "nil-check-symbol-defined-elsewhere": (
        {"app/__init__.py": "", "app/o.py": "def other(p):\n    return p\n", "app/a.py": "x = 1\n"},
        {"app/a.py": "x = 2\n"}, "fix the None check in other"),
    "nil-check-in-js-is-not-judged": (
        {"src/a.ts": "export function load(p: string) {\n  return p;\n}\n"},
        {"src/a.ts": "export function load(p: string) {\n  return p.trim();\n}\n"}, "fix the null check in load"),
    "vague-cleanup": ({"app/a.py": "x = 1\n"}, {"app/a.py": "x = 2\n"}, "cleanup"),
    "vague-wip": ({"app/a.py": "x = 1\n"}, {"app/a.py": "x = 2\n"}, "wip"),
    "vague-update": ({"app/a.py": "x = 1\n"}, {"app/a.py": "x = 2\n"}, "update"),
    "vague-remove-unused": ({"app/a.py": "x = 1\ny = 2\n"}, {"app/a.py": "x = 1\n"}, "remove unused code"),
    "tests-claim-in-a-code-block": (
        PY_TESTS, {"tests/conftest.py": "X = 1\n"}, "Tidy\n\n```\nadd tests for slugify\n```\n"),
}


@pytest.mark.parametrize("name", sorted(NEGATIVES))
def test_a_true_or_unjudgeable_message_gets_no_claim_finding(repo, name):
    files, edit, message = NEGATIVES[name]
    base(repo, files)
    change(repo, edit)
    assert both(repo, message) == []


def test_a_deleted_file_counts_as_a_removal(repo):
    base(repo, {"app/__init__.py": "", "app/old.py": "def legacy_format(x):\n    return x\n", "app/a.py": "x = 1\n"})
    repo.git("rm", "-q", "app/old.py")
    repo.write("app/a.py", "x = 2\n")
    repo.add()
    assert both(repo, "remove the unused legacy_format helper") == []
    assert both(repo, "remove the unused helper function") == []


def test_removing_the_named_name_in_a_modified_file_is_silent_but_removing_other_things_is_reported(repo):
    base(repo, {"app/__init__.py": "", "app/u.py": "def keep(x):\n    return x\n\n\ndef old_fn(x):\n    return x\n\n\ndef other(x):\n    return x\n"})
    change(repo, {"app/u.py": "def keep(x):\n    return x\n\n\ndef old_fn(x):\n    return x\n"})
    assert both(repo, "remove the unused `old_fn` function")
    assert both(repo, "remove the unused `other` function") == []


# ---------------------------------------------------------------------------------------------- verifier probes (true messages)

GO_TEST = ('package a\n\nimport "testing"\n\nfunc TestF(t *testing.T) {\n\tif F(1) != 1 {\n\t\tt.Errorf("x")\n\t}\n}\n')
PY_DESCRIBE = "def describe(p):\n    return p.name\n\n\ndef run(p):\n    return describe(p)\n"
MIGRATIONS = {"app/__init__.py": "", "app/a.py": "x = 1\n"}

PROBES = {
    # add tests
    "go-require-equal-in-existing-test": (
        {"go.mod": "module m\n\ngo 1.22\n", "a/a_test.go": GO_TEST},
        {"a/a_test.go": GO_TEST.replace('\t}\n}\n', '\t}\n\trequire.Equal(t, 2, F(2))\n}\n')}, "add tests for F"),
    "node-test-strict-equal": (
        {"src/a.test.js": "const { strictEqual } = require('node:assert');\ntest('f', () => {\n  strictEqual(f(1), 1);\n});\n"},
        {"src/a.test.js": "const { strictEqual } = require('node:assert');\ntest('f', () => {\n  strictEqual(f(1), 1);\n"
                          "  strictEqual(f(2), 2);\n});\n"}, "add tests for f"),
    "add-test-fixtures": (
        PY_TESTS, {"tests/conftest.py": "def items():\n    return [1]\n"}, "add test fixtures"),
    "add-test-helpers": (
        PY_TESTS, {"tests/helpers.py": "def make():\n    return 1\n"}, "add test helpers for the parser"),
    "add-test-data": (PY_TESTS, {"tests/data.py": "SAMPLE = 1\n"}, "add test data"),
    "add-test-config": (PY_TESTS, {"tests/conftest.py": "collect_ignore = ['x.py']\n"}, "add test config to ignore x.py"),
    "add-tests-via-doctest": (
        PY_TESTS, {"tests/conftest.py": "X = 1\n", "app/util.py": "def slugify(t):\n    '''>>> slugify('a')\n    'a'\n    '''\n    return t\n"},
        "add tests via doctest"),
    "revert-of-an-add-tests-commit": (
        PY_TESTS, {"tests/conftest.py": "X = 1\n"}, 'Revert "add tests for slugify"\n\nThis reverts commit 0123abc.'),
    # remove unused
    "drop-unused-column-migration": (
        MIGRATIONS, {"migrations/0002_drop.sql": "ALTER TABLE t DROP COLUMN legacy_flag;\n"}, "drop the unused legacy_flag column"),
    "drop-unused-column-python-migration": (
        MIGRATIONS, {"migrations/0002_drop.py": "def upgrade(op):\n    op.drop_column('t', 'legacy_flag')\n"},
        "drop the unused `legacy_flag` column"),
    "remove-unused-files-from-the-package": (MIGRATIONS, {".npmignore": "dist\n"}, "remove unused files from the package"),
    "delete-unused-cache-entries": (
        MIGRATIONS, {"app/cache.py": "def prune(cache):\n    cache.clear()\n"}, "delete the unused cache entries on startup"),
    "drop-unused-package-from-the-build": (
        {"pyproject.toml": "[tool.x]\nname = 'a'\n"}, {"pyproject.toml": "[tool.x]\nname = 'a'\nexclude = ['b']\n"},
        "drop the unused b package from the build"),
    "remove-unused-imports-from-a-test-file": (
        {"tests/test_foo.py": "import os\nimport sys\n\n\ndef test_foo():\n    assert sys\n"},
        {"tests/test_foo.py": "import sys\n\n\ndef test_foo():\n    assert sys\n"}, "remove unused imports from test_foo.py"),
    "remove-unused-import-in-a-file-name": (
        {"app/foo_bar.py": "import os\nimport sys\n\n\ndef foo_bar():\n    return sys.argv\n"},
        {"app/foo_bar.py": "import sys\n\n\ndef foo_bar():\n    return sys.argv\n"}, "remove unused import in foo_bar.py"),
    "remove-unused-imports-in-a-module": (
        {"app/my_mod.py": "import os\nimport sys\n\nNAME = 'my_mod'\nprint(sys.argv)\n"},
        {"app/my_mod.py": "import sys\n\nNAME = 'my_mod'\nprint(sys.argv)\n"}, "remove unused imports in my_mod"),
    "revert-of-a-remove-unused-commit": (
        {"app/a.py": "x = 1\n"}, {"app/a.py": "x = 1\ny = 2\n"}, 'Revert "remove the unused helper function"'),
    # handle the error
    "handle-failures-subprocess-check": (
        {"app/a.py": "import subprocess\n"}, {"app/a.py": "import subprocess\n\n\ndef go(c):\n    return subprocess.run(c, check=True)\n"},
        "handle failures of the subprocess"),
    "handle-error-exist-ok": (
        {"app/a.py": "import os\n"}, {"app/a.py": "import os\n\n\ndef mk(p):\n    os.makedirs(p, exist_ok=True)\n"},
        "handle the error when the dir exists"),
    "handle-failures-then-rejection-handler": (
        {"src/a.js": "export const f = (u) => fetch(u);\n"},
        {"src/a.js": "export const f = (u) => fetch(u).then(r => r.text(), () => 'x');\n"}, "handle failures"),
    "handle-error-on-error-callback": (
        {"src/a.js": "export const f = (u) => load(u);\n"}, {"src/a.js": "export const f = (u) => load(u, { onError: report });\n"},
        "handle the error"),
    "handle-error-go-ok-idiom": (
        {"a/a.go": "package a\n\nfunc F(m map[string]int) int { return 0 }\n"},
        {"a/a.go": "package a\n\nfunc F(m map[string]int) int {\n\tv, ok := m[\"a\"]\n\t_ = ok\n\treturn v\n}\n"}, "handle the error"),
    "handle-error-retry": (
        {"app/a.py": "def f():\n    return 1\n"}, {"app/a.py": "def f():\n    return 1\n\n\ndef g():\n    return retry(f)\n"},
        "handle the error"),
    # nil check
    "nil-check-added-in-a-helper": (
        {"shop/d.go": GO_DESCRIBE},
        {"shop/d.go": GO_DESCRIBE.replace("return p.Name", "return nameOf(p)") + "\nfunc nameOf(p *Item) string {\n\tif p == nil {\n"
                                                                                     "\t\treturn \"\"\n\t}\n\treturn p.Name\n}\n"},
        "fix the nil check in Describe"),
    "none-check-added-in-a-caller": (
        {"app/a.py": PY_DESCRIBE},
        {"app/a.py": PY_DESCRIBE.replace("    return describe(p)", "    if p is None:\n        return ''\n    return describe(p)")},
        "fix the None check in describe"),
    "nil-check-comma-ok": (
        {"shop/d.go": GO_DESCRIBE},
        {"shop/d.go": GO_DESCRIBE.replace("\treturn p.Name", "\tv, ok := names[\"a\"]\n\t_ = ok\n\treturn v")},
        "fix the nil check in Describe"),
    "nil-check-recover": (
        {"shop/d.go": GO_DESCRIBE},
        {"shop/d.go": GO_DESCRIBE.replace("\treturn p.Name", "\tdefer func() { recover() }()\n\treturn p.Name")},
        "fix the nil check in Describe"),
    "none-check-optional": (
        {"app/a.py": PY_DESCRIBE}, {"app/a.py": PY_DESCRIBE.replace("def describe(p):", "def describe(p: Optional[str]):")},
        "fix the None check in describe"),
    "none-check-nullsafe": (
        {"app/a.py": PY_DESCRIBE}, {"app/a.py": PY_DESCRIBE.replace("def describe(p):", "@nullsafe\ndef describe(p):")},
        "fix the None check in describe"),
}


@pytest.mark.parametrize("name", sorted(PROBES))
def test_verifier_probe_true_message_gets_no_claim_finding(repo, name):
    files, edit, message = PROBES[name]
    base(repo, files)
    change(repo, edit)
    assert both(repo, message) == []


@pytest.mark.parametrize("ext,text", [(".vue", "<script>try { go() } catch (e) { log(e) }</script>\n"),
                                      (".svelte", "{#await p}{:catch e}x{/await}\n"),
                                      (".hs", "f = catch go handler\n"), (".sql", "BEGIN TRY SELECT 1 END TRY BEGIN CATCH END CATCH;\n")])
def test_a_trivial_python_edit_next_to_an_unjudged_language_is_silent(repo, ext, text):
    base(repo, {"app/__init__.py": "", "app/a.py": "x = 1\n"})
    change(repo, {"app/a.py": "x = 2\n", f"ui/App{ext}": text})
    assert both(repo, "handle the error when loading") == []
