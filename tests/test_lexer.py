"""The generic lexer: every string and comment syntax the language specs will need, driven by synthetic configs
(only Go and JS/TS ship a spec in 0.3.0a1; Rust, Kotlin, C#, PHP and Ruby specs are plain data on top of this)."""
import re

import pytest

from conftest import aigate

LexConfig, StrForm = aigate.LexConfig, aigate.StrForm
CODE, STR, COM = aigate.CODE, aigate.STR, aigate.COM
lex_text, LexError = aigate.lex_text, aigate.LexError

JS = aigate.language_for("a.ts").lex
GO = aigate.language_for("a.go").lex
RUST = LexConfig(
    line_comments=("//",), block_comments=((r"/\*", "*/", True),),
    strings=(StrForm(r'b?r(#*)"', '"{g1}', esc=""), StrForm(r'b?"', '"'),
             StrForm(r"b?'(?=(?:\\.[^'\n]*|[^\\'\n])')", "'", nl=False)))
KOTLIN = LexConfig(
    line_comments=("//",), block_comments=((r"/\*", "*/", True),),
    strings=(StrForm(r'"""', '"""', esc="", interp="${"), StrForm(r'"', '"', nl=False, interp="${"),
             StrForm(r"'", "'", nl=False)))
CSHARP = LexConfig(
    line_comments=("//",), block_comments=((r"/\*", "*/", False),),
    strings=(StrForm(r'\$?("{3,})', "{g1}", esc="", interp="{", interp_double=True),
             StrForm(r'\$@"|@\$"', '"', esc="", double=True, interp="{", interp_double=True),
             StrForm(r'@"', '"', esc="", double=True),
             StrForm(r'\$"', '"', nl=False, interp="{", interp_double=True),
             StrForm(r'"', '"', nl=False), StrForm(r"'", "'", nl=False)))
PHP = LexConfig(
    line_comments=("//", "#"), block_comments=((r"/\*", "*/", False),),
    strings=(StrForm(r"<<<[ \t]*(?:\"(\w+)\"|'(\w+)'|(\w+))", r"^[ \t]*{g1}(?!\w)", kind="heredoc"),
             StrForm(r'"', '"'), StrForm(r"'", "'")))
RUBY = LexConfig(
    line_comments=("#",), block_comments=((r"^=begin\b", "=end", False),),
    strings=(StrForm(r"<<[~-](?:\"(\w+)\"|'(\w+)'|(\w+))", r"^[ \t]*{g1}[ \t]*\r?$", kind="heredoc"),
             StrForm(r"%[qQwWiI]?([(\[{<|!/])", "", kind="pct", expr=True),
             StrForm(r'"', '"', interp="#{"), StrForm(r"'", "'")))


def kinds(text, cfg, needle, nth=0):
    """Region of the `nth` occurrence of `needle` (its first character)."""
    lx = lex_text(text, cfg)
    i = -1
    for _ in range(nth + 1):
        i = text.index(needle, i + 1)
    return lx.kinds[i]



# ---------------------------------------------------------------- Go

def test_go_strings_comments_and_the_skip_that_hides_in_them():
    text = 'a := "t.Skip(" // t.Skip(\nb := `\nt.Skip(\n`\nt.Skip()\n/* t.Skip( */ x := \'\\\'\'\n'
    assert [kinds(text, GO, "t.Skip", i) for i in range(5)] == [STR, COM, STR, CODE, COM]
    lx = lex_text(text, GO)
    rx = re.compile(r"\bt\.Skip\(")
    assert [bool(lx.search(rx, n)) for n in range(1, 7)] == [False, False, False, False, True, False]


def test_go_raw_string_ignores_escapes_and_quotes():
    text = 'x := `a\\` + "b"\ny := 1\n'
    assert kinds(text, GO, "+") == CODE and kinds(text, GO, "y") == CODE


def test_go_rune_literals_do_not_start_strings():
    text = "c := '\"'\nd := \"ok\"\nt.Skip()\n"
    assert kinds(text, GO, "t.Skip") == CODE and kinds(text, GO, "ok") == STR


# ---------------------------------------------------------------- JS / TS

