# What it catches

The categories of mistakes, the default action of each rule, and what each language gets. Every rule, with its preset
settings, is in [RULES.md](RULES.md) (generated from the code).

**Actions:** `block` and `fix` stop the commit; `warn` only reports.

| Mistake | Rules (default action) |
|---|---|
| Exposed secrets | `secrets.hardcoded`: provider keys and private keys (block), also, in simple shapes, when the literal is decoded (base64, hex, rot13, reversed) or joined from literal pieces first; the message names the step (what is not found: see Limits in the README). `secrets.env_file`: `.env`, key and credential files (block). `secrets.generic`: credential-like literals and JWTs (fix). `secrets.env_not_ignored` (warn). |
| Hallucinated or risky packages | `deps.hallucinated`: package or version not on PyPI/npm, in a manifest or an import (block). `deps.untrusted_source`: git/URL deps, custom indexes, odd lockfile URLs (block). `deps.new_package`: first release under 30 days (fix). `deps.version_cooldown`: version under 5 days (fix), with security bumps exempt. `deps.vulnerable`: OSV advisories for the pinned version (fix). `deps.undeclared` (fix). `deps.removed_stdlib`, e.g. `distutils` or `cgi` (fix). `deps.typosquat`, `deps.outdated` and `deps.deprecated` (warn). |
| Weakened tests | Assertions or cases removed, skip/xfail added, tests deleted (renames are detected), `.only` focus, trivial asserts, test-config narrowing (all fix). `sitecustomize.py`, `.pth` files and `pytest11` entry points (block). Result-changing pytest hooks (fix). Expected values changed alongside source (warn). |
| Placeholder or unsafe code | `... rest of the code` comments, `...` bodies, swallowed exceptions, debugger leftovers, syntax errors (fix). `eval`/`exec`, `shell=True`, pickle and unsafe YAML, SQL built with f-strings, `verify=False` (fix). Debug mode, wildcard CORS, weak crypto, raw HTML (warn). |
| Silenced checkers | `# nosec`, `# type: ignore`, `@ts-ignore`, `# pragma: no cover`, whole-file disables (fix). `# noqa`, `eslint-disable-line` (warn). In `[paths] sensitive` these are one step stricter. |
| Bloat and time complexity | Long or complex functions, long files, big diffs, duplicated blocks, unused imports (warn). Loops nested 3 or more deep, nested loops over the same list, `x in list` / `.pop(0)` / sorting inside loops, network or DB calls inside loops, branching recursion without a cache (warn). |
| Scope creep | `scope.forbidden_path` (block). `scope.out_of_scope` against a declared task scope (fix). `gate.config_changed` (warn). `gate.baseline_changed` (warn): the change adds baseline entries; they do not apply to that change. |

`aigate-lite explain` lists all 76 rules. `aigate-lite explain <rule>` shows how to change or allow one.

**Language coverage.** Python gets every check, using its AST. Go and JavaScript/TypeScript are read with a lexer
(strings and comments are never mistaken for code) and get secrets, dependencies, tests, suppressions,
placeholders, function length, complexity and loop nesting (estimated from brace structure, not parsed), duplicated
blocks, and the reviewer rules of [REVIEWER.md](REVIEWER.md) (each rule says which languages it covers). Unused imports and the
quadratic-pattern, call-in-loop and exponential-recursion checks are Python-only. Other languages get secrets,
placeholders, TODOs and suppressions.

**Text inside a string is not code.** In Python, JavaScript/TypeScript (`.js` `.mjs` `.cjs` `.ts`) and Go the rules `code.todo`,
`security.weak_crypto`, `suppress.lint`, `suppress.type` and `secrets.generic` ignore the contents of a string: quotes, template
literals and Go raw strings (backquotes), including a string that holds `// TODO`, `sha1(password)`, `// eslint-disable-line` or
`password = "..."`. What still counts: a provider key or a connection-string password inside a string (they are where real secrets
live), a credential assigned in code (`password = "..."`, `{ "password": "..." }`), a quoted key and its value inside a string (a JSON
body such as `{"token":"..."}` in a raw string), and a weak algorithm named as an argument (`createHash("md5")`, `{ digest: "sha1" }`).
A person and an AI agent get the same answer. Limits:
- Files that contain JSX keep the plain line reading (the same as 0.6.1): every `.jsx` and `.tsx` file, and any other JS/TS file whose
  text holds a closing tag (`</p>`), a self-closing tag (`/>`), a fragment (`<>`) or `<!--`. JSX text can confuse a string reader: the
  apostrophe in `<p>Don't</p>` can pair with a quote later on the line. There, words inside a string still count for
  `security.weak_crypto` and `secrets.generic`. The check looks at the raw text, so a file with HTML in a string (`"</div>"`) is read
  plainly too. `.vue` and `.svelte` files are unchanged (no string reading).
- After a quote that is never closed on its line, a quote that follows a name or a closing bracket with nothing between (`Don't`), a
  closed "string" that holds code (`'a; const h = f(x); b'`), a division sign that could instead open a regular expression holding a
  quote, or a regular expression that is more likely a division (after a variable named `p`, after `>` or a non-null `!`), the rest of
  the file counts as code. A file the lexer cannot read at all (an unclosed template literal or block comment) counts as code
  throughout. These fallbacks only ever add findings. The string reader remains a heuristic for source that is not valid code.
- A line over 2,000 characters (a minified bundle) is not read for weak crypto, and one between 2,000 and 4,000 characters is judged as
  code for `secrets.generic`.

**Size and copy-paste advice is off for people by default.** `size.*` (long functions and files, complexity, big
diffs), `perf.*` (loop findings) and `code.duplicate_block` are the "core" exception: they are not reported for a
person's change unless you name them in `[rules]` (`"size.*" = "warn"`) or use `preset = "strict"`. AI agents always get
them, and so does `aigate-lite audit --strict` (or `check --strict`). On a large existing repository these rules were
most of what `audit` reported, which is why they are off.
