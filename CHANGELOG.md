# Changelog

## 0.2.0 — Phase 2: usability

### Behaviour changes
- Errors are one cause line plus a `fix:` line, without tracebacks (`--debug` or `AIGATE_DEBUG=1` shows them):
  not a repository, git missing, unresolvable ranges (empty repo, shallow CI clone, unknown ref), invalid TOML
  (with a Windows-path hint), unreachable registries (the specific reason), bad flags (`see: aigate-lite --help`),
  the interpreter guard and a missing vendored script. Any unexpected error exits 2 (the hook blocks) with a short
  "internal error" message. An empty side of `--range` (e.g. `HEAD~1..`) now means HEAD.
- **Text output is compact by default** (JSON, SARIF and github formats unchanged): a one-line header, findings
  grouped by file (most severe file first) with aligned `line  ACTION  rule  message` columns, each rule's fix
  hint once, at most 25 findings in hooks (`+N more — run: aigate-lite check`), the summary line as before and
  one final `next:` line. `-v` keeps the previous detailed view (now with the `next:` line instead of the
  "Fix the findings…" sentence); `-q` is unchanged. Symbols fall back to ASCII (`X FIX`, `->`, `-`) when the
  output cannot encode them, instead of printing `?`. Colour: `--color`, `NO_COLOR`, `FORCE_COLOR`, otherwise only
  on a terminal; long lines are cut to the terminal width only on a terminal. Before / after:

      aigate-lite 0.1.0 · staged changes · 1 file (+1 -0) · human · config: built-in defaults
        FIX   security.eval                a.py:1
              eval() runs code built at runtime
              → Avoid eval/exec; parse the input explicitly (ast.literal_eval, JSON.parse).
      ✖ FIX · risk 30/100 · 1 fix · 0.1s — commit stopped
        Fix the findings, or for a false positive: `aigate-lite explain <rule>` shows how to allow it. Bypass…

      aigate-lite 0.2.0 · staged · 1 file +1 −0 · human
      a.py
        1  FIX    security.eval  eval() runs code built at runtime
                  → Avoid eval/exec; parse the input explicitly (ast.literal_eval, JSON.parse).
      ✖ FIX · risk 30/100 · 1 fix · 0.1s — commit stopped
      next: fix it, or for this commit only (humans): AIGATE_ALLOW=security.eval git commit ...
- **Fingerprints** are 16 hex characters and no longer depend on line numbers (they use the enclosing function
  and the code, or the whole line for suppressions); identical findings in one file are numbered. `[[allow]]`
  entries with 0.1 fingerprints (12 characters) keep matching during 0.2.x, each for the first matching
  finding in its file; `check -v` and `doctor` show the new value. SARIF `aigateFingerprint/v1` now carries the
  0.1 value. Renaming a function or file changes the fingerprints of its findings.
- `aigate-lite init [--preset NAME] [--print] [--force]` detects languages, package managers, the test command
  and private registries from tracked files, and writes only `preset`, detected `[deps]` keys and a commented
  `[[commands]]` block (before: a fixed template with every key). `--force` keeps the old file as
  `.aigate/lite.toml.bak`.
- Hooks: a non-vendored hook still uses the interpreter pinned at install without probing, but if that path
  is gone it now looks for python3 / python / `py -3` and prints one hint to re-run `install` (before: the
  hook failed). A candidate must run Python 3.11+ and print its version. When nothing qualifies the message
  lists every candidate with its version or error, the fix, and that the commit/push is blocked (it used to
  list only the names). Re-run `aigate-lite install` to get the new hooks.
