import textwrap

from conftest import find, rules


def d(s):
    return textwrap.dedent(s).lstrip("\n")


# ---------------------------------------------------------------- weakened tests

BASE_TEST = d('''
    import pytest

    def test_login():
        assert login("a") == 200
        assert login("b") == 401
        assert login("") == 400

    def test_logout():
        assert logout() is True

    @pytest.mark.parametrize("n", [1, 2, 3])
    def test_n(n):
        assert n
''')


def test_assertions_removed_skip_added_deleted(repo):
    repo.write("tests/test_auth.py", BASE_TEST)
    repo.commit()
    repo.write("tests/test_auth.py", d('''
        import pytest

        @pytest.mark.skip(reason="flaky")
        def test_login():
            assert login("a") == 200

        @pytest.mark.parametrize("n", [1])
        def test_n(n):
            assert n
    '''))
    repo.add()
    res = repo.check()
    msgs = {f["rule"]: f["message"] for f in res["findings"]}
    assert "assertions 3 → 1" in [f["message"].split(": ")[1] for f in find(res, "tests.assertions_removed")
                                  if "test_login" in f["message"]][0]
    assert "cases 3 → 1" in " ".join(f["message"] for f in find(res, "tests.assertions_removed"))
    assert "skip" in msgs["tests.skip_added"] and "test_logout" in msgs["tests.deleted"]
    assert res["verdict"] in ("fix", "block")


def test_renamed_or_moved_tests_are_not_deleted(repo):
    repo.write("tests/test_a.py", "def test_one():\n    assert f() == 1\n")
    repo.commit()
    repo.git("mv", "tests/test_a.py", "tests/test_b.py")
    repo.write("tests/test_b.py", "def test_one():\n    assert f() == 1\n\n\ndef test_two():\n    assert g() == 2\n")
    repo.add()
    assert not find(repo.check(), "tests.deleted")


def test_trivial_and_missing_assertions(repo):
    repo.write("tests/test_x.py", d('''
        def test_a():
            assert True

        def test_b():
            assert result() or True

        def test_c():
            run_it()
    '''))
    repo.add()
    res = repo.check()
    assert [f["line"] for f in find(res, "tests.trivial_assert")] == [2, 5]
    assert find(res, "tests.no_assertions")[0]["line"] == 7


def test_expected_value_changed_with_source(repo):
    repo.write("calc.py", "def add(a, b):\n    return a + b\n")
    repo.write("tests/test_calc.py", "def test_add():\n    assert add(2, 2) == 4\n")
    repo.commit()
    repo.write("calc.py", "def add(a, b):\n    return a + b + 1\n")
    repo.write("tests/test_calc.py", "def test_add():\n    assert add(2, 2) == 5\n")
    repo.add()
    assert find(repo.check(), "tests.expected_changed")[0]["action"] == "warn"


