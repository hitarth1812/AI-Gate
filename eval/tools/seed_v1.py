#!/usr/bin/env python3
"""One-shot generator of corpus v1 (the families REAL, SEEDED and ADVERSARIAL) and eval/registry_fixture.json.

    py eval/tools/seed_v1.py            # (re)write eval/corpus/ and eval/holdout/ and the fixture

After this ran and a human approved the labels, the case directories are the source of truth and labels are FROZEN:
this script is kept only so v1 is reproducible and new rounds can copy its helpers. Do not re-run it over a corpus whose
labels were changed on purpose (it would overwrite them). New cases go in by hand or by a new tools/seed_vN.py.

Every expectation is written from the intent of the case ("this injected mistake should be caught", "this look-alike is
fine"), never copied from what the tool printed. Provider-shaped secrets are written as @@AWS@@ etc. (see run.py).
"""
from __future__ import annotations

import json
import shutil
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CORPUS, HOLDOUT = HERE / "corpus", HERE / "holdout"
TESTS_FIXTURE = HERE.parent / "tests" / "registry_fixture.json"
CASES = []


def holdout_of(case_id: str) -> bool:
    return zlib.crc32(case_id.encode()) % 5 == 0          # a stable 20%


def tq(s) -> str:
    return json.dumps(s, ensure_ascii=False)


def line_of(text: str, needle: str) -> int:
    for i, ln in enumerate(text.split("\n"), 1):
        if needle in ln:
            return i
    raise SystemExit(f"needle {needle!r} not found")


def emit(id, family, language, origin, before, after, fire=(), notfire=(), who="both", messages=(), mode="staged",
         status="frozen", contested=False, extra="", note="", delete=(), renames=()):
    """fire: (rule, path, needle|None, action|None, agent_action|None); notfire: (rule, path)."""
    d = (HOLDOUT if holdout_of(id) else CORPUS) / id
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    out = [f"id = {tq(id)}", f"source = {tq(family)}", f"language = {tq(language)}", f"origin = {tq(origin)}",
           f"who = {tq(who)}", f"mode = {tq(mode)}", f"label_status = {tq(status)}"]
    if contested:
        out.append("contested = true")
    if note:
        out.append(f"note = {tq(note)}")
    if messages:
        out.append("messages = [" + ", ".join(tq(m) for m in messages) + "]")
    if delete:
        out.append("delete = [" + ", ".join(tq(p) for p in delete) + "]")
    for rn in renames:
        out += ["", "[[rename]]", f"from = {tq(rn[0])}", f"to = {tq(rn[1])}"]
    for rule, path, needle, action, agent_action, tol in fire:
        out += ["", "[[must_fire]]", f"rule = {tq(rule)}", f"path = {tq(path)}"]
        if needle:
            text = after.get(path, before.get(path))
            out.append(f"line = {line_of(text, needle)}")
            if tol:
                out.append(f"tol = {tol}")
        if action:
            out.append(f"action = {tq(action)}")
        if agent_action:
            out.append(f"agent_action = {tq(agent_action)}")
    for rule, path in notfire:
        out += ["", "[[must_not_fire]]", f"rule = {tq(rule)}", f"path = {tq(path)}"]
    if extra:
        out += ["", extra.strip("\n")]
    (d / "case.toml").write_bytes(("\n".join(out) + "\n").encode("utf-8"))      # LF on every OS (git must not rewrite corpus bytes)
    for sub, files in (("before", before), ("after", after)):
        for rel, text in files.items():
            p = d / sub / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(text.encode("utf-8"))
    CASES.append(id)


def F(rule, path, needle=None, action=None, agent_action=None, tol=0):
    return (rule, path, needle, action, agent_action, tol)


# =============================================================================================== registry fixture

def write_fixture():
    fx = json.loads(TESTS_FIXTURE.read_text(encoding="utf-8"))
    old = {"created": "2015-01-01T00:00:00Z"}
    for name in ("flaskk", "numpyy", "pandass"):
        fx["pypi"][name] = {**old, "versions": {"1.0": "2019-01-01T00:00:00Z"}, "latest": "1.0", "downloads": 15}
    fx["pypi"]["pytest"] = {**old, "versions": {"8.3.3": "2024-09-10T00:00:00Z"}, "latest": "8.3.3"}
    for name in ("lodahs", "expresss", "reactt"):
        fx["npm"][name] = {**old, "versions": {"1.0.0": "2019-01-01T00:00:00Z"}, "latest": "1.0.0", "downloads": 12}
    t = "2024-01-01T00:00:00Z"
    fx["go"]["github.com/example/otelsdk"] = {**old, "versions": {"v1.37.0": t, "v1.38.0": "2025-02-01T00:00:00Z"},
                                              "latest": "v1.38.0", "vulns": {"v1.37.0": ["GHSA-test-otel-0001"], "v1.38.0": []},
                                              "fixed": {"v1.37.0": "v1.38.0"}}
    fx["go"]["github.com/example/cryptolib"] = {**old, "versions": {"v0.53.0": t, "v0.54.0": "2025-02-01T00:00:00Z"},
                                                "latest": "v0.54.0", "vulns": {"v0.53.0": ["GO-TEST-0001", "GO-TEST-0002"]},
                                                "fixed": {"v0.53.0": "v0.54.0"}}
    for i, name in enumerate(("dockerlib", "compresslib", "archivelib")):
        fx["go"][f"github.com/example/{name}"] = {**old, "versions": {"v1.0.0": t, "v1.1.0": "2025-02-01T00:00:00Z"},
                                                  "latest": "v1.1.0", "vulns": {"v1.0.0": [f"GHSA-test-ind-{i}"]},
                                                  "fixed": {"v1.0.0": "v1.1.0"}}
    (HERE / "registry_fixture.json").write_text(json.dumps(fx, indent=1) + "\n", encoding="utf-8")


# =============================================================================================== seeded: python

PY_BASE = {
    "app/__init__.py": "",
    "app/util.py": "def parse_amount(text):\n    return int(text)\n\n\ndef slugify(title, *, lower=True):\n"
                   "    return title.lower() if lower else title\n",
    "app/orders.py": "from app.util import parse_amount\n\n\ndef compute_total(items, tax):\n    total = sum(items)\n"
                     "    return total + total * tax\n\n\ndef load_amounts(lines):\n    return [parse_amount(x) for x in lines]\n",
    "app/report.py": "from app.orders import compute_total\nfrom app.util import slugify\n\n\ndef summary(items):\n"
                     "    return compute_total(items, 0.1)\n\n\ndef headline(title):\n    return slugify(title, lower=False)\n",
    "app/store.py": "def load(path):\n    try:\n        with open(path) as fh:\n            return fh.read()\n"
                    "    except FileNotFoundError:\n        return None\n    except PermissionError:\n        raise\n",
    "tests/test_orders.py": "from app.orders import compute_total, load_amounts\n\n\ndef test_total():\n"
                            "    assert compute_total([1, 2], 0.0) == 3\n    assert compute_total([10], 0.5) == 15\n\n\n"
                            "def test_empty():\n    assert compute_total([], 0.0) == 0\n    assert load_amounts([]) == []\n",
    "requirements.txt": "requests==2.32.3\n",
}
PEM = "@@PEMBEGIN@@\n@@PEMBODY@@\n@@PEMEND@@\n"


def py(id, after, fire=(), notfire=(), **kw):
    emit(id, "seeded", "python", "seeded: clean python base + one injected AI failure", PY_BASE, after, fire, notfire, **kw)


