# Using aigate-lite

Setup modes, the hooks, task scopes, baselines, presets and the output formats. The short version is in the
[README](../README.md); every rule is in [RULES.md](RULES.md).

## Quick start

```bash
# inside your project
aigate-lite setup                                  # adds both hooks, writes .aigate/lite.toml
aigate-lite audit                                  # see what is already in the repo
aigate-lite baseline                               # optional: accept today's findings so only new ones block
git add .aigate && git commit -m "add aigate-lite"
```

## Without installing, and how `setup` picks a mode

Without installing anything: `python3 /path/to/aigate_lite.py setup` (Windows: `py C:\path\to\aigate_lite.py setup`)
copies the gate into `.aigate/`, and the commands become `python3 .aigate/aigate_lite.py <command>`
(Windows: `py .aigate/aigate_lite.py <command>`).

`setup` is `install` plus `init`; it never overwrites an existing `.aigate/lite.toml`. Run from a loose `aigate_lite.py` (as
above) it vendors the gate into `.aigate/`, because a loose file cannot be found again by name; run from an installed package
(`pipx install aigate-lite`) the hooks run the installed tool instead (`--mode installed|vendored` chooses explicitly). A
repository that already has `.aigate/aigate_lite.py` stays vendored, and `setup` says whether its copy was updated (from which
version to which) or is unchanged; a copy newer than the running tool is kept, with a warning. `aigate-lite unvendor`
switches such a repository to the installed tool. `audit` is `check --all`.

On Windows use `py -3` instead of `python3`; the installed hooks use the interpreter that ran `install` (pinned in `.git/hooks`, never committed) and, if
that is gone, look for `python3`, `python`, `python3.14`..`python3.11`, `py -3.14`..`py -3.11`, then `py -3`.

## Manual setup and what runs when

**Manual setup** (what `setup` does, step by step):

```bash
aigate-lite install                                # adds both hooks (they run the installed aigate-lite)
aigate-lite init                                   # optional: starter .aigate/lite.toml
git add .aigate && git commit -m "add aigate-lite"
```

`aigate-lite install --vendor` instead copies the gate into `.aigate/aigate_lite.py`, so teammates need no install.

From then on:

| When | What runs | Checks |
|---|---|---|
| `git commit` | `pre-commit` hook | The staged changes |
| `git push` | `pre-push` hook | Every commit being pushed, one by one for secrets. This catches commits made with `--no-verify`. |
| Any time | `aigate-lite check` | Staged changes, or `--range origin/main..HEAD` |
| Audit | `aigate-lite audit` (same as `check --all`) | Every tracked file in HEAD, including the root commit (not used by hooks) |

`--vendor` makes the hooks portable: they run `.aigate/aigate_lite.py` with whatever `python3` is on
the PATH, so teammates only need to run `python3 .aigate/aigate_lite.py install --vendor`
(Windows: `py .aigate/aigate_lite.py install --vendor`).
Without `--vendor`, the hooks point at the script's current location.

**Existing hooks.** If you already use husky or the pre-commit framework, `install` prints the line or
YAML snippet to add instead. `--force` installs anyway; your old hook is kept as `<hook>.local` and
still runs first.

## Declaring a task scope (scope creep)

```bash
aigate-lite scope set 'src/auth/**' 'tests/auth/**'   # before you start the AI on a task
aigate-lite scope show
aigate-lite scope clear                                # when the task is done
```

The scope is stored inside `.git/`, so it is per-clone and never committed. An AI agent cannot change it (see
[AI_AGENTS.md](AI_AGENTS.md)).

## Existing project? Accept today's findings with `baseline`

```bash
aigate-lite check --all                             # see what is already there
aigate-lite baseline                                # record non-secret findings in .aigate/baseline.json
git add .aigate/baseline.json && git commit -m "baseline"
```

Fingerprints do not depend on line numbers, so unrelated edits do not resurrect old findings. Secrets are never
baselined unless you pass `--include-secrets` (rotate them instead), and a baseline never hides *new* copies of an old finding.
The file is applied only once it is committed: until then `audit` and `check` say "baseline.json exists but is not committed, so it
is not applied yet". A push or a range is judged change by change, so a baseline accepts only as many findings as the change
removed (a new branch whose whole history is "new" is judged against that rule too).

## Fixing the mechanical findings: `fix`

```bash
aigate-lite fix                  # staged changes: a diff of what it would change (debugger lines, focused tests, env files)
aigate-lite fix --yes            # apply it and stage the edits
aigate-lite fix --all            # the same for the tracked files (what `audit` reads); the edits are staged, not committed
aigate-lite fix --all --allow-pass --yes   # a debugger line that is the only statement in its block becomes `pass`
```

`fix` never runs from a hook. A line it cannot change safely is listed with its file, line and the reason; the rest of that file
is still fixed. When it changes nothing it says why: nothing staged, findings in committed files (use `--all`), or findings that
need a manual change (with their rules). `check` and `audit` name `fix` on their `next:` line only when it can change something.

## Where the gate looks

The gate uses the git repository that contains the current folder. When that is a parent of the folder you are in it says so
("using the git repository at ..."), and `setup` and `install` stop and ask for `--yes` if the folder has no tracked files at all
(usually a new project that needs its own `git init`). A manual `check` or `audit` also says how many files in the current
folder were not tracked, because they were not scanned.

## Presets

`preset = "relaxed" | "balanced" | "strict"` in `.aigate/lite.toml` (default `balanced`); `init` picks one for you.
Your own `[rules]` settings always win (this includes turning the "core" rules of [CHECKS.md](CHECKS.md) on for people). `strict` turns
them on. The full table of what each preset changes is in
[docs/RULES.md](https://github.com/hitarth1812/AI-Gate/blob/main/docs/RULES.md) (generated by `scripts/gen_docs.py`).

## Output and exit codes

- `--format text` (default), `json` (stable schema), `github` (annotations for Actions) or `sarif`
  (SARIF 2.1.0, for code-scanning uploads).
- `-q` prints only what stops the commit; `-v` adds hints, fingerprints and allowed findings. A text report that prints
  a finding ends with a one-line credit (see [CREDIT.md](CREDIT.md)).
- Exit codes: `0` pass or warnings only, `1` fix or block, `2` usage, git or internal error (one line, never a traceback). `--format json` carries `schema_version`. What stays stable (exit codes, JSON keys, rule ids, versioning and deprecation) is in [docs/CONTRACT.md](https://github.com/hitarth1812/AI-Gate/blob/main/docs/CONTRACT.md).
- The verdict is the strongest action found. A Fix verdict becomes Block when the risk score is 60 or
  higher; the score uses the same formula as the full gate.
