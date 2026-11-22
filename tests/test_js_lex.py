"""The JavaScript / TypeScript lexer view (code / string / comment per character) that the line rules read, and the line from which it
stops being trusted: an unclosed single-line quote (a lone apostrophe in JSX text, say) makes every line from there on "code".
Table driven: each row is a source text and the region (C code, S string, M comment) at the first character of each needle."""
import pytest

from conftest import aigate

JS = aigate.language_for("a.ts").lex
KIND = {"C": aigate.CODE, "S": aigate.STR, "M": aigate.COM}

# name -> (source, ((needle, region), ...)); a needle may carry "#2" for its second occurrence
ROWS = {
    "dq": ('x = "a b"; y', (("x", "C"), ('"a b"', "S"), ("a b", "S"), ("; y", "C"), ("y", "C"))),
    "sq": ("x = 'a b'; y", (("'a b'", "S"), ("; y", "C"))),
    "tpl": ("x = `a b`; y", (("`a b`", "S"), ("; y", "C"))),
    "tpl-expr": ("x = `a ${b} c`; y", (("a ", "S"), ("${", "S"), ("b", "C"), (" c`", "S"), ("; y", "C"))),
    "tpl-nested": ('x = `${ `${"}"}` }`; z', (('"}"', "S"), ("; z", "C"), ("z", "C"))),
    "tpl-comment-in-expr": ("x = `${ /* } */ 1}`; z", (("/* } */", "M"), ("1", "C"), ("; z", "C"))),
    "tpl-multiline": ("x = `a\nb \"c\" 'd'\ne`;\ny", (("b ", "S"), ('"c"', "S"), ("'d'", "S"), (";\ny", "C"))),
    "tpl-escaped-tick": ("x = `a\\`b` + c", (("a\\`b", "S"), ("+ c", "C"))),
    "tpl-escaped-dollar": ("x = `\\${not code}` + c", (("not code", "S"), ("+ c", "C"))),
    "tpl-two-exprs": ("`${a}x${b}`; z", (("a", "C"), ("x", "S"), ("b", "C"), ("; z", "C"))),
    "sq-dollar-brace": ("x = '${y}'; z", (("${y}", "S"), ("; z", "C"))),
    "dq-has-line-comment": ('u = "http://x.y"; // real', (("//x.y", "S"), ("// real", "M"))),
    "dq-has-block-comment": ('u = "a /* b */ c"; d', (("/* b */", "S"), ("; d", "C"))),
    "line-comment-quote": ("// don't do it\nx = 1", (("'", "M"), ("x", "C"))),
    "line-comment-tick": ("a = 1 // `q\nb = 2", (("`", "M"), ("b", "C"))),
    "block-comment": ("a /* x \"y' z */ b", (("x", "M"), ('"', "M"), ("b", "C"))),
    "block-comment-multiline": ("a /* x\n y */ b", (("y", "M"), ("b", "C"))),
    "escape-dq": ('s = "a\\"b" + c', (('a\\"b', "S"), ("+ c", "C"))),
    "escape-sq": ("s = 'a\\'b' + c", (("a\\'b", "S"), ("+ c", "C"))),
    "escaped-backslash": ('s = "a\\\\" + c', (("+ c", "C"),)),
    "line-continuation": ('s = "ab\\\ncd" + c', (("cd", "S"), ("+ c", "C"))),
    "two-strings": ("a('x', \"y\"); b", (("'x'", "S"), ("\"y\"", "S"), ("; b", "C"))),
    "regex-quote": ('x = /ab"c/.test(s)', (('"', "S"), ("/ab", "S"), (".test", "C"))),
    "regex-then-code": ('x = /"/; eval(a)', (('"', "S"), ("eval", "C"), ("(a)", "C"))),
    "regex-class-slash": ('x = /[/"]/.test(s); eval(a)', (('"', "S"), ("eval", "C"))),
    "regex-flags": ("x = /a+/gi; y", (("a+", "S"), ("; y", "C"))),
    "regex-escaped-slash": ('x = /a\\/"/; eval(q)', (('"', "S"), ("eval", "C"))),
    "return-regex": ('function f() { return /re"/.test(s); }', (('re"', "S"), (".test", "C"))),
    "typeof-regex": ('x = typeof /a"/;\ny', (('a"', "S"),)),
    "after-comma-regex": ('f(a, /"/); eval(b)', (('"', "S"), ("eval", "C"))),
    "after-colon-regex": ("x = c ? /'/ : /\"/; eval(b)", (("'", "S"), ('"', "S"), ("eval", "C"))),
    "division": ('x = a / b / c; s = "q"', (("/ b /", "C"), ('"q"', "S"))),
    "division-after-paren": ('x = f(a) / 2 + g(b) / 3; s = "q"', (("/ 2", "C"), ("/ 3", "C"), ('"q"', "S"))),
    "division-after-bracket": ('x = a[0] / 2 + b[1] / 3; s = "q"', (("/ 2", "C"), ("/ 3", "C"), ('"q"', "S"))),
    "division-after-postfix": ('x = n++ / 2 + m-- / 3; s = "q"', (("/ 2", "C"), ("/ 3", "C"), ('"q"', "S"))),
    "division-after-number": ('x = 10 / 2 / 5; s = "q"', (("/ 2 /", "C"), ('"q"', "S"))),
    "division-after-member-return": ('x = obj.return / y / 2; s = "q"', (("/ y /", "C"), ('"q"', "S"))),
    "division-after-string": ('x = "a" / 2 / 3; s = "q"', (('"a"', "S"), ("/ 2 /", "C"), ('"q"', "S"))),
    "jsx-closing-tag": ("x = <b>hi</b>;\ny = 1", (("</b>", "C"), ("y", "C"))),
    "jsx-self-closing": ('x = <img src="a" />;\ny = 1', (('"a"', "S"), ("/>", "C"), ("y", "C"))),
    "jsx-comment": ("x = <p>{/* hi */}</p>;\ny", (("/* hi */", "M"), ("</p>", "C"))),
    "ts-generics": ('const m: Map<string, number> = f<number>(c); const t = "x"', (('"x"', "S"), ("Map", "C"))),
    "ts-arrow-generic": ('const f = <T,>(a: T) => a; const t = "x"', (('"x"', "S"),)),
    "ts-string-literal-type": ("type A = 'a' | 'b';\nconst c = 1", (("'a'", "S"), ("'b'", "S"), ("const", "C"))),
    "object-key": ('o = {"a": 1, \'b\': 2}', (('"a"', "S"), ("'b'", "S"), (": 1", "C"))),
    "unicode": ('x = "é😀" + c', (("é😀", "S"), ("+ c", "C"))),
    "line-separator-char": ('x = "a b" + c', ((" ", "S"), ("+ c", "C"))),
    "crlf": ('a = "x"\r\nb = \'y\' // z\r\nc', (('"x"', "S"), ("'y'", "S"), ("// z", "M"), ("c", "C"))),
    "bom": ('﻿x = "a" // b', (("﻿", "C"), ('"a"', "S"), ("// b", "M"))),
    "empty": ("", ()),
    "only-comment": ("// just this", (("just", "M"),)),
}