def test_js_template_literals_with_nested_interpolation():
    text = "const a = `x ${ foo(`y ${z}`) + \"q\" } end`; eval(a)\n"
    assert kinds(text, JS, "x ") == STR and kinds(text, JS, "foo") == CODE
    assert kinds(text, JS, "y ") == STR and kinds(text, JS, "z}") == CODE and kinds(text, JS, "q") == STR
    assert kinds(text, JS, " end") == STR and kinds(text, JS, "eval") == CODE


def test_js_regex_literal_versus_division():
    text = "const r = /[\"'`]\\/x/g; const d = a / b / c; const s = 'x'\n"
    assert kinds(text, JS, '"') == STR and kinds(text, JS, "x/g") == STR
    assert kinds(text, JS, "b /") == CODE and kinds(text, JS, "c;") == CODE and kinds(text, JS, "x'") == STR
    assert kinds(text, JS, "d =") == CODE


def test_js_regex_after_return_and_open_paren_but_not_jsx_closing_tags():
    text = "function f(s) { return /\"/.test(s) }\nconst e = <b>x</b>; const g = <i>y</i>\nfoo()\n"
    assert kinds(text, JS, '"') == STR and kinds(text, JS, "test") == CODE
    assert kinds(text, JS, "x<") == CODE and kinds(text, JS, "y<") == CODE and kinds(text, JS, "foo") == CODE


def test_js_an_apostrophe_in_jsx_text_only_costs_its_own_line():
    text = "const e = <b>don't</b>; foo()\nbar()\n"
    assert kinds(text, JS, "foo") == STR and kinds(text, JS, "bar") == CODE      # limited to that line


def test_js_comments_and_division_do_not_confuse_each_other():
    text = "a = b // c ' \nd = e /* ' */ + f\ng = h\n"
    assert kinds(text, JS, "c '") == COM and kinds(text, JS, "' */") == COM
    assert kinds(text, JS, "g = h") == CODE and kinds(text, JS, "+ f") == CODE


def test_js_single_quoted_string_ends_at_the_line_end_when_unterminated():
    text = "x = 'don\ny = eval(2)\n"
    assert kinds(text, JS, "eval") == CODE


# ---------------------------------------------------------------- nested block comments and raw strings

def test_rust_nested_block_comments():
    text = "/* a /* b */ still */ code /* x */ more\n"
    assert kinds(text, RUST, "still") == COM and kinds(text, RUST, "code") == CODE and kinds(text, RUST, "more") == CODE


def test_rust_raw_strings_and_char_literals_versus_lifetimes():
    text = ('let a = r#"he said "hi" // not a comment"#; let b = r"c:\\x"; let c = \'x\'; '
            "fn f<'a>(x: &'a str) { todo!() }\n")
    assert kinds(text, RUST, "hi") == STR and kinds(text, RUST, "not a") == STR
    assert kinds(text, RUST, "c:") == STR and kinds(text, RUST, "x'") == STR
    assert kinds(text, RUST, "todo") == CODE and kinds(text, RUST, "fn f") == CODE


def test_kotlin_nested_comments_text_blocks_and_templates():
    text = ('/* a /* b */ c */ val s = """\n  t.Skip( ${ "inner" + x } "quoted" \n"""; val t = "a ${b} c"\nTODO()\n')
    assert kinds(text, KOTLIN, " c */") == COM and kinds(text, KOTLIN, "t.Skip") == STR
    assert kinds(text, KOTLIN, "inner") == STR and kinds(text, KOTLIN, "+ x") == CODE
    assert kinds(text, KOTLIN, "quoted") == STR and kinds(text, KOTLIN, "TODO") == CODE


# ---------------------------------------------------------------- C#

def test_csharp_verbatim_interpolated_and_raw_strings():
    text = ('var a = @"x ""quoted"" // y"; var b = $"v {(c ? "p" : "q")} {{lit}} w"; '
            'var d = """\n  raw "quote" here\n  """; var e = 1; // tail\n')
    assert kinds(text, CSHARP, "quoted") == STR and kinds(text, CSHARP, "// y") == STR
    assert kinds(text, CSHARP, "c ?") == CODE and kinds(text, CSHARP, "p\"") == STR and kinds(text, CSHARP, "lit") == STR
    assert kinds(text, CSHARP, " w") == STR and kinds(text, CSHARP, "quote\" here") == STR
    assert kinds(text, CSHARP, "var e") == CODE and kinds(text, CSHARP, "tail") == COM


# ---------------------------------------------------------------- PHP and Ruby heredocs, Ruby literals

