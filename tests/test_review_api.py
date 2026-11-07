"""Phase 6A: the symbol index and the api.* rules (api.unknown_symbol, api.bad_call, api.broken_caller).

Each rule has a true positive, a false positive (code that looks wrong but is fine) and a string/comment false positive
(the same text inside a string or a comment). The reviewer runs with --deep; nothing here goes through a commit hook."""
import ast
import json

import pytest

from conftest import aigate

AGENT = {"CLAUDECODE": "1"}


def deep(repo, env=None):
    return repo.check("--deep", "--offline", env=env)


def hits(res, rule):
    return sorted((f["path"], f["line"]) for f in res["findings"] if f["rule"] == rule and not f["allowed"])


def one(res, rule):
    got = [f for f in res["findings"] if f["rule"] == rule and not f["allowed"]]
    assert len(got) == 1, got
    return got[0]


def base(repo, files):
    for rel, text in files.items():
        repo.write(rel, text)
    repo.commit("base")


def change(repo, files):
    for rel, text in files.items():
        repo.write(rel, text)
    repo.add()


UTILS = ("def parse_date(text, fmt='%Y'):\n    return text\n\n\ndef slugify(title, *, lower=True):\n    return title\n\n\n"
         "class Box:\n    pass\n")
PKG = {"app/__init__.py": "", "app/utils.py": UTILS}


# ------------------------------------------------------------------------------------------------ the symbol index

def test_python_index_records_names_signatures_and_dynamic_markers():
    entry = aigate.py_symbols(
        "import os\nfrom . import sibling\nfrom .m import thing as other\n__all__ = ['f']\n\n"
        "def f(a, b=1, /, c=2, *rest, d, e=3, **kw):\n    pass\n\n\nclass K:\n    def m(self, x):\n        pass\n\n\nX = 1\n")
    names = entry["names"]
    assert set(names) >= {"os", "sibling", "other", "f", "K", "X"} and not entry["dyn"] and entry["all"] == ["f"]
    sig = names["f"]["sig"]
    assert sig["p"] == [["a", False], ["b", True], ["c", True]] and sig["po"] == 2 and sig["va"] and sig["kw"]
    assert sig["ko"] == [["d", False], ["e", True]] and not sig["dec"]
    assert names["K"]["m"]["m"]["p"] == [["self", False], ["x", False]]
    assert names["other"] == {"t": "i", "mod": "m", "lvl": 1, "from": "thing"}
    assert aigate.py_symbols("def broken(:\n") is None


@pytest.mark.parametrize("text", ["def __getattr__(name):\n    return 1\n", "from x import *\n", "globals()['a'] = 1\n",
                                  "x = vars(m).__dict__\n", "import sys\nsys.modules['a'] = 1\n", "__path__ = []\n",
                                  "__all__ = compute()\n"])
def test_python_index_marks_dynamic_modules(text):
    assert aigate.py_symbols(text)["dyn"] is True


def test_python_index_collects_names_bound_in_branches_and_loops():
    entry = aigate.py_symbols("try:\n    import fast as impl\nexcept ImportError:\n    impl = None\n"
                              "if True:\n    def late():\n        pass\nfor loop_var in []:\n    pass\n"
                              "with open('x') as handle:\n    pass\n")
    assert set(entry["names"]) >= {"impl", "late", "loop_var", "handle"}
    assert entry["names"]["impl"].get("dup")


