#!/usr/bin/env python3
"""Corpus v7 (round 5, text inside strings in JavaScript, TypeScript and Go): 37 labelled TRAINING cases written to eval/corpus/ (never to
eval/holdout/).

    py eval/tools/seeds/seed_v7.py                       # write every case (it rewrites the same bytes)
    py eval/tools/seeds/seed_v7.py --only ID[,ID...]     # write only these ids
    py eval/tools/seeds/seed_v7.py --out DIR             # write somewhere else (to check that the bytes are the same)

They were written from what docs/CHECKS.md and docs/RULES.md say about `code.todo`, `security.weak_crypto`, `suppress.lint`,
`suppress.type` and `secrets.generic` (they read code and comments, not the text of a string), NOT from what the tool prints today.
A provider-shaped key in a string must still block.

  A. JavaScript and TypeScript, seeded (12): six `must_not_fire` (the words sit in a quoted string, a template literal, a regular
     expression or after escaped quotes) and six `must_fire` (the same words as code, an algorithm named in `createHash`, comments
     that are directives, a credential assigned in code, a provider key inside a template literal, and real code after strings)
  B. Go, seeded (12): the same split, with raw strings, runes and struct tags instead of templates
  C. adversarial (13), all `must_fire`: code that a lexer could misread as the inside of a string (a regular expression holding a
     quote, divisions, nested templates, a comment inside a template expression, an unterminated quote, a rune holding a quote, a division sign before a regex holding a quote), so
     that a lexer mistake can never hide the weak hash that follows

A provider key is written with the placeholder of eval/run.py (@@AWS@@), so no tracked file holds one; the credential-looking values
below are not provider-shaped.

Every id is checked against the holdout hash: these are training cases, so `holdout_of(id)` must be False (force_holdout is not used).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_v1 as v1                      # noqa: E402

F = v1.F
ORIGIN = "round 5 authored from the documented rules (no tool output)"
BLOCK = ("block", "block")
BASE = {"javascript": v1.TS_BASE, "typescript": v1.TS_BASE, "go": v1.GO_BASE}
BARE = {"README.md": "# base\n"}
SECRET, OTHER = "Zx9kQ2mT7vRw", "Qw3rTy9uIoP2"           # look like passwords, are not provider keys
WEAK, TODO, LINT, SUPP = "security.weak_crypto", "code.todo", "suppress.lint", "suppress.security"


def case(id, family, language, path, text, fire=(), notfire=(), base=None):
    assert not v1.holdout_of(id), f"{id} would be routed to eval/holdout (hash); pick another id"
    assert id.startswith("r5-")
    v1.emit(id, family, language, ORIGIN, BASE[language] if base is None else base, {path: text}, fire, notfire)


def quiet(id, language, path, text, *rules):
    """seeded, must NOT fire: the words are text."""
    case(id, "seeded", language, path, text, notfire=[(r, path) for r in rules])


def loud(id, language, path, text, *fires):
    """seeded, must fire: fires are (rule, needle) or (rule, needle, action, agent_action)."""
    case(id, "seeded", language, path, text, fire=[F(r, path, n, *a) for r, n, *a in fires])


def sneaky(id, language, path, text, *fires):
    """adversarial, must fire: the weak operation follows something a lexer could misread."""
    case(id, "adversarial", language, path, text, fire=[F(r, path, n) for r, n in fires], base=BARE)


def javascript():
    # ---- the words are text: no finding
    quiet("r5-js-words-in-quotes", "javascript", "src/notes.js",
          'const msg = "remember: // TODO later and sha1(password) is weak";\nconst note = \'use md5(token) never\';\n'
          'module.exports = { msg, note };\n', WEAK, TODO)
    quiet("r5-js-words-in-template", "javascript", "src/help.js",
          "const help = `\nHow to hash a password (do not do this):\n  const h = sha1(password);   // TODO replace with scrypt\n"
          f'  // eslint-disable-line no-console\n  password = "{SECRET}"\n`;\nmodule.exports = {{ help }};\n',
          WEAK, TODO, LINT, "secrets.generic")
    quiet("r5-js-lint-marker-in-string", "typescript", "src/tips.ts",
          'export const tips = [\n  "silence one line with // eslint-disable-line no-console",\n  \'// @ts-ignore hides the next error\',\n'
          "  `use /* eslint-disable */ for a whole file`,\n];\n", LINT, "suppress.type")
    quiet("r5-js-credential-assignment-in-string", "javascript", "src/sample.js",
          f"const envSample = 'password = \"{SECRET}\"';\nconst cfgSample = `api_secret = '{SECRET}'`;\n"
          f"const jsonSample = \"{{ token = '{OTHER}' }}\";\nmodule.exports = {{ envSample, cfgSample, jsonSample }};\n", "secrets.generic")
    quiet("r5-js-words-in-comment-and-regex", "javascript", "src/changelog.js",
          "// we replaced sha1(password) with scrypt\nconst legacy = /md5\\(password\\)/;\nmodule.exports = { legacy };\n", WEAK)
    quiet("r5-js-words-after-escaped-quote-marks", "javascript", "src/escapes.js",
          'const a = "say \\"hi\\"";\nconst b = "sha1(password) \\\\ is weak";\nconst c = \'\\\'\' + "md5(token)";\n'
          "module.exports = { a, b, c };\n", WEAK)
    # ---- the same words as code: still reported
    loud("r5-js-weak-hash-in-code", "javascript", "src/hash.js",
         'const sha1 = require("./sha1");\nconst md5 = require("./md5");\nconst a = sha1(password);\nconst b = "x" + md5(token);\n'
         "const c = `${sha1(secret)}`;\nmodule.exports = { a, b, c };\n",
         (WEAK, "= sha1(password)"), (WEAK, '"x" + md5'), (WEAK, "`${sha1(secret)}`"))
    loud("r5-js-create-hash-with-weak-algorithm", "javascript", "src/digest.js",
         'const crypto = require("crypto");\nconst a = crypto.createHash("md5").update(password).digest("hex");\n'
         "const b = crypto.createHash('sha1').update(secret).digest('hex');\nmodule.exports = { a, b };\n",
         (WEAK, 'createHash("md5")'), (WEAK, "createHash('sha1')"))
    loud("r5-js-directives-in-comments", "typescript", "src/loose.ts",
         "export const a = 1; // TODO wire this up\nexport const b = 2; // eslint-disable-line no-console\n// @ts-ignore\n"
         'export const c: number = "x";\n',
         (TODO, "wire this up"), (LINT, "eslint-disable-line"), ("suppress.type", "@ts-ignore"))
    loud("r5-js-credential-assigned-in-code-still-fires", "javascript", "src/config.js",
         f'const password = "{SECRET}";\nconst cfg = {{ api_secret: \'{OTHER}\' }};\nmodule.exports = {{ password, cfg }};\n',
         ("secrets.generic", "const password"), ("secrets.generic", "api_secret"))
    loud("r5-js-provider-key-in-template", "javascript", "src/aws.js",
         "const note = `rotate the key @@AWS@@ soon`;\nmodule.exports = { note };\n",
         ("secrets.hardcoded", "rotate the key", *BLOCK))
    loud("r5-js-real-code-after-words-in-strings", "javascript", "src/mixed.js",
         'const help = "never call sha1(password) here // TODO";\nconst t = `sha1 of the password: ${name}`;\nconst hash = sha1(password);\n'
         "module.exports = { help, t, hash };\n", (WEAK, "const hash"))


def golang():
    pkg = "package shop\n\n"
    # ---- the words are text: no finding
    quiet("r5-go-words-in-interpreted-string", "go", "shop/doc.go",
          pkg + 'var doc = "// TODO: sha1(password) is weak; //nolint:gosec"\n', WEAK, TODO, SUPP)
    quiet("r5-go-words-in-raw-string", "go", "shop/help.go",
          pkg + "var help = `\nh := sha1.Sum([]byte(password)) // TODO use argon2\nx := 1 //nolint:gosec\n"
          f'password = "{SECRET}"\n`\n', WEAK, TODO, SUPP, "secrets.generic")
    quiet("r5-go-words-after-a-rune-quote", "go", "shop/quote.go",
          pkg + "var quote, advice = '\"', \"md5(token) is weak, so is sha1(password)\"\n", WEAK)
    quiet("r5-go-words-in-struct-tag", "go", "shop/model.go",
          pkg + 'type Row struct {\n\tDigest string `json:"sha1,omitempty" db:"password"`\n}\n', WEAK)
    quiet("r5-go-nolint-text-in-string", "go", "shop/tips.go",
          pkg + 'var tip = "add //nolint:gosec after the line"\nvar tip2 = "// nolint is a directive"\n', LINT, SUPP)
    quiet("r5-go-credential-in-raw-string", "go", "shop/sample.go", pkg + f'var sample = `password = "{SECRET}"`\n', "secrets.generic")
    # ---- the same words as code: still reported
    loud("r5-go-weak-hash-of-a-password-in-code", "go", "shop/hash.go",
         pkg + 'import "crypto/sha1"\n\nfunc Digest(password []byte) [20]byte {\n\treturn sha1.Sum(password)\n}\n', (WEAK, "sha1.Sum(password)"))
    loud("r5-go-weak-hash-after-a-raw-string", "go", "shop/digest.go",
         pkg + 'import "crypto/md5"\n\nfunc Digest(password []byte) ([16]byte, string) {\n'
         "\tlabel, sum := `say \"hi\" // twice`, md5.Sum(password)\n\treturn sum, label\n}\n", (WEAK, "md5.Sum(password)"))
    loud("r5-go-weak-hash-after-a-rune-quote", "go", "shop/token.go",
         pkg + 'import "crypto/sha1"\n\nfunc Sum(token []byte) ([20]byte, rune) {\n\tquote, sum := \'"\', sha1.Sum(token)\n\treturn sum, quote\n}\n',
         (WEAK, "sha1.Sum(token)"))
    loud("r5-go-directives-in-comments", "go", "shop/loose.go",
         pkg + "var a = 1 //nolint:gosec\nvar b = 2 // nolint\nvar c = 3 // TODO tidy\n",
         (SUPP, "nolint:gosec"), (LINT, "// nolint"), (TODO, "TODO tidy"))
    loud("r5-go-credential-assigned-in-code", "go", "shop/config.go",
         pkg + f'func Login() string {{\n\tpassword := "{SECRET}"\n\treturn password\n}}\n', ("secrets.generic", "password :="))
    loud("r5-go-provider-key-in-raw-string", "go", "shop/aws.go", pkg + "var note = `rotate the key @@AWS@@ soon`\n",
         ("secrets.hardcoded", "rotate the key", *BLOCK))


def adversarial():
    sneaky("r5-lex-regex-with-a-quote-then-weak-hash", "javascript", "src/a.js",
           'const re = /"/;\nconst re2 = /"/; const h = sha1(password);\nmodule.exports = { re, re2, h };\n', (WEAK, "const re2"))
    sneaky("r5-lex-division-then-weak-hash", "javascript", "src/b.js",
           'const r = f(a) / 2 + g(b) / 3; const h = md5(token) + "x";\nmodule.exports = { r, h };\n', (WEAK, "const r"))
    sneaky("r5-lex-nested-template-then-weak-hash", "javascript", "src/c.js",
           "const t = `${ `${\"}\"}` }`; const h = sha1(password);\nmodule.exports = { t, h };\n", (WEAK, "const t"))
    sneaky("r5-lex-unterminated-quote-then-weak-hash", "javascript", "src/d.js",
           "const s = 'abc\nconst h = sha1(password);\nmodule.exports = { h };\n", (WEAK, "const h"))
    sneaky("r5-lex-escaped-rune-then-weak-hash", "go", "shop/e.go",
           'package shop\n\nimport "crypto/sha1"\n\nfunc Sum(token []byte) ([20]byte, rune) {\n\tquote, sum := \'\\\'\', sha1.Sum(token)\n'
           "\treturn sum, quote\n}\n", (WEAK, "sha1.Sum(token)"))
    sneaky("r5-lex-template-expression-comment-then-weak-hash", "javascript", "src/f.js",
           "const t = `${ /* } */ 1 }`; const h = md5(password);\nmodule.exports = { t, h };\n", (WEAK, "const t"))
    # JSX text with an apostrophe pairs with a quote in a regex later on the line: .jsx and .tsx keep the plain line reading
    sneaky("r5-lex-tsx-apostrophe-and-regex-keep-the-weak-hash", "typescript", "src/j.tsx",
           "export const el = <p>Don't</p>; export const h = sha1(password); export const re = /'/;\n", (WEAK, "export const el"))
    sneaky("r5-lex-jsx-apostrophe-in-jsx-text-then-weak-hash", "javascript", "src/k.jsx",
           "const el = <p>Don't panic</p>; const h = md5(password); const re = /'/g;\nmodule.exports = { el, h, re };\n", (WEAK, "const el"))
    # JSX written in a plain .js or .ts file: the file gets the plain line reading too
    sneaky("r5-lex-jsx-in-a-js-file-apostrophe-then-weak-hash", "javascript", "src/l.js",
           "const el = <p>Don't</p>; const h = sha1(password); const re = /'/;\nmodule.exports = { el, h, re };\n", (WEAK, "const el"))
    sneaky("r5-lex-p-element-in-a-ts-file-then-weak-hash", "typescript", "src/m.ts",
           "export const el = <p>It's {name}'s</p>; export const h = md5(token);\n", (WEAK, "export const el"))
    # a division sign after `)`, `}` or a name that could instead open a regular expression: between two regular expressions holding a
    # quote, the weak call is code and must not be read as the inside of a string
    sneaky("r5-lex-slash-after-paren-between-regexes", "javascript", "src/g.js",
           'if (ok) /"/.test(a) && md5(password) && /"/.test(b);\n', (WEAK, "if (ok)"))
    sneaky("r5-lex-slash-after-brace-between-regexes", "javascript", "src/h.js",
           "x = y }\n/'/.test(s) && md5(password) || /'/.exec(s);\nmodule.exports = { x };\n", (WEAK, "md5(password)"))
    sneaky("r5-lex-division-sign-before-a-regex-with-a-quote", "javascript", "src/i.js",
           "function f(s) {\n  if (s) /'/.test(s) && md5(password) && /'/.test(s);\n}\nmodule.exports = { f };\n", (WEAK, "md5(password)"))


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="write the round-5 (strings) training cases")
    ap.add_argument("--only", help="comma-separated ids: write only these")
    ap.add_argument("--out", help="write the cases under this folder instead of eval/corpus")
    args = ap.parse_args(argv)
    only = set(args.only.split(",")) if args.only else None
    if args.out:
        v1.CORPUS = Path(args.out)
    seen, real = [], v1.emit

    def emit(id, *a, **kw):
        seen.append(id)
        if only is None or id in only:
            real(id, *a, **kw)
    v1.emit = emit
    javascript()
    golang()
    adversarial()
    assert len(set(seen)) == len(seen) == 37, len(seen)
    assert only is None or only <= set(seen), only - set(seen)
    print(len(v1.CASES), "round-5 string cases written to", v1.CORPUS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
