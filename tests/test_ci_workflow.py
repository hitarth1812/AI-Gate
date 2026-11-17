"""The continuous-integration workflows: tests run in parallel with a serial step for what cannot, pull requests use a
smaller matrix than the nightly run, every action is pinned by commit, and the development tools are pinned the same way
in requirements-dev.txt and in the `dev` extra. No YAML library is used (standard library only): the workflow files are
read as text, like in test_release_workflow.py."""
import itertools
import re
import tomllib

import pytest

from conftest import ROOT
from test_release_workflow import code, jobs

WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
CI = ROOT / ".github" / "workflows" / "ci.yml"
FULL = ROOT / ".github" / "workflows" / "ci-full.yml"
REQUIREMENTS = ROOT / "requirements-dev.txt"
PYPROJECT = ROOT / "pyproject.toml"

PULL_REQUEST_LEGS = {("ubuntu-latest", "3.11"), ("ubuntu-latest", "3.14"), ("windows-latest", "3.12"),
                     ("macos-latest", "3.12")}


def text_of(path):
    return path.read_text(encoding="utf-8")


def pytest_steps(job_text):
    """The `run:` command of every step that starts pytest."""
    return [c.strip() for c in re.findall(r"^\s+run:\s*(.*python -m pytest.*)$", code(job_text), re.M)]


def requirement_pins(items):
    """{package name: pinned version} from lines like `pkg==1.2.3` (inline comments dropped); every pin must be exact."""
    pins = {}
    for item in items:
        item = item.split("#", 1)[0].strip()
        if not item:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([0-9][\w.]*)", item)
        assert match, f"not an exact pin: {item!r}"
        pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pins


# ---- parallel tests and the serial step ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [CI, FULL], ids=lambda p: p.name)
def test_the_suite_runs_in_parallel_and_the_serial_marked_tests_run_in_a_second_step(path):
    for job_name, job in jobs(text_of(path)).items():
        steps = pytest_steps(job)
        if not steps:
            continue
        assert len(steps) == 2, (job_name, steps)
        parallel, serial = steps
        assert "-n auto" in parallel and '-m "not serial"' in parallel, parallel
        assert "-n" not in serial.split() and "-m serial" in serial, serial
        assert code(job).index(parallel) < code(job).index(serial)


@pytest.mark.parametrize("path", [CI, FULL], ids=lambda p: p.name)
def test_a_workflow_that_runs_the_suite_runs_it_on_a_matrix(path):
    assert any(pytest_steps(job) for job in jobs(text_of(path)).values())


def test_the_serial_marker_is_registered():
    markers = tomllib.loads(text_of(PYPROJECT))["tool"]["pytest"]["ini_options"].get("markers", [])
    assert any(m.split(":", 1)[0].strip() == "serial" and ":" in m for m in markers), markers


# ---- the two tiers ------------------------------------------------------------------------------------------------

def test_pull_requests_and_pushes_to_main_run_four_legs_and_nothing_scheduled():
    text = text_of(CI)
    legs = re.findall(r"\{os: ([\w.-]+), python: \"([\d.]+)\"\}", jobs(text)["test"])
    assert len(legs) == 4 and set(legs) == PULL_REQUEST_LEGS, legs
    assert "schedule:" not in code(text) and "\n  push:\n    branches: [main]\n" in text and "\n  pull_request:\n" in text
    assert "os: [" not in jobs(text)["test"]          # no cross product next to the listed legs


def test_the_nightly_run_has_all_twelve_legs_and_can_be_started_by_hand():
    text = text_of(FULL)
    test_job = code(jobs(text)["test"])
    oses = re.search(r"^\s+os: \[(.*)\]$", test_job, re.M).group(1).replace(" ", "").split(",")
    pythons = re.search(r"^\s+python: \[(.*)\]$", test_job, re.M).group(1).replace('"', "").replace(" ", "").split(",")
    legs = set(itertools.product(oses, pythons))
    assert len(legs) == 12 and oses == ["ubuntu-latest", "macos-latest", "windows-latest"], legs
    assert PULL_REQUEST_LEGS <= legs
    assert re.search(r'^  schedule:\n    - cron: "[^"\n]+"\n', text, re.M) and "\n  workflow_dispatch:\n" in text
    assert "pull_request" not in code(text)