def test_go_index_records_functions_methods_types_and_values():
    entry = aigate.go_symbols(
        "package store\n\nimport \"fmt\"\n\ntype Server struct{}\n\ntype (\n\tReader interface{}\n\tWriter interface{}\n)\n\n"
        "const (\n\tModeA = iota\n\tModeB\n)\n\nvar Default, other = 1, 2\n\n"
        "func Open(path string, flags ...int) (*Server, error) {\n\treturn nil, nil\n}\n\n"
        "func (s *Server) Close() error { return nil }\n\nfunc helper(a, b int, c func(x, y int) bool) {}\n\n"
        "func Pair() (int, string) { return 1, \"\" }\n\n"
        "// func Commented(a int) {}\nvar text = \"func InString(a int) {}\"\n")
    names = entry["names"]
    assert entry["pkg"] == "store" and "Commented" not in names and "InString" not in names
    assert names["Open"]["n"] == 2 and names["Open"]["va"] and names["Open"]["err"] and names["Open"]["r"] == 2
    assert names["helper"]["n"] == 3 and not names["helper"]["err"]
    assert names["Pair"]["r"] == 2 and not names["Pair"]["err"]
    assert names["Server"]["t"] == "t" and names["Reader"]["t"] == "t" and names["ModeB"]["t"] == "v"
    assert {"Default", "other"} <= set(names) and "Server.Close" in entry["methods"] and entry["methods"]["Server.Close"]["err"]
    assert {"Open", "helper", "Pair"} <= set(entry["calls"])


def test_js_index_records_exports_and_arity():
    entry = aigate.js_symbols(
        "export function a(x, y) {}\nexport const b = (p, q, r) => p;\nexport const c = 1;\nexport class K {}\n"
        "export interface I {}\nexport type T = number;\nexport enum E { A }\nconst hidden = 1;\nfunction local() {}\n"
        "export { hidden as renamed, local };\nexport default function () {}\n"
        "// export const commented = 1;\nconst s = 'export const inString = 1';\n")
    names = entry["names"]
    assert {"a", "b", "c", "K", "I", "T", "E", "renamed", "local"} <= set(names)
    assert names["a"]["n"] == 2 and names["b"]["n"] == 3 and entry["default"] and not entry["dyn"]
    assert "commented" not in names and "inString" not in names and "hidden" not in names


@pytest.mark.parametrize("text", ["export * from './x';\n", "module.exports = { a: 1 };\n", "export = thing;\n",
                                  "Object.defineProperty(exports, '__esModule', { value: true });\n"])
def test_js_index_marks_unknowable_export_lists(text):
    assert aigate.js_symbols(text)["dyn"] is True


def test_the_index_is_cached_by_blob_and_a_tampered_cache_is_ignored(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "from app.utils import parse_date\n"})
    assert not hits(deep(repo), "api.unknown_symbol")
    store = [p for p in (repo.path.parent / "cache").iterdir() if p.name.startswith("symbols-v")]
    assert len(store) == 1
    doc = json.loads(store[0].read_text())
    assert doc["tool"] == aigate.__version__ and doc["entries"]
    doc["entries"] = {k: {"k": "py", "names": {"parse_date": {"t": "v"}}, "dyn": False, "all": None, "calls": []}
                      for k in doc["entries"]}                                  # forged entries, stale signature
    store[0].write_text(json.dumps(doc))
    change(repo, {"app/h.py": "from app.utils import parse_datex\n"})
    assert hits(deep(repo), "api.unknown_symbol") == [("app/h.py", 1)]            # re-parsed, the forgery was not trusted


def test_no_cache_flag_writes_no_symbol_file(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "from app.utils import parse_date\n"})
    assert repo.check("--deep", "--offline", "--no-cache")["verdict"] in ("pass", "warn")
    assert not [p for p in (repo.path.parent / "cache").glob("symbols-v*")]


def test_reviewer_rules_never_run_without_deep(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "from app.utils import nope\nfrom app.utils import slugify\nslugify(1, 2, 3)\n"})
    plain = repo.check("--offline")
    assert not [f for f in plain["findings"] if f["rule"].startswith(("api.", "tests.missing", "review.", "code.error_check"))]
    assert hits(deep(repo), "api.unknown_symbol") and hits(deep(repo), "api.bad_call")


# ------------------------------------------------------------------------------------------------ api.unknown_symbol (Python)

