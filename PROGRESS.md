# Phase 1 progress log

Machine: Windows 11 (10.0.26300), Python 3.12.10 (`py -3.12`), git 2.52.0.windows.1.
All commands below run with that interpreter (`PY=.../Python312/python.exe`).

## Iteration 0 — setup

Context claims checked:
- `aigate_lite.py` is 3,884 lines, stdlib only, `requires-python >= 3.11` — **true**.
- No `.git` — **true**; `git init` + baseline commit `956c181` ("chore: baseline v0.1.0 (untouched)").
- "52 pytest tests, passing on 3.11–3.13" — **partly false on this machine**: 52 collected,
  **48 passed, 4 failed** on Windows / 3.12 before any change (`python -m pytest -q` → `4 failed, 48 passed in 236.68s`).
  - `test_installed_hooks_block_and_allow_commits`: `install --force` crashes with
    `PermissionError [WinError 32]` — `os.replace(hook, hook + ".local")` runs while the hook file is
    still open (Windows forbids it). Tool bug → fixed in item 7.
  - `test_vendored_install_and_init`: the vendored hook picks `python3`, which on Windows is the
    Microsoft Store alias stub ("Python was not found"). Tool bug → item 7.
  - `test_github_and_text_output`: text output crashes with `UnicodeEncodeError: 'charmap' codec can't
    encode character '\u2192'` when stdout is a cp1252 pipe. Tool bug → item 7.
  - `test_configured_command_runs`: the **test** writes `run = "C:\Users\...\python.exe ..."` into a TOML
    basic string; `\U` is an invalid TOML escape, so the config is rejected and no command runs. The
    tool behaves correctly (it reports `gate.config_invalid`). → **BLOCKED** (test is wrong on Windows;
    rule 7 forbids editing it). Suggested fix for a human: use a TOML literal string `'...'` or
    `sys.executable.replace("\\", "/")` in the test.
- The default `python` on PATH is 3.10.11 (EOL, rejected by the script); 3.12 is used throughout.
- `git config core.autocrlf=true` (system + global) on this box: added `.gitattributes` (`eol=lf`).
- Known limitation "`--range A..B` excludes commit A": **true** by reading `plan_runs`: base =
  `merge-base(A, B)` = A and commits = `rev-list A..B`, so A's own diff is never scanned; a root commit
  can never be the "B side" of a range. Addressed by item 9 (`check --all`).
- pyflakes on `aigate_lite.py`: clean at baseline. (tests/test_gate.py has 2 pre-existing unused-import
  warnings; not touched.)

## BLOCKED
- `tests/test_gate.py::test_configured_command_runs` fails on Windows because the test embeds a Windows
  path in a TOML basic string (invalid escape). Not a tool bug; test left unchanged per rule 7.
- ~~Item 8~~ unblocked 2026-10-31: the user approved adding `assert repo.run("trust").returncode == 0`
  to `test_configured_command_runs` (one line added, nothing removed). Implemented in iteration 8b.

## Iteration 1 — benchmark harness + AIGATE_PROFILE counters

Explore:
- No timing hooks existed. git is spawned from `run_git`, `Repo.discover`, `Repo.commit`, the single
  `git cat-file --batch` reader in `Repo.read`, and one `git check-ignore` per root `.env*` file.
- Blob reads all go through `Repo.read` (memoised per spec), so one counter there is exact.
- Risk: printing counters must not touch stdout (JSON output) — written to stderr only, only when
  `AIGATE_PROFILE=1`; `argparse` exits for `--version` go through `finally` so counters still print.

Change: `PROFILE` counter (`git_calls`, `blobs_read`), incremented at those 6 sites; `main()` prints
`aigate-profile: {"blobs_read": n, "git_calls": n}` to stderr on exit. `bench/bench.py` builds 4
deterministic fixture repos (D: 50,000 files via `git fast-import`, index from `read-tree`, nothing
checked out) and runs `check --staged --offline --no-commands --format json` 30× each.

Tests (tests/test_perf.py, new): counters present and > 0 on a real check with JSON stdout intact (TP);
no output without the env var (TN); `--version` reports zero git calls (edge). Failed before the
change (`2 failed, 1 passed` — missing line), pass after.

Verify:
- `python -m pyflakes aigate_lite.py` → clean.
- `python bench/bench.py --runs 30 --save bench/baseline.json` (the untouched v0.1.0 + counters):
```
python 3.12.10 on Windows-11-10.0.26300-SP0, 30 runs each
measure       median ms    p95 ms  git calls  blobs
python_noop        60.9      66.1
startup           312.1     340.5
A                5749.9    6265.2          9      2
B                5574.5    5718.5          9     22
C                5761.4    6460.7          9    302
D                5450.6    5655.6          9      2
```
- Finding: every scenario costs ~5.5 s regardless of size. cProfile of scenario A
  (`main 6258 ms`): `python_environment → importlib.metadata.packages_distributions` = **5,796 ms**,
  of which 4,202 ms is 22,660 `nt.stat` calls (it checks every file of every installed distribution
  exists). It runs for *any* added .py file, even one importing only stdlib/local modules. → item 3.
