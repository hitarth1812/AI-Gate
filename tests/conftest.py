import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "aigate_lite.py"
_STALE = sorted(p.name for p in ROOT.glob("*.egg-info"))
if _STALE:      # left by `python -m build`: importlib.metadata would then call this checkout an installed package
    raise pytest.UsageError(f"remove {', '.join(_STALE)} from {ROOT} (left by python -m build) before running the tests")
FIXTURE = Path(__file__).with_name("registry_fixture.json")
NOW = "2026-10-31T00:00:00+00:00"
STRIP = {"CLAUDECODE", "AI_AGENT", "GEMINI_CLI", "GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME", "VIRTUAL_ENV",
         "CONDA_PREFIX", "NO_COLOR", "PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF"}
ENCODING_VARS = ("PYTHONIOENCODING", "PYTHONUTF8", "PYTHONLEGACYWINDOWSSTDIO", "LC_ALL", "LC_CTYPE", "LANG")
_encodings = {}


def child_encoding(env) -> str:
    """The encoding a child Python uses for a piped stdout under this environment, asked from a real child
    (cached per encoding-related variables). Decoding with the parent's own default breaks when they differ
    (a cp1252 child under a UTF-8 parent on Linux); guessing from the variables misses UTF-8 mode defaults
    (PEP 686) and failed locale coercion."""
    key = tuple(env.get(k) for k in ENCODING_VARS)
    if key not in _encodings:
        probe = subprocess.run([sys.executable, "-c", "import sys; print(sys.stdout.encoding)"], env=env,
                               capture_output=True, check=True)
        _encodings[key] = probe.stdout.decode("ascii").strip()
    return _encodings[key]


def decode_output(data, encoding):
    """Bytes -> str like text=True would (universal newlines), but never raising on undecodable bytes."""
    if data is None:
        return None
    return data.decode(encoding, errors="replace").replace("\r\n", "\n").replace("\r", "\n")


WALK_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "build", "dist", ".venv", "venv", "node_modules", ".work"}


def project_files(suffixes=None, names=()) -> list:
    """The project's own files as relative POSIX paths: `git ls-files` in a git checkout, else (an unpacked sdist or
    zip) a walk of the tree without build output and caches. The checks that use this run either way."""
    if (ROOT / ".git").exists():
        proc = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
        files = [p for p in proc.stdout.splitlines() if (ROOT / p).is_file()] if proc.returncode == 0 else None
    else:
        files = None
    if files is None:
        files = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = sorted(d for d in dirnames if d not in WALK_SKIP_DIRS and not d.endswith(".egg-info"))
            rel = Path(dirpath).relative_to(ROOT)
            files += [(rel / f).as_posix() for f in sorted(filenames) if not f.endswith((".pyc", ".pyo"))]
    if suffixes is None and not names:
        return files
    return [p for p in files if (suffixes and p.endswith(tuple(suffixes))) or p.rsplit("/", 1)[-1] in names]


spec = importlib.util.spec_from_file_location("aigate_lite", SCRIPT)
aigate = importlib.util.module_from_spec(spec)
sys.modules["aigate_lite"] = aigate
spec.loader.exec_module(aigate)


class GitRepo:
    def __init__(self, path: Path, cache: Path):
        self.path = path
        self.env = {k: v for k, v in os.environ.items() if k not in STRIP and not k.startswith("AIGATE_")}
        self.env.update({"AIGATE_REGISTRY_FIXTURE": str(FIXTURE), "AIGATE_NOW": NOW, "AIGATE_CACHE_DIR": str(cache),
                         "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(cache), "GOENV": "off"})
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
        env = {**self.env, **(env or {})}
        enc = child_encoding(env)
        if input is not None:                              # text mode wrote "\n" as os.linesep
            input = input.replace("\n", os.linesep).encode(enc)
        proc = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.path, env=env, capture_output=True,
                              input=input)
        proc.stdout, proc.stderr = decode_output(proc.stdout, enc), decode_output(proc.stderr, enc)
        return proc

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
