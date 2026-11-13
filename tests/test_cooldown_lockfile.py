"""0.5.1 Part D4: an npm range that allows a version inside the cooldown is only information for a person when a
committed lockfile pins an older version (npm ci / pnpm install --frozen-lockfile install that one). Without a lockfile
the warning stays and suggests committing one. An agent keeps the warning either way. Registry answers come from
tests/registry_fixture.json (`fresh-npm-lib`: 1.0.0 old, 1.1.0 published two days before AIGATE_NOW)."""
import json

import pytest

from conftest import find

PKG = json.dumps({"name": "app", "version": "1.0.0", "dependencies": {"fresh-npm-lib": "^1.0.0"}}, indent=2) + "\n"
LOCKS = {
    "package-lock.json": lambda v: json.dumps({"name": "app", "lockfileVersion": 3, "packages": {
        "": {"dependencies": {"fresh-npm-lib": "^1.0.0"}},
        "node_modules/fresh-npm-lib": {"version": v}}}, indent=2) + "\n",
    "pnpm-lock.yaml": lambda v: f"lockfileVersion: '9.0'\npackages:\n  fresh-npm-lib@{v}:\n    resolution: {{integrity: sha512-x}}\n",
    "yarn.lock": lambda v: f'"fresh-npm-lib@^1.0.0":\n  version "{v}"\n  resolved "https://registry.yarnpkg.com/x"\n',
    "bun.lock": lambda v: '{\n  "lockfileVersion": 1,\n  "packages": {\n    "fresh-npm-lib": ["fresh-npm-lib@' + v + '", "", {}, "sha512-x"]\n  }\n}\n',
}


def cooldown(res):
    return [f for f in find(res, "deps.version_cooldown") if f["path"] == "package.json"]


@pytest.mark.parametrize("lock", sorted(LOCKS))
def test_a_lockfile_pinning_an_older_version_makes_it_information_for_a_person(repo, lock):
    repo.write(lock, LOCKS[lock]("1.0.0"))
    repo.commit("lockfile")
    repo.write("package.json", PKG)
    repo.add()
    person = cooldown(repo.check())
    assert len(person) == 1 and person[0]["allowed"] == "info only", person
    assert f"{lock} pins 1.0.0" in person[0]["message"] and "npm ci" in person[0]["message"]
    agent = cooldown(repo.check(env={"CLAUDECODE": "1"}))
    assert len(agent) == 1 and agent[0]["action"] == "warn" and not agent[0]["allowed"]


def test_no_lockfile_keeps_the_warning_and_suggests_one(repo):
    repo.write("package.json", PKG)
    repo.add()
    found = cooldown(repo.check())
    assert len(found) == 1 and found[0]["action"] == "warn" and not found[0]["allowed"]
    assert "lockfile" in (found[0].get("hint") or "")


def test_a_lockfile_that_pins_the_fresh_version_keeps_the_warning(repo):
    repo.write("package-lock.json", LOCKS["package-lock.json"]("1.1.0"))
    repo.write("package.json", PKG)
    repo.add()
    found = cooldown(repo.check())
    assert len(found) == 1 and found[0]["action"] == "warn" and not found[0]["allowed"]