def test_unknown_name_in_a_from_import_is_reported_with_a_suggestion(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "from app.utils import parse_dates\n"})
    f = one(deep(repo), "api.unknown_symbol")
    assert (f["path"], f["line"]) == ("app/h.py", 1) and "no `parse_dates`" in f["message"] and "parse_date" in f["hint"]


def test_unknown_module_inside_an_in_repo_package_is_reported(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "from app.helpers import shout\nimport app.nothing\n"})
    res = deep(repo)
    assert hits(res, "api.unknown_symbol") == [("app/h.py", 1), ("app/h.py", 2)]
    assert "does not exist" in one_of(res, 1)["message"]


def one_of(res, line):
    return next(f for f in res["findings"] if f["rule"] == "api.unknown_symbol" and f["line"] == line)


def test_unknown_attribute_of_an_imported_module_is_reported(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "import app.utils as u\nfrom app import utils\n\n\ndef go():\n    u.nothing()\n"
                              "    return utils.Box, utils.parse_dates\n"})
    assert hits(deep(repo), "api.unknown_symbol") == [("app/h.py", 6), ("app/h.py", 7)]


def test_relative_imports_are_resolved(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "from .utils import nope\nfrom . import utils\nfrom .. import beyond\nfrom .utils import Box\n"})
    assert hits(deep(repo), "api.unknown_symbol") == [("app/h.py", 1)]


def test_names_that_exist_are_not_reported(repo):
    base(repo, {**PKG, "app/__init__.py": "from .utils import parse_date\n__all__ = ['parse_date', 'pkg_var']\npkg_var = 1\n",
                "app/sub/__init__.py": "", "app/sub/leaf.py": "VALUE = 1\n"})
    change(repo, {"app/h.py": "from app import parse_date, pkg_var, utils, sub\nfrom app.sub import leaf\n"
                              "from app.sub.leaf import VALUE\nimport app.sub.leaf\nfrom app.utils import (Box,\n    slugify)\n"
                              "x = app.sub.leaf.VALUE\ny = utils.Box\n"})
    assert not hits(deep(repo), "api.unknown_symbol")


def test_reexports_through_an_init_are_followed(repo):
    base(repo, {**PKG, "app/__init__.py": "from .utils import slugify as make_slug\n"})
    change(repo, {"app/h.py": "from app import make_slug, ghost\n"})
    assert hits(deep(repo), "api.unknown_symbol") == [("app/h.py", 1)]
    assert "ghost" in one(deep(repo), "api.unknown_symbol")["message"]


@pytest.mark.parametrize("module", [
    "def __getattr__(name):\n    return 1\n",
    "from other import *\n",
    "globals()['dynamic'] = 1\n",
    "import sys\nsys.modules[__name__].dynamic = 1\n",
    "__all__ = ['dynamic']\n",
    "__path__ = []\n",
])
def test_dynamic_modules_get_no_claims(repo, module):
    base(repo, {"app/__init__.py": "", "app/dyn.py": module})
    change(repo, {"app/h.py": "from app.dyn import dynamic\nimport app.dyn as d\nd.dynamic()\n"})
    assert not hits(deep(repo), "api.unknown_symbol")


def test_getattr_on_the_module_stops_attribute_claims(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "import app.utils as u\nif hasattr(u, 'maybe'):\n    u.maybe()\n"})
    assert not hits(deep(repo), "api.unknown_symbol")


def test_optional_imports_are_not_claimed(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "try:\n    from app.utils import fast_path\nexcept ImportError:\n    fast_path = None\n"
                              "import sys\nif sys.version_info < (3, 8):\n    from app.utils import legacy\n"})
    assert not hits(deep(repo), "api.unknown_symbol")


def test_external_and_stdlib_modules_are_never_judged(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "import os\nimport requests\nfrom os import nothing\nfrom requests import nothing as n2\n"
                              "os.nothing()\nrequests.nothing()\n"})
    assert not hits(deep(repo), "api.unknown_symbol")


