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