def seed_python():
    P = "seed-py-"
    # hallucinated packages (manifest and import)
    py(P + "halluc-req-1", {"requirements.txt": PY_BASE["requirements.txt"] + "fastjsonx-utils==1.0.0\n"},
       [F("deps.hallucinated", "requirements.txt", "fastjsonx-utils", "block", "block")])
    py(P + "halluc-req-2", {"requirements.txt": PY_BASE["requirements.txt"] + "pydantic-validator-pro>=2\n"},
       [F("deps.hallucinated", "requirements.txt", "pydantic-validator-pro", "block", "block")])
    py(P + "halluc-import-1", {"app/api.py": "import fastjsonx\n\n\ndef parse(text):\n    return fastjsonx.loads(text)\n"},
       [F("deps.hallucinated", "app/api.py", "import fastjsonx", "block", "block")])
    py(P + "halluc-import-2", {"app/api.py": "from quickparse_ai import parse\n\n\ndef run(text):\n    return parse(text)\n"},
       [F("deps.hallucinated", "app/api.py", "quickparse_ai", "block", "block")])
    # typosquats of popular packages
    for n, name in enumerate(("flaskk", "numpyy", "pandass"), 1):
        py(P + f"typosquat-{n}", {"requirements.txt": PY_BASE["requirements.txt"] + f"{name}==1.0\n"},
           [F("deps.typosquat", "requirements.txt", name)])
    # removed assertions / deleted test
    t = PY_BASE["tests/test_orders.py"]
    py(P + "assert-removed-1", {"tests/test_orders.py": t.replace("    assert compute_total([10], 0.5) == 15\n", "")},
       [F("tests.assertions_removed", "tests/test_orders.py")])
    py(P + "assert-removed-2", {"tests/test_orders.py": t.replace("    assert compute_total([1, 2], 0.0) == 3\n", "")
                                .replace("    assert compute_total([], 0.0) == 0\n", "")},
       [F("tests.assertions_removed", "tests/test_orders.py")])
    py(P + "assert-to-pass", {"tests/test_orders.py": t.replace("    assert compute_total([10], 0.5) == 15\n", "    pass\n")},
       [F("tests.assertions_removed", "tests/test_orders.py")])
    py(P + "test-deleted", {"tests/test_orders.py": t.split("\n\ndef test_empty")[0].rstrip("\n") + "\n"},
       [F("tests.deleted", "tests/test_orders.py")])
    # skipped tests
    sk = t.replace("def test_total", "import pytest\n\n\n@pytest.mark.skip(reason=\"fails after the refactor\")\ndef test_total")
    py(P + "skip-mark", {"tests/test_orders.py": sk}, [F("tests.skip_added", "tests/test_orders.py", "pytest.mark.skip", "fix", "fix", tol=2)])
    py(P + "skip-call", {"tests/test_orders.py": t.replace("def test_empty():\n", "def test_empty():\n    import pytest\n    pytest.skip(\"todo\")\n")},
       [F("tests.skip_added", "tests/test_orders.py", "pytest.skip", "fix", "fix", tol=2)])
    py(P + "skip-unittest", {"tests/test_orders.py": "import unittest\n\n\nclass T(unittest.TestCase):\n    @unittest.skip(\"flaky\")\n"
                             "    def test_total(self):\n        self.assertEqual(1, 1)\n"},
       [F("tests.skip_added", "tests/test_orders.py", "unittest.skip", "fix", "fix", tol=2)])
    py(P + "skip-xfail", {"tests/test_orders.py": t.replace("def test_total", "import pytest\n\n\n@pytest.mark.xfail\ndef test_total")},
       [F("tests.skip_added", "tests/test_orders.py", "pytest.mark.xfail", "fix", "fix", tol=2)])
    py(P + "skip-env-conditional", {"tests/test_orders.py": t.replace("def test_total", "import os\nimport pytest\n\n\n"
                                    "@pytest.mark.skipif(not os.environ.get(\"DATABASE_URL\"), reason=\"DATABASE_URL is not set\")\ndef test_total")},
       [F("tests.skip_added", "tests/test_orders.py", "skipif", "warn", "fix", tol=2)], note="environment-gated: a warning for a person")
    # invented API symbols
    o = PY_BASE["app/orders.py"]
    py(P + "invented-name", {"app/orders.py": o.replace("import parse_amount", "import parse_amount, compute_discount")
                             + "\n\ndef discounted(items, tax):\n    return compute_discount(compute_total(items, tax))\n"},
       [F("api.unknown_symbol", "app/orders.py", "compute_discount", "warn", "fix")])
    py(P + "invented-attr", {"app/orders.py": "import app.util as util\n" + o + "\n\ndef label(x):\n    return util.format_money(x)\n"},
       [F("api.unknown_symbol", "app/orders.py", "util.format_money", "warn", "fix")])
    py(P + "invented-module", {"app/orders.py": "from app.helpers import tidy\n" + o + "\n\ndef clean(x):\n    return tidy(x)\n"},
       [F("api.unknown_symbol", "app/orders.py", "app.helpers", "warn", "fix")])
    py(P + "invented-name-2", {"app/report.py": PY_BASE["app/report.py"].replace("import slugify", "import slugify, to_title")
                               + "\n\ndef pretty(t):\n    return to_title(t)\n"},
       [F("api.unknown_symbol", "app/report.py", "to_title", "warn", "fix")])
    # wrong call arity
    py(P + "arity-missing", {"app/report.py": PY_BASE["app/report.py"].replace("compute_total(items, 0.1)", "compute_total(items)")},
       [F("api.bad_call", "app/report.py", "compute_total(items)", "warn", "fix")])
    py(P + "arity-positional-kwonly", {"app/report.py": PY_BASE["app/report.py"].replace("slugify(title, lower=False)", "slugify(title, False)")},
       [F("api.bad_call", "app/report.py", "slugify(title, False)", "warn", "fix")])
    py(P + "arity-too-many", {"app/orders.py": o.replace("parse_amount(x)", "parse_amount(x, 10, 2)")},
       [F("api.bad_call", "app/orders.py", "parse_amount(x, 10, 2)", "warn", "fix")])
    # broken callers (a signature changed, a caller was not updated)
    py(P + "broken-caller-new-param", {"app/orders.py": o.replace("def compute_total(items, tax):", "def compute_total(items, tax, currency):")},
       [F("api.broken_caller", "app/report.py", "compute_total(items, 0.1)", "warn", "fix")])
    py(P + "broken-caller-dropped-kw", {"app/util.py": PY_BASE["app/util.py"].replace("def slugify(title, *, lower=True):\n    return title.lower() if lower else title\n",
                                        "def slugify(title):\n    return title.lower()\n")},
       [F("api.broken_caller", "app/report.py", "slugify(title, lower=False)", "warn", "fix")])
    # error handling removed or narrowed
    s = PY_BASE["app/store.py"]
    py(P + "error-swallowed", {"app/store.py": s.replace("    except FileNotFoundError:\n        return None\n    except PermissionError:\n        raise\n",
                                                          "    except Exception:\n        pass\n")},
       [F("code.error_check_removed", "app/store.py", "except Exception", "warn", "fix")])
    py(P + "error-reraise-removed", {"app/store.py": s.replace("    except PermissionError:\n        raise\n", "")},
       [F("code.error_check_removed", "app/store.py", None, "warn", "fix")])
    py(P + "error-handler-dropped", {"app/store.py": "def load(path):\n    with open(path) as fh:\n        return fh.read()\n"},
       [F("code.error_check_removed", "app/store.py", None, "warn", "fix")])
    # leaked provider keys
    py(P + "leak-aws", {"app/config.py": "AWS_KEY = \"@@AWS@@\"\n"}, [F("secrets.hardcoded", "app/config.py", "AWS_KEY", "block", "block")])
    py(P + "leak-github", {"scripts/deploy.py": "TOKEN = \"@@GHTOKEN@@\"\n"}, [F("secrets.hardcoded", "scripts/deploy.py", "TOKEN", "block", "block")])
    py(P + "leak-stripe", {"app/settings.py": "STRIPE_KEY = \"@@STRIPE@@\"\n"}, [F("secrets.hardcoded", "app/settings.py", "STRIPE_KEY", "block", "block")])
    # 0.5.2 (LABEL_CHANGES A5): one key file gives one BLOCK; the secrets.env_file duplicate is a must_not_fire
    py(P + "leak-pem", {"keys/server.pem": PEM}, [F("secrets.hardcoded", "keys/server.pem", "BEGIN", "block", "block")],
       [("secrets.env_file", "keys/server.pem")])
    # weak crypto
    py(P + "weak-md5", {"app/auth.py": "import hashlib\n\n\ndef hash_password(password):\n    return hashlib.md5(password.encode()).hexdigest()\n"},
       [F("security.weak_crypto", "app/auth.py", "hashlib.md5", "warn", "warn")])
    py(P + "weak-sha1", {"app/auth.py": "import hashlib\n\n\ndef sign_token(token):\n    return hashlib.sha1(token.encode()).hexdigest()\n"},
       [F("security.weak_crypto", "app/auth.py", "hashlib.sha1", "warn", "warn")])
    py(P + "weak-random-token", {"app/auth.py": "import random\nimport string\n\n\ndef new_token():\n    chars = string.ascii_letters\n"
                                 "    token = \"\".join(random.choice(chars) for _ in range(32))\n    return token\n"},
       [F("security.weak_crypto", "app/auth.py", "random.choice", "warn", "warn")])
    py(P + "weak-random-secret", {"app/auth.py": "import random\n\n\ndef make_secret():\n    secret = random.randint(0, 10 ** 12)\n    return secret\n"},
       [F("security.weak_crypto", "app/auth.py", "random.randint", "warn", "warn")])
    # claims the diff does not support
    changed = {"app/orders.py": o.replace("total + total * tax", "round(total + total * tax, 2)")}
    py(P + "claim-add-tests", dict(changed), [F("review.claim_mismatch", "(commit)", None, "warn", "warn")],
       messages=["add tests for compute_total"])
    py(P + "claim-fix-other", {"app/util.py": PY_BASE["app/util.py"].replace("int(text)", "int(text.strip())")},
       [F("review.claim_mismatch", "(commit)", None, "warn", "warn")], messages=["fix compute_total rounding"])
    py(P + "claim-refactor-signature", {"app/orders.py": o.replace("def compute_total(items, tax):", "def compute_total(items, tax, currency=\"USD\"):")
                                        .replace("parse_amount(x)", "parse_amount(x, 10)")},
       [F("review.claim_mismatch", "(commit)", None, "warn", "warn")], messages=["refactor only, no behaviour change"])


# =============================================================================================== seeded: go

GO_BASE = {
    "go.mod": "module example.com/shop\n\ngo 1.22\n",
    "shop/shop.go": "package shop\n\nimport \"strconv\"\n\nfunc Total(items []int, tax int) int {\n\tt := 0\n\tfor _, i := range items {\n\t\tt += i\n\t}\n"
                    "\treturn t + t*tax/100\n}\n\nfunc Parse(s string) (int, error) {\n\tn, err := strconv.Atoi(s)\n\tif err != nil {\n\t\treturn 0, err\n\t}\n\treturn n, nil\n}\n",
    "store/store.go": "package store\n\nimport \"os\"\n\nfunc Open(path string, flags ...int) error {\n\treturn nil\n}\n\nfunc Pair(a, b int) (int, error) {\n\treturn a, nil\n}\n\n"
                      "func Load(p string) ([]byte, error) {\n\tb, err := os.ReadFile(p)\n\tif err != nil {\n\t\treturn nil, err\n\t}\n\treturn b, nil\n}\n\nconst Version = \"1\"\n",
    "cmd/app/main.go": "package main\n\nimport (\n\t\"example.com/shop/shop\"\n\t\"example.com/shop/store\"\n)\n\nfunc main() {\n\t_, _ = store.Pair(1, 2)\n\t_ = shop.Total([]int{1}, 5)\n}\n",
    "shop/shop_test.go": "package shop\n\nimport \"testing\"\n\nfunc TestTotal(t *testing.T) {\n\tif got := Total([]int{1, 2}, 0); got != 3 {\n\t\tt.Errorf(\"got %d\", got)\n\t}\n"
                         "\tif got := Total([]int{10}, 50); got != 15 {\n\t\tt.Errorf(\"got %d\", got)\n\t}\n}\n\nfunc TestParse(t *testing.T) {\n\tn, err := Parse(\"4\")\n\tif err != nil {\n\t\tt.Fatalf(\"err %v\", err)\n\t}\n"
                         "\tif n != 4 {\n\t\tt.Errorf(\"got %d\", n)\n\t}\n}\n",
}


