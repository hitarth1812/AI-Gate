"""Property test for the JavaScript and Go lexer view: for random soups of the characters that confuse a lexer (quotes, backquotes,
slashes, comment openers, `${`, braces, escapes) mixed with the words the line rules look for, the findings WITH the lexer are a subset of
the findings WITHOUT it (the lexer may only remove a finding that is text inside a string, never invent or move one), and the two are
equal on every line before the first line that holds a quote, a backquote or a slash. It also pins the switch that turns the string-aware
view off for a language (the rollback). `hypothesis` is a dev-only dependency (requirements-dev.txt); the tests are skipped without it.

The examples are fixed (derandomized) so CI is reproducible; set AIGATE_FUZZ_RANDOM=1 (the nightly run does) for a random seed, and
AIGATE_FUZZ_EXAMPLES=N for more of them."""
import os

import pytest

from conftest import aigate

hypothesis = pytest.importorskip("hypothesis")
st = pytest.importorskip("hypothesis.strategies")
given, settings, HealthCheck = hypothesis.given, hypothesis.settings, hypothesis.HealthCheck

TODO, SHA1, MD5, ESL = "TO" + "DO", "sha" + "1", "md" + "5", "eslint-" + "disable-line"
TOKENS = ['"', "'", "`", "/", "/*", "*/", "//", "${", "}", "{", "(", ")", "[", "]", "\\", "\n", "\n", " ", " ", ";", "=", "+", "<", ">", "++",
          "return ", "x", "a", "=> ", ": ", ", ", f"{SHA1}(password)", f"{MD5}(token)", f"{SHA1}", "password", "token", "password = ",
          "Zx9kQ2mT7vRw", "eval(", "debugger", f"// {TODO}", f"/* {TODO} */", f"// {ESL}", "//no" + "lint", "@ts-" + "ignore", "Math.random()",
          "createHash(", "typeof ", "in ", "of ",
          "if (x) /\"/", "} /'/", ") /\"/.test(", "a[0] /'/", "n++ /\"/", "/\"/.test(a) && ", " && /\"/.test(b);",
          "<p>Don't</p>", "<p>Don't panic</p>; ", "/'/g;", "/'/", "<b>it's</b> ", "</p>", "<br />", "<>", "p / 100; ", "a! / 2; ", "Don't "]
SOUP = st.lists(st.sampled_from(TOKENS), max_size=60).map("".join)
RANDOM = bool(os.environ.get("AIGATE_FUZZ_RANDOM"))
EXAMPLES = int(os.environ.get("AIGATE_FUZZ_EXAMPLES") or 400)
SETTINGS = settings(max_examples=EXAMPLES, deadline=None, derandomize=not RANDOM, suppress_health_check=[HealthCheck.too_slow])
SPECIAL = set("'\"`/")
RULES = {"code.todo", "security.weak_crypto", "suppress.lint", "suppress.type", "suppress.security", "suppress.blanket", "suppress.coverage",
         "secrets.generic", "secrets.hardcoded", "code.debug_leftover", "security.eval", "security.shell", "security.sql_injection"}


class Ctx:
    """Just enough of a check context for the per-line checks: the lexer view is built on request, or withheld."""

    def __init__(self, text, use_lexer):
        self.text, self.use_lexer = text, use_lexer

    def is_test(self, path):
        return False

    def ignored(self, path):
        return False

    def sensitive(self, path):
        return False

    def lexed(self, fc, which="new"):
        if not self.use_lexer or which != "new":
            return None
        try:
            return aigate.lex_text(self.text, aigate.language_for(fc.path).lex)
        except aigate.LexError:
            return None


def findings(text, path, use_lexer=True):
    ctx = Ctx(text, use_lexer)
    added = {i: ln for i, ln in enumerate(text.split("\n"), 1) if ln}
    fc = aigate.FileChange(path, None, "A", "100644", added=added)
    got = {(f.rule, f.line) for f in aigate._code_lines_file(ctx, fc) + aigate._suppressions_file(ctx, fc)}
    for line, ln in added.items():
        got |= {(f.rule, f.line) for f in aigate.secret_findings(ctx, fc, line, ln)}
    return {g for g in got if g[0] in RULES}


@pytest.mark.parametrize("path", ["src/a.js", "src/a.ts", "src/a.go"])
@SETTINGS
@given(text=SOUP)
def test_the_lexer_never_adds_a_finding_and_changes_nothing_on_plain_lines(path, text):
    with_lexer, without = findings(text, path), findings(text, path, use_lexer=False)
    assert with_lexer <= without, (sorted(with_lexer - without), text)
    lines = text.split("\n")
    first = next((i for i, ln in enumerate(lines, 1) if SPECIAL & set(ln)), len(lines) + 1)
    assert {g for g in with_lexer if g[1] < first} == {g for g in without if g[1] < first}, text


@pytest.mark.parametrize("path", ["src/a.js", "src/a.go"])
@SETTINGS
@given(head=SOUP, tail=SOUP)
def test_a_line_after_an_unclosed_quote_is_judged_as_without_the_lexer(path, head, tail):
    """After a line that opens a quote it never closes, every later line gets the line-based answer (the rest of the file is code)."""
    text = f"{head.replace(chr(10), ' ')}\nconst s = 'open\n{tail}"
    try:
        lx = aigate.lex_text(text, aigate.language_for(path).lex)
    except aigate.LexError:
        return                                                  # the whole file already falls back
    if lx.has_jsx():
        return                                                  # a file that looks like JSX has the plain reading (tested below)
    bad = lx.trusted_until
    with_lexer, without = findings(text, path), findings(text, path, use_lexer=False)
    assert {g for g in with_lexer if g[1] >= bad} == {g for g in without if g[1] >= bad}, (bad, text)


