"""0.5.1 Part C3: `setup` / `install --vendor` say in one message what happened to .aigate/aigate_lite.py, and never
overwrite a vendored copy that is newer than the running tool."""
from conftest import SCRIPT, aigate


def copy_path(repo):
    return repo.path / ".aigate" / "aigate_lite.py"


def with_version(version):
    text = SCRIPT.read_text(encoding="utf-8")
    return text.replace(f'__version__ = "{aigate.__version__}"', f'__version__ = "{version}"', 1)


def write_copy(repo, version):
    p = copy_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(with_version(version), encoding="utf-8")
    return p


def test_first_vendor_copies_and_names_the_version(repo):
    proc = repo.run("install", "--vendor")
    assert proc.returncode == 0
    assert f"copied the gate (version {aigate.__version__}) to .aigate/aigate_lite.py" in proc.stdout
    assert copy_path(repo).read_bytes().replace(b"\r\n", b"\n") == SCRIPT.read_bytes().replace(b"\r\n", b"\n")


def test_an_older_copy_is_updated_from_x_to_y_in_one_message(repo):
    write_copy(repo, "0.1.0")
    proc = repo.run("setup")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ".aigate/aigate_lite.py" in ln]
    assert len(lines) == 1, proc.stdout
    assert f"updated the vendored copy .aigate/aigate_lite.py from version 0.1.0 to {aigate.__version__}" in lines[0]
    assert "aigate-lite unvendor" in lines[0]
    assert f'__version__ = "{aigate.__version__}"' in copy_path(repo).read_text(encoding="utf-8")


def test_an_unchanged_copy_says_unchanged(repo):
    assert repo.run("install", "--vendor").returncode == 0
    proc = repo.run("install", "--vendor")
    assert "is unchanged" in proc.stdout and "copied" not in proc.stdout and "updated" not in proc.stdout


def test_a_newer_copy_is_kept_with_a_warning(repo):
    newer = write_copy(repo, "99.0.0")
    before = newer.read_bytes()
    for args in (("setup",), ("install", "--vendor")):
        proc = repo.run(*args)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "warning: the vendored copy .aigate/aigate_lite.py is version 99.0.0, NEWER than this aigate-lite" \
            in proc.stdout and "kept it unchanged" in proc.stdout
        assert newer.read_bytes() == before


def test_same_version_different_content_is_updated(repo):
    p = write_copy(repo, aigate.__version__)
    p.write_text(p.read_text(encoding="utf-8") + "\n# local edit\n", encoding="utf-8")
    proc = repo.run("install", "--vendor")
    assert f"from version {aigate.__version__} to {aigate.__version__}" in proc.stdout
    assert "# local edit" not in p.read_text(encoding="utf-8")
