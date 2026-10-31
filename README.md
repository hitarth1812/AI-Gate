# aigate-lite

A free, local "CI gate" that runs when you commit. It checks only what the commit adds, looking for
the mistakes AI coding tools make most often, and stops the commit if it finds a serious one.

- **Cost:** none. One Python file, standard library only. No account, server, API key or LLM.
- **Network:** only free public lookups (PyPI, npm, OSV), cached on disk. `--offline` skips them.
- **Speed:** about 0.3–0.7 s per commit in testing (small commits, warm cache).
- **Needs:** Python 3.11+ and git.

It is the lightweight, local version of the AI Code Trust Gate plan. It uses the same rule names,
actions and risk score.

## Quick start

```bash
# inside your project
python3 /path/to/aigate_lite.py install --vendor   # copies itself to .aigate/ and adds both hooks
git add .aigate/aigate_lite.py && git commit -m "add aigate-lite"
python3 .aigate/aigate_lite.py init                # optional: starter .aigate/lite.toml
```

From then on:

| When | What runs | Checks |
|---|---|---|
| `git commit` | `pre-commit` hook | The staged changes |
| `git push` | `pre-push` hook | Every commit being pushed, one by one for secrets. This catches commits made with `--no-verify`. |
| Any time | `aigate-lite check` | Staged changes, or `--range origin/main..HEAD` |

`--vendor` makes the hooks portable: they run `.aigate/aigate_lite.py` with whatever `python3` is on
the PATH, so teammates only need to run `python3 .aigate/aigate_lite.py install --vendor`.
Without `--vendor`, the hooks point at the script's current location.

**Existing hooks.** If you already use husky or the pre-commit framework, `install` prints the line or
YAML snippet to add instead. `--force` installs anyway; your old hook is kept as `<hook>.local` and
still runs first.

## What it catches

**Actions:** `block` and `fix` stop the commit; `warn` only reports.

| Mistake | Rules (default action) |
|---|---|
| Exposed secrets | `secrets.hardcoded`: provider keys and private keys (block). `secrets.env_file`: `.env`, key and credential files (block). `secrets.generic`: credential-like literals and JWTs (fix). `secrets.env_not_ignored` (warn). |
| Hallucinated or risky packages | `deps.hallucinated`: package or version not on PyPI/npm, in a manifest or an import (block). `deps.untrusted_source`: git/URL deps, custom indexes, odd lockfile URLs (block). `deps.new_package`: first release under 30 days (fix). `deps.version_cooldown`: version under 5 days (fix), with security bumps exempt. `deps.vulnerable`: OSV advisories for the pinned version (fix). `deps.undeclared` (fix). `deps.removed_stdlib`, e.g. `distutils` or `cgi` (fix). `deps.typosquat`, `deps.outdated` and `deps.deprecated` (warn). |
| Weakened tests | Assertions or cases removed, skip/xfail added, tests deleted (renames are detected), `.only` focus, trivial asserts, test-config narrowing (all fix). `sitecustomize.py`, `.pth` files and `pytest11` entry points (block). Result-changing pytest hooks (fix). Expected values changed alongside source (warn). |
| Placeholder or unsafe code | `... rest of the code` comments, `...` bodies, swallowed exceptions, debugger leftovers, syntax errors (fix). `eval`/`exec`, `shell=True`, pickle and unsafe YAML, SQL built with f-strings, `verify=False` (fix). Debug mode, wildcard CORS, weak crypto, raw HTML (warn). |
| Silenced checkers | `# nosec`, `# type: ignore`, `@ts-ignore`, `# pragma: no cover`, whole-file disables (fix). `# noqa`, `eslint-disable-line` (warn). In `[paths] sensitive` these are one step stricter. |
| Bloat and time complexity | Long or complex functions, long files, big diffs, duplicated blocks, unused imports (warn). Loops nested 3 or more deep, nested loops over the same list, `x in list` / `.pop(0)` / sorting inside loops, network or DB calls inside loops, branching recursion without a cache (warn). |
| Scope creep | `scope.forbidden_path` (block). `scope.out_of_scope` against a declared task scope (fix). `gate.config_changed` (warn). |

`aigate-lite explain` lists all 65 rules. `aigate-lite explain <rule>` shows how to change or allow one.

**Language coverage.** Python gets every check, using its AST. JavaScript/TypeScript get secrets,
dependencies, tests, suppressions and line-level code checks. Function size, complexity and loop
analysis are Python-only. Other languages get secrets, placeholders, TODOs and suppressions.

### Declaring a task scope (scope creep)

```bash
aigate-lite scope set 'src/auth/**' 'tests/auth/**'   # before you start the AI on a task
aigate-lite scope show
aigate-lite scope clear                                # when the task is done
```

The scope is stored inside `.git/`, so it is per-clone and never committed.

## AI agents

When an AI agent runs `git commit` itself, the gate notices it and becomes stricter. It detects agents
through `CLAUDECODE`, `AI_AGENT`, `GEMINI_CLI` or `AIGATE_AGENT` in the environment, an `(aider)`
author name, or AI co-author trailers in pushed commits. You can add your own variables under
`[provenance] agent_env`. For agent commits:

