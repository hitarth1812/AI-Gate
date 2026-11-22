"""JavaScript, TypeScript and Go: on text INSIDE a string (or a template literal, or a Go raw string) code.todo, security.weak_crypto,
suppress.lint, suppress.type and secrets.generic must not fire, as in Python since 0.6.1. This is a parsing fix, not a softening: a
person and an AI agent get the same answer (every test compares them). A provider-shaped key in a string still blocks. After an
unclosed quote the rest of the file is read as code: a lexer failure never hides a finding. Every test runs the CLI in a temporary
repository (a staged change)."""
import pytest

from conftest import find

AGENT = {"CLAUDECODE": "1"}
TODO, FIXME, ESL, TSI = "TO" + "DO", "FIX" + "ME", "eslint-" + "disable-line", "@ts-" + "ignore"   # spelled in pieces: this file is not itself a finding
MD5, SHA1 = "md" + "5", "sha" + "1"
NOLINT = "no" + "lint"
RULES = ("code.todo", "code.placeholder", "security.weak_crypto", "suppress.lint", "suppress.type", "suppress.security",
         "secrets.generic", "secrets.hardcoded", "code.debug_leftover", "security.eval")
LITERAL = "Zx9kQ2mT7vRw"                                 # not a provider key; a value that looks like a password


def hits(repo, path, text):
    """{rule: [lines]} for the stated rules, for a person and for an agent (they must be equal). `text` may be bytes."""
    p = repo.path / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    repo.add()
    out = []
    for env in (None, AGENT):
        res = repo.check("--offline", env=env)
        out.append({r: sorted(f["line"] for f in find(res, r) if not f["allowed"]) for r in RULES if find(res, r)})
    assert out[0] == out[1], out
    return out[0]


def lines(*rows):
    return "\n".join(rows) + "\n"


# ------------------------------------------------------------------------------------------ the probe of the specification

PROBE_JS = lines('const a = 1;', 'const b = 2;', 'const c = 3;', 'const d = 4;', 'const e = 5;',
                 f'const msg = "remember: // {TODO} later and {SHA1}(password) is weak";',
                 f"const note = 'use {MD5}(token) never';", 'const x = 7;')
PROBE_GO = lines('package k', '', 'import "fmt"', '', f'var doc = "// {TODO}: {SHA1}(password) is weak; //{NOLINT}:gosec"',
                 '', 'func f() { fmt.Println(doc) }')


def test_the_probe_lines_of_the_specification_give_no_finding_in_javascript(repo):
    assert hits(repo, "src/k.js", PROBE_JS) == {}


def test_the_probe_line_of_the_specification_gives_no_finding_in_go(repo):
    assert hits(repo, "src/k.go", PROBE_GO) == {}


def test_the_control_line_still_gives_the_finding_in_javascript(repo):
    assert hits(repo, "src/k.js", PROBE_JS + f"const h = {SHA1}(password);\n") == {"security.weak_crypto": [9]}


def test_the_control_line_still_gives_the_finding_in_go(repo):
    got = hits(repo, "src/k.go", PROBE_GO + f"\nfunc g(password []byte) [20]byte {{\n\treturn {SHA1}.Sum(password)\n}}\n")
    assert got == {"security.weak_crypto": [10]}


# ----------------------------------------------------------------------------------------------- text in a string: no finding

JS_QUIET = {
    "todo-dq": f'const t = "// {TODO} later";\n',
    "todo-template": f"const t = `\n// {TODO} in a template\n// {FIXME} too\n`;\n",
    "weak-dq": f'const t = "use {MD5}(password) never";\n',
    "weak-sq": f"const t = 'call {SHA1}(token) and be sorry';\n",
    "weak-template": f"const t = `use {SHA1}(password) never`;\n",
    "weak-template-multiline": f"const t = `\nconst h = {MD5}(password);\n`;\n",
    "weak-template-with-expr": f"const t = `{SHA1} of ${{name}}: the password`;\n",
    "weak-random-text": 'const t = "never Math.random() for a token";\n',
    "weak-regex-literal": f"const re = /{SHA1}\\(password\\)/;\n",
    "lint-dq": f'const t = "x // {ESL} no-console";\n',
    "lint-template": f"const t = `y // {ESL}`;\n",
    "lint-block-text": 'const t = "/* eslint-disable */ is a directive";\n',
    "ts-ignore-dq": f'const t = "// {TSI} hides errors";\n',
    "generic-sq": f"const t = 'password = \"{LITERAL}\"';\n",
    "generic-template": f"const t = `api_secret = '{LITERAL}'`;\n",
    "generic-template-multiline": f"const t = `\ndb_password = \"{LITERAL}\"\n`;\n",
    "generic-dq": f'const t = "secret = \'{LITERAL}\'";\n',
    "after-escaped-quote": f'const t = "a\\"b"; const u = "{SHA1}(password) is weak";\n',
    "nested-template": f"const t = `${{ `${{\"}}\"}}` }} {SHA1}(password)`;\n",
}