- `python -c pass` is already 60.9 ms median on this machine, so the "startup ≤ 40 ms" target is not
  reachable here whatever the script does; it will be reported as missed with this number.

## Iteration 2 — lazy imports

Explore:
- `python -X importtime aigate_lite.py --version`: importlib.metadata 61.5–68.4 ms cumulative, argparse
  18.3 ms, concurrent.futures 18.7 ms, urllib.request 15.4–16.4 ms, hashlib 8.6 ms, `__future__` 1.3 ms.
- importlib.metadata/importlib.util are used only by the Python-import checks; urllib/gzip/concurrent
  only for live registry lookups; configparser for setup.cfg/ini parsing; shutil/shlex/glob/atexit in
  one function each. No test reaches these through the module namespace (grep of tests/).
- Risk: an annotation that names a lazily imported module (`-> argparse.ArgumentParser`) would be a
  NameError once `from __future__ import annotations` is gone → removed that annotation.

Change: those imports moved into the functions that use them; `from __future__ import annotations`
removed (the 3.11+ guard still prints its message on 3.10: `aigate-lite needs Python 3.11 or newer
(found 3.10.11)`, rc=2).

Tests (tests/test_perf.py): importing the module loads none of argparse / concurrent.futures /
configparser / gzip / importlib.metadata / shutil / urllib.request (TP, failed before); the features that
need argparse and configparser still load them (TN); `--version` output unchanged (edge).

Verify:
- `python -m pytest -q` → `4 failed, 54 passed in 258.16s` (the same 4 pre-existing Windows failures).
- `python -m pyflakes aigate_lite.py` → clean.
- importtime after: top-level imports are ast 19.6 ms (incl. shared deps), dataclasses 15.0, tomllib 9.7,
  subprocess 6.8, json 4.3; argparse+shutil+textwrap (~16 ms) now load only when the CLI parses args.
- `python bench/bench.py --runs 30 --compare bench/baseline.json`:
```
measure       median ms    p95 ms  git calls  blobs   vs baseline (median / p95)
python_noop        89.0     101.5                     +46.1% / +53.6%
startup           304.3     350.7                     -2.5% / +3.0%
A                6250.5    6849.2          9      2   +8.7% / +9.3%
B                6204.6    7209.3          9     22   +11.3% / +26.1%
C                6620.3    7367.3          9    302   +14.9% / +14.0%
D                6207.0    7276.6          9      2   +13.9% / +28.7%
Perf gates: PASS
```
  The machine got slower between runs (bare `python -c pass` +46%), so wall times here are noise around
  the 5.8 s metadata scan; the counters are unchanged.
- Compile cost: `compile()` of the 190 KB source = **77.1 ms** median, module-body exec 4.4 ms. A script run
  as `python aigate_lite.py` is recompiled on every run (no .pyc for `__main__`), so ~77 ms of the startup
  is compilation, not imports. Noted in ROADMAP (hooks could import the module to use cached bytecode).

## Iteration 3 — work avoidance

Explore:
- `python_imports` computed `python_local_modules` (all of `git ls-files`), `python_declared` (every
  manifest in the tree) and `python_environment` (`importlib.metadata.packages_distributions()`, 5.8 s
  here) before looking at a single import — for every commit that touches a .py file.
- `packages_distributions()` → `dist.files` → `skip_missing_files` stats every file of every installed
  distribution (22,660 `nt.stat` calls on this interpreter).
- `check_env_not_ignored` built `set(ctx.files)` (full listing) on every staged run, and ran one
  `git check-ignore` per root `.env*` file.
- Range mode ran `git rev-parse <sha>^` once per pushed commit; `empty_tree()` (a `hash-object`) ran on every
  plan; `MERGE_HEAD` and the scope file each cost a `rev-parse --git-path`.
- `check_duplicates` looked up a file's added lines with a linear scan inside a nested loop (O(files²)).
- Already fine: one `git cat-file --batch` process; `ctx.files` was already a lazy property.

Change (behaviour identical, findings unchanged):
- New imports are collected first; only names that are not stdlib / first_party / private are looked up
  in the tree, and only with a targeted `git ls-files -- ':(glob)**/<name>/**' ':(glob)**/<name>.*'`
  (falls back to the full listing for >64 names, non-identifier names, or range mode). Manifests and
  installed packages are consulted only when a name is still unresolved.
- `_dist_tops()` reproduces `packages_distributions()` from `top_level.txt` / `RECORD` without stat'ing
  files (including its quirk of splitting backslash RECORD paths, so results are identical — asserted by a
  test against `importlib.metadata.packages_distributions()` on this interpreter).
