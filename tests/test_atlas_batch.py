"""The fix batch after the ATLAS trial: the private-key body check, the "core" default for people, tests.missing_for_change
and entry points, the version / doctor hash, and the README rule count.

Markers the repository's own self-scan looks for are built from pieces so this file is not itself a finding."""
import json
import re
from pathlib import Path

import pytest

from conftest import aigate, find
from test_review_api import AGENT, base, change, deep, hits
import precision_fixtures as fx

ROOT = Path(__file__).resolve().parents[1]
EC_BEGIN = "-----BEGIN " + "EC PRIVATE KEY-----"
EC_END = "-----END " + "EC PRIVATE KEY-----"
RSA_BEGIN = "-----BEGIN " + "RSA PRIVATE KEY-----"
KEY = fx.KEY_LINES[:3]                      # three real-looking 64-character base64 lines
ACTIONS = {"block": 4, "fix": 3, "warn": 2, "pass": 1}


def stage(repo, files, env=None):
    for path, text in files.items():
        repo.write(path, text)
    repo.add()
    return repo.check("--offline", env=env)


def keys(res):
    return [f for f in find(res, "secrets.hardcoded") if not f["allowed"]]


# ------------------------------------------------------------------------------------------------ 1. the PEM body check

ATLAS_COMMENT = ("package x\n\n"
                 "// parseECPrivateKey decodes a PEM-encoded EC private key\n"
                 '// "' + EC_BEGIN + '\\n..." and returns the parsed key. The input must be\n'
                 "// produced by an earlier call to the signing helper in this package, or the call fails.\n"
                 "func parseECPrivateKey(b []byte) (*Key, error) { return nil, nil }\n")


@pytest.mark.parametrize("env", [None, AGENT], ids=["person", "agent"])
def test_a_go_doc_comment_that_talks_about_a_pem_key_is_not_a_key(repo, env):
    assert not keys(stage(repo, {"keys.go": ATLAS_COMMENT}, env=env))


@pytest.mark.parametrize("prose", [
    "// The block starts at the BEGIN line and every following line is wrapped at sixtyfour characters.",
    "// ThisIsOneLongCamelCaseIdentifierWithoutSpaces and AnotherVeryLongIdentifierName1234567890abcdefgh",
    "// see parseECPrivateKey above for how the sixty-four character lines are produced",
])
def test_prose_after_the_marker_is_not_merged_into_a_body(repo, prose):
    text = 'package x\n\n// "' + EC_BEGIN + '\\n..."\n' + prose + "\n// ThisIsOneLongCamelCaseIdentifierWithoutSpaces1\nfunc f() {}\n"
    assert not keys(stage(repo, {"k.go": text}))


def test_a_marker_followed_by_short_base64_looking_words_is_not_a_key(repo):
    text = "package x\n// " + EC_BEGIN + "\n// MIIEowIBAAKCAQEA\n// is how it starts\nfunc f() {}\n"
    assert not keys(stage(repo, {"k.go": text}))


STRINGS = {
    "go raw string": ("k.go", "package x\n\nvar key = `" + EC_BEGIN + "\n" + "\n".join(KEY) + "\n`\n"),
    "go raw string with end": ("k.go", "package x\n\nvar key = `" + EC_BEGIN + "\n" + "\n".join(KEY) + "\n" + EC_END + "\n`\n"),
    "python triple quoted": ("k.py", 'KEY = """' + RSA_BEGIN + "\n" + "\n".join(KEY) + '\n"""\n'),
    "javascript template literal": ("k.js", "const key = `" + RSA_BEGIN + "\n" + "\n".join(KEY) + "\n`;\n"),
    "go concatenated strings": ("k.go", 'package x\n\nvar key = "' + EC_BEGIN + '\\n" +\n\t"' + KEY[0] + '\\n" +\n\t"'
                                + KEY[1] + '\\n" +\n\t"' + KEY[2] + '"\n'),
    "one line with escapes": ("k.go", 'package x\n\nconst key = "' + EC_BEGIN + "\\n" + "\\n".join(KEY) + '"\n'),
    "slash comment": ("k.go", "package x\n// " + EC_BEGIN + "\n" + "".join("// " + k + "\n" for k in KEY)),
    "hash comment": ("k.py", "# " + RSA_BEGIN + "\n" + "".join("# " + k + "\n" for k in KEY)),
    "star comment": ("k.js", "/*\n * " + RSA_BEGIN + "\n" + "".join(" * " + k + "\n" for k in KEY) + " */\n"),
    "encrypted key with headers": ("k.py", 'KEY = """' + RSA_BEGIN + "\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,"
                                   "3F1A9C2B7D4E5F60718293A4B5C6D7E8\n\n" + "\n".join(fx.KEY_LINES) + '\n"""\n'),
}


