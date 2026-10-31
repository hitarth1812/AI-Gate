# Changelog

## Unreleased (Phase 1 → 1.0)

### Behaviour changes
<!-- Every changed default or text-output format goes here. -->
- New default: a per-file result cache (`results-v2.json` + `results.key`, both 0600, in the cache dir)
  remembers which files the pure checks found clean. `--no-cache` now turns off this cache as well as
  the registry cache.
- Vendored hooks pick the first interpreter that really runs Python 3.11+ among `python3`, `python`,
  `py -3` (one extra interpreter start per hook run), and stop with a message if none does.
- On a non-UTF-8 stdout (e.g. a cp1252 pipe on Windows) unencodable characters in text/github output are
  printed as `?` instead of crashing.

### Added
- `AIGATE_PROFILE=1` prints `aigate-profile: {"blobs_read": n, "git_calls": n}` to stderr on exit.
- `bench/bench.py`: deterministic benchmark (4 scenarios, 30 runs, median/p95, git-call and blob counters)
  with `--save` / `--compare` perf gates.

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
- `bench/bench.py` also reports `--no-cache` timings for scenarios A and C.

### Fixed
- Windows: `install --force` / `uninstall` no longer fail with WinError 32 (file renamed while open).
- Windows: vendored hooks no longer run the Microsoft Store `python3` stub.
- Windows: the text report no longer crashes with UnicodeEncodeError on a cp1252 pipe.
- Non-vendored hooks single-quote absolute paths (spaces, quotes, `$`).

## 0.1.0
- Initial release: 65 rules, staged / range / push checks, JSON schema 1.