- `check_env_not_ignored`: lists only the root `.env*` candidates (`:(literal)` pathspecs) and asks one
  `git check-ignore -z --stdin`; nothing at all when no candidate exists. A failing check-ignore still
  reports nothing, as before.
- `Repo.discover` asks for `--absolute-git-dir` in the same `rev-parse`; `private_path()` replaces
  `--git-path` for MERGE_HEAD and aigate/scope; `empty_tree()` memoised and computed only when needed;
  pushed commits' parents come from the `git log` call that already reads their messages.
- `check_duplicates`: dict lookup instead of `ctx_added()` (removed).

Tests (tests/test_perf.py, 8 new): stdlib+local imports trigger no environment scan and no manifest walk
(TP, failed before); a hallucinated third-party import is still flagged and the environment still consulted
(TN); local package in a `my pkg/ütil/` path with CRLF sources resolves as local (edge: unicode + space +
CRLF); environment mapping equals the stdlib's; git calls don't grow with the number of `.env*` files
(failed before: 12 vs 10); no file listing for a commit with no code; range git calls grow by exactly the
2 diff calls per extra commit (failed before: 6); 40 duplicated files → 39 findings, first copy unflagged.

Verify:
- `python -m pytest -q` → `4 failed, 62 passed in 90.50s` (same 4 pre-existing; suite was 258 s).
- `python -m pyflakes aigate_lite.py` → clean.
- `python bench/bench.py --runs 30 --compare bench/baseline.json`:
```
measure       median ms    p95 ms  git calls  blobs   vs baseline (median / p95)
python_noop        56.5      61.0                     -7.2% / -7.7%
startup           210.3     218.6                     -32.6% / -35.8%
A                 483.3     730.5          6      1   -91.6% / -88.3%   (baseline git 9, blobs 2)
B                 573.9     621.2          7     21   -89.7% / -89.1%   (baseline git 9, blobs 22)
C                 872.8     909.1          7    301   -84.9% / -85.9%   (baseline git 9, blobs 302)
D                 487.9     503.9          6      1   -91.0% / -91.1%   (baseline git 9, blobs 2)
Phase 1 targets:
  MISS startup (--version)    target <= 40 ms       achieved 210.3 ms
  met  scenario A             target >= 40% faster  achieved 91.6% faster
  met  scenario B             target >= 40% faster  achieved 89.7% faster
  met  D / A                  target <= 1.2         achieved 1.01
Perf gates: PASS
```
  (startup is not affected by this item; the −32.6% vs the item-2 run is the machine being less loaded —
  `python_noop` moved −7.2% too.) B and C make 7 git calls: JS files still need the full listing for
  `js_project` (package.json / tsconfig discovery).

## Iteration 4 ⚠ — safe result cache

Explore:
- The four pure checkers (secrets line scan, suppressions, code lines, Python AST) read only one file's
  blobs, its added/removed lines, its path (ext, test/ignore globs) and the config; none reads provenance,
  the registry, `ctx.merge` or other files (grep of `ctx.agent|provenance|registry|merge|mode`).
- Blob ids are already in `git diff --raw --no-abbrev` output; parse_raw dropped them.
- Risk: storing finding text on disk (secret leakage); stale results if any key input is missed.

Change: FileChange gets `new_sha`/`old_sha`; the four checkers' loop bodies moved verbatim into
`_secrets_file/_suppressions_file/_code_lines_file/_python_file` and called through `ResultCache.run`.
After review: **only clean verdicts are stored** (key → timestamp); a file with findings is re-checked
every run. Key = sha256(check, path, old path, status, mode, new blob, old blob, sha256(added+removed
lines), sha256(version + script source + RULES + config + Python minor)). File `results-v2.json` + key
`results.key` in the cache dir, created 0600 via O_EXCL temp + `os.replace`; HMAC-SHA256; >2 MB, bad MAC,
malformed JSON, RecursionError/MemoryError → ignored. Pruned to 14 days / 5,000 entries; hits refresh
the timestamp daily. Python parses that hit RecursionError/MemoryError are not cached. `--no-cache`
disables it.

Tests (tests/test_cache.py, 12): second run hits with identical findings and no secret in the file (TP);
clean code cached as clean (TN); config change and tool source/version change invalidate; forged entries
with the old MAC → whole cache ignored, secret still blocks; replaced key / garbage JSON → ignored, no
gate.error; same blob in tests/ and app/ keyed by path (warn vs fix); `--no-cache` writes nothing; 0600
(POSIX only) and pruning; empty / CRLF / binary files; secret + TODO on one line never reach the file;
200,000-deep `[[[` file ignored without a crash.

Review (independent subagent, diff + tests only): 6 findings.
1. HIGH raw secrets reach the cache (keys of code.todo etc. are the whole line) → **fixed** (clean-only).
2. MEDIUM key depended on git diff settings → **fixed** (hash of added/removed lines in the key).
3. MEDIUM RecursionError on nested JSON / no size cap → **fixed** (caught; 2 MB cap).
4. LOW transient parse failure cached as clean → **fixed** (`ctx.parse_failures`).
5. LOW salt re-serialised per file → **fixed** (digest once per config).
6. LOW temp-file leftovers, PID reuse, no refresh on hit → **fixed** (`_write_private` with O_EXCL +
   cleanup; daily refresh).
