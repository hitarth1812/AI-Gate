from conftest import aigate, find
from test_perf import run_inproc, watch


def test_checkers_are_described_by_the_plugin_interface():
    names = [c.name for c in aigate.CHECKERS]
    assert len(names) == len(set(names)) == 12
    assert all(isinstance(c, aigate.Checker) and callable(c.run) for c in aigate.CHECKERS)
    by_name = {c.name: c for c in aigate.CHECKERS}
    assert by_name["check_deps"].needs_network and not by_name["check_secrets"].needs_network
    assert by_name["check_python"].languages == ("python",) and by_name["check_secrets"].languages is None


def test_checker_for_a_language_not_in_the_change_is_skipped(repo, monkeypatch, capsys):
    repo.write("web/app.js", "export const x = 1;\n")
    repo.add()
    py = watch(monkeypatch, "check_python")
    monkeypatch.setattr(aigate, "CHECKERS", [c if c.name != "check_python" else
                                             aigate.Checker(c.name, c.languages, c.needs_network, aigate.check_python)
                                             for c in aigate.CHECKERS])
    code, res = run_inproc(repo, monkeypatch, capsys, "check", "--format", "json")
    assert code == 0 and py == []


def test_checker_for_a_language_in_the_change_runs(repo, monkeypatch, capsys):
    repo.write("svc.py", "x = eval(y)\n")
    repo.add()
    _, res = run_inproc(repo, monkeypatch, capsys, "check", "--format", "json")
    assert find(res, "security.eval")


def test_deleted_python_file_is_not_parsed(repo, monkeypatch, capsys):
    repo.write("old.py", "def f():\n    return 1\n")
    repo.commit()
    (repo.path / "old.py").unlink()
    repo.add()
    trees = watch(monkeypatch, "_python_file")
    code, _ = run_inproc(repo, monkeypatch, capsys, "check", "--format", "json")
    assert trees == []


def test_a_plugin_checker_runs_and_a_crashing_one_fails_closed(repo, monkeypatch, capsys):
    def boom(ctx):
        raise RuntimeError("kaput")

    def todo_counter(ctx):
        return [aigate.make("code.todo", fc.path, None, "plugin saw it", key="plugin") for fc in ctx.changes]
    monkeypatch.setattr(aigate, "CHECKERS", aigate.CHECKERS + [aigate.Checker("check_boom", None, False, boom),
                                                               aigate.Checker("check_plugin", ("python",), False,
                                                                              todo_counter)])
    repo.write("a.py", "x = 1\n")
    repo.add()
    code, res = run_inproc(repo, monkeypatch, capsys, "check", "--format", "json")
    err = find(res, "gate.error")
    assert code == 1 and err and err[0]["message"] == "check_boom failed: RuntimeError: kaput"
    assert [f["message"] for f in find(res, "code.todo")] == ["plugin saw it"]
