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
- Bytecode reuse for Windows hooks (POSIX hooks have it since the Phase 1 cleanup; ~50 ms per hook run
  measured on Windows too). Blocked on proving the cache directory is private: needs the owner and DACL of
  `%LOCALAPPDATA%/aigate-lite/pycache` (ctypes `GetNamedSecurityInfoW`, or an approved assumption that
  `%LOCALAPPDATA%` is per-user).
- `js_project` and `python_declared` still list every file (`git ls-files`) to find package.json /
  tsconfig / Python manifests. A pathspec listing (`:(glob,icase)**/package.json` …) would make JS
  commits and third-party Python imports independent of repo size too.
- Range/push mode runs `git diff` twice per pushed commit for the secrets scan; one `git log -p --raw`
  stream could replace them.
- Trust model follow-ups: trust store in the common git dir so linked worktrees share approvals; optional
  y/N prompt on a TTY.
- Pin the vendored `.aigate/aigate_lite.py` by hash at install time so a pulled change to the gate itself
  needs approval too.
- ~~A deeply nested JSON file in a change makes `check_files` raise RecursionError (reported as gate.error,
  so the commit is blocked); parse it with a depth guard instead (found by the Phase 2 item 4 review).~~
  Done: RecursionError is caught where JSON is parsed (`test_deeply_nested_json_is_not_a_gate_error`).
- Baseline: optionally salt fingerprints of `--include-secrets` entries so weak values cannot be guessed from
  `.aigate/baseline.json` (today the value is hashed without a salt; opt-in only).
