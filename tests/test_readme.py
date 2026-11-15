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


def _accuracy_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_accuracy", ROOT / "scripts" / "gen_accuracy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BOARD = {"tool_version": "9.9.9", "python": "3.12.0",
         "train": {"total_cases": 4, "cases": {"real": 4}, "rules": {"x.y": {"tp": 4, "fp": 0, "fn": 0, "over": 0, "under": 0,
                                                                        "precision": 1.0, "recall": 1.0}},
                   "overall": {"false_block_clean": 0, "unexpected_block": 0, "crashes": 0, "gate_errors": 0, "agent_softer": 0,
                               "fn": 0, "fp": 0, "over": 0, "under": 0}},
         "holdout": {"total_cases": 10, "failing_cases": 3, "failures": {"FN": 4, "OVER": 1}, "overall": {"agent_softer": 0}}}


def test_the_accuracy_section_leads_with_the_holdout():
    text = _accuracy_module().render(BOARD)
    assert text.index("Holdout (cases the tool was never tuned on): 7 of 10 cases pass; missed 4, too strict 1") < text.index("Training cases")
    assert "### Per rule: training cases (the tool was tuned on these)" in text
    assert text.index("Holdout") < text.index("| Rule |")


def test_the_accuracy_section_in_the_readme_leads_with_the_holdout():
    part = README[README.index("## Measured accuracy"):]
    assert part.index("Holdout (cases the tool was never tuned on)") < part.index("Per rule: training cases (the tool was tuned on these)")


def test_security_policy_exists_and_the_readme_links_to_it():
    policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "SECURITY.md" in README and "/blob/main/SECURITY.md" in README
    for want in ("security/advisories/new", "aigate-lite security", "latest minor", "7 days", "no bug bounty", "--no-verify",
                 "aigate-lite --version", "do not open a public issue"):
        assert want.lower() in policy.lower(), want


def test_the_security_contact_appears_only_in_the_security_policy():
    contact = "hitarthkhatiwala" + "@gmail.com"
    assert contact in (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    for name in ("README.md", "pyproject.toml", "CONTRIBUTING.md", "CHANGELOG.md", "aigate_lite.py"):
        assert contact not in (ROOT / name).read_text(encoding="utf-8"), name