def go(id, after, fire=(), notfire=(), **kw):
    emit(id, "seeded", "go", "seeded: clean go base + one injected AI failure", GO_BASE, after, fire, notfire, **kw)


def seed_go():
    P = "seed-go-"
    gm = GO_BASE["go.mod"]
    go(P + "halluc-mod-1", {"go.mod": gm + "\nrequire github.com/nonexistent-ai/fastjson v1.0.0\n"},
       [F("deps.hallucinated", "go.mod", "nonexistent-ai/fastjson", "block", "block")])
    go(P + "halluc-mod-2", {"go.mod": gm + "\nrequire golang.org/x/fastcrypto v0.1.0\n"},
       [F("deps.hallucinated", "go.mod", "golang.org/x/fastcrypto", "block", "block")])
    go(P + "halluc-import", {"shop/api.go": "package shop\n\nimport \"github.com/ghostorg/quickparse\"\n\nfunc Run(s string) string {\n\treturn quickparse.Parse(s)\n}\n"},
       [F("deps.hallucinated", "shop/api.go", "ghostorg/quickparse", "block", "block")])
    t = GO_BASE["shop/shop_test.go"]
    go(P + "assert-removed-1", {"shop/shop_test.go": t.replace("\tif got := Total([]int{10}, 50); got != 15 {\n\t\tt.Errorf(\"got %d\", got)\n\t}\n", "")},
       [F("tests.assertions_removed", "shop/shop_test.go")])
    go(P + "assert-removed-2", {"shop/shop_test.go": t.replace("\tif n != 4 {\n\t\tt.Errorf(\"got %d\", n)\n\t}\n", "")},
       [F("tests.assertions_removed", "shop/shop_test.go")])
    go(P + "assert-removed-3", {"shop/shop_test.go": t.replace("\tif err != nil {\n\t\tt.Fatalf(\"err %v\", err)\n\t}\n", "\t_ = err\n")},
       [F("tests.assertions_removed", "shop/shop_test.go")])
    go(P + "test-deleted", {"shop/shop_test.go": t.split("\nfunc TestParse")[0]}, [F("tests.deleted", "shop/shop_test.go")])
    for n, (name, line, act) in enumerate((("bare", "t.Skip()", ("fix", "fix")), ("flaky", "t.Skip(\"flaky on CI\")", ("fix", "fix")),
                                           ("skipnow", "t.SkipNow()", ("fix", "fix")), ("broken", "t.Skip(\"broken after the refactor\")", ("fix", "fix")),
                                           ("env", "t.Skip(\"DATABASE_URL is not set\")", ("warn", "fix"))), 1):
        go(P + f"skip-{name}", {"shop/shop_test.go": t.replace("func TestParse(t *testing.T) {\n", "func TestParse(t *testing.T) {\n\t" + line + "\n")},
           [F("tests.skip_added", "shop/shop_test.go", line, *act)])
    cm = GO_BASE["cmd/app/main.go"]
    go(P + "invented-member", {"cmd/app/main.go": cm.replace("_, _ = store.Pair(1, 2)", "_, _ = store.Pair(1, 2)\n\t_ = store.Opn(\"x\")")},
       [F("api.unknown_symbol", "cmd/app/main.go", "store.Opn", "warn", "fix")])
    go(P + "invented-member-2", {"cmd/app/main.go": cm.replace("_ = shop.Total([]int{1}, 5)", "_ = shop.TotalWithTax([]int{1}, 5)")},
       [F("api.unknown_symbol", "cmd/app/main.go", "shop.TotalWithTax", "warn", "fix")])
    go(P + "invented-member-3", {"cmd/app/main.go": cm.replace("_, _ = store.Pair(1, 2)", "_, _ = store.Pair(1, 2)\n\t_ = store.Defaults")},
       [F("api.unknown_symbol", "cmd/app/main.go", "store.Defaults", "warn", "fix")])
    go(P + "arity-pair", {"cmd/app/main.go": cm.replace("store.Pair(1, 2)", "store.Pair(1)")}, [F("api.bad_call", "cmd/app/main.go", "store.Pair(1)", "warn", "fix")])
    go(P + "arity-total", {"cmd/app/main.go": cm.replace("shop.Total([]int{1}, 5)", "shop.Total([]int{1})")},
       [F("api.bad_call", "cmd/app/main.go", "shop.Total([]int{1})", "warn", "fix")])
    go(P + "arity-parse", {"shop/api.go": "package shop\n\nfunc Run() {\n\t_, _ = Parse(\"4\", \"5\")\n}\n"}, [F("api.bad_call", "shop/api.go", "Parse(\"4\", \"5\")", "warn", "fix")])
    sh = GO_BASE["shop/shop.go"]
    go(P + "broken-caller-new-param", {"store/store.go": GO_BASE["store/store.go"].replace("func Pair(a, b int)", "func Pair(a, b, c int)")},
       [F("api.broken_caller", "cmd/app/main.go", "store.Pair(1, 2)", "warn", "fix")])
    go(P + "broken-caller-total", {"shop/shop.go": sh.replace("func Total(items []int, tax int) int", "func Total(items []int, tax, discount int) int")},
       [F("api.broken_caller", "cmd/app/main.go", "shop.Total(", "warn", "fix")])
    st = GO_BASE["store/store.go"]
    go(P + "error-check-removed", {"store/store.go": st.replace("\tif err != nil {\n\t\treturn nil, err\n\t}\n", "")},
       [F("code.error_check_removed", "store/store.go", None, "warn", "fix")])
    go(P + "error-blank-assign", {"cmd/app/main.go": cm.replace("_ = shop.Total([]int{1}, 5)", "_ = shop.Total([]int{1}, 5)\n\t_ = store.Open(\"f\")")},
       [F("code.error_check_removed", "cmd/app/main.go", "store.Open", "warn", "fix")])
    go(P + "error-blank-multi", {"cmd/app/main.go": cm.replace("_ = shop.Total([]int{1}, 5)", "_ = shop.Total([]int{1}, 5)\n\tn, _ := shop.Parse(\"4\")\n\t_ = n")},
       [F("code.error_check_removed", "cmd/app/main.go", "n, _ :=", "warn", "fix")])
    go(P + "error-parse-ignored", {"shop/shop.go": sh.replace("\tif err != nil {\n\t\treturn 0, err\n\t}\n", "")},
       [F("code.error_check_removed", "shop/shop.go", None, "warn", "fix")])
    go(P + "leak-aws", {"shop/config.go": "package shop\n\nconst awsKey = \"@@AWS@@\"\n"}, [F("secrets.hardcoded", "shop/config.go", "awsKey", "block", "block")])
    go(P + "leak-github", {"shop/config.go": "package shop\n\nconst token = \"@@GHTOKEN@@\"\n"}, [F("secrets.hardcoded", "shop/config.go", "token", "block", "block")])
    go(P + "leak-stripe", {"shop/pay.go": "package shop\n\nconst stripeKey = \"@@STRIPE@@\"\n"}, [F("secrets.hardcoded", "shop/pay.go", "stripeKey", "block", "block")])
    go(P + "leak-pem-rawstring", {"shop/key.go": "package shop\n\nconst key = `@@PEMBEGIN@@\n@@PEMBODY@@\n@@PEMEND@@`\n"},
       [F("secrets.hardcoded", "shop/key.go", "BEGIN", "block", "block")])
    go(P + "weak-math-rand-token", {"shop/auth.go": "package shop\n\nimport \"math/rand\"\n\nfunc NewToken() int {\n\ttoken := rand.Int()\n\treturn token\n}\n"},
       [F("security.weak_crypto", "shop/auth.go", "rand.Int()", "warn", "warn")])
    go(P + "weak-math-rand-session", {"shop/auth.go": "package shop\n\nimport \"math/rand\"\n\nfunc NewSession() int64 {\n\tsessionID := rand.Int63()\n\treturn sessionID\n}\n"},
       [F("security.weak_crypto", "shop/auth.go", "rand.Int63", "warn", "warn")])
    go(P + "weak-md5-password", {"shop/auth.go": "package shop\n\nimport \"crypto/md5\"\n\nfunc HashPassword(password string) [16]byte {\n\treturn md5.Sum([]byte(password))\n}\n"},
       [F("security.weak_crypto", "shop/auth.go", "md5.Sum", "warn", "warn")])
    go(P + "weak-sha1-secret", {"shop/auth.go": "package shop\n\nimport \"crypto/sha1\"\n\nfunc HashSecret(secret string) [20]byte {\n\treturn sha1.Sum([]byte(secret))\n}\n"},
       [F("security.weak_crypto", "shop/auth.go", "sha1.Sum", "warn", "warn")])
    go(P + "claim-add-tests", {"shop/shop.go": sh.replace("t + t*tax/100", "(t * (100 + tax)) / 100")}, [F("review.claim_mismatch", "(commit)", None, "warn", "warn")],
       messages=["add tests for Total"])
    go(P + "claim-fix-other", {"store/store.go": st.replace("const Version = \"1\"", "const Version = \"2\"")},
       [F("review.claim_mismatch", "(commit)", None, "warn", "warn")], messages=["fix Total rounding"])
    go(P + "claim-refactor-signature", {"shop/shop.go": sh.replace("func Total(items []int, tax int) int", "func Total(items []int, tax int, round bool) int")},
       [F("review.claim_mismatch", "(commit)", None, "warn", "warn")], messages=["refactor only, no behaviour change"])