@pytest.mark.parametrize("name", sorted(JS_QUIET))
def test_javascript_text_in_a_string_is_not_a_finding(repo, name):
    path = "src/a.ts" if name == "ts-ignore-dq" else "src/a.js"
    assert hits(repo, path, JS_QUIET[name]) == {}


GO_QUIET = {
    "todo-dq": f'package k\n\nvar t = "// {TODO} later"\n',
    "todo-raw": f"package k\n\nvar t = `\n// {TODO} in a raw string\n`\n",
    "weak-dq": f'package k\n\nvar t = "use {SHA1}(password) never"\n',
    "weak-raw": f"package k\n\nvar t = `{MD5}(token) is weak`\n",
    "weak-raw-multiline": f"package k\n\nvar t = `\nh := {SHA1}.Sum(password)\n`\n",
    "weak-struct-tag": f'package k\n\ntype T struct {{ Hash string `json:"{SHA1}_password"` }}\n',
    "nolint-dq": f'package k\n\nvar t = "//{NOLINT}:gosec"\n',
    "nolint-raw": f"package k\n\nvar t = `x //{NOLINT} y`\n",
    "generic-raw": f'package k\n\nvar t = `password = "{LITERAL}"`\n',
    "generic-raw-multiline": f'package k\n\nvar t = `\napi_secret = "{LITERAL}"\n`\n',
    "after-rune": f"package k\n\nvar c, t = '\"', \"{SHA1}(password) is weak\"\n",
    "raw-with-quote-and-slashes": f'package k\n\nvar t = `say "hi" // {TODO} {SHA1}(password)`\n',
}


@pytest.mark.parametrize("name", sorted(GO_QUIET))
def test_go_text_in_a_string_is_not_a_finding(repo, name):
    assert hits(repo, "src/k.go", GO_QUIET[name]) == {}


# --------------------------------------------------------------------------------- the same words in real code still fire