Not fixed (documented): whoever can write the key file can forge the cache (same user account).

Verify:
- `python -m pytest -q --ignore=tests/test_ci.py --ignore=tests/test_checkers.py` → `4 failed, 74 passed`
  (the 4 known; test_ci/test_checkers are the pre-written item 5/6 tests).
- `python -m pyflakes aigate_lite.py` → clean.
- `python bench/bench.py --runs 30 --compare bench/baseline.json`:
```
measure       median ms    p95 ms  git calls  blobs   vs baseline (median / p95)
python_noop        59.3     132.9                     -2.6% / +101.1%
startup           208.4     249.5                     -33.2% / -26.7%
A                 525.8     636.3          6      1   -90.9% / -89.8%
B                 592.6     778.8          7     21   -89.4% / -86.4%
C                 840.7     977.7          7    301   -85.4% / -84.9%
D                 490.8     518.6          6      1   -91.0% / -90.8%
A-cold            460.9     481.4
C-cold            872.7     942.1
Perf gates: PASS
```
  Measured benefit is small: C warm 840.7 vs cold 872.7 ms (−3.7%); A warm vs cold is within noise
  (startup + compile + git dominate). The cache is kept because it is safe and cheap, not because it is
  a big win on this machine.

## Iteration 5 — Checker plugin interface

Explore:
- `CHECKERS` was a list of functions; `run_check` used `checker.__name__` in `gate.error` messages and
  `checker in SECRET_CHECKERS` to skip secrets in range mode (scanned per commit instead).
- suppressions / code_lines / duplicates read only `ctx.code_changes(CODE_EXTS)`, python only
  `code_changes(PY_EXTS)`; every other checker can react to any path (manifests, env files, test config).
- Risk: a skip predicate that differs from the checker's own file filter would drop findings.

Change: `Checker(name, languages, needs_network, run)` dataclass; `applies(ctx)` uses the *same*
`ctx.code_changes(exts)` filter the checker uses, so skipping is exact. Names kept equal to the old
function names (gate.error text unchanged). No build step; single file.

Tests (tests/test_checkers.py, 5): interface shape (11 checkers, unique names, network flag); JS-only
change never calls check_python (TP skip); .py change still flags eval (TN); deleted .py not parsed
(edge); a plugin Checker appended to CHECKERS runs, and one that raises yields
`check_boom failed: RuntimeError: kaput` as a blocking gate.error (fail closed). The 52 original tests are
untouched.

Verify:
- `python -m pytest -q --ignore=tests/test_ci.py` → `4 failed, 79 passed` (the 4 known).
- `python -m pyflakes aigate_lite.py` → clean.
- `python bench/bench.py --runs 30 --compare bench/baseline.json`:
```
python_noop        56.5      68.2                     -7.2% / +3.2%
startup           211.1     223.8                     -32.4% / -34.3%
A                 484.4     511.2          6      1   -91.6% / -91.8%
B                 539.3     564.2          7     21   -90.3% / -90.1%
C                 745.3     787.5          7    301   -87.1% / -87.8%
D                 500.6     531.8          6      1   -90.8% / -90.6%
A-cold            457.3     477.1
C-cold            864.0     948.8
Perf gates: PASS   (D / A = 1.03)
```

## Iteration 6 ⚠ — CI as the enforcement point + SARIF

Explore:
- The old example used tag pins (`actions/checkout@v4`), ran the PR's own copy of the gate, and in range
  mode a human's config is read from HEAD (`load_config(repo, head)`), i.e. from the PR itself.
- Output formats are text/json/github; Finding paths like `(gate)` / `(commit)` have no file.
- Risk: SARIF that code scanning rejects; a CI job that a PR can weaken; an upload failure that blocks.

Change: `--format sarif` (`report_sarif`: SARIF 2.1.0, rules used, `ruleIndex`, levels error/warning,
percent-encoded relative URIs, `partialFingerprints` = existing fingerprint, allowed findings as
`suppressions: external`, repo-wide findings located at `.aigate/lite.toml`). Exit codes unchanged.
`examples/github-actions.yml` rewritten: actions pinned by SHA (looked up with
`git ls-remote --tags https://github.com/actions/checkout` etc.: checkout v5.1.0 `fbc6f399…`,
setup-python v6.3.0 `ece7cb06…`, codeql-action v4.39.1 peeled `9b325408…`), gate script **and config**
from the base branch (empty config = defaults when the base has none), `persist-credentials: false`,
`--no-commands`, annotations step decides pass/fail, SARIF upload `continue-on-error`. New
`examples/gitlab-ci.yml`. README section "Enforce it in CI" (required status check `gate`, CODEOWNERS
requirement, fork pipelines).