# =============================================================================================== seeded: javascript / typescript

TS_BASE = {
    "package.json": json.dumps({"name": "shop", "version": "1.0.0", "private": True, "scripts": {"test": "jest"}}, indent=2) + "\n",
    "src/lib.ts": "export function total(items: number[], tax: number): number {\n  const t = items.reduce((a, b) => a + b, 0);\n  return t + t * tax;\n}\n\n"
                  "export const helper = (x: number) => x * 2;\n\nexport default function main() {}\n",
    "src/api.ts": "export async function fetchAll(url: string) {\n  const r = await fetch(url);\n  return r.json().catch(() => null);\n}\n",
    "src/app.ts": "import { total, helper } from './lib';\n\nexport const run = () => helper(total([1, 2], 0.1));\n",
    "src/lib.test.ts": "import { total } from './lib';\n\ntest('total', () => {\n  expect(total([1, 2], 0)).toBe(3);\n  expect(total([10], 0.5)).toBe(15);\n});\n\n"
                       "test('empty', () => {\n  expect(total([], 0)).toBe(0);\n  expect(total([5], 0)).toBe(5);\n});\n",
}


def ts(id, after, fire=(), notfire=(), **kw):
    emit(id, "seeded", "typescript", "seeded: clean typescript base + one injected AI failure", TS_BASE, after, fire, notfire, **kw)


def seed_ts():
    P = "seed-ts-"
    pj = json.loads(TS_BASE["package.json"])
    def with_dep(name, ver="^1.0.0"):
        return json.dumps({**pj, "dependencies": {name: ver}}, indent=2) + "\n"
    ts(P + "halluc-dep-1", {"package.json": with_dep("ts-quickparse-ai")}, [F("deps.hallucinated", "package.json", "ts-quickparse-ai", "block", "block")])
    ts(P + "halluc-dep-2", {"package.json": with_dep("react-super-validate")}, [F("deps.hallucinated", "package.json", "react-super-validate", "block", "block")])
    ts(P + "halluc-import-1", {"src/app.ts": "import parse from 'ts-fast-validate-ai';\n" + TS_BASE["src/app.ts"]},
       [F("deps.hallucinated", "src/app.ts", "ts-fast-validate-ai", "block", "block")])
    ts(P + "halluc-import-2", {"src/app.ts": TS_BASE["src/app.ts"] + "import { z } from 'zod-extras-pro';\nexport const s = z;\n"},
       [F("deps.hallucinated", "src/app.ts", "zod-extras-pro", "block", "block")])
    for n, name in enumerate(("lodahs", "expresss", "reactt"), 1):
        ts(P + f"typosquat-{n}", {"package.json": with_dep(name)}, [F("deps.typosquat", "package.json", name)])
    t = TS_BASE["src/lib.test.ts"]
    ts(P + "assert-removed-1", {"src/lib.test.ts": t.replace("  expect(total([10], 0.5)).toBe(15);\n", "")}, [F("tests.assertions_removed", "src/lib.test.ts")])
    ts(P + "assert-removed-2", {"src/lib.test.ts": t.replace("  expect(total([], 0)).toBe(0);\n", "").replace("  expect(total([5], 0)).toBe(5);\n", "")},
       [F("tests.assertions_removed", "src/lib.test.ts")])
    ts(P + "assert-removed-3", {"src/lib.test.ts": t.replace("  expect(total([1, 2], 0)).toBe(3);\n", "  total([1, 2], 0);\n")}, [F("tests.assertions_removed", "src/lib.test.ts")])
    ts(P + "test-deleted", {"src/lib.test.ts": t.split("\ntest('empty'")[0].rstrip("\n") + "\n"}, [F("tests.deleted", "src/lib.test.ts")])
    for name, old, new, act in (("it-skip", "test('total'", "it.skip('total'", ("fix", "fix")), ("test-skip", "test('total'", "test.skip('total'", ("fix", "fix")),
                                ("xit", "test('empty'", "xit('empty'", ("fix", "fix")), ("describe-skip", "test('empty'", "describe.skip('empty'", ("fix", "fix"))):
        ts(P + f"skip-{name}", {"src/lib.test.ts": t.replace(old, new)}, [F("tests.skip_added", "src/lib.test.ts", new, *act)])
    ts(P + "skip-env-conditional", {"src/lib.test.ts": t.replace("test('empty', () => {\n", "test('empty', () => {\n  test.skip(!process.env.API_URL, 'needs API_URL');\n")},
       [F("tests.skip_added", "src/lib.test.ts", "test.skip(!process.env", "warn", "fix")])
    ts(P + "skip-fixme-conditional", {"src/lib.test.ts": t.replace("test('empty', () => {\n", "test('empty', () => {\n  test.fixme(process.platform === 'win32', 'differs on windows');\n")},
       [F("tests.skip_added", "src/lib.test.ts", "test.fixme", "warn", "fix")])
    ts(P + "invented-export", {"src/app.ts": TS_BASE["src/app.ts"].replace("import { total, helper }", "import { total, helper, loadd }") + "export const x = loadd;\n"},
       [F("api.unknown_symbol", "src/app.ts", "loadd", "warn", "fix")])
    ts(P + "invented-file", {"src/app.ts": "import { thing } from './gone';\n" + TS_BASE["src/app.ts"] + "export const y = thing;\n"},
       [F("api.unknown_symbol", "src/app.ts", "./gone", "warn", "fix")])
    ts(P + "invented-export-2", {"src/app.ts": TS_BASE["src/app.ts"].replace("{ total, helper }", "{ total, helperr }").replace("helper(", "helperr(")},
       [F("api.unknown_symbol", "src/app.ts", "helperr", "warn", "fix")])
    a = TS_BASE["src/api.ts"]
    ts(P + "error-await-dropped", {"src/api.ts": a.replace("await fetch(url)", "fetch(url)")}, [F("code.error_check_removed", "src/api.ts", "fetch(url)", "warn", "fix")])
    ts(P + "error-catch-dropped", {"src/api.ts": a.replace(".catch(() => null)", "")}, [F("code.error_check_removed", "src/api.ts", None, "warn", "fix")])
    ts(P + "error-empty-catch", {"src/api.ts": "export async function fetchAll(url: string) {\n  try {\n    const r = await fetch(url);\n    return r.json();\n  } catch (e) {\n  }\n}\n"},
       [F("code.swallowed_exception", "src/api.ts", "catch (e)", "fix", "fix")])
    ts(P + "leak-aws", {"src/config.ts": "export const awsKey = '@@AWS@@';\n"}, [F("secrets.hardcoded", "src/config.ts", "awsKey", "block", "block")])
    ts(P + "leak-github", {"src/config.ts": "export const token = '@@GHTOKEN@@';\n"}, [F("secrets.hardcoded", "src/config.ts", "token", "block", "block")])
    ts(P + "leak-stripe", {"src/pay.ts": "export const stripeKey = '@@STRIPE@@';\n"}, [F("secrets.hardcoded", "src/pay.ts", "stripeKey", "block", "block")])
    ts(P + "leak-slack", {"src/notify.ts": "export const slack = '@@SLACK@@';\n"}, [F("secrets.hardcoded", "src/notify.ts", "slack", "block", "block")])
    ts(P + "weak-math-random-token", {"src/auth.ts": "export const newToken = () => Math.random().toString(36).slice(2);\nexport const sessionToken = Math.random().toString(36);\n"},
       [F("security.weak_crypto", "src/auth.ts", "sessionToken", "warn", "warn")])
    ts(P + "weak-md5-password", {"src/auth.ts": "import { createHash } from 'crypto';\n\nexport const hashPassword = (password: string) => createHash('md5').update(password).digest('hex');\n"},
       [F("security.weak_crypto", "src/auth.ts", "createHash('md5')", "warn", "warn")])
    ts(P + "weak-sha1-secret", {"src/auth.ts": "import { createHash } from 'crypto';\n\nexport const hashSecret = (secret: string) => createHash('sha1').update(secret).digest('hex');\n"},
       [F("security.weak_crypto", "src/auth.ts", "createHash('sha1')", "warn", "warn")])
    ts(P + "weak-math-random-secret", {"src/auth.ts": "export function makeSecret(): string {\n  const secret = String(Math.random());\n  return secret;\n}\n"},
       [F("security.weak_crypto", "src/auth.ts", "Math.random", "warn", "warn")])
    ts(P + "claim-add-tests", {"src/lib.ts": TS_BASE["src/lib.ts"].replace("t + t * tax", "Math.round(t + t * tax)")}, [F("review.claim_mismatch", "(commit)", None, "warn", "warn")],
       messages=["add tests for total"])
    ts(P + "claim-fix-other", {"src/api.ts": a.replace("r.json()", "await r.json()")}, [F("review.claim_mismatch", "(commit)", None, "warn", "warn")], messages=["fix the total() rounding"])   # LABEL_CHANGES E
    ts(P + "claim-refactor-signature", {"src/lib.ts": TS_BASE["src/lib.ts"].replace("tax: number)", "tax: number, round: boolean)")},
       [F("review.claim_mismatch", "(commit)", None, "warn", "warn")], messages=["refactor only, no behaviour change"])


# =============================================================================================== real (ATLAS audits)

OS = "ATLAS_OS audit (aigate-lite 0.5.1 `audit`, 2026-11-13)"
WEB = "ATLAS_WEB_APPLICATION audit (aigate-lite 0.5.1 `audit`, 2026-11-13)"
NOCODE = "pattern rewritten; no ATLAS code"
GO_NOTEST = "package x\n\n"


def real(id, lang, ref, files, fire=(), notfire=(), who="both", contested=False, note="", before=None, which=OS):
    emit(id, "real", lang, f"{which}: {ref} ({NOCODE})", before or {"README.md": "# base\n"}, files, fire, notfire, who=who,
         status="proposed", contested=contested, note=note)


