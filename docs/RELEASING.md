# Releasing aigate-lite to PyPI

Releases are published by [`.github/workflows/release.yml`](../.github/workflows/release.yml) with PyPI
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC). There is no API token, password or secret anywhere:
PyPI trusts one workflow of one repository, and only the `publish` job can ask GitHub for the short-lived identity token
(`id-token: write`).

Pushing a tag `vX.Y.Z` runs three jobs in order:

1. **build**: checks that the tag equals `__version__`, builds the sdist and the wheel, runs `twine check`, checks that the
   wheel holds only `aigate_lite.py` and its dist-info (with `LICENSE` and `NOTICE`) and that the built metadata says the same
   version, prints the SHA-256 of both files and uploads them.
2. **test-wheel**: installs that wheel (not the checkout) into a clean environment on Ubuntu, macOS and Windows with Python
   3.11 and 3.14, runs `aigate-lite --version` and `python -m aigate_lite --version`, and in a temporary git repository runs
   `aigate-lite setup`, stages a fake AWS key built at run time, and requires `aigate-lite check --staged --offline` to exit 1.
3. **publish**: waits for the `pypi` environment, then uploads the files from step 1, unchanged. It has no checkout and
   installs nothing.

Running the workflow by hand (Actions → release → Run workflow) does steps 1 and 2 only. It never publishes, even when you
pick a tag as the branch.

## One-time setup

1. **PyPI**: register a (pending) trusted publisher with exactly these values; a single wrong character gives
   `invalid-publisher`.

   | Field | Value |
   |---|---|
   | PyPI project name | `aigate-lite` |
   | Owner | `hitarth1812` |
   | Repository name | `AI-Gate` |
   | Workflow filename | `release.yml` |
   | Environment name | `pypi` |

2. **GitHub**: Settings → Environments → New environment → `pypi`. Recommended: add yourself under *Required reviewers*, so
   every publish needs a click, and under *Deployment branches and tags* allow only tags matching `v*`.

## Each release

1. Set `__version__` in `aigate_lite.py` (for example `0.5.0`), and in `CHANGELOG.md` replace "unreleased" in that version's
   heading with the date. A version that is on PyPI can never be uploaded again.
2. Run the tests, open a pull request and merge it into `main`.
3. Tag the merge commit and push the tag:

   ```bash
   git switch main && git pull
   git tag v0.5.0
   git push origin v0.5.0
   ```

4. Open the run (Actions → release). When `build` and `test-wheel` are green, `publish` waits for the `pypi` environment:
   open it and choose *Approve and deploy*.
5. Check the result from a clean place:

   ```bash
   pipx install aigate-lite==0.5.0     # or: pip install aigate-lite==0.5.0 / uvx aigate-lite@0.5.0 --version
   aigate-lite --version
   ```

## Common failures

| What you see | Why | What to do |
|---|---|---|
| `invalid-publisher` (or "no trusted publisher matches") in the publish step | A name differs between PyPI and GitHub: project, owner, repository, workflow filename or environment. | Compare the PyPI publisher page with the table above and with the `environment:` and file name in the workflow. Fix the PyPI entry (the workflow file must keep its name and the environment must stay `pypi`), then re-run the failed job. |
| `File already exists` | That version is already on PyPI (also true after a partly failed upload). | Bump `__version__`, update the changelog, merge, and push a new tag. Never re-use a version. |
| The run stays at "Waiting" on `publish` | The `pypi` environment has a required reviewer. | Open the run and approve the deployment. |
| `release check failed: the tag is 'vX' but __version__ ...` | The tag and `__version__` disagree. | Delete the tag (`git push --delete origin vX`, `git tag -d vX`), fix `__version__`, merge, and tag again. Nothing was published. |

## Check the guards locally

```bash
python -m pip install build==1.6.1 twine==7.0.0
python scripts/release_check.py tag v0.5.0     # prints the version, or exits 1 with the reason
python -m build && python -m twine check dist/*
python scripts/release_check.py dist dist      # version in the metadata, wheel contents
```

Delete `dist/`, `build/` and `*.egg-info` afterwards (they are git-ignored, and the test suite refuses to start while an
`aigate_lite.egg-info` is in the repository root).