- `check --all` reports the risk score but no longer escalates a Fix verdict to Block (the verdict is the
  strongest finding's action; JSON `escalated` is always false there). Staged, range and push checks still
  escalate at risk 60.
- `check --all` run by an AI agent uses the configuration committed in HEAD (before: built-in defaults,
  because the "base" of a full scan is the empty tree). Uncommitted config edits still never apply to agents.
- `security.shell` findings in Python files are fingerprinted by the call text instead of its line number,
  so the fingerprint survives edits above it. `[[allow]]` entries that used an old `security.shell`
  fingerprint no longer match until legacy fingerprints are supported (Phase 2 item 4).
- Installed-package metadata is read one distribution at a time: a broken distribution is skipped
  (before, it silently dropped every distribution after it); an unexpected error is no longer swallowed and
  reports `gate.error` (fail closed).

### Added
- CI: `.github/workflows/ci.yml` runs pyflakes, pytest and the self-scan on {ubuntu, macos, windows} × Python
  {3.11, 3.12, 3.13, 3.14}, with a non-blocking benchmark smoke on 3.12. Actions are pinned by commit SHA, the token is
  read-only, and dev tools are pinned in `requirements-dev.txt`.
- `check --format agent`: a short ASCII-only report for AI coding agents (`AIGATE verdict=… risk=… exit=…`, one
  `<ACTION> <rule> <path>:<line> <message> | fix: <hint>` line per finding, `MORE <n>` when capped at 50 block/fix
  and 20 warn lines, `END`). README: "Using with AI coding agents".
- `aigate-lite fix [--yes] [--only rule,...]`: removes staged debugger lines (`breakpoint()`, `pdb.set_trace()`,
  `debugger;` and a debugger import that is no longer used), un-focuses tests (`it.only(` → `it(`, `fit(` → `it(`)
  and unstages env/key files (`git rm --cached`, file kept on disk, anchored `.gitignore` line). Without `--yes` it
  only prints the diff. Refuses files with unstaged changes, symlinks and anything it cannot do exactly. Never run
  by hooks.
- `aigate-lite baseline [--include-secrets] [--prune]` writes `.aigate/baseline.json` (rule, path and
  fingerprint of each finding a full offline scan reports today; no code, messages or timestamps). Matching
  findings are hidden (`-v` shows them) and do not count. Secrets are never baselined unless
  `--include-secrets`. Outside `check --all` a baseline only covers findings on edited lines that existed
  before; new copies are reported. Refused for AI agents; agents read the baseline from the committed version.
- JSON: optional `legacy_fingerprint` per finding. SARIF: `partialFingerprints` gains `aigateFingerprint/v2`.
- Presets: `preset = "relaxed" | "balanced" | "strict"` (default balanced; new config key). Effective config
  = built-in defaults, then the preset, then your own settings. Table in docs/RULES.md (generated by
  `scripts/gen_docs.py`, which also lists every rule).
- `aigate-lite doctor [--offline] [--format text|json]`: checks git (2.13+), the repository, the Python the
  hooks will use, the hooks (and other hook managers, with the line to add), vendored-copy drift, config,
  unapproved commands, the cache, registry reachability (proxy, TLS/SSL_CERT_FILE advice) and AI-agent
  detection; exit 1 when something would make the hook fail.
- Hooks honour `AIGATE_PYTHON` (path to the interpreter to use; ignored with a note while an AI agent runs the
  commit, like `AIGATE_SKIP`). `install` prints `hook Python: <interpreter> (<version>): <why>`.
- `.aigate/lite.toml` for this repository: one `[[allow]]` per intentional finding in the tool's own source
  and test fixtures (exact rule, file and fingerprint each), so `check --all` on this repo has no fix/block.

### Fixed
- tests: `test_configured_command_runs` wrote a Windows interpreter path into a TOML basic string (`\U` is an
  invalid escape, so the config was rejected); it now uses a TOML literal string. It still fails on Windows
  when the interpreter path contains a space (see PROGRESS.md, BLOCKED).

### Not run / known limits
- CI matrix cells other than Windows/3.12 and Linux/3.12 (macOS, Python 3.11, 3.13, 3.14) were never executed; the
  workflow has not run on GitHub.
- `tests/test_gate.py::test_configured_command_runs` fails on Windows when the interpreter path contains a space
  (needs path quoting, which decision D5 does not allow); it passes on Linux.
- Hook start-up on Windows is about 13-15% slower than at phase start (a larger script is recompiled on every run);
  Windows bytecode reuse is not implemented.
- Item 1 is partial: versioned `python3.N` candidates and pinning for vendored installs conflict with frozen tests.
- The version literal in existing tests (`test_bytecode_cache`, `test_cache`, `test_perf`) was bumped to 0.2.0.

## Unreleased (Phase 1 → 1.0)

### Behaviour changes
<!-- Every changed default or text-output format goes here. -->
No existing rule IDs, config keys, JSON schema (1) or exit codes changed; one rule added
(`commands.untrusted`, 66 rules in total) and one config key (`[gate] cache`). New: `--format sarif`,
`check --all`, `--cache`.
- **`[[commands]]` from a repository's config no longer run until approved** with `aigate-lite trust`
  (new subcommand; `--rev` approves a committed config). Until then each check adds one finding of the new
  rule `commands.untrusted` (warn, low severity; `[rules]` overrides do not apply to it, and the config
  reports any that try) and runs nothing; any change to the commands needs a new approval. A config
  given with `--config` on the command line is trusted as before.
- `{files}` in commands: on Windows paths are double-quoted for cmd.exe and paths containing `%` or `"` are
  left out; paths starting with `-` are passed as `./-…` on every OS.
- New, **off by default**: a per-file result cache (`results-v2.json` + `results.key`, both 0600, in the
  cache dir) that remembers which files the pure checks found clean. Turn it on with `--cache` or
  `[gate] cache = true` (a non-boolean value is reported as `gate.config_invalid` and leaves it off).
  `--no-cache` wins over both and, as before, also turns off the registry lookup cache. (An earlier Phase 1
  build had it on by default; it was made opt-in because the measured gain is small.)
- `examples/github-actions.yml` now runs the gate and its config from the base branch, pinned by SHA,
  and uploads SARIF; copies of the old example keep working but are not updated.
- Vendored hooks pick the first interpreter that really runs Python 3.11+ among `python3`, `python`,
  `py -3` (one extra interpreter start per hook run), and stop with a message if none does.
- On a non-UTF-8 stdout (e.g. a cp1252 pipe on Windows) unencodable characters in text/github output are
  printed as `?` instead of crashing.
- Hooks installed on Linux/macOS start the gate through a small loader (`python -P -c …`) that keeps one
  bytecode file of the gate in `<cache dir>/pycache` (directory 0700, owned by you; reused only when it
  matches the current source's hash) instead of recompiling it on every run. If the directory cannot be
  used it falls back silently to compiling, as before. Hooks installed on Windows are unchanged.
  Re-run `aigate-lite install` to get the faster hooks.

### Added
- `AIGATE_PROFILE=1` prints `aigate-profile: {"blobs_read": n, "git_calls": n}` to stderr on exit.
- `bench/bench.py`: deterministic benchmark (4 scenarios, 30 runs, median/p95, git-call and blob counters)
  with `--save` / `--compare` perf gates and `--markdown` (docs/PERF.md is generated by it).
- `--format sarif` (SARIF 2.1.0) for code-scanning uploads.
- `examples/github-actions.yml` rewritten (SHA-pinned actions, gate and config from the base branch,
  SARIF upload) and new `examples/gitlab-ci.yml`; README "Enforce it in CI".
- `check --all`: scan every tracked file in HEAD as if newly added (finds secrets in the root commit,
  which `--range` cannot); skips ignore paths and change-only rules; not used by hooks.

### Changed
- Start-up: argparse, urllib, gzip, concurrent.futures, configparser, importlib.metadata, hashlib, shutil,
  shlex and glob are imported only by the code that needs them; `from __future__ import annotations` dropped.
- Python import checks no longer scan installed packages, manifests or the whole file list unless a new
  import is neither stdlib, first-party, private nor a local module; installed packages are read from
  `RECORD`/`top_level.txt` instead of stat'ing every installed file (5.8 s → 0 on the bench machine for
  commits without third-party imports). Same findings.
- Fewer git processes: one `check-ignore` for all root `.env*` files (none when there are none), git dir
  from the first `rev-parse`, no `rev-parse <sha>^` per pushed commit, empty tree computed once on demand.

- Checkers are `Checker(name, languages, needs_network, run)` objects; a checker whose languages are not
  in the change is skipped (same findings). Extra checkers can be appended to `CHECKERS`.
- README: "First success in under 2 minutes"; speed claim replaced by the generated docs/PERF.md.
- `bench/bench.py` also reports `--no-cache` and `--cache` timings for scenarios A and C, a `startup-hook` row
  on POSIX (start-up the way hooks start the gate), and notes in docs/PERF.md why the gain over the v0.1.0
  baseline depends on the environment. docs/PERF.md and bench/latest.json regenerated from the final code.

### Fixed
- Test harness: subprocess output is decoded with the child's real stdout encoding (`errors="replace"`),
  so `test_text_output_survives_a_non_utf8_pipe` passes on Linux too (it decoded cp1252 output as UTF-8).
- The repository's own docs no longer quote a private-key header (`check --all` on this repo blocked on
  PROGRESS.md); `tests/test_repo_hygiene.py` keeps tracked docs free of `secrets.hardcoded` findings.
- Windows: a changed file named like `a&cmd&.py` could inject shell commands through `{files}` (POSIX
  quoting under cmd.exe).
- Windows: `install --force` / `uninstall` no longer fail with WinError 32 (file renamed while open).
- Windows: vendored hooks no longer run the Microsoft Store `python3` stub.
- Windows: the text report no longer crashes with UnicodeEncodeError on a cp1252 pipe.
- Non-vendored hooks single-quote absolute paths (spaces, quotes, `$`).

## 0.1.0
- Initial release: 65 rules, staged / range / push checks, JSON schema 1.
