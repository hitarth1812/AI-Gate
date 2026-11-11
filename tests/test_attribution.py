import json
import re
import shutil
import subprocess
import sys

from conftest import ROOT, SCRIPT, aigate, decode_output

AWS = "AKIA" + "QWERTYUIOPASDFGH"                 # split so this file is not itself a finding
AUTHOR = "Hitarth Khatiwala"


def stripped_copy(tmp_path, *, header=True, author=True):
    """A copy of the gate without its copyright header (and optionally with another __author__)."""
    text = SCRIPT.read_text(encoding="utf-8")
    if not header:
        text = "\n".join(line for line in text.split("\n")[:12] if not line.startswith("#") or line.startswith("#!")) \
            + "\n" + "\n".join(text.split("\n")[12:])
    if not author:
        text = text.replace(f'__author__ = "{AUTHOR}"', '__author__ = "Someone Else"')
    dest = tmp_path / "copy" / "aigate_lite.py"
    dest.parent.mkdir()
    dest.write_text(text, encoding="utf-8", newline="\n")
    return dest


def run_copy(repo, script, *args):
    proc = subprocess.run([sys.executable, str(script), *args], cwd=repo.path, env=repo.env, capture_output=True)
    return proc.returncode, decode_output(proc.stdout, "utf-8"), decode_output(proc.stderr, "utf-8")


# ---------------------------------------------------------------- visible credit

def test_version_names_the_author_and_url(repo):
    out = repo.run("--version").stdout.strip()
    assert out.startswith(f"aigate-lite {aigate.__version__}") and len(out.splitlines()) == 1
    assert aigate.__author__ in out and aigate.__url__ in out and "Apache-2.0" in out


def test_help_ends_with_the_attribution_line(repo):
    assert repo.run("--help").stdout.rstrip().endswith(aigate.ATTRIBUTION)


def test_module_constants():
    assert (aigate.__author__, aigate.__license__) == (AUTHOR, "Apache-2.0")
    assert aigate.__url__ == "https://github.com/hitarth1812/AI-Gate"
    assert aigate.ATTRIBUTION == f"aigate-lite by {AUTHOR} · Apache-2.0 · {aigate.__url__}"


def test_header_has_the_spdx_and_copyright_lines():
    head = SCRIPT.read_text(encoding="utf-8").split("\n")[:6]
    assert "# SPDX-License-Identifier: Apache-2.0" in head
    assert f"# Copyright (c) 2026 {AUTHOR} (https://github.com/hitarth1812)" in head


def test_notice_and_license_name_the_author():
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert AUTHOR in notice and "https://github.com/hitarth1812/AI-Gate" in notice
    assert f"Copyright 2026 {AUTHOR}" in (ROOT / "LICENSE").read_text(encoding="utf-8")


def test_pyproject_metadata():
    import tomllib
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = meta["project"]
    assert project["authors"][0]["name"] == AUTHOR and project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE", "NOTICE"]
    assert project["scripts"] == {"aigate-lite": "aigate_lite:main", "aigate": "aigate_lite:main"}
    assert project["urls"]["Repository"] == aigate.__url__


def test_explain_list_and_doctor_text_start_with_the_credit(repo):
    assert repo.run("explain").stdout.splitlines()[0] == aigate.ATTRIBUTION
    assert repo.run("doctor", "--offline").stdout.splitlines()[0] == aigate.ATTRIBUTION


def test_setup_ends_with_the_credit(repo):
    assert repo.run("setup").stdout.rstrip().endswith(aigate.ATTRIBUTION)


# ---------------------------------------------------------------- machine formats

def test_sarif_carries_organization_and_information_uri(repo):
    driver = json.loads(repo.run("audit", "--offline", "--format", "sarif").stdout)["runs"][0]["tool"]["driver"]
    assert driver["name"] == "aigate-lite" and driver["version"] == aigate.__version__
    assert driver["organization"] == AUTHOR and driver["informationUri"] == aigate.__url__


def test_json_adds_tool_info_without_touching_existing_keys(repo):
    data = json.loads(repo.run("audit", "--offline", "--format", "json").stdout)
    assert data["tool_info"] == {"name": "aigate-lite", "version": aigate.__version__, "author": AUTHOR,
                                 "license": "Apache-2.0", "url": aigate.__url__}
    assert data["tool"] == "aigate-lite" and data["version"] == aigate.__version__ and data["schema"] == 1
    for key in ("label", "provenance", "config", "verdict", "escalated", "risk", "exit_code", "stats", "notes",
                "findings"):
        assert key in data