JS_LOUD = {
    "sha1-code": (f"const h = {SHA1}(password);\n", {"security.weak_crypto": [1]}),
    "md5-with-the-word-in-a-string": (f'const h = {MD5}("password");\n', {"security.weak_crypto": [1]}),
    "string-concat": (f'const h = "x" + {SHA1}(password);\n', {"security.weak_crypto": [1]}),
    "template-expression": (f"const h = `${{{SHA1}(password)}}`;\n", {"security.weak_crypto": [1]}),
    "template-then-code": (f"const a = `x`; const h = {SHA1}(password);\n", {"security.weak_crypto": [1]}),
    "createhash-md5-dq": (f"const h = crypto.createHash(\"{MD5}\").update(password).digest('hex');\n", {"security.weak_crypto": [1]}),
    "createhash-sha1-sq": (f"const h = createHash('{SHA1}').update(secret).digest('hex');\n", {"security.weak_crypto": [1]}),
    "createhash-sha1-template": (f"const h = createHash(`{SHA1}`).update(token);\n", {"security.weak_crypto": [1]}),
    "createhash-upper": (f"const h = createHash('{MD5.upper()}').update(password);\n", {"security.weak_crypto": [1]}),
    "pbkdf2-digest": (f'crypto.pbkdf2Sync(password, salt, 1000, 64, "{SHA1}");\n', {"security.weak_crypto": [1]}),
    "digest-option": (f"const o = {{ password, digest: '{SHA1}' }};\n", {"security.weak_crypto": [1]}),
    "math-random-token": ("const token = Math.random();\n", {"security.weak_crypto": [1]}),
    "math-random-secret-key-in-a-string": ('const o = {}; o["token"] = Math.random();\n', {"security.weak_crypto": [1]}),
    "todo-comment": (f"const a = 1; // {TODO} fix\n", {"code.todo": [1]}),
    "todo-after-slashes-in-a-string": (f'const u = "http://x"; // {TODO} fix\n', {"code.todo": [1]}),
    "todo-block-comment": (f"/* {FIXME}: later */\nconst a = 1;\n", {"code.todo": [1]}),
    "lint-line": (f"const a = 1; // {ESL} no-console\n", {"suppress.lint": [1]}),
    "lint-after-a-string": (f"const a = 'x'; // {ESL}\n", {"suppress.lint": [1]}),
    "ts-ignore": (f"// {TSI}\nconst a: number = 'x';\n", {"suppress.type": [1]}),
    "password-assignment": (f'const password = "{LITERAL}";\n', {"secrets.generic": [1]}),
    "password-in-an-object": (f'const c = {{ password: "{LITERAL}" }};\n', {"secrets.generic": [1]}),
    "password-quoted-key": (f'const c = {{ "password": "{LITERAL}" }};\n', {"secrets.generic": [1]}),
    "password-after-a-string": (f'const l = "x"; const api_secret = \'{LITERAL}\';\n', {"secrets.generic": [1]}),
    "json-body-in-a-template": (f'const body = `{{"password": "{LITERAL}"}}`;\n', {"secrets.generic": [1]}),
    "json-body-in-a-string": (f"const body = '{{\"token\":\"{LITERAL}\"}}';\n", {"secrets.generic": [1]}),
    "password-in-a-template-expression": (f"const t = `${{ cfg({{ password: '{LITERAL}' }}) }}`;\n", {"secrets.generic": [1]}),
    "debugger": ("function f() {\n  debugger;\n}\n", {"code.debug_leftover": [2]}),
    "eval-after-a-regex-with-a-quote": ('const re = /"/; eval(a);\n', {"security.eval": [1]}),
    "regex-quote-then-sha1": (f'const re = /"/; const h = {SHA1}(password);\n', {"security.weak_crypto": [1]}),
    "regex-class-then-sha1": (f'const re = /[/"]/; const h = {SHA1}(password);\n', {"security.weak_crypto": [1]}),
    "division-after-paren": (f'const r = f(a) / 2 + g(b) / 3; const h = {SHA1}(password) + "x";\n', {"security.weak_crypto": [1]}),
    "division-after-bracket": (f'const r = a[0] / 2 + b[1] / 3; const h = {MD5}(token);\n', {"security.weak_crypto": [1]}),
    "division-after-increment": (f'let r = n++ / 2 + m-- / 3; const h = {SHA1}(secret);\n', {"security.weak_crypto": [1]}),
    "return-regex-then-sha1": (f"function f(s) {{ return /re/.test(s) && {SHA1}(password); }}\n", {"security.weak_crypto": [1]}),
    "slash-after-paren-between-regexes": (f'if (ok) /"/.test(a) && {MD5}(password) && /"/.test(b);\n', {"security.weak_crypto": [1]}),
    "slash-after-paren-single-quotes": (f"function f(s) {{\n  if (s) /'/.test(s) && {MD5}(password) && /'/.test(s);\n}}\n",
                                        {"security.weak_crypto": [2]}),
    "slash-after-brace-between-regexes": (f"x = y }}\n/'/.test(s) && {MD5}(password) || /'/.exec(s);\n", {"security.weak_crypto": [2]}),
    "slash-after-bracket-between-regexes": (f'a[0] /"/.test(s) && {SHA1}(token) && /"/.test(t);\n', {"security.weak_crypto": [1]}),
    "slash-after-paren-in-a-later-line": (f'const a = 1;\nif (ok) /"/.test(a) && {MD5}(password) && /"/.test(b);\nconst c = 2;\n',
                                          {"security.weak_crypto": [2]}),
    "nested-template-then-sha1": (f"const t = `${{ `${{\"}}\"}}` }}`; const h = {SHA1}(password);\n", {"security.weak_crypto": [1]}),
    "comment-in-a-template-expression": (f"const t = `${{ /* }} */ 1 }}`; const h = {MD5}(password);\n", {"security.weak_crypto": [1]}),
    "weak-call-in-an-expression-of-a-template": (f"const t = `a ${{{SHA1}(password)}} b ${{x}}`;\n", {"security.weak_crypto": [1]}),
}


