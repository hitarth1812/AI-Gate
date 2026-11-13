# Improvement loop log

Each round: the scoreboard before and after (commands shown), the cases fixed, rejected attempts, and what the human still has
to decide. Every number comes from a command that was run.

## Round 1 (in progress)

Baseline: `py eval/run.py` at `07013b9` (Windows 11, Python 3.12.10): training set false-BLOCK on clean 13, unexpected BLOCK 0,
crashes 0, gate.error 0, agent-softer 0, FN 18, FP 13, over 2, under 0; holdout failures: 2 crash, 1 over, 1 FP, 4 FN (counts only).

Decisions applied: the human's "GO" after the Part 1 report gave no corrections, so the proposed real-case labels in
`TRIAGE_SHEET.md` were treated as approved. Policy items in `LABEL_CHANGES.md` section A were NOT decided, so nothing that
softens a BLOCK was touched (this keeps the 13 false BLOCKs on clean repositories out of round 1).

Round-1 targets (ranked crash > seeded FN > false FIX), each triaged then fixed on its own branch `eval/r1-*`:
1. nothing staged: `--format json|sarif|github` prints nothing (crash class; holdout).
2. `security.weak_crypto` misses camelCase names (`sessionToken = Math.random()`).
3. TypeScript signature changes are not indexed for `review.claim_mismatch`.
4. SHA-1 in a `_test.go` file under `auth/` is FIX for a person (human's open item): WARN for a person, agent unchanged.
5. A Go `t.Skip` whose message says the data is absent is FIX for a person: WARN + hint, agent unchanged.
6. The `next:` line says secrets cannot be baselined for a `suppress.security` BLOCK (human's open item).

Not changed in round 1, with the reason:
- `seed-ts-claim-fix-other`: the claim "fix total rounding" uses a bare lowercase word, which the tool deliberately does not treat
  as a symbol (precision case `cm-ok-plain-word`); the label is doubtful, proposed for change in `LABEL_CHANGES.md` (section E).
- The SQL-in-test inconsistency: by design (sensitive-path escalation), see `LABEL_CHANGES.md` A2.
- `real-go-sha1-in-test-file` label rationale: the case hashes a `password` variable, not a fixed input; the label stands.

Process notes: the project's `.claude/agents` definitions are only discovered at session start, so this session ran the same three
roles as general-purpose agents (triage: haiku; fixers: sonnet) that read the definition files. Worktree isolation by the tool
failed on Windows, so worktrees were created by hand under `D:\aigate-lite-wt` (short path: one corpus case has a 60-level path).
One triage agent ran `eval/run.py --case` on a holdout id (totals only; it says it did not use it). Fixers ran targeted tests and
the offline eval check; the full suite runs once on the integrated result.
