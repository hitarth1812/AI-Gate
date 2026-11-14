# The contract: what stays stable

This page says what you can depend on when you run aigate-lite from a script, a CI job or an agent. Everything here is tested
(`tests/test_json_contract.py`, `tests/test_output.py`, `tests/test_agent_format.py`, `tests/test_ci.py`).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | pass, or only warnings (the commit may go in) |
| `1` | at least one FIX or BLOCK (the commit is stopped), including a `gate.error` finding |
| `2` | usage error, git error, or an internal error. An internal error is one line, `aigate-lite: internal error (Class: message)`, never a traceback; add `--debug` for the traceback |

An internal error never passes silently. A check that fails becomes a `gate.error` finding: a BLOCK by default; `[gate]
fail_closed = false` softens it to a warning for a person only, and it stays a BLOCK for an AI agent whatever the config says.

## `--format json`

One JSON document on stdout, always valid, also when nothing is staged (an empty pass report). The layout version is
`schema_version` (an integer, currently `1`; the older key `schema` carries the same value and is kept so nothing that reads it
breaks). Existing keys are never removed or retyped within a `schema_version`; new keys may be added, so read the keys you use and
ignore the rest. A change that removes or retypes a key gets a new `schema_version` and a CHANGELOG entry under "Changed".

Top-level keys of `check` and `audit`: `tool`, `version`, `schema`, `schema_version`, `author`, `url`, `label`, `tool_info`,
`provenance`, `config`, `verdict` (`pass | warn | fix | block`), `escalated`, `risk`, `exit_code`, `stats`, `notes`, `findings`.
Each finding has `rule`, `action`, `severity`, `confidence`, `path`, `line`, `message`, `hint`, `fingerprint`, `legacy_fingerprint`,
`sensitive_path` and `allowed`.
`review --format json` has its own document with `kind: "review"` and the same `schema` / `schema_version` fields.
`--format sarif` is SARIF 2.1.0. `--format agent` is the line format documented in the README; its first line and `END` are the
contract, the `TOOL` line is informational.

## Rule ids

Rule ids (`secrets.hardcoded`, `deps.hallucinated`, ...) are frozen: a rule may be added, but is never renamed or removed without the
deprecation cycle below. `tests/test_json_contract.py` lists the ids of the 0.5.2 release and fails if one disappears. A rule's
default action can change in a minor release and is then listed in the CHANGELOG under "Changed".

## Versioning (SemVer statement)

aigate-lite follows [Semantic Versioning](https://semver.org). While the major version is `0`:

- a patch release (`0.5.x`) fixes bugs and adds detections or documentation; it may make an existing check stricter or quieter when
  the CHANGELOG says so (a detection gain or a labelled false positive);
- a minor release (`0.x.0`) may change defaults and behaviour you can see, always listed in the CHANGELOG;
- from `1.0.0` on, anything that breaks the contract on this page needs a major release.

## Deprecation policy

A rule id, a config key, a JSON key or a command that is going away is first marked "Deprecated" in the CHANGELOG and, where it
can be done, answered with a one-line notice when it is used. It keeps working for at least two minor releases before it is
removed, and its removal is listed under "Removed". Security fixes are the exception: they may change behaviour at once.
