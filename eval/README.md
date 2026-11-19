# The evaluation harness

A fixed yardstick for aigate-lite: a corpus of cases with frozen labels, a runner that sends each case through the real
CLI, a scoreboard, and a ratchet that makes any regression fail. Standard library only. No LLM, no telemetry.

```bash
py eval/run.py                  # everything (clones the pinned clean repositories on the first run), writes eval/scoreboard.json
py eval/run.py --check          # the same, and exit 1 if anything is worse than eval/ratchet.json
py eval/run.py --families real,seeded,adversarial --check --no-write     # what ci.yml runs (offline, ~3 minutes)
py eval/run.py --check --no-write --case clean-go-mux --case clean-go-chi   # what ci.yml also runs: two clean repositories, clone + recorded snapshot
py eval/run.py --case <id>      # one case, with every finding printed (for a person and for an agent)
py eval/run.py --failures       # list every failing key of the training set
py eval/run.py --update-ratchet # lock in a gain (refuses if a known case got a NEW failure)
```

## What runs

Every case runs twice, as a person and as an AI agent (`CLAUDECODE=1`), through
`aigate_lite.py check --deep --format json` in a throw-away git repository: the registry fixture
(`eval/registry_fixture.json`) instead of the network, the clock pinned to the fixtures' `2026-10-31`, private caches.
A case with commit messages also runs `review --message`. A clean repository is audited with `check --all --offline`.

## Case families

| Family | Where it comes from | Expectation |
|---|---|---|
| `real` | triaged findings of the two ATLAS audits (true and false positives), rewritten as minimal reproducers: **no ATLAS code**, each case names the audited `path:line` it came from | the label of that finding |
| `seeded` | a clean base project (Python, Go, TypeScript) plus ONE injected AI failure: hallucinated or typosquatted package, removed assertion, added skip, invented API symbol, wrong call arity, broken caller, removed error check, leaked provider key, weak crypto, claim mismatch | the injected failure is reported |
| `adversarial` | evasion (split or aliased secrets, over-long lines, huge files), look-alikes that are NOT findings (homoglyphs, zero-width characters, documented example keys), and robustness (huge or binary files, deep nesting, CRLF, non-UTF-8, symlinks, submodules, 10,000-file diffs, renames, a merge in progress, an empty repository, a detached HEAD) | the labelled finding, nothing at all for a look-alike, and never a crash, a traceback, a hang or invalid JSON |
| `clean` | popular, well-maintained open-source projects pinned by commit SHA and cloned into `eval/.cache` (not vendored) | a person sees **0 BLOCK** |

Two folders: `eval/corpus/` is the training set. `eval/holdout/` (a stable 20% by hash of the case id) is **never shown to
a fixer**: the runner prints only totals for it and writes the case list to the git-ignored `eval/.holdout_detail.json`.
A fix that helps the training set but hurts the holdout is rejected.

### What the holdout shows, and the refresh rule

The runner prints one more line for the holdout, and stores the same counts in `eval/scoreboard.json` as `holdout.by_rule`:

```
holdout by rule: secrets.hardcoded missed 6 · review.claim_mismatch missed 2 · tests.skip_added too strict 1
```

It groups the failing holdout keys (one key per case and per person or AI agent) by rule and by kind, and the counts add up to the
failure total printed on the line above. The kinds are `missed` (a labelled finding was not reported), `too strict` (a result
harsher than the label), `too lax` (weaker than the label), `false positive`, `block on a clean project`, `unexpected block`,
`crash`, `gate error` and `agent softer`. A failure that belongs to no rule (a crash, a verdict difference) is counted under
`(none)`. The line never names a case, a path, a language or a file: that stays in the git-ignored `eval/.holdout_detail.json`.
It tells a fixer which family of rules is behind, without the answers. Read it once per round and write it in `eval/LOG.md`;
do not run fixes in a loop against the holdout number.

The holdout refresh rule: every minor version adds at least 10 holdout cases. They are written by an agent session that has seen
neither the fixes nor the existing holdout, from the feature specs only. Each addition is logged in `eval/LOG.md` with what the
writer was given. Old holdout cases are never deleted and never moved to the training set.

## The scoreboard

`eval/scoreboard.json` holds the numbers the README accuracy section is generated from (`py scripts/gen_accuracy.py`): the version
of the tool, the case counts and the results of the training set (`train`), and the same for the holdout (`holdout`), where
`holdout.by_rule` is the per-rule count above (older files have no such key; the README section then leaves that line out).
It is produced on the reference runner (GitHub `ubuntu-latest`, Python 3.12) by a full `py eval/run.py` and committed by the
maintainer, who also runs `--update-ratchet`; a number from one laptop is not the published number.
`py scripts/release_check.py scoreboard` (a release run for a tag does it too, a pull request does not) fails when the file is for
another version than `__version__` or its case counts differ from the case folders on disk:

