"""Round 3: a provider key written as a constant string operation in JavaScript, TypeScript or Go is one key at run time
(the same detection gain as the Python fold). A person and an AI agent get the same result; no fake key is a literal
in this file (the repository self-scan would flag it)."""
import time

import pytest

from conftest import find

TAIL = "qwertyuiopasdfgh".upper()          # the tail of a fake AWS key id, built so this file holds no key
HEAD = "AK" + "IA"
AGENT = {"CLAUDECODE": "1"}
WHO = [pytest.param(None, id="person"), pytest.param(AGENT, id="agent")]


def staged(repo, rel, text):
    repo.write(rel, text)
    repo.add()


def hits(res):
    return sorted((f["line"], f["action"]) for f in find(res, "secrets.hardcoded"))


def both(repo, rel, text):
    """The result for a person and for an agent, which must be the same."""
    staged(repo, rel, text)
    person, agent = repo.check(), repo.check(env=AGENT)
    assert hits(person) == hits(agent)
    return hits(person)


def dq(s):
    return '"' + s + '"'


def sq(s):
    return "'" + s + "'"


def bt(s):
    return "`" + s + "`"


A, B, C = HEAD, TAIL[:6], TAIL[6:]

# ---------------------------------------------------------------- JavaScript and TypeScript

JS_SPLITS = {
    "concat": f"const k = {dq(A)} + {dq(B + C)};\n",
    "concat_chain_mixed_quotes": f"const k = {sq(A)} + {dq(B)} + {bt(C)};\n",
    "concat_no_spaces": f"const k = {dq(A)}+{dq(B + C)};\n",
    "template": f"const k = `{A}${{{dq(B)}}}{C}`;\n",
    "template_single_quoted_expression": f"const k = `{A}${{ {sq(B)} }}{C}`;\n",
    "template_leading_expression": f"const k = `${{{dq(A)}}}{B}${{{sq(C)}}}`;\n",
    "join_dq": f"const k = [{dq(A)}, {dq(B)}, {dq(C)}].join(\"\");\n",
    "join_sq": f"const k = [{sq(A)}, {sq(B + C)}].join('');\n",
    "join_trailing_comma": f"const k = [{dq(A)}, {dq(B + C)},].join(\"\");\n",
    "join_separator": f"const k = [{dq(A)}, {dq(B + C)}].join(\"\");\n",
    "join_then_plus": f"const k = [{dq(A)}].join(\"\") + {dq(B + C)};\n",
    "parenthesised": f"const k = ({dq(A)} + ({dq(B)} + {dq(C)}));\n",
    "multi_line_chain": f"const k =\n  {dq(A)} +\n  {dq(B)} +\n  {dq(C)};\n",
    "multi_line_leading_plus": f"const k = {dq(A)}\n  + {dq(B)}\n  + {dq(C)};\n",
    "multi_line_join": f"const k = [\n  {dq(A)},\n  {dq(B + C)},\n].join(\"\");\n",
    "in_a_call": f"client({{ key: {dq(A)} + {dq(B)} + {dq(C)} }});\n",
    "json_like_escape": f"const k = {dq(A)} + {dq(chr(92) + 'x51' + B[1:] + C)};\n",
}
JS_LINES = {"multi_line_chain": 2, "multi_line_leading_plus": 1, "multi_line_join": 1}


@pytest.mark.parametrize("path", ["src/a.js", "src/a.ts", "src/a.tsx", "src/a.mjs"])
@pytest.mark.parametrize("name", sorted(JS_SPLITS))
def test_js_key_split_is_found_for_person_and_agent(repo, path, name):
    got = both(repo, path, JS_SPLITS[name])
    assert got == [(JS_LINES.get(name, 1), "block")]


def test_js_key_split_reports_the_line_where_the_expression_starts(repo):
    assert both(repo, "src/a.js", f"const a = 1;\n\nconst k = {dq(A)} + {dq(B + C)};\n") == [(3, "block")]


def test_js_key_on_one_line_and_split_key_each_at_its_own_line(repo):
    src = f"const a = {dq(A + TAIL)};\nconst b = {dq(A)} + {dq(TAIL)};\n"
    assert [ln for ln, _ in both(repo, "src/a.js", src)] == [1, 2]


def test_js_key_split_is_reported_once_for_a_key_that_is_also_whole(repo):
    assert both(repo, "src/a.js", f"const k = `{A}{TAIL}${{{dq('x')}}}`;\n") == [(1, "block")]


