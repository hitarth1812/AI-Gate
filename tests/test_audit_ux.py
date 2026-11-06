"""`check --all` for an existing repository: --summary, --group-by, --max-findings and the pointer to a baseline."""
import json
import re

import pytest

from conftest import GitRepo
import precision_fixtures as fx


@pytest.fixture(scope="module")
def audit_repo(tmp_path_factory):
    base = tmp_path_factory.mktemp("audit")
    (base / "repo").mkdir()
    repo = GitRepo(base / "repo", base / "cache")
    repo.write("README.md", "# demo\n")
    for rel, text in fx.FILES.items():
        repo.write(rel, text)
    repo.commit("everything")
    return repo


def text(repo, *args):
    proc = repo.run("check", "--all", "--offline", "--color", "never", *args)
    assert proc.returncode in (0, 1), proc.stderr
    return proc


def test_summary_counts_per_rule_per_directory_and_per_file(audit_repo):
    out = text(audit_repo, "--summary").stdout
    for heading in ("by rule", "by directory", "files with the most findings"):
        assert heading in out
    rule_rows = out.split("by rule\n", 1)[1].split("by directory", 1)[0].strip().splitlines()
    counts = [int(row.split()[0]) for row in rule_rows]
    assert counts == sorted(counts, reverse=True) and any("tests.skip_added" in row for row in rule_rows)
    assert "pkg" in out.split("by directory\n", 1)[1]
    assert "pkg/skip_test.go:" not in out                      # the findings themselves are not listed


def test_summary_counts_match_the_full_json_report(audit_repo):
    full = json.loads(audit_repo.run("check", "--all", "--offline", "--format", "json").stdout)
    active = [f for f in full["findings"] if not f["allowed"]]
    summary = json.loads(audit_repo.run("check", "--all", "--offline", "--format", "json", "--summary").stdout)["summary"]
    assert sum(r["count"] for r in summary["by_rule"]) == len(active)
    assert sum(d["count"] for d in summary["by_directory"]) == len(active)
    assert summary["top_files"][0]["count"] == max(sum(1 for f in active if f["path"] == p) for p in {f["path"] for f in active})


def test_the_baseline_pointer_closes_every_audit_report(audit_repo):
    for args in ((), ("--summary",), ("--group-by", "rule"), ("--max-findings", "2")):
        last = text(audit_repo, *args).stdout.splitlines()[-1]
        assert last.startswith("next: ") and "aigate-lite baseline" in last, (args, last)


def test_group_by_rule_lists_the_strictest_rule_first(audit_repo):
    out = text(audit_repo, "--group-by", "rule").stdout
    headers = [ln for ln in out.splitlines() if ln and not ln.startswith((" ", "aigate-lite", "next:")) and "(" in ln]
    assert headers and headers[0].startswith("secrets.")
    assert any(ln.startswith("  BLOCK") and ".pem" in ln for ln in out.splitlines())


def test_max_findings_cuts_the_list_but_not_the_verdict(audit_repo):
    full = text(audit_repo)
    cut = text(audit_repo, "--max-findings", "3")
    shown = [ln for ln in cut.stdout.splitlines() if ln.startswith("  ") and ln.split()[1:2] and ln.split()[1] in ("BLOCK", "FIX", "WARN")]
    assert len(shown) == 3 and "more" in cut.stdout and "--max-findings" in cut.stdout
    assert cut.returncode == full.returncode
    def verdict(out):                                                           # the one-line verdict, minus the elapsed time
        return re.sub(r"\d+(?:\.\d+)?s\b", "Ns", out.splitlines()[-2])
    assert verdict(cut.stdout) == verdict(full.stdout)


def test_max_findings_in_json_says_how_many_were_left_out(audit_repo):
    data = json.loads(audit_repo.run("check", "--all", "--offline", "--format", "json", "--max-findings", "2").stdout)
    assert len(data["findings"]) == 2 and data["truncated"] > 0 and data["verdict"] == "block"


def test_audit_takes_the_same_flags(audit_repo):
    proc = audit_repo.run("audit", "--offline", "--color", "never", "--summary")
    assert proc.returncode in (0, 1) and "by directory" in proc.stdout


def test_default_text_output_is_otherwise_unchanged(audit_repo):
    out = text(audit_repo).stdout
    assert "by rule" not in out and "pkg/skip_test.go" in out and "more" not in out