- `forbidden_for_agents` paths are blocked: `.aigate/`, CI workflows and hook configs.
- The config is read from the last commit, so an agent cannot loosen the gate in the same commit.
- `AIGATE_ALLOW` and `AIGATE_SKIP` are ignored.

Detection is best effort. An agent that runs with none of these signals looks like a human.

## Configuration: `.aigate/lite.toml`

See `examples/lite.toml` for every key. The most useful ones:

```toml
[rules]
"suppress.type" = "warn"        # block | fix | warn | off; globs like "perf.*" work

[deps]
private = ["acme-*", "@acme/*"]                       # internal packages: skip public lookups
trusted_sources = ["https://pypi.acme.dev/simple"]
import_map = { mylib = "my-lib-dist" }                # import name -> package name

[[commands]]                    # your own checks, e.g. run tests before every push
name = "tests"
run = "python -m pytest -q"
stage = "push"                  # commit | push | both

[[allow]]                       # a known false positive
rule = "secrets.generic"
path = "tests/fixtures/**"      # blocking rules need a path or a fingerprint (see `check -v`)
reason = "fake credentials used by tests"
expires = 2027-06-30
```

To allow a fix- or warn-level finding for one commit: `AIGATE_ALLOW=tests.deleted git commit ...`.
Blocking findings can only be allowed through `[[allow]]`.

## Output and exit codes

- `--format text` (default), `json` (stable schema), `github` (annotations for Actions) or `sarif`
  (SARIF 2.1.0, for code-scanning uploads).
- `-q` prints only what stops the commit; `-v` adds hints, fingerprints and allowed findings.
- Exit codes: `0` pass or warnings only, `1` fix or block, `2` usage or git error.
- The verdict is the strongest action found. A Fix verdict becomes Block when the risk score is 60 or
  higher; the score uses the same formula as the full gate.

## Enforce it in CI (hooks can be skipped)

Hooks run on the developer's machine, so `--no-verify` (or an agent that commits that way) skips them.
The check that cannot be skipped is a required CI status on the pull request.

**GitHub Actions** — `examples/github-actions.yml`:

1. Copy it to `.github/workflows/aigate-lite.yml` and commit `.aigate/aigate_lite.py`
   (`aigate-lite install --vendor` copies it for you).
2. Open a pull request once so the check runs and GitHub learns its name (the job is called `gate`).
3. Settings → Branches (or Rules → Rulesets) → protect your default branch → **Require status checks to
   pass** → add `gate`. If you use a merge queue, the workflow already listens to `merge_group`.
4. **Required for this to mean anything:** GitHub runs the workflow file from the pull request itself, so
   a pull request that edits `.github/workflows/aigate-lite.yml` can change what runs. Put
   `.github/workflows/**` and `.aigate/**` in CODEOWNERS and turn on **Require review from Code Owners**
   (or make the workflow a ruleset-required workflow). Without that, CI is a safety net, not a boundary.

The workflow pins every action to a full commit SHA, runs the gate **and its config from the base
branch** (a pull request cannot loosen the script or `.aigate/lite.toml` that judge it; the first PR that
adds the gate uses its own copy, with a warning), writes annotations with `--format github`, and uploads
`--format sarif` to code scanning (Security → Code scanning; the upload is skipped without failing the
job where code scanning is not enabled). `--no-commands` keeps `[[commands]]` from the pull request from
running in CI. The gate runs twice (annotations, then SARIF); only the first run decides the result.

**GitLab CI** — `examples/gitlab-ci.yml` runs on merge-request pipelines and keeps `aigate.sarif` as an
artifact. Make it blocking with Settings → Merge requests → **Pipelines must succeed**, and run fork
merge-request pipelines in the parent project (otherwise a fork is checked against its own copy).

**Other CI:** run `python .aigate/aigate_lite.py check --range "origin/main..HEAD" --no-commands` and fail
the job on exit code 1. `--format json` (schema 1) and `--format sarif` (SARIF 2.1.0) are stable outputs.

## Limits (read these)

- **Hooks are bypassable.** `git commit --no-verify` and `git push --no-verify` skip them, and some
  agents commit that way. The pre-push hook catches what the commit hook missed, but only a
  server-side check is enforceable: see "Enforce it in CI" above.
- **Static heuristics, not proof.** Time complexity is flagged from code shape, not measured. Test
  tampering for JS/TS is line-based.
- **Not covered in lite:** calls to functions or attributes that don't exist (that needs a type
  checker, which you can add as a `[[commands]]` entry), sandboxed test runs, mutation testing, and
  LLM review.
- **Commands in `.aigate/lite.toml` run on your machine.** Review that file when you clone a repo that
  has one, as you would with any hook setup.
- **Offline:** if PyPI/npm can't be reached, dependency findings become `deps.unverified` warnings.
  Set `[gate] offline = "block"` to stop instead.

## Development

```bash
python3 -m pytest -q        # 52 tests; registry calls use tests/registry_fixture.json
```