@pytest.mark.parametrize("src", [
    f"const k = {dq(A)} + {dq('short')};\n",                                  # too short for a key
    f"const k = {dq('hello')} + {dq(' ')} + {dq('world')};\n",                # ordinary words
    f"const h = [{dq('ACCESS')}, {dq('KEY')}, {dq('ID')}].join({dq('_')});\n",
    f"const k = {dq(A)} + suffix;\n",                                         # a variable
    f"const k = {dq(A)} + {dq(B)} + suffix + {dq(C)};\n",
    f"const k = [{dq(A)}, part, {dq(B + C)}].join(\"\");\n",
    f"const k = `{A}${{part}}{B + C}`;\n",                                     # a template with a variable
    f"const k = prefix + {dq(A)} + {dq(B + C)};\n",
    f"const k = {dq(A)} + {dq('EXAMPLEEXAMPLEEX')};\n",                       # the documentation placeholder
    f"const k = {dq(A + 'IOSFODNN7' + 'EXAMPLE')} + {dq('x')};\n",           # the AWS documentation key
    f"const k = {dq('9f86d081884c7d659a2feaa0c55ad015')} + {dq('a3bf4f1b2b0b822cd15d6c15b0f00a08')};\n",   # a sha256 digest
    f"// {dq(A)} + {dq(B + C)}\nconst k = 1;\n",                              # in a comment
    f"/* {dq(A)} + {dq(B + C)} */\nconst k = 1;\n",
    f"const re = /{dq(A)}/; const s = {dq(B)} + {dq(C)};\n",
    f"const p = path.join(root, {dq('a')}, {dq('b')});\n",
])
def test_js_key_split_negatives(repo, src):
    assert both(repo, "src/a.js", src) == []


def test_js_text_that_is_not_code_does_not_make_constants(repo):
    # the quotes below are inside a regular expression and a comment: not string constants
    src = f"const r = /[\"']/g;\nconst a = r + {dq(A)} + x;\n// '{A}' + '{TAIL}'\nconst b = {dq(B)} + {dq(C)};\n"
    assert both(repo, "src/a.ts", src) == []


def test_js_unterminated_construct_does_not_crash(repo):
    src = f"const a = {dq(A)} + {dq(TAIL)};\nconst b = `unterminated ${{\n"
    staged(repo, "src/a.js", src)
    res = repo.check()
    assert res["code"] in (0, 1)


# ---------------------------------------------------------------- Go

GO_SPLITS = {
    "concat": f"package p\n\nconst k = {dq(A)} + {dq(B + C)}\n",
    "concat_chain_raw": f"package p\n\nconst k = {dq(A)} + {bt(B)} + {dq(C)}\n",
    "join": f"package p\n\nimport \"strings\"\n\nvar k = strings.Join([]string{{{dq(A)}, {dq(B + C)}}}, \"\")\n",
    "join_multi_line": f"package p\n\nimport \"strings\"\n\nvar k = strings.Join([]string{{\n\t{dq(A)},\n\t{dq(B + C)},\n}}, \"\")\n",
    "sprintf_s": f"package p\n\nimport \"fmt\"\n\nvar k = fmt.Sprintf(\"%s%s\", {dq(A)}, {dq(B + C)})\n",
    "sprintf_v": f"package p\n\nimport \"fmt\"\n\nvar k = fmt.Sprintf(\"%v%v\", {dq(A)}, {dq(B + C)})\n",
    "sprintf_text": f"package p\n\nimport \"fmt\"\n\nvar k = fmt.Sprintf(\"{A}%s%s\", {dq(B)}, {dq(C)})\n",
    "sprintf_raw_format": f"package p\n\nimport \"fmt\"\n\nvar k = fmt.Sprintf(`%s%s`, {bt(A)}, {bt(B + C)})\n",
    "parenthesised": f"package p\n\nvar k = ({dq(A)} + ({dq(B)} + {dq(C)}))\n",
    "multi_line_chain": f"package p\n\nvar k = {dq(A)} +\n\t{dq(B)} +\n\t{dq(C)}\n",
    "in_a_call": f"package p\n\nfunc f() {{\n\tclient({dq(A)} + {dq(B)} + {dq(C)})\n}}\n",
}
GO_LINES = {"concat": 3, "concat_chain_raw": 3, "join": 5, "join_multi_line": 5, "sprintf_s": 5, "sprintf_v": 5,
            "sprintf_text": 5, "sprintf_raw_format": 5, "parenthesised": 3, "multi_line_chain": 3,
            "in_a_call": 4}


@pytest.mark.parametrize("name", sorted(GO_SPLITS))
def test_go_key_split_is_found_for_person_and_agent(repo, name):
    src = GO_SPLITS[name]
    assert both(repo, "shop/k.go", src) == [(GO_LINES[name], "block")]