@pytest.mark.parametrize("name", sorted(STRINGS))
def test_a_real_key_body_still_blocks_for_a_person_and_an_agent(repo, name):
    path, text = STRINGS[name]
    person = keys(stage(repo, {path: text}))
    assert person and all(f["action"] == "block" for f in person), name
    agent = keys(repo.check("--offline", env=AGENT))
    assert agent and min(ACTIONS[f["action"]] for f in agent) >= max(ACTIONS[f["action"]] for f in person), name


def test_the_end_marker_alone_still_counts_as_a_key(repo):
    text = "package x\n// " + EC_BEGIN + "\n// MIIE\n// " + EC_END + "\n"
    assert keys(stage(repo, {"k.go": text}))


def test_a_pasted_header_in_documentation_still_counts(repo):
    assert keys(stage(repo, {"docs/keys.md": "Use a key like:\n\n    " + EC_BEGIN + "\n"}))


def test_prose_between_the_marker_and_the_body_ends_the_search(repo):
    text = "package x\nvar a = `" + EC_BEGIN + "\nthis line is prose\n" + KEY[0] + "\n" + KEY[1] + "\n`\n"
    assert not keys(stage(repo, {"k.go": text}))


def test_a_long_first_body_line_is_enough_even_if_prose_follows(repo):
    text = "package x\nvar a = `" + EC_BEGIN + "\n" + KEY[0] + "\nthis line is prose\n`\n"
    assert keys(stage(repo, {"k.go": text}))


# ------------------------------------------------------------------------------------------------ 2. the "core" default

BIG_GO = ("package x\n\nfunc big() {\n" + "\tx++\n" * 120 + "}\n\n"
          "func deep(a [][]int) {\n\tfor _, r := range a {\n\t\tfor _, c := range r {\n\t\t\tfor _, d := range c {\n"
          "\t\t\t\t_ = d\n\t\t\t}\n\t\t}\n\t}\n}\n")
BLOCK = "".join(f"\tv{i} := step({i})\n" for i in range(8))
DUPES = {"a.go": "package a\n\nfunc a() {\n" + BLOCK + "}\n", "b.go": "package a\n\nfunc b() {\n" + BLOCK + "}\n"}
CORE = ("size.function_length", "perf.nested_loops", "code.duplicate_block")


def active(res, *rules):
    return sorted({f["rule"] for f in res["findings"] if f["rule"] in rules and not f["allowed"]})


def test_size_perf_and_duplicates_are_off_for_a_persons_change_but_not_an_agents(repo):
    res = stage(repo, {"big.go": BIG_GO, **DUPES})
    assert active(res, *CORE) == []
    assert active(repo.check("--offline", env=AGENT), *CORE) == sorted(CORE)


def test_a_rules_entry_turns_a_core_rule_on_for_people(repo):
    repo.write(".aigate/lite.toml", '[rules]\n"size.*" = "warn"\n')
    res = stage(repo, {"big.go": BIG_GO, **DUPES})
    assert active(res, *CORE) == ["size.function_length"]                       # only what the entry names


@pytest.mark.parametrize("entry, want", [
    ('"perf.nested_loops" = "warn"', ["perf.nested_loops"]),
    ('"code.duplicate_block" = "warn"', ["code.duplicate_block"]),
    ('"perf.*" = "fix"', ["perf.nested_loops"]),
    ('"size.function_length" = "off"', []),
])
def test_an_explicit_setting_wins_in_both_directions(repo, entry, want):
    repo.write(".aigate/lite.toml", "[rules]\n" + entry + "\n")
    assert active(stage(repo, {"big.go": BIG_GO, **DUPES}), *CORE) == want


