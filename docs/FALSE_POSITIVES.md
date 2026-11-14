# Reporting a false positive (or a missed detection)

A false positive is a finding that is wrong for your code. A missed detection (a false negative) is a mistake the gate should have
reported. Both are the most useful bug reports this project gets, because each good one becomes a permanent case in the evaluation
corpus (`eval/`) and the gate is measured against it from then on.

## What to send

Open an issue with the "False positive / missed detection" template and include:

1. the **rule id** (`aigate-lite check --format json` shows it, or the text report's second column);
2. the **smallest code** that shows it: a few lines, written by you, with names and values changed. **Never paste a real secret, a
   real key, or code you may not share.** A fake value of the same shape is enough;
3. **who** it concerns: a person's change, an AI agent's change, or both (`aigate-lite doctor` shows what your shell is detected as);
4. what you **expected** (no finding, or WARN instead of FIX, or a finding that was missed) and why;
5. your `aigate-lite --version` and the command you ran.

## What happens next

The maintainer reduces it to a corpus case (`before/` and `after/` files and a `case.toml` with the label), proposes the label in
`eval/LABEL_CHANGES.md`, and you are asked to confirm it. Once the label is approved it is frozen. The fix then starts with a failing
test; a fix that softens a finding applies to a person only, and an AI agent's result is unchanged.

## Until it is fixed

- Allow one specific finding with a reason: an `[[allow]]` entry in `.aigate/lite.toml` with the rule, the path and the fingerprint
  (`aigate-lite check -v` prints it). A key or secret BLOCK prints a ready snippet. An AI agent cannot add the entry: it must stop and
  ask a person.
- For a whole class, lower one rule in `[rules]` (for example `"size.*" = "off"`). `aigate-lite explain <rule>` says how.