def test_the_credit_is_one_visible_line_in_a_text_report_with_findings_and_never_elsewhere(repo):
    # Phase 7A replaced "normal reports stay free of the credit": one footer line when a finding is printed
    repo.write("a.py", f'KEY = "{AWS}"\n')
    repo.add()
    out = repo.run("check", "--offline", "--color", "never").stdout
    assert out.count(AUTHOR) == 1 and out.rstrip().splitlines()[-1] in (aigate.ATTRIBUTION,
                                                                       aigate.credit_line(aigate.SYMBOLS_ASCII))
    for args in (("check", "--offline", "-q"), ("check", "--offline", "--format", "github")):
        got = repo.run(*args).stdout
        assert AUTHOR not in got and "github.com/hitarth1812" not in got, args


def test_agent_format_keeps_its_first_line_and_end_and_gains_one_tool_line():
    res = aigate.Result([], "pass", False, 0, ("human", "default"), "staged", {"files": 0, "added": 0, "removed": 0,
                                                                              "seconds": 0}, "defaults", [])
    assert aigate.render_agent(res) == (f"AIGATE verdict=pass risk=0 exit=0\n"
                                        f"TOOL aigate-lite {aigate.__version__} {aigate.__url__}\nEND\n")


# ---------------------------------------------------------------- vendored copy

def test_install_vendor_copies_the_notice(repo):
    assert repo.run("install", "--vendor").returncode == 0
    assert (repo.path / ".aigate" / "NOTICE").read_text(encoding="utf-8") == (ROOT / "NOTICE").read_text(encoding="utf-8")


def test_install_vendor_without_a_notice_next_to_the_script_is_silent(repo, tmp_path):
    script = stripped_copy(tmp_path)
    code, out, err = run_copy(repo, script, "install", "--vendor")
    assert code == 0 and "Traceback" not in err
    assert not (repo.path / ".aigate" / "NOTICE").exists()


# ---------------------------------------------------------------- soft tamper notice

def doctor_checks(repo, script):
    code, out, err = run_copy(repo, script, "doctor", "--offline", "--format", "json")
    return code, {c["id"]: c for c in json.loads(out)}


def test_doctor_attribution_is_ok_for_the_real_file(repo):
    code, by_id = doctor_checks(repo, SCRIPT)
    assert by_id["attribution"]["status"] == "ok"


def test_doctor_warns_when_the_header_is_missing(repo, tmp_path):
    real_code, _ = doctor_checks(repo, SCRIPT)
    code, by_id = doctor_checks(repo, stripped_copy(tmp_path, header=False))
    assert by_id["attribution"]["status"] == "warn"
    assert "attribution notice missing or changed" in by_id["attribution"]["detail"]
    assert "Apache-2.0" in by_id["attribution"]["fix"] and "NOTICE" in by_id["attribution"]["fix"]
    assert code == real_code                                          # a WARN never changes the exit code


def test_doctor_warns_when_the_author_was_changed(repo, tmp_path):
    _, by_id = doctor_checks(repo, stripped_copy(tmp_path, author=False))
    assert by_id["attribution"]["status"] == "warn"


def test_check_results_and_exit_codes_do_not_depend_on_the_attribution(repo, tmp_path):
    clean = stripped_copy(tmp_path, header=False, author=False)
    repo.write("a.py", f'KEY = "{AWS}"\n')
    repo.add()
    real = run_copy(repo, SCRIPT, "check", "--format", "json", "--offline")
    copy = run_copy(repo, clean, "check", "--format", "json", "--offline")
    assert real[0] == copy[0] == 1
    strip = lambda text: {k: v for k, v in json.loads(text).items()                      # noqa: E731
                          if k not in ("stats", "tool_info", "author")}                  # author: the credit itself
    left, right = strip(real[1]), strip(copy[1])
    left.pop("version", None), right.pop("version", None)
    assert left == right
    def timeless(result):                                   # the text report ends with its elapsed time ("0.2s")
        return result[0], re.sub(r"\b\d+\.\d+s\b", "<time>", result[1])
    for args in (("check", "--offline", "-q"), ("check", "--format", "agent", "--offline")):
        assert timeless(run_copy(repo, SCRIPT, *args)) == timeless(run_copy(repo, clean, *args))
    shutil.rmtree(tmp_path / "copy")