@pytest.mark.parametrize("name", sorted(JS_LOUD))
def test_javascript_the_same_words_as_code_still_fire(repo, name):
    text, want = JS_LOUD[name]
    assert hits(repo, "src/a.ts" if name == "ts-ignore" else "src/a.js", text) == want


GO_LOUD = {
    "sha1-code": (f"package k\n\nfunc f(password []byte) [20]byte {{\n\treturn {SHA1}.Sum(password)\n}}\n", {"security.weak_crypto": [4]}),
    "md5-after-a-raw-string": (f'package k\n\nfunc f(password []byte) {{\n\ts := `a "b"`; h := {MD5}.Sum(password)\n\t_, _ = s, h\n}}\n',
                               {"security.weak_crypto": [4]}),
    "sha1-after-a-rune-quote": (f"package k\n\nfunc f(password []byte) {{\n\tc := '\"'; h := {SHA1}.Sum(password)\n\t_, _ = c, h\n}}\n",
                                {"security.weak_crypto": [4]}),
    "sha1-after-an-escaped-rune": (f"package k\n\nfunc f(password []byte) {{\n\tc := '\\''; h := {SHA1}.Sum(password)\n\t_, _ = c, h\n}}\n",
                                   {"security.weak_crypto": [4]}),
    "sha1-in-a-call-with-a-string": (f'package k\n\nfunc f(password []byte) {{\n\tlog("x"); h := {SHA1}.Sum(password); _ = h\n}}\n',
                                     {"security.weak_crypto": [4]}),
    "todo-comment": (f"package k\n\nvar a = 1 // {TODO} fix\n", {"code.todo": [3]}),
    "todo-after-a-raw-string": (f"package k\n\nvar a = `x // y` // {TODO} fix\n", {"code.todo": [3]}),
    "nolint-comment": (f"package k\n\nvar a = 1 // {NOLINT}\n", {"suppress.lint": [3]}),
    "nolint-gosec-comment": (f"package k\n\nvar a = 1 //{NOLINT}:gosec\n", {"suppress.security": [3]}),
    "password-assignment": (f'package k\n\nvar password = "{LITERAL}"\n', {"secrets.generic": [3]}),
    "json-body-in-a-raw-string": (f'package k\n\nvar body = `{{"token":"{LITERAL}"}}`\n', {"secrets.generic": [3]}),
    "short-declaration": (f'package k\n\nfunc f() {{\n\tpassword := "{LITERAL}"\n\t_ = password\n}}\n', {"secrets.generic": [4]}),
}


@pytest.mark.parametrize("name", sorted(GO_LOUD))
def test_go_the_same_words_as_code_still_fire(repo, name):
    text, want = GO_LOUD[name]
    assert hits(repo, "src/k.go", text) == want


# ---------------------------------------------------------------- an unclosed quote: later lines are read as code, nothing is hidden

