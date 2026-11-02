import os
from pathlib import Path

import pytest

from conftest import aigate

SNAP = Path(__file__).with_name("snapshots")
UPDATE = os.environ.get("AIGATE_UPDATE_SNAPSHOTS") == "1"
make = aigate.make
AWS_LIKE = "AKIA" + "QWERTYUIOPASDFGH"


def result(findings, verdict, risk, *, escalated=False, agent=False):
    prov = ("agent", "$CLAUDECODE") if agent else ("human", "default")
    stats = {"files": 2, "added": 12, "removed": 3, "seconds": 0.1}
    return aigate.Result(findings, verdict, escalated, risk, prov, "staged changes", stats, "built-in defaults", [])


def baselined(f):
    f.allowed = "baseline"
    return f


CASES = {
    "pass": (lambda: result([], "pass", 0), {}),
    "warn_only": (lambda: result([make("code.todo", "app/a.py", 4, "# TODO later")], "warn", 3), {}),
    "fix_one_rule": (lambda: result([make("security.eval", "app/a.py", 7, "eval() runs code built at runtime")],
                                    "fix", 30), {}),
    "fix_two_rules": (lambda: result([make("security.eval", "app/a.py", 7, "eval() runs code built at runtime"),
                                      make("security.shell", "app/b.py", 12, "os.system(...) runs through a shell"),
                                      make("security.eval", "app/a.py", 30, "eval() runs code built at runtime"),
                                      make("code.todo", "app/b.py", 2, "# TODO later")], "fix", 50), {}),
    "block": (lambda: result([make("security.eval", "app/a.py", 7, "eval() runs code built at runtime"),
                              make("secrets.hardcoded", "cfg/keys.py", 1, "AWS access key ID (AKIA…FGH)")],
                             "block", 100), {}),
    "escalated": (lambda: result([make("security.shell", f"app/m{i}.py", 5, "subprocess.run(...) runs through a shell")
                                  for i in range(4)], "block", 64, escalated=True), {}),
    "agent_fix": (lambda: result([make("security.eval", "app/a.py", 7, "eval() runs code built at runtime")],
                                 "fix", 36, agent=True), {}),
    "baseline_hidden": (lambda: result([make("code.todo", "app/a.py", 4, "# TODO later"),
                                        baselined(make("security.eval", "app/old.py", 9, "eval() runs code"))],
                                       "warn", 3), {}),
    "hook_cap": (lambda: result([make("code.todo", f"src/f{i:02d}.py", i + 1, "# TODO later") for i in range(30)],
                                "warn", 25), {"hook": True}),
    "ascii": (lambda: result([make("security.eval", "app/a.py", 7, "eval() runs code built at runtime")], "fix", 30),
              {"sym": aigate.SYMBOLS_ASCII}),
    "tty_80": (lambda: result([make("security.eval", "app/a_very_long_module_name.py", 7,
                                    "eval() runs code built at runtime " + "and this message keeps going " * 4)],
                              "fix", 30), {"width": 80}),
    "no_tty_full": (lambda: result([make("security.eval", "app/a_very_long_module_name.py", 7,
                                         "eval() runs code built at runtime " + "and this message keeps going " * 4)],
                                   "fix", 30), {}),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_golden_snapshot(name):
    build, opts = CASES[name]
    text = aigate.render_text(build(), **opts).replace(f"aigate-lite {aigate.__version__} ", "aigate-lite <VERSION> ")
    path = SNAP / f"{name}.txt"
    if UPDATE:
        SNAP.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    assert text == path.read_text(encoding="utf-8"), f"snapshot {name} changed (AIGATE_UPDATE_SNAPSHOTS=1 rewrites)"


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_non_pass_result_ends_with_exactly_one_next_line(name):
    build, opts = CASES[name]
    res = build()
    lines = aigate.render_text(res, **opts).splitlines()
    nexts = [ln for ln in lines if ln.startswith("next: ")]
    if res.verdict == "pass" and not any(f.allowed == "baseline" for f in res.findings):
        assert nexts == []
    else:
        assert len(nexts) == 1 and lines[-1] == nexts[0]


def test_hints_are_shown_once_per_rule_and_files_ordered_by_severity():
    text = aigate.render_text(CASES["fix_two_rules"][0]())
    assert text.count("Avoid eval/exec") == 1
    order = [ln for ln in text.splitlines() if ln.startswith("app/")]
    assert order == ["app/a.py", "app/b.py"]
    block = aigate.render_text(CASES["block"][0]()).splitlines()
    assert [ln for ln in block if ln.startswith(("app/", "cfg/"))] == ["cfg/keys.py", "app/a.py"]


def test_truncation_only_with_a_width():
    long = aigate.render_text(CASES["no_tty_full"][0]())
    short = aigate.render_text(CASES["tty_80"][0](), width=80)
    assert max(map(len, short.splitlines())) <= 80 and max(map(len, long.splitlines())) > 80


def test_verbose_keeps_the_detailed_view_and_quiet_is_unchanged():
    res = CASES["fix_one_rule"][0]()
    verbose = aigate.render_text(res, verbose=True)
    assert "fingerprint " in verbose and "        eval() runs code built at runtime" in verbose
    quiet = aigate.render_text(res, quiet=True)
    assert "--no-verify" in quiet and "next:" not in quiet


def test_cp1252_pipe_gets_ascii_symbols_not_question_marks(repo):
    repo.write("a.py", "eval(x)\n")
    repo.add()
    out = repo.run("check", "--color", "never", env={"PYTHONIOENCODING": "cp1252"}).stdout
    assert "X FIX" in out and "?" not in out and "->" in out


def test_color_switches(repo):
    repo.write("a.py", "eval(x)\n")
    repo.add()
    esc = "\x1b["
    assert esc in repo.run("check", env={"FORCE_COLOR": "1"}).stdout                        # forced on a pipe
    assert esc not in repo.run("check", env={"FORCE_COLOR": "1", "NO_COLOR": "1"}).stdout
    assert esc not in repo.run("check").stdout                                               # pipe: no colour
    assert esc in repo.run("check", "--color", "always").stdout
    assert esc not in repo.run("check", "--color", "never", env={"FORCE_COLOR": "1"}).stdout


def test_secret_values_never_reach_the_text_report(repo):
    repo.write("keys.py", f'K = "{AWS_LIKE}"\n')
    repo.add()
    assert AWS_LIKE not in repo.run("check", "--color", "never").stdout