Tests (tests/test_ci.py, 6): SARIF with findings validates against a minimal 2.1.0 shape and never contains
the secret (TP); clean code → valid SARIF with empty results (TN); allowed finding suppressed and a
`fix tures/ünï.py` path percent-encoded (edge); repo-wide finding still has a location; workflow uses only
40-hex SHA pins, base config always passed quoted, upload non-blocking; GitLab example present. (The two
example-file tests passed before the reporter existed because the example files were written first.)

Review (independent subagent): 10 findings.
1. HIGH PR config used when base has no lite.toml → **fixed** (always `--config`, empty file fallback).
2. HIGH workflow file comes from the PR → **documented** (README step 4 now mandatory CODEOWNERS /
   required workflow; caveat restored in the YAML). Cannot be fixed inside a workflow file.
3. HIGH failed SARIF upload turns the check red → **fixed** (`continue-on-error: true`).
4. HIGH GitLab fork MRs check against the fork → **documented** (run fork pipelines in the parent project).
5. MEDIUM results without a location → **fixed** (+ test).
6. MEDIUM first-adoption PR fails → **fixed** (PR copy used once, with a `::warning::`).
7. MEDIUM GitLab fetch without refspec → **fixed**.
8. LOW unquoted `$CONFIG` → **fixed**.
9. LOW gate runs twice → **documented** in README (first run decides).
10. LOW README merge-queue / check name → **fixed**.

Verify:
- `python -m pytest -q` → `4 failed, 85 passed in 116.09s` (the 4 known).
- `python -m pyflakes aigate_lite.py` → clean.
- `python bench/bench.py --runs 30 --compare bench/baseline.json`:
```
startup           212.6     224.4                     -31.9% / -34.1%
A                 474.8     484.4          6      1   -91.7% / -92.3%
B                 527.1     544.4          7     21   -90.5% / -90.5%
C                 732.1     769.8          7    301   -87.3% / -88.1%
D                 502.8     517.4          6      1   -90.8% / -90.9%
Perf gates: PASS   (D / A = 1.06)
```

## Iteration 7 ⚠ — Windows hook compatibility

Explore:
- Vendored hook used `command -v python3 || command -v python`: on Windows `python3` resolves to the
  Microsoft Store stub (exists, prints an install hint, exit 9009) → baseline failure of
  `test_vendored_install_and_init`. On this box plain `python` is also 3.10 (too old).
- Non-vendored hook double-quoted absolute paths: `$`, backticks or `"` in a path would break it.
- `cmd_install --force` called `os.replace` on the hook while it was open → `PermissionError [WinError 32]`.
- Text report crashes with `UnicodeEncodeError` on a cp1252 pipe (`→`, `·`).
- pre-push refs already parse CRLF (`str.split()`); hook files are written with `newline="\n"`.

Change: hook probes `python3`, `python`, `py -3` in that order (each must run 3.11+, stdin closed),
stops with "no Python 3.11+ found …" if none; absolute paths single-quoted (`_sh_quote`); install /
uninstall close the hook file before rename/remove; stdout/stderr get `errors="replace"` when not UTF-8.

Tests (tests/test_hooks.py, 9): rendered order and probe; paths with spaces and `'` quoted; under `sh`
with a fake PATH (every candidate stubbed, git+sh dirs only): broken `python3` stub skipped and a secret
blocked (TP), `py -3` fallback passes clean code (TN), no interpreter → message + exit 1, pre-push with CRLF
refs in a repo path with a space (edge); install --force keeps the old hook (Windows guard); text report
on cp1252 pipe; JSON unchanged on cp1252.

Review (independent subagent): no blocking defects; 4 points.
1. MEDIUM test PATH could leak a real python on Linux → **fixed** (all candidates stubbed).
2. LOW vacuous order assertion; install test only meaningful on Windows → **fixed** / **commented**.
3. LOW probe inherits stdin → **fixed** (`</dev/null`). Cost: one extra interpreter start per hook run
   (more on Windows when stubs are tried first).
4. LOW `--format github` on a cp1252 pipe prints `?` for unencodable path characters (was a crash) →
   **documented** in CHANGELOG.

Verify:
- `python -m pytest -q` → `1 failed, 97 passed in 132.47s` — **3 of the 4 baseline Windows failures now pass**;
  only the BLOCKED test-side TOML bug remains. Re-run of hooks+gate after review fixes: `1 failed, 25 passed`.