UNCLOSED = {
    "js-later-code": ("a.js", f"const s = 'abc\nconst h = {SHA1}(password);\ndebugger;\n", {"security.weak_crypto": [2], "code.debug_leftover": [3]}),
    "js-same-line-tail": ("a.js", f"const s = 'abc {MD5}(password)\nconst x = 1;\n", {"security.weak_crypto": [1]}),
    "js-todo-after-the-quote": ("a.js", f"const s = 'abc // {TODO} fix\nconst x = 1;\n", {"code.todo": [1]}),
    "js-lint-after-the-quote": ("a.js", f"const s = \"abc // {ESL}\nconst x = 1;\n", {"suppress.lint": [1]}),
    "js-everything-after-is-code": ("a.js", f'const s = "abc\nconst t = "// {TODO} words in a string";\n', {"code.todo": [2]}),
    "js-jsx-apostrophe": ("a.jsx", f"const a = <p>Don't</p>;\nconst h = {SHA1}(password);\n", {"security.weak_crypto": [2]}),
    "js-jsx-apostrophe-then-string-words": ("a.jsx", f'const a = <p>Don\'t</p>;\nconst t = "{SHA1}(password)";\n', {"security.weak_crypto": [2]}),
    "js-before-the-quote-still-text": ("a.js", f'const t = "{SHA1}(password)";\nconst s = \'abc\nconst x = 1;\n', {}),
    "js-unclosed-template": ("a.js", f"const s = `abc\nconst h = {SHA1}(password);\n", {"security.weak_crypto": [2]}),
    "js-unclosed-block-comment": ("a.js", f"/* never closed\nconst h = {SHA1}(password);\n", {"security.weak_crypto": [2]}),
    "js-unbalanced-interpolation": ("a.js", f"const s = `x ${{ y\nconst h = {SHA1}(password);\n", {"security.weak_crypto": [2]}),
    "js-quote-at-end-of-file": ("a.js", f"const h = {SHA1}(password);\nconst s = 'abc", {"security.weak_crypto": [1]}),
    "go-later-code": ("k.go", f'package k\n\nvar s = "abc\nvar h = {SHA1}.Sum(password)\nvar d = 1 // {TODO}\n',
     {"security.weak_crypto": [4], "code.todo": [5]}),
    "go-same-line-tail": ("k.go", f'package k\n\nvar s = "abc {SHA1}(password)\nvar x = 1\n', {"security.weak_crypto": [3]}),
    "go-unclosed-rune": ("k.go", f"package k\n\nvar c = 'x\nvar h = {MD5}.Sum(password)\n", {"security.weak_crypto": [4]}),
    "go-unclosed-raw-string": ("k.go", f"package k\n\nvar s = `abc\nvar h = {SHA1}.Sum(password)\n", {"security.weak_crypto": [4]}),
}


@pytest.mark.parametrize("name", sorted(UNCLOSED))
def test_after_an_unclosed_quote_nothing_is_hidden(repo, name):
    path, text, want = UNCLOSED[name]
    assert hits(repo, "src/" + path, text) == want


def test_a_file_the_lexer_has_no_spec_for_keeps_the_line_based_result(repo):
    got = hits(repo, "src/a.vue", f'<script>\nconst t = "{MD5}(password)";\n</script>\n')
    assert got == {"security.weak_crypto": [2]}


# ------------------------------------------------------------------------------- a provider key in a string still blocks

KEY = "AKIA" + "qwertyuiopasdfgh".upper()                      # split and rebuilt so this file is not itself a finding


@pytest.mark.parametrize("name,path,text", [
    ("js-dq", "src/a.js", f'const e = "AWS_ACCESS_KEY_ID={KEY}";\n'),
    ("js-sq", "src/a.js", f"const e = '{KEY}';\n"),
    ("js-template", "src/a.js", f"const e = `aws key: {KEY}`;\n"),
    ("js-template-multiline", "src/a.js", f"const e = `\nkey = {KEY}\n`;\n"),
    ("ts-dq", "src/a.ts", f'export const KEY: string = "{KEY}";\n'),
    ("go-raw", "src/k.go", f"package k\n\nvar e = `aws_key={KEY}`\n"),
    ("go-dq", "src/k.go", f'package k\n\nvar e = "{KEY}"\n'),
    ("go-raw-multiline", "src/k.go", f"package k\n\nvar e = `\n{KEY}\n`\n"),
])
def test_a_provider_key_inside_a_string_blocks_for_a_person_and_an_agent(repo, name, path, text):
    (repo.path / path).parent.mkdir(parents=True, exist_ok=True)
    (repo.path / path).write_bytes(text.encode("utf-8"))
    repo.add()
    for env in (None, AGENT):
        res = repo.check("--offline", env=env)
        keys = [f for f in find(res, "secrets.hardcoded") if not f["allowed"]]
        assert keys and {f["action"] for f in keys} == {"block"}, (env, res["findings"])
        assert res["code"] == 1


