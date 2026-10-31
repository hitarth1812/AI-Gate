import json
import subprocess

import pytest

from conftest import ROOT, GitRepo, find

DOC_SUFFIXES = (".md", ".toml", ".yml", ".yaml")
KEY_HEADER = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"     # split so this file is not itself a finding


def tracked_files():
    proc = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.skip("not a git checkout")
    return [p for p in proc.stdout.splitlines() if (ROOT / p).is_file()]


def doc_files():
    return [p for p in tracked_files() if p.endswith(DOC_SUFFIXES)]


def scan_copy(tmp_path, files, extra=None):
    (tmp_path / "repo").mkdir()
    repo = GitRepo(tmp_path / "repo", tmp_path / "cache")
    for rel in files:
        dest = repo.path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / rel).read_bytes())
    for rel, text in (extra or {}).items():
        repo.write(rel, text)
    repo.commit("docs")
    proc = repo.run("check", "--all", "--offline", "--format", "json")
    assert proc.returncode in (0, 1), proc.stderr + proc.stdout
    return json.loads(proc.stdout)


def test_repo_docs_contain_no_hardcoded_secrets(tmp_path):
    res = scan_copy(tmp_path, doc_files())
    assert [(f["path"], f["line"]) for f in find(res, "secrets.hardcoded")] == []


def test_whole_tracked_tree_contains_no_hardcoded_secrets(tmp_path):
    # the tool's own regex table and the tests' fixtures included: fixtures must be built at run time
    res = scan_copy(tmp_path, tracked_files())
    assert [(f["path"], f["line"]) for f in find(res, "secrets.hardcoded")] == []


def test_a_key_planted_in_a_copied_doc_is_found(tmp_path):
    # control: proves the scan above really reads the copied docs
    res = scan_copy(tmp_path, doc_files(), {"docs/notes.md": f"see `{KEY_HEADER}`\n"})
    assert "docs/notes.md" in [f["path"] for f in find(res, "secrets.hardcoded")]


def test_doc_set_covers_progress_readme_and_examples():
    files = doc_files()
    assert {"PROGRESS.md", "README.md", "CHANGELOG.md", "pyproject.toml"} <= set(files)
    assert any(p.startswith("examples/") for p in files)
