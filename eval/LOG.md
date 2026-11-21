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

## Round 3 (2026-11-15)

Same command before and after: `py eval/run.py` (before = the tree with the 37 new training and 10 new holdout cases and none of the round-3 fixes, `b908095`).

| | before | after |
|---|---|---|
| training cases | 247 (210 + 37 new) | 247 |
| training failures (keys, person and agent) | 34 (17 cases, all missed detections) | 2 (1 case: `r3-properties-key`, see LABEL_CHANGES H) |
| false BLOCK on clean, unexpected BLOCK, crashes, gate.error, agent softer | 0 / 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 / 0 |
| holdout cases | 67 (57 + 10 new) | 67 |
| holdout failure keys | 15 (missed 14, too strict 1) | 11 (missed 10, too strict 1) |
| holdout, original 57 | 9 keys | 9 keys (no movement) |
| holdout, the 10 new cases (written without sight of the fixes) | 6 keys (3 cases) | 2 keys (1 case) |
| holdout cases passing | not recorded (the baseline run did not store the failing-case count) | 61 of 67 |

What the numbers say: the fixes transfer to cases written independently from the same README rules (6 keys down to 2), but the 9 failing
keys of the original holdout (per-rule counts: `secrets.hardcoded` 6, `review.claim_mismatch` 2, `tests.skip_added` 1 too strict)
did not move at all, so the training cases written for those families did not cover whatever shapes the holdout uses. The
training zeros are not evidence of generality. Per decision, nobody (fixers included) saw the holdout cases; only these counts.

What changed: key folding for JS/TS/Go; a multi-word `secrets.generic` rule; an agent WARN for the test-writes-the-module excuse;
four claim checks in `review.claim_mismatch`; SECURITY.md; the README accuracy section leads with the holdout.

Rejected or revised attempts:
- Claim checks: three verifications. The first fixer commit was rejected for 22 false warnings on true messages; the narrowing was
  rejected again (ordinary parametrize rows, class members, error idioms); the last narrowing (fire only on unambiguous evidence)
  was accepted with about 200 probes and one more list of nouns added afterwards. Cost: the checks now miss many false claims.
