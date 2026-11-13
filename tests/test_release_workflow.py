"""The release workflow and its guards: every workflow pins its actions, the publish job is as small and as privileged as
intended, and scripts/release_check.py rejects a wrong tag, a wrong version and a wheel with stray files.
No YAML library is used (standard library only): the workflow files are read as text."""
import importlib.util
import re
import tarfile
import zipfile

import pytest

from conftest import ROOT

WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
RELEASE = ROOT / ".github" / "workflows" / "release.yml"


def jobs(text):
    """{job name: its text} for the top-level `jobs:` mapping (jobs sit at two spaces of indent)."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):\s*$", body, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def code(text):
    """The workflow without its comment lines and trailing comments."""
    return "\n".join(re.sub(r"\s+#.*$", "", ln) for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def load_release_check():
    spec = importlib.util.spec_from_file_location("release_check", ROOT / "scripts" / "release_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_full_commit_sha_with_its_version(path):
    uses = re.findall(r"^\s*-?\s*uses:\s*(.*)$", path.read_text(encoding="utf-8"), re.M)
    assert uses
    for u in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*", u.strip()), u


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflows_default_to_read_only_and_checkout_keeps_no_credentials(path):
    text = path.read_text(encoding="utf-8")
    assert re.search(r"^permissions:\n  contents: read\n", text, re.M)
    for step in re.findall(r"uses: actions/checkout@[^\n]*\n((?:\s+with:\n(?:\s{10,}.*\n)+)?)", text):
        assert "persist-credentials: false" in step


def test_release_workflow_matches_the_registered_trusted_publisher():
    text = RELEASE.read_text(encoding="utf-8")
    assert RELEASE.name == "release.yml"
    assert re.search(r"^\s+environment: pypi\s*$", text, re.M)
    assert "# PyPI project: aigate-lite - repo: hitarth1812/AI-Gate - workflow: release.yml - environment: pypi" in text
    assert re.search(r'^  push:\n    tags: \["v\*"\]\n  workflow_dispatch:\n', text, re.M)


def test_release_workflow_holds_no_secret_token_or_password():
    text = code(RELEASE.read_text(encoding="utf-8"))
    assert "secrets." not in text and "GITHUB_TOKEN" not in text
    assert not re.search(r"\b(password|api[-_]?token|PYPI_TOKEN|TWINE_PASSWORD|TWINE_USERNAME)\b", text, re.I)


def test_only_the_publish_job_may_write_the_oidc_token():
    text = RELEASE.read_text(encoding="utf-8")
    by_job = jobs(text)
    assert set(by_job) == {"build", "test-wheel", "publish"}
    assert text.count("id-token: write") == 1 and "id-token: write" in by_job["publish"]
    assert "permissions:" not in by_job["build"] + by_job["test-wheel"]       # the top-level contents: read applies


def test_publish_job_is_gated_and_runs_nothing_but_download_and_publish():
    publish = code(jobs(RELEASE.read_text(encoding="utf-8"))["publish"])
    assert "needs: [build, test-wheel]" in publish
    assert "github.event_name == 'push'" in publish and "startsWith(github.ref, 'refs/tags/v')" in publish   # dispatch from a tag must not publish
    steps = re.findall(r"uses: ([\w./-]+)@", publish)
    assert steps == ["actions/download-artifact", "pypa/gh-action-pypi-publish"]
    assert not re.search(r"^\s+(- )?run:", publish, re.M) and "checkout" not in publish and "pip install" not in publish


def test_the_wheel_is_tested_without_a_checkout_on_three_oses_and_two_pythons():
    text = RELEASE.read_text(encoding="utf-8")
    test_job = code(jobs(text)["test-wheel"])
    assert "actions/checkout" not in test_job and "needs: build" in test_job
    assert 'os: [ubuntu-latest, macos-latest, windows-latest]' in test_job and 'python: ["3.11", "3.14"]' in test_job
    assert "--no-index" in test_job and "check --staged --offline" in test_job
    assert "concurrency:" in text and "cancel-in-progress: false" in text


def test_build_job_runs_the_guards_before_and_after_building():
    build = jobs(RELEASE.read_text(encoding="utf-8"))["build"]
    order = [build.index(s) for s in ("release_check.py tag", "python -m build", "twine check", "release_check.py dist", "sha256sum")]
    assert order == sorted(order)
    assert "persist-credentials: false" in build and "python-version: \"3.12\"" in build


# ---- scripts/release_check.py -------------------------------------------------------------------------------------

def make_tree(tmp_path, version="1.2.3"):
    root = tmp_path / "src"
    root.mkdir()
    (root / "aigate_lite.py").write_text(f'"""x"""\n__version__ = "{version}"\n', encoding="utf-8")
    return root


GOOD_META = ("".join(f"Classifier: {c}{'4 - Beta' if c.endswith(': ') else ''}\n"
                     for c in ("Development Status :: ", "Environment :: Console", "Intended Audience :: Developers",
                               "Operating System :: OS Independent", "Programming Language :: Python :: 3 :: Only",
                               "Programming Language :: Python :: 3.11", "Programming Language :: Python :: 3.12",
                               "Programming Language :: Python :: 3.13", "Programming Language :: Python :: 3.14",
                               "Topic :: Software Development :: Quality Assurance", "Topic :: Security"))
             + "".join(f"Project-URL: {u}, https://example.invalid/{u}\n"
                       for u in ("Homepage", "Repository", "Issues", "Changelog", "Documentation")))


def make_dist(tmp_path, version="1.2.3", wheel_extra=(), wheel_meta=None, sdist_meta=None, skip=(),
              extra_meta=GOOD_META):
    dist = tmp_path / "dist"
    dist.mkdir()
    info = f"aigate_lite-{version}.dist-info/"
    with zipfile.ZipFile(dist / f"aigate_lite-{version}-py3-none-any.whl", "w") as z:
        files = {"aigate_lite.py": "x = 1\n", info + "METADATA": f"Metadata-Version: 2.4\nName: aigate-lite\nVersion: {wheel_meta or version}\n"
                 + extra_meta,
                 info + "WHEEL": "w", info + "RECORD": "", info + "licenses/LICENSE": "l", info + "licenses/NOTICE": "n"}
        for name in skip:
            files.pop(name)
        for name, text in files.items():
            z.writestr(name, text)
        for name in wheel_extra:
            z.writestr(name, "x")
    pkg = tmp_path / "PKG-INFO"
    pkg.write_text(f"Metadata-Version: 2.4\nName: aigate-lite\nVersion: {sdist_meta or version}\n", encoding="utf-8")
    with tarfile.open(dist / f"aigate_lite-{version}.tar.gz", "w:gz") as t:
        t.add(pkg, arcname=f"aigate_lite-{version}/PKG-INFO")
    return dist


def test_tag_guard_accepts_the_matching_tag_and_a_dispatch_run(tmp_path):
    rc, root = load_release_check(), make_tree(tmp_path)
    assert rc.check_tag("v1.2.3", root) == "1.2.3" and rc.check_tag("", root) == "1.2.3"


@pytest.mark.parametrize("tag", ["v1.2.4", "1.2.3", "v1.2", "vv1.2.3", "v1.2.3.1", "release-1.2.3"])
def test_tag_guard_rejects_any_other_tag_with_a_clear_message(tmp_path, tag):
    rc, root = load_release_check(), make_tree(tmp_path)
    with pytest.raises(rc.Failure, match=r"must be v1\.2\.3"):
        rc.check_tag(tag, root)


def test_tag_guard_command_line_exit_codes(tmp_path, capsys):
    rc = load_release_check()
    assert rc.main(["tag", "v0.0.0-nope"]) == 1
    assert "release check failed" in capsys.readouterr().err
    assert rc.main(["wat"]) == 2


def test_dist_guard_accepts_a_correct_pair(tmp_path):
    rc, root = load_release_check(), make_tree(tmp_path)
    assert rc.check_dist(make_dist(tmp_path), root) == "1.2.3"


@pytest.mark.parametrize("kwargs, message", [
    ({"wheel_extra": ["tests/test_x.py"]}, "holds files besides"),
    ({"wheel_extra": ["other_module.py"]}, "holds files besides"),
    ({"skip": ["aigate_lite-1.2.3.dist-info/licenses/NOTICE"]}, "does not carry NOTICE"),
    ({"skip": ["aigate_lite-1.2.3.dist-info/licenses/LICENSE"]}, "does not carry LICENSE"),
    ({"skip": ["aigate_lite.py"]}, "does not contain aigate_lite.py"),
    ({"wheel_meta": "9.9.9"}, "wheel metadata says"),
    ({"sdist_meta": "9.9.9"}, "sdist metadata says"),
])
def test_dist_guard_rejects_a_bad_wheel_or_version(tmp_path, kwargs, message):
    rc, root = load_release_check(), make_tree(tmp_path)
    with pytest.raises(rc.Failure, match=message):
        rc.check_dist(make_dist(tmp_path, **kwargs), root)


def test_dist_guard_rejects_a_build_of_another_version_and_a_missing_dist(tmp_path):
    rc, root = load_release_check(), make_tree(tmp_path)
    with pytest.raises(rc.Failure, match="built version differs"):
        rc.check_dist(make_dist(tmp_path, version="1.2.4"), root)
    with pytest.raises(rc.Failure, match="does not exist"):
        rc.check_dist(tmp_path / "nowhere", root)


@pytest.mark.parametrize("drop, message", [
    ("Classifier: Topic :: Security\n", "classifiers missing"),
    ("Classifier: Programming Language :: Python :: 3.14\n", "classifiers missing"),
    ("Project-URL: Issues, https://example.invalid/Issues\n", r"missing: \['Issues'\]"),
    ("Project-URL: Changelog, https://example.invalid/Changelog\n", r"missing: \['Changelog'\]"),
])
def test_dist_guard_requires_the_pypi_classifiers_and_urls(tmp_path, drop, message):
    rc, root = load_release_check(), make_tree(tmp_path)
    assert drop in GOOD_META
    with pytest.raises(rc.Failure, match=message):
        rc.check_dist(make_dist(tmp_path, extra_meta=GOOD_META.replace(drop, "")), root)


def test_dist_guard_rejects_a_license_classifier(tmp_path):
    rc, root = load_release_check(), make_tree(tmp_path)
    meta = GOOD_META + "Classifier: License :: OSI Approved :: Apache Software License\n"
    with pytest.raises(rc.Failure, match="SPDX"):
        rc.check_dist(make_dist(tmp_path, extra_meta=meta), root)
