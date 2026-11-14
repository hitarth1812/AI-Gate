## What and why

## Checks (paste the commands you ran and their last lines)

- [ ] a failing test first, then the fix (name the test)
- [ ] `python -m pyflakes aigate_lite.py` is clean
- [ ] `python -m pytest -q` passes
- [ ] `python eval/run.py --check --families real,seeded,adversarial` passes (no new failure)
- [ ] no test or corpus label was weakened, skipped or deleted
- [ ] an AI agent's result is not softer than a person's; any softening is for a person only, with a test
- [ ] CHANGELOG entry for anything a user can see
- [ ] no new dependency, network call or telemetry