def test_a_module_that_does_not_parse_gives_no_claim(repo):
    base(repo, {"app/__init__.py": "", "app/broken.py": "def oops(:\n"})
    change(repo, {"app/h.py": "from app.broken import anything\n"})
    assert not hits(deep(repo), "api.unknown_symbol")


def test_only_added_lines_are_judged(repo):
    base(repo, {**PKG, "app/h.py": "from app.utils import old_mistake\n"})
    change(repo, {"app/h.py": "from app.utils import old_mistake\nfrom app.utils import parse_date\n"})
    assert not hits(deep(repo), "api.unknown_symbol")


def test_a_shadowed_name_is_not_resolved(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "from app.utils import slugify\n\n\ndef run(slugify):\n    return slugify(1, 2, 3)\n"})
    assert not hits(deep(repo), "api.bad_call")


def test_import_text_in_strings_and_comments_is_not_an_import(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "# from app.utils import nothing\nDOC = 'from app.utils import nothing'\n"
                              "TEXT = '''\nimport app.nothing\nu.nope()\n'''\n"})
    assert not hits(deep(repo), "api.unknown_symbol")


def test_unknown_symbol_is_fix_for_agents_and_warn_for_people(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "from app.utils import nope\n"})
    assert one(deep(repo), "api.unknown_symbol")["action"] == "warn"
    assert one(deep(repo, AGENT), "api.unknown_symbol")["action"] == "fix"


def test_src_layout_is_a_source_root(repo):
    base(repo, {"src/lib/__init__.py": "", "src/lib/core.py": "def real():\n    pass\n"})
    change(repo, {"src/lib/h.py": "from lib.core import real, fake\n"})
    assert hits(deep(repo), "api.unknown_symbol") == [("src/lib/h.py", 1)]


# ------------------------------------------------------------------------------------------------ api.bad_call (Python)

CALLS = "from app.utils import parse_date, slugify\n\n\ndef go(x):\n"


@pytest.mark.parametrize("call,needle", [
    ("slugify(x, 1)", "at most 1"),
    ("slugify(x, upper=True)", "unknown keyword argument `upper`"),
    ("slugify()", "required `title`"),
    ("slugify(x, title=x)", "twice"),
    ("parse_date(x, '%d', 3)", "at most 2"),
])
def test_calls_that_cannot_work_are_reported(repo, call, needle):
    base(repo, PKG)
    change(repo, {"app/h.py": CALLS + f"    return {call}\n"})
    f = one(deep(repo), "api.bad_call")
    assert f["line"] == 5 and needle in f["message"] and "app/utils.py" in f["message"]


@pytest.mark.parametrize("call", ["slugify(x)", "slugify(x, lower=False)", "slugify(title=x)", "parse_date(x)",
                                  "parse_date(x, '%d')", "parse_date(*x)", "slugify(**x)", "slugify(x, **x)"])
def test_calls_that_fit_are_not_reported(repo, call):
    base(repo, PKG)
    change(repo, {"app/h.py": CALLS + f"    return {call}\n"})
    assert not hits(deep(repo), "api.bad_call")


def test_varargs_decorators_and_methods_get_no_claims(repo):
    base(repo, {"app/__init__.py": "", "app/f.py": "import functools\n\n\ndef many(*args, **kw):\n    pass\n\n\n"
                "@functools.cache\ndef cached(a):\n    pass\n\n\nclass C:\n    def m(self, a):\n        pass\n"})
    change(repo, {"app/h.py": "from app.f import many, cached, C\nmany(1, 2, 3, x=1)\ncached(1, 2, 3)\nC().m(1, 2, 3)\nC.m(1, 2, 3)\n"})
    assert not hits(deep(repo), "api.bad_call")


def test_a_call_to_a_function_of_the_same_file_is_checked(repo):
    base(repo, {"app/__init__.py": ""})
    change(repo, {"app/h.py": "def helper(a, b):\n    return a\n\n\ndef go():\n    return helper(1)\n"})
    assert hits(deep(repo), "api.bad_call") == [("app/h.py", 6)]


