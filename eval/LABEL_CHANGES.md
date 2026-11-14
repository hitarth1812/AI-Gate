# Proposed label changes and open decisions (for the human)

Corpus labels are frozen: nobody applies a change here except you. Each item names the case, the question, what the
tool does today, and what the engineer recommends. Contested cases are excluded from every metric until decided.

## A. Policy questions the harness surfaced (decide; they change what the tool does)

1. **A justified `//nolint:gosec` is BLOCK today.** Case `real-go-hibp-sha1-nolint-reason` (CONTESTED, so not scored).
   Today: `suppress.security` BLOCK for a person and an agent, plus `security.weak_crypto` FIX, for
   `sha1.Sum(...) //nolint:gosec // SHA-1 is the breach-check protocol, not a security control here`.
   You asked to propose, not decide: **recommend FIX for a person when the reason names what makes the use safe, BLOCK when
   the directive is bare, an agent unchanged (BLOCK)**. That softens a block for people only, so it needs your yes.
   `real-go-gosec-bare` already pins "bare is BLOCK for everyone".
2. **The same test-file SQL pattern was FIX in one file and WARN in others.** Cause found: paths under `auth/` are
   `[paths] sensitive` and escalate one step *by design* (warn -> fix for a person, fix -> block for an agent). The labels
   of `real-go-sql-delete-table` follow that design (person fix, agent block). Question: should a `_test.go` file be exempt
   from sensitive-path escalation? Recommend **no change**: a test that builds SQL in a security-sensitive package is
   worth a second look, and the docs already say so.
3. **`security.weak_crypto` on SHA-1 in a `_test.go` file** (`real-go-sha1-in-test-file`): FIX today, labelled WARN for a
   person (your item), agent unchanged (FIX).
4. **Footer wording**: "secrets cannot be baselined" is printed for a `suppress.security` BLOCK, where it is wrong (the
   finding is not a secret; it can be allowed with a reason). Not a corpus case (a message, not a finding); it goes in
   round 1 as a text fix with its own test.
5. **Private keys committed as test fixtures** (a certificate-authority test key under `tests/certs/`, `testdata/key.pem`):
   a person's change gets `secrets.hardcoded` BLOCK and `secrets.env_file` BLOCK. Three of the ten clean repositories do this
   (`requests`, `axios`, `chi`; see the scoreboard), so the clean-repository expectation "0 BLOCK for a person" fails there. Options: (a) keep BLOCK
   (a real private key in a repo is a real risk) and exclude those cases from the clean set; (b) downgrade to FIX for a
   person when the path is clearly test data (`testdata/`, `tests/certs/`, `**/fixtures/**`) and the key is not
   referenced from non-test code. **Recommend (b)** for people only, agents unchanged. This is a softening of a block, so it
   needs your decision before any fix.
6. **`suppress.coverage` BLOCK on a clean repository** (`/* istanbul ignore next */` in `examples/auth/index.js` of
   express): `suppress.coverage` is FIX by default and the path contains `auth/`, which is `[paths] sensitive`, so it is
   escalated to BLOCK for a person. Same question as item 2; recommend the same answer (no change to escalation), and
   to keep this case failing as a visible reminder, or to drop `examples/**` from the sensitive globs in your own config.

## B. Cases where the engineer's first label was wrong and was corrected BEFORE the labels froze

These were found when the first scoreboard was triaged; they are listed so nothing was changed silently.

| Case(s) | First label | Corrected to | Why |
|---|---|---|---|
| every `api.*` and `code.error_check_removed` label | person FIX | person WARN, agent FIX | the README documents "warn / fix" for these reviewer rules; the first labels ignored it |
| `adv-nolint-uppercase` | must fire `suppress.lint` | must not fire | golangci-lint directives are case-sensitive: `//NOLINT` is not a directive |
| `adv-toml-config-garbage` | who both | who person (+ a new agent case) | an agent's config is read from HEAD, so a staged garbage config is a forbidden-path BLOCK for it, not a `gate.config_invalid` |
| `adv-config-allow-everything` | missing a label | + `scope.forbidden_path` BLOCK for the agent | the unlabelled BLOCK was legitimate |
| `seed-py-leak-pem` | missing a label | + `secrets.env_file` BLOCK | a `.pem` private key is also a key file |
| `seed-py-assert-removed-2` | left an empty test body (a syntax error) | one assertion removed from each test | the mutation was malformed |
| Python `tests.skip_added` seeds | exact line | line within 2 | the tool reports the test (decorator and `def` are adjacent) |
| `real-go-test-*` literals, `real-ts-e2e-password-const` | agent FIX | agent WARN | a credential-like literal in a test file is WARN for everyone today (`soft` in `secret_findings`) |
| `real-go-sql-delete-table` | WARN / FIX | FIX / BLOCK | sensitive-path escalation (item A2) |
| `real-go-vuln-indirect-*` | WARN / WARN | WARN / FIX | an agent keeps FIX for an indirect advisory |

## C. Harness bugs found and fixed while building it (not label changes)

