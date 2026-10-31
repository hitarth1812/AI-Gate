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