def test_module_attribute_calls_are_checked(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "import app.utils as u\nfrom app import utils\nu.slugify(1, 2)\nutils.parse_date()\n"})
    assert hits(deep(repo), "api.bad_call") == [("app/h.py", 3), ("app/h.py", 4)]


def test_redefined_functions_and_dynamic_modules_get_no_call_claims(repo):
    base(repo, {"app/__init__.py": "", "app/twice.py": "def f(a):\n    pass\n\n\ndef f(a, b):\n    pass\n",
                "app/dyn.py": "def __getattr__(n):\n    pass\n\n\ndef g(a):\n    pass\n"})
    change(repo, {"app/h.py": "from app.twice import f\nfrom app.dyn import g\nf(1, 2, 3)\ng(1, 2, 3)\n"})
    res = deep(repo)
    assert not hits(res, "api.bad_call") or hits(res, "api.bad_call") == [("app/h.py", 4)]
    assert ("app/h.py", 3) not in hits(res, "api.bad_call")


def test_calls_in_strings_and_comments_are_not_calls(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": "from app.utils import slugify\n# slugify(1, 2, 3)\nX = 'slugify(1, 2, 3)'\n"
                              "Y = '''\nslugify()\n'''\n"})
    assert not hits(deep(repo), "api.bad_call")


def test_bad_call_is_fix_for_agents_and_warn_for_people(repo):
    base(repo, PKG)
    change(repo, {"app/h.py": CALLS + "    return slugify(x, 1)\n"})
    assert one(deep(repo), "api.bad_call")["action"] == "warn"
    assert one(deep(repo, AGENT), "api.bad_call")["action"] == "fix"


def test_call_problem_helper_on_raw_signatures():
    sig = aigate.py_symbols("def f(a, b=1, /, c=2, *, d):\n    pass\n")["names"]["f"]["sig"]
    call = lambda src: ast.parse(src).body[0].value                                  # noqa: E731
    assert aigate.py_call_problem(sig, call("f(1, d=1)")) is None
    assert "required `d`" in aigate.py_call_problem(sig, call("f(1)"))
    assert "unknown keyword" in aigate.py_call_problem(sig, call("f(1, a=2, d=1)"))      # a is positional-only
    assert "at most 3" in aigate.py_call_problem(sig, call("f(1, 2, 3, 4, d=1)"))


# ------------------------------------------------------------------------------------------------ api.broken_caller

SERVICE = "from app.utils import parse_date\n\n\ndef load(raw):\n    return parse_date(raw, '%d')\n"


def test_a_stale_caller_of_a_changed_signature_is_reported_with_its_location(repo):
    base(repo, {**PKG, "app/service.py": SERVICE})
    change(repo, {"app/utils.py": UTILS.replace("def parse_date(text, fmt='%Y')", "def parse_date(text, fmt, strict)")})
    res = deep(repo)
    f = one(res, "api.broken_caller")
    assert (f["path"], f["line"]) == ("app/service.py", 5) and "app/utils.py" in f["message"] and "parse_date" in f["message"]
    assert f["action"] == "warn" and one(deep(repo, AGENT), "api.broken_caller")["action"] == "fix"


def test_a_renamed_parameter_breaks_keyword_callers_only(repo):
    base(repo, {**PKG, "app/a.py": "from app.utils import slugify\nslugify('x', lower=False)\n",
                "app/b.py": "from app.utils import slugify\nslugify('x')\n"})
    change(repo, {"app/utils.py": UTILS.replace("lower=True", "lowercase=True")})
    assert hits(deep(repo), "api.broken_caller") == [("app/a.py", 2)]