- `python -m pyflakes aigate_lite.py` → clean.
- `python bench/bench.py --runs 30 --compare bench/baseline.json`:
```
startup           214.1     231.2                     -31.4% / -32.1%
A                 478.1     517.2          6      1   -91.7% / -91.7%
B                 546.4     597.3          7     21   -90.2% / -89.6%
C                 740.3     793.5          7    301   -87.2% / -87.7%
D                 512.6     546.9          6      1   -90.6% / -90.3%
Perf gates: PASS   (D / A = 1.07)
```
  (The bench calls the script directly, so the hook's interpreter probe is not in these numbers.)

## Iteration 8 ⚠ — trust model for [[commands]]: BLOCKED

Explore: `run_commands` runs every `[[commands]]` entry from the active config through `shell=True`;
for humans in staged mode the config is the working-tree `.aigate/lite.toml`, so a cloned repo's config
runs on the first commit. `test_configured_command_runs` (original suite) asserts exactly that flow.
Plan stopped at step 2: the item conflicts with non-negotiable 7 (see BLOCKED). No code changed; no commit
of code. README still warns "Commands in `.aigate/lite.toml` run on your machine".

## Iteration 9 — full-tree scan (`check --all`)

Explore:
- `--range A..B` uses base = merge-base = A, so A's own diff (and any root commit) is never scanned.
- In range mode secret checkers are skipped in the main pass and run per commit; with no commits they
  would never run, so a whole-tree mode must run them in the main pass.
- Change-oriented rules would misfire on existing files: `scope.forbidden_path`, `scope.out_of_scope`,
  `gate.config_changed` (check_paths) and `size.diff_budget`.

Change: `check --all` (mutually exclusive with --staged/--range/--push): diff empty tree → HEAD, drop
`[paths] ignore` matches up front, run every checker except `check_paths` in one pass (secrets included),
no diff budget, no `[[commands]]`, text report without "commit stopped"/--no-verify lines. Exit 2 with
"check --all needs at least one commit" on an empty repo. Hook scripts never use it.

Tests (tests/test_full_scan.py, 4): secret in the **root commit** missed by `--range root..HEAD` and found
by `--all`, never printed (TP); clean 7-file tree with a forbidden path and `diff_files = 2` passes with no
budget/scope/config findings (TN); secret under `node_modules/` ignored and `my dir/ünï.py` with CRLF
flagged (edge); empty repo → exit 2, `--all` with `--staged`/`--push` → exit 2, no hook contains `--all`.

Verify:
- `python -m pytest -q` → `1 failed, 101 passed in 136.16s` (only the BLOCKED test).
- `python -m pyflakes aigate_lite.py` → clean.
- `python bench/bench.py --runs 30 --compare bench/baseline.json`:
```
startup           209.3     214.8                     -32.9% / -36.9%
A                 470.4     484.4          6      1   -91.8% / -92.3%
B                 524.6     539.6          7     21   -90.6% / -90.6%
C                 746.5     763.1          7    301   -87.0% / -88.2%
D                 498.4     521.7          6      1   -90.9% / -90.8%
Perf gates: PASS   (D / A = 1.06)
```

## Iteration 10 — release prep

- README: "First success in under 2 minutes" (install --vendor → a fake key built at run time so the
  README does not flag itself → blocked commit → clean up → optional `check --all`); Windows `py -3` note;
  hand-typed "0.3–0.7 s" speed claim and "52 tests" count replaced by pointers to generated output;
  `check --all` row; bench commands under Development. (The 2-minute figure is a goal for the steps, not
  a measured number.)
- CHANGELOG "Behaviour changes" complete for the phase (result cache default, hook interpreter search,
  `?` replacement on non-UTF-8 pipes, new CI example); confirms no rule ID / config key / schema / exit
  code changes.
- `bench/bench.py --markdown` writes docs/PERF.md; `bench/latest.json` is the run it was generated from.
- Self-audit `python aigate_lite.py check --all --offline` on this repo: **1 block** —
  `secrets.hardcoded tests/test_secrets_deps.py:49` (the original v0.1.0 test's
  OpenSSH private-key header fixture, unchanged per rule 7; hooks never saw it because they
  check only added lines). Plus 13 fix-level findings in the tool's own source/tests (regex literals,
  intentional `shell=True` for `[[commands]]`, test fixtures). None comes from samples added this phase.
  Suggested for a human: split that fixture string, or add an `[[allow]]` with a path for it.

Verify:
- `python -m pytest -q` → `1 failed, 101 passed in 135.45s` (only the BLOCKED test).
- `python -m pyflakes aigate_lite.py` → clean.
- `python bench/bench.py --runs 30 --compare bench/baseline.json --markdown docs/PERF.md --json bench/latest.json`:
```
python_noop        54.9      59.0                     -9.9% / -10.7%
startup           208.1     219.0                     -33.3% / -35.7%
A                 470.6     486.2          6      1   -91.8% / -92.2%
B                 524.8     557.0          7     21   -90.6% / -90.3%
C                 738.8     757.1          7    301   -87.2% / -88.3%
D                 495.9     525.2          6      1   -90.9% / -90.7%
A-cold            459.3     468.8
C-cold            876.5     964.4
  MISS startup (--version)    target <= 40 ms       achieved 208.1 ms
  met  scenario A             target >= 40% faster  achieved 91.8% faster
  met  scenario B             target >= 40% faster  achieved 90.6% faster
  met  D / A                  target <= 1.2         achieved 1.05
Perf gates: PASS
```
- Caveat on the A–D gains: most of the baseline's ~5.5 s was `importlib.metadata.packages_distributions()`
  over this interpreter's installed packages; on an interpreter with few packages the baseline (and so the
  relative gain) is much smaller. The startup miss is structural here: bare `python -c pass` is 54.9 ms and
  compiling the 190 KB script is ~77 ms (iteration 2).

