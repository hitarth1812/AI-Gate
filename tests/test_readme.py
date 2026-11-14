"""0.5.1 Part E: the README is the PyPI page. Its numbers come from the code, its links work on PyPI (absolute), its first
screen says what the tool is, and every python3 command has a Windows form."""
import re

from conftest import ROOT, aigate

README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_rule_count_comes_from_the_rule_table():
    counts = re.findall(r"\b(\d+) rules\b", README)
    assert counts and {int(n) for n in counts} == {len(aigate.RULES)}, counts
    rules_md = (ROOT / "docs" / "RULES.md").read_text(encoding="utf-8")
    assert f"{len(aigate.RULES)} rules" in rules_md


def test_every_link_is_absolute_so_it_works_on_pypi():
    links = re.findall(r"\]\(([^)\s]+)\)", README)
    relative = [u for u in links if not u.startswith(("https://", "http://", "mailto:", "#", "<"))]
    assert links and relative == [], relative


def test_the_first_screen_says_what_it_is_and_how_to_install():
    first = README[:README.index("\n## ", README.index("\n## ") + 1)]           # title block up to the second section
    assert "free, local reviewer and commit gate for code written by AI coding tools" in first
    assert "CI gate" not in first and "AI Code Trust Gate plan" not in first
    assert "AI Code Trust Gate plan" in README[README.index("## Background"):]
    for what in ("Fake packages", "Weakened tests", "Invented APIs", "Leaked secrets", "Unsafe shortcuts"):
        assert f"**{what}:**" in first
    for cmd in ("pip install aigate-lite", "aigate-lite setup", "aigate-lite review"):
        assert cmd in first
    assert "img.shields.io/pypi/v/aigate-lite" in first and "img.shields.io/pypi/pyversions/aigate-lite" in first


def test_the_aigate_alias_is_not_documented():
    assert not re.search(r"`aigate`(?!-)", README) and "commands `aigate-lite` and `aigate`" not in README


def test_every_python3_command_has_a_windows_form_nearby():
    lines = README.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"\bpython3 (?:-|/|\.|bench|aigate)", line):
            near = "\n".join(lines[i:i + 5])
            assert "Windows" in near or re.search(r"\bpy (?:-|\.|[A-Z]:)", near), (i + 1, line)


def test_troubleshooting_names_the_common_problems():
    part = README[README.index("## Troubleshooting"):]
    part = part[:part.index("\n## ", 1)]
    for want in ("command not found", "py -m aigate_lite", "needs Python 3.11+", "SSLKEYLOGFILE", "HTTPS_PROXY",
                 "--offline", "husky", "pre-commit framework", "AIGATE_CACHE_DIR"):
        assert want in part, want


def test_the_measured_accuracy_section_is_generated_from_the_scoreboard():
    import subprocess
    import sys
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_accuracy.py"), "--check"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "## Measured accuracy" in README and "scripts/gen_accuracy.py" in README
