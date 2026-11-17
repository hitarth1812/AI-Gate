# Instructions for AI coding agents working on aigate-lite

This file is for an AI agent (Claude Code, Codex, Cursor, Copilot, ...) that changes this repository. People: read
[CONTRIBUTING.md](CONTRIBUTING.md); the rules are the same.

## What the project is

- One file, `aigate_lite.py`, at the repository root. It is the whole tool: standard library only, Python 3.11 to 3.14, and it
  must stay vendorable as a single file. Do not split it into a package and do not add a second module.
- **No runtime dependencies.** `dependencies = []` in `pyproject.toml` stays empty. Development tools are pinned in
  `requirements-dev.txt` and mirrored as the `dev` extra (`pip install -e .[dev]`).
- **No telemetry.** The only network use is the documented public lookups (PyPI, npm, the Go module proxy, OSV and the
  download counters). Do not add another endpoint, an analytics call or anything that sends data from the user's machine.

## How to run the checks

On Windows use `py` in place of `python3`.

```bash
python3 -m pip install -e .[dev]                  # or: python3 -m pip install -r requirements-dev.txt
python3 -m pyflakes aigate_lite.py
python3 -m pytest -q -n auto -m "not serial"      # in parallel; 5.4 to 6.3 minutes on a 20-thread Windows machine (4 runs)
python3 -m pytest -q -m serial                    # the timing-sensitive tests, one at a time; 0.4 to 1.1 minutes
python3 eval/run.py --check --no-write --families real,seeded,adversarial     # the accuracy yardstick, offline
python3 scripts/gen_docs.py --check               # docs/RULES.md matches the rule table
python3 scripts/gen_accuracy.py --check           # the README accuracy section matches eval/scoreboard.json
AIGATE_PROVENANCE=human python3 aigate_lite.py check --all --offline -q   # the gate on its own repository
```

- **Run the suite on Linux (WSL2) as well as on Windows before the maintainer pushes; CI runs Ubuntu, macOS and Windows.** A test
  that passes on one system only is a bug in the test. Write test files with `repo.write(...)` or
  `write_text(..., encoding="utf-8", newline="\n")` (Windows would write CRLF), and make a finding's line part of the *staged*
  change. See CONTRIBUTING.md.
- The two pytest commands above are what CI runs (registry calls use `tests/registry_fixture.json`). A plain
  `python3 -m pytest -q` runs everything in one process and took 31 minutes 17 seconds on the same machine; use it to
  debug an order- or parallel-only failure. A test that asserts a wall-clock limit gets `@pytest.mark.serial` with a
  one-line reason; mark nothing else serial (isolate its temporary directories instead). `pytest-xdist` is a development
  tool only, pinned in `requirements-dev.txt`.
- Delete `aigate_lite.egg-info/` (left by a build) before running pytest: the suite refuses to start next to it.
- The tests clear the agent variables (`CLAUDECODE`, `AI_AGENT`, ...) for the commands they run. A tool you start yourself
  from an agent shell sees them and behaves as for an agent; that is expected.
- A test fixture that looks like a real secret (a provider key, a private-key header) must be built at run time
  (`"AKIA" + "..."`), or the repository's own self-scan fails.

## Rules

1. **Never weaken, skip or delete a test, and never change an evaluation label** to make a number go up. A fix starts with a
   test that fails for the stated reason. Label changes are proposals in `eval/LABEL_CHANGES.md`, decided by the maintainer;
   `eval/ratchet.json` changes only through `eval/run.py --update-ratchet`.
2. **Do not open `eval/holdout/`** when working on a fix: it is the held-out set the accuracy claims are measured on.
3. **An AI agent is never treated more softly than a person.** A softening applies to a person's change only, with a test
   that the agent's result is unchanged. The evaluation reports this as `agent-softer`, which must stay 0.
4. **Never push, tag, publish or merge,** and do not commit unless you were asked to. Never use `git commit --no-verify`.
   Releases are done by the maintainer (`docs/dev/RELEASING.md`).
5. **Do not edit `.aigate/`** (the gate's own config and allow list) to make a finding go away; ask instead. The gate
   blocks an agent's commit that touches it.
6. No fabricated numbers: every number in a document or a pull request comes from a command that was run.