@pytest.mark.parametrize("src", [
    f"package p\n\nconst k = {dq(A)} + {dq('short')}\n",
    f"package p\n\nconst k = {dq('hello')} + {dq(' ')} + {dq('world')}\n",
    f"package p\n\nvar k = {dq(A)} + suffix\n",
    f"package p\n\nimport \"fmt\"\n\nvar k = fmt.Sprintf(\"%s%s\", {dq(A)}, suffix)\n",
    f"package p\n\nimport \"fmt\"\n\nvar k = fmt.Sprintf(\"%s-%s\", {dq(A)}, {dq(B + C)})\n",   # the dash stays
    f"package p\n\nimport \"fmt\"\n\nvar k = fmt.Sprintf(\"%s%s\", {dq(A)})\n",                 # too few arguments
    f"package p\n\nimport \"fmt\"\n\nvar k = fmt.Sprintf(\"%x%s\", {dq(A)}, {dq(B + C)})\n",      # a verb that is not %s / %v
    f"package p\n\nimport \"strings\"\n\nvar k = strings.Join([]string{{{dq(A)}, part}}, \"\")\n",
    f"package p\n\nimport \"strings\"\n\nvar k = strings.Join(parts, \"\")\n",
    f"package p\n\nconst k = {dq(A)} + {dq('EXAMPLEEXAMPLEEX')}\n",
    f"package p\n\nconst k = {dq(A + 'IOSFODNN7' + 'EXAMPLE')} + {dq('x')}\n",
    f"package p\n\nconst d = {dq('9f86d081884c7d659a2feaa0c55ad015')} + {dq('a3bf4f1b2b0b822cd15d6c15b0f00a08')}\n",
    f"package p\n\n// {dq(A)} + {dq(B + C)}\nconst k = 1\n",
    f"package p\n\nconst r = '\"' + \"x\"\nconst s = {dq(B)} + {dq(C)}\n",
])
def test_go_key_split_negatives(repo, src):
    assert both(repo, "shop/k.go", src) == []


# ---------------------------------------------------------------- the fold stays fast and does not crash

def timed(repo, rel, src, env):
    staged(repo, rel, src)
    start = time.perf_counter()
    res = repo.check(env=env)
    return res, time.perf_counter() - start


@pytest.mark.parametrize("env", WHO)
def test_js_fold_is_linear_on_a_line_with_many_constants(repo, env):
    items = ",".join(["'a'"] * 60000)
    src = f"const g = {sq('x')} + {sq('y')};\nconst X = [{items}];\nconst k = {sq(A)} + {sq(TAIL)};\n"
    res, elapsed = timed(repo, "src/big.js", src, env)
    assert hits(res) == [(3, "block")]
    assert elapsed < 5, f"{elapsed:.1f} s"


@pytest.mark.parametrize("env", WHO)
def test_ts_template_fold_is_linear_with_many_interpolations(repo, env):
    body = "${'a'}" * 20000
    src = f"const g = {sq('x')} + {sq('y')};\nconst X = `{body}`;\nconst k = `{A}${{{sq(TAIL)}}}`;\n"
    res, elapsed = timed(repo, "src/big.ts", src, env)
    assert hits(res) == [(3, "block")]
    assert elapsed < 5, f"{elapsed:.1f} s"


@pytest.mark.parametrize("env", WHO)
def test_go_fold_is_linear_on_a_line_with_many_constants(repo, env):
    items = ",".join(['"a"'] * 60000)
    src = f"package p\n\nvar g = {dq('x')} + {dq('y')}\nvar X = []string{{{items}}}\nvar k = {dq(A)} + {dq(TAIL)}\n"
    res, elapsed = timed(repo, "shop/big.go", src, env)
    assert hits(res) == [(5, "block")]
    assert elapsed < 5, f"{elapsed:.1f} s"


@pytest.mark.parametrize("env", WHO)
def test_js_deep_chain_and_deep_nesting_do_not_crash_or_hang(repo, env):
    chain = " + ".join([dq("a")] * 3000)
    nest = "(" * 3000 + dq("a") + ")" * 3000
    src = (f"const g = {sq('x')} + {sq('y')};\nconst c = {chain};\nconst n = {nest};\n"
           f"const k = {sq(A)} + {sq(TAIL)};\n")
    res, elapsed = timed(repo, "src/deep.js", src, env)
    assert hits(res) == [(4, "block")]
    assert elapsed < 5, f"{elapsed:.1f} s"


@pytest.mark.parametrize("env", WHO)
def test_go_deep_chain_and_deep_nesting_do_not_crash_or_hang(repo, env):
    chain = " + ".join([dq("a")] * 3000)
    nest = "(" * 3000 + dq("a") + ")" * 3000
    src = f"package p\n\nvar g = {dq('x')} + {dq('y')}\nvar c = {chain}\nvar n = {nest}\nvar k = {dq(A)} + {dq(TAIL)}\n"
    res, elapsed = timed(repo, "shop/deep.go", src, env)
    assert hits(res) == [(6, "block")]
    assert elapsed < 5, f"{elapsed:.1f} s"


def test_a_long_chain_that_is_itself_the_key_is_found(repo):
    pieces = [dq(c) for c in A + TAIL]
    assert both(repo, "src/a.js", "const k = " + " + ".join(pieces) + ";\n") == [(1, "block")]
