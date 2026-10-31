import json
import re
from pathlib import Path

from conftest import ROOT

AWS = "AKIA" + "QWERTYUIOPASDFGH"                 # split so this file is not itself a finding


def assert_minimal_sarif(doc):
    """The parts of the SARIF 2.1.0 schema that GitHub code scanning requires."""
    assert doc["version"] == "2.1.0" and "sarif-2.1.0" in doc["$schema"]
    assert isinstance(doc["runs"], list) and len(doc["runs"]) == 1
    run = doc["runs"][0]
    driver = run["tool"]["driver"]
    assert driver["name"] == "aigate-lite" and isinstance(driver["version"], str)
    rule_ids = [r["id"] for r in driver["rules"]]
    assert len(rule_ids) == len(set(rule_ids))
    for r in driver["rules"]:
        assert r["shortDescription"]["text"] and r["defaultConfiguration"]["level"] in ("error", "warning", "note")
    for res in run["results"]:
        assert res["ruleId"] in rule_ids and rule_ids[res["ruleIndex"]] == res["ruleId"]
        assert res["level"] in ("error", "warning", "note", "none")
        assert isinstance(res["message"]["text"], str) and res["message"]["text"]
        for loc in res.get("locations", []):
            phys = loc["physicalLocation"]
            assert not phys["artifactLocation"]["uri"].startswith(("/", "\\")) and "\\" not in phys["artifactLocation"]["uri"]
            if "region" in phys:
                assert isinstance(phys["region"]["startLine"], int) and phys["region"]["startLine"] >= 1
        assert all(isinstance(v, str) for v in res.get("partialFingerprints", {}).values())


def sarif(repo, *args):
    proc = repo.run("check", "--format", "sarif", *args)
    assert proc.returncode in (0, 1), proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def test_sarif_output_for_findings(repo):
    repo.write("src/keys.py", f'K = "{AWS}"\nx = eval(y)\n')
    repo.add()
    code, doc = sarif(repo)
    assert_minimal_sarif(doc)
    results = doc["runs"][0]["results"]
    assert code == 1 and {r["ruleId"] for r in results} >= {"secrets.hardcoded", "security.eval"}
    hard = [r for r in results if r["ruleId"] == "secrets.hardcoded"][0]
    assert hard["level"] == "error" and hard["locations"][0]["physicalLocation"]["region"]["startLine"] == 1
    assert AWS not in json.dumps(doc)                        # never the secret itself


def test_sarif_output_for_clean_code(repo):
    repo.write("src/ok.py", "def add(a, b):\n    return a + b\n")
    repo.add()
    code, doc = sarif(repo)
    assert_minimal_sarif(doc)
    assert code == 0 and doc["runs"][0]["results"] == []


def test_sarif_allowed_findings_are_suppressed_and_paths_are_uri_safe(repo):
    repo.write(".aigate/lite.toml", '[[allow]]\nrule = "secrets.generic"\npath = "fix tures/**"\nreason = "fake"\n')
    repo.commit()
    repo.write("fix tures/ünï.py", 'token = "Zx9kQ2vB7mN4pL1w"\n')
    repo.add()
    code, doc = sarif(repo)
    assert_minimal_sarif(doc)
    res = [r for r in doc["runs"][0]["results"] if r["ruleId"] == "secrets.generic"][0]
    assert res["suppressions"][0]["kind"] == "external"
    assert res["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "fix%20tures/%C3%BCn%C3%AF.py"


def test_github_actions_example_pins_actions_by_sha():
    text = (ROOT / "examples" / "github-actions.yml").read_text(encoding="utf-8")
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", text, re.M)
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", u) for u in uses), uses
    assert "--format sarif" in text and "--format github" in text
    assert "permissions:" in text and "security-events: write" in text
    assert "continue-on-error: true" in text and '--config "$RUNNER_TEMP/lite.toml"' in text
    assert "$CONFIG" not in text                               # always the base config, always quoted


def test_gitlab_example_exists():
    text = Path(ROOT / "examples" / "gitlab-ci.yml").read_text(encoding="utf-8")
    assert "aigate_lite.py check --range" in text and "CI_MERGE_REQUEST_TARGET_BRANCH_NAME" in text


def test_sarif_repo_wide_findings_still_have_a_location(repo):
    repo.write(".aigate/lite.toml", "[limits]\ndiff_files = 1\n")
    repo.commit()
    repo.write("a.py", "x = 1\n")
    repo.write("b.py", "y = 2\n")
    repo.add()
    _, doc = sarif(repo)
    assert_minimal_sarif(doc)
    budget = [r for r in doc["runs"][0]["results"] if r["ruleId"] == "size.diff_budget"][0]
    assert budget["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == ".aigate/lite.toml"
    assert all(r.get("locations") for r in doc["runs"][0]["results"])