def test_compatible_signature_changes_are_not_reported(repo):
    base(repo, {**PKG, "app/service.py": SERVICE})
    change(repo, {"app/utils.py": UTILS.replace("def parse_date(text, fmt='%Y')", "def parse_date(text, fmt='%Y', extra=None)")})
    assert not hits(deep(repo), "api.broken_caller")
    change(repo, {"app/utils.py": UTILS.replace("def parse_date(text, fmt='%Y')", "def parse_date(text, *args, **kw)")})
    assert not hits(deep(repo), "api.broken_caller")


def test_a_caller_the_change_updated_is_not_reported(repo):
    base(repo, {**PKG, "app/service.py": SERVICE})
    change(repo, {"app/utils.py": UTILS.replace("def parse_date(text, fmt='%Y')", "def parse_date(text, fmt, strict)"),
                  "app/service.py": SERVICE.replace("parse_date(raw, '%d')", "parse_date(raw, '%d', True)")})
    assert not hits(deep(repo), "api.broken_caller")


def test_a_caller_in_the_changed_file_on_an_untouched_line_is_reported(repo):
    text = "def helper(a):\n    return a\n\n\ndef go():\n    return helper(1)\n"
    base(repo, {"app/__init__.py": "", "app/h.py": text})
    change(repo, {"app/h.py": text.replace("def helper(a):", "def helper(a, b):")})
    assert hits(deep(repo), "api.broken_caller") == [("app/h.py", 6)]


def test_callers_through_reexports_and_module_attributes_are_found(repo):
    base(repo, {**PKG, "app/__init__.py": "from .utils import slugify\n",
                "app/a.py": "from app import slugify\nslugify('x', lower=False)\n",
                "app/b.py": "import app.utils\napp.utils.slugify('x', lower=False)\n"})
    change(repo, {"app/utils.py": UTILS.replace("lower=True", "lowercase=True")})
    assert hits(deep(repo), "api.broken_caller") == [("app/a.py", 2), ("app/b.py", 2)]


def test_caller_text_in_strings_and_comments_is_not_a_caller(repo):
    base(repo, {**PKG, "app/a.py": "# slugify('x', lower=False)\nDOC = \"slugify('x', lower=False)\"\n"})
    change(repo, {"app/utils.py": UTILS.replace("lower=True", "lowercase=True")})
    assert not hits(deep(repo), "api.broken_caller")


def test_broken_caller_reports_at_most_a_bounded_number(repo):
    callers = {f"app/c{i}.py": "from app.utils import parse_date\nparse_date('x', 'y')\n" for i in range(aigate.CALLER_LIMIT + 5)}
    base(repo, {**PKG, **callers})
    change(repo, {"app/utils.py": UTILS.replace("def parse_date(text, fmt='%Y')", "def parse_date(text, fmt, strict)")})
    assert len(hits(deep(repo), "api.broken_caller")) == aigate.CALLER_LIMIT


# ------------------------------------------------------------------------------------------------ Go

GO_MOD = "module example.com/app\n\ngo 1.22\n"
GO_STORE = ("package store\n\nfunc Open(path string, flags ...int) error {\n\treturn nil\n}\n\n"
            "func Pair(a, b int) (int, error) {\n\treturn a, nil\n}\n\ntype Server struct{}\n\nconst Version = \"1\"\n\n"
            "func internal(a int) {}\n")
GO_BASE = {"go.mod": GO_MOD, "store/store.go": GO_STORE}
GO_HEAD = "package main\n\nimport (\n\t\"fmt\"\n\t\"example.com/app/store\"\n)\n\nfunc main() {\n"


def go_main(*lines):
    return GO_HEAD + "\n".join("\t" + x for x in lines) + "\n\tfmt.Println()\n}\n"


def test_go_unknown_package_member_is_reported(repo):
    base(repo, GO_BASE)
    change(repo, {"main.go": go_main("store.Opn(\"x\")", "_ = store.Version", "var s store.Server", "_ = s")})
    res = deep(repo)
    f = one(res, "api.unknown_symbol")
    assert f["line"] == 9 and "no `Opn`" in f["message"] and "Open" in f["hint"]