@pytest.mark.parametrize("path,text,line", [
    ("src/a.js", 'const u = "SCHEME://app:Zq9xLm2Rt7@db.internal/app";\n', 1),
    ("src/k.go", "package k\n\nvar u = `SCHEME://app:Zq9xLm2Rt7@db.internal/app`\n", 3),
], ids=["js", "go"])
def test_a_connection_string_with_a_password_inside_a_string_still_fires(repo, path, text, line):
    scheme = "post" + "gres"                                   # no URL-shaped literal in this file; the staged file has one
    assert hits(repo, path, text.replace("SCHEME", scheme)) == {"secrets.generic": [line]}


# ----------------------------------------------------------------------------------------------- CRLF and a byte order mark

def crlf(text, bom=False):
    return ("\ufeff" if bom else "") + text.replace("\n", "\r\n")


@pytest.mark.parametrize("bom", [False, True], ids=["crlf", "bom+crlf"])
def test_the_probe_behaves_the_same_with_crlf_and_a_byte_order_mark_javascript(repo, bom):
    assert hits(repo, "src/k.js", crlf(PROBE_JS, bom)) == {}
    assert hits(repo, "src/k2.js", crlf(PROBE_JS + f"const h = {SHA1}(password);\n", bom)) == {"security.weak_crypto": [9]}


@pytest.mark.parametrize("bom", [False, True], ids=["crlf", "bom+crlf"])
def test_the_probe_behaves_the_same_with_crlf_and_a_byte_order_mark_go(repo, bom):
    assert hits(repo, "src/k.go", crlf(PROBE_GO, bom)) == {}
    assert hits(repo, "src/k2.go", crlf(PROBE_GO + f"\nfunc g(p []byte) [20]byte {{\n\treturn {SHA1}.Sum(p)\n}}\n", bom)) == {}   # no secret word
    assert hits(repo, "src/k3.go", crlf(PROBE_GO + f"\nfunc g(password []byte) [20]byte {{\n\treturn {SHA1}.Sum(password)\n}}\n", bom)) == {
        "security.weak_crypto": [10]}


def test_a_bom_on_the_first_line_does_not_hide_that_line(repo):
    got = hits(repo, "src/k.js", ("\ufeff" + f"const h = {SHA1}(password); // {TODO} x\n").encode("utf-8"))
    assert got == {"security.weak_crypto": [1], "code.todo": [1]}


def test_crlf_secrets_and_suppressions(repo):
    text = crlf(f'const password = "{LITERAL}"; // {ESL}\nconst t = "password = \'{LITERAL}\'";\n')
    assert hits(repo, "src/k.js", text) == {"secrets.generic": [1], "suppress.lint": [1]}


# -------------------------------------------------------------------------------------------------- a very long (minified) line

def test_a_long_line_still_lets_the_next_line_be_judged_and_is_never_a_crash(repo):
    long_line = "var a=1;" * 260 + f'var t="{SHA1}(password) is weak";'           # over the 2,000 characters the line rules read
    got = hits(repo, "src/min.js", long_line + f"\nconst h = {SHA1}(password);\n")
    assert got == {"security.weak_crypto": [2]}


def test_on_a_long_line_a_credential_in_a_string_is_judged_as_code(repo):
    pad = "var a=1;" * 260
    short = f"const t = \"password = '{LITERAL}'\";\n"
    assert hits(repo, "src/short.js", short) == {}
    assert hits(repo, "src/long.js", pad + short) == {"secrets.generic": [1]}      # over the limit the line counts as code: it fires


def test_a_long_line_with_a_provider_key_blocks(repo):
    pad = "var a=1;" * 700
    assert hits(repo, "src/big.js", pad + f'var e="{KEY}";\n') == {"secrets.hardcoded": [1]}


# ------------------------------------------------------------------------ .jsx and .tsx keep the plain (0.6.1) line reading
# JSX text can confuse a string reader (`<p>Don't</p>` opens a quote that pairs with a `'` later on the line), so those two extensions are
# not read string-aware: they give exactly the answer 0.6.1 gave, words in strings included. Each expected value below is what the 0.6.1
# tool printed for the same file.