def go_file(body, imports=()):
    imp = "".join(f"import {i}\n" for i in imports)
    return GO_NOTEST + imp + ("\n" if imp else "") + body


def seed_real():
    R = "real-"
    # -- gosec / sha1 / nolint
    p = "internal/auth/credentials/screener.go"
    real(R + "go-hibp-sha1-nolint-reason", "go", f"{p}:77",
         {p: go_file("func Check(password string) [20]byte {\n\tsum := sha1.Sum([]byte(password)) //nolint:gosec // SHA-1 is the breach-check protocol, not a security control here\n\treturn sum\n}\n", ['"crypto/sha1"'])},
         [F("suppress.security", p, "nolint:gosec", "fix", "block")],
         note="DECIDED (LABEL_CHANGES A1): a justified //nolint:gosec (reason of 4+ words) is FIX for a person; agent BLOCK")
    p = "internal/auth/credentials/screener_test.go"
    real(R + "go-sha1-in-test-file", "go", f"{p}:15", {p: go_file("func TestSum(t *testing.T) {\n\tsum := sha1.Sum([]byte(password))\n\t_ = sum\n}\n", ['"crypto/sha1"', '"testing"'])},
         [F("security.weak_crypto", p, "sha1.Sum", "warn", "fix")], note="SHA-1 of a fixed test input in a _test.go file: warn for people")
    p = "internal/crypto/hash.go"
    real(R + "go-gosec-bare", "go", "internal/auth/credentials/screener.go:77 (reason removed)",
         {p: go_file("func H(b []byte) [16]byte {\n\treturn md5.Sum(b) //nolint:gosec\n}\n", ['"crypto/md5"'])},
         [F("suppress.security", p, "nolint:gosec", "block", "block")], note="bare //nolint:gosec silences the security scanner: BLOCK for everyone")
    # -- //nolint:errcheck on deferred rollback
    for n, (path, ref) in enumerate((("internal/auth/credentials/postgres.go", "internal/auth/credentials/postgres.go:39"), ("internal/auth/mfa/postgres.go", "internal/auth/mfa/postgres.go:73"),
                                     ("internal/auth/session/postgres.go", "internal/auth/session/postgres.go:107")), 1):
        real(R + f"go-rollback-nolint-sensitive-{n}", "go", ref, {path: go_file("func (s *Store) Save(tx Tx) error {\n\tdefer tx.Rollback() //nolint:errcheck // no-op after Commit\n\treturn tx.Commit()\n}\n")},
             [F("suppress.lint", path, "nolint:errcheck", "fix", "fix")], note="paths under auth/ are [paths] sensitive: one step stricter by design")
    path = "cmd/atlas/router_integration_test.go"
    real(R + "go-rollback-nolint-plain", "go", f"{path}:266", {path: go_file("func TestRouter(t *testing.T) {\n\tdefer tx.Rollback() //nolint:errcheck // no-op after commit\n}\n")},
         [F("suppress.lint", path, "nolint:errcheck", "warn", "warn")])
    path = "internal/store/plain.go"
    real(R + "go-nolint-bare-plain", "go", "pattern: bare //nolint", {path: go_file("func f() {\n\t_ = g() //nolint\n}\n")}, [F("suppress.lint", path, "nolint", "fix", "fix")])
    # -- skips
    skips = [("go-skip-data-fixture", "internal/api/drift_test.go:165", "internal/api/drift_test.go", "t.Skip(\"no undocumented routes in current fixture — skip sanity check\")", ("warn", "fix"),
              "data-gated skip: warn with the hint 'assert the precondition instead of skipping'"),
             ("go-skip-data-routes", "internal/api/drift_test.go:175", "internal/api/drift_test.go", "t.Skip(\"no registered routes to patch\")", ("fix", "fix"), "DECIDED (LABEL_CHANGES F a): an unconditional Go skip stays FIX for a person"),
             ("go-skip-goos", "cmd/atlas/graceful_shutdown_other_test.go:18", "cmd/atlas/graceful_shutdown_other_test.go",
              "t.Skipf(\"Linux-only graceful-shutdown assertions run in CI, not on %s\", runtime.GOOS)", ("warn", "fix"), "environment-gated"),
             ("go-skip-env-var", "cmd/atlas/router_integration_test.go:51", "cmd/atlas/router_integration_test.go",
              "t.Skip(\"ATLAS_DATABASE_HOST is not set; run this suite through CI or a local PostgreSQL\")", ("warn", "fix"), "environment-gated"),
             ("go-skip-pg-activity", "internal/auth/activity/integration_test.go:42", "internal/auth/activity/integration_test.go", "t.Skipf(\"no reachable PostgreSQL for integration test: %v\", err)", ("warn", "fix"), "environment-gated"),
             ("go-skip-pg-credentials", "internal/auth/credentials/integration_test.go:72", "internal/auth/credentials/integration_test.go", "t.Skipf(\"no reachable PostgreSQL for integration test: %v\", err)", ("warn", "fix"), "environment-gated"),
             ("go-skip-pg-events", "internal/events/postgres/idempotency_integration_test.go:51", "internal/events/postgres/idempotency_integration_test.go", "t.Skipf(\"no reachable PostgreSQL for integration tests: %v\", err)", ("warn", "fix"), "environment-gated"),
             ("go-skip-pg-orgs5", "internal/orgs/postgres/stage5_integration_test.go:68", "internal/orgs/postgres/stage5_integration_test.go", "t.Skipf(\"no reachable PostgreSQL for integration tests: %v\", err)", ("warn", "fix"), "environment-gated"),
             ("go-skip-pg-orgs6", "internal/orgs/postgres/stage6_integration_test.go:43", "internal/orgs/postgres/stage6_integration_test.go", "t.Skipf(\"no reachable PostgreSQL: %v\", err)", ("warn", "fix"), "environment-gated"),
             ("go-skip-pg-db3", "internal/platform/database/stage3_integration_test.go:61", "internal/platform/database/stage3_integration_test.go", "t.Skipf(\"no reachable PostgreSQL for integration tests: %v\", err)", ("warn", "fix"), "environment-gated"),
             ("go-skip-pg-load", "internal/githubapp/load_test.go:106", "internal/githubapp/load_test.go", "t.Skipf(\"no reachable PostgreSQL for load test: %v\", err)", ("warn", "fix"), "environment-gated"),
             ("go-skip-pg-corr", "internal/metrics/correlator/pr_lifecycle_consumer_test.go:56", "internal/metrics/correlator/pr_lifecycle_consumer_test.go", "t.Skipf(\"no reachable PostgreSQL for integration tests: %v\", err)", ("warn", "fix"), "environment-gated"),
             ("go-skip-bare-control", "pattern: unconditional skip (control)", "internal/x/x_test.go", "t.Skip()", ("fix", "fix"), "control: an unconditional skip stays a fix"),
             ("go-skip-flaky-control", "pattern: flaky skip (control)", "internal/x/y_test.go", "t.Skip(\"flaky, TODO\")", ("fix", "fix"), "control: a skip that says flaky stays a fix")]
    for id, ref, path, line, (pa, aa), note in skips:
        real(R + id, "go", ref, {path: go_file(f"func TestX(t *testing.T) {{\n\t{line}\n}}\n")}, [F("tests.skip_added", path, "t.Skip", pa, aa)], note=note)
    # -- secrets.generic: connection strings and test literals
    ci = "name: ci\non: push\njobs:\n  t:\n    env:\n      MIGRATE_URL: \"postgres://atlas:atlas@localhost:5432/atlas?sslmode=disable\"\n"
    real(R + "go-conn-localhost-ci", "yaml", ".github/workflows/ci.yml:145", {".github/workflows/ci.yml": ci}, notfire=[("secrets.generic", ".github/workflows/ci.yml")], who="person",
         note="a throwaway localhost database in CI config: information only for people (proposed); agents keep the warning")
    real(R + "go-conn-localhost-ci-2", "yaml", ".github/workflows/ci-stability.yml:39", {".github/workflows/ci-stability.yml": ci.replace("name: ci", "name: stability")},
         notfire=[("secrets.generic", ".github/workflows/ci-stability.yml")], who="person", note="same pattern, second workflow")
    path = "cmd/verify_p2/main.go"
    real(R + "go-conn-localhost-code", "go", f"{path}:37", {path: go_file("func main() {\n\tdbDSN := \"postgres://atlas:atlas@localhost:5432/atlas?sslmode=disable\"\n\t_ = dbDSN\n}\n")},
         [F("secrets.generic", path, "postgres://", "warn", "warn")], note="a localhost DSN in non-test code: a low warning")
    path = "internal/auth/identity/resolver_diagnostics_test.go"
    real(R + "go-conn-sprintf-test", "go", f"{path}:36", {path: go_file("func TestR(t *testing.T) {\n\tdb, _ := sql.Open(\"pgx\", fmt.Sprintf(\"postgres://atlas:atlas@%s/atlas?sslmode=disable\", addr))\n\t_ = db\n}\n")},
         notfire=[("secrets.generic", path)], who="person", note="the password is user=atlas in a test DSN built from an address: not a secret (proposed FP)")
    lits = [("go-test-password-literal", "internal/auth/credentials/screener_test.go:53", "internal/auth/credentials/screener_test.go", "password := \"clean-password-1234\"", ("warn", "fix")),
            ("go-test-clientsecret-field", "internal/auth/oauth/github/oauth_config_diagnostics_test.go:169", "internal/auth/oauth/github/oauth_config_diagnostics_test.go",
             "c := Config{ClientSecret: \"0123456789abcdef0123456789abcdef01234567\"} // 40 chars, shape only", ("warn", "fix")),
            ("go-test-const-secret", "internal/githubapp/load_test.go:30", "internal/githubapp/load_test.go", "const loadSecret = \"stage7-load-secret\"", ("warn", "fix")),
            ("go-test-const-password", "internal/orgs/postgres/stage9_integration_test.go:258", "internal/orgs/postgres/stage9_integration_test.go", "const password = \"stage9_unprivileged_role_pw\"", ("warn", "fix")),
            ("go-test-cfg-password", "internal/platform/config/config_test.go:315", "internal/platform/config/config_test.go", "cfg.Database.Password = \"x-super-secret-password\"", ("warn", "fix")),
            ("go-test-cfg-jwt-secret", "internal/platform/config/config_test.go:316", "internal/platform/config/config_test.go", "cfg.JWT.Secret = \"a-sufficiently-long-secret\"", ("warn", "fix")),
            ("go-test-db-password-field", "internal/platform/database/database_test.go:17", "internal/platform/database/database_test.go", "cfg := Config{Password: \"s3cr3t/pass?word\"}", ("warn", "fix")),
            ("go-test-runtime-password", "internal/platform/database/database_test.go:42", "internal/platform/database/database_test.go", "cfg.RuntimePassword = \"runtime/pw?ord\"", ("warn", "fix"))]
    for id, ref, path, line, (pa, aa) in lits:
        real(R + id, "go", ref, {path: go_file(f"func TestX(t *testing.T) {{\n\t{line}\n}}\n")}, [F("secrets.generic", path, line[:12], pa, "warn")],
             note="a credential-looking literal in a _test.go file: warn (test files are warn for everyone; no agent difference by design)")
    fps = [("go-fp-token-literal-json", "internal/auth/credentials/handler_test.go:117", "internal/auth/credentials/handler_test.go", "unknown := post(t, mux, VerifyPath, `{\"token\":\"definitely-not-a-token\"}`)"),
           ("go-fp-secretid-path", "internal/platform/secrets/awskeys/provider_test.go:61", "internal/platform/secrets/awskeys/provider_test.go", "p, err := New(client, Options{SecretID: \"atlas/jwt-signing-keys\", CacheTTL: ttl})"),
           ("go-fp-token-concat", "internal/orgs/postgres/membership_integration_test.go:473", "internal/orgs/postgres/membership_integration_test.go", "token := \"uac-token-\" + uniqueRun(\"t\")"),
           ("go-fp-collision-token", "internal/orgs/postgres/membership_integration_test.go:508", "internal/orgs/postgres/membership_integration_test.go", "collisionToken := \"uac-token-\" + uniqueRun(\"collision\")")]
    for id, ref, path, line in fps:
        real(R + id, "go", ref, {path: go_file(f"func TestX(t *testing.T) {{\n\t{line}\n}}\n")}, notfire=[("secrets.generic", path)], who="person",
             note="an identifier, a path or a generated name, not a credential (proposed FP for people; agents keep the finding)")
    # -- SQL built from identifiers in tests
    sqls = [("go-sql-grant-role", "internal/orgs/postgres/stage9_integration_test.go:282", "internal/orgs/postgres/stage9_integration_test.go", "admin.Exec(`GRANT SELECT, INSERT ON audit_log TO ` + role)", ("warn", "fix")),
            ("go-sql-grant-role-3a", "internal/platform/database/stage3_integration_test.go:349", "internal/platform/database/stage3_integration_test.go", "admin.Exec(`GRANT SELECT, INSERT ON organization_members, repositories TO ` + role)", ("warn", "fix")),
            ("go-sql-grant-role-3b", "internal/platform/database/stage3_integration_test.go:352", "internal/platform/database/stage3_integration_test.go", "admin.Exec(`GRANT SELECT ON organizations, users TO ` + role)", ("warn", "fix")),
            ("go-sql-delete-table", "internal/auth/oauth/github/session_lifecycle_integration_test.go:398", "internal/auth/oauth/github/session_lifecycle_integration_test.go", "appDB.Exec(`DELETE FROM ` + table + ` WHERE false`)", ("fix", "block"))]
    for id, ref, path, call, (pa, aa) in sqls:
        real(R + id, "go", ref, {path: go_file(f"func TestX(t *testing.T) {{\n\tif _, err := {call}; err != nil {{\n\t\tt.Fatal(err)\n\t}}\n}}\n")},
             [F("security.sql_injection", path, "Exec(", pa, aa)], note="identifiers cannot be parameterised; in a test file this is a warning for people. Paths under auth/ are [paths] sensitive and one step stricter by design (that is why the same pattern was FIX in one file and WARN in the others)")
    path = "internal/store/orders.go"
    real(R + "go-sql-value-concat-control", "go", "pattern: a value concatenated into SQL (control)", {path: go_file("func find(id string) {\n\tdb.Query(\"SELECT * FROM users WHERE id = \" + id)\n}\n")},
         [F("security.sql_injection", path, "db.Query", "fix", "fix")], note="control: a user value in SQL stays a fix")
    # -- docs
    path = "infra/vault/README.md"
    real(R + "go-docs-export-password", "markdown", f"{path}:61", {path: "# Vault\n\n```\nexport ATLAS_DEV_RDS_PASSWORD='a-strong-dev-password'\n```\n"},
         notfire=[("secrets.generic", path)], who="person", note="a placeholder in a runbook: information only for people (proposed); the agent keeps the finding")
    # -- dependencies (the registry fixture stands in for the network)
    gm = lambda *reqs: "module example.com/atlas\n\ngo 1.22\n\nrequire (\n" + "".join(f"\t{r}\n" for r in reqs) + ")\n"
    real(R + "go-vuln-direct", "go", "go.mod:22", {"go.mod": gm("github.com/example/otelsdk v1.37.0")}, [F("deps.vulnerable", "go.mod", "otelsdk", "fix", "fix")], note="a direct dependency with a known advisory")
    real(R + "go-vuln-direct-crypto", "go", "go.mod:125", {"go.mod": gm("github.com/example/cryptolib v0.53.0")}, [F("deps.vulnerable", "go.mod", "cryptolib", "fix", "fix")], note="two advisories, one fix version")
    for n, name in enumerate(("dockerlib", "compresslib", "archivelib"), 1):
        real(R + f"go-vuln-indirect-{n}", "go", f"go.mod:{70 + n * 10} (// indirect)", {"go.mod": gm(f"github.com/example/{name} v1.0.0 // indirect")},
             [F("deps.vulnerable", "go.mod", name, "warn", "fix")], note="an indirect dependency (go mod tidy added it): a warning for people, the agent keeps fix")
    real(R + "go-claude-settings-json", "json", ".claude/settings.json:47", {".claude/settings.json": "{\n  \"permissions\": {}\n}\nTodo - 3/9 done\n"},
         [F("code.syntax_error", ".claude/settings.json", None, "fix", "fix")], note="a JSON file with text after the object: a real syntax error")
    # -- web: eslint-disable
    web = [("ts-eslint-next-line-sensitive", "src/components/auth/QrCode.tsx:48", "src/components/auth/QrCode.tsx", "// eslint-disable-next-line @next/next/no-img-element -- a local data URL, not a remote image\n    return <img src={url} alt=\"\" />;", "eslint-disable-next-line", ("fix", "fix")),
           ("ts-eslint-file-block", "src/components/Logo.tsx:30", "src/components/Logo.tsx", "/* eslint-disable @next/next/no-img-element */\nexport const Logo = () => <img src=\"/l.svg\" alt=\"\" />;", "eslint-disable", ("warn", "warn")),
           ("ts-eslint-jsx-comment", "src/components/landing/EcoLogo.tsx:35", "src/components/landing/EcoLogo.tsx", "{/* eslint-disable-next-line @next/next/no-img-element */}\n  <img src=\"/e.svg\" alt=\"\" />", "eslint-disable-next-line", ("warn", "warn")),
           ("ts-eslint-next-line-avatar", "src/components/members/MemberAvatar.tsx:8", "src/components/members/MemberAvatar.tsx", "// eslint-disable-next-line @next/next/no-img-element\nexport const A = () => <img src=\"/a.png\" alt=\"\" />;", "eslint-disable-next-line", ("warn", "warn")),
           ("ts-eslint-next-line-service", "src/components/services/ServiceMark.tsx:15", "src/components/services/ServiceMark.tsx", "// eslint-disable-next-line @next/next/no-img-element\nexport const S = () => <img src=\"/s.png\" alt=\"\" />;", "eslint-disable-next-line", ("warn", "warn")),
           ("ts-eslint-next-line-header", "src/components/shell/GlobalHeader.tsx:67", "src/components/shell/GlobalHeader.tsx", "// eslint-disable-next-line @next/next/no-img-element\nexport const H = () => <img src=\"/h.png\" alt=\"\" />;", "eslint-disable-next-line", ("warn", "warn"))]
    for id, ref, path, body, needle, (pa, aa) in web:
        real(R + id, "typescript", ref, {path: body + "\n"}, [F("suppress.lint", path, needle, pa, aa)], which=WEB)
    # -- web: e2e / mocks / config
    wsec = [("ts-e2e-password-const", "e2e/auth-mfa.spec.ts:7", "e2e/auth-mfa.spec.ts", "const PASSWORD = 'CorrectHorse12!';", "PASSWORD"),
            ("ts-mock-demo-password", "e2e/mock-api.mjs:117", "e2e/mock-api.mjs", "const DEMO_PASSWORD = 'CorrectHorse12!';", "DEMO_PASSWORD"),
            ("ts-playwright-session-secret", "playwright.config.ts:47", "playwright.config.ts", "export const env = { SESSION_SECRET: 'e2e-only-session-secret-0123456789abcdef' };", "SESSION_SECRET")]
    for id, ref, path, line, needle in wsec:
        real(R + id, "typescript", ref, {path: line + "\n"}, [F("secrets.generic", path, needle, "warn", "warn" if ".spec." in path else "fix")], which=WEB,
             note="a fixture credential in an e2e / mock / test-runner file: test context for people (a *.spec.* file is a test file for everyone)")
    path = "e2e/mock-api.mjs"
    real(R + "ts-mock-random-secret", "javascript", f"{path}:884", {path: "const BASE32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';\nconst secret = Array.from({ length: 32 }, () => BASE32[Math.floor(Math.random() * BASE32.length)]).join('');\n"},
         notfire=[("security.weak_crypto", path)], who="person", which=WEB, note="a mock TOTP secret in an e2e mock: not production crypto (proposed FP for people; the agent keeps the warning)")
    real(R + "ts-mock-otpauth-url", "javascript", f"{path}:887", {path: "const secret = 'JBSWY3DPEHPK3PXP';\nconst otpauthUrl = `otpauth://totp/ATLAS:${encodeURIComponent(email)}?secret=${secret}&issuer=ATLAS`;\n"},
         notfire=[("security.weak_crypto", path)], who="person", which=WEB, note="a mock otpauth URL in an e2e mock (proposed FP for people)")
    spec = "e2e/real-backend.spec.ts"
    for id, ref, line, act in (("ts-skip-env-conditional", f"{spec}:16", "test.skip(!baseURL, 'set ATLAS_SMOKE_BASE_URL to run the real-backend smoke');", ("warn", "fix")),
                               ("ts-skip-data-conditional-1", f"{spec}:51", "if ((await firstOrg.count()) === 0) test.skip(true, 'the signed-in user has no organizations');", ("warn", "fix")),
                               ("ts-skip-data-conditional-2", f"{spec}:73", "if ((await rows.count()) === 0) test.skip(true, 'nothing to show');", ("warn", "fix"))):
        real(R + id, "typescript", ref, {spec: "const baseURL = process.env.BASE_URL;\ntest('x', async () => {\n  " + line + "\n});\n"},
             [F("tests.skip_added", spec, "test.skip", *act)], which=WEB, note="a conditional skip (environment or data gated): a warning for people")
    for n, (ref, line) in enumerate((("src/lib/api/errors.test.ts:23", "problem('mfa_required', { challenge_token: 'challenge-1', expires_in: 240 }),"),
                                     ("src/lib/api/errors.test.ts:28", "challengeToken: 'challenge-1',"),
                                     ("src/lib/api/errors.test.ts:37", "problem('mfa_enrollment_required', { challenge_token: 'challenge-2', expires_in: 300 }),"),
                                     ("src/lib/api/errors.test.ts:42", "challengeToken: 'challenge-2',"),
                                     ("src/lib/api/errors.test.ts:49", "const error = errorForStatus(401, problem('mfa_required', { challenge_token: 'challenge-3' }), JSON_TYPE);")), 1):
        path = "src/lib/api/errors.test.ts"
        real(R + f"ts-fp-challenge-token-{n}", "typescript", ref, {path: f"test('x', () => {{\n  const v = [\n    {line}\n  ];\n}});\n"},
             notfire=[("secrets.generic", path)], who="person", which=WEB, note="a short identifier-like test value named *token (proposed FP for people)")