def test_the_strict_preset_turns_them_on_for_people(repo):
    repo.write(".aigate/lite.toml", 'preset = "strict"\n')
    assert active(stage(repo, {"big.go": BIG_GO, **DUPES}), *CORE) == sorted(CORE)


def test_the_relaxed_preset_keeps_them_off(repo):
    repo.write(".aigate/lite.toml", 'preset = "relaxed"\n')
    assert active(stage(repo, {"big.go": BIG_GO, **DUPES}), *CORE) == []


def test_check_strict_gives_a_person_the_agents_rule_set(repo):
    stage(repo, {"big.go": BIG_GO, **DUPES})
    assert active(repo.check("--offline", "--strict"), *CORE) == sorted(CORE)


def test_audit_strict_reports_them_and_audit_does_not(repo):
    repo.write("big.go", BIG_GO)
    repo.commit("add")
    plain = json.loads(repo.run("audit", "--offline", "--format", "json").stdout)
    strict = json.loads(repo.run("audit", "--offline", "--strict", "--format", "json").stdout)
    assert active(plain, *CORE) == [] and active(strict, *CORE) == ["perf.nested_loops", "size.function_length"]


def test_audit_summary_counts_drop_for_a_person(repo):
    repo.write("big.go", BIG_GO)
    repo.write("a.go", DUPES["a.go"])
    repo.write("b.go", DUPES["b.go"])
    repo.commit("add")
    plain = json.loads(repo.run("audit", "--offline", "--format", "json").stdout)
    strict = json.loads(repo.run("audit", "--offline", "--strict", "--format", "json").stdout)
    n = lambda r: sum(1 for f in r["findings"] if not f["allowed"])     # noqa: E731
    assert n(strict) - n(plain) == len([f for f in strict["findings"] if f["rule"] in CORE and not f["allowed"]]) > 0


def test_the_baseline_still_records_them_so_an_agents_change_is_not_flooded(repo):
    repo.write("big.go", BIG_GO)
    repo.commit("add")
    assert repo.run("baseline").returncode == 0
    entries = json.loads((repo.path / ".aigate" / "baseline.json").read_text(encoding="utf-8"))["entries"]
    assert {e["rule"] for e in entries} >= {"size.function_length", "perf.nested_loops"}


def test_explain_and_init_say_that_the_rules_are_off_for_people(repo, capsys):
    assert aigate.main(["explain", "size.function_length"]) == 0
    assert "off for a person's change by default" in capsys.readouterr().out
    assert aigate.main(["explain", "secrets.hardcoded"]) == 0
    assert "off for a person's change" not in capsys.readouterr().out
    assert "size.*" in aigate.init_text("balanced", aigate.ProjectInfo([], [], [], [], []))


def test_the_core_set_is_exactly_size_perf_and_duplicate_block():
    assert aigate.CORE_OFF_RULES == {r for r in aigate.RULES if r.startswith(("size.", "perf."))} | {"code.duplicate_block"}
    assert {"size.function_length", "size.complexity", "size.file_length", "size.diff_budget", "perf.nested_loops",
            "perf.quadratic_pattern", "perf.call_in_loop", "perf.exponential_recursion", "code.duplicate_block"} \
        == aigate.CORE_OFF_RULES


# ------------------------------------------------------------------------------------------------ 3. entry points

GO_MOD = "module example.com/app\n\ngo 1.22\n"
GO_ENTRY = ("package main\n\nimport \"fmt\"\n\nfunc init() {\n\tfmt.Println(\"init\")\n}\n\nfunc main() {\n\tfmt.Println(\"hi\")\n}\n\n"
            "func helper(x int) int {\n\treturn x\n}\n")


def test_go_main_and_init_need_no_test(repo):
    base(repo, {"go.mod": GO_MOD, "main.go": GO_ENTRY})
    change(repo, {"main.go": GO_ENTRY.replace('"init"', '"init 2"').replace('"hi"', '"hello"')})
    assert not hits(deep(repo), "tests.missing_for_change")
    assert not hits(deep(repo, AGENT), "tests.missing_for_change")