JSX_OLD = {
    "tsx-apostrophe-regex-weak": ("src/a.tsx", f"const el = <p>Don't</p>; const h = {SHA1}(password); const re = /'/;\n", {"security.weak_crypto": [1]}),
    "jsx-apostrophe-regex-weak": ("src/a.jsx", f"const el = <p>Don't panic</p>; const h = {MD5}(password); const re = /'/g;\n",
                                  {"security.weak_crypto": [1]}),
    "jsx-apostrophe-regex-generic": ("src/a.jsx", f"const el = <p>Don't panic</p>; const password = \"{LITERAL}\"; const re = /'/g;\n",
                                     {"secrets.generic": [1]}),
    "jsx-words-in-a-string-still-warn": ("src/a.jsx", f'const msg = "use {SHA1}(password) never";\n', {"security.weak_crypto": [1]}),
    "tsx-generic-in-a-string-still-fires": ("src/a.tsx", f"const t = 'password = \"{LITERAL}\"';\n", {"secrets.generic": [1]}),
    "jsx-todo-in-a-string-stays-quiet": ("src/a.jsx", f'const t = "// {TODO} later";\n', {}),
    "jsx-todo-string-after-apostrophe-quiet": ("src/a.jsx", f'const a = <p>Don\'t</p>;\nconst t = "// {TODO} words";\n', {}),
    "tsx-weak-words-after-apostrophe": ("src/a.tsx", f"const a = <p>Don't</p>;\nconst t = \"{SHA1}(password) words\";\n", {"security.weak_crypto": [2]}),
    "tsx-lint-string-after-apostrophe-quiet": ("src/a.tsx", f"const a = <p>Don't</p>;\nconst t = \"x // {ESL}\";\n", {}),
    "jsx-template-words-warn": ("src/a.jsx", f'const t = "x // {ESL}"; const u = `use {MD5}(token) now`;\n', {"security.weak_crypto": [1]}),
    "jsx-real-todo-comment": ("src/a.jsx", f"const a = <p>x</p>; // {TODO} later\n", {"code.todo": [1]}),
    "tsx-real-lint-comment": ("src/a.tsx", f"const a = <p>x</p>; // {ESL}\n", {"suppress.lint": [1]}),
}


@pytest.mark.parametrize("name", sorted(JSX_OLD))
def test_jsx_and_tsx_files_give_the_answer_of_the_plain_line_reading(repo, name):
    path, text, want = JSX_OLD[name]
    assert hits(repo, path, text) == want


def test_the_same_line_without_jsx_in_js_and_ts_stays_string_aware(repo):
    quiet = f'const msg = "use {SHA1}(password) never"; const t = \'password = "{LITERAL}"\';\n'
    assert hits(repo, "src/q.js", quiet) == {}
    assert hits(repo, "src/q.ts", quiet) == {}


@pytest.mark.parametrize("path", ["src/a.js", "src/a.mjs", "src/a.cjs", "src/a.ts"])
def test_real_code_is_still_reported_in_the_string_aware_extensions(repo, path):
    assert hits(repo, path, f"const h = {SHA1}(password); const re = /x/;\n") == {"security.weak_crypto": [1]}


def test_vue_files_are_unchanged(repo):
    got = hits(repo, "src/a.vue", f'<script>\nconst t = "{MD5}(password)";\n</script>\n')
    assert got == {"security.weak_crypto": [2]}


# ------------------------------------------------ JSX written in a .js/.ts/.mjs/.cjs file, and other text that is not code but has quotes
# A lone apostrophe in JSX text pairs with a quote later on the line and hides real code in a closed fake string. Files that look like they
# hold JSX (a closing or self-closing tag, a fragment, `<!--`) get the plain line reading, like .jsx and .tsx; a few other shapes make
# every later line count as code. In every one of the lines below the real line is reported, as 0.6.1 reported it.

