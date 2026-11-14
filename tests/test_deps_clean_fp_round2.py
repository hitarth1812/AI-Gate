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


# ---------------------------------------------------------------- 3. a test that creates the module it imports

FLASK_MODULE = r'''import os

import flask


def test_uninstalled_module_paths(modules_tmp_path, purge_module):
    (modules_tmp_path / "config_module_app.py").write_text(
        "import os\n"
        "app = flask.Flask(__name__)\n"
    )
    purge_module("config_module_app")

    from config_module_app import app

    assert app.instance_path == os.fspath(modules_tmp_path / "instance")
'''
FLASK_PACKAGE = r'''def test_uninstalled_package_paths(modules_tmp_path, purge_module):
    app = modules_tmp_path / "config_package_app"
    app.mkdir()
    (app / "__init__.py").write_text("import flask\napp = flask.Flask(__name__)\n")
    purge_module("config_package_app")

    from config_package_app import app

    assert app.instance_path
'''
FLASK_SITE_MODULE = r'''def test_installed_module_paths(modules_tmp_path, purge_module, site_packages):
    (site_packages / "site_app.py").write_text("import flask\napp = flask.Flask(__name__)\n")
    purge_module("site_app")

    from site_app import app

    assert app.instance_path
'''
FLASK_INSTALLED_PACKAGE = r'''def test_installed_package_paths(modules_tmp_path, purge_module, monkeypatch):
    installed_path = modules_tmp_path / "path"
    installed_path.mkdir()
    monkeypatch.syspath_prepend(installed_path)

    app = installed_path / "installed_package"
    app.mkdir()
    (app / "__init__.py").write_text("import flask\napp = flask.Flask(__name__)\n")
    purge_module("installed_package")

    from installed_package import app

    assert app.instance_path
'''
FLASK_PREFIX_PACKAGE = r'''def test_prefix_package_paths(modules_tmp_path, purge_module, site_packages):
    app = site_packages / "site_package"
    app.mkdir()
    (app / "__init__.py").write_text("import flask\napp = flask.Flask(__name__)\n")
    purge_module("site_package")

    import site_package

    assert site_package.app.instance_path
'''
FLASK_CASES = {"module": FLASK_MODULE, "package": FLASK_PACKAGE, "site-module": FLASK_SITE_MODULE,
               "installed-package": FLASK_INSTALLED_PACKAGE, "prefix-package": FLASK_PREFIX_PACKAGE}
MADE_UP = ("config_module_app", "config_package_app", "site_app", "installed_package", "site_package")


def py_findings(repo, path, text):
    repo.write("pyproject.toml", PYPROJECT)
    repo.write(path, text)
    person, agent = both(repo)
    assert person == agent
    return blocked(person)


@pytest.mark.parametrize("case", sorted(FLASK_CASES))
def test_module_a_test_creates_is_not_hallucinated(repo, case):
    # clean-py-flask tests/test_instance_config.py:25, 41, 73, 92, 107
    assert py_findings(repo, "tests/test_instance_config.py", FLASK_CASES[case]) == []


def test_the_whole_flask_file_has_no_hallucinated_import(repo):
    text = "import os\n\n\n" + "\n\n".join(FLASK_CASES[c] for c in sorted(FLASK_CASES))
    assert py_findings(repo, "tests/test_instance_config.py", text) == []


def test_joinpath_and_os_path_join_also_count_as_creating(repo):
    text = ('import os\n\n\ndef test_a(tmp_path):\n    tmp_path.joinpath("gen_one.py").write_text("x = 1")\n'
            '    from gen_one import x\n\n\ndef test_b(tmp_path):\n    d = os.path.join(tmp_path, "gen_two")\n'
            '    os.makedirs(d)\n    import gen_two\n')
    assert py_findings(repo, "tests/test_gen.py", text) == []


MISSING = 'def test_a(tmp_path):\n    from ghost_mod import app\n    assert app\n'


@pytest.mark.parametrize("name,path,text", [
    ("no-creation", "tests/test_a.py", MISSING),
    ("same-shape-in-source", "src/app.py", FLASK_MODULE),
    ("same-shape-in-source-package", "src/app.py", FLASK_PACKAGE.replace("config_package_app", "ghost_mod")),
    ("name-only-in-a-comment", "tests/test_a.py",
     'def test_a(tmp_path):\n    # (tmp_path / "ghost_mod.py").write_text("x")\n    tmp_path.mkdir()\n'
     '    from ghost_mod import app\n'),
    ("name-only-in-a-docstring", "tests/test_a.py",
     'def test_a(tmp_path):\n    """(tmp_path / "ghost_mod.py").write_text("x")"""\n    tmp_path.mkdir()\n'
     '    from ghost_mod import app\n'),
    ("string-but-not-a-path", "tests/test_a.py",
     'def test_a(tmp_path):\n    assert tmp_path.name != "ghost_mod.py"\n    tmp_path.mkdir()\n    from ghost_mod import app\n'),
    ("path-but-nothing-created", "tests/test_a.py",
     'def test_a(tmp_path):\n    p = tmp_path / "ghost_mod.py"\n    assert not p\n    from ghost_mod import app\n'),
    ("created-after-the-import", "tests/test_a.py",
     'def test_a(tmp_path):\n    from ghost_mod import app\n    (tmp_path / "ghost_mod.py").write_text("x")\n'),
    ("created-in-another-test", "tests/test_a.py",
     'def test_a(tmp_path):\n    (tmp_path / "ghost_mod.py").write_text("x")\n\n\n'
     'def test_b(tmp_path):\n    from ghost_mod import app\n'),
    ("other-name-created", "tests/test_a.py",
     'def test_a(tmp_path):\n    (tmp_path / "other_mod.py").write_text("x")\n    from ghost_mod import app\n'),
])
def test_not_created_here_still_blocks(repo, name, path, text):
    found = py_findings(repo, path, text)
    assert [f[3] for f in found] == ["block"] * len(found) and len(found) >= 1


# ---------------------------------------------------------------- 2b. a lexer misread must not hide a real import

@pytest.mark.parametrize("path,text", [
    ("src/a.js", "const a = <p>I'm ok</p>; const x = require(\"ghost-evil-pkg\");\n"),
    ("src/a.jsx", "const a = <p>I'm ok</p>; const x = require(\"ghost-evil-pkg\");\n"),
    ("src/a.jsx", "const a = <p>don't</p>; const x = require('ghost-evil-pkg');\n"),
    ("src/a.js", "if (x) /'/.test(y); import z from \"ghost-evil-pkg\"\n"),
    ("src/a.js", "if (x) /\"/.test(y); const m = await import('ghost-evil-pkg');\n"),
    ("src/a.jsx", "const a = <p>I'm ok</p>; export * from \"ghost-evil-pkg\";\n"),
])
def test_unterminated_quote_on_the_line_does_not_hide_a_real_import(repo, path, text):
    found = js_findings(repo, path, text)
    assert [f[3] for f in found] == ["block"]


def test_a_closed_string_next_to_a_misread_line_is_still_not_an_import(repo):
    text = ("describe('res', function(){\n  it('should set location from \"Referer\" header', function(done){})\n})\n"
            "const a = <p>I'm ok</p>;\n")
    assert js_findings(repo, "src/a.jsx", text) == []
