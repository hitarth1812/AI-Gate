"""Phase 7A: visible credit in every output, additive only. One footer line under a text report that prints a finding
(never on a clean pass, never with -q, never in --format github), always under `review` text, a last line in the
markdown review, `author` and `url` in the JSON, one TOOL line before END in the agent format, and a pinned SARIF driver.

Markers the repository's own self-scan looks for are built from pieces so this file is not itself a finding."""
import json
import unicodedata

import pytest

from conftest import ROOT, aigate, project_files
from test_review_api import base, change
from test_review_rest import review

AWS = "AKIA" + "QWERTYUIOPASDFGH"
AUTHOR = "Hitarth Khatiwala"
URL = "https://github.com/hitarth1812/AI-Gate"
TOOL = f"TOOL aigate-lite {aigate.__version__} {URL}"
# a pipe with a legacy encoding gets ASCII separators, a UTF-8 terminal the middle dot: the same line either way
CREDITS = {aigate.ATTRIBUTION, aigate.credit_line(aigate.SYMBOLS_ASCII)}


def dirty(repo):
    repo.write("a.py", f'KEY = "{AWS}"\n')
    repo.add()


def clean(repo):
    repo.write("a.py", "x = 1\n")
    repo.add()


def lines(proc):
    return proc.stdout.rstrip("\n").splitlines()


# ------------------------------------------------------------------------------------------------ the text footer

@pytest.mark.parametrize("args", [("check",), ("check", "-v"), ("check", "--summary"), ("check", "--group-by", "rule"),
                                  ("check", "--max-findings", "1"), ("audit",)])
def test_text_with_a_finding_ends_with_the_credit_exactly_once(repo, args):
    dirty(repo)
    if args[0] == "audit":
        repo.commit("add")
    out = lines(repo.run(*args, "--offline", "--color", "never"))
    assert out[-1] in CREDITS and sum(ln in CREDITS for ln in out) == 1, args


def test_the_credit_comes_after_the_next_line(repo):
    dirty(repo)
    out = lines(repo.run("check", "--offline"))
    assert out[-2].startswith("next: ") and out[-1] in CREDITS


def test_a_clean_pass_has_no_credit(repo):
    clean(repo)
    proc = repo.run("check", "--offline")
    assert proc.returncode == 0 and AUTHOR not in proc.stdout and URL not in proc.stdout
    assert AUTHOR not in repo.run("check", "--offline", "-v").stdout


def test_quiet_never_prints_the_credit(repo):
    dirty(repo)
    proc = repo.run("check", "--offline", "-q")
    assert proc.returncode == 1 and AUTHOR not in proc.stdout and URL not in proc.stdout
    clean(repo)
    assert repo.run("check", "--offline", "-q").stdout == ""


def test_github_format_never_prints_the_credit(repo):
    dirty(repo)
    out = repo.run("check", "--offline", "--format", "github").stdout
    assert out.splitlines()[0].startswith("::error") and AUTHOR not in out and URL not in out


def test_only_allowed_findings_print_no_credit(repo):
    repo.write(".aigate/lite.toml", '[[allow]]\nrule = "secrets.hardcoded"\npath = "a.py"\nreason = "a test key"\n')
    repo.commit("config")                      # a changed config is itself a (warn) finding, so commit it first
    dirty(repo)
    proc = repo.run("check", "--offline")
    assert proc.returncode == 0 and AUTHOR not in proc.stdout


def test_color_wraps_the_credit_in_dim_and_plain_text_is_the_same_line(repo):
    dirty(repo)
    plain = lines(repo.run("check", "--offline", "--color", "never"))[-1]
    colored = lines(repo.run("check", "--offline", "--color", "always"))[-1]
    assert plain in CREDITS and plain in colored and colored.startswith("\x1b[")


def test_the_credit_uses_ascii_where_the_console_has_no_middle_dot():
    ascii_line = aigate.credit_line(aigate.SYMBOLS_ASCII)
    ascii_line.encode("ascii")
    assert ascii_line == f"aigate-lite by {AUTHOR} {aigate.SYMBOLS_ASCII['dot']} Apache-2.0 {aigate.SYMBOLS_ASCII['dot']} {URL}"
    assert aigate.credit_line() == aigate.ATTRIBUTION


def test_the_credit_is_not_cut_at_the_terminal_width():
    res = aigate.Result([aigate.make("security.eval", "a.py", 1, "eval() runs code built at runtime")], "fix", False, 30,
                        ("human", "default"), "staged", {"files": 1, "added": 1, "removed": 0, "seconds": 0.1}, "defaults", [])
    assert aigate.render_text(res, width=40).splitlines()[-1] == aigate.ATTRIBUTION


# ------------------------------------------------------------------------------------------------ review

def test_review_text_always_ends_with_the_credit(repo):
    base(repo, {"app/__init__.py": ""})
    change(repo, {"notes.md": "hello\n"})
    out = review(repo, fmt="text").rstrip("\n").splitlines()
    assert out[-1] in CREDITS and out[-2] == "" and sum(ln in CREDITS for ln in out) == 1
    dirty(repo)
    assert review(repo, fmt="text").rstrip("\n").splitlines()[-1] in CREDITS


def test_review_markdown_ends_with_a_reviewed_by_line(repo):
    base(repo, {"app/__init__.py": ""})
    change(repo, {"notes.md": "hello\n"})
    out = review(repo, fmt="md").rstrip("\n").splitlines()
    assert out[-1] == f"Reviewed by [aigate-lite]({URL}) {aigate.__version__}" and out[-2] == ""
    assert out[-3].startswith("**Verdict: ")


# ------------------------------------------------------------------------------------------------ JSON: additive only