- Multi-word rule: first version matched a secret word anywhere in a name (9 false positives in jinja's lexer); the secret word must
  now end the name. A later narrowing silenced keyword arguments and `DEFAULT_PASSWORD`, which hides real leaks; undone. A
  `testing/` directory exclusion was removed after the verifier noted production packages can be called that.
- Fold fixer: its properties test spelled a real-shaped token that the fold itself caught (self-scan failure); rebuilt at run time.
- The `.properties` case: the case's `@@SLACK@@` token contains `12345678` and `AbCdEfGh`, which the documented-placeholder rule
  discards in every language, so the label cannot be met without changing that rule. Left failing and proposed in LABEL_CHANGES H.
- Holdout writer was given the multi-word idea (the spec), not any code or tool output.

## First nightly run (2026-11-15): every leg failed, found and fixed

The first scheduled `eval-nightly` run failed on all six legs (3 operating systems x Python 3.11 and 3.14) with 15 false BLOCKs on
`clean-py-click` and `clean-py-requests`: `import setuptools` read as "no package 'setuptools' on PyPI".

Cause: the registry snapshots were recorded on a machine that already had `setuptools` installed, so the tool never looked it up
(an installed package is not looked up) and the snapshot did not hold it. A runner without `setuptools` then replayed a snapshot in which
the name "did not exist". The pull-request CI never saw this: its eval step runs only real, seeded and adversarial cases, never the
clean repositories. It also passed locally on Python 3.12 for the same reason (the same installed packages), and failed on
Python 3.14 (no `setuptools`).

Fix: the harness runs the tool under `python -S` (both when recording a snapshot and when replaying it), so the packages installed on
the machine are never visible and the answer does not depend on the machine; the five Python snapshots were recorded again
(click 55 -> 56 packages, requests 10 -> 12, attrs 22 -> 23; flask and jinja unchanged in size). Two tests pin it
(`tests/test_eval_harness.py`). Result: `py eval/run.py --check --no-write` passes on 3.11, 3.12 and 3.14 from a clean export with an
empty clone cache (training failure keys 2, as in the ratchet; holdout 11, as in the ratchet).

## Harness round (2026-11-17): a scoreboard that cannot be stale, and a holdout line that points

This round changes the harness, the scripts, CI and one generated case. It does not change the tool (`aigate_lite.py` is byte for
byte as before), so the holdout was not expected to move and was not tuned on.

Same command before and after: `py eval/run.py --check --no-write --families real,seeded,adversarial --failures` (Windows 11,
Python 3.12.10, jobs 4; before = the tree at `61ad6a0`, after = this change).

| | before | after |
|---|---|---|
| training cases (real, seeded, adversarial) | 255 (61 + 125 + 69) | 255 |
| training failures (keys, person and agent) | 2 (1 case: `r3-properties-key`, both `FN:secrets.hardcoded@src/main/resources/application.properties:2`) | 0 |
| false BLOCK on clean, unexpected BLOCK, crashes, gate.error, agent softer | 0 / 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 / 0 |
| holdout cases (these three families) | 64 | 64 |
| holdout failure keys | 11 (missed 10, too strict 1) | 11 (missed 10, too strict 1): no movement |
| `eval --check` | ok | ok |

One more full run, `py eval/run.py --check` with all four families (the clean repositories included, a fresh scoreboard written to a
scratch path, not over the committed one): 265 training cases (adversarial 69, clean 10, real 61, seeded 125), no training failure,
holdout 67 cases with the same 11 failure keys, `eval --check` ok. The committed `eval/scoreboard.json` and `eval/ratchet.json` were
not touched: the maintainer commits the scoreboard made on the reference runner (GitHub `ubuntu-latest`, Python 3.12) and runs
`--update-ratchet`.

The new line, as printed:

```
holdout by rule: secrets.hardcoded missed 8 · review.claim_mismatch missed 2 · tests.skip_added too strict 1
```

It adds up to the 11 failure keys. Round 3 recorded 9 keys for the original 57 holdout cases (`secrets.hardcoded` 6,
`review.claim_mismatch` 2, `tests.skip_added` 1 too strict) and 2 more in the 10 cases added that round; with the original 9 unchanged,
the line now says those 2 are `secrets.hardcoded` too.

What changed:
- `eval/run.py` prints `holdout by rule` and stores the same counts as `holdout.by_rule` in the scoreboard (rule and kind and a count;
  never a case id, a path or a language). A test checks the printed line and the stored counts against the known case-id prefixes.
- `scripts/release_check.py` fails a tag run when `eval/scoreboard.json` is for another version than `__version__` or its case counts
  differ from the case folders; `release_check.py scoreboard` runs the same check by hand. It is not in a pull-request workflow. It
  is red against today's committed scoreboard (for 0.6.0 with 247 training cases, the tree is 0.6.1 with 265) until the maintainer
  commits a scoreboard made on the reference runner.
- `scripts/gen_accuracy.py` shows the holdout failures by rule when the scoreboard has them, and works unchanged when it does not.
- `.github/workflows/ci.yml`: the evaluation job also runs the two smallest pinned clean repositories (`clean-go-mux`,
  `clean-go-chi`) from their recorded snapshots, so a snapshot problem shows in the pull request, not in the nightly run.
  Locally this took 1.7 s from an empty clone cache.
- `r3-properties-key`: decided by the maintainer, option (a) of LABEL_CHANGES H: the generator writes a Slack token without a
  documentation-placeholder run (`@@SLACKKEY@@`) in this one case. The label is unchanged; the rule in the tool is unchanged. The ratchet
  still lists its two old failure keys until the maintainer runs `--update-ratchet`.
- The holdout refresh rule (at least 10 new holdout cases per minor version, written by a session that has seen neither the fixes nor
  the holdout, logged here with the writer's inputs; old holdout cases never deleted or moved) is in `eval/README.md`.

Rejected or revised attempts: none. Not changed: any label, any test expectation, `.aigate/`, `eval/ratchet.json`.

## Round 5 (2026-11-19): keys behind a decoder call or assembled from pieces

The change: `secrets.hardcoded` decodes a string literal that is the argument of a known decoder call and runs only the provider
patterns on the result (never `secrets.generic`; the boundary and documented-example rules still apply). Known calls: Python
`base64.b64decode`, `urlsafe_b64decode`, `standard_b64decode`, `decodebytes`, `binascii.unhexlify`, `a2b_hex`, `a2b_base64`,
`bytes.fromhex`, `codecs.decode(x, "rot13" or "hex" or "base64")` (with the codec aliases), a `[::-1]` or `"".join(reversed(x))` of a
literal; JS/TS `atob` (also `window.`, `globalThis.`, `self.`, `global.`), `Buffer.from(x, "base64", "base64url" or "hex")` in any letter case,
`.split("").reverse().join("")`; Go `base64.StdEncoding`, `URLEncoding`, `RawStdEncoding`, `RawURLEncoding` and `hex.DecodeString`.
At most two decoders one after the other and at most 4,096 characters per literal; a longer literal is not decoded and is counted in
a `decode.skipped` note shown with `-v`. A list or string that starts as literals, is extended with literals only (`push`, `append`,
`extend`, `+=`) and is joined, with every statement at most 25 lines from the first, is folded too. The finding names the step
("base64-decoded from a literal", "reversed from a literal", "assembled from literals") and its fingerprint is that of the decoded
value. A person and an agent get the same BLOCK. The decoder table (`DECODER_CALLS`) is data: an empty table restores 0.6.1.

Names: a decoder is also known by a name the same file imports it under (`from base64 import b64decode as d`, `import base64 as b`,
`from codecs import decode`, a Go import alias). The resolver is file-local and static. A name the file binds to anything else (a def,
an assignment, a parameter, another module, a relative or star import) is not a decoder; a name is not followed across files, through
a reassignment, `getattr` or a dynamic import. A custom base64 alphabet is not decoded. The claim that a rebound name is not a decoder
is exact for Python only. For JS/TS and Go it is a check of the code text (strings and comments are not read): a `function`, `const`,
`let`, `var` or `class` named `atob` or `Buffer`, or a Go name declared with `:=` or `var`, stops the match; a parameter, a `catch`
binding, a destructuring or (JS/TS) an `import` under that name is not seen, so such a call may still be read as the standard
decoder. That errs on the stricter side (more findings, never fewer).

Linear time: the first version of the assembly and of the JS/Go chain reading was quadratic on generated files (120,000 lines of
`k += ""` took 123 s against 4.0 s before the feature). A name is now dropped when a statement is more than 25 lines after the first,
after 256 extensions, or beyond 4,096 characters; a chain of literals is read once. Measured on the final code, person, staged, no cache
(new against 0.6.1): 120,000 lines of `k += ""` 4.8 s against 4.4 s; 30,000 lines of `parts.append("")` 2.1 s against 1.9 s; an
8,000-piece literal chain next to `[].reverse()` 0.5 s against 0.4 s. Every other shape of the two timing scripts is within about 1.2
times 0.6.1. Tests count the work of 4 times the pieces against 1 times (not time-based) and one serial test times seven generated files.

A later review found one more quadratic shape: a Go file with N import aliases of `encoding/hex`, each called once, searched the whole
file once per alias for a variable of that name (10,000 aliases: 69.7 s against 1.3 s after the fix; 5,000: 17.9 s against 0.8 s). The
names a Go file declares are now read once per file; a test counts the characters the module-level `re` calls scan (twice the aliases,
at most 3 times the work), and the serial timing test has the 10,000-alias shape. The shadow checks of JS/TS and Go read the code only
(a `"function atob"` in a string does not stop the match), and one Python expression nested too deeply to follow (a chain of 600
`[::-1]`) no longer drops the findings of the rest of the file: it is skipped alone, and a chain longer than 64 steps is not followed.

Cases (training only, from `eval/tools/seeds/seed_v6.py`; the placeholders `@@AWSB64@@`, `@@AWSHEX@@`, `@@AWSREV@@`, `@@AWSROT13@@` in
`eval/run.py`, and `@@AWSWORDB64@@` for the key inside a longer word, are built at run time, so no tracked file holds the key in any
form, plain, encoded, or encoded inside a word; a test scans every stored file for all of them): 30 in all. Seeded `must_fire`, 20: 14 for each
decoder call and language plus a three-file assembly case, and 6 for the other spellings (a `from` import, a module alias, a codec
alias, the global object and other encodings, two Go import aliases). Adversarial `must_not_fire`, 10: 8 of harmless encoded data
(ordinary text, a PNG header, SHA-256 and SHA-1 hex, an image-like blob, a JWT segment, a key-shaped run inside a longer word, the
encoded AWS documentation example) and 2 more (a function of another module that only has the same name, and a blob decoded through an
aliased import). Not a case: a key inside a literal over 4,096 characters; the tool documents that it does not decode it, so neither
label would be honest; a unit test pins the skip. On the unchanged tool the 14 first seeded cases and the 6 new ones give 34 and 14 `FN`
keys (every `must_fire` label, person and agent) and no other failure.

Holdout: the last full run of `py eval/run.py --check --no-write --families real,seeded,adversarial` (before the name resolver and the
linear-time change) gave holdout 66 cases, `secrets.hardcoded missed 8` both before and after this feature. The count did NOT move. This
feature was a hypothesis about that family, not a fix for named holdout cases (the holdout is not read), so the holdout was not the target
and no claim about it is made. The final full run, with the clean family, is part of the verification that follows this entry; its
numbers are not given here.