def test_go_lowercase_member_of_another_package_is_not_exported(repo):
    base(repo, GO_BASE)
    change(repo, {"main.go": go_main("store.internal(1)")})
    assert "not exported" in one(deep(repo), "api.unknown_symbol")["message"]


def test_go_existing_members_and_aliases_are_fine(repo):
    base(repo, GO_BASE)
    change(repo, {"main.go": GO_HEAD.replace('"example.com/app/store"', 's "example.com/app/store"')
                  + "\t_ = s.Open(\"x\")\n\t_ = s.Version\n\tvar v s.Server\n\t_ = v\n\tfmt.Println()\n}\n"})
    assert not hits(deep(repo), "api.unknown_symbol")


def test_go_local_variable_named_like_the_package_is_not_the_package(repo):
    base(repo, GO_BASE)
    change(repo, {"main.go": go_main("store := map[string]int{}", "_ = store.Nothing")})
    assert not hits(deep(repo), "api.unknown_symbol")


def test_go_text_in_strings_and_comments_is_not_a_reference(repo):
    base(repo, GO_BASE)
    change(repo, {"main.go": go_main("// store.Nothing(1, 2, 3)", "_ = \"store.Nothing(1, 2, 3)\"", "_ = `store.Open(1,`")})
    res = deep(repo)
    assert not hits(res, "api.unknown_symbol") and not hits(res, "api.bad_call")


def test_go_packages_outside_the_repository_are_never_judged(repo):
    base(repo, GO_BASE)
    change(repo, {"main.go": "package main\n\nimport \"strings\"\n\nfunc main() {\n\t_ = strings.Nothing(1, 2)\n}\n"})
    assert not hits(deep(repo), "api.unknown_symbol")


def test_go_string_arguments_count_as_arguments(repo):
    base(repo, GO_BASE)
    change(repo, {"main.go": go_main("_, _ = store.Pair(\"a, b\", \"(\")", "_, _ = store.Pair(\"only one\")")})
    assert hits(deep(repo), "api.bad_call") == [("main.go", 10)]


def test_go_calls_that_miss_the_required_arguments_are_reported(repo):
    base(repo, GO_BASE)
    change(repo, {"main.go": go_main("_ = store.Open()")})
    assert "passes 0 arguments but needs at least 1" in one(deep(repo), "api.bad_call")["message"]


@pytest.mark.parametrize("call,count", [("store.Pair(1)", 1), ("store.Pair(1, 2, 3)", 3)])
def test_go_calls_with_the_wrong_number_of_arguments_are_reported(repo, call, count):
    base(repo, GO_BASE)
    change(repo, {"main.go": go_main("_, _ = " + call)})
    f = one(deep(repo), "api.bad_call")
    assert f["line"] == 9 and f"passes {count} arguments but takes 2" in f["message"]


@pytest.mark.parametrize("call", ["store.Pair(1, 2)", "store.Open(\"a\")", "store.Open(\"a\", 1, 2, 3)", "store.Open(\"a\", a...)",
                                  "store.Pair(f())"])
def test_go_calls_that_fit_are_not_reported(repo, call):
    base(repo, GO_BASE)
    change(repo, {"main.go": go_main("a := []int{}", "f := func() (int, int) { return 1, 2 }", "_, _, _ = a, f, 0",
                                     "_ = " + call if "Pair" not in call else "_, _ = " + call)})
    assert not hits(deep(repo), "api.bad_call")


def test_go_same_package_calls_are_checked_and_declarations_are_not_calls(repo):
    base(repo, GO_BASE)
    change(repo, {"store/more.go": "package store\n\nfunc user() {\n\t_, _ = Pair(1)\n\tinternal(1)\n}\n\nfunc (s Server) Open() {}\n"})
    assert hits(deep(repo), "api.bad_call") == [("store/more.go", 4)]