def at(src, needle):
    nth = 1
    if "#" in needle and needle.rsplit("#", 1)[1].isdigit():
        needle, nth = needle.rsplit("#", 1)[0], int(needle.rsplit("#", 1)[1])
    i = -1
    for _ in range(nth):
        i = src.index(needle, i + 1)
    return i


@pytest.mark.parametrize("name", sorted(ROWS))
def test_each_form_is_classified(name):
    src, checks = ROWS[name]
    lx = aigate.lex_text(src, JS)
    for needle, region in checks:
        assert lx.kinds[at(src, needle)] == KIND[region], (name, needle, region)
    assert lx.first_bad is None and lx.trusted_until > src.count("\n") + 1         # these are all well-formed


# ---- where the lexer cannot be trusted: an unclosed single-line quote ---------------------------------------------------------

@pytest.mark.parametrize("name,src,first_bad,line", [
    ("sq-first-line", "a = 'abc\nb = 1\n", 4, 1),
    ("dq-second-line", 'ok = 1\nx = "abc\ny = 2\n', 11, 2),
    ("jsx-apostrophe", "const a = <p>Don't</p>;\nconst b = 2;\n", 16, 1),
    ("dq-third-line-crlf", 'a = 1\r\nb = 2\r\nc = "x\r\nd = 4\r\n', 18, 3),
    ("bad-after-good-string", "a = 'ok'; b = 'bad\nc = 1\n", 14, 1),
])
def test_an_unclosed_quote_marks_the_rest_of_the_file_untrusted(name, src, first_bad, line):
    lx = aigate.lex_text(src, JS)
    assert lx.first_bad == first_bad
    assert lx.trusted_until == line
    n = src.count("\n") + 1
    assert all((ln < lx.trusted_until) == (ln < line) for ln in range(1, n + 1))


