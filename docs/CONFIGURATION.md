# Configuration: `.aigate/lite.toml`

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
                                # then approve them once: aigate-lite trust

[[allow]]                       # a known false positive
rule = "secrets.generic"
path = "tests/fixtures/**"      # blocking rules need a path or a fingerprint (see `check -v`)
reason = "fake credentials used by tests"
expires = 2027-06-30
```

To allow a fix- or warn-level finding for one commit: `AIGATE_ALLOW=tests.deleted git commit ...`.
Blocking findings can only be allowed through `[[allow]]`.

Result cache (off by default): `--cache` or `[gate] cache = true` remembers which files the per-file checks
found clean, so an unchanged file is not checked again. Only clean verdicts are stored (no finding text),
signed, in two 0600 files in your user cache dir. `--no-cache` turns it off even when the config turns it
on, and also skips the registry lookup cache. It helps large changes and not small ones: compare the `C`
and `C-cache` rows (300 files) with `A` and `A-cache` (1 file) in [PERF.md](PERF.md).

## Commands need your approval

A cloned or pulled config can't run code on your
next commit: `[[commands]]` stay off (one `commands.untrusted` warning says so; a repo's `[rules]` can't
change that rule) until a person runs
`aigate-lite trust`, which prints every command with hidden characters escaped and records exactly that
list in `.git/aigate/trusted`. Any change to any command needs a new approval; `trust --rev <branch>`
approves a config you push but have not checked out; AI agents can't approve. `--config FILE` on the
command line counts as your own choice and is trusted (so don't point it at a file a pull request controls).