## Iteration 8b ⚠ — trust model for [[commands]] (unblocked by the user)

User approval: add `assert repo.run("trust").returncode == 0` after `repo.commit()` in
`tests/test_gate.py::test_configured_command_runs`. That is the only change to an original test.

Change: `aigate-lite trust [--rev REV]` validates the config, prints every command (name, run, stage,
action, timeout, when; control characters escaped via repr), refuses when an AI agent is detected, and
appends sha256(canonical validated list) to `.git/aigate/trusted` (0600, atomic). `run_commands` runs a
stage's commands only if the list's hash is approved or the config came from `--config`; otherwise one
`commands.failed` finding with action `warn`, naming the commands and the fix. Reused rule id (no new
rule without approval). `{files}`: cmd.exe-safe quoting on Windows (double quotes; `%`/`"` paths
dropped), `./` before a leading `-`. Write failure → clean message, exit 2.

Tests (tests/test_trust.py, 11): untrusted → warn and nothing runs, then `trust` → runs (TP); no commands
→ no finding, `trust` says so (TN); changed command and changed `when` need re-approval; switching back to
an approved list needs none; `--config` file in a path with a space is trusted; push-stage commands make no
noise at commit; corrupt/CRLF/binary trust file → untrusted, no gate.error, `trust` rewrites it; control
characters shown escaped and agents refused; `trust --rev feature` + `--range main..feature` runs; a file
named `a&echo INJECTED&.py` does not inject on Windows (**this test executed `echo INJECTED` before the fix**)
and `-rf.py` arrives as `./-rf.py`.

Review (independent subagent): 6 findings.
1. HIGH Windows `{files}` injection via cmd.exe → **fixed** (+ test that reproduced it).
2. HIGH vendored `.aigate/aigate_lite.py` is repo code that runs → **documented** (README limit; ROADMAP:
   pin by hash). Pre-existing, outside this item.
3. MEDIUM `trust` display could hide content; agents could self-approve → **fixed** (escaped, all fields,
   agents refused). No y/N prompt: it would break the approved test line (non-TTY, no flag).
4. MEDIUM reusing `commands.failed` lets repo `[rules]` silence/upgrade the notice and adds risk score →
   **fixed in 8c** after approval (was: open, needs a decision): a dedicated rule id was not approved. Fails closed in every case (commands
   never run untrusted). ROADMAP.
5. LOW push-stage config not approvable; per-worktree store → `--rev` **added**; common-dir store in ROADMAP.
6. LOW write failure traceback → **fixed**.

Verify:
- `python -m pytest -q` → `1 failed, 112 passed in 147.50s` (only the Windows TOML-escape bug in
  `test_configured_command_runs` remains; its new `trust` line passes).
- `python -m pyflakes aigate_lite.py` → clean.
- `python bench/bench.py --runs 30 --compare bench/baseline.json`:
```
python_noop        57.7      62.3                     -5.3% / -5.7%
startup           156.0     227.9                     -50.0% / -33.1%
A                 473.5     498.0          6      1   -91.8% / -92.1%
B                 527.3     550.1          7     21   -90.5% / -90.4%
C                 716.9     747.6          7    301   -87.6% / -88.4%
D                 498.4     528.3          6      1   -90.9% / -90.7%
Perf gates: PASS   (D / A = 1.05)
```
  startup median 156.0 vs 208.1 in iteration 10 with p95 227.9: run-to-run noise (this item does not touch
  start-up). docs/PERF.md still shows the iteration-10 run; it was not regenerated.

## Iteration 8c — `commands.untrusted` rule (approved by the user)

User approval: add a dedicated rule instead of reusing `commands.failed`.

Change: new rule `commands.untrusted` (warn, low, confidence 1.0) — 66 rules. It is in `FIXED_RULES`:
`apply_policy` ignores `[rules]` overrides for it (exact or glob), and `validate_config` reports any key
that matches it as `gate.config_invalid`. `[[allow]]` entries still work on it. `commands.failed` (actual
command failures) stays overridable. README rule count and trust text, CHANGELOG updated.

Tests (tests/test_trust.py, +3, helpers switched to the new rule): `"commands.untrusted" = "off"`,
`= "block"` and `"commands.*" = "block"` all leave exactly one warn notice, exit 0, and add at most 2.0 risk
compared with `--no-commands`; the config problem names the rule; `"commands.failed" = "warn"` still
applies to real failures; `explain` lists the new rule. 6 failed before the rule existed.