WEAK_LINE, CRED_LINE = f"const h = {SHA1}(password);", f'const password = "{LITERAL}";'
REALS = [(WEAK_LINE, "security.weak_crypto"), (CRED_LINE, "secrets.generic")]      # (security.eval inside a JSX file is as hidden as in 0.6.1)
SANDWICH = [      # (text before the real line, text after it); the real line is put between them
    ("const el = <p>Don't</p>;", "const re = /'/;"), ("const el = <p>It's {name}'s</p>;", ""), ("", "const el = <p>Don't</p>;"),
    ("const el = <a title='x'>don't</a>;", "const r = /'/g;"), ("const el = <p>{\"it's\"}</p>;", "const r = /'/;"), ("const el = <>It's</>;", "const q = '';"),
    ("return (<div>it's</div>);", ""), ("{/* don't */}", ""), ("const el = <p dangerouslySetInnerHTML={{__html: 'x'}}>don't</p>;", "const re = /'/;"),
    ("const el = cond ? <a>it's</a> : <b>no</b>;", "const d = /'/;"), ("const el = <input value='a' disabled />;", ""), ("const el = <p>5 > 3 'ok</p>;", "x = /'/;"),
    ("<!-- don't", ""), ("x = a! / 2;", "y = b! / 3;"), ("r = p / 100;", "q = r / 4;"), ("u = or / 2;", "v = and / 3;"), ("Don't", "it's"),
    ("s = 'quote", "/'/;"), ('const el = <p>say "hi</p>;', 'const q = "x";'), ("const el = <p>&apos;x</p>;", "const w = /'/;"),
    ("const el = <img alt=\"it's\" />;", "const w = /'/;"), ("if (ok) /\"/.test(a) &&", "&& /\"/.test(b);"), ("if (s) /'/.test(s) &&", "|| /'/.test(t);"),
    ("x = y }\n/'/.test(s) &&", "|| /'/.exec(s);"), ("a[0] /\"/.test(s) &&", "&& /\"/.test(t);"), ("n++ /'/.test(s) &&", "&& /'/.test(t);"),
    ("x = a >/2;", "y = b >/3;"), ("print / 2;", "when / 3;"), ("const T = () => <p>Don't</p>;", "const U = /'/;"),
    ("const el = <ul>{xs.map(x => <li key={x}>{x}'s</li>)}</ul>;", "const re = /'/;"), ("const f = <T,>(x: T) => x; const el = <b>it's</b>;", "const re = /'/;"),
]
JSX_TABLE = []
for _i, (_a, _b) in enumerate(SANDWICH):
    for _j in range(3):
        _real, _rule = REALS[(_i + _j) % 2]
        _ext = (".js", ".ts", ".mjs", ".cjs")[(_i + _j) % 4]
        _text = " ".join(x for x in (_a, _real, _b) if x) if "\n" not in _a else _a + " " + _real + (" " + _b if _b else "")
        JSX_TABLE.append((f"src/r{_i:02d}_{_j}{_ext}", _text + "\n", _rule, _a.count("\n") + 1))


def test_the_hand_written_confusion_lines_keep_their_findings(repo):
    for path, text, _rule, _line in JSX_TABLE:
        p = repo.path / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    repo.add()
    seen = []
    for env in (None, AGENT):
        res = repo.check("--offline", env=env)
        found = {(f["path"], f["rule"], f["line"]) for f in res["findings"] if not f["allowed"]}
        seen.append(found)
        missing = [(p, r, ln) for p, _t, r, ln in JSX_TABLE if (p, r, ln) not in found]
        assert not missing, (env, missing[:5])
    assert {x for x in seen[0] if x[1] in {r for _, r in REALS}} == {x for x in seen[1] if x[1] in {r for _, r in REALS}}


@pytest.mark.parametrize("path", ["src/a.js", "src/a.ts", "src/a.mjs", "src/a.cjs"])
def test_jsx_in_a_script_file_keeps_the_weak_call_between_the_apostrophe_and_the_regex(repo, path):
    text = f"const el = <p>Don't</p>; const h = {SHA1}(password); const re = /'/;\n"
    assert hits(repo, path, text) == {"security.weak_crypto": [1]}


def test_a_file_with_jsx_gets_the_plain_reading_even_for_words_in_strings(repo):
    text = f'const el = <p>x</p>;\nconst msg = "use {SHA1}(password) never";\n'
    assert hits(repo, "src/a.js", text) == {"security.weak_crypto": [2]}          # as in 0.6.1: a documented limit


def test_the_same_words_in_a_file_without_jsx_stay_quiet(repo):
    assert hits(repo, "src/b.js", f'const msg = "use {SHA1}(password) never";\nconst n = a < b && c > d;\n') == {}