```
release_check: eval/scoreboard.json is for 0.6.0 with 247 training cases; the tree is 0.6.1 with 265.
  fix: py eval/run.py && py scripts/gen_accuracy.py
```

## `case.toml`

```toml
id = "seed-py-halluc-req-1"        # = the folder name
source = "seeded"                   # real | seeded | adversarial | clean
language = "python"
origin = "..."                      # repo + commit SHA, or "ATLAS_OS audit ..."
who = "both"                        # person | agent | both: whom the labels apply to (the invariants always apply to both)
mode = "staged"                     # staged | range | push | merge | detached | empty | clean
label_status = "frozen"             # real cases start as "proposed" until the human approves the triage sheet
messages = ["add tests for x"]      # commit messages, for review.claim_mismatch
contested = false                   # excluded from every metric and from the ratchet until the human decides

[[must_fire]]                       # the tool must report this
rule = "deps.hallucinated"
path = "requirements.txt"
line = 2                            # optional; `tol = N` allows N lines of difference
action = "block"                    # optional: the exact action for a person; agent_action for an agent
[[must_not_fire]]                   # a false-positive guard: no active finding with this rule on this path
rule = "secrets.hardcoded"
path = "app.py"
```

`before/` is committed as the base, `after/` is overlaid and staged. With `mode = "range"` the overlay is committed too and the gate
checks `HEAD~1..HEAD`; with `mode = "push"` it is committed the same way and the gate runs as the pre-push hook would for the first push of a
new branch to a remote that holds none of the history (`check --push`, fed `refs/heads/main <HEAD> refs/heads/main 000...0`). Extra keys: `delete = [...]`, `[[rename]] from/to`,
`[[symlink]] path/target`, `[[gitlink]] path/sha`, `[[transform]] path/kind` (`crlf mixed latin1 bom nul utf16`) and
`[[generate]] path/kind/size` (`long_line binary deep_json deep_python many_lines many_files big_text empty`): content
that is huge, binary or oddly encoded is made at run time and never committed.

**No provider-shaped secret and no invisible character is stored.** Write `@@AWS@@`, `@@GHTOKEN@@`, `@@STRIPE@@`,
`@@SLACK@@`, `@@SLACKKEY@@` (a Slack token with no documentation-placeholder run in it), `@@PEMBEGIN@@`/`@@PEMBODY@@`/`@@PEMEND@@`, `@@ZWSP@@`, `@@RLO@@`, `@@LRI@@`, `@@PDI@@`; `run.py` fills them in.
A key hidden behind a decoder call is written as the placeholder of the form it decodes (`@@AWSB64@@`, `@@AWSHEX@@`, `@@AWSREV@@`,
`@@AWSROT13@@`: the `@@AWS@@` key in base64, hex, reversed and rot13), and a key assembled from pieces as `@@AKIA@@`, `@@AKT1@@`, `@@AKT2@@`.
(The repository's own hygiene tests scan every tracked file, and `.aigate/lite.toml` ignores `eval/corpus` and
`eval/holdout` for the self-scan because the corpus is made of mistakes.)

## What is measured

Per rule: **TP** (reported, right action), **FP** (reported where a `must_not_fire` says no), **FN** (not reported),
**over** (reported with a stricter action than labelled), **under** (weaker), precision and recall.
Overall: false BLOCKs on `clean`, unexpected BLOCKs (a BLOCK not named by any label), crashes (traceback, no JSON, timeout,
exit other than 0/1), `gate.error` findings, runtime p50/p95, and **agent-softer** violations: an AI agent's result softer
than a person's for the same change (compared per finding, or by a stricter finding on the same path, and by verdict).

## The ratchet

`eval/ratchet.json` holds the known failures (one key per case, who and kind) and the best aggregate values. `--check` fails
on any failure that is not in it, on a higher holdout failure count, and (with `--perf`) on a p95 more than 25% slower.
`--update-ratchet` only writes gains (and keys of newly added cases); it refuses a new failure for a known case.

## Labels are frozen

Do not change a label to make a number go up. A label that looks wrong is proposed in `eval/LABEL_CHANGES.md`; the
human decides. `eval/TRIAGE_SHEET.md` lists every real case with its proposed label for approval.
`eval/tools/seeds/seed_v1.py` and `eval/tools/pin_clean.py` made corpus v1 and are kept for reproducibility only (never re-run
them over a corpus whose labels were changed on purpose).