CHECK_KEYS = {"tool": str, "version": str, "schema": int, "schema_version": int, "label": str, "tool_info": dict, "provenance": dict,
              "config": str, "verdict": str, "escalated": bool, "risk": (int, float), "exit_code": int, "stats": dict,
              "notes": list, "findings": list}
FINDING_KEYS = {"rule": str, "action": str, "severity": str, "confidence": float, "path": str, "line": (int, type(None)),
                "message": str, "hint": str, "fingerprint": str, "legacy_fingerprint": str, "sensitive_path": bool,
                "allowed": (str, type(None))}
REVIEW_KEYS = {"tool": str, "version": str, "schema": int, "schema_version": int, "kind": str, "label": str, "provenance": dict, "verdict": str,
               "risk": (int, float), "summary": dict, "must_fix": list, "should_check": list, "looks_good": list,
               "allowed": int, "notes": list}


def assert_types(data, spec, added):
    assert set(data) == set(spec) | set(added), sorted(set(data) ^ (set(spec) | set(added)))
    for key, kind in spec.items():
        assert isinstance(data[key], kind), (key, type(data[key]))
    for key in added:
        assert data[key] == added[key]


def test_check_json_keeps_every_key_and_type_and_adds_author_and_url(repo):
    dirty(repo)
    data = json.loads(repo.run("check", "--offline", "--format", "json").stdout)
    assert_types(data, CHECK_KEYS, {"author": AUTHOR, "url": URL})
    assert data["tool"] == "aigate-lite" and data["schema"] == 1                      # unchanged
    assert data["tool_info"] == {"name": "aigate-lite", "version": aigate.__version__, "author": AUTHOR,
                                 "license": "Apache-2.0", "url": URL}                  # unchanged
    for f in data["findings"]:
        assert set(f) == set(FINDING_KEYS) and all(isinstance(f[k], t) for k, t in FINDING_KEYS.items()), f


def test_audit_and_clean_json_carry_the_same_keys(repo):
    clean(repo)
    for args in (("check",), ("audit",)):
        if args[0] == "audit":
            repo.commit("add")
        data = json.loads(repo.run(*args, "--offline", "--format", "json").stdout)
        assert_types(data, CHECK_KEYS, {"author": AUTHOR, "url": URL})


def test_review_json_keeps_every_key_and_type_and_adds_author_and_url(repo):
    base(repo, {"app/__init__.py": ""})
    change(repo, {"notes.md": "hello\n"})
    data = review(repo)
    assert_types(data, REVIEW_KEYS, {"author": AUTHOR, "url": URL})
    assert data["schema"] == aigate.REVIEW_SCHEMA == 1 and data["kind"] == "review" and data["tool"] == "aigate-lite"


# ------------------------------------------------------------------------------------------------ the agent format

def test_agent_format_has_one_tool_line_directly_before_end(repo):
    dirty(repo)
    out = lines(repo.run("check", "--offline", "--format", "agent"))
    assert out[0].startswith("AIGATE verdict=block ") and out[-2] == TOOL and out[-1] == "END"
    assert sum(1 for ln in out if ln.startswith("TOOL ")) == 1
    clean(repo)
    assert lines(repo.run("check", "--offline", "--format", "agent")) == ["AIGATE verdict=pass risk=0 exit=0", TOOL, "END"]


def test_agent_format_with_nothing_staged_and_for_review_has_the_tool_line(repo):
    base(repo, {"app/__init__.py": ""})
    assert lines(repo.run("check", "--offline", "--format", "agent")) == ["AIGATE verdict=pass risk=0 exit=0", TOOL, "END"]
    change(repo, {"notes.md": "hello\n"})
    out = lines(repo.run("review", "--offline", "--format", "agent"))
    assert out[0].startswith("AIGATE verdict=") and out[-2] == TOOL and out[-1] == "END"


def test_the_tool_line_is_ascii_and_carries_the_running_version():
    assert aigate.agent_tool_line() == TOOL
    TOOL.encode("ascii")


# ------------------------------------------------------------------------------------------------ SARIF

def test_sarif_pins_name_version_information_uri_and_organization(repo):
    dirty(repo)
    doc = json.loads(repo.run("check", "--offline", "--format", "sarif").stdout)
    driver = doc["runs"][0]["tool"]["driver"]
    assert {k: driver[k] for k in ("name", "version", "informationUri", "organization")} == {
        "name": "aigate-lite", "version": aigate.__version__, "informationUri": URL, "organization": AUTHOR}
    assert doc["version"] == "2.1.0" and driver["rules"]


# ------------------------------------------------------------------------------------------------ nothing hidden

HIDDEN = {"\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\u2060",
          "\u2061", "\u2062", "\u2063", "\u2064", "\ufeff", "\u00ad",
          "\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066",
          "\u2067", "\u2068", "\u2069"}      # zero-width, bidi and soft-hyphen characters, written as escapes so this file stays clean


def test_no_zero_width_or_bidi_characters_in_the_tool_docs_or_outputs():
    # git ls-files in a checkout; a walk of the tree in an unpacked sdist or zip (the check runs in both)
    paths = project_files((".py", ".md", ".toml", ".yml", ".txt"), names=("NOTICE", "LICENSE"))
    assert "aigate_lite.py" in paths and "NOTICE" in paths
    bad = []
    for rel in paths:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        bad += [f"{rel}: U+{ord(c):04X}" for c in sorted(set(text) & HIDDEN)]
    assert bad == []
    for text in (aigate.ATTRIBUTION, aigate.agent_tool_line()):
        assert not any(unicodedata.category(c) == "Cf" for c in text)