def test_harness_tampering(repo):
    repo.write("sitecustomize.py", "import os\n")
    repo.write("conftest.py", "def pytest_runtest_makereport(item, call):\n    pass\n")
    repo.write("pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-p no:cacheprovider --deselect tests/test_a.py::test_x"\n')
    repo.add()
    res = repo.check()
    tamper = {f["path"]: f["action"] for f in find(res, "tests.harness_tamper")}
    assert tamper == {"sitecustomize.py": "block", "conftest.py": "fix"}
    assert find(res, "tests.config_changed")


def test_js_focus_skip_and_lost_assertions(repo):
    repo.write("src/a.test.js", "test('a', () => {\n  expect(a()).toBe(1);\n  expect(b()).toBe(2);\n});\n")
    repo.commit()
    repo.write("src/a.test.js", "test.only('a', () => {\n  expect(a()).toBe(1);\n});\nit.skip('c', () => {});\n")
    repo.add()
    res = repo.check()
    assert find(res, "tests.focused") and find(res, "tests.skip_added") and find(res, "tests.assertions_removed")


# ---------------------------------------------------------------- code

def test_python_code_mistakes(repo):
    repo.write("svc.py", d('''
        import subprocess
        import yaml
        import requests


        def run(cmd, items=[]):
            # ... rest of the implementation
            subprocess.run(cmd, shell=True)
            data = yaml.load(open("c.yml"))
            requests.get("https://x.dev", verify=False)
            eval(cmd)
            breakpoint()
            try:
                cur.execute(f"SELECT * FROM users WHERE id = {cmd}")
            except Exception:
                pass
            return data


        def todo():
            ...
    '''))
    repo.add()
    found = set(rules(repo.check()))
    for rule in ("code.placeholder", "security.shell", "security.deserialization", "security.verification_disabled",
                 "security.eval", "code.debug_leftover", "security.sql_injection", "code.swallowed_exception",
                 "code.mutable_default"):
        assert rule in found, rule


def test_abstract_and_protocol_methods_are_not_placeholders(repo):
    repo.write("base.py", d('''
        from abc import ABC, abstractmethod
        from typing import Protocol

        class Base(ABC):
            @abstractmethod
            def run(self): ...

        class P(Protocol):
            def go(self) -> int: ...
    '''))
    repo.add()
    assert not find(repo.check(), "code.placeholder")


def test_js_code_mistakes(repo):
    repo.write("src/api.js", "export function f(q) {\n  debugger;\n  eval(q);\n  db.query(`SELECT * FROM t WHERE id = ${q}`);\n"
                             "  console.log(q);\n  try { g() } catch (e) {}\n  return fetch(u, { rejectUnauthorized: false });\n}\n")
    repo.add()
    found = set(rules(repo.check()))
    assert {"code.debug_leftover", "security.eval", "security.sql_injection", "code.console_log",
            "code.swallowed_exception", "security.verification_disabled"} <= found


def test_syntax_errors(repo):
    repo.write("broken.py", "def f(:\n    pass\n")
    repo.write("data.json", '{"a": 1,}\n')
    repo.add()
    paths = {f["path"] for f in find(repo.check(), "code.syntax_error")}
    assert paths == {"broken.py", "data.json"}


def test_size_and_performance(repo):
    long_body = "\n".join(f"    x{i} = {i}" for i in range(70))
    repo.write("perf.py", d('''
        def find(items: list, wanted):
            out = []
            for w in wanted:
                if w in items:
                    out.append(w)
                for u in users:
                    requests.get(u)
            return out


        def fib(n):
            if n < 2:
                return n
            return fib(n - 1) + fib(n - 2)


        def branchy(a, b, c, d, e):
            if a and b or c:
                pass
            if b: return 1
            if c: return 2
            if d: return 3
            if e: return 4
            for i in range(a):
                if i: return 5
            while a: a -= 1
            return 0 if a else (1 if b else 2)


        def cube(xs):
            for a in xs:
                for b in xs:
                    for c in xs:
                        print(a, b, c)
    ''') + "\n\ndef long():\n" + long_body + "\n")
    repo.add()
    found = rules(repo.check())
    for rule in ("perf.quadratic_pattern", "perf.call_in_loop", "perf.exponential_recursion", "size.complexity",
                 "perf.nested_loops", "size.function_length"):
        assert rule in found, rule


def test_existing_long_function_is_not_reported_again(repo):
    body = "\n".join(f"    x{i} = {i}" for i in range(70))
    repo.write("big.py", "def long():\n" + body + "\n")
    repo.commit()
    repo.write("big.py", "def long():\n" + body.replace("x3 = 3", "x3 = 33") + "\n")
    repo.add()
    assert not find(repo.check(), "size.function_length")


def test_duplicate_block_and_diff_budget(repo):
    block = "\n".join(f"    total = total + values[{i}] * weights[{i}]" for i in range(8))
    repo.write("dup.py", f"def a(values, weights):\n    total = 0\n{block}\n    return total\n\n\n"
                         f"def b(values, weights):\n    total = 0\n{block}\n    return total\n")
    repo.write(".aigate/lite.toml", "[limits]\ndiff_added_lines = 10\n")
    repo.add()
    res = repo.check()
    assert len(find(res, "code.duplicate_block")) == 1 and find(res, "size.diff_budget")


# ---------------------------------------------------------------- suppressions

def test_suppressions(repo):
    repo.write("s.py", "x = 1  # type: ignore\n")
    repo.commit()
    repo.write("s.py", "x = 2  # type: ignore\ny = f()  # nosec\nz = g()  # noqa: E501\n")
    repo.add()
    res = repo.check()
    got = {f["line"]: (f["rule"], f["action"]) for f in res["findings"] if f["rule"].startswith("suppress.")}
    assert got == {2: ("suppress.security", "fix"), 3: ("suppress.lint", "warn")}


def test_suppression_in_sensitive_path_is_stricter(repo):
    repo.write("app/auth/login.py", "check()  # noqa\nverify()  # type: ignore\n")
    repo.add()
    res = repo.check()
    got = {f["rule"]: f["action"] for f in res["findings"] if f["rule"].startswith("suppress.")}
    assert got == {"suppress.lint": "fix", "suppress.type": "block"}
