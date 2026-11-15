# The reviewer: `aigate-lite review`

The gate asks "may this commit go in?". The reviewer reads the same change the way a careful senior reviewer would and
answers "what changed, what must be fixed, what should I look at, what looks good":

```bash
aigate-lite review                       # what is staged
aigate-lite review --range origin/main..HEAD
aigate-lite review --pr                  # the pull request in CI: range against its target branch, plus its title and text
aigate-lite review --format md           # or text (default), json (versioned schema), agent
aigate-lite check --deep                 # the gate's normal report, with the reviewer's findings added
```

It runs the gate's checks and six more that look at the rest of the repository, not only the changed lines:

| rule | catches | people / AI agents |
|---|---|---|
| `api.unknown_symbol` | `from app.utils import parse_date` when there is no `parse_date`; a missing module; `pkg.DoThing()` with no such function (Python, Go, relative JS/TS imports) | warn / fix |
| `api.bad_call` | a call with an unknown keyword or the wrong number of arguments to a function defined in the repo (Python, Go) | warn / fix |
| `api.broken_caller` | a function's parameters changed and callers elsewhere were not updated (reported at each stale caller) | warn / fix |
| `tests.missing_for_change` | functions changed with no related test changed (`[review] test_map` maps your layout) | warn / fix |
| `review.claim_mismatch` | the commit message or PR text says "add tests", "fix `parse_date`" or "no behaviour change" and the diff says otherwise | warn / warn |
| `code.error_check_removed` | an `if err != nil` removed, `except ValueError` turned into `except Exception: pass`, a `.catch` or `await` dropped, `_ = f()` where `f` returns an error | warn / fix |

How much to trust it:

- Everything is read with `ast` (Python) or the lexer (Go, JS/TS). Your code is never imported or run.
- A file that does not parse, or a module that is dynamic (`__getattr__`, star imports, `getattr`/`hasattr` on the module,
  `globals()`, `__dict__`, `exec`), produces no claim: the reviewer says nothing it cannot prove from the source.
- Only names inside this repository are judged. Standard library, installed packages and path aliases are left alone.
- These checks run only in `review` and `check --deep`, never in the commit hook, so a commit is not slower.
  Measured costs are in [PROGRESS.md](dev/PROGRESS.md).
- The symbol index is cached by git blob id next to the result cache (`symbols-v1.json`, signed); `--no-cache` skips it.

```toml
[review]
# source glob = where its tests live ({stem}, {dir}, {ext}); added to the usual names (test_x.py, x_test.go, x.test.ts)
test_map = { "app/**/*.py" = "checks/{stem}_spec.py" }
```