# ---- the rollback: one switch per language returns "everything is code" ------------------------------------------------------

@pytest.mark.parametrize("path,lang,text", [
    ("src/a.js", "js", f'const t = "use {SHA1}(password) never";\n'),
    ("src/a.go", "go", f'package k\n\nvar t = "use {SHA1}(password) never"\n'),
])
def test_the_switch_restores_the_line_based_result_for_one_language(monkeypatch, path, lang, text):
    assert not any(r == "security.weak_crypto" for r, _ in findings(text, path))
    monkeypatch.setitem(aigate.STRING_VIEW, lang, False)
    assert ("security.weak_crypto", 1 if lang == "js" else 3) in findings(text, path)
    other = "src/a.go" if lang == "js" else "src/a.js"
    other_text = f'const t = "use {SHA1}(password) never";\n' if lang == "go" else f'package k\n\nvar t = "use {SHA1}(password) never"\n'
    assert not any(r == "security.weak_crypto" for r, _ in findings(other_text, other))        # the other language is unaffected


def test_a_generic_secret_in_a_string_is_text_unless_the_switch_is_off(monkeypatch):
    text = "const t = 'password = \"Zx9kQ2mT7vRw\"';\n"
    assert ("secrets.generic", 1) not in findings(text, "src/a.js")
    monkeypatch.setitem(aigate.STRING_VIEW, "js", False)
    assert ("secrets.generic", 1) in findings(text, "src/a.js")


# ---- the weak call between two regular-expression-shaped spans is code: it must be reported whatever surrounds it ---------------------

SAFE = ["a = 1;", "s = 'x';", 't = "y";', "n = a / 2;", "// a comment", "/* a comment */", "r = /ab/;", "", "u = `t`;", "p = f(x) / 2;"]
TRAPS = ['if (x) /"/.test(a) && {w}(password) && /"/.test(b);', "} /'/.test(s) && {w}(token) || /'/.exec(s);",
         'a[0] /"/.test(s) && {w}(secret) && /"/.test(t);', "f(x) /`/.test(s) && {w}(password) && /`/.test(t);",
         'n++ /"/.test(s) && {w}(password) && /"/.test(t);', "v /'/.test(s) && {w}(password) && /'/.test(t);",
         'x = a / 2 + "s" / {w}(password);', "(a) /'/.test(s) && {w}(password) && /\"/.test(t);", "r = p / 100; {w}(password); q = r / 4;",
         "y = a! / 2; {w}(token); z = b! / 3;", "Don't {w}(password) it's", "s = 'quote {w}(password); /'/;", "<!-- don't {w}(password)",
         "x = a >/2; {w}(password); y = b >/3;", "u = or / 2; {w}(secret); v = and / 3;"]


@pytest.mark.parametrize("path", ["src/a.js", "src/a.ts"])
@SETTINGS
@given(head=st.lists(st.sampled_from(SAFE), max_size=4), trap=st.sampled_from(TRAPS), weak=st.sampled_from([MD5, SHA1]),
       tail=st.lists(st.sampled_from(SAFE), max_size=3))
def test_a_weak_call_between_regex_shaped_spans_is_never_hidden(path, head, trap, weak, tail):
    line = trap.replace("{w}", weak)
    text = "\n".join(head + [line] + tail) + "\n"
    assert ("security.weak_crypto", len(head) + 1) in findings(text, path), text


# ---- .jsx and .tsx keep the plain line reading: no finding is lost to a lone apostrophe in JSX text ---------------------------------

JSX_TRAPS = ["const el = <p>Don't</p>; const h = {w}(password); const re = /'/;", "const el = <p>Don't panic</p>; const h = {w}(token); const re = /'/g;",
             "const el = <b>it's</b> + {w}(secret) + /'/.source;", 'const el = <p>say "hi</p>; const h = {w}(password); const re = /"/;',
             "x = <a>don't</a>; {w}(password); y = /'/;"]


def with_switch(on, fn, *args):
    old = dict(aigate.STRING_VIEW)
    aigate.STRING_VIEW.update({"js": on, "go": on})
    try:
        return fn(*args)
    finally:
        aigate.STRING_VIEW.clear()
        aigate.STRING_VIEW.update(old)


@pytest.mark.parametrize("path", ["src/a.jsx", "src/a.tsx", "src/A.TSX"])
@SETTINGS
@given(text=SOUP)
def test_jsx_and_tsx_findings_equal_the_switched_off_reading(path, text):
    assert findings(text, path) == with_switch(False, findings, text, path), text


@pytest.mark.parametrize("path", ["src/a.jsx", "src/a.tsx"])
@SETTINGS
@given(head=st.lists(st.sampled_from(SAFE), max_size=3), trap=st.sampled_from(JSX_TRAPS), weak=st.sampled_from([MD5, SHA1]),
       tail=st.lists(st.sampled_from(SAFE), max_size=3))
def test_a_weak_call_next_to_jsx_text_with_an_apostrophe_is_never_lost(path, head, trap, weak, tail):
    text = "\n".join(head + [trap.replace("{w}", weak)] + tail) + "\n"
    assert ("security.weak_crypto", len(head) + 1) in findings(text, path), text


@pytest.mark.parametrize("path", ["src/a.js", "src/a.ts", "src/a.mjs", "src/a.cjs"])
@SETTINGS
@given(text=SOUP)
def test_a_script_file_that_looks_like_jsx_gets_the_plain_reading(path, text):
    try:
        lx = aigate.lex_text(text, aigate.language_for(path).lex)
    except aigate.LexError:
        return
    if lx.has_jsx():
        assert findings(text, path) == with_switch(False, findings, text, path), text
