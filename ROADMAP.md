# Roadmap

Phase 1 (performance, safety and CI) is tracked in PROGRESS.md. Later phases each start in a new
session after a human review of the previous one. Do not start them early.

## Phase 2 — Usability
- `doctor` command (interpreter, hooks, config, cache, network reachability).
- `init` presets (relaxed / balanced / strict) with language detection.
- Baseline file for legacy repos (known findings recorded once, new ones still fail).
- Compact grouped text output (documented behaviour change).
- Errors that state cause + remedy.
- `fix --yes` for mechanical fixes: always shows the diff, never runs from hooks.

## Phase 3 — Languages
- Tier D registries, each verified live first: Go → Rust → Java/Kotlin (Maven + Gradle catalogs) →
  Ruby → PHP → .NET → Dart → Elixir.
- Swift / vcpkg / Conan: source/URL checks only.
- JS/TS tokenizer to replace the line regexes.
- Structure checks for other languages only through an optional `[deep]` extra (tree-sitter), same rule IDs.

## Phase 4 — Accuracy & hardening
- Corpus: own repos + 3–5 large OSS repos pinned by commit; generated docs/ACCURACY.md with false
  findings per 1,000 lines per rule.
- Fuzz the diff parser and tokenizers.
- Install tests: pipx / uv tool / pre-commit / single-file on Linux, macOS, Windows × Python 3.11–3.14.

## Ideas noted during Phase 1 (not done)
<!-- appended by the Phase 1 loop -->
- Hooks run `python .aigate/aigate_lite.py`, which recompiles 190 KB of source every time (77 ms measured
  on the Phase 1 machine). A hook that imports the module with `sys.pycache_prefix` pointed at the cache dir
  would reuse bytecode. Needs care: writable cache dir, stale-pyc safety, Windows paths.
