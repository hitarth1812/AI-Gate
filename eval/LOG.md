# Improvement loop log

Each round: the scoreboard before and after (commands shown), the cases fixed, rejected attempts, and what the human still has
to decide. Every number comes from a command that was run.

## Round 1

Baseline (`py eval/run.py` at `07013b9`, Windows 11, Python 3.12.10, jobs 4): training set false-BLOCK on clean 13, unexpected
BLOCK 0, crashes 0, gate.error 0, agent-softer 0, FN 18, FP 13, over 2, under 0. Holdout failures (counts only): 2 crash,
1 over, 1 FP, 4 FN. Ratchet: 46 known failing, holdout 8.

After (`py eval/run.py --update-ratchet --failures` at the final integrated commit, jobs 4): false-BLOCK on clean 13, unexpected
BLOCK 0, crashes 0, gate.error 0, agent-softer 0, **FN 14, FP 13, over 1**, under 0. Holdout failures: 1 over, 1 FP, 4 FN (crashes 0).
Ratchet: **41** known failing, holdout 6. Runtime p50 467.6 ms, p95 2295.2 ms (the p95 is the clean-repository audits; jobs 4).

| Metric | Before | After |
|---|---|---|
| Training FN / FP / over | 18 / 13 / 2 | **14** / 13 / **1** |
| Training false-BLOCK on clean | 13 | 13 (policy-gated, see below) |
| Holdout failures (counts) | crash 2, over 1, FP 1, FN 4 | over 1, FP 1, FN 4 |
| `security.weak_crypto` tp / fn / over | 19 / 2 / 1 | **22 / 0 / 0** (fp 1) |
| `review.claim_mismatch` recall | 0.75 | **0.875** |
| Ratchet known failing | 46 | **41** |
| Full test suite (py 3.12) | 1167 passed, 13 skipped (v0.5.1) **1281 passed, 13 skipped, 0 failed** (`py -m pytest -q`, 12 m 39 s) |

Accepted (each triaged by a haiku agent, fixed on its own branch by a sonnet agent with a failing test first, then verified by a fresh
read-only sonnet agent; all five verdicts "accept"):

| # | Change | Case(s) | Person / agent | Commit |
|---|---|---|---|---|
| 1 | `check --format json\|sarif\|github` with nothing staged printed nothing (exit 0): now a valid empty document (json: same keys; sarif: empty results; github: summary line). Text and agent output byte-identical | holdout crash (2 -> 0) | no change in findings | `a70a6fb` |
| 2 | `security.weak_crypto` sees secret words inside camelCase names (`sessionToken = Math.random()`, `sha1(userToken)`); `tokenizer`, `passwordless`, `secretary` stay quiet. A strict superset of the old `` match | `seed-ts-weak-math-random-token` (FN 2 -> 0) | detection gain, both | `88cb9d6` |
| 3 | `review.claim_mismatch` indexes JS/TS exported-function parameter count as a signature change (reviewer only, never the hook; `api.broken_caller` does not fire for TS) | `seed-ts-claim-refactor-signature` (FN 2 -> 0) | detection gain, both | `086f09b` |
| 4 | SHA-1/MD5 of a secret-word variable in a TEST file is WARN for a person even under a sensitive path (new `Finding.person_flat`, honoured at the escalation site for this rule only); agent unchanged (fix under `auth/`) | `real-go-sha1-in-test-file` (over 2 -> 1) | person only | `c0f981e` |
| 5 | The `next:` line no longer says secrets cannot be baselined for a non-secret BLOCK (`suppress.security`, `deps.*`, `scope.*`): kind-specific advice, an `[[allow]]` route and an `aigate-lite explain` pointer; secrets-only wording byte-identical | the human's open item (not a corpus case) | identical for both | `121cfbb` |

Rejected and reverted (not in the branch):
- **Go `t.Skip` whose message says the data is absent -> WARN for a person** (`real-go-skip-data-routes`). Rejected twice by independent
  verifiers. First version scanned every string literal on the line (`if name == "empty" { t.Skip() }` became WARN). The tightened
  version (message argument only, data noun, deferral words excluded) still turned `t.Skip("no time for results")`,
  `t.Skip("zero users, too slow")` and `t.Skip("empty results are not handled")` into WARN: a message heuristic cannot separate "the
  data is absent" from "I do not want to deal with it" without a list that would grow with every sentence. Softening a skip on
  words alone is exactly what the rules forbid ("an AI agent's change is never treated more softly" holds, but a person's
  unconditional skip must not pass on wording). The case stays an expected failure in the ratchet. A different mechanism is needed
  (for example the label could require the skip to sit under an `if`, as the JS path does); proposed for the human in
  `LABEL_CHANGES.md` section F. Also: the verifier brief itself contradicted the label (it demanded FIX for
  `t.Skipf("no routes registered for %s", name)` while the label wants WARN for `t.Skip("no registered routes to patch")`): my error,
  noted so it is not repeated.