Verify:
- `python -m pytest -q` → `1 failed, 115 passed in 157.38s` (only the Windows TOML bug in
  `test_configured_command_runs`).
- `python -m pyflakes aigate_lite.py` → clean.
- `python bench/bench.py --runs 30 --compare bench/baseline.json`:
```
startup           208.0     214.9                     -33.4% / -36.9%
A                 485.4     496.6          6      1   -91.6% / -92.1%
B                 529.5     541.6          7     21   -90.5% / -90.5%
C                 714.4     759.9          7    301   -87.6% / -88.2%
D                 498.5     511.3          6      1   -90.9% / -91.0%
Perf gates: PASS   (D / A = 1.03)
```

# Cleanup after the Phase 1 review (6 findings)

Decisions given by the user: D1 split the fixture string at run time; D2 result cache becomes opt-in
(`--cache` / `[gate] cache = true`); D3 attempt the pycache start-up change, BLOCKED if a risk stays open.

## Cleanup — Iteration 0

Machine: same box (Windows 11 10.0.26300, `py -3.12` = 3.12.10, git 2.52.0.windows.1). The default
`python` on PATH is still 3.10.11 (below `requires-python`), so every command uses `py -3.12`.
- `aigate_lite.py` 4,337 lines, stdlib only — **true**. pyflakes 3.4.0, pytest 9.1.1.
- `python -m pytest -q` → `1 failed, 115 passed in 111.96s` — **differs from the reviewer's numbers**.
  The reviewer (Linux/3.13) saw `tests/test_hooks.py::test_text_output_survives_a_non_utf8_pipe` fail;
  on Windows that test passes (the harness decodes as cp1252 here, which matches the env). The failure
  on Windows is the known `tests/test_gate.py::test_configured_command_runs` (the test puts a Windows path
  in a TOML basic string: `\U` is an invalid escape, so the config is rejected and no command runs; see
  BLOCKED above). It is an original, frozen test and is not one of the 6 cleanup items, so it stays failing
  on Windows; it passes on Linux per the reviewer.
- `python -m pyflakes aigate_lite.py` → clean.
- `python aigate_lite.py check --all --offline -q` → `BLOCK · risk 100/100 · 2 block · 13 fix · 98 warn`
  — **matches** the reviewer. Blocks: `PROGRESS.md:419` and `tests/test_secrets_deps.py:49` (both a
  private-key header).
- Pre-cleanup bench (`python bench/bench.py --runs 30 --compare bench/baseline.json`), the reference for
  the final "no scenario regresses > 10 %" check. This box is faster today than in earlier iterations
  (`python -c pass` 34 ms vs ~55 ms), so compare against this run, not against older logs:
```
measure       median ms    p95 ms  git calls  blobs   vs baseline (median / p95)
python_noop        34.4      35.9                     -43.5% / -45.7%
startup           159.4     165.7                     -48.9% / -51.3%
A                 321.7     331.3          6      1   -94.4% / -94.7%
B                 359.4     374.6          7     21   -93.6% / -93.4%
C                 548.1     581.0          7    301   -90.5% / -91.0%
D                 343.7     371.4          6      1   -93.7% / -93.4%
A-cold            325.6     401.7
C-cold            736.7     843.4
Perf gates: PASS   (D / A = 1.07)
```

## Cleanup 1 — self-flagging docs

Explore:
- `check --all` scans tracked files in HEAD, so docs are scanned like code; `.md` is not ignored.
- Ran the tool's own `PROVIDER_SECRETS` regexes plus looser prefixes (`AKIA…`, `ghp_`, `sk-…`, `xox…`,
  `eyJ…`, `BEGIN … KEY`, …) over every tracked file except `aigate_lite.py`. Hits: `PROGRESS.md:419`
  (private-key header, BLOCK), `tests/test_secrets_deps.py:49` (item 2) and `tests/test_secrets_deps.py:18`
  (AWS's documented `…EXAMPLE` key; the tool skips it on purpose, original frozen test, left as is).
- The 8 `secrets.generic` warns are fixtures in test files (frozen originals, or Phase 1 tests that may
  change only for D2); they are warn-level, not literal key formats, and are left.
Change: PROGRESS.md iteration 10 now says "OpenSSH private-key header fixture" instead of quoting it.
Tests (tests/test_repo_hygiene.py, new, 3): tracked `*.md/*.toml/*.yml/*.yaml` copied into a temp repo,
`check --all --offline` → no `secrets.hardcoded` (TP; failed before with `[('PROGRESS.md', 419)]`); a
header built at run time and planted in a copied doc is found (control, so the TP cannot pass vacuously);
the copied set includes PROGRESS/README/CHANGELOG/pyproject and `examples/` (edge).
Verify: `python -m pytest -q` → `1 failed, 118 passed in 110.99s` (only the known Windows test);
pyflakes clean. Bench not re-run: `aigate_lite.py` unchanged by this item.
