import json

import pytest

from conftest import ROOT, aigate, find

PLACEHOLDER = 'echo "Error: no test specified" && exit 1'
IGNORE = "# type" + ": ignore"                    # split so this file is not itself a finding
ECOSYSTEMS = [
    # (files, ecosystem, test command or None)
    ({"requirements.txt": "requests==2.0\n", "pytest.ini": "[pytest]\n", "app.py": "x = 1\n"}, "pip",
     "python -m pytest -q"),
    ({"pyproject.toml": "[tool.poetry]\nname = 'x'\n", "poetry.lock": "", "conftest.py": ""}, "poetry",
     "python -m pytest -q"),
    ({"pyproject.toml": "[project]\nname = 'x'\n", "uv.lock": ""}, "uv", None),
    ({"Pipfile": "[packages]\n"}, "pipenv", None),
    ({"environment.yml": "name: x\n"}, "conda", None),
    ({"package.json": json.dumps({"scripts": {"test": "vitest run"}}), "package-lock.json": "{}"}, "npm", "npm test"),
    ({"package.json": json.dumps({"scripts": {"test": "jest"}}), "yarn.lock": ""}, "yarn", "yarn test"),
    ({"package.json": json.dumps({"scripts": {"test": "vitest"}}), "pnpm-lock.yaml": ""}, "pnpm", "pnpm test"),
    ({"package.json": json.dumps({"scripts": {"test": "bun test"}}), "bun.lockb": ""}, "bun", "bun run test"),
    ({"deno.json": "{}", "main.ts": "export {}\n"}, "deno", "deno test"),
    ({"go.mod": "module x\n", "main.go": "package main\n"}, "go", "go test ./..."),
    ({"Cargo.toml": "[package]\nname = 'x'\n", "src/main.rs": "fn main() {}\n"}, "cargo", "cargo test"),
    ({"pom.xml": "<project/>\n"}, "maven", "mvn -q test"),
    ({"build.gradle": "", "gradlew": "#!/bin/sh\n"}, "gradle", "./gradlew test"),
    ({"Gemfile": "gem 'rspec'\n", "spec/a_spec.rb": "describe 1 do; end\n"}, "bundler", "bundle exec rspec"),
    ({"composer.json": "{}", "phpunit.xml": "<phpunit/>"}, "composer", "vendor/bin/phpunit"),
    ({"app/App.csproj": "<Project/>"}, "dotnet", "dotnet test"),
]


def tracked(repo, files):
    for rel, text in files.items():
        repo.write(rel, text)
    repo.add()


def init(repo, *args, env=None):
    return repo.run("init", *args, env=env)


def config_text(repo):
    return (repo.path / ".aigate" / "lite.toml").read_text(encoding="utf-8")


@pytest.mark.parametrize("files,eco,command", ECOSYSTEMS, ids=[e[1] for e in ECOSYSTEMS])
def test_each_ecosystem_is_detected_with_its_test_command(repo, files, eco, command):
    tracked(repo, files)
    found = aigate.detect_project(repo.path, sorted(files))
    assert eco in found.ecosystems
    assert (command in found.test_commands) if command else not found.test_commands


def test_npm_placeholder_test_script_is_not_suggested(repo):
    files = {"package.json": json.dumps({"scripts": {"test": PLACEHOLDER}})}
    tracked(repo, files)
    assert aigate.detect_project(repo.path, list(files)).test_commands == []


def test_jest_without_a_test_script_is_suggested_through_npx(repo):
    files = {"package.json": json.dumps({"devDependencies": {"jest": "^29"}})}
    tracked(repo, files)
    assert aigate.detect_project(repo.path, list(files)).test_commands == ["npx jest"]


def test_untracked_files_are_ignored(repo):
    repo.write("go.mod", "module x\n")                      # not added
    assert "go" not in aigate.detect_project(repo.path, []).ecosystems


def test_crlf_package_json_and_languages(repo):
    files = {"package.json": json.dumps({"scripts": {"test": "jest"}}).replace("{", "{\r\n"), "a.ts": "",
             "b.py": "", "c.bin": ""}
    tracked(repo, files)
    found = aigate.detect_project(repo.path, list(files))
    assert found.test_commands == ["npm test"] and {"TypeScript", "Python"} <= set(found.languages)


