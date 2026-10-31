import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "aigate_lite.py"
FIXTURE = Path(__file__).with_name("registry_fixture.json")
NOW = "2026-10-31T00:00:00+00:00"
STRIP = {"CLAUDECODE", "AI_AGENT", "GEMINI_CLI", "GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME", "VIRTUAL_ENV",
         "CONDA_PREFIX", "NO_COLOR", "PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF"}

spec = importlib.util.spec_from_file_location("aigate_lite", SCRIPT)
aigate = importlib.util.module_from_spec(spec)
sys.modules["aigate_lite"] = aigate
spec.loader.exec_module(aigate)


class GitRepo:
    def __init__(self, path: Path, cache: Path):
        self.path = path
        self.env = {k: v for k, v in os.environ.items() if k not in STRIP and not k.startswith("AIGATE_")}
        self.env.update({"AIGATE_REGISTRY_FIXTURE": str(FIXTURE), "AIGATE_NOW": NOW, "AIGATE_CACHE_DIR": str(cache),
                         "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(cache)})
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "dev@example.com")
        self.git("config", "user.name", "Dev")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args, env=None, check=True, input=None):
        return subprocess.run(["git", *args], cwd=self.path, env={**self.env, **(env or {})}, text=True,
                              capture_output=True, check=check, input=input)

    def write(self, rel: str, text: str):
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def add(self, *paths):
        self.git("add", "-A", *paths)

    def commit(self, msg="change", env=None):
        self.add()
        return self.git("commit", "-q", "--no-verify", "-m", msg, env=env)

    def run(self, *args, env=None, input=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.path, env={**self.env, **(env or {})},
                              text=True, capture_output=True, input=input)

    def check(self, *args, env=None):
        proc = self.run("check", "--format", "json", *args, env=env)
        assert proc.returncode in (0, 1), proc.stderr + proc.stdout
        data = json.loads(proc.stdout)
        data["code"] = proc.returncode
        return data


def rules(result, *, action=None, allowed=False):
    return [f["rule"] for f in result["findings"] if (action is None or f["action"] == action)
            and bool(f["allowed"]) == allowed]


def find(result, rule):
    return [f for f in result["findings"] if f["rule"] == rule]


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "repo").mkdir()
    r = GitRepo(tmp_path / "repo", tmp_path / "cache")
    r.write("README.md", "# demo\n")
    r.commit("init")
    return r