def test_every_later_line_is_still_classified_by_the_scanner():
    lx = aigate.lex_text("a = 'abc\nb = \"x\" // c\n", JS)
    assert lx.kind_at(2, 5) == aigate.STR and lx.kind_at(2, 10) == aigate.COM       # it recovers at the newline; the rules just do not rely on it


@pytest.mark.parametrize("src", [
    "a = 1;\nb = \"x\"; // it's\n",             # a quote inside a comment
    "a = \"it's\";\n",                           # a quote inside the other kind
    "a = /'/.test(s);\n",                        # a quote inside a regular expression
    "a = `it's \"q\"`;\n",                       # inside a template
    "a = 'x';\nb = \"y\";\n",
    "",
])
def test_nothing_is_untrusted_without_an_unclosed_quote(src):
    lx = aigate.lex_text(src, JS)
    assert lx.first_bad is None and lx.trusted_until > src.count("\n") + 1


# ---- malformed input: no view at all (the rules fall back to line-based behaviour) --------------------------------------------

@pytest.mark.parametrize("name,src", [
    ("template", "a = `never closed\nb = 1\n"),
    ("block-comment", "a = 1 /* never closed\nb = 2\n"),
    ("template-expr", "a = `x ${ y\nb = 2\n"),
    ("quote-in-template-expr", "x = `${ 'oops }`;\ny\n"),
    ("sq-at-eof", "a = 'never closed"),
    ("dq-at-eof", 'a = "never closed'),
    ("too-deep", "`${" * 60 + "x" + "}`" * 60),
])
def test_malformed_input_gives_no_view(name, src):
    with pytest.raises(aigate.LexError):
        aigate.lex_text(src, JS)


def test_a_minified_line_is_lexed_in_linear_time():
    import time
    src = ("a=b/c+d/e;" + 'x="y";' + "z=`w`;" * 3) * 5000
    t0 = time.perf_counter()
    lx = aigate.lex_text(src, JS)
    assert time.perf_counter() - t0 < 5.0
    assert lx.kinds[src.index('"y"')] == aigate.STR


def test_lines_and_columns_match_with_a_bom_and_crlf():
    lx = aigate.lex_text('﻿x = "a"\r\ny = 1 // c\r\n', JS)
    assert lx.line(1).rstrip("\r") == '﻿x = "a"'
    assert lx.kind_at(1, 5) == aigate.STR and lx.kind_at(2, 6) == aigate.COM


# ---- a "/" that is not a regular expression to the lexer, but could be one: the view cannot be trusted from there ----------------
# After `)`, `}`, `]`, a name or a number the lexer reads `/` as division. If a regular expression COULD start there (it would close on
# the same line) and the span holds a quote, the quote pair the lexer sees may be the wrong one, and real code between two regular
# expressions would be read as the inside of a string. That marks the line untrusted (the rules then read it and every later line
# as code): stricter, never softer. Only `first_bad` is set; the classification itself is unchanged.
AFTER_PAREN = 'if (ok) /"/.test(a) && md5(password) && /"/.test(b);\n'
BAD_AT_SLASH = {
    "after-paren": (AFTER_PAREN, "/\"/.test"),
    "after-paren-sq": ("function f(s) {\n  if (s) /'/.test(s) && md5(password) && /'/.test(s);\n}\n", "/'/.test(s)"),
    "after-brace": ("x = y }\n/'/.test(s) && md5(password) || /'/.exec(s);\n", "/'/.test"),
    "after-bracket": ('a[0] /"/.test(s) && md5(password) && /"/.test(t);\n', '/"/.test'),
    "after-name": ('v /"/.test(s) && md5(password) && /"/.test(t);\n', '/"/.test'),
    "after-number": ("n = 1 /'/.test(s) && md5(password) && /'/.test(t);\n", "/'/.test"),
    "after-postfix": ('n++ /"/.test(s) && md5(password) && /"/.test(t);\n', '/"/.test'),
    "after-member-keyword": ("o.return /'/.test(s) && md5(password) && /'/.test(t);\n", "/'/.test"),
    "backquote-in-span": ("if (ok) /`/.test(a) && md5(password) && /`/.test(b);\n", "/`/.test"),
    "class-with-slash": ('if (ok) /[/"]/.test(a) && md5(password) && /"/.test(b);\n', '/[/"]/.test'),
    "division-with-quotes": ('a / b + "x" / c\n', "/ b"),
    "division-then-string-with-slash": ('x = a/2; s = "a/b"\n', "/2"),
    "division-and-single-quote": ("n = 1 / 2; t = 'it/s'\n", "/ 2"),
}