def test_php_heredoc_and_nowdoc():
    text = "$a = <<<EOT\nline eval(1) // x\nEOT;\n$b = <<<'RAW'\n$no ${interp}\n  RAW;\neval($c); # done\n"
    assert kinds(text, PHP, "eval(1)") == STR and kinds(text, PHP, "interp") == STR
    assert kinds(text, PHP, "eval($c)") == CODE and kinds(text, PHP, "done") == COM
    assert kinds(text, PHP, "$a") == CODE and kinds(text, PHP, "EOT;") == STR


def test_ruby_heredocs_two_on_one_line_and_after_them_code_resumes():
    text = "run(<<~A, <<~B)\n  system(x)\nA\n  eval(y)\nB\nputs 1 # c\n"
    assert kinds(text, RUBY, "system") == STR and kinds(text, RUBY, "eval(y)") == STR
    assert kinds(text, RUBY, "puts") == CODE and kinds(text, RUBY, "c\n") == COM and kinds(text, RUBY, "run(") == CODE


def test_ruby_percent_literals_and_modulo():
    text = 'a = %w[x y#z]\nb = %q(one (two) three)\nc = n % (k)\nd = x\n=begin\neval\n=end\ne = 1\n'
    assert kinds(text, RUBY, "x y") == STR and kinds(text, RUBY, "#z") == STR
    assert kinds(text, RUBY, "two") == STR and kinds(text, RUBY, "three") == STR
    assert kinds(text, RUBY, "(k)") == CODE and kinds(text, RUBY, "d = x") == CODE
    assert kinds(text, RUBY, "eval") == COM and kinds(text, RUBY, "e = 1") == CODE


def test_ruby_interpolation_in_double_quotes():
    text = 'm = "a #{ f("b") } c"; g = 1\n'
    assert kinds(text, RUBY, "a #") == STR and kinds(text, RUBY, "f(") == CODE
    assert kinds(text, RUBY, "b\"") == STR and kinds(text, RUBY, " c") == STR and kinds(text, RUBY, "g = 1") == CODE


# ---------------------------------------------------------------- failure modes become fallbacks, not findings

@pytest.mark.parametrize("cfg,text", [
    (GO, "x := `never closed\nfoo()\n"),
    (JS, "x = `open ${ y \n"),
    (JS, "/* never closed\n"),
    (RUBY, "x = <<~EOS\nbody without a terminator\n"),
    (RUBY, "x = %w[open\n"),
    (KOTLIN, "/* a /* b */ missing\n"),
])
def test_runaway_literals_raise_instead_of_hiding_the_rest_of_the_file(cfg, text):
    with pytest.raises(LexError):
        lex_text(text, cfg)


def test_deeply_nested_input_is_refused():
    with pytest.raises(LexError):
        lex_text("/*" * 200 + "x" + "*/" * 200, KOTLIN)
    with pytest.raises(LexError):
        lex_text("`${" * 100 + "x" + "}`" * 100, JS)


def test_a_slow_lex_is_abandoned(monkeypatch):
    monkeypatch.setattr(aigate, "LEX_SECONDS", -1.0)
    with pytest.raises(LexError):
        lex_text("a = 'x' // y\n" * 5000, JS)


def test_crlf_files_keep_line_and_column_alignment():
    text = 'a := "x"\r\n// t.Skip(\r\nt.Skip()\r\n'
    lx = lex_text(text, GO)
    rx = re.compile(r"t\.Skip\(")
    assert not lx.search(rx, 2) and lx.search(rx, 3)
    assert lx.line(3).rstrip("\r") == "t.Skip()"


def test_masked_lines_keep_their_columns():
    lx = lex_text('x := "abc" // note\n', GO)
    assert lx.masked(1) == "x :=       " + " " * 7 and len(lx.masked(1)) == len('x := "abc" // note')
    assert lx.masked(1, (COM,)) == 'x := "abc" ' + " " * 7


def test_code_text_blanks_strings_and_comments_but_keeps_newlines():
    lx = lex_text('a { "}" } // }\n}\n', GO)
    assert lx.code_text().count("}") == 2 and lx.code_text().count("\n") == 2


def test_lexing_is_linear_enough():
    import time
    text = ("func f() { x := \"a // b\" // c\n y := `d` }\n" * 20000)
    t = time.perf_counter()
    lex_text(text, GO)
    assert time.perf_counter() - t < 5.0
