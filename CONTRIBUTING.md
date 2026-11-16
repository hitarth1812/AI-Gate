# Contributing to aigate-lite

aigate-lite is one Python file (`aigate_lite.py`), standard library only, Python 3.11 to 3.14, vendorable. Keep it that way:
no runtime dependency, no network use beyond the PyPI, npm, Go proxy and OSV lookups it already does, no telemetry.

## Set up and run the checks

```bash
python -m pip install -e .[dev]                   # pytest, pyflakes, hypothesis (pinned); the tool itself needs nothing
                                                  # (CI installs the same pins from requirements-dev.txt)
python -m pyflakes aigate_lite.py
python -m pytest -q                               # about 25 minutes; registry calls use tests/registry_fixture.json
python eval/run.py --check --families real,seeded,adversarial     # the accuracy yardstick, offline, a few minutes
```

On Windows use `py` in place of `python`.

**Run the suite on Linux (WSL2) as well as on Windows before you push.** CI runs Ubuntu, macOS and Windows on Python 3.11-3.14,
and a test that only passes on one of them is a bug in the test. The usual cause is line endings: `Path.write_text` writes
CRLF on Windows and LF elsewhere, so a staged change can look different from a committed one. In tests write files with
`repo.write(...)` (LF and UTF-8 everywhere) or `write_text(..., encoding="utf-8", newline="\n")`, and make a test that checks a
finding on an *added* line add that line in the staged change, not in the commit before it. A test that is about CRLF writes
the bytes itself. Pull requests also set `GITHUB_BASE_REF`; a test about its absence must unset it.

Performance gates (on the same machine as the baseline):

```bash
python3 bench/bench.py --compare bench/baseline.json   # perf gates (same machine as the baseline)
python3 bench/bench.py --compare bench/baseline.json --markdown docs/PERF.md   # regenerate docs/PERF.md
```

AI coding agents: read [AGENTS.md](AGENTS.md) as well. Everyone: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Rules for a change

1. **A failing test first.** Every fix and every new check starts with a test that fails for the stated reason, then the
   smallest change that makes it pass. Never weaken, skip or delete a test to make a number go up.
2. **An AI agent is never treated more softly than a person.** A softening (for example a false-positive fix) applies to a person's
   change only and has a test that the agent's result is unchanged.
3. **Nothing that blocks today may pass** unless a labelled false positive proves it wrong.
4. **No fabricated numbers.** Every number in a doc or a pull request comes from a command you ran; paste the command.
5. Keep the contract in [docs/CONTRACT.md](docs/CONTRACT.md): exit codes, JSON keys and rule ids are stable.
6. Add a CHANGELOG entry (under the next version's "Changed", "Fixed" or "Added") for anything a user can see.

## The evaluation harness

`eval/` holds a fixed corpus (real, seeded, adversarial and clean cases) with frozen labels, a runner and a ratchet; see
[eval/README.md](eval/README.md). A pull request must not add a failure the ratchet does not know. Corpus labels are changed only by
the maintainer (propose a change in `eval/LABEL_CHANGES.md`). Do not read `eval/holdout/` when you work on a fix: it is the held-out set.

## Reporting a false positive or a missed detection

Use the issue template, and read [docs/FALSE_POSITIVES.md](docs/FALSE_POSITIVES.md): a good report becomes a corpus case.

## Security issues

Do not open a public issue for a vulnerability; see [SECURITY.md](SECURITY.md).

## Commit style

Short imperative subject (`fix(rule): what`), a body that says why, and no unrelated changes in the same commit.

## Releases (maintainers)

How a release is published (PyPI Trusted Publishing, no token) is in [docs/dev/RELEASING.md](docs/dev/RELEASING.md).
