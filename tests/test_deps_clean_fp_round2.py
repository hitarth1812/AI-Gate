"""Round 2: false deps.hallucinated BLOCKs on clean popular repositories (attrs, express, flask), found with dated
registry snapshots. These are code-evidence fixes, so they apply to a person and to an AI agent alike: every test
runs both and compares them."""
import pytest

from conftest import find

AGENT = {"CLAUDECODE": "1"}
PYPROJECT = '[project]\nname = "demo"\nversion = "1"\nrequires-python = ">=3.9"\n'


def both(repo):
    """The deps findings (rule, path, line, action) of a person and of an agent for the staged change."""
    repo.add()
    out = []
    for env in (None, AGENT):
        res = repo.check(env=env)
        out.append(sorted((f["rule"], f["path"], f["line"], f["action"]) for f in res["findings"]
                          if f["rule"].startswith("deps.") and not f["allowed"]))
    return out


def blocked(findings, rule="deps.hallucinated"):
    return [f for f in findings if f[0] == rule]


# ---------------------------------------------------------------- 1. stdlib modules newer than the running Python

@pytest.mark.parametrize("stmt", ["import annotationlib", "from annotationlib import Format, get_annotations",
                                  "import compression.zstd", "from compression import zstd"])
def test_python_314_stdlib_module_is_not_hallucinated(repo, stmt):
    # clean-py-attrs src/attr/_compat.py:31 `import annotationlib` (3.14 stdlib; the gate may run on 3.12)
    repo.write("pyproject.toml", PYPROJECT)
    repo.write("src/attr/_compat.py", f"PY_3_14_PLUS = True\n\nif PY_3_14_PLUS:\n    {stmt}\n")
    person, agent = both(repo)
    assert person == agent == []


def test_made_up_name_next_to_a_new_stdlib_name_still_blocks(repo):
    repo.write("pyproject.toml", PYPROJECT)
    repo.write("src/app.py", "import annotationlibs\nimport compressions\n")
    person, agent = both(repo)
    assert len(blocked(person)) == 2 and person == agent


# ---------------------------------------------------------------- 2. `from "x"` inside a JS string or comment

PKG_JSON = '{"name": "demo", "version": "1.0.0", "dependencies": {"left-pad": "1.3.0"}}\n'
EXPRESS_TEST = ("describe('res', function(){\n"
                "  it('should set location from \"Referer\" header', function(done){\n"
                "    request(app).get('/').expect('Location', '/', done)\n"
                "  })\n"
                "  it('should set location from \\'Referer\\' header', function(){})\n"
                "})\n")


def js_findings(repo, path, text, rule="deps.hallucinated"):
    repo.write("package.json", PKG_JSON)
    repo.write(path, text)
    person, agent = both(repo)
    assert person == agent
    return [f for f in person if f[0] == rule]


def test_from_inside_a_js_string_is_not_an_import(repo):
    # clean-js-express test/res.location.js:50 and :64
    assert js_findings(repo, "test/res.location.js", EXPRESS_TEST) == []


@pytest.mark.parametrize("line", [
    "const t = `it should read from \"ghost-npm\" header`;",
    "const t = 'use require(\"ghost-npm\") here';",
    "const t = \"await import('ghost-npm')\";",
    "run(); // load it from \"ghost-npm\"",
    "run(); /* from \"ghost-npm\" */ more();",
    "/* a block comment\n   import x from \"ghost-npm\"\n*/",
])
def test_import_words_in_a_string_or_comment_are_not_imports(repo, line):
    assert js_findings(repo, "src/a.js", line + "\n") == []


@pytest.mark.parametrize("text", [
    'import x from "ghost-npm";\n',
    'import { a, b } from \'ghost-npm\';\n',
    'export * from "ghost-npm";\n',
    'export { a } from "ghost-npm";\n',
    'import {\n  a,\n  b,\n} from "ghost-npm";\n',
    'import "ghost-npm";\n',
    'const x = require("ghost-npm");\n',
    'const m = await import("ghost-npm");\n',
    'const s = `${require("ghost-npm")}`;\n',
    "it('works', () => {\n  const x = require('ghost-npm')\n})\n",
])
def test_real_js_imports_of_a_made_up_package_still_block(repo, text):
    found = js_findings(repo, "src/a.js", text)
    assert [f[3] for f in found] == ["block"]


def test_a_real_import_after_a_string_with_from_on_the_same_line_still_blocks(repo):
    found = js_findings(repo, "src/a.js", 'const t = \'from "Referer"\'; import x from "ghost-npm";\n')
    assert [f[3] for f in found] == ["block"]