def test_the_nightly_run_opens_an_issue_when_a_leg_fails_with_no_other_permission():
    text = text_of(FULL)
    report = jobs(text)["report"]
    assert "needs: [fast, test]" in report and "contains(needs.*.result, 'failure')" in report
    assert report.count("issues: write") == 1 and "gh issue" in report
    assert "issues: write" not in jobs(text)["test"] and re.search(r"^permissions:\n  contents: read\n", text, re.M)
    assert not re.search(r"^\s+(contents|id-token|pull-requests|actions): write", text, re.M)


def test_the_cheap_checks_run_first_and_every_leg_waits_for_them():
    for path in (CI, FULL):
        by_job = jobs(text_of(path))
        fast = code(by_job["fast"])
        for command in ("pyflakes aigate_lite.py", "scripts/gen_docs.py --check", "scripts/gen_accuracy.py --check",
                        "aigate_lite.py check --all --offline -q"):
            assert command in fast, (path.name, command)
        assert "pytest" not in fast and "timeout-minutes: 5" in fast
        assert "needs: fast" in code(by_job["test"])


def test_the_evaluation_check_still_runs_on_every_pull_request():
    by_job = jobs(text_of(CI))
    commands = [code(job) for name, job in by_job.items() if "eval/run.py --check --no-write" in code(job)]
    assert len(commands) == 1 and "needs: fast" in commands[0] and "runs-on: ubuntu-latest" in commands[0]


# ---- pip cache and pinned actions ---------------------------------------------------------------------------------

@pytest.mark.parametrize("path", [CI, FULL], ids=lambda p: p.name)
def test_every_job_that_installs_the_development_tools_caches_pip_on_requirements_dev(path):
    for name, job in jobs(text_of(path)).items():
        if "pip install -r requirements-dev.txt" not in job:
            continue
        setup = re.findall(r"uses: actions/setup-python@[^\n]*\n {8}with:\n((?: {10}\S.*\n)+)", job + "\n")
        assert setup, name
        for block in setup:
            assert re.search(r"^\s+cache: pip\s*$", block, re.M), (name, block)
            assert re.search(r"^\s+cache-dependency-path: requirements-dev\.txt\s*$", block, re.M), (name, block)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_in_every_workflow_is_pinned_by_a_forty_character_commit(path):
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", text_of(path), re.M)
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), ref


# ---- pinned development tools -------------------------------------------------------------------------------------

def test_requirements_dev_and_the_dev_extra_pin_the_same_packages_and_versions():
    lines = text_of(REQUIREMENTS).splitlines()
    from_file = requirement_pins(lines)
    extra = requirement_pins(tomllib.loads(text_of(PYPROJECT))["project"]["optional-dependencies"]["dev"])
    assert from_file == extra, (from_file, extra)
    assert {"pytest", "pyflakes", "hypothesis", "pytest-xdist", "execnet"} <= set(from_file)


def test_the_tool_itself_still_has_no_runtime_dependency():
    project = tomllib.loads(text_of(PYPROJECT))["project"]
    assert project["dependencies"] == []
    assert re.search(r"^dependencies = \[\]\s*$", text_of(PYPROJECT), re.M)


# ---- the release checklist ----------------------------------------------------------------------------------------

def test_the_release_checklist_requires_a_green_full_run_on_the_release_commit():
    text = text_of(ROOT / "docs" / "dev" / "RELEASING.md")
    assert "ci-full" in text and re.search(r"green", text) and "release commit" in text
