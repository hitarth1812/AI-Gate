"""0.5.3 round 2 (Part 3, fail-safe): an internal error is one clear line and a defined exit code, and it is never a silent pass
for an AI agent. A checker that raises becomes a gate.error finding; `[gate] fail_closed = false` softens it to a warning for a
person only (an agent's config is read from HEAD, but a gate.error is a BLOCK for an agent whatever the config says)."""
import dataclasses
import json

import pytest

from conftest import aigate, find

CFG = "version = 1\n[gate]\nfail_closed = false\n"


def boom(ctx):
    raise RuntimeError("simulated checker failure")


@pytest.fixture
def broken(monkeypatch, repo):
    """A repo whose first checker raises, run in-process so the checker table can be patched."""
    first = dataclasses.replace(aigate.CHECKERS[0], run=boom)                                         # Checker is frozen
    monkeypatch.setattr(aigate, "CHECKERS", [first] + aigate.CHECKERS[1:])
    monkeypatch.chdir(repo.path)
    for k in ("CLAUDECODE", "AI_AGENT", "GEMINI_CLI"):          # this process may itself run under an AI agent
        monkeypatch.delenv(k, raising=False)
    for k, v in repo.env.items():
        monkeypatch.setenv(k, v)
    repo.write("app.py", "x = 1\n")
    repo.add()
    return repo


def run(capsys, *args):
    code = aigate.main(["check", "--offline", "--format", "json", *args])
    return code, json.loads(capsys.readouterr().out)


def errors(res):
    return [f for f in find(res, "gate.error") if not f["allowed"]]


def test_a_failing_checker_is_a_block_by_default(broken, capsys):
    code, res = run(capsys)
    assert errors(res) and errors(res)[0]["action"] == "block" and code == 1


def test_fail_closed_false_softens_it_for_a_person_only(broken, capsys, monkeypatch):
    broken.write(".aigate/lite.toml", CFG)
    broken.commit("config")
    broken.write("b.py", "y = 2\n")
    broken.add()
    code, res = run(capsys)
    assert errors(res) and errors(res)[0]["action"] == "warn", res["findings"]
    monkeypatch.setenv("CLAUDECODE", "1")
    code, res = run(capsys)
    assert errors(res) and errors(res)[0]["action"] == "block" and code == 1, res["findings"]      # never a silent pass for an agent


def test_an_unexpected_exception_outside_the_checkers_is_one_line_and_exit_2(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.path)
    monkeypatch.setattr(aigate, "run_check", lambda *a, **k: (_ for _ in ()).throw(ValueError("simulated")))
    code = aigate.main(["check", "--offline"])
    err = capsys.readouterr().err
    assert code == 2 and "aigate-lite: internal error (ValueError: simulated)" in err and "Traceback" not in err