# =============================================================================================== adversarial

def adv(id, language, kind, before, after, fire=(), notfire=(), who="both", extra="", mode="staged", delete=(), renames=(), messages=()):
    emit(id, "adversarial", language, f"adversarial: {kind}", before, after, fire, notfire, who=who, extra=extra, mode=mode, delete=delete, renames=renames, messages=messages)


def gen(path, kind, size=None, prefix="", suffix=""):
    out = ["", "[[generate]]", f"path = {tq(path)}", f"kind = {tq(kind)}"]
    if size is not None:
        out.append(f"size = {size}")
    if prefix:
        out.append(f"prefix = {tq(prefix)}")
    if suffix:
        out.append(f"suffix = {tq(suffix)}")
    return "\n".join(out)


def seed_adversarial():
    A = "adv-"
    base = {"README.md": "# base\n", "app.py": "def f():\n    return 1\n"}
    aws = "@@AWS@@"
    # ---- secrets in awkward places: a good gate still sees them
    adv(A + "key-in-json", "json", "provider key inside a JSON value", base, {"config/app.json": "{\"aws\": \"" + aws + "\"}\n"}, [F("secrets.hardcoded", "config/app.json", "aws", "block", "block")])
    adv(A + "key-in-yaml", "yaml", "provider key in YAML", base, {"deploy/values.yaml": "aws_key: " + aws + "\n"}, [F("secrets.hardcoded", "deploy/values.yaml", "aws_key", "block", "block")])
    adv(A + "key-in-comment", "python", "provider key in a comment", base, {"app.py": "def f():\n    # old key: " + aws + "\n    return 1\n"}, [F("secrets.hardcoded", "app.py", "old key", "block", "block")])
    adv(A + "key-in-docstring", "python", "provider key in a docstring", base, {"app.py": "def f():\n    \"\"\"uses " + aws + " for tests\"\"\"\n    return 1\n"}, [F("secrets.hardcoded", "app.py", "uses", "block", "block")])
    adv(A + "key-in-url", "python", "token in a clone URL", base, {"app.py": "URL = 'https://x-access-token:@@GHTOKEN@@@github.com/org/repo.git'\n"}, [F("secrets.hardcoded", "app.py", "URL", "block", "block")])
    adv(A + "key-in-notebook", "json", "key in a notebook cell", base, {"nb/a.ipynb": "{\"cells\": [{\"cell_type\": \"code\", \"source\": [\"KEY = '" + aws + "'\"]}]}\n"},
        [F("secrets.hardcoded", "nb/a.ipynb", "KEY", "block", "block")])
    adv(A + "key-in-shell-script", "shell", "key exported in a shell script", base, {"scripts/run.sh": "#!/bin/sh\nexport AWS_ACCESS_KEY_ID=" + aws + "\n"}, [F("secrets.hardcoded", "scripts/run.sh", "AWS_ACCESS", "block", "block")])
    adv(A + "key-in-dockerfile", "dockerfile", "key in a Dockerfile ENV", base, {"Dockerfile": "FROM scratch\nENV AWS_ACCESS_KEY_ID=" + aws + "\n"}, [F("secrets.hardcoded", "Dockerfile", "ENV", "block", "block")])
    adv(A + "env-production-committed", "dotenv", "a .env.production with real-looking values", base, {".env.production": "DATABASE_PASSWORD=Zx9kQ2vB7mN4pL1w\n"}, [F("secrets.env_file", ".env.production", None, "block", "block")])
    adv(A + "key-crlf-file", "python", "key in a CRLF file", base, {"app.py": "def f():\r\n    return '" + aws + "'\r\n"}, [F("secrets.hardcoded", "app.py", "return", "block", "block")],
        extra="[[transform]]\npath = \"app.py\"\nkind = \"crlf\"")
    adv(A + "key-after-bom", "python", "key in a file with a UTF-8 BOM", base, {"app.py": "KEY = '" + aws + "'\n"}, [F("secrets.hardcoded", "app.py", "KEY", "block", "block")],
        extra="[[transform]]\npath = \"app.py\"\nkind = \"bom\"")
    adv(A + "key-latin1-file", "python", "key in a latin-1 encoded file", base, {"app.py": "# caf\u00e9\nKEY = '" + aws + "'\n"}, [F("secrets.hardcoded", "app.py", "KEY", "block", "block")],
        extra="[[transform]]\npath = \"app.py\"\nkind = \"latin1\"")
    adv(A + "key-split-concat", "python", "provider key split over a string concatenation", base, {"app.py": "KEY = 'AKIA' + 'QWERTYUIOPASDFGH'\n"}, [F("secrets.hardcoded", "app.py", "KEY", "block", "block")])
    adv(A + "key-split-join", "python", "provider key split over a join", base, {"app.py": "KEY = ''.join(['AKIA', 'QWERTY', 'UIOPASDFGH'])\n"}, [F("secrets.hardcoded", "app.py", "KEY", "block", "block")])
    adv(A + "key-on-long-line", "javascript", "key on a 5000-character line", base, {},
        [F("secrets.hardcoded", "bundle.js", None, "block", "block")], extra=gen("bundle.js", "long_line", 5000, prefix="var k='" + aws + "';", suffix=""))
    adv(A + "key-in-big-file", "python", "key in a file over 1 MB", base, {}, [F("secrets.hardcoded", "big.py", None, "block", "block")],
        extra=gen("big.py", "big_text", 1_100_000, prefix="KEY = '" + aws + "'\n"))
    # ---- look-alikes that are NOT keys (must stay quiet)
    adv(A + "homoglyph-akia", "python", "a Cyrillic look-alike of an AWS key prefix (not a key)", base, {"app.py": "KEY = '\u0410KIAQWERTYUIOPASDFGH'\n"}, notfire=[("secrets.hardcoded", "app.py")])
    adv(A + "zero-width-in-key", "python", "a zero-width space inside a key shape (not a key)", base, {"app.py": "KEY = 'AKIA@@ZWSP@@QWERTYUIOPASDFGH'\n"}, notfire=[("secrets.hardcoded", "app.py")])
    adv(A + "akia-in-prose", "markdown", "AKIA inside a word (not a key)", base, {"docs/a.md": "The word AKIAMBIENCEISNOTAKEYATALL appears here.\n"}, notfire=[("secrets.hardcoded", "docs/a.md")])
    adv(A + "doc-example-key", "markdown", "the documented AWS example key", base, {"docs/b.md": "Use AKIAIOSFODNN7EXAMPLE as the example.\n"}, notfire=[("secrets.hardcoded", "docs/b.md")])
    adv(A + "env-example-file", "dotenv", ".env.example with placeholders", base, {".env.example": "DATABASE_PASSWORD=changeme\nAPI_KEY=<your-api-key>\n"}, notfire=[("secrets.env_file", ".env.example")])
    adv(A + "eval-in-string", "python", "eval( inside a string literal (not a call)", base, {"app.py": "MSG = \"never call eval(x) on input\"\n"}, notfire=[("security.eval", "app.py")])
    adv(A + "skip-in-comment", "python", "pytest.mark.skip inside a comment (not a skip)", base, {"tests/test_a.py": "# pytest.mark.skip would hide this\ndef test_a():\n    assert 1\n"}, notfire=[("tests.skip_added", "tests/test_a.py")])
    adv(A + "todo-in-string", "python", "TODO inside a string (not a comment)", base, {"app.py": "def f():\n    return 'TODO list'\n"}, notfire=[("code.todo", "app.py")])
    # ---- evasion of rules by aliasing
    adv(A + "skip-alias-import", "python", "skip through an import alias", base, {"tests/test_a.py": "from pytest import mark as m\n\n\n@m.skip\ndef test_a():\n    assert 1\n"}, [F("tests.skip_added", "tests/test_a.py", "@m.skip", "fix", "fix")])
    adv(A + "skip-alias-var", "python", "skip through a variable holding the marker", base, {"tests/test_a.py": "import pytest\n\nlater = pytest.mark.skip\n\n\n@later\ndef test_a():\n    assert 1\n"},
        [F("tests.skip_added", "tests/test_a.py", "later = pytest.mark.skip", "fix", "fix")])
    adv(A + "eval-via-builtins", "python", "eval through __builtins__", base, {"app.py": "def f(s):\n    return __builtins__['eval'](s)\n"}, [F("security.eval", "app.py", "__builtins__", "fix", "fix")])
    adv(A + "nolint-uppercase", "go", "//NOLINT in capitals is not a golangci-lint directive (directives are case-sensitive): not a suppression",
        {"go.mod": "module x\n\ngo 1.22\n", "a.go": "package x\n"}, {"a.go": "package x\n\nfunc f() {\n\t_ = g() //NOLINT\n}\n"}, notfire=[("suppress.lint", "a.go")])
    # ---- robustness: nothing may crash, hang or print a traceback
    adv(A + "huge-single-line", "python", "a 5 MB single line", base, {}, extra=gen("huge.py", "long_line", 5_000_000))
    adv(A + "minified-js", "javascript", "a 2 MB minified bundle on one line", base, {}, extra=gen("dist/app.min.js", "long_line", 2_000_000, prefix="!function(){"))
    adv(A + "binary-file", "binary", "a binary file", base, {}, extra=gen("assets/blob.bin", "binary", 4096))
    adv(A + "binary-as-py", "python", "binary content named .py", base, {}, extra=gen("app2.py", "binary", 4096))
    adv(A + "deep-json", "json", "100,000 nested brackets", base, {}, extra=gen("data/deep.json", "deep_json", 100_000))
    adv(A + "deep-python-expr", "python", "an expression nested 5,000 parentheses deep", base, {}, extra=gen("deep.py", "deep_python", 5000))
    adv(A + "many-lines", "python", "a 200,000-line file", base, {}, extra=gen("big_table.py", "many_lines", 200_000))
    adv(A + "empty-file", "python", "an empty file", base, {}, extra=gen("empty.py", "empty"))
    adv(A + "nul-bytes", "python", "NUL bytes inside a source file", base, {"app.py": "def f():\n    return 'a'\n\n\ndef g():\n    return 'b'\n"}, extra="[[transform]]\npath = \"app.py\"\nkind = \"nul\"")
    adv(A + "utf16-source", "python", "a UTF-16 source file", base, {"app.py": "def f():\n    return 1\n"}, extra="[[transform]]\npath = \"app.py\"\nkind = \"utf16\"")
    adv(A + "bidi-in-comment", "python", "bidi override characters in a comment (Trojan Source)", base, {"app.py": "def f():\n    # @@RLO@@ check @@LRI@@ admin @@PDI@@\n    return 1\n"})
    adv(A + "unicode-filename", "python", "non-ASCII and spaced file names", base, {"d\u00e9j\u00e0 vu/caf\u00e9 \u00fcber.py": "def f():\n    return 1\n"})
    adv(A + "dash-filename", "python", "a file named like an option", base, {"-rf.py": "def f():\n    return 1\n", "--help.md": "# x\n"})
    adv(A + "deep-path", "python", "a path 60 directories deep", base, {"/".join(["d"] * 60) + "/m.py": "def f():\n    return 1\n"})
    adv(A + "many-files-10k", "python", "a 10,000-file diff", base, {}, extra=gen("gen", "many_files", 10_000))
    adv(A + "symlink-added", "python", "a symlink to a file outside the repository", base, {}, extra="[[symlink]]\npath = \"link.py\"\ntarget = \"../../etc/passwd\"")
    adv(A + "symlink-to-dir", "python", "a symlink to a directory", base, {}, extra="[[symlink]]\npath = \"vendor_link\"\ntarget = \"app\"")
    adv(A + "submodule-gitlink", "python", "a submodule entry", base, {".gitmodules": "[submodule \"lib\"]\n\tpath = lib\n\turl = https://example.invalid/lib.git\n"},
        extra="[[gitlink]]\npath = \"lib\"\nsha = \"1111111111111111111111111111111111111111\"")
    adv(A + "rename-with-edit", "python", "a rename that also edits the file", {"README.md": "# base\n", "old_name.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"},
        {"new_name.py": "def f():\n    return 1\n\n\ndef g():\n    return eval('2')\n"}, [F("security.eval", "new_name.py", "eval", "fix", "fix")], renames=[("old_name.py", "new_name.py")])
    adv(A + "rename-only", "python", "a pure rename", {"README.md": "# base\n", "old_name.py": "def f():\n    return 1\n"}, {}, renames=[("old_name.py", "new_name.py")])
    adv(A + "delete-everything", "python", "every tracked file deleted", {"README.md": "# base\n", "a.py": "x = 1\n", "b.py": "y = 2\n"}, {}, delete=["README.md", "a.py", "b.py"])
    adv(A + "empty-repo-first-commit", "python", "the very first commit (no HEAD)", {}, {"app.py": "def f():\n    return eval('1')\n"}, [F("security.eval", "app.py", "eval", "fix", "fix")], mode="empty")
    adv(A + "detached-head", "python", "a staged change on a detached HEAD", base, {"app.py": "def f():\n    return eval('1')\n"}, [F("security.eval", "app.py", "eval", "fix", "fix")], mode="detached")
    adv(A + "merge-in-progress", "python", "a merge commit being made", base, {"topic.py": "def t():\n    return eval('3')\n"}, [F("security.eval", "topic.py", "eval", "fix", "fix")], mode="merge")
    adv(A + "range-check", "python", "check --range over one commit", base, {"app.py": "def f():\n    return eval('1')\n"}, [F("security.eval", "app.py", "eval", "fix", "fix")], mode="range")
    adv(A + "crlf-everywhere", "python", "CRLF line endings with a real finding", base, {"app.py": "def f(s):\r\n    return eval(s)\r\n"}, [F("security.eval", "app.py", "eval", "fix", "fix")],
        extra="[[transform]]\npath = \"app.py\"\nkind = \"crlf\"")
    adv(A + "mixed-endings", "python", "mixed line endings with a real finding", base, {"app.py": "def f(s):\n    x = 1\n    return eval(s)\n"}, [F("security.eval", "app.py", "eval", "fix", "fix")],
        extra="[[transform]]\npath = \"app.py\"\nkind = \"mixed\"")
    adv(A + "go-generated-huge", "go", "a huge generated Go file", {"go.mod": "module x\n\ngo 1.22\n", "a.go": "package x\n"}, {}, extra=gen("gen/big.go", "many_lines", 100_000))
    adv(A + "ts-long-template", "typescript", "a very long template literal", base, {"src/a.ts": "export const t = `" + ("a${b}" * 3000) + "`;\n"})
    adv(A + "unterminated-string", "python", "an unterminated string literal", base, {"app.py": "def f():\n    return 'abc\n"}, [F("code.syntax_error", "app.py", None, "fix", "fix")])
    adv(A + "toml-config-garbage", "toml", "an unreadable repository config (a person's worktree config is used)", base, {".aigate/lite.toml": "this is = not [valid toml\n"},
        [F("gate.config_invalid", ".aigate/lite.toml", None)], who="person")
    adv(A + "toml-config-garbage-agent", "toml", "an agent edits the gate config: forbidden path (config is read from HEAD for an agent)", base,
        {".aigate/lite.toml": "this is = not [valid toml\n"}, [F("scope.forbidden_path", ".aigate/lite.toml", None, "block", "block")], who="agent")
    adv(A + "nothing-staged-json", "python", "check --format json with nothing staged must still print a JSON document", base, {})
    adv(A + "config-allow-everything", "toml", "an agent adds an allow entry for a block", base,
        {".aigate/lite.toml": "version = 1\n[[allow]]\nrule = \"secrets.hardcoded\"\npath = \"*\"\nreason = \"trust me\"\n", "app.py": "KEY = '" + aws + "'\n"},
        [F("secrets.hardcoded", "app.py", "KEY", "block", "block"), F("scope.forbidden_path", ".aigate/lite.toml", None, "block", "block")], who="agent")


# =============================================================================================== clean repositories

def main():
    for d in (CORPUS, HOLDOUT):
        d.mkdir(exist_ok=True)
        for old in d.iterdir():
            if old.is_dir() and not old.name.startswith("clean-"):
                shutil.rmtree(old)
    write_fixture()
    seed_python()
    seed_go()
    seed_ts()
    seed_real()
    seed_adversarial()
    by = {}
    for cid in CASES:
        by[cid.split("-")[0]] = by.get(cid.split("-")[0], 0) + 1
    print(len(CASES), "cases written:", by, "holdout:", sum(holdout_of(c) for c in CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