@pytest.mark.parametrize("name", sorted(BAD_AT_SLASH))
def test_a_slash_that_could_open_a_regex_with_a_quote_marks_the_view_untrusted(name):
    src, needle = BAD_AT_SLASH[name]
    lx = aigate.lex_text(src, JS)
    assert lx.first_bad == src.index(needle), name
    assert lx.trusted_until == src[:lx.first_bad].count("\n") + 1


@pytest.mark.parametrize("name", sorted(BAD_AT_SLASH))
def test_the_classification_of_the_same_text_is_not_changed(name):
    """The flag is all that is new: the kinds are what they were (a closed string between the two slashes), for the other callers."""
    src, _needle = BAD_AT_SLASH[name]
    lx = aigate.lex_text(src, JS)
    assert len(lx.kinds) == len(src)


FINE = {
    "division-one-slash": 'a / b; s = "x"\n',
    "division-chain": 'x = a / b / c; s = "q"\n',
    "division-no-quotes": "x = a / 2 + b / 3;\n",
    "slash-only-in-a-string": 's = "a/b"; n = 1\n',
    "slash-in-a-comment": 'n = 1 // a / "b" / c\n',
    "slash-in-a-template": "t = `a / b / ${c}`; n = 1\n",
    "division-quote-after-last-slash": 'x = f(a) / 2 + g(b) / 3; s = "q"\n',
    "span-on-two-lines": 'n = a /\n"x" / b\n',
    "jsx-self-closing": 'x = <img src="a" />;\ny = 1\n',
    "jsx-closing-tag": "x = <b>'hi'</b>;\ny = 1\n",
    "regex-where-a-value-may-start": 'x = /"/; y = 1\n',
    "increment-then-quote-line": "n++ / 2; s = 'a'\n",
}


@pytest.mark.parametrize("name", sorted(FINE))
def test_a_slash_that_cannot_hold_a_regex_with_a_quote_leaves_the_view_trusted(name):
    lx = aigate.lex_text(FINE[name], JS)
    assert lx.first_bad is None, name


def test_go_has_no_regular_expression_literal_so_a_slash_never_marks_the_view():
    for src in ('a := b / c + "x" / d\n', 'x := a / 2 / 3; s := "q"\n', "x := f(a) /'/ g(b) /'/ h\n"):
        assert aigate.lex_text(src, aigate.language_for("a.go").lex).first_bad is None


def test_a_line_full_of_slashes_and_an_open_class_is_judged_in_linear_time():
    import time
    src = ("a/[b/[c" * 3000 + "'x'\n") * 3
    t0 = time.perf_counter()
    aigate.lex_text(src, JS)                                    # either answer is safe; what matters is that it ends quickly
    assert time.perf_counter() - t0 < 5.0


# ---- more places where the view cannot be trusted ----------------------------------------------------------------------------------
# JSX text (`<p>Don't</p>`), an HTML comment, a division read as a regex (after a variable named `p`, after `>` or a non-null `!`) and a
# closed "string" that holds code all make `first_bad` set; a file that looks like it holds JSX is read plainly altogether (`has_jsx`).
JSX_TRUE = {
    "element": "x = <p>a</p>;\n", "fragment": "x = <></>;\n", "self-closing": "x = <br />;\n", "self-closing-attr": "x = <Foo a={1}/>;\n",
    "html-comment": "<!-- a comment\nx = 1;\n", "hint-in-a-string": 'x = "</div>";\n', "hint-in-a-comment": "// see </b>\nx = 1;\n",
    "triple-slash": '/// <reference path="a.d.ts"/>\nx = 1;\n', "closing-fragment": "x = <>a</>;\n",
}
JSX_FALSE = {
    "generic-call": "x = a<b>(c);\n", "comparison": "if (a < b && c > d) {}\n", "generic-arrow": "const f = <T,>(a: T) => a;\n",
    "new-generic": "const m = new Map<string, number>();\n", "as-generic": "k = a as C<D>;\n", "nested-generic": "type X = Array<Y<Z>>;\n",
    "string-with-words": 'x = "use sha1(password) never";\n', "plain-regex": "x = /ab+/g.test(s);\n", "less-than-slash": "i = a < b / 2;\n",
    "type-assertion": "let z = <string>y;\n", "empty": "",
}


