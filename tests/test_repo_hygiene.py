import json

from conftest import ROOT, GitRepo, find, project_files

DOC_SUFFIXES = (".md", ".toml", ".yml", ".yaml")
KEY_HEADER = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"     # split so this file is not itself a finding


def tracked_files():
    """git ls-files in a checkout; the walked tree in an unpacked sdist or zip (0.5.1: no longer skipped there)."""
    return project_files()


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
    # like the real self-scan: offline and without the test registry fixture (it lacks e.g. setuptools)
    proc = repo.run("check", "--all", "--offline", "--format", "json", env={"AIGATE_REGISTRY_FIXTURE": ""})
    assert proc.returncode in (0, 1), proc.stderr + proc.stdout
    return json.loads(proc.stdout)


def test_repo_docs_contain_no_hardcoded_secrets(tmp_path):
    res = scan_copy(tmp_path, doc_files())
    assert [(f["path"], f["line"]) for f in find(res, "secrets.hardcoded")] == []


def test_whole_tracked_tree_contains_no_hardcoded_secrets(tmp_path):
    # the tool's own regex table and the tests' fixtures included: fixtures must be built at run time
    res = scan_copy(tmp_path, tracked_files())
    assert [(f["path"], f["line"]) for f in find(res, "secrets.hardcoded")] == []


def test_self_scan_has_no_unallowed_fix_or_block_findings(tmp_path):
    # intentional findings in this repo are allowed one by one in .aigate/lite.toml (rule + path + fingerprint)
    res = scan_copy(tmp_path, tracked_files())
    loud = [(f["rule"], f["path"], f["line"]) for f in res["findings"]
            if f["action"] in ("fix", "block") and not f["allowed"]]
    assert loud == [] and res["verdict"] in ("pass", "warn")
    assert not [f for f in res["findings"] if f["rule"] == "gate.config_invalid"]


def test_a_key_planted_in_a_copied_doc_is_found(tmp_path):
    # control: proves the scan above really reads the copied docs
    res = scan_copy(tmp_path, doc_files(), {"docs/notes.md": f"see `{KEY_HEADER}`\n"})
    assert "docs/notes.md" in [f["path"] for f in find(res, "secrets.hardcoded")]


def test_doc_set_covers_progress_readme_and_examples():
    files = doc_files()
    assert {"PROGRESS.md", "README.md", "CHANGELOG.md", "pyproject.toml"} <= set(files)
    assert any(p.startswith("examples/") for p in files)