def test_go_external_test_package_sees_only_imports(repo):
    base(repo, GO_BASE)
    change(repo, {"store/store_test.go": "package store_test\n\nimport \"testing\"\n\nfunc TestX(t *testing.T) {\n\tPair(1)\n}\n"})
    assert not hits(deep(repo), "api.bad_call")


def test_go_changed_arity_breaks_callers_elsewhere(repo):
    base(repo, {**GO_BASE, "main.go": go_main("_, _ = store.Pair(1, 2)"),
                "store/use.go": "package store\n\nfunc use() {\n\t_, _ = Pair(3, 4)\n}\n"})
    change(repo, {"store/store.go": GO_STORE.replace("func Pair(a, b int)", "func Pair(a, b, c int)")})
    assert hits(deep(repo), "api.broken_caller") == [("main.go", 9), ("store/use.go", 4)]


def test_go_compatible_arity_changes_and_updated_callers_are_fine(repo):
    base(repo, {**GO_BASE, "main.go": go_main("_ = store.Open(\"x\")")})
    change(repo, {"store/store.go": GO_STORE.replace("flags ...int", "flags ...int)(error")})
    change(repo, {"store/store.go": GO_STORE.replace("func Open(path string, flags ...int)", "func Open(path string, more string, flags ...int)"),
                  "main.go": go_main("_ = store.Open(\"x\", \"y\")")})
    assert not hits(deep(repo), "api.broken_caller")


# ------------------------------------------------------------------------------------------------ JS / TS

JS_LIB = ("export function load(a, b) { return a; }\nexport const helper = (x) => x;\nexport class Store {}\n"
          "export default function main() {}\n")


def test_js_missing_named_export_is_reported(repo):
    base(repo, {"src/lib.ts": JS_LIB})
    change(repo, {"src/app.ts": "import main, { load, loadd, Store as S } from './lib';\nimport type { Store } from './lib';\n"})
    f = one(deep(repo), "api.unknown_symbol")
    assert f["line"] == 1 and "`loadd`" in f["message"] and "load" in f["hint"]


def test_js_missing_default_export_and_missing_file_are_reported(repo):
    base(repo, {"src/lib.ts": "export const a = 1;\n"})
    change(repo, {"src/app.ts": "import lib from './lib';\nimport x from './gone';\nimport { y } from '../src/nowhere';\n"})
    assert hits(deep(repo), "api.unknown_symbol") == [("src/app.ts", 1), ("src/app.ts", 2), ("src/app.ts", 3)]


def test_js_assets_packages_and_dynamic_modules_are_never_judged(repo):
    base(repo, {"src/styles.css": "a {}\n", "src/logo.svg": "<svg/>\n", "src/star.ts": "export * from './lib';\n",
                "src/lib.ts": "export const a = 1;\n", "src/cjs.js": "module.exports = { anything: 1 };\n",
                "src/types.d.ts": "export type T = number;\n"})
    change(repo, {"src/app.ts": "import './styles.css';\nimport logo from './logo.svg';\nimport { whatever } from './star';\n"
                                "import { anything } from './cjs';\nimport { T } from './types';\n"
                                "import React from 'react';\nimport { nope } from '@org/pkg';\nimport { z } from '@/alias';\n"})
    assert not hits(deep(repo), "api.unknown_symbol")


def test_js_import_text_in_strings_comments_and_templates_is_not_an_import(repo):
    base(repo, {"src/lib.ts": "export const a = 1;\n"})
    change(repo, {"src/app.ts": "// import { nope } from './lib';\nconst s = \"import { nope } from './lib'\";\n"
                                "const t = `\nimport { nope } from './lib';\n`;\n/* import x from './gone'; */\n"})
    assert not hits(deep(repo), "api.unknown_symbol")


def test_js_index_and_index_files_resolve(repo):
    base(repo, {"src/ui/index.ts": "export const Button = 1;\n"})
    change(repo, {"src/app.ts": "import { Button, Missing } from './ui';\n"})
    assert one(deep(repo), "api.unknown_symbol")["message"].endswith("`Missing`")
