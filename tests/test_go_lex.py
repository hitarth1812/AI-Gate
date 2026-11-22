"""The Go lexer view (code / string / comment per character) that the line rules read, and the line from which it stops being
trusted (an unclosed interpreted string or rune). Table driven: each row is a source text and the region (C code, S string,
M comment) at the first character of each needle."""
import pytest

from conftest import aigate

GO = aigate.language_for("a.go").lex
KIND = {"C": aigate.CODE, "S": aigate.STR, "M": aigate.COM}

ROWS = {
    "interpreted": ('x := "a b"; y', (("x", "C"), ('"a b"', "S"), ("a b", "S"), ("; y", "C"))),
    "raw": ("x := `a b`; y", (("`a b`", "S"), ("; y", "C"))),
    "raw-with-quote": ('x := `say "hi"`; y', (('"hi"', "S"), ("; y", "C"))),
    "raw-with-line-comment": ("x := `a // b`; y", (("// b", "S"), ("; y", "C"))),
    "raw-with-block-comment": ("x := `a /* b */ c`; y", (("/* b */", "S"), ("; y", "C"))),
    "raw-multiline": ("x := `a\n\"b\" // c\nd`\ny", (('"b"', "S"), ("// c", "S"), ("\ny", "C"))),
    "raw-backslash-is-literal": ("x := `a\\`; y", (("; y", "C"), ("a\\", "S"))),
    "raw-struct-tag": ('type T struct{ ID int `json:"id"` }\nvar y int', (('json:"id"', "S"), ("var", "C"))),
    "raw-after-interpreted": ('x := "a" + `b "c"`; y', (('"a"', "S"), ('"c"', "S"), ("; y", "C"))),
    "interpreted-escaped-quote": ('x := "a\\"b" + c', (('a\\"b', "S"), ("+ c", "C"))),
    "interpreted-escaped-backslash": ('x := "a\\\\" + c', (("+ c", "C"),)),
    "interpreted-has-comment-marks": ('x := "// no /* no */"; y', (("// no", "S"), ("; y", "C"))),
    "interpreted-has-backquote": ('x := "a`b"; y', (("`", "S"), ("; y", "C"))),
    "no-interpolation": ('x := "${y} {{z}}"; w', (("${y}", "S"), ("; w", "C"))),
    "rune": ("c := 'a'; y", (("'a'", "S"), ("; y", "C"))),
    "rune-double-quote": ("c := '\"'; eval(a)", (("'\"'", "S"), ("eval", "C"))),
    "rune-escaped-quote": ("c := '\\''; y", (("'\\''", "S"), ("; y", "C"))),
    "rune-escaped-backslash": ("c := '\\\\'; y", (("; y", "C"),)),
    "rune-newline": ("c := '\\n'; y", (("'\\n'", "S"), ("; y", "C"))),
    "rune-hex": ("c := '\\x41'; y", (("\\x41", "S"), ("; y", "C"))),
    "rune-unicode": ("c := 'é'; y", (("é", "S"), ("; y", "C"))),
    "rune-backquote": ("c := '`'; y", (("`", "S"), ("; y", "C"))),
    "rune-then-string": ("a, b := '\"', \"x\"; y", (("'\"'", "S"), ('"x"', "S"), ("; y", "C"))),
    "line-comment": ("x := 1 // it's `here\ny := 2", (("'", "M"), ("`", "M"), ("y", "C"))),
    "line-comment-quote": ('// say "hi\nx := 1', (('"', "M"), ("x", "C"))),
    "block-comment": ('a /* x "y` z */ b', (("x", "M"), ('"', "M"), ("`", "M"), ("b", "C"))),
    "block-comment-multiline": ("a /* x\n y */ b", (("y", "M"), ("b", "C"))),
    "block-comment-star-slash": ("a /**/ b", (("/**/", "M"), ("b", "C"))),
    "comment-after-string": ('x := "a" // b', (('"a"', "S"), ("// b", "M"))),
    "url-in-string": ('u := "http://x.y/z" // real', (("//x.y", "S"), ("// real", "M"))),
    "division": ("x := a / b / c; s := \"q\"", (("/ b /", "C"), ('"q"', "S"))),
    "division-then-quote": ("x := n / 2 // it's\ny := 1", (("/ 2", "C"), ("'", "M"), ("y", "C"))),
    "generics": ('var m Map[string]int\nvar s = "x"', (('"x"', "S"), ("Map", "C"))),
    "import-block": ('import (\n\t"fmt"\n\t_ "os"\n)\nvar y = 1', (('"fmt"', "S"), ('"os"', "S"), ("var", "C"))),
    "printf": ('fmt.Printf("%d // x %s", 1, "a")', (("%d", "S"), ("// x", "S"), ('"a"', "S"), ("fmt", "C"))),
    "go-directive": ("//go:generate stringer -type=T\nvar x = 1", (("go:generate", "M"), ("var", "C"))),
    "nolint-comment": ('x := 1 //nolint:gosec // why\n', (("//nolint", "M"),)),
    "nolint-in-string": ('x := "//nolint:gosec"\n', (("//nolint", "S"),)),
    "two-raw-strings": ("a, b := `x`, `y // z`; c", (("`x`", "S"), ("// z", "S"), ("; c", "C"))),
    "unicode-in-string": ('x := "日本語 😀" + c', (("日本語", "S"), ("+ c", "C"))),
    "crlf": ('a := "x"\r\nb := `y`\r\n// z\r\nc', (('"x"', "S"), ("`y`", "S"), ("// z", "M"), ("c", "C"))),
    "crlf-raw-multiline": ("a := `x\r\ny // z\r\n`\r\nb", (("// z", "S"), ("b", "C"))),
    "bom": ('\ufeffpackage k\nvar x = "a" // b', (("\ufeff", "C"), ('"a"', "S"), ("// b", "M"))),
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
    lx = aigate.lex_text(src, GO)
    for needle, region in checks:
        assert lx.kinds[at(src, needle)] == KIND[region], (name, needle, region)
    assert lx.first_bad is None and lx.trusted_until > src.count("\n") + 1         # these are all well-formed


@pytest.mark.parametrize("name,src,first_bad,line", [
    ("string-first-line", 'x := "abc\ny := 1\n', 5, 1),
    ("string-second-line", 'ok := 1\nx := "abc\ny := 2\n', 13, 2),
    ("rune-unclosed", "a := 1\nc := 'x\nb := 2\n", 12, 2),
    ("crlf", 'a := 1\r\nb := "x\r\nc := 3\r\n', 13, 2),
    ("after-a-good-string", 'a := "ok"; b := "bad\nc := 1\n', 16, 1),
])
def test_an_unclosed_quote_marks_the_rest_of_the_file_untrusted(name, src, first_bad, line):
    lx = aigate.lex_text(src, GO)
    assert lx.first_bad == first_bad
    assert lx.trusted_until == line


@pytest.mark.parametrize("src", [
    'a := 1\nb := "x" // it\'s\n',
    'a := "it\'s"\n',
    "a := `it's \"q\"`\n",
    "a := '\"'\n",
    "a := '\\''\n",
    "",
])
def test_nothing_is_untrusted_without_an_unclosed_quote(src):
    lx = aigate.lex_text(src, GO)
    assert lx.first_bad is None and lx.trusted_until > src.count("\n") + 1


@pytest.mark.parametrize("name,src", [
    ("raw", "a := `never closed\nb := 1\n"),
    ("block-comment", "a := 1 /* never closed\nb := 2\n"),
    ("string-at-eof", 'a := "never closed'),
    ("rune-at-eof", "a := 'x"),
])
def test_malformed_input_gives_no_view(name, src):
    with pytest.raises(aigate.LexError):
        aigate.lex_text(src, GO)


def test_a_long_single_line_is_lexed_in_linear_time():
    import time
    src = ('a := b / c; s := "x"; r := `y`; c := \'z\'; // end\n') * 5000
    t0 = time.perf_counter()
    lx = aigate.lex_text(src, GO)
    assert time.perf_counter() - t0 < 5.0
    assert lx.kinds[src.index('"x"')] == aigate.STR


def test_lines_and_columns_match_with_a_bom_and_crlf():
    lx = aigate.lex_text('\ufeffx := "a"\r\ny := 1 // c\r\n', GO)
    assert lx.line(1).rstrip("\r") == '\ufeffx := "a"'
    assert lx.kind_at(1, 6) == aigate.STR and lx.kind_at(2, 7) == aigate.COM