- Clean repositories were audited with the registry fixture (every real package looked hallucinated): fixture off for them.
- Two parallel runs of one clean case raced on `git clone`, leaving a half-made folder that made git resolve to the
  *parent* repository: clones are now serial, and a folder is trusted only if it is its own work tree.
- The agent-softer check compared rule ids only; an agent's edit of `.aigate/` is a forbidden-path BLOCK where a person's
  is a `gate.config_changed` WARN. A stricter finding on the same path now counts as not softer.
- A symlink case was staged and then un-staged by `git add -A`: links are added after it.

## D. Tool defects the harness already found (round 1 candidates; each gets a failing test first)

- `check --format json` (and `--format sarif`) with **nothing staged prints nothing** and exits 0: a machine reader gets an
  empty stdout (`adv-nothing-staged-json`, holdout).
- Secrets on a line longer than 4,000 characters, and in a file larger than 1 MB, are never scanned
  (`adv-key-on-long-line`, `adv-key-in-big-file`).
- `Math.random()` assigned to a camelCase name (`sessionToken`) is not `security.weak_crypto`: the word list needs a word
  boundary and camelCase has none (`seed-ts-weak-math-random-token`).
- `review.claim_mismatch` does not fire for TypeScript changes (Python and Go do).

## E. Doubtful seeded labels found by triage (proposals; not applied)

- `seed-ts-claim-fix-other`: commit message "fix total rounding" while the diff touches only `src/api.ts`. The tool does not
  report it because a bare lowercase word (`total`) is not treated as a symbol (existing precision case `cm-ok-plain-word`
  pins that on purpose; the Python and Go twins pass only because their messages name `compute_total` / `Total`).
  Proposal: change the commit message of the case to `fix the total() rounding` (a symbol-shaped token) so it tests the
  language gate and not the word heuristic. Until then it stays an expected FN (excluded from nothing; it is in the ratchet).

## F. Round 1: the Go data-gated skip (proposal)

`real-go-skip-data-routes` (and the JS/TS data-gated skips, which already work) rely on a skip being CONDITIONAL. In Go the
data-gated skip is an unconditional `t.Skip("no registered routes to patch")` after an `if`; the diff added only the call. Two
independent verifiers rejected a message-text heuristic because it softens unconditional skips written with similar words.
Options: (a) keep today's FIX for a person and change the label to FIX (proposal: the skip is not provably conditional); (b) only
soften when the `t.Skip` is the body of an `if` whose condition reads a length/count/emptiness (the JS rule), which needs the Go
lexer's block structure. Recommend (a) now, (b) as a later round. Needs your decision before the label changes.

## G. Decisions applied (human, 2026-11-14), with status

| Item | Decision | Status |
|---|---|---|
| A1 | justified `//nolint:gosec` (reason >= 4 words, not a bare false positive / ok / ignore / intentional) is FIX for a person; agent BLOCK | applied: `_refine_suppression` + `person_flat`; tests/test_nolint_reason.py; case real-go-hibp-sha1-nolint-reason un-contested |
| A2 | no change to sensitive-path escalation | nothing to apply |
| A3 | SHA-1 in a `_test.go` is WARN for a person, FIX for an agent | applied in round 1 (commit c0f981e) |
| A4 | footer wording fixed | applied in round 1 (commit 121cfbb) |
| A5 | keep BLOCK for private keys; BLOCK prints a ready `[[allow]]` snippet; allow works for secrets.hardcoded; one key file gives one BLOCK | applied: `_one_block_per_key_file`, snippet hint, tests/test_key_file_block.py; seed-py-leak-pem relabelled (env_file must_not_fire); tests/test_secrets_deps.py expectation changed to match (one BLOCK, no env_file); round 2: clean-repo key cases are scored with the allow entry listed in their case.toml |
| A6 | remove `examples/**` and `docs/**` from the default `[paths] sensitive` | applied in round 2 as a person-only `[paths] sensitive_exclude` (default `examples/**`, `docs/**`); the agent is still escalated; tests/test_sensitive_exclude.py |
| B | pre-freeze corrections accepted; each must cite README or docs/RULES.md, not tool output | accepted; citations to be added to section B in the next pass (the api.* and error_check rows already match the README table) |
| C | dated registry snapshot per clean repo; nightly uses the snapshot | applied in round 2: `eval/tools/snapshot_registry.py`, `eval/snapshots/<id>.json` (dated 2026-11-14), replayed by `eval/run.py`. The snapshots made `deps.hallucinated` run on the clean repos and found three real false BLOCKs (see LOG.md, round 2) |
| D | order: long-line secrets, empty JSON/SARIF, camelCase Math.random, TS claim_mismatch | long-line: applied (`provider_findings`, tests/test_secret_coverage.py). The big-file item was dropped: a key in a 1.1, 2.1 or 5 MB file is already found (verified), so there is no gap. Empty JSON, camelCase and TS were applied in round 1 |
| E | change `seed-ts-claim-fix-other`'s message to `fix the total() rounding` | applied (case message and generator) |
| F | keep FIX for a person (label changed to FIX) | applied to real-go-skip-data-routes (person and generator) |

Note on A1 and the verifier brief: the verifier brief for a future round must confirm that every A-item change is done
through the decided wording, not a looser one.