def test_init_writes_preset_detection_and_a_commented_command(repo):
    tracked(repo, {"requirements.txt": "x\n", "pytest.ini": "[pytest]\n"})
    proc = init(repo)
    out, text = proc.stdout, config_text(repo)
    assert proc.returncode == 0 and out.splitlines()[-1] == "next: aigate-lite doctor"
    assert "Detected: " in out and "Preset: balanced" in out and "Wrote .aigate/lite.toml" in out
    assert 'preset = "balanced"' in text and '# run = "python -m pytest -q"' in text and "aigate-lite trust" in text
    assert "\n[[commands]]" not in text                     # only commented: commands need approval anyway


def test_init_preset_flag_and_layering(repo):
    assert init(repo, "--preset", "strict").returncode == 0
    assert 'preset = "strict"' in config_text(repo)
    cfg = aigate.load_config(aigate.Repo.discover(str(repo.path)), "worktree")
    assert cfg["rules"]["security.xss"] == "fix" and cfg["gate"]["offline"] == "block"
    assert cfg["limits"]["function_lines"] == 50 and not cfg.problems


def test_user_settings_beat_the_preset_and_the_preset_beats_defaults(repo):
    repo.write(".aigate/lite.toml", 'preset = "relaxed"\n[rules]\n"code.todo" = "warn"\n"deps.*" = "fix"\n'
                                    '[limits]\ncomplexity = 12\n')
    cfg = aigate.load_config(aigate.Repo.discover(str(repo.path)), "worktree")
    rules = cfg["rules"]
    assert rules["code.todo"] == "warn" and rules["deps.*"] == "fix" and "deps.undeclared" not in rules
    assert rules["suppress.type"] == "warn" and cfg["limits"]["complexity"] == 12
    assert cfg["limits"]["function_lines"] == 100 and not cfg.problems


def test_relaxed_preset_changes_a_real_check(repo):
    repo.write(".aigate/lite.toml", 'preset = "relaxed"\n')
    repo.write("a.py", f"x = f()  {IGNORE}\n# TODO later\n")
    repo.add()
    res = repo.check()
    assert {f["action"] for f in find(res, "suppress.type")} == {"warn"} and not find(res, "code.todo")


def test_unknown_preset_is_reported_and_falls_back_to_balanced(repo):
    repo.write(".aigate/lite.toml", 'preset = "yolo"\n')
    repo.write("a.py", f"x = f()  {IGNORE}\n")
    repo.add()
    res = repo.check()
    assert [f["message"] for f in find(res, "gate.config_invalid")] == [
        "preset must be relaxed, balanced or strict (got 'yolo'); using balanced"]
    assert {f["action"] for f in find(res, "suppress.type")} == {"fix"}


def test_print_writes_nothing(repo):
    proc = init(repo, "--print", "--preset", "relaxed")
    assert proc.returncode == 0 and 'preset = "relaxed"' in proc.stdout
    assert not (repo.path / ".aigate" / "lite.toml").exists()


def test_force_backs_up_and_is_idempotent(repo):
    assert init(repo).returncode == 0
    first = config_text(repo)
    again = init(repo)
    assert again.returncode == 1 and "--force" in again.stdout
    assert init(repo, "--force").returncode == 0 and config_text(repo) == first
    assert (repo.path / ".aigate" / "lite.toml.bak").read_text(encoding="utf-8") == first


def test_private_registries_are_suggested_without_credentials(repo):
    token = "npm_" + "x" * 36
    tracked(repo, {".npmrc": f"@acme:registry=https://npm.acme.dev/\n//npm.acme.dev/:_authToken={token}\n",
                   "requirements.txt": "--extra-index-url https://bob:s3cretpw@pypi.acme.dev/simple\nacme-lib\n"})
    proc = init(repo)
    text = config_text(repo)
    assert 'private = ["@acme/*"]' in text
    assert "https://npm.acme.dev/" in text and "https://pypi.acme.dev/simple" in text
    for secret in (token, "s3cretpw", "bob:"):
        assert secret not in text and secret not in proc.stdout


def test_empty_repository_detects_nothing(repo):
    proc = init(repo, "--print")
    assert proc.returncode == 0 and "Detected: nothing" in proc.stdout


def test_presets_table_in_docs_is_generated():
    import subprocess
    import sys
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_docs.py"), "--check"], capture_output=True,
                          text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