def test_a_changed_go_helper_next_to_main_still_needs_one(repo):
    base(repo, {"go.mod": GO_MOD, "main.go": GO_ENTRY})
    change(repo, {"main.go": GO_ENTRY.replace('"hi"', '"hello"').replace("return x", "return x + 1")})
    res = deep(repo)
    assert hits(res, "tests.missing_for_change") == [("main.go", 13)]
    assert "`helper`" in [f for f in res["findings"] if f["rule"] == "tests.missing_for_change"][0]["message"]


def test_a_function_called_main_in_another_language_is_still_judged(repo):
    src = "def main():\n    return 1\n"
    base(repo, {"app/__init__.py": "", "app/cli.py": src})
    change(repo, {"app/cli.py": src.replace("1", "2")})
    assert hits(deep(repo), "tests.missing_for_change") == [("app/cli.py", 1)]


MAIN_PY = ("def work(x):\n    return x\n\n\nif __name__ == \"__main__\":\n    def run():\n        return work(1)\n\n    run()\n")


def test_a_python_main_guard_block_needs_no_test(repo):
    base(repo, {"app/__init__.py": "", "app/cli.py": MAIN_PY})
    change(repo, {"app/cli.py": MAIN_PY.replace("work(1)", "work(2)").replace("    run()", "    run()\n    print('done')")})
    assert not hits(deep(repo), "tests.missing_for_change")


def test_a_python_function_outside_the_main_guard_is_still_judged(repo):
    base(repo, {"app/__init__.py": "", "app/cli.py": MAIN_PY})
    change(repo, {"app/cli.py": MAIN_PY.replace("return x", "return x + 1").replace("work(1)", "work(2)")})
    assert hits(deep(repo), "tests.missing_for_change") == [("app/cli.py", 1)]


def test_a_string_or_comment_that_looks_like_a_main_guard_does_not_hide_a_function(repo):
    src = '# if __name__ == "__main__":\n\ndef work(x):\n    return x\n\n\nDOC = \'if __name__ == "__main__":\'\n'
    base(repo, {"app/__init__.py": "", "app/cli.py": src})
    change(repo, {"app/cli.py": src.replace("return x", "return x * 2")})
    assert hits(deep(repo), "tests.missing_for_change") == [("app/cli.py", 3)]


# ------------------------------------------------------------------------------------------------ 4. version and doctor

def test_the_version_matches_the_changelog():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    first = re.search(r"^## \[?v?(\d+\.\d+\.\d+)", changelog, re.M).group(1)
    assert aigate.__version__ == first == "0.5.2"


def doctor_version(repo):
    out = json.loads(repo.run("doctor", "--offline", "--format", "json").stdout)
    return next(c for c in out if c["id"] == "version")


def test_doctor_says_the_vendored_copy_matches_only_when_the_code_does(repo):
    assert repo.run("install", "--vendor").returncode == 0
    ok = doctor_version(repo)
    assert ok["status"] == "ok" and "vendored copy matches" in ok["detail"] and "sha256" in ok["detail"]


def test_doctor_warns_when_the_vendored_copy_has_the_same_version_but_other_code(repo):
    assert repo.run("install", "--vendor").returncode == 0
    copy = repo.path / ".aigate" / "aigate_lite.py"
    copy.write_bytes(copy.read_bytes() + b"\n# a different build with the same version number\n")
    bad = doctor_version(repo)
    assert bad["status"] == "warn" and "contents differ" in bad["detail"] and "install --vendor" in bad["fix"]


def test_doctor_ignores_line_endings_when_comparing(repo):
    assert repo.run("install", "--vendor").returncode == 0
    copy = repo.path / ".aigate" / "aigate_lite.py"
    copy.write_bytes(copy.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert doctor_version(repo)["status"] == "ok"


def test_doctor_still_reports_a_different_version(repo):
    assert repo.run("install", "--vendor").returncode == 0
    copy = repo.path / ".aigate" / "aigate_lite.py"
    copy.write_text(copy.read_text(encoding="utf-8").replace(f'__version__ = "{aigate.__version__}"', '__version__ = "0.1.0"', 1),
                    encoding="utf-8")
    got = doctor_version(repo)
    assert got["status"] == "warn" and "0.1.0" in got["detail"]


# ------------------------------------------------------------------------------------------------ 5. README

def test_the_readme_rule_count_matches_explain():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    counts = {int(n) for n in re.findall(r"lists all (\d+) rules", readme)}
    assert counts == {len(aigate.RULES)}
