# Enforce it in CI (hooks can be skipped)

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