@pytest.mark.parametrize("name", sorted(JSX_TRUE))
def test_text_that_looks_like_jsx_is_detected(name):
    assert aigate.lex_text(JSX_TRUE[name], JS).has_jsx(), name


@pytest.mark.parametrize("name", sorted(JSX_FALSE))
def test_ordinary_typescript_is_not_taken_for_jsx(name):
    assert not aigate.lex_text(JSX_FALSE[name], JS).has_jsx(), name


UNTRUSTED_FROM = {
    "apostrophe-in-text": ("x = <p>Don't</p>; y = 1;\n", "'t"),
    "apostrophe-without-tags": ("a = b Don't c; d = 'e';\n", "'t"),
    "its": ("ok = 1;\nx = it's ok; y = 'z';\n", "'s"),
    "after-paren": ("x = f(a)'b'; y = 1;\n", "'b'"),
    "after-brace": ("x = {a}'s'; y = 1;\n", "'s'"),
    "after-bracket": ("x = a[0]'b'; y = 1;\n", "'b'"),
    "after-digit": ("x = 1'b'; y = 1;\n", "'b'"),
    "string-holding-code": ('x = "a; const h = f(b); c"; y = 1;\n', '"a;'),
    "string-holding-and-call": ("x = 'a && md5(b)'; y = 1;\n", "'a &&"),
    "string-holding-plus-call": ("x = 'a + sha1(b)'; y = 1;\n", "'a +"),
    "variable-p-divided": ("r = p / 100; q = r / 4;\n", "/ 100"),
    "variable-or-divided": ("r = or / 2; q = r / 4;\n", "/ 2"),
    "non-null-divided": ("y = a! / 2; z = b! / 3;\n", "/ 2"),
    "after-greater-than": ("x = a >/2; y = b >/3;\n", "/2"),
    "blank-window-before-regex": ("x = 1;\n" + " " * 60 + "/re/.test(y);\n", "/re/"),
}
TRUSTED = {
    "keyword-then-string": "function f() { return'x'; }\nx = typeof'y';\n",
    "case-string": "switch (a) { case'x': break; }\n",
    "plain-strings": "x = 'a'; y = \"b\"; z = f('c', \"d\"); o = {'k': 1}; t = a ? 'b':'c';\n",
    "use-strict-at-offset-zero": "'use strict'\nx = 1;\n",
    "probe-lines": "m = \"remember: // TODO later and sha1(password) is weak\";\nn = 'use md5(token) never';\n",
    "semicolon-prose": 'm = "tip: x.md5(password) is weak; use sha256 for a password";\n',
    "cookie-string": 'document.cookie = "a=1; path=/";\n',
    "message-with-parens": 'm = "Error: bad input; retry (later)";\n',
    "arrow-then-regex": "f = x => /re/.test(x);\n",
    "negated-regex": "if (!/x/.test(y)) {}\n",
    "return-regex": "function f(s) { return /re/.test(s); }\n",
    "regex-after-equals": "x = /re/g;\n",
    "regex-at-file-start": "/re/.test(x);\n",
    "keyword-in-property": "o.in / 2 / 3;\n",
}


@pytest.mark.parametrize("name", sorted(UNTRUSTED_FROM))
def test_these_make_the_view_untrusted_from_the_first_line_that_has_them(name):
    src, needle = UNTRUSTED_FROM[name]
    lx = aigate.lex_text(src, JS)
    assert lx.first_bad is not None, name
    assert lx.first_bad == src.index(needle) or name.startswith("string-holding") or name == "blank-window-before-regex", (name, lx.first_bad)


@pytest.mark.parametrize("name", sorted(TRUSTED))
def test_ordinary_code_keeps_the_view_trusted(name):
    assert aigate.lex_text(TRUSTED[name], JS).first_bad is None, name


def test_go_is_not_affected_by_the_javascript_division_and_text_hints():
    go = aigate.language_for("a.go").lex
    for src in ("x := a/2; y := p / 3\n", "x := a! / 2\n", "s := `a; const h = f(b); c`\n"):
        assert aigate.lex_text(src, go).first_bad is None, src
    assert aigate.lex_text('s := "a; const h = f(b); c"\n', go).first_bad == 5      # a Go string that holds code is as doubtful as a JS one
