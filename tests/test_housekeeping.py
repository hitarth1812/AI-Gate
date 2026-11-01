import types

import pytest

from conftest import aigate, find

SHELL = "import subprocess\n\n\ndef run(cmd):\n    return subprocess.run(cmd, shell=True)\n"


class Dist:
    def __init__(self, name, tops=None, error=None):
        self.metadata, self.files, self._tops, self._error = {"Name": name}, None, tops, error

    def read_text(self, filename):
        if self._error:
            raise self._error
        return self._tops if filename == "top_level.txt" else None


def environment(monkeypatch, tmp_path, dists, site=None):
    import importlib.metadata
    monkeypatch.setattr(importlib.metadata, "distributions", lambda path=None: iter(dists))
    monkeypatch.setattr(aigate, "find_site_packages", lambda root: [str(site)] if site else [])
    ctx = types.SimpleNamespace(cache={}, repo=types.SimpleNamespace(root=str(tmp_path)))
    return aigate.python_environment(ctx)


def test_one_unreadable_distribution_does_not_hide_the_others(monkeypatch, tmp_path):
    names, mapping = environment(monkeypatch, tmp_path,
                                 [Dist("broken", error=OSError("unreadable")), Dist("good-dist", "goodmod\n")])
    assert "goodmod" in names and mapping["goodmod"] == {"good-dist"}


def test_project_site_packages_survive_a_broken_distribution(monkeypatch, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "localmod.py").write_text("x = 1\n")
    names, mapping = environment(monkeypatch, tmp_path,
                                 [Dist("bad", error=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")),
                                  Dist("good-dist", "goodmod\n")], site)
    assert {"localmod", "goodmod"} <= names and mapping["goodmod"] == {"good-dist"}


def test_an_unexpected_error_is_not_swallowed(monkeypatch, tmp_path):
    with pytest.raises(TypeError):                      # a bug, not broken metadata: fail closed (gate.error)
        environment(monkeypatch, tmp_path, [Dist("weird", error=TypeError("bug"))])


def test_different_shell_calls_get_different_fingerprints_and_twins_both_report(repo):
    twice = SHELL + "\n\ndef other(cmd):\n    return subprocess.run(cmd + ['-v'], shell=True)\n" \
                    "\n\ndef again(cmd):\n    return subprocess.run(cmd, shell=True)\n"
    repo.write("app/run.py", twice)
    repo.add()
    hits = find(repo.check(), "security.shell")
    assert [f["line"] for f in hits] == [5, 9, 13]
    assert hits[0]["fingerprint"] == hits[2]["fingerprint"] != hits[1]["fingerprint"]


def test_shell_fingerprint_is_the_same_for_the_same_call_on_another_line(repo):
    repo.write("app/a.py", SHELL)
    repo.commit("a")
    first = find(repo.check("--all"), "security.shell")[0]["fingerprint"]
    repo.write("app/a.py", "# one\n# two\n# three\n" + SHELL)
    repo.commit("moved")
    second = find(repo.check("--all"), "security.shell")
    assert second and second[0]["line"] == first_line_plus(3) and second[0]["fingerprint"] == first


def first_line_plus(n):
    return SHELL.splitlines().index("    return subprocess.run(cmd, shell=True)") + 1 + n