Not attempted in round 1 (gated on the human's policy decisions in `LABEL_CHANGES.md` section A, because each softens a BLOCK):
the 13 false BLOCKs on clean repositories (committed private-key test fixtures, `suppress.coverage` under `auth/`), and the
justified `//nolint:gosec` question. The 13 `secrets.generic` false positives (the largest remaining group) are the natural target
of round 2; they soften WARN/FIX to quiet for a person only.

Findings and mistakes along the way (so nothing is silent):
- My own regression: literal zero-width characters in `eval/run.py` and its test made the repository's hidden-character test fail
  (found by the first fixer; fixed in `9490b36`). The self-scan also flagged my generator and the footer fixer's `# nosec` fixture
  lines once they were tracked; handled with an ignore for `eval/tools/` and three fingerprinted allow entries (`.aigate/lite.toml`).
- A fixer's `git stash pop` popped another fixer's stash (stashes are shared between worktrees); it noticed, saved it back and the
  verifier confirmed both commits are clean. Worktrees must not use `git stash`: added to the verifier/fixer instructions for round 2.
- Verifier notes kept for later: the TS arity can be miscounted by a bare `<` in a default value, by a TS `this:` pseudo-parameter and by
  overloads (rare, same for person and agent); a camelCase name on a line with `Math.random()` now fires like its snake_case twin
  (`Math.random() < sessionSampleRate`); an agent cannot complete the `[[allow]]` route the new next-line text names (its config
  is read from HEAD): a person has to add the entry.
- Triage slip: one triage agent ran `eval/run.py --case` on a holdout id (it printed totals only); it says it did not use the output.

## Round 2 (2026-11-14)

Scoreboard from `py eval/run.py --update-ratchet` (same command before and after; before = origin/main `cfb08d7`):

| | before | after |
|---|---|---|
| training cases (person and agent runs each) | 210 | 210 |
| false BLOCK on clean repositories | 7 | 0 |
| failures against labels (missed / false positive / strict / lax) | 10 / 13 / 0 / 0 | 0 / 0 / 0 / 0 |
| unexpected BLOCK, crashes, gate.error, agent softer | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| holdout cases | 42 | 57 (21 % of 267) |
| holdout failures | missed 4, false positive 1, too strict 1 | missed 8, too strict 1 |
| ratchet: known failing | 30 | 0 (holdout 9) |

The training failures are zero because the fixers worked on exactly those cases. The holdout is the honest number: it grew by
15 seeded cases nobody has fixed against, and 9 of its 57 fail (details in `eval/.holdout_detail.json`, not for fixers).

What changed, in order: A6 as a person-only `sensitive_exclude`; clean-repo key cases scored with an allow entry (A5); dated registry
snapshots for the 13 clean repositories (decision C); the agent is no longer shown the `[[allow]]` route; `gate.error` blocks for an
agent; `schema_version`; the 13 `secrets.generic`/`weak_crypto` person-only softenings; split/joined provider keys in Python, an
aliased skip, `eval` through builtins; and three `deps.hallucinated` false BLOCKs that only the snapshots made visible
(`annotationlib` is Python 3.14 stdlib, a `from "Referer"` inside a JS test title, modules a test writes to a temp dir and imports).

Rejected or revised attempts (so nothing is silent):
- Round 1 Go data-skip fixer: rejected twice (a message heuristic over-softens); dropped, see LABEL_CHANGES F.
- FP fixer first attempt (`e97ac3c`) rejected by its verifier (path-id and concatenation softening applied to non-test source); the retry
  gates both on test files. The verifier then found one remaining overreach (a mock-file `Math.random` under `auth/` went fix to pass for
  a person); narrowed in `24e85ff`: a sensitive path keeps its floor.
- Evasion fixer first attempt rejected by its verifier (quadratic time on a long one-line list with a gate line: 105 s for 60,000
  constants); fixed (0.96 s) in the next commit.
- Deps fixer: verifier accepted, and found a narrow hole (a made-up package on the same line as misread JSX text passed); closed in
  a further commit. Remaining, known: the key fold is Python only (JavaScript, TypeScript and Go are not folded); an agent can excuse a
  made-up import in a test file by creating the module in the same function (by design, test files only).
- My own mistakes: the self-scan failed after integration (a docstring example and a test fixture spelled a literal); fixed in `7dcb67b`.
  The first full eval after integration showed the three clean-repo false BLOCKs above instead of hiding them behind an update of the ratchet
  (the ratchet refused, as designed).
- Fixers had to rewrite 18 test files' fake-key construction (`"AKIA" + "..."` became a call) because the new fold sees the literal; no
  assertion changed (verifier read every file).
