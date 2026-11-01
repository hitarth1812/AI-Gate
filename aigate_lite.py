#!/usr/bin/env python3
"""aigate-lite: a free, local commit-time gate for AI-written code.

It runs as a git pre-commit / pre-push hook (or on demand) and checks only what
a commit adds, for the mistakes AI coding tools make most often:

* exposed secrets and env/key files
* hallucinated, brand-new, typo-squatted, vulnerable or untrusted packages
* weakened tests (removed assertions, new skips, deleted tests, harness tricks)
* placeholder code, swallowed errors, unsafe calls, silenced checkers
* bloat, duplicated blocks and slow patterns (nested loops, N+1 calls)
* scope creep (forbidden paths, files outside the task's declared scope)

Single file, Python 3.11+ standard library only. No account, server, API key or
LLM. The only network use is free public lookups (PyPI, npm, OSV), which are
cached and skipped with --offline.

    python3 aigate_lite.py install        # add the pre-commit and pre-push hooks
    python3 aigate_lite.py check          # check staged changes now
    python3 aigate_lite.py check --range origin/main..HEAD
    python3 aigate_lite.py explain        # list every rule
"""
import sys

if sys.version_info < (3, 11):
    sys.stderr.write("aigate-lite: needs Python 3.11 or newer (found %s)\n"
                     "  fix: install Python 3.11+ (hooks also use $AIGATE_PYTHON when it is set)\n"
                     % sys.version.split()[0])
    sys.exit(2)

import ast
import dataclasses
import datetime as dt
import fnmatch
import json
import math
import os
import re
import subprocess
import time
import tomllib
from collections import Counter, defaultdict

# Imported where used, to keep start-up fast (see `python -X importtime`): argparse, atexit,
# concurrent.futures, configparser, glob, gzip, hashlib, importlib.metadata, importlib.util, shlex,
# shutil, urllib.

__version__ = "0.1.0"

MAX_BYTES = 1_000_000            # files larger than this skip content checks
ACTION_RANK = {"off": 0, "pass": 1, "warn": 2, "fix": 3, "block": 4}
WEIGHTS = {"critical": 40, "high": 20, "medium": 8, "low": 2}
BLOCK_AT = 60                    # risk score that escalates a Fix verdict to Block
FIXED_RULES = {"commands.untrusted"}   # [rules] overrides cannot change these (a repo config must not hide or
                                       # weaponise the notice that its own commands are unapproved)
PROFILE = Counter(git_calls=0, blobs_read=0)   # AIGATE_PROFILE=1 prints these to stderr on exit


# --------------------------------------------------------------------------- rules

@dataclasses.dataclass(frozen=True)
class Rule:
    id: str
    action: str          # default action: block | fix | warn
    severity: str        # critical | high | medium | low (feeds the risk score)
    confidence: float    # share of flags expected to be right (feeds the risk score)
    title: str
    hint: str
    escalate: bool = False   # one step stricter inside [paths] sensitive


_RULE_ROWS = [
    # secrets
    ("secrets.hardcoded", "block", "critical", 0.95, "Provider credential or private key in the diff",
     "Remove it and rotate the credential (anything committed is leaked), then load it from an environment variable or a secret manager."),
    ("secrets.generic", "fix", "high", 0.7, "Credential-like value written into the code",
     "Read it from the environment instead. If it is a harmless fixture, add an [[allow]] entry for its path."),
    ("secrets.env_file", "block", "critical", 0.9, "Env, key or credential file staged",
     "Unstage it (git rm --cached <file>), add it to .gitignore, and commit a .env.example with placeholder values instead."),
    ("secrets.env_not_ignored", "warn", "medium", 0.9, "A local .env file is not git-ignored",
     "Add it to .gitignore before a `git add .` commits it."),
    # dependencies
    ("deps.hallucinated", "block", "critical", 0.9, "Package does not exist on the registry",
     "Check the name. AI tools invent plausible package names, and attackers register them ('slopsquatting'). "
     "If it comes from a private index, list it under [deps] private; if the import name differs from the package name, add it to [deps] import_map."),
    ("deps.undeclared", "fix", "medium", 0.8, "Import is not declared as a dependency",
     "Add the package to your manifest (requirements.txt, pyproject.toml or package.json) or remove the import."),
    ("deps.new_package", "fix", "high", 0.8, "Package was first published very recently",
     "Brand-new packages are a common malware vector. Check the project, maintainer and source before adding it."),
    ("deps.version_cooldown", "fix", "medium", 0.7, "Version was published very recently",
     "Pin the previous version for a few days; compromised releases are usually pulled within days."),
    ("deps.untrusted_source", "block", "high", 0.9, "Dependency from git, a URL or a custom index",
     "Install it from the public registry, or add the source to [deps] trusted_sources."),
    ("deps.local_source", "warn", "low", 0.9, "Dependency installed from a local path",
     "Fine inside a monorepo; otherwise publish or vendor it deliberately."),
    ("deps.typosquat", "warn", "high", 0.6, "Name is one or two edits away from a popular package",
     "Double-check the spelling against the package you meant."),
    ("deps.vulnerable", "fix", "high", 0.9, "Pinned version has known vulnerabilities (OSV)",
     "Pin a fixed version. AI tools often suggest old versions from their training data."),
    ("deps.outdated", "warn", "low", 0.8, "Pinned version is a major version behind",
     "AI tools often suggest versions from their training data; check whether the latest major works for you."),
    ("deps.deprecated", "warn", "medium", 0.9, "Package or version is deprecated or yanked",
     "Use the replacement the maintainers point to."),
    ("deps.removed_stdlib", "fix", "medium", 0.9, "Module was removed from the Python standard library",
     "Use the replacement named in the message."),
    ("deps.unverified", "warn", "low", 1.0, "Package could not be checked (registry unreachable)",
     "Re-run the check online: aigate-lite check"),
    # scope
    ("scope.forbidden_path", "block", "high", 1.0, "Change to a forbidden path",
     "Unstage the file, or change [paths] forbidden / forbidden_for_agents in .aigate/lite.toml."),
    ("scope.out_of_scope", "fix", "medium", 1.0, "File is outside the task scope you declared",
     "Unstage it, or widen the scope: aigate-lite scope add '<glob>'"),
    ("gate.config_changed", "warn", "medium", 1.0, "Gate or hook configuration changed",
     "Review this change: loosening the gate is an easy way for an agent to get a commit through."),
    # tests
    ("tests.assertions_removed", "fix", "high", 0.85, "Test lost assertions or cases",
     "Restore them, or confirm the behaviour change and allow it once: AIGATE_ALLOW=tests.assertions_removed git commit ..."),
    ("tests.skip_added", "fix", "high", 0.9, "Test was skipped or marked as an expected failure",
     "Fix the code instead of skipping the test."),
    ("tests.deleted", "fix", "high", 0.85, "Test was deleted",
     "Restore it. If it was renamed or moved, allow it once: AIGATE_ALLOW=tests.deleted git commit ..."),
    ("tests.focused", "fix", "high", 0.95, "Focused test (.only / fit / fdescribe) committed",
     "Remove the focus: it silently stops every other test from running."),
    ("tests.trivial_assert", "fix", "medium", 0.9, "Assertion that can never fail",
     "Assert on the real result."),
    ("tests.no_assertions", "warn", "low", 0.7, "New test has no assertions",
     "A test that only calls the code passes even when the result is wrong."),
    ("tests.expected_changed", "warn", "medium", 0.5, "Expected values changed together with the code",
     "Fine for an intended behaviour change; otherwise the test was edited to match a bug."),
    ("tests.harness_tamper", "block", "high", 0.85, "Code that can rewrite test results",
     "Remove it. Result-changing pytest hooks, sitecustomize.py, .pth files and pytest11 entry points run around every test."),
    ("tests.config_changed", "fix", "medium", 0.8, "Test or coverage configuration changed",
     "Check that no tests or files were excluded just to make the run pass."),
    ("tests.internals_touched", "fix", "high", 0.7, "Application code touches test-runner internals",
     "Production code should not reach into pytest/unittest internals or the coverage module."),
    # suppressions
    ("suppress.security", "fix", "high", 0.9, "Security scanner silenced", "Fix the finding instead of hiding it.", True),
    ("suppress.blanket", "fix", "medium", 0.9, "Whole-file checker suppression", "Fix the errors instead of switching the checker off.", True),
    ("suppress.type", "fix", "medium", 0.8, "Type checker silenced", "Fix the type error; AI tools add ignores to make errors disappear.", True),
    ("suppress.coverage", "fix", "medium", 0.8, "Code excluded from coverage", "Test the code instead of excluding it.", True),
    ("suppress.lint", "warn", "low", 0.8, "Linter silenced on a line", "Fine when justified; say why in the comment.", True),
    ("suppress.type_cast", "warn", "low", 0.6, "TypeScript `any` escape hatch", "Use a real type, or `unknown` with a type guard.", True),
    # code
    ("code.syntax_error", "fix", "high", 0.95, "File does not parse", "Fix the syntax error before committing."),
    ("code.placeholder", "fix", "high", 0.85, "Placeholder or truncated code",
     "AI output was cut short or left a stub; finish the implementation."),
    ("code.stub", "warn", "low", 0.6, "Function has no implementation", "Implement it or remove it."),
    ("code.todo", "warn", "low", 0.9, "New TODO/FIXME", "Finish it or track it in an issue."),
    ("code.swallowed_exception", "fix", "medium", 0.85, "Error silently swallowed",
     "Handle or log the error, or catch a narrower exception."),
    ("code.bare_except", "warn", "low", 0.8, "Bare `except:` also catches KeyboardInterrupt and SystemExit",
     "Catch Exception or something narrower."),
    ("code.debug_leftover", "fix", "medium", 0.95, "Debugger statement left in", "Remove it before committing."),
    ("code.console_log", "warn", "low", 0.6, "console.log left in source", "Remove it or use a logger."),
    ("code.mutable_default", "warn", "medium", 0.9, "Mutable default argument",
     "Default to None and create the list/dict inside the function."),
    ("code.unused_import", "warn", "low", 0.8, "Import is never used", "Remove it."),
    ("code.duplicate_block", "warn", "low", 0.7, "Duplicated block of code", "Extract a shared function instead of copying."),
    # security
    ("security.eval", "fix", "high", 0.8, "Dynamic code execution",
     "Avoid eval/exec; parse the input explicitly (ast.literal_eval, JSON.parse).", True),
    ("security.shell", "fix", "high", 0.8, "Shell command built at runtime", "Pass an argument list without a shell.", True),
    ("security.deserialization", "fix", "high", 0.85, "Unsafe deserialization",
     "Use JSON or yaml.safe_load; pickle-style loaders run code while loading.", True),
    ("security.sql_injection", "fix", "high", 0.75, "SQL built with string formatting", "Use parameterised queries.", True),
    ("security.verification_disabled", "fix", "high", 0.9, "TLS or signature verification disabled",
     "Keep verification on; fix the certificate or key setup instead.", True),
    ("security.insecure_default", "warn", "medium", 0.7, "Insecure default (debug mode, wildcard CORS or hosts)",
     "Make it configurable and strict by default.", True),
    ("security.weak_crypto", "warn", "medium", 0.6, "Weak hash or non-cryptographic randomness for a secret",
     "Use the secrets module, hashlib.scrypt/bcrypt/argon2, or crypto.randomBytes.", True),
    ("security.xss", "warn", "medium", 0.6, "Raw HTML injection", "Escape or sanitise the content first.", True),
    # size and performance
    ("size.function_length", "warn", "low", 0.9, "Long function", "Split it into smaller functions."),
    ("size.complexity", "warn", "medium", 0.9, "High cyclomatic complexity", "Simplify the branching or split the function."),
    ("size.file_length", "warn", "low", 0.9, "Long file", "Split the module."),
    ("size.diff_budget", "warn", "low", 1.0, "Very large change", "Split it into smaller commits; large AI changes hide mistakes."),
    ("perf.nested_loops", "warn", "medium", 0.6, "Deeply nested loops",
     "Check the time complexity; an index (dict/set) often removes a loop level."),
    ("perf.quadratic_pattern", "warn", "medium", 0.6, "Likely quadratic pattern inside a loop",
     "Use a set/dict or deque, or move the work out of the loop."),
    ("perf.call_in_loop", "warn", "medium", 0.5, "Network or database call inside a loop (N+1)",
     "Batch the calls, or fetch everything once before the loop."),
    ("perf.exponential_recursion", "warn", "medium", 0.6, "Recursion that branches without memoisation",
     "Add functools.cache or rewrite it iteratively."),
    # gate
    ("commands.failed", "fix", "high", 1.0, "Configured command failed", "See the command output above."),
    ("commands.untrusted", "warn", "low", 1.0, "Configured commands not approved yet, so they did not run",
     "Review the [[commands]] in .aigate/lite.toml, then run `aigate-lite trust`."),
    ("gate.config_invalid", "warn", "low", 1.0, "Problem in .aigate/lite.toml", "Fix the entry named in the message."),
    ("gate.error", "block", "high", 1.0, "aigate-lite could not finish a check",
     "This is a bug or an environment problem, and the gate fails closed. Bypass once with git commit --no-verify and report it."),
]
RULES = {row[0]: Rule(*row) for row in _RULE_ROWS}


# --------------------------------------------------------------------------- small helpers

def norm_py(name: str) -> str:
    """PEP 503 normalisation: 'Foo_Bar.baz' -> 'foo-bar-baz'."""
    return re.sub(r"[-_.]+", "-", name).lower()


_GLOB_CACHE: dict = {}


def _glob_re(pattern: str):
    rx = _GLOB_CACHE.get(pattern)
    if rx is None:
        i, out = 0, []
        while i < len(pattern):
            c = pattern[i]
            if pattern.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            if c == "*":
                out.append("[^/]*")
            elif c == "?":
                out.append("[^/]")
            elif c == "[" and pattern.find("]", i + 1) > i:
                j = pattern.find("]", i + 1)
                body = pattern[i + 1:j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j + 1
                continue
            else:
                out.append(re.escape(c))
            i += 1
        rx = _GLOB_CACHE[pattern] = re.compile("".join(out) + r"\Z", re.S)
    return rx


def path_matches(path: str, patterns) -> bool:
    """gitignore-style matching. A pattern without '/' also matches any single path component."""
    for pat in patterns or ():
        pat = str(pat).strip()
        if not pat:
            continue
        if pat.endswith("/"):
            pat += "**"
        if _glob_re(pat).match(path):
            return True
        if "/" not in pat and any(_glob_re(pat).match(part) for part in path.split("/")):
            return True
    return False


def entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def edit_distance(a: str, b: str, limit: int) -> int:
    """Optimal-string-alignment distance (adjacent swaps count as 1); returns limit+1 once exceeded."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[len(b)]


def redact(value: str) -> str:
    v = value.strip()
    return v[:4] + "…" + v[-2:] if len(v) > 10 else "…"


def fingerprint(*parts) -> str:
    """The 0.1 fingerprint (12 hex characters). Kept as `legacy_fingerprint` so [[allow]] entries and baselines
    written by 0.1.x still match during 0.2.x."""
    import hashlib
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8", "replace")).hexdigest()[:12]


def fingerprint_v2(*parts) -> str:
    """The 0.2 fingerprint (16 hex characters, so old ones are recognisable): rule, path and a key that does
    not depend on line numbers, so it survives edits elsewhere in the file."""
    import hashlib
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8", "replace")).hexdigest()[:16]


def utcnow() -> dt.datetime:
    fixed = os.environ.get("AIGATE_NOW")          # tests pin the clock with this
    if fixed:
        return parse_time(fixed)
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value):
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def age_days(value):
    t = parse_time(value)
    return None if t is None else (utcnow() - t).total_seconds() / 86400


def release_tuple(v):
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", str(v or ""))
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _cmp(a, b) -> int:
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def py_spec_allows(spec: str, version: str):
    """Does a PEP 440 specifier allow this version? True/False, or None when unsure."""
    v = release_tuple(version)
    if v is None:
        return None
    spec = (spec or "").strip()
    if not spec:
        return True
    for clause in spec.split(","):
        m = re.match(r"^(===|==|!=|~=|>=|<=|>|<)\s*(\S+)$", clause.strip())
        if not m:
            return None
        op, target = m.groups()
        wildcard = target.endswith(".*")
        t = release_tuple(target[:-2] if wildcard else target)
        if t is None:
            return None
        if op in ("==", "==="):
            ok = v[:len(t)] == t if wildcard else _cmp(v, t) == 0
        elif op == "!=":
            ok = v[:len(t)] != t if wildcard else _cmp(v, t) != 0
        elif op == ">=":
            ok = _cmp(v, t) >= 0
        elif op == "<=":
            ok = _cmp(v, t) <= 0
        elif op == ">":
            ok = _cmp(v, t) > 0
        elif op == "<":
            ok = _cmp(v, t) < 0
        else:  # ~=
            if len(t) < 2:
                return None
            ok = _cmp(v, t) >= 0 and v[:len(t) - 1] == t[:len(t) - 1]
        if not ok:
            return False
    return True


def npm_spec_allows(spec: str, version: str):
    """Does a common npm semver range allow this version? True/False, or None when unsure."""
    v = release_tuple(version)
    if v is None:
        return None
    v = (v + (0, 0, 0))[:3]
    spec = (spec or "").strip()
    if spec in ("", "*", "latest", "x", "X"):
        return True
    if "||" in spec:
        results = [npm_spec_allows(part, version) for part in spec.split("||")]
        return True if True in results else (None if None in results else False)
    if " - " in spec:
        lo, hi = (release_tuple(p) for p in spec.split(" - ", 1))
        return None if lo is None or hi is None else _cmp(v, lo) >= 0 and _cmp(v, hi) <= 0
    for comp in spec.split():
        m = re.match(r"^(\^|~|>=|<=|>|<|=)?\s*v?(\d+|[xX*])(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?(?:-[0-9A-Za-z.-]+)?$", comp)
        if not m:
            return None
        op, fixed = m.group(1) or "", []
        for part in m.group(2, 3, 4):
            if part is None or part in "xX*":
                break
            fixed.append(int(part))
        t = tuple(fixed)
        t3 = (t + (0, 0, 0))[:3]
        if op in ("", "="):
            ok = v[:len(t)] == t if len(t) < 3 else v == t
        elif op == "^":
            k = next((i for i, x in enumerate(t3) if x != 0), max(len(t) - 1, 0))
            k = min(k, max(len(t) - 1, 0))
            ok = _cmp(v, t3) >= 0 and v[:k + 1] == t3[:k + 1]
        elif op == "~":
            keep = 2 if len(t) >= 2 else 1
            ok = _cmp(v, t3) >= 0 and v[:keep] == t3[:keep]
        else:
            c = _cmp(v, t3)
            ok = {">=": c >= 0, "<=": c <= 0, ">": c > 0, "<": c < 0}[op]
        if not ok:
            return False
    return True


def loads_jsonc(text: str):
    """json.loads for tsconfig-style files: strips // and /* */ comments and trailing commas."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


# --------------------------------------------------------------------------- git

class UserError(Exception):
    """A problem the user can fix: main() prints `aigate-lite: <cause>` and `  fix: <fix>`, no traceback."""

    def __init__(self, cause: str, fix: str = "", exit_code: int = 2):
        super().__init__(cause)
        self.cause, self.fix, self.exit_code = cause, fix, exit_code


class GitError(UserError):
    pass


def git_missing() -> UserError:
    return UserError("git was not found on PATH", "install git (2.13 or newer) and check that `git --version` works")


def run_git(root, *args, input_bytes=None, check=True) -> str:
    PROFILE["git_calls"] += 1
    try:
        proc = subprocess.run(["git", *args], cwd=root, input=input_bytes, capture_output=True)
    except FileNotFoundError:
        raise git_missing() from None
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args[:2])} failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode("utf-8", "replace")


class Repo:
    def __init__(self, root: str, git_dir=None):
        self.root = root
        self.git_dir = git_dir
        self._reader = None
        self._blobs: dict = {}
        self._empty_tree = None

    @classmethod
    def discover(cls, cwd=None) -> "Repo":
        PROFILE["git_calls"] += 1
        try:
            proc = subprocess.run(["git", "rev-parse", "--show-toplevel", "--absolute-git-dir"], cwd=cwd,
                                  capture_output=True, text=True)
        except FileNotFoundError:
            raise git_missing() from None
        lines = proc.stdout.splitlines()
        if proc.returncode != 0 or len(lines) != 2:
            raise GitError("not inside a git repository",
                           "run it inside your project's folder (or create a repository there: git init)")
        return cls(lines[0].strip(), lines[1].strip())

    def git(self, *args, **kw) -> str:
        return run_git(self.root, *args, **kw)

    def commit(self, rev: str):
        PROFILE["git_calls"] += 1
        proc = subprocess.run(["git", "rev-parse", "--verify", "-q", rev + "^{commit}"],
                              cwd=self.root, capture_output=True, text=True)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def empty_tree(self) -> str:
        if self._empty_tree is None:
            self._empty_tree = self.git("hash-object", "-t", "tree", "--stdin", input_bytes=b"").strip()
        return self._empty_tree

    def git_path(self, name: str) -> str:
        p = self.git("rev-parse", "--git-path", name).strip()
        return p if os.path.isabs(p) else os.path.join(self.root, p)

    def private_path(self, name: str) -> str:
        """A per-worktree file inside the git dir (MERGE_HEAD, aigate/scope); same answer as git_path, no subprocess."""
        return os.path.join(self.git_dir, name) if self.git_dir else self.git_path(name)

    def read(self, spec: str):
        """Bytes of '<rev>:<path>' or ':<path>' (the index); None when missing or not a blob."""
        if spec in self._blobs:
            return self._blobs[spec]
        data = None
        if "\n" not in spec:
            if self._reader is None:
                import atexit
                PROFILE["git_calls"] += 1
                self._reader = subprocess.Popen(["git", "cat-file", "--batch"], cwd=self.root,
                                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                atexit.register(self.close)
            r = self._reader
            PROFILE["blobs_read"] += 1
            r.stdin.write(spec.encode("utf-8") + b"\n")
            r.stdin.flush()
            header = r.stdout.readline()
            if not header:
                raise GitError("git cat-file stopped unexpectedly")
            if not header.rstrip().endswith((b" missing", b" ambiguous")):
                _sha, kind, size = header.split()[:3]
                body = r.stdout.read(int(size))
                r.stdout.read(1)
                if kind == b"blob":
                    data = body
        self._blobs[spec] = data
        return data

    def close(self):
        if self._reader is not None:
            try:
                self._reader.stdin.close()
                self._reader.wait(timeout=5)
            except Exception:
                self._reader.kill()
            self._reader = None


def as_text(data):
    if data is None or len(data) > MAX_BYTES or b"\0" in data[:8000]:
        return None
    return data.decode("utf-8", "replace")


@dataclasses.dataclass
class FileChange:
    path: str
    old_path: str | None
    status: str                                   # A M D R C T
    new_mode: str
    added: dict = dataclasses.field(default_factory=dict)      # new line number -> text
    removed: list = dataclasses.field(default_factory=list)    # (old line number, text)
    binary: bool = False
    new_spec: str | None = None
    old_spec: str | None = None
    new_sha: str | None = None                    # blob ids from `git diff --raw` (key the result cache)
    old_sha: str | None = None

    @property
    def deleted(self) -> bool:
        return self.status == "D"

    @property
    def symlink(self) -> bool:
        return self.new_mode == "120000"

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def ext(self) -> str:
        n = self.name.lower()
        return n[n.rfind("."):] if "." in n else ""


HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _c_unquote(s: str) -> str:
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        return s
    out, i, body = bytearray(), 0, s[1:-1]
    simple = {"n": 10, "t": 9, '"': 34, "\\": 92, "a": 7, "b": 8, "f": 12, "r": 13, "v": 11}
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
            if re.fullmatch(r"[0-7]{3}", body[i + 1:i + 4]):
                out.append(int(body[i + 1:i + 4], 8))
                i += 4
                continue
        out.extend(c.encode("utf-8"))
        i += 1
    return out.decode("utf-8", "replace")


def _patch_path(s: str):
    s = s.rstrip("\t")
    if s == "/dev/null":
        return None
    s = _c_unquote(s)
    return s[2:] if s[:2] in ("a/", "b/") else s


def parse_patch(text: str) -> list:
    sections, cur, in_hunk, old_ln, new_ln = [], None, False, 0, 0
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            cur = {"a": None, "b": None, "added": {}, "removed": [], "binary": False}
            sections.append(cur)
            in_hunk = False
            continue
        if cur is None:
            continue
        if line.startswith("@@ "):
            m = HUNK_RE.match(line)
            if m:
                old_ln, new_ln, in_hunk = int(m.group(1)), int(m.group(2)), True
            continue
        if not in_hunk:
            if line.startswith("--- "):
                cur["a"] = _patch_path(line[4:])
            elif line.startswith("+++ "):
                cur["b"] = _patch_path(line[4:])
            elif line.startswith(("Binary files ", "GIT binary patch")):
                cur["binary"] = True
            continue
        if line.startswith("+"):
            cur["added"][new_ln] = line[1:].rstrip("\r")
            new_ln += 1
        elif line.startswith("-"):
            cur["removed"].append((old_ln, line[1:].rstrip("\r")))
            old_ln += 1
        elif line.startswith(" "):
            old_ln += 1
            new_ln += 1
    return sections


def parse_raw(out: str) -> list:
    items, entries, i = out.split("\0"), [], 0
    while i < len(items):
        meta = items[i]
        if not meta.startswith(":"):
            i += 1
            continue
        fields = meta[1:].split()
        new_mode, status = fields[1], fields[4][0]
        if status in "RC":
            old, new = items[i + 1], items[i + 2]
            i += 3
        else:
            old = new = items[i + 1]
            i += 2
        entries.append((status, new_mode, old, new, fields[2], fields[3]))
    return entries


DIFF_OPTS = ("-c", "core.quotepath=false", "diff", "--no-color", "--no-ext-diff", "--no-textconv",
             "-M", "--ignore-submodules=all")


def collect_changes(repo: Repo, base: str, head) -> list:
    """Changes from base to head. head=None means the index, i.e. what `git commit` would record."""
    target = ["--cached", base] if head is None else [base, head]
    entries = parse_raw(repo.git(*DIFF_OPTS, "--raw", "-z", "--no-abbrev", *target))
    patch_args = (*DIFF_OPTS, "-p", "-U0", "--src-prefix=a/", "--dst-prefix=b/", *target)
    sections = parse_patch(repo.git(*patch_args))
    if len(sections) != len(entries) or any(
            (s["b"] or s["a"]) not in (None, e[2], e[3]) for s, e in zip(sections, entries)):
        sections = []                                 # unusual output: fall back to one diff per file
        for _status, _mode, old, new, *_shas in entries:
            one = parse_patch(repo.git(*patch_args, "--", *dict.fromkeys([old, new])))
            sections.append(one[0] if one else {"added": {}, "removed": [], "binary": False})
    new_ref = ":" if head is None else head + ":"
    return [FileChange(path=new, old_path=None if status == "A" else old, status=status, new_mode=mode,
                       added=sec["added"], removed=sec["removed"], binary=sec["binary"],
                       new_spec=None if status == "D" else new_ref + new,
                       old_spec=None if status == "A" else f"{base}:{old}",
                       new_sha=None if status == "D" else new_sha, old_sha=None if status == "A" else old_sha)
            for (status, mode, old, new, old_sha, new_sha), sec in zip(entries, sections)]


# --------------------------------------------------------------------------- file classes

PY_EXTS = {".py", ".pyw"}
JS_EXTS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte"}
TS_EXTS = {".ts", ".tsx", ".mts", ".cts"}
CODE_EXTS = PY_EXTS | JS_EXTS | {
    ".go", ".rs", ".java", ".kt", ".kts", ".rb", ".php", ".cs", ".c", ".h", ".cc", ".cpp", ".hpp", ".swift",
    ".scala", ".sh", ".bash", ".zsh", ".ps1", ".sql", ".lua", ".dart", ".ex", ".exs", ".r", ".jl", ".pl"}
CONFIG_EXTS = {".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".properties", ".env", ".tf", ".tfvars"}
LOCKFILES = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb",
             "poetry.lock", "uv.lock", "Pipfile.lock", "pdm.lock", "Cargo.lock", "go.sum", "composer.lock",
             "Gemfile.lock"}
CONFIG_PATH = ".aigate/lite.toml"


def is_lockfile(path: str) -> bool:
    return path.rsplit("/", 1)[-1] in LOCKFILES


# --------------------------------------------------------------------------- configuration

DEFAULT_CONFIG = {
    "gate": {"fail_closed": True, "offline": "warn", "max_seconds": 20, "cache": False},
    "rules": {},
    "paths": {
        "ignore": ["**/node_modules/**", "**/vendor/**", "**/third_party/**", "dist/**", "build/**",
                   "**/.venv/**", "**/venv/**", "*.min.js", "*.min.css", "*.map", ".aigate/aigate_lite.py"],
        "tests": ["**/tests/**", "**/test/**", "**/__tests__/**", "**/spec/**", "test_*.py", "*_test.py",
                  "conftest.py", "*.test.*", "*.spec.*", "*_test.go", "*Test.java", "*Tests.java"],
        "sensitive": ["**/auth/**", "**/security/**", "**/payments/**", "**/billing/**", "**/crypto/**",
                      "**/migrations/**"],
        "forbidden": [],
        "forbidden_for_agents": [".aigate/**", ".github/workflows/**", ".pre-commit-config.yaml", ".husky/**",
                                 "lefthook.yml"],
    },
    "limits": {"function_lines": 60, "complexity": 10, "loop_depth": 3, "file_lines": 800,
               "diff_added_lines": 400, "diff_files": 25, "duplicate_lines": 6},
    "deps": {"new_package_days": 30, "cooldown_days": 5, "first_party": [], "private": [],
             "trusted_sources": [], "import_map": {}, "typosquat_min_weekly_downloads": 1000},
    "provenance": {"agent_env": [], "default": "human"},
    "commands": [],
    "allow": [],
}
BUILTIN_AGENT_ENV = ("CLAUDECODE", "AI_AGENT", "GEMINI_CLI", "AIGATE_AGENT")


@dataclasses.dataclass
class Config:
    data: dict
    problems: list
    origin: str

    def __getitem__(self, key):
        return self.data[key]


def _parse_date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None


PRESET_NAMES = ("relaxed", "balanced", "strict")


def _presets() -> dict:
    """Built-in presets, the single source for `preset = ...` and the docs/RULES.md table. Effective config =
    built-in defaults <- preset <- the user's file."""
    strict_security = {r: "fix" for r in RULES if r.startswith("security.") and RULES[r].action == "warn"}
    return {
        "relaxed": {"rules": {"suppress.type": "warn", "suppress.coverage": "warn", "deps.undeclared": "warn",
                              "deps.version_cooldown": "warn", "code.todo": "off", "size.*": "off"},
                    "limits": {"function_lines": 100, "complexity": 15}},
        "balanced": {},
        "strict": {"rules": {**strict_security, "deps.typosquat": "fix", "tests.no_assertions": "fix",
                             "tests.expected_changed": "fix"},
                   "gate": {"offline": "block"},
                   "limits": {"function_lines": 50, "complexity": 8}},
    }


PRESETS = _presets()


def apply_preset(raw: dict, problems: list) -> dict:
    """The user's raw config laid over its preset. A user [rules] key (exact or glob) replaces every preset
    rule it matches, so the user's setting always wins; other sections merge key by key."""
    name = raw.get("preset", "balanced")
    if name not in PRESETS:
        problems.append(f"preset must be relaxed, balanced or strict (got {name!r}); using balanced")
        name = "balanced"
    out = {k: v for k, v in raw.items() if k != "preset"}
    for section, values in PRESETS[name].items():
        user = raw.get(section)
        if user is not None and not isinstance(user, dict):
            continue                                   # merge_config reports the wrong type
        user = user or {}
        if section == "rules":
            kept = {p: a for p, a in values.items() if not any(fnmatch.fnmatchcase(p, k) for k in user)}
            out["rules"] = {**kept, **user}
        else:
            out[section] = {**values, **user}
    return out


def merge_config(raw: dict, problems: list) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for section, value in raw.items():
        if section == "version":
            continue
        if section not in cfg:
            problems.append(f"unknown section [{section}]")
        elif section in ("commands", "allow"):
            if isinstance(value, list):
                cfg[section] = value
            else:
                problems.append(f"[[{section}]] must be an array of tables")
        elif not isinstance(value, dict):
            problems.append(f"[{section}] must be a table")
        elif section == "rules":
            cfg["rules"] = value
        else:
            for key, v in value.items():
                if key in cfg[section]:
                    cfg[section][key] = v
                else:
                    problems.append(f"unknown key {section}.{key}")
    return cfg


def validate_config(cfg: dict, problems: list) -> None:
    rules = {}
    for key, value in (cfg.get("rules") or {}).items():
        if any(fnmatch.fnmatchcase(r, key) for r in FIXED_RULES):
            problems.append(f'rules."{key}" does not apply to {", ".join(sorted(FIXED_RULES))} (it cannot be changed)')
        value = str(value).lower()
        if value not in ("off", "warn", "fix", "block"):
            problems.append(f'rules."{key}" = "{value}": use off, warn, fix or block')
        elif not any(fnmatch.fnmatchcase(r, key) for r in RULES):
            problems.append(f'rules."{key}" matches no rule (see `aigate-lite explain`)')
        else:
            rules[key] = value
    cfg["rules"] = rules

    allows, today = [], utcnow().date()
    for i, entry in enumerate(cfg.get("allow") or [], 1):
        if not isinstance(entry, dict) or not entry.get("rule") or not str(entry.get("reason", "")).strip():
            problems.append(f"[[allow]] #{i} needs a rule and a reason")
            continue
        paths = entry.get("path") or []
        paths = [paths] if isinstance(paths, str) else list(paths)
        matched = [r for r in RULES if fnmatch.fnmatchcase(r, str(entry["rule"]))]
        if not matched:
            problems.append(f"[[allow]] #{i}: rule {entry['rule']!r} matches no rule")
            continue
        if any(RULES[r].action == "block" for r in matched) and not paths and not entry.get("fingerprint"):
            problems.append(f"[[allow]] #{i}: blocking rules need a path or a fingerprint")
            continue
        if entry.get("expires"):
            expires = _parse_date(entry["expires"])
            if expires is None:
                problems.append(f"[[allow]] #{i}: expires must be a date (YYYY-MM-DD)")
                continue
            if expires < today:
                problems.append(f"[[allow]] #{i} for {entry['rule']} expired on {expires} and no longer applies")
                continue
        allows.append({"rule": str(entry["rule"]), "paths": paths, "fingerprint": entry.get("fingerprint"),
                       "reason": str(entry["reason"]).strip()})
    cfg["allow"] = allows

    commands = []
    for i, c in enumerate(cfg.get("commands") or [], 1):
        if not isinstance(c, dict) or not c.get("name") or not c.get("run"):
            problems.append(f"[[commands]] #{i} needs a name and a run command")
            continue
        stage, action = c.get("stage", "push"), c.get("action", "fix")
        if stage not in ("commit", "push", "both") or action not in ("warn", "fix", "block"):
            problems.append(f"[[commands]] {c['name']}: stage must be commit/push/both, action warn/fix/block")
            continue
        when = c.get("when") or []
        commands.append({"name": str(c["name"]), "run": str(c["run"]), "stage": stage, "action": action,
                         "timeout": int(c.get("timeout", 600)), "when": [when] if isinstance(when, str) else when})
    cfg["commands"] = commands

    if cfg["gate"].get("offline") not in ("warn", "block", "skip"):
        problems.append("gate.offline must be warn, block or skip")
        cfg["gate"]["offline"] = "warn"
    if not isinstance(cfg["gate"].get("cache", False), bool):
        problems.append("gate.cache must be true or false")
        cfg["gate"]["cache"] = False
    if cfg["provenance"].get("default") not in ("human", "agent"):
        problems.append("provenance.default must be human or agent")
        cfg["provenance"]["default"] = "human"
    for section in ("paths", "deps"):
        for key, val in cfg[section].items():
            if isinstance(val, str) and key not in ("import_map",):
                cfg[section][key] = [val]
    for key, val in cfg["limits"].items():
        if not isinstance(val, int) or val <= 0:
            problems.append(f"limits.{key} must be a positive integer")
            cfg["limits"][key] = DEFAULT_CONFIG["limits"][key]


def load_config(repo: Repo, source: str, explicit=None) -> Config:
    """source: 'worktree', 'index', or a commit to read .aigate/lite.toml from."""
    problems, text, origin = [], None, "built-in defaults"
    if explicit:
        try:
            with open(explicit, encoding="utf-8") as fh:
                text, origin = fh.read(), explicit
        except OSError as e:
            problems.append(f"cannot read {explicit}: {e}")
    elif source == "worktree":
        path = os.path.join(repo.root, CONFIG_PATH)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                text, origin = fh.read(), CONFIG_PATH
    else:
        data = repo.read((":" if source == "index" else source + ":") + CONFIG_PATH)
        if data is not None:
            text, origin = data.decode("utf-8", "replace"), f"{CONFIG_PATH} @ {source[:10]}"
    raw = {}
    if text is not None:
        try:
            raw = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            hint = ""
            if any(w in str(e).lower() for w in ("escape", "hex value", "unicode")):
                hint = "; on Windows put paths in single quotes ('C:\\path') or use forward slashes"
            problems.append(f"{CONFIG_PATH} is not valid TOML ({e}){hint}; using defaults")
    data = merge_config(apply_preset(raw, problems), problems)
    validate_config(data, problems)
    return Config(data, problems, origin)


BASELINE_PATH = ".aigate/baseline.json"
BASELINE_NEVER = ("gate.", "commands.", "scope.")   # the gate's own state, never accepted by a baseline


def load_baseline(repo, source) -> tuple:
    """({(rule, path, fingerprint)}, problems) from .aigate/baseline.json in the worktree or a commit. Anything
    unreadable is ignored (more findings, never fewer)."""
    if source is None:
        return set(), []
    if source == "worktree":
        try:
            with open(os.path.join(repo.root, BASELINE_PATH), "rb") as fh:
                data = fh.read()
        except OSError:
            return set(), []
    else:                                              # "" = the index (staged content), else a commit
        data = repo.read(f"{source}:{BASELINE_PATH}")
        if data is None:
            return set(), []
    try:
        doc = json.loads(data.decode("utf-8-sig"))
        rows = doc["entries"] if isinstance(doc, dict) and type(doc.get("version")) is int and doc["version"] == 1 \
            else None
    except (ValueError, KeyError, UnicodeDecodeError, RecursionError):
        rows = None
    if not isinstance(rows, list):
        return set(), [f"{BASELINE_PATH} is not a version 1 baseline; ignored (re-create it: aigate-lite baseline)"]
    valid = [e for e in rows if isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ("rule", "path", "fingerprint"))]
    entries = {(e["rule"], e["path"], e["fingerprint"]) for e in valid
               if not e["rule"].startswith(BASELINE_NEVER)}
    bad = len(rows) - len(valid)
    return entries, ([f"{bad} malformed {BASELINE_PATH} entr{'y' if bad == 1 else 'ies'} ignored"] if bad else [])


# --------------------------------------------------------------------------- provenance

AI_TRAILER_RE = re.compile(
    r"(?im)^(?:co-authored-by|generated-by|assisted-by|ai-assisted):.*?\b(claude|copilot|cursor|aider|codex|gemini|"
    r"devin|jules|windsurf|codeium|openhands|tabnine)\b|generated with \[?(claude|copilot|cursor|codex|gemini)")


def detect_provenance(env, extra_vars=(), default="human", messages=(), authors=()):
    """('agent' | 'human', why). Best effort: an agent that hides itself looks human."""
    forced = env.get("AIGATE_PROVENANCE", "").strip().lower()
    if forced == "agent":
        return "agent", "AIGATE_PROVENANCE"
    for var in (*BUILTIN_AGENT_ENV, *extra_vars):
        if env.get(var):
            return "agent", f"${var}"
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        if "(aider)" in env.get(var, ""):
            return "agent", f"${var} contains (aider)"
    for text in messages:
        if AI_TRAILER_RE.search(text or ""):
            return "agent", "AI trailer in a commit message"
    for name in authors:
        if "(aider)" in (name or ""):
            return "agent", "commit author contains (aider)"
    if forced == "human":
        return "human", "AIGATE_PROVENANCE"
    return default, "default" if default == "human" else "provenance.default"


# --------------------------------------------------------------------------- findings and context

@dataclasses.dataclass
class Finding:
    rule: str
    path: str
    line: int | None
    message: str
    action: str
    key: str = ""                  # stable identity for the fingerprint (never a raw secret)
    hint: str | None = None
    allowed: str | None = None     # why it was allowed, when it was
    sensitive: bool = False
    legacy_key: str | None = None  # the 0.1 key, when it differs (it used line numbers)
    base_key: str | None = None    # key before twin numbering (set by number_twins)
    twin: int = 1                  # 1 for the first finding with this rule, file and key; 2, 3, ... after it

    @property
    def meta(self) -> Rule:
        return RULES[self.rule]

    @property
    def fingerprint(self) -> str:
        return fingerprint_v2(self.rule, self.path, self.key or self.message)

    @property
    def legacy_fingerprint(self) -> str:
        return fingerprint(self.rule, self.path, self.legacy_key if self.legacy_key is not None
                           else self.key or self.message)


def make(rule: str, path: str, line, message: str, *, action=None, key="", hint=None, legacy_key=None) -> Finding:
    return Finding(rule, path, line, message, action or RULES[rule].action, key=key, hint=hint, legacy_key=legacy_key)


def number_twins(findings) -> None:
    """Findings with the same rule, file and key (e.g. two identical `except Exception: pass`) get #2, #3, ...
    in line order, so each has its own fingerprint and a baseline entry never covers a new twin."""
    groups = defaultdict(list)
    for f in findings:
        f.base_key = f.key or f.message
        groups[(f.rule, f.path, f.base_key)].append(f)
    for twins in groups.values():
        for n, f in enumerate(sorted(twins, key=lambda f: f.line or 0)[1:], 2):
            if f.legacy_key is None:
                f.legacy_key = f.base_key
            f.key, f.twin = f"{f.base_key}#{n}", n


def _removed_keys(ctx, path) -> Counter:
    """How many findings per (rule, base key) the change's removed lines of `path` carried in the old version:
    the four per-file checks run over the removed lines as if they were added. Outside `check --all` a baseline
    entry only accepts as many added findings as were removed (an edited line keeps its debt; a new copy of a
    baselined pattern is reported)."""
    memo = ctx.cache.setdefault("removed_keys", {})
    if path not in memo:
        found = []
        for fc in ctx.changes:
            if fc.path == path and fc.removed and fc.old_spec and not fc.binary:
                old = dataclasses.replace(fc, path=fc.old_path or fc.path, added=dict(fc.removed), removed=[],
                                          new_spec=fc.old_spec, old_spec=None, new_sha=fc.old_sha, old_sha=None)
                for run in (lambda: _secrets_file(ctx, old, ""), lambda: _suppressions_file(ctx, old),
                            lambda: _code_lines_file(ctx, old), lambda: _python_file(ctx, old) if old.ext == ".py" else []):
                    try:
                        found += run()
                    except Exception:                          # unknown means nothing accepted (fail safe)
                        found = []
                        break
        memo[path] = Counter((f.rule, f.key or f.message) for f in found)
    return memo[path]


class Context:
    def __init__(self, repo, cfg, changes, *, mode, base, head, provenance, registry, merge=False, label="",
                 results=None, full=False):
        self.repo, self.cfg, self.changes = repo, cfg, changes
        self.full = full                       # `check --all`: every tracked file, as if newly added
        self.results = results or ResultCache(enabled=False)
        self.mode, self.base, self.head = mode, base, head
        self.provenance, self.registry, self.merge, self.label = provenance, registry, merge, label
        self.new_ref = ":" if head is None else head + ":"
        self._files = None
        self._texts: dict = {}
        self._asts: dict = {}
        self.parse_failures: set = set()
        self.cache: dict = {}          # per-run memo shared by checkers
        self.baseline: set = set()     # (rule, path, fingerprint) accepted by .aigate/baseline.json

    @property
    def agent(self) -> bool:
        return self.provenance[0] == "agent"

    def text(self, spec):
        if spec is None:
            return None
        if spec not in self._texts:
            self._texts[spec] = as_text(self.repo.read(spec))
        return self._texts[spec]

    def new_text(self, fc):
        return None if fc.symlink else self.text(fc.new_spec)

    def old_text(self, fc):
        return self.text(fc.old_spec)

    def read_new(self, path):
        return self.text(self.new_ref + path)

    @property
    def files(self) -> list:
        """Every path in the tree being committed (index) or pushed (head)."""
        if self._files is None:
            out = (self.repo.git("ls-files", "-z") if self.head is None
                   else self.repo.git("ls-tree", "-r", "-z", "--name-only", self.head))
            self._files = [p for p in out.split("\0") if p]
        return self._files

    def py_tree(self, fc, which="new"):
        """(tree, SyntaxError | None) for the new or old version of a Python file."""
        key = (fc.path, which)
        if key not in self._asts:
            text = self.new_text(fc) if which == "new" else self.old_text(fc)
            tree = err = None
            if text is not None:
                try:
                    tree = ast.parse(text, filename=fc.path)
                except SyntaxError as e:
                    err = e
                except ValueError:
                    pass
                except (RecursionError, MemoryError):
                    self.parse_failures.add(key)     # transient: not a verdict the result cache may keep
            self._asts[key] = (tree, err)
        return self._asts[key]

    def ignored(self, path) -> bool:
        return path_matches(path, self.cfg["paths"]["ignore"])

    def is_test(self, path) -> bool:
        return path_matches(path, self.cfg["paths"]["tests"])

    def sensitive(self, path) -> bool:
        return path_matches(path, self.cfg["paths"]["sensitive"])

    def code_changes(self, exts=None, tests=True):
        """Changed text files worth analysing (not deleted, binary, ignored or lockfiles)."""
        for fc in self.changes:
            if fc.deleted or fc.binary or fc.symlink or self.ignored(fc.path) or is_lockfile(fc.path):
                continue
            if exts is not None and fc.ext not in exts:
                continue
            if not tests and self.is_test(fc.path):
                continue
            yield fc


# --------------------------------------------------------------------------- result cache

class ResultCache:
    """Remembers which files the pure per-file checks found clean, keyed by everything those checks read.

    Only checks that read nothing but one file's blobs, its added/removed lines, its path and the config are
    cached (secrets line scan, suppressions, code lines, Python AST checks), and only a clean verdict is
    stored: a file with findings is always checked again, so no finding text, key or secret ever reaches
    the disk. The key covers both blob ids, a hash of the added/removed lines (git's diff settings can
    change them), the path, the check, the tool version and source, RULES, the whole config and the Python
    version. The file is JSON signed with HMAC-SHA256 under a random key kept next to it (both created
    0600); a bad signature, a malformed entry, a file over 2 MB or any read error means the cache is
    ignored, so tampering can only cause a re-check. It trusts the local user account: whoever can rewrite
    the key file can rewrite the cache."""

    VERSION = 2
    MAX_AGE = 14 * 86400
    MAX_ENTRIES = 5000
    MAX_BYTES = 2_000_000

    def __init__(self, directory=None, enabled=True):
        self.entries: dict = {}
        self.dirty = False
        self.secret = None
        self.path = self.key_path = None
        self._salt = self._cfg = self._cfg_digest = None
        if not enabled or not directory:
            return
        self.path = os.path.join(directory, f"results-v{self.VERSION}.json")
        self.key_path = os.path.join(directory, "results.key")
        try:
            self.secret = self._load_secret(directory)
        except OSError:
            self.secret = None
            return
        try:
            if os.path.getsize(self.path) > self.MAX_BYTES:
                return
            with open(self.path, encoding="utf-8") as fh:
                doc = json.load(fh)
            entries = doc["entries"]
            if doc.get("v") != self.VERSION or not isinstance(entries, dict) or                     not _hmac_ok(self.secret, _canonical(entries), doc.get("mac")):
                return
            if all(isinstance(t, (int, float)) for t in entries.values()):
                self.entries = entries
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError, MemoryError):
            return

    def _load_secret(self, directory):
        try:
            with open(self.key_path, "rb") as fh:
                secret = fh.read()
            if len(secret) == 32:
                return secret
        except FileNotFoundError:
            pass
        os.makedirs(directory, exist_ok=True)
        secret = os.urandom(32)
        _write_private(self.key_path, secret)
        self.entries, self.dirty = {}, True           # a new key invalidates whatever was stored before
        return secret

    @property
    def enabled(self) -> bool:
        return self.secret is not None

    def salt(self, ctx) -> str:
        """Digest of everything outside the file that the cached checks depend on (once per config)."""
        import hashlib
        if self._salt is None:
            try:
                with open(__file__, "rb") as fh:
                    source = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                source = "unknown"
            self._salt = json.dumps([__version__, source, _RULE_ROWS, list(sys.version_info[:2])], default=str)
        if self._cfg is not ctx.cfg:
            text = self._salt + json.dumps(ctx.cfg.data, sort_keys=True, default=str)
            self._cfg, self._cfg_digest = ctx.cfg, hashlib.sha256(text.encode()).hexdigest()
        return self._cfg_digest

    def run(self, check, ctx, fc, compute) -> list:
        if not self.enabled or not fc.new_sha or set(fc.new_sha) == {"0"}:
            return compute()
        import hashlib
        lines = hashlib.sha256(json.dumps([sorted(fc.added.items()), fc.removed]).encode()).hexdigest()
        key = hashlib.sha256(json.dumps([check, fc.path, fc.old_path, fc.status, fc.new_mode, fc.new_sha,
                                         fc.old_sha if fc.old_spec else None, lines, self.salt(ctx)]).encode()).hexdigest()
        now = time.time()
        if key in self.entries:
            PROFILE["cache_hits"] += 1
            if now - self.entries[key] > 86400:            # keep files that keep being checked
                self.entries[key], self.dirty = now, True
            return []
        failures = len(ctx.parse_failures)
        found = compute()
        if not found and len(ctx.parse_failures) == failures:   # a parser that ran out of memory/stack is no verdict
            self.entries[key], self.dirty = now, True
        return found

    def save(self) -> None:
        if not self.enabled or not self.dirty:
            return
        now = time.time()
        fresh = sorted(((k, t) for k, t in self.entries.items() if now - t < self.MAX_AGE),
                       key=lambda kv: kv[1])[-self.MAX_ENTRIES:]
        entries = dict(fresh)
        doc = {"v": self.VERSION, "mac": _hmac(self.secret, _canonical(entries)), "entries": entries}
        try:
            _write_private(self.path, json.dumps(doc).encode("utf-8"))
        except OSError:
            pass


def _write_private(path, data: bytes) -> None:
    """Atomically replace `path` with `data`, created 0600; never leaves a temp file behind."""
    tmp = f"{path}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _canonical(entries) -> bytes:
    return json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hmac(secret, data) -> str:
    import hmac
    return hmac.new(secret, data, "sha256").hexdigest()


def _hmac_ok(secret, data, mac) -> bool:
    import hmac
    return isinstance(mac, str) and hmac.compare_digest(_hmac(secret, data), mac)


# --------------------------------------------------------------------------- registry lookups

MISSING = "missing"
_NOT_CACHED = object()


def default_cache_dir() -> str:
    if os.environ.get("AIGATE_CACHE_DIR"):
        return os.environ["AIGATE_CACHE_DIR"]
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return os.path.join(os.environ["LOCALAPPDATA"], "aigate-lite")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "aigate-lite")


def network_reason(e) -> str:
    """Why a registry request failed, in words a user can act on."""
    import socket
    import ssl
    import urllib.error
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code} from the registry"
    r = getattr(e, "reason", e)
    if isinstance(r, ssl.SSLCertVerificationError) or isinstance(e, ssl.SSLCertVerificationError):
        return "TLS certificate verification failed; behind a company proxy? set SSL_CERT_FILE to its CA bundle"
    if isinstance(r, ssl.SSLError) or isinstance(e, ssl.SSLError):
        return f"TLS error ({getattr(r, 'reason', None) or r}); a proxy that intercepts HTTPS?"
    if isinstance(r, ConnectionRefusedError):
        return "connection refused; check HTTPS_PROXY / HTTP_PROXY"
    if isinstance(r, TimeoutError) or "timed out" in str(r):
        return "timed out"
    if isinstance(r, socket.gaierror):
        return "DNS lookup failed (offline?)"
    if isinstance(e, (ValueError, KeyError, TypeError)):
        return "unexpected response from the registry"
    return f"{type(r).__name__}: {str(r)[:80]}"


def npm_quote(name: str) -> str:
    import urllib.parse
    return urllib.parse.quote(name, safe="@")


class Registry:
    """PyPI / npm / OSV lookups with a local cache. Every getter returns None when the answer is unknown."""

    TTL = {"exists": (86400, 3600), "info": (86400, 3600), "downloads": (7 * 86400, 3600), "osv": (86400, 3600)}

    def __init__(self, *, offline=False, timeout=6.0, budget=20.0, use_cache=True, fixture=None):
        self.offline, self.timeout = offline, timeout
        self.deadline = time.monotonic() + budget
        self.fixture = None
        if fixture:
            with open(fixture, encoding="utf-8") as fh:
                self.fixture = json.load(fh)
        self.mem: dict = {}
        self.cache: dict = {}
        self.cache_path = None
        self.dirty = False
        self.failures = 0
        self.failure_reason = None
        if use_cache and self.fixture is None:
            self.cache_path = os.path.join(default_cache_dir(), "registry-v1.json")
            try:
                with open(self.cache_path, encoding="utf-8") as fh:
                    self.cache = json.load(fh).get("entries", {})
            except (OSError, ValueError, AttributeError):
                self.cache = {}

    def exists(self, eco, name):
        return self.get(("exists", eco, name))

    def info(self, eco, name):
        return self.get(("info", eco, name))

    def downloads(self, eco, name):
        return self.get(("downloads", eco, name))

    def vulns(self, eco, name, version):
        return self.get(("osv", eco, name, version))

    def prefetch(self, keys) -> None:
        keys = [k for k in dict.fromkeys(keys) if k not in self.mem]
        if not keys:
            return
        if self.fixture is not None or self.offline or len(keys) == 1:
            for k in keys:
                self.get(k)
            return
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(self.get, keys))

    def get(self, key):
        if key in self.mem:
            return self.mem[key]
        if self.fixture is not None:
            val = self._from_fixture(key)
        else:
            val = self._cached(key)
            if val is _NOT_CACHED:
                val = self._fetch(key)
                if val is not None:
                    self.cache["|".join(map(str, key))] = {"t": time.time(), "v": val}
                    self.dirty = True
        self.mem[key] = val
        return val

    def _cached(self, key):
        entry = self.cache.get("|".join(map(str, key)))
        if not isinstance(entry, dict) or "v" not in entry:
            return _NOT_CACHED
        good, bad = self.TTL[key[0]]
        ttl = bad if entry["v"] in (False, MISSING) else good
        return _NOT_CACHED if time.time() - entry.get("t", 0) > ttl else entry["v"]

    def _from_fixture(self, key):
        kind, eco, name = key[0], key[1], key[2]
        table = self.fixture.get(eco) or {}
        want = norm_py(name) if eco == "pypi" else name
        entry = next((v for k, v in table.items() if (norm_py(k) if eco == "pypi" else k) == want), MISSING)
        if entry == "unknown":
            return None
        if kind == "exists":
            return entry not in (None, MISSING)
        if entry in (None, MISSING):
            return {"info": MISSING, "downloads": 0, "osv": []}[kind]
        if kind == "info":
            return {"name": name, "created": entry.get("created"), "versions": entry.get("versions", {}),
                    "latest": entry.get("latest"), "yanked": entry.get("yanked", []),
                    "deprecated": entry.get("deprecated")}
        if kind == "downloads":
            return entry.get("downloads", 10 ** 6)
        return (entry.get("vulns") or {}).get(key[3], [])

    def _http(self, url, method="GET", body=None):
        import gzip
        import urllib.error
        import urllib.request
        headers = {"User-Agent": f"aigate-lite/{__version__}", "Accept-Encoding": "gzip",
                   "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        timeout = max(1.0, min(self.timeout, self.deadline - time.monotonic()))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return resp.status, raw
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return e.code, b""
            raise

    def _fetch(self, key):
        if self.offline or time.monotonic() > self.deadline:
            return None
        import urllib.error
        import urllib.parse
        kind, eco, name = key[0], key[1], key[2]
        try:
            if kind == "exists":
                if eco == "pypi":
                    return self._http(f"https://pypi.org/pypi/{norm_py(name)}/json", method="HEAD")[0] == 200
                status, raw = self._http(f"https://registry.npmjs.org/-/package/{npm_quote(name)}/dist-tags")
                return status == 200 and "latest" in json.loads(raw or b"{}")
            if kind == "info":
                return self._fetch_info(eco, name)
            if kind == "downloads":
                if eco == "npm":
                    status, raw = self._http("https://api.npmjs.org/downloads/point/last-week/"
                                             + urllib.parse.quote(name, safe="@/"))
                    return int(json.loads(raw).get("downloads", 0)) if status == 200 else 0
                status, raw = self._http(f"https://pypistats.org/api/packages/{norm_py(name)}/recent")
                return int(json.loads(raw)["data"]["last_week"]) if status == 200 else 0
            body = json.dumps({"package": {"name": name, "ecosystem": "PyPI" if eco == "pypi" else "npm"},
                               "version": key[3]}).encode()
            status, raw = self._http("https://api.osv.dev/v1/query", method="POST", body=body)
            return sorted({v["id"] for v in json.loads(raw).get("vulns", []) if v.get("id")}) if status == 200 else None
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:
            self.failures += 1
            self.failure_reason = self.failure_reason or network_reason(e)
            return None

    def _fetch_info(self, eco, name):
        if eco == "pypi":
            status, raw = self._http(f"https://pypi.org/pypi/{norm_py(name)}/json")
            if status != 200:
                return MISSING
            d = json.loads(raw)
            versions, yanked = {}, []
            for ver, files in (d.get("releases") or {}).items():
                times = [f["upload_time_iso_8601"] for f in files if f.get("upload_time_iso_8601")]
                if times:
                    versions[ver] = min(times)
                if files and all(f.get("yanked") for f in files):
                    yanked.append(ver)
            info = d.get("info") or {}
            inactive = any("Development Status :: 7 - Inactive" in c for c in info.get("classifiers") or [])
            return {"name": info.get("name") or name, "created": min(versions.values()) if versions else None,
                    "versions": versions, "latest": info.get("version"), "yanked": yanked,
                    "deprecated": "marked 'Development Status :: 7 - Inactive' on PyPI" if inactive else None}
        status, raw = self._http(f"https://registry.npmjs.org/{npm_quote(name)}")
        if status != 200:
            return MISSING
        d = json.loads(raw)
        times = d.get("time") or {}
        if "unpublished" in times or not d.get("versions"):
            return MISSING
        latest = (d.get("dist-tags") or {}).get("latest")
        versions = {v: times[v] for v in d["versions"] if times.get(v)}
        meta = d["versions"].get(latest) if latest else None
        deprecated = str(meta["deprecated"])[:160] if isinstance(meta, dict) and meta.get("deprecated") else None
        return {"name": name, "created": times.get("created") or (min(versions.values()) if versions else None),
                "versions": versions, "latest": latest, "yanked": [], "deprecated": deprecated}

    def save(self) -> None:
        if not self.dirty or not self.cache_path:
            return
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            now = time.time()
            entries = {k: v for k, v in self.cache.items() if now - v.get("t", 0) < 30 * 86400}
            tmp = f"{self.cache_path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"v": 1, "entries": entries}, fh)
            os.replace(tmp, self.cache_path)
        except OSError:
            pass


# --------------------------------------------------------------------------- checks: secrets

PROVIDER_SECRETS = [(label, re.compile(rx)) for label, rx in [
    ("AWS access key ID", r"\b((?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16})\b"),
    ("AWS secret access key",
     r"(?i)\baws_?(?:secret|private)[a-z_]*['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])"),
    ("GitHub token", r"\b(gh[pousr]_[A-Za-z0-9]{36,255})\b"),
    ("GitHub fine-grained token", r"\b(github_pat_[A-Za-z0-9_]{60,255})\b"),
    ("GitLab token", r"\b(glpat-[A-Za-z0-9_\-]{20,})"),
    ("Slack token", r"\b(xox[abposr]-[A-Za-z0-9-]{10,})"),
    ("Slack webhook", r"(https://hooks\.slack\.com/services/T[A-Za-z0-9_]+/B[A-Za-z0-9_]+/[A-Za-z0-9_]{20,})"),
    ("Discord webhook", r"(https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]{30,})"),
    ("Stripe live key", r"\b((?:sk|rk)_live_[A-Za-z0-9]{20,})"),
    ("Google API key", r"\b(AIza[0-9A-Za-z_\-]{35})(?![0-9A-Za-z_\-])"),
    ("Google OAuth client secret", r"\b(GOCSPX-[A-Za-z0-9_\-]{28})"),
    ("OpenAI API key", r"\b(sk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{40,})"),
    ("OpenAI API key", r"\b(sk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20})\b"),
    ("Anthropic API key", r"\b(sk-ant-(?:api|admin)\d\d-[A-Za-z0-9_\-]{60,})"),
    ("Hugging Face token", r"\b(hf_[A-Za-z0-9]{34,})\b"),
    ("npm token", r"\b(npm_[A-Za-z0-9]{36})\b"),
    ("PyPI token", r"\b(pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_\-]{50,})"),
    ("SendGrid API key", r"\b(SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43})\b"),
    ("Telegram bot token", r"\b(\d{8,10}:AA[A-Za-z0-9_\-]{33})\b"),
    ("Shopify token", r"\b(shp(?:at|ss|ca|pa)_[a-fA-F0-9]{32})\b"),
    ("DigitalOcean token", r"\b(do[opr]_v1_[a-f0-9]{64})\b"),
    ("Azure storage key", r"(AccountKey=[A-Za-z0-9+/]{86}==)"),
    ("Private key", r"(-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----)"),
]]
DOC_PLACEHOLDER = re.compile(r"(?i)example|xxxx|0{8}|12345678|abcdefgh")
CRED_WORD = (r"(?:pass(?:word|wd|phrase)?|pwd|secret|token|api[_.-]?key|apikey|access[_.-]?key|private[_.-]?key|"
             r"client[_.-]?secret|auth[_.-]?(?:key|token)|credentials?|bearer)")
GENERIC_ASSIGN = re.compile(
    r"(?i)(?P<name>[a-z0-9_.\-]*" + CRED_WORD + r"[a-z0-9_.\-]*)['\"]?\]?\s*(?::=|=>|==|=|:)\s*"
    r"(?:[rbuf]{1,2})?(?P<q>['\"`])(?P<val>[^'\"`\s]{8,200})(?P=q)")
GENERIC_UNQUOTED = re.compile(
    r"(?i)^\s*(?:export\s+|ENV\s+|set\s+)?(?P<name>[a-z0-9_.\-]*" + CRED_WORD + r"[a-z0-9_.\-]*)\s*[:=]\s*"
    r"(?P<val>[^\s'\"#$]{8,200})\s*$")
JWT_RE = re.compile(r"\b(eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})")
CONN_RE = re.compile(r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|rediss?|amqps?|mssql|sqlserver)://"
                     r"[^\s:/@'\"]*:(?P<val>[^\s@'\"/]{3,})@(?P<host>[^\s/'\":?]+)")
PLACEHOLDER_VALUE = re.compile(
    r"(?i)your[_-]|xxx|\*\*\*|example|change[_-]?me|placeholder|dummy|sample|fake|mock|redacted|replace[_-]?me|"
    r"insert[_-]|todo|tbd|<|>|\$\{|\{\{|%\(|%s|process\.env|os\.environ|getenv|"
    r"^(?:none|null|undefined|true|false|password\d*|passwd|secret|test\w*|\.+|(.)\1+)$")
NAME_EXCLUDE = re.compile(
    r"(?i)(?:^|[_.\-])(?:field|name|type|url|uri|path|file|dir|env|var|header|label|prompt|placeholder|regex|re|"
    r"pattern|length|len|min|max|hint|message|msg|format|policy|endpoint|count|limit|ttl|timeout|expiry|hash|salt|"
    r"algorithm|alg|scheme|prefix|suffix|id|mode|class|kind|enabled|required|help|description|title|widget|input|"
    r"column|col|attr|param|arg|error|errors|rule|rules|validator|strength)$")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "db", "database", "postgres", "mysql", "mariadb", "redis",
               "mongo", "mongodb", "rabbitmq", "host.docker.internal"}
DEFAULT_PASSWORDS = {"postgres", "password", "root", "admin", "secret", "example", "test", "pass", "user", "guest",
                     "changeme", "mysql", "redis"}


def _looks_like_secret(name: str, val: str) -> bool:
    if NAME_EXCLUDE.search(name) or PLACEHOLDER_VALUE.search(val):
        return False
    if re.fullmatch(r"[A-Z][A-Z0-9_]{3,}", val):                    # the name of an env var, not its value
        return False
    if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", val):       # a dotted reference
        return False
    if val.startswith(("http://", "https://", "/", "./", "../", "~")):
        return False
    return entropy(val) >= 3.0


def secret_findings(ctx, fc, line_no, text, where="") -> list:
    """Secrets on one added line."""
    import hashlib
    found, seen = [], set()

    def add(rule, val, what, action=None):
        if val in seen:
            return
        seen.add(val)
        found.append(make(rule, fc.path, line_no, f"{what}{where}", action=action,
                          key=hashlib.sha1(val.encode("utf-8", "replace")).hexdigest()))

    for label, rx in PROVIDER_SECRETS:
        for m in rx.finditer(text):
            val = m.group(1)
            if label == "Private key":
                add("secrets.hardcoded", val, "private key block")
            elif not DOC_PLACEHOLDER.search(val):
                add("secrets.hardcoded", val, f"{label} ({redact(val)})")
    lname = fc.name.lower()
    if found or ctx.ignored(fc.path) or is_lockfile(fc.path) or lname == ".env" or lname.startswith(".env."):
        return found                                              # env files are judged as a whole by secrets.env_file
    soft = "warn" if ctx.is_test(fc.path) else None              # fixtures in tests are usually fake
    for m in GENERIC_ASSIGN.finditer(text):
        if _looks_like_secret(m.group("name"), m.group("val")):
            add("secrets.generic", m.group("val"), f"'{m.group('name')}' is set to a literal ({redact(m.group('val'))})", soft)
    if fc.ext in CONFIG_EXTS or fc.ext in {".sh", ".bash", ".zsh"} or fc.name.startswith("Dockerfile"):
        m = GENERIC_UNQUOTED.match(text)
        if m and _looks_like_secret(m.group("name"), m.group("val")):
            add("secrets.generic", m.group("val"), f"'{m.group('name')}' is set to a literal ({redact(m.group('val'))})", soft)
    for m in JWT_RE.finditer(text):
        add("secrets.generic", m.group(1), f"JSON Web Token ({redact(m.group(1))})", soft)
    for m in CONN_RE.finditer(text):
        val, host = m.group("val"), m.group("host").lower()
        if val.lower() not in DEFAULT_PASSWORDS and not PLACEHOLDER_VALUE.search(val):
            local = host in LOCAL_HOSTS or host.endswith(".local")
            add("secrets.generic", val, f"password in a connection string for {host}", "warn" if local else soft)
    return found


def check_secrets(ctx, changes=None, where="") -> list:
    out = []
    for fc in changes if changes is not None else ctx.changes:
        if fc.deleted or fc.binary or fc.symlink or not fc.added:
            continue
        out += ctx.results.run("secrets" + where, ctx, fc, lambda fc=fc: _secrets_file(ctx, fc, where))
    return out


def _secrets_file(ctx, fc, where) -> list:
    out = []
    for line_no, text in fc.added.items():
        if len(text) <= 4000:
            out.extend(secret_findings(ctx, fc, line_no, text, where))
    return out


ENV_SAFE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults", ".tmpl", ".tpl", ".schema")
KEY_FILE_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", "_netrc", ".git-credentials",
                  ".htpasswd", ".pgpass"}
KEY_FILE_EXTS = {".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".ppk", ".tfstate"}
SECRETISH_KEY = re.compile(r"(?i)pass|secret|token|key|private|credential|auth|dsn|database_url|conn")
PUBLIC_ENV_PREFIX = re.compile(r"(?i)^(?:next_public|nuxt_public|expo_public|vite|react_app|gatsby|public)_")


def _env_secret_keys(text: str) -> list:
    keys = []
    for line in text.splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*[=:]\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip().strip("'\"")
        if (val and not val.startswith(("${", "#")) and not PLACEHOLDER_VALUE.search(val)
                and SECRETISH_KEY.search(key) and not PUBLIC_ENV_PREFIX.match(key)):
            keys.append(key)
    return keys


def check_env_files(ctx, changes=None, where="") -> list:
    out = []
    for fc in changes if changes is not None else ctx.changes:
        if fc.deleted or fc.symlink:
            continue
        lname, reason, action = fc.name.lower(), None, None
        if lname == ".env" or lname.startswith(".env.") or lname.endswith(".env"):
            if lname.endswith(ENV_SAFE_SUFFIXES) or lname in ("example.env", "sample.env", "template.env"):
                continue
            text = ctx.new_text(fc)
            keys = _env_secret_keys(text) if text is not None else ["(unreadable)"]
            if keys:
                reason = f"env file with secret-looking values ({', '.join(keys[:3])}{'…' if len(keys) > 3 else ''})"
            elif lname.endswith(".local"):
                reason = "local env file (.local files hold machine-specific secrets by convention)"
            else:
                reason, action = "env file committed (no secret-looking values found; keep it that way)", "warn"
        elif lname in KEY_FILE_NAMES:
            reason = "credential file"
        elif fc.ext in KEY_FILE_EXTS or lname.endswith(".tfstate.backup"):
            reason = "key store or state file (these usually contain secrets)"
        elif fc.ext in (".pem", ".key", ".p8"):
            text = ctx.new_text(fc)
            if text is not None and "PRIVATE KEY" in text:
                reason = "private key file"
        elif lname == ".npmrc":
            if re.search(r"(?m)(?:_authToken|_auth|_password)\s*=\s*(?!\$\{)\S", ctx.new_text(fc) or ""):
                reason = ".npmrc with an auth token"
        elif lname == ".pypirc":
            if re.search(r"(?m)^\s*password\s*[:=]\s*(?!\$\{|%\()\S", ctx.new_text(fc) or ""):
                reason = ".pypirc with a password"
        elif fc.ext == ".json" and re.search(r"credential|service[-_]?account|client[-_]?secret|adminsdk", lname):
            text = ctx.new_text(fc) or ""
            if '"private_key"' in text or '"client_secret"' in text:
                reason = "cloud credential file"
        if reason:
            out.append(make("secrets.env_file", fc.path, None, reason + where, action=action, key=fc.path))
    return out


def check_env_not_ignored(ctx) -> list:
    if ctx.mode != "staged":
        return []
    try:
        names = sorted(os.listdir(ctx.repo.root))
    except OSError:
        return []
    candidates = []
    for name in names:
        lname = name.lower()
        if not (lname == ".env" or lname.startswith(".env.")) or lname.endswith(ENV_SAFE_SUFFIXES):
            continue
        if os.path.isfile(os.path.join(ctx.repo.root, name)):
            candidates.append(name)
    if not candidates:
        return []
    tracked = set(ctx.repo.git("ls-files", "-z", "--", *(":(literal)" + n for n in candidates)).split("\0"))
    candidates = [n for n in candidates if n not in tracked]
    if not candidates:
        return []
    PROFILE["git_calls"] += 1
    proc = subprocess.run(["git", "check-ignore", "-z", "--stdin"], cwd=ctx.repo.root, capture_output=True,
                          input="\0".join(candidates).encode("utf-8"))
    if proc.returncode not in (0, 1):
        return []                              # as before: a failing check-ignore reports nothing
    ignored = set(proc.stdout.decode("utf-8", "replace").split("\0"))
    return [make("secrets.env_not_ignored", name, None, f"{name} exists but is not in .gitignore", key=name)
            for name in candidates if name not in ignored]


# --------------------------------------------------------------------------- checks: paths and scope

GATE_FILES = (".aigate/**", ".pre-commit-config.yaml", ".husky/**", "lefthook.yml", "lefthook.yaml",
              ".lefthook.yml", ".github/workflows/**", ".gitlab-ci.yml")


def scope_path(repo: Repo) -> str:
    return repo.private_path("aigate/scope")


def load_scope(repo: Repo) -> list:
    env = os.environ.get("AIGATE_SCOPE")
    if env:
        return [p for p in re.split(r"[,\s]+", env) if p]
    try:
        with open(scope_path(repo), encoding="utf-8") as fh:
            return [ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return []


def check_paths(ctx) -> list:
    out, cfg = [], ctx.cfg["paths"]
    scope = load_scope(ctx.repo)
    for fc in ctx.changes:
        paths = [fc.path] + ([fc.old_path] if fc.old_path and fc.old_path != fc.path else [])
        forbidden = next((p for p in paths if path_matches(p, cfg["forbidden"])), None)
        if forbidden:
            out.append(make("scope.forbidden_path", fc.path, None, f"{forbidden} is listed in [paths] forbidden",
                            key=forbidden))
        agent_forbidden = ctx.agent and next((p for p in paths if path_matches(p, cfg["forbidden_for_agents"])), None)
        if agent_forbidden and not forbidden:
            out.append(make("scope.forbidden_path", fc.path, None,
                            f"{agent_forbidden} may not be changed by an AI agent (detected via {ctx.provenance[1]})",
                            key=agent_forbidden))
        if not forbidden and not agent_forbidden and any(path_matches(p, GATE_FILES) for p in paths):
            out.append(make("gate.config_changed", fc.path, None, f"{fc.path} configures the gate or git hooks",
                            key=fc.path))
        if scope and not is_lockfile(fc.path) and not any(path_matches(p, scope) for p in paths):
            shown = ", ".join(scope[:3]) + ("…" if len(scope) > 3 else "")
            out.append(make("scope.out_of_scope", fc.path, None, f"{fc.path} is outside the declared scope ({shown})",
                            key=fc.path))
    return out


# --------------------------------------------------------------------------- dependency data

REMOVED_STDLIB = {
    "distutils": ((3, 12), "setuptools, sysconfig or shutil"), "imp": ((3, 12), "importlib"),
    "asynchat": ((3, 12), "asyncio"), "asyncore": ((3, 12), "asyncio"), "smtpd": ((3, 12), "aiosmtpd"),
    "aifc": ((3, 13), "a third-party audio library"), "audioop": ((3, 13), "audioop-lts"),
    "cgi": ((3, 13), "urllib.parse / email.message, or legacy-cgi"), "cgitb": ((3, 13), "traceback"),
    "chunk": ((3, 13), "a third-party library"), "crypt": ((3, 13), "hashlib, bcrypt or passlib"),
    "imghdr": ((3, 13), "filetype or Pillow"), "mailcap": ((3, 13), "mimetypes"),
    "msilib": ((3, 13), "a third-party library"), "nis": ((3, 13), "a third-party library"),
    "nntplib": ((3, 13), "a third-party library"), "ossaudiodev": ((3, 13), "a third-party library"),
    "pipes": ((3, 13), "shlex.quote and subprocess"), "sndhdr": ((3, 13), "filetype"),
    "spwd": ((3, 13), "a third-party library"), "sunau": ((3, 13), "a third-party library"),
    "telnetlib": ((3, 13), "telnetlib3"), "uu": ((3, 13), "base64"), "xdrlib": ((3, 13), "a third-party library"),
    "lib2to3": ((3, 13), "libcst or parso"),
}
# import name -> distribution name(s) where they differ ("a|b" lists alternatives)
IMPORT_MAP = {
    "yaml": "PyYAML", "cv2": "opencv-python|opencv-python-headless|opencv-contrib-python", "sklearn": "scikit-learn",
    "skimage": "scikit-image", "PIL": "Pillow", "bs4": "beautifulsoup4", "dateutil": "python-dateutil",
    "dotenv": "python-dotenv", "jwt": "PyJWT", "jose": "python-jose", "magic": "python-magic",
    "Crypto": "pycryptodome|pycrypto", "Cryptodome": "pycryptodomex", "OpenSSL": "pyOpenSSL", "serial": "pyserial",
    "usb": "pyusb", "zmq": "pyzmq", "git": "GitPython", "github": "PyGithub", "gitlab": "python-gitlab",
    "docx": "python-docx", "pptx": "python-pptx", "fitz": "PyMuPDF", "telegram": "python-telegram-bot",
    "discord": "discord.py", "attr": "attrs", "MySQLdb": "mysqlclient", "psycopg2": "psycopg2|psycopg2-binary",
    "psycopg": "psycopg|psycopg-binary", "multipart": "python-multipart", "slugify": "python-slugify",
    "win32api": "pywin32", "win32con": "pywin32", "win32com": "pywin32", "win32gui": "pywin32",
    "pywintypes": "pywin32", "gi": "PyGObject", "wx": "wxPython", "cairo": "pycairo", "nacl": "PyNaCl",
    "Bio": "biopython", "mpl_toolkits": "matplotlib", "pkg_resources": "setuptools", "faiss": "faiss-cpu|faiss-gpu",
    "googleapiclient": "google-api-python-client", "grpc": "grpcio", "dns": "dnspython", "ldap": "python-ldap",
    "kafka": "kafka-python", "websocket": "websocket-client", "socketio": "python-socketio",
    "engineio": "python-engineio", "rest_framework": "djangorestframework", "corsheaders": "django-cors-headers",
    "environ": "django-environ", "decouple": "python-decouple", "debug_toolbar": "django-debug-toolbar",
    "storages": "django-storages", "allauth": "django-allauth", "markdown_it": "markdown-it-py",
    "factory": "factory-boy", "bson": "pymongo", "gridfs": "pymongo", "pdfminer": "pdfminer.six",
    "argon2": "argon2-cffi", "Levenshtein": "Levenshtein|python-Levenshtein", "speech_recognition": "SpeechRecognition",
    "umap": "umap-learn", "osgeo": "GDAL", "ffmpeg": "ffmpeg-python", "vlc": "python-vlc", "Xlib": "python-xlib",
    "OpenGL": "PyOpenGL", "_pytest": "pytest", "pyximport": "Cython", "graphql": "graphql-core",
    "weaviate": "weaviate-client", "vertexai": "google-cloud-aiplatform", "community": "python-louvain",
    "snappy": "python-snappy", "llama_cpp": "llama-cpp-python", "pinecone": "pinecone|pinecone-client",
    "google.protobuf": "protobuf", "google.auth": "google-auth", "google.oauth2": "google-auth",
    "google.generativeai": "google-generativeai", "google.genai": "google-genai",
}
NAMESPACE_TOPS = {"google", "azure", "zope", "jaraco", "ruamel", "sphinxcontrib", "backports", "opentelemetry",
                  "oslo", "flufl", "repoze", "plone", "aws_cdk"}
NODE_BUILTINS = set(
    "assert async_hooks buffer child_process cluster console constants crypto dgram diagnostics_channel dns domain "
    "events fs http http2 https inspector module net os path perf_hooks process punycode querystring readline repl "
    "stream string_decoder sys timers tls trace_events tty url util v8 vm wasi worker_threads zlib test sqlite sea".split())
POPULAR_PYPI = set(
    "boto3 botocore urllib3 requests setuptools certifi charset-normalizer idna typing-extensions python-dateutil "
    "packaging s3transfer six numpy pyyaml fsspec pip cryptography cffi pycparser pandas importlib-metadata rsa "
    "pyasn1 protobuf attrs jmespath zipp wheel click platformdirs pydantic pydantic-core jinja2 markupsafe filelock "
    "pluggy pytest virtualenv tomli pyjwt colorama wrapt psutil decorator sqlalchemy werkzeug flask django fastapi "
    "uvicorn starlette httpx httpcore h11 anyio sniffio aiohttp multidict yarl frozenlist aiosignal scipy matplotlib "
    "pillow scikit-learn joblib tqdm regex pyparsing pytz tzdata soupsieve beautifulsoup4 lxml openpyxl greenlet "
    "docutils pygments rich tabulate toml tomlkit iniconfig exceptiongroup coverage pytest-cov mock redis celery "
    "kombu psycopg2 psycopg2-binary pymysql mysqlclient google-auth google-cloud-storage grpcio websockets "
    "websocket-client paramiko pynacl bcrypt oauthlib requests-oauthlib gunicorn black isort flake8 mypy pylint "
    "ruff pre-commit tensorflow keras torch torchvision transformers tokenizers huggingface-hub datasets accelerate "
    "openai anthropic langchain tiktoken nltk spacy sympy networkx seaborn plotly streamlit gradio opencv-python "
    "imageio scikit-image xgboost lightgbm statsmodels pyarrow polars dask numba cython alembic marshmallow "
    "jsonschema cachetools more-itertools termcolor python-dotenv loguru sentry-sdk docker kubernetes ansible "
    "selenium playwright scrapy tornado gevent typer pyinstaller poetry hatchling build twine sphinx mkdocs jupyter "
    "notebook ipython ipykernel tenacity arrow pendulum faker hypothesis responses moto simplejson ujson orjson "
    "msgpack xmltodict chardet emoji unidecode passlib itsdangerous pymongo elasticsearch pika pyserial pygame pyqt5 "
    "pyside6 shapely geopandas h5py xlrd xlsxwriter python-docx pypdf pypdf2 reportlab markdown bleach slack-sdk "
    "stripe twilio pytest-mock pytest-asyncio pytest-xdist tox nox pydantic-settings sqlmodel peewee aiofiles "
    "watchdog schedule apscheduler pyzmq uvloop python-multipart dnspython pyopenssl".split())
POPULAR_NPM = set(
    "react react-dom vue svelte next nuxt express koa fastify lodash lodash-es underscore axios node-fetch got "
    "request superagent moment dayjs date-fns luxon chalk commander yargs minimist debug dotenv uuid nanoid "
    "classnames clsx prop-types redux react-redux @reduxjs/toolkit zustand mobx rxjs typescript ts-node tslib "
    "@types/node @types/react webpack webpack-cli vite rollup esbuild parcel @babel/core @babel/preset-env eslint "
    "prettier jest mocha chai sinon vitest cypress playwright @playwright/test puppeteer jsdom supertest nodemon pm2 "
    "concurrently cross-env rimraf mkdirp glob fs-extra chokidar semver minimatch micromatch yaml js-yaml ajv joi "
    "yup zod validator body-parser cors helmet morgan cookie-parser express-session passport jsonwebtoken bcrypt "
    "bcryptjs mongoose mongodb pg mysql mysql2 sqlite3 sequelize typeorm prisma @prisma/client knex redis ioredis "
    "socket.io ws graphql @apollo/client tailwindcss postcss autoprefixer sass less styled-components "
    "@emotion/react @mui/material antd bootstrap jquery d3 chart.js three async bluebird ramda immutable immer "
    "react-router react-router-dom @tanstack/react-query swr formik react-hook-form framer-motion react-icons sharp "
    "multer nodemailer winston pino inquirer ora execa shelljs cheerio xml2js papaparse xlsx marked markdown-it "
    "highlight.js dompurify crypto-js ethers web3 firebase firebase-admin aws-sdk @aws-sdk/client-s3 stripe twilio "
    "openai @anthropic-ai/sdk langchain discord.js electron react-native expo next-auth @nestjs/core @nestjs/common "
    "reflect-metadata class-validator class-transformer core-js qs form-data mime iconv-lite husky lint-staged "
    "lerna nx turbo".split())
TRUSTED_DEFAULT = ("https://pypi.org/", "https://files.pythonhosted.org/", "https://registry.npmjs.org/",
                   "https://registry.yarnpkg.com/")


# --------------------------------------------------------------------------- manifests

@dataclasses.dataclass
class Dep:
    eco: str                     # pypi | npm
    name: str
    spec: str = ""
    source: str = "registry"     # registry | git | url | path | index | workspace
    line: int | None = None
    origin: str = ""
    alias: str | None = None     # npm "npm:<target>" alias


REQ_NAME = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[^\]]*\])?\s*(.*)$")


def _target_kind(target: str) -> str:
    if re.match(r"^(?:git\+|git:|git@|hg\+|svn\+|bzr\+)", target):
        return "git"
    if re.match(r"^https?://", target):
        return "url"
    return "path"


def parse_pep508(text: str):
    """(name, spec, source) for one requirement string, or None."""
    m = REQ_NAME.match(text or "")
    if not m:
        return None
    name, rest = m.group(1), m.group(3).split(";", 1)[0].strip()
    if rest.startswith("@"):
        url = rest[1:].strip()
        return name, url, _target_kind(url) if not url.startswith(("file:", ".", "/")) else "path"
    rest = re.sub(r"\s*--hash[=\s]\S+", "", rest).strip()
    if rest.startswith("(") and rest.endswith(")"):
        rest = rest[1:-1].strip()
    return name, rest, "registry"


def parse_requirements(text: str, path: str):
    deps, sources, logical, buf, first = {}, [], [], "", None
    for no, raw in enumerate(text.splitlines(), 1):
        first = first or no
        if raw.rstrip().endswith("\\"):
            buf += raw.rstrip()[:-1] + " "
            continue
        logical.append((first, buf + raw))
        buf, first = "", None
    if buf:
        logical.append((first, buf))
    for no, raw in logical:
        line = re.sub(r"(?:^|\s)#.*$", "", raw).strip()
        if not line or line.lower().startswith(("-r", "--requirement", "-c", "--constraint")):
            continue
        m = re.match(r"^(?:-i|--index-url|--extra-index-url|-f|--find-links)(?:\s+|=)(\S+)", line)
        if m:
            sources.append((no, "index", m.group(1)))
            continue
        m = re.match(r"^(?:-e|--editable)(?:\s+|=)(\S+)", line)
        if m or re.match(r"^(?:git\+|hg\+|svn\+|bzr\+|https?://|file:|\.{1,2}[/\\]|/)", line):
            target = m.group(1) if m else line.split()[0]
            egg = re.search(r"#egg=([A-Za-z0-9._-]+)", target)
            name = egg.group(1) if egg else target
            deps["@" + target] = Dep("pypi", name, target, _target_kind(target), no, path)
            continue
        if line.startswith("-"):
            continue
        parsed = parse_pep508(line)
        if parsed:
            deps[norm_py(parsed[0])] = Dep("pypi", parsed[0], parsed[1], parsed[2], no, path)
    return deps, sources


def _poetry_dep(name, val, path) -> Dep:
    if isinstance(val, list):
        val = val[0] if val else ""
    if isinstance(val, dict):
        for key, kind in (("git", "git"), ("url", "url"), ("path", "path")):
            if val.get(key):
                return Dep("pypi", name, str(val[key]), kind, None, path)
        if val.get("source"):
            return Dep("pypi", name, str(val.get("version", "")), "index", None, path)
        val = val.get("version", "")
    spec = str(val).strip()
    if re.fullmatch(r"\d+(?:\.\d+)*", spec):
        spec = "==" + spec                       # a bare version is an exact pin in Poetry
    return Dep("pypi", name, "" if spec == "*" else spec, "registry", None, path)


def parse_pyproject(text: str, path: str):
    try:
        d = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None, []
    deps, sources = {}, []

    def add(req):
        parsed = parse_pep508(req) if isinstance(req, str) else None
        if parsed:
            deps[norm_py(parsed[0])] = Dep("pypi", parsed[0], parsed[1], parsed[2], None, path)

    project, tool = d.get("project") or {}, d.get("tool") or {}
    uv, poetry = tool.get("uv") or {}, tool.get("poetry") or {}
    groups = [project.get("dependencies") or [], (d.get("build-system") or {}).get("requires") or [],
              uv.get("dev-dependencies") or []]
    groups += list((project.get("optional-dependencies") or {}).values())
    groups += list((d.get("dependency-groups") or {}).values())
    groups += list(((tool.get("pdm") or {}).get("dev-dependencies") or {}).values())
    for group in groups:
        for req in group if isinstance(group, list) else []:
            add(req)
    tables = [poetry.get("dependencies") or {}, poetry.get("dev-dependencies") or {}]
    tables += [(g or {}).get("dependencies") or {} for g in (poetry.get("group") or {}).values() if isinstance(g, dict)]
    for table in tables:
        for name, val in table.items():
            if name.lower() != "python":
                deps[norm_py(name)] = _poetry_dep(name, val, path)
    for name, src in (uv.get("sources") or {}).items():
        if isinstance(src, dict):
            kind = next((k for k, key in (("workspace", "workspace"), ("git", "git"), ("url", "url"),
                                          ("path", "path"), ("index", "index")) if src.get(key)), None)
            if kind:
                dep = deps.get(norm_py(name)) or Dep("pypi", name, "", "registry", None, path)
                dep.source = kind
                dep.spec = str(src.get(kind)) if kind in ("git", "url", "path") else dep.spec
                deps[norm_py(name)] = dep
    for idx in list(uv.get("index") or []) + list(poetry.get("source") or []):
        if isinstance(idx, dict) and idx.get("url"):
            sources.append((None, "index", str(idx["url"])))
    return deps, sources


def parse_pipfile(text: str, path: str):
    try:
        d = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None, []
    deps, sources = {}, []
    for section in ("packages", "dev-packages"):
        for name, val in (d.get(section) or {}).items():
            if isinstance(val, dict):
                kind = "git" if val.get("git") else "path" if (val.get("path") or val.get("file")) else \
                    "index" if val.get("index") else None
                if kind:
                    deps[norm_py(name)] = Dep("pypi", name, str(val.get("git") or val.get("path") or ""), kind, None, path)
                    continue
                val = val.get("version", "")
            spec = str(val).strip()
            deps[norm_py(name)] = Dep("pypi", name, "" if spec == "*" else spec, "registry", None, path)
    for src in d.get("source") or []:
        if isinstance(src, dict) and src.get("url"):
            sources.append((None, "index", str(src["url"])))
    return deps, sources


def parse_setup_cfg(text: str, path: str):
    import configparser
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read_string(text)
    except configparser.Error:
        return None, []
    lines = []
    for key in ("install_requires", "setup_requires", "tests_require"):
        if cp.has_option("options", key):
            lines += cp.get("options", key).splitlines()
    if cp.has_section("options.extras_require"):
        for _k, v in cp.items("options.extras_require"):
            lines += v.splitlines()
    deps = {}
    for ln in lines:
        parsed = parse_pep508(ln.strip()) if ln.strip() and not ln.strip().startswith("#") else None
        if parsed:
            deps[norm_py(parsed[0])] = Dep("pypi", parsed[0], parsed[1], parsed[2], None, path)
    return deps, []


NPM_DEP_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


def classify_npm(spec):
    """(source, alias target, spec)."""
    s = str(spec).strip()
    if s.startswith("npm:"):
        t = s[4:]
        at = t.find("@", 1)
        return "registry", (t[:at] if at > 0 else t), (t[at + 1:] if at > 0 else "")
    if s.startswith(("workspace:", "catalog:")):
        return "workspace", None, s
    if s.startswith(("file:", "link:", "portal:", ".", "/", "~/")):
        return "path", None, s
    if s.startswith(("git+", "git:", "git@", "github:", "gitlab:", "bitbucket:", "gist:")) or \
            re.fullmatch(r"[\w.-]+/[\w.-]+(?:#.*)?", s):
        return "git", None, s
    if s.startswith(("http://", "https://")):
        return "url", None, s
    return "registry", None, s


def parse_package_json(text: str, path: str):
    try:
        d = json.loads(text)
    except ValueError:
        return None, []
    if not isinstance(d, dict):
        return None, []
    deps = {}
    for section in NPM_DEP_SECTIONS:
        table = d.get(section)
        if isinstance(table, dict):
            for name, spec in table.items():
                source, alias, spec2 = classify_npm(spec)
                deps[name] = Dep("npm", name, spec2, source, None, path, alias)
    return deps, []


def manifest_kind(path: str):
    base = path.rsplit("/", 1)[-1]
    low = base.lower()
    if low == "pyproject.toml":
        return "pyproject"
    if base == "Pipfile":
        return "pipfile"
    if low == "setup.cfg":
        return "setupcfg"
    if low == "setup.py":
        return "setup.py"
    if low == "package.json":
        return "npm"
    if low.endswith((".txt", ".in")) and ("requirements" in low or "constraints" in low
                                           or "/requirements/" in "/" + path.lower()):
        return "requirements"
    return None


def parse_manifest(kind, text, path):
    if text is None:
        return None, []
    if kind == "setup.py":
        names = re.findall(r"['\"]([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?:[<>=!~;]|['\"])", text)
        return {norm_py(n): Dep("pypi", n, "", "registry", None, path) for n in names}, []
    parser = {"requirements": parse_requirements, "pyproject": parse_pyproject, "pipfile": parse_pipfile,
              "setupcfg": parse_setup_cfg, "npm": parse_package_json}[kind]
    return parser(text, path)


def find_line(text, needle, added):
    """Line of the first occurrence of `needle`, preferring added lines."""
    if not text or not needle:
        return None
    rx = re.compile(r"(?i)(?<![A-Za-z0-9_.-])" + re.escape(needle) + r"(?![A-Za-z0-9_-])")
    hit = None
    for i, ln in enumerate(text.splitlines(), 1):
        if rx.search(ln):
            if i in added:
                return i
            hit = hit or i
    return hit


def safe_url(url: str) -> str:
    url = re.sub(r"//[^/@\s]+@", "//***@", url)          # never print credentials embedded in URLs
    return url if len(url) <= 90 else url[:87] + "..."


def classify_source(url: str, trusted) -> str:
    u = url.strip().strip("'\"")
    if u.startswith(("file:", "link:", "workspace:", "portal:", ".", "/")) or re.match(r"^[A-Za-z]:[\\/]", u):
        return "local"
    if u.startswith(("git+", "git:", "git@", "github:", "gitlab:", "bitbucket:", "ssh://")) or ".git#" in u \
            or u.endswith(".git"):
        return "git"
    if u.startswith(tuple(trusted)):
        return "trusted"
    if u.startswith(("http://", "https://")):
        return "url"
    return "git" if re.search(r"github|gitlab|bitbucket", u) else "url"


def lockfile_urls(fname: str, line: str) -> list:
    if fname in ("package-lock.json", "npm-shrinkwrap.json"):
        return re.findall(r'"resolved"\s*:\s*"([^"]+)"', line)
    if fname == "yarn.lock":
        out = re.findall(r'^\s*resolved\s+"?([^"\s]+)"?', line)
        m = re.match(r'^\s*resolution:\s*"?(?:@[^@"\s/]+/)?[^@"\s]+@([^"\s]+)', line)
        if m and not m.group(1).startswith(("npm:", "workspace:", "patch:")):
            out.append(m.group(1))
        return out
    if fname == "pnpm-lock.yaml":
        return re.findall(r'(?:tarball|repo):\s*["\']?([^"\'\s,}]+)', line)
    if fname in ("uv.lock", "pdm.lock"):
        return [("git+" + u if k == "git" else u) for k, u in re.findall(r'\b(git|url|registry)\s*=\s*"([^"]+)"', line)]
    if fname == "poetry.lock":
        m = re.match(r'^\s*type\s*=\s*"(git|url)"', line)
        return [f"{m.group(1)}+poetry-source"] if m else re.findall(r'^\s*url\s*=\s*"([^"]+)"', line)
    if fname == "Pipfile.lock":
        return [("git+" if k == "git" else "") + u for k, u in re.findall(r'"(git|file)"\s*:\s*"([^"]+)"', line)]
    return []


# --------------------------------------------------------------------------- imports

def dotted_name(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _catches_import_error(node) -> bool:
    for h in node.handlers:
        if h.type is None:
            return True
        types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
        if any((dotted_name(t) or "").split(".")[-1] in ("ImportError", "ModuleNotFoundError", "Exception",
                                                          "BaseException") for t in types):
            return True
    return False


def iter_imports(tree) -> list:
    """(top, dotted, line, optional) for absolute imports; optional = guarded by try/except or a platform check."""
    out = []

    def walk(stmts, optional):
        for node in stmts:
            if isinstance(node, ast.Import):
                out.extend((a.name.split(".")[0], a.name, node.lineno, optional) for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    out.append((node.module.split(".")[0], node.module, node.lineno, optional))
            elif isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
                walk(node.body, optional or _catches_import_error(node))
                for h in node.handlers:
                    walk(h.body, True)
                walk(node.orelse, optional)
                walk(node.finalbody, optional)
            elif isinstance(node, ast.If):
                test = ast.dump(node.test)
                guarded = optional or any(k in test for k in ("platform", "'name'", "version_info", "TYPE_CHECKING"))
                walk(node.body, guarded)
                walk(node.orelse, guarded)
            elif isinstance(node, ast.Match):
                for case in node.cases:
                    walk(case.body, optional)
            elif hasattr(node, "body") and isinstance(node.body, list):
                walk(node.body, optional)
                walk(getattr(node, "orelse", []) or [], optional)

    walk(tree.body, False)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str) \
                and dotted_name(node.func) in ("importlib.import_module", "import_module", "__import__"):
            name = node.args[0].value
            if name and not name.startswith("."):
                out.append((name.split(".")[0], name, node.lineno, True))
    return out


@dataclasses.dataclass
class Imp:
    eco: str
    module: str
    lookup: str
    path: str
    line: int
    optional: bool
    manifest: bool       # does the project have a manifest at all


def find_site_packages(root: str) -> list:
    import glob
    bases = [os.path.join(root, d) for d in (".venv", "venv", "env", ".env")]
    bases += [os.environ[v] for v in ("VIRTUAL_ENV", "CONDA_PREFIX") if os.environ.get(v)]
    out = []
    for base in bases:
        if os.path.isdir(base):
            for pattern in ("lib/python3*/site-packages", "lib64/python3*/site-packages", "Lib/site-packages"):
                out.extend(glob.glob(os.path.join(base, pattern)))
    return list(dict.fromkeys(out))


def _dist_tops(dist) -> set:
    """Top-level import names of an installed distribution, as importlib.metadata.packages_distributions()
    finds them, but read straight from RECORD: `dist.files` stats every listed file, which took seconds."""
    import csv
    import inspect
    tops = (dist.read_text("top_level.txt") or "").split()
    if tops:
        return set(tops)
    record = dist.read_text("RECORD")
    paths = ([row[0] for row in csv.reader(record.splitlines()) if row] if record
             else [str(f) for f in dist.files or []])
    names = set()
    for p in paths:
        parts = p.split("/")                 # no "\\" handling, exactly like the stdlib (keeps results identical)
        name = parts[0] if len(parts) > 1 else (inspect.getmodulename(parts[0]) or parts[0])
        if name and "." not in name:
            names.add(name)
    return names


def python_environment(ctx):
    """(importable top-level names, import -> distributions) from this interpreter and project venvs."""
    if "py_env" in ctx.cache:
        return ctx.cache["py_env"]
    import csv
    import importlib.metadata
    unreadable = (OSError, ValueError, LookupError, csv.Error)    # broken metadata of one distribution: skip it
    names, mapping = set(), defaultdict(set)
    for dist in importlib.metadata.distributions():
        try:
            dname, tops = (dist.metadata["Name"] if dist.metadata else None), _dist_tops(dist)
        except unreadable:
            continue
        for top in tops:
            names.add(top)
            if dname:
                mapping[top].add(dname)
    for sp in find_site_packages(ctx.repo.root):
        try:
            entries = os.listdir(sp)
        except OSError:
            continue
        for entry in entries:
            if entry.endswith(".py"):
                names.add(entry[:-3])
            elif entry.endswith((".so", ".pyd")):
                names.add(entry.split(".")[0])
            elif "." not in entry and entry != "__pycache__" and os.path.isdir(os.path.join(sp, entry)):
                names.add(entry)
        for dist in importlib.metadata.distributions(path=[sp]):
            try:
                dname = dist.metadata["Name"] if dist.metadata else None
                tops = _site_tops(dist) if dname else ()
            except unreadable:
                continue
            for t in tops:
                names.add(t)
                mapping[t].add(dname)
    ctx.cache["py_env"] = (names, mapping)
    return names, mapping


def _site_tops(dist) -> set:
    """Top-level import names of a distribution in a project venv: top_level.txt, else its installed files."""
    tops = (dist.read_text("top_level.txt") or "").split()
    if not tops:
        for f in dist.files or []:
            first = f.parts[0] if f.parts else ""
            if first and not first.endswith((".dist-info", ".egg-info", ".pth", ".data")) \
                    and first not in ("..", "__pycache__", "bin"):
                tops.append(first[:-3] if first.endswith(".py") else first.split(".")[0])
    return set(tops)


def _importable(top: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(top) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def python_declared(ctx):
    """(declared normalised names, has_manifest, requires-python lower bound)."""
    if "py_declared" in ctx.cache:
        return ctx.cache["py_declared"]
    declared, has_manifest, req_py = set(), False, None
    for path in ctx.files:
        kind = manifest_kind(path)
        if kind in (None, "npm") or ctx.ignored(path):
            continue
        text = ctx.read_new(path)
        if text is None:
            continue
        has_manifest = True
        deps, _ = parse_manifest(kind, text, path)
        declared |= {norm_py(d.name) for d in (deps or {}).values()}
        if kind == "pyproject" and req_py is None:
            m = re.search(r'requires-python\s*=\s*["\'][^"\']*>=\s*(\d+)\.(\d+)', text)
            req_py = (int(m.group(1)), int(m.group(2))) if m else None
    ctx.cache["py_declared"] = (declared, has_manifest, req_py)
    return ctx.cache["py_declared"]


def _module_names(paths) -> set:
    names = set()
    for p in paths:
        parts = p.split("/")
        if parts[-1].endswith((".py", ".pyi", ".so", ".pyd", ".pyx")):
            names.update(parts[:-1])
            names.add(parts[-1].split(".")[0])
    return names


def python_local_modules(ctx, tops=None) -> set:
    """Module and package names in the tree. Given `tops`, the answer is exact for those names only, and git
    lists just the matching paths instead of every file (the difference is large in big repos)."""
    if "py_local" in ctx.cache:
        return ctx.cache["py_local"]
    if tops is not None and ctx._files is None and ctx.head is None and len(tops) <= 64 \
            and all(t.isidentifier() for t in tops):
        if not tops:
            return set()
        specs = [s for t in sorted(tops) for s in (f":(glob)**/{t}/**", f":(glob)**/{t}.*")]
        return _module_names(p for p in ctx.repo.git("ls-files", "-z", "--", *specs).split("\0") if p) & set(tops)
    ctx.cache["py_local"] = _module_names(ctx.files)
    return ctx.cache["py_local"]


def import_dists(top, dotted, user_map, env_map) -> list:
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        key = ".".join(parts[:i])
        if key in user_map:
            return [str(user_map[key])]
    if top in env_map:
        return sorted(env_map[top])
    for i in range(len(parts), 0, -1):
        key = ".".join(parts[:i])
        if key in IMPORT_MAP:
            return IMPORT_MAP[key].split("|")
    if top in NAMESPACE_TOPS and len(parts) > 1:
        return ["-".join(parts[:3])] + (["-".join(parts[:2])] if len(parts) > 2 else [])
    return [top]


def is_declared(top, dotted, dists, declared) -> bool:
    if not declared:
        return False
    t = norm_py(top)
    if ({norm_py(d) for d in dists} | {t, norm_py(dotted)}) & declared:
        return True
    if {f"python-{t}", f"py{t}", f"py-{t}", f"{t}-python", f"{t}py", f"{t}-py"} & declared:
        return True
    return any(d.startswith(t + "-") for d in declared)


def python_imports(ctx):
    """Findings that need no network, plus candidates to look up on PyPI."""
    findings, candidates = [], []
    files = list(ctx.code_changes(PY_EXTS))
    if not files:
        return findings, candidates
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    dcfg = ctx.cfg["deps"]
    new_imports = []                      # (fc, imports added by this change)
    for fc in files:
        tree, _ = ctx.py_tree(fc)
        if tree is None:
            continue
        old_tree = ctx.py_tree(fc, "old")[0] if fc.old_spec else None
        old_tops = {t for t, *_ in iter_imports(old_tree)} if old_tree else set()
        seen, found = set(), []
        for top, dotted, line, optional in iter_imports(tree):
            if line not in fc.added or top in old_tops or dotted in seen:
                continue
            seen.add(dotted)
            found.append((top, dotted, line, optional))
        new_imports.append((fc, found))
    # Only names that are not stdlib, configured or private need the tree, manifests or installed packages,
    # and each of those is looked up only when the first such import shows up.
    maybe_local = {top for _, found in new_imports for top, *_ in found
                   if top not in stdlib and top not in REMOVED_STDLIB and top not in dcfg["first_party"]
                   and not path_matches(top, dcfg["private"])}
    local = python_local_modules(ctx, maybe_local)
    for fc, found in new_imports:
        for top, dotted, line, optional in found:
            if top in REMOVED_STDLIB:
                req_py = python_declared(ctx)[2]
                removed, repl = REMOVED_STDLIB[top]
                breaks = sys.version_info[:2] >= removed or (req_py is not None and req_py >= removed)
                findings.append(make("deps.removed_stdlib", fc.path, line,
                                     f"`{top}` was removed in Python {removed[0]}.{removed[1]}; use {repl}",
                                     action=None if breaks else "warn", key=top))
                continue
            if top in stdlib or top in local or top in dcfg["first_party"] or path_matches(top, dcfg["private"]):
                continue
            declared, has_manifest, _req_py = python_declared(ctx)
            env_names, env_map = python_environment(ctx)
            dists = import_dists(top, dotted, dcfg["import_map"], env_map)
            if is_declared(top, dotted, dists, declared):
                continue
            if top in env_names or _importable(top):
                if has_manifest and not optional:
                    findings.append(make("deps.undeclared", fc.path, line,
                                         f"`import {dotted}`: {dists[0]} is installed but not declared in a manifest",
                                         action="warn" if ctx.is_test(fc.path) else None, key=dists[0]))
                continue
            candidates.append(Imp("pypi", dotted, dists[0], fc.path, line, optional, has_manifest))
    return findings, candidates


JS_IMPORT_RES = [re.compile(r"""\bfrom\s*(['"])([^'"\n]+)\1"""),
                 re.compile(r"""^\s*import\s*(['"])([^'"\n]+)\1"""),
                 re.compile(r"""\bimport\s*\(\s*(['"])([^'"\n]+)\1\s*\)"""),
                 re.compile(r"""\brequire\s*\(\s*(['"])([^'"\n]+)\1\s*\)""")]


def js_specifiers(line: str) -> list:
    s = line.strip()
    if s.startswith(("//", "*", "/*")) or re.match(r"^(?:import|export)\s+type\b", s):
        return []
    return [m.group(2) for rx in JS_IMPORT_RES for m in rx.finditer(line)]


def js_package(spec: str):
    if spec.startswith("npm:"):
        spec = spec[4:]
    elif spec.startswith(("node:", ".", "/", "~", "#", "$", "virtual:", "data:", "http:", "https:", "bun:",
                          "jsr:", "astro:", "@/")) or "!" in spec:
        return None
    parts = spec.split("/")
    name = "/".join(parts[:2]) if spec.startswith("@") and len(parts) > 1 else parts[0]
    name = re.sub(r"(?<=.)@[^/]*$", "", name)                   # drop a version suffix: pkg@1.2
    if not re.fullmatch(r"(?:@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*", name, re.I):
        return None
    return name


def js_project(ctx):
    """(manifests: dir -> declared names, first-party names, deno?)"""
    if "js" in ctx.cache:
        return ctx.cache["js"]
    manifests, first = {}, set(ctx.cfg["deps"]["first_party"])
    base_dirs = ["src"]
    for p in ctx.files:
        base = p.rsplit("/", 1)[-1]
        if ctx.ignored(p):
            continue
        if base == "package.json":
            try:
                d = json.loads(ctx.read_new(p) or "")
            except ValueError:
                continue
            if isinstance(d, dict):
                if isinstance(d.get("name"), str):
                    first.add(d["name"])
                names = set()
                for s in NPM_DEP_SECTIONS:
                    if isinstance(d.get(s), dict):
                        names |= set(d[s])
                manifests[p.rsplit("/", 1)[0] if "/" in p else ""] = names
        elif re.fullmatch(r"(?:ts|js)config(?:\.[\w-]+)?\.json", base):
            try:
                opts = (loads_jsonc(ctx.read_new(p) or "{}") or {}).get("compilerOptions") or {}
            except (ValueError, AttributeError):
                continue
            for key in opts.get("paths") or {}:
                first.add(key.split("/")[0].rstrip("*"))
            if isinstance(opts.get("baseUrl"), str):
                base_dirs.append(opts["baseUrl"].strip("./"))
    for p in ctx.files:
        for d in base_dirs:
            prefix = (d + "/") if d else ""
            if p.startswith(prefix):
                first.add(p[len(prefix):].split("/")[0].split(".")[0])
    deno = any(p.rsplit("/", 1)[-1] in ("deno.json", "deno.jsonc") for p in ctx.files)
    ctx.cache["js"] = (manifests, first, deno)
    return ctx.cache["js"]


def js_imports(ctx):
    findings, candidates = [], []
    files = list(ctx.code_changes(JS_EXTS))
    if not files:
        return findings, candidates
    manifests, first, deno = js_project(ctx)
    if not manifests:
        return findings, candidates                 # no package.json: nothing to compare against
    for fc in files:
        old = ctx.old_text(fc) or ""
        old_specs = {s for ln in old.splitlines() for s in js_specifiers(ln)}
        dirs = fc.path.split("/")[:-1]
        ancestors = ["/".join(dirs[:i]) for i in range(len(dirs), -1, -1)]
        declared = set().union(*(manifests.get(a, set()) for a in ancestors))
        if not any(a in manifests for a in ancestors):
            continue
        seen = set()
        for line, text in fc.added.items():
            for spec in js_specifiers(text):
                pkg = js_package(spec)
                if not pkg or spec in old_specs or pkg in seen or (deno and not spec.startswith("npm:")):
                    continue
                seen.add(pkg)
                if pkg in NODE_BUILTINS or pkg.split("/")[0] in NODE_BUILTINS or pkg in declared \
                        or pkg in first or pkg.split("/")[0] in first or path_matches(pkg, ctx.cfg["deps"]["private"]):
                    continue
                if any(os.path.isdir(os.path.join(ctx.repo.root, a, "node_modules", pkg)) for a in ancestors):
                    findings.append(make("deps.undeclared", fc.path, line,
                                         f"'{pkg}' is in node_modules but not in package.json", key=pkg))
                    continue
                candidates.append(Imp("npm", spec, pkg, fc.path, line, False, True))
    return findings, candidates


# --------------------------------------------------------------------------- checks: dependencies

def exact_pin(eco, spec):
    s = (spec or "").strip()
    if eco == "pypi":
        m = re.fullmatch(r"===?\s*([0-9][^\s,;*]*)", s)
    else:
        m = re.fullmatch(r"=?v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)", s)
    return m.group(1) if m else None


def match_version(pinned, versions):
    if pinned in versions:
        return pinned
    want = release_tuple(pinned)
    suffix = re.sub(r"^\s*v?\d+(?:\.\d+)*", "", pinned).lower()
    for v in versions:
        rt = release_tuple(v)
        if rt and want and _cmp(rt, want) == 0 and re.sub(r"^\s*v?\d+(?:\.\d+)*", "", v).lower() == suffix:
            return v
    return None


def age_text(days: float) -> str:
    return f"{int(days)} days" if days >= 2 else f"{max(1, int(days * 24))} hours"


def unverified(ctx, path, line, message, key):
    mode = ctx.cfg["gate"]["offline"]
    if mode == "skip":
        return []
    return [make("deps.unverified", path, line, message, action="block" if mode == "block" else None, key=key)]


def typosquat_of(eco, name):
    n = norm_py(name) if eco == "pypi" else name.lower()
    popular = POPULAR_PYPI if eco == "pypi" else POPULAR_NPM
    if n in popular:
        return None
    for p in sorted(popular):
        limit = 1 if len(p) < 8 else 2
        if abs(len(p) - len(n)) <= limit and edit_distance(n, p, limit) <= limit:
            return p
    return None


def evaluate_dep(ctx, dep, old, path) -> list:
    out, reg, dcfg = [], ctx.registry, ctx.cfg["deps"]
    name = dep.alias or dep.name
    label = f"{dep.name} (alias of {name})" if dep.alias else dep.name
    where = "PyPI" if dep.eco == "pypi" else "npm"
    info = reg.info(dep.eco, name)
    if info is None:
        return unverified(ctx, path, dep.line, f"{label}: could not reach {where} to verify it", name)
    if info == MISSING:
        return [make("deps.hallucinated", path, dep.line, f"{label} does not exist on {where}", key=name)]
    versions = info.get("versions") or {}
    if not versions:
        return [make("deps.hallucinated", path, dep.line, f"{label} is on {where} but has no installable releases", key=name)]
    if old is None:
        age = age_days(info.get("created"))
        if age is not None and age < dcfg["new_package_days"]:
            out.append(make("deps.new_package", path, dep.line, f"{label} was first published {age_text(age)} ago", key=name))
        if info.get("deprecated"):
            out.append(make("deps.deprecated", path, dep.line, f"{label} is deprecated: {info['deprecated']}", key=name))
    pinned, latest = exact_pin(dep.eco, dep.spec), info.get("latest")
    if pinned:
        version = match_version(pinned, versions)
        if version is None:
            return out + [make("deps.hallucinated", path, dep.line, f"{label} has no version {pinned} on {where}",
                               key=f"{name}@{pinned}")]
        if version in (info.get("yanked") or []):
            out.append(make("deps.deprecated", path, dep.line, f"{label} {pinned} was yanked by its maintainers",
                            action="fix", key=f"{name}@{pinned}"))
        age = age_days(versions.get(version))
        if age is not None and age < dcfg["cooldown_days"]:
            old_pin = exact_pin(dep.eco, old.spec) if old else None
            if not (old_pin and reg.vulns(dep.eco, name, old_pin)):       # security bumps are exempt
                out.append(make("deps.version_cooldown", path, dep.line,
                                f"{label} {pinned} was published {age_text(age)} ago (cooldown: {dcfg['cooldown_days']} days)",
                                key=f"{name}@{pinned}"))
        vulns = reg.vulns(dep.eco, name, version)
        if vulns:
            shown = ", ".join(vulns[:4]) + ("…" if len(vulns) > 4 else "")
            out.append(make("deps.vulnerable", path, dep.line, f"{label} {pinned} has known vulnerabilities: {shown}",
                            key=f"{name}@{pinned}"))
        lt, pt = release_tuple(latest), release_tuple(pinned)
        if lt and pt and lt[0] > pt[0]:
            out.append(make("deps.outdated", path, dep.line, f"{label} is pinned to {pinned}; the latest is {latest}",
                            key=f"{name}@{pinned}"))
    elif latest and latest in versions:
        allows = (py_spec_allows if dep.eco == "pypi" else npm_spec_allows)(dep.spec, latest)
        age = age_days(versions.get(latest))
        if allows is not False and age is not None and age < dcfg["cooldown_days"]:
            out.append(make("deps.version_cooldown", path, dep.line,
                            f"{label} '{dep.spec or '*'}' allows {latest}, published {age_text(age)} ago; "
                            "a fresh install will pick it up", action="warn", key=f"{name}@{latest}"))
    return out


def check_deps(ctx) -> list:
    out, pending = [], []
    reg, dcfg = ctx.registry, ctx.cfg["deps"]
    trusted = TRUSTED_DEFAULT + tuple(dcfg["trusted_sources"])
    for fc in ctx.changes:
        if fc.deleted or fc.binary or fc.symlink or ctx.ignored(fc.path):
            continue
        kind = manifest_kind(fc.path)
        if kind and kind != "setup.py":
            new_text = ctx.new_text(fc)
            new_deps, new_sources = parse_manifest(kind, new_text, fc.path)
            if new_deps is None:
                continue
            old_text = ctx.old_text(fc)
            old_deps, old_sources = parse_manifest(kind, old_text, fc.path) if old_text else ({}, [])
            old_deps, old_urls = old_deps or {}, {s[2] for s in old_sources}
            for line, _kind, url in new_sources:
                if url not in old_urls and classify_source(url, trusted) in ("git", "url"):
                    out.append(make("deps.untrusted_source", fc.path, line or find_line(new_text, url, fc.added),
                                    f"custom package index {safe_url(url)}", key=url))
            for key, dep in new_deps.items():
                old = old_deps.get(key)
                if old and (old.spec, old.source, old.alias) == (dep.spec, dep.source, dep.alias):
                    continue
                dep.line = dep.line or find_line(new_text, dep.name, fc.added)
                if dep.source in ("git", "url"):
                    out.append(make("deps.untrusted_source", fc.path, dep.line,
                                    f"{dep.name} is installed from {dep.source} ({safe_url(dep.spec)})", key=dep.name))
                elif dep.source == "path":
                    out.append(make("deps.local_source", fc.path, dep.line,
                                    f"{dep.name} is installed from a local path", key=dep.name))
                elif dep.source == "registry" and not path_matches(dep.alias or dep.name, dcfg["private"]):
                    pending.append((dep, old, fc))
        elif is_lockfile(fc.path):
            seen, shown = set(), 0
            for line, text in fc.added.items():
                for url in lockfile_urls(fc.name, text):
                    kind_ = classify_source(url, trusted)
                    if url in seen or kind_ not in ("git", "url"):
                        continue
                    seen.add(url)
                    shown += 1
                    if shown <= 5:
                        out.append(make("deps.untrusted_source", fc.path, line,
                                        f"lockfile resolves a package from a {kind_} source: {safe_url(url)}", key=url))

    py_find, py_cand = python_imports(ctx)
    js_find, js_cand = js_imports(ctx)
    out += py_find + js_find
    imports = py_cand + js_cand
    squats = [(dep, fc, p) for dep, old, fc in pending if old is None
              for p in [typosquat_of(dep.eco, dep.alias or dep.name)] if p]

    reg.prefetch([("info", d.eco, d.alias or d.name) for d, _, _ in pending]
                 + [("exists", i.eco, i.lookup) for i in imports]
                 + [("downloads", d.eco, d.alias or d.name) for d, _, _ in squats])
    osv = []
    for dep, old, _fc in pending:
        info = reg.info(dep.eco, dep.alias or dep.name)
        if isinstance(info, dict):
            for spec in (dep.spec, old.spec if old else None):
                pin = exact_pin(dep.eco, spec)
                version = pin and match_version(pin, info.get("versions") or {})
                if version:
                    osv.append(("osv", dep.eco, dep.alias or dep.name, version))
    reg.prefetch(osv)

    for dep, old, fc in pending:
        out += evaluate_dep(ctx, dep, old, fc.path)
    for dep, fc, popular in squats:
        downloads = reg.downloads(dep.eco, dep.alias or dep.name)
        if downloads is not None and downloads >= dcfg["typosquat_min_weekly_downloads"]:
            continue
        extra = f" and had {downloads} downloads last week" if downloads is not None else ""
        out.append(make("deps.typosquat", fc.path, dep.line,
                        f"{dep.alias or dep.name} is one or two edits from the popular '{popular}'{extra}",
                        key=dep.alias or dep.name))
    for imp in imports:
        where = "PyPI" if imp.eco == "pypi" else "npm"
        exists = reg.exists(imp.eco, imp.lookup)
        label = f"`import {imp.module}`" if imp.eco == "pypi" else f"'{imp.module}'"
        if exists is None:
            out += unverified(ctx, imp.path, imp.line, f"{label}: could not reach {where} to check '{imp.lookup}'",
                              imp.lookup)
        elif exists is False:
            out.append(make("deps.hallucinated", imp.path, imp.line,
                            f"{label}: no package '{imp.lookup}' on {where}, and it is not installed or declared",
                            action="warn" if imp.optional else None, key=imp.lookup))
        elif not imp.optional:
            out.append(make("deps.undeclared", imp.path, imp.line,
                            f"{label}: '{imp.lookup}' is not installed here and not declared"
                            + ("" if imp.manifest else " (the project has no manifest)"),
                            action=None if imp.manifest else "warn", key=imp.lookup))
    return out


# --------------------------------------------------------------------------- checks: tests

SKIP_MARKS = {"skip", "skipif", "xfail", "skipunless", "expectedfailure"}
ASSERT_CALLS = {"pytest.raises", "pytest.warns", "pytest.deprecated_call", "pytest.fail", "self.fail"}
HARNESS_HOOKS = {"pytest_runtest_makereport", "pytest_runtest_call", "pytest_runtest_protocol", "pytest_runtest_setup",
                 "pytest_runtest_teardown", "pytest_collection_modifyitems", "pytest_report_teststatus",
                 "pytest_pyfunc_call", "pytest_ignore_collect", "pytest_sessionfinish", "pytest_terminal_summary",
                 "pytest_deselected", "pytest_collect_file", "pytest_runtest_logreport"}
PYTEST_NARROWING = {"addopts", "testpaths", "python_files", "python_classes", "python_functions", "norecursedirs"}
COVERAGE_NARROWING = {"omit", "exclude_lines", "exclude_also", "fail_under", "source", "include", "partial_branches"}


@dataclasses.dataclass
class TInfo:
    name: str
    line: int
    asserts: int
    consts: Counter
    marks: set
    cases: int | None
    body: str


def _marks(nodes) -> set:
    out = set()
    for d in nodes:
        last = (dotted_name(d.func if isinstance(d, ast.Call) else d) or "").split(".")[-1].lower()
        if last in SKIP_MARKS:
            out.add(last)
    return out


def _pytestmark(body) -> set:
    out = set()
    for n in body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in n.targets):
            out |= _marks(n.value.elts if isinstance(n.value, (ast.List, ast.Tuple)) else [n.value])
    return out


def _body_skips(fn) -> set:
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and dotted_name(n.func) in ("pytest.skip", "pytest.xfail", "self.skipTest"):
            return {"skipped in body"}
        if isinstance(n, ast.Raise) and n.exc is not None and \
                (dotted_name(n.exc.func if isinstance(n.exc, ast.Call) else n.exc) or "").endswith("SkipTest"):
            return {"skipped in body"}
    return set()


def _assert_info(fn):
    count, consts = 0, Counter()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assert):
            count += 1
            consts.update(repr(c.value) for c in ast.walk(n.test) if isinstance(c, ast.Constant))
        elif isinstance(n, ast.Call):
            name = dotted_name(n.func) or ""
            last = name.split(".")[-1]
            if last.startswith("assert") or name in ASSERT_CALLS:
                count += 1
                if last.startswith("assert"):
                    consts.update(repr(c.value) for a in n.args for c in ast.walk(a) if isinstance(c, ast.Constant))
    return count, consts


def _cases(fn):
    total = None
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and (dotted_name(d.func) or "").endswith("parametrize") and len(d.args) >= 2 \
                and isinstance(d.args[1], (ast.List, ast.Tuple)):
            total = (total or 1) * len(d.args[1].elts)
    return total


def python_tests(tree) -> dict:
    import hashlib
    out = {}
    if tree is None:
        return out
    module_marks = _pytestmark(tree.body)

    def info(fn, qual, inherited):
        count, consts = _assert_info(fn)
        body = hashlib.sha1(ast.dump(ast.Module(body=fn.body, type_ignores=[])).encode()).hexdigest()
        return TInfo(qual, fn.lineno, count, consts, inherited | _marks(fn.decorator_list) | _body_skips(fn),
                     _cases(fn), body)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            out[node.name] = info(node, node.name, module_marks)
        elif isinstance(node, ast.ClassDef) and (node.name.startswith("Test") or any(
                (dotted_name(b) or "").endswith("TestCase") for b in node.bases)):
            cls_marks = module_marks | _marks(node.decorator_list) | _pytestmark(node.body)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test"):
                    out[f"{node.name}.{item.name}"] = info(item, f"{node.name}.{item.name}", cls_marks)
    return out


def _trivial(node) -> bool:
    if isinstance(node, ast.Assert):
        t = node.test
        if isinstance(t, ast.Constant):
            return bool(t.value)
        if isinstance(t, ast.BoolOp) and isinstance(t.op, ast.Or):
            return any(isinstance(v, ast.Constant) and v.value for v in t.values)
        if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], (ast.Eq, ast.Is, ast.GtE, ast.LtE)):
            return ast.dump(t.left) == ast.dump(t.comparators[0])
        if isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not) and isinstance(t.operand, ast.Constant):
            return not t.operand.value
    elif isinstance(node, ast.Call):
        last = (dotted_name(node.func) or "").split(".")[-1]
        if last == "assertTrue" and node.args and isinstance(node.args[0], ast.Constant):
            return bool(node.args[0].value)
        if last in ("assertEqual", "assertEquals", "assertIs") and len(node.args) >= 2:
            return ast.dump(node.args[0]) == ast.dump(node.args[1])
    return False


def _fmt_consts(c: Counter) -> str:
    items = list(c.elements())[:3]
    return ", ".join(x if len(x) <= 20 else x[:17] + "..." for x in items)


def _ini_values(text, sections):
    import configparser
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read_string(text or "")
    except configparser.Error:
        return {}
    return {f"{s}.{k}": v for s in cp.sections() if s in sections for k, v in cp.items(s)}


def _toml_values(text):
    try:
        tool = (tomllib.loads(text or "").get("tool") or {})
    except tomllib.TOMLDecodeError:
        return {}
    out = {f"pytest.{k}": v for k, v in ((tool.get("pytest") or {}).get("ini_options") or {}).items()}
    for section, table in (tool.get("coverage") or {}).items():
        if isinstance(table, dict):
            out.update({f"coverage.{section}.{k}": v for k, v in table.items()})
    return out


def check_test_config(ctx, fc) -> list:
    name = fc.name.lower()
    if name == "pyproject.toml":
        old, new = _toml_values(ctx.old_text(fc)), _toml_values(ctx.new_text(fc))
    elif name in ("pytest.ini", "tox.ini", "setup.cfg", ".coveragerc"):
        sections = {"pytest", "tool:pytest", "coverage:run", "coverage:report", "run", "report"}
        old, new = _ini_values(ctx.old_text(fc), sections), _ini_values(ctx.new_text(fc), sections)
    else:
        return []
    keys = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k)
                  and k.split(".")[-1] in PYTEST_NARROWING | COVERAGE_NARROWING)
    if not keys:
        return []
    return [make("tests.config_changed", fc.path, find_line(ctx.new_text(fc), keys[0].split(".")[-1], fc.added),
                 f"changed {', '.join(keys[:4])}", key=",".join(keys))]


JS_FOCUS = re.compile(r"\b(?:it|test|describe|context|suite)\.only\s*\(|\b(?:fit|fdescribe|ftest)\s*\(")
JS_SKIP = re.compile(r"\b(?:it|test|describe|context|suite)\.(?:skip|todo)\s*\(|\b(?:xit|xdescribe|xtest|xcontext)\s*\(")
JS_TRIVIAL = re.compile(r"expect\(\s*(?:true|1)\s*\)\s*\.(?:toBe\(\s*(?:true|1)\s*\)|toBeTruthy\(\s*\))|"
                        r"\bassert(?:\.ok)?\(\s*(?:true|1)\s*\)|expect\((\w+)\)\.to(?:Be|Equal)\(\1\)")
JS_ASSERT = re.compile(r"\b(?:expect|assert)\s*[.(]")
JS_TEST = re.compile(r"\b(?:(?:it|test)(?:\.(?:only|skip|todo|concurrent|failing))?|xit|fit|xtest)\s*\(\s*['\"`]")
OTHER_SKIP = re.compile(r"\bt\.Skip(?:Now|f)?\(|@Disabled\b|@Ignore\b")
JS_TEST_CONFIG = re.compile(r"testPathIgnorePatterns|coveragePathIgnorePatterns|modulePathIgnorePatterns|"
                            r"passWithNoTests|coverageThreshold|testMatch|testRegex|coverage\.exclude")


def check_tests(ctx) -> list:
    out = []
    src_changed = any(not fc.deleted and fc.ext in CODE_EXTS and not ctx.is_test(fc.path) for fc in ctx.changes)
    tests = [fc for fc in ctx.changes if not fc.binary and not fc.symlink and not ctx.ignored(fc.path)
             and ctx.is_test(fc.path)]

    parsed, new_bodies, new_names = [], set(), set()
    for fc in tests:
        if fc.ext not in PY_EXTS:
            continue
        new = {} if fc.deleted else python_tests(ctx.py_tree(fc)[0])
        old = python_tests(ctx.py_tree(fc, "old")[0]) if fc.old_spec else {}
        parsed.append((fc, old, new))
        new_bodies |= {t.body for t in new.values()}
        new_names |= {t.name.split(".")[-1] for t in new.values()}
    for fc, old, new in parsed:
        gone = [q for q, o in old.items() if q not in new and o.body not in new_bodies
                and o.name.split(".")[-1] not in new_names]
        if gone:
            out.append(make("tests.deleted", fc.path, None,
                            f"{len(gone)} test(s) removed: {', '.join(gone[:4])}{'…' if len(gone) > 4 else ''}",
                            key=",".join(sorted(gone))))
        for q, n in new.items():
            o = old.get(q)
            if o is None:
                if n.marks:
                    out.append(make("tests.skip_added", fc.path, n.line, f"new test {q} is marked {', '.join(sorted(n.marks))}", key=q))
                elif n.asserts == 0:
                    out.append(make("tests.no_assertions", fc.path, n.line, f"new test {q} has no assertions", key=q))
                continue
            if n.asserts < o.asserts:
                out.append(make("tests.assertions_removed", fc.path, n.line, f"{q}: assertions {o.asserts} → {n.asserts}", key=q))
            elif o.cases and n.cases is not None and n.cases < o.cases:
                out.append(make("tests.assertions_removed", fc.path, n.line,
                                f"{q}: parametrized cases {o.cases} → {n.cases}", key=q + ":cases"))
            if n.marks - o.marks:
                out.append(make("tests.skip_added", fc.path, n.line, f"{q}: now {', '.join(sorted(n.marks - o.marks))}", key=q))
            if src_changed and (o.consts - n.consts) and (n.consts - o.consts):
                out.append(make("tests.expected_changed", fc.path, n.line,
                                f"{q}: expected values changed ({_fmt_consts(o.consts - n.consts)} → "
                                f"{_fmt_consts(n.consts - o.consts)}) in the same commit as source changes", key=q))
        tree = None if fc.deleted else ctx.py_tree(fc)[0]
        for node in ast.walk(tree) if tree else []:
            if isinstance(node, (ast.Assert, ast.Call)) and getattr(node, "lineno", 0) in fc.added and _trivial(node):
                out.append(make("tests.trivial_assert", fc.path, node.lineno,
                                f"`{ast.unparse(node)[:60]}` can never fail", key=ast.dump(node)))

    for fc in tests:
        if fc.ext not in PY_EXTS and not fc.deleted:
            removed = "\n".join(t for _, t in fc.removed)
            added = "\n".join(fc.added.values())
            for line, text in fc.added.items():
                if JS_FOCUS.search(text):
                    out.append(make("tests.focused", fc.path, line, text.strip()[:80], key=text.strip()))
                elif JS_SKIP.search(text) or OTHER_SKIP.search(text):
                    out.append(make("tests.skip_added", fc.path, line, text.strip()[:80], key=text.strip()))
                if JS_TRIVIAL.search(text):
                    out.append(make("tests.trivial_assert", fc.path, line, text.strip()[:80], key=text.strip()))
            lost_asserts = len(JS_ASSERT.findall(removed)) - len(JS_ASSERT.findall(added))
            lost_tests = len(JS_TEST.findall(removed)) - len(JS_TEST.findall(added))
            if lost_tests > 0:
                out.append(make("tests.deleted", fc.path, None, f"net {lost_tests} test case(s) removed", key="js-tests"))
            if lost_asserts > 0:
                out.append(make("tests.assertions_removed", fc.path, None, f"net {lost_asserts} assertion(s) removed",
                                key="js-asserts"))
        elif fc.deleted and fc.ext not in PY_EXTS:
            text = ctx.old_text(fc) or ""
            if JS_TEST.search(text) or "func Test" in text or "@Test" in text:
                out.append(make("tests.deleted", fc.path, None, "test file deleted", key="file"))

    for fc in ctx.changes:
        if fc.deleted or fc.binary or ctx.ignored(fc.path):
            continue
        if fc.name in ("sitecustomize.py", "usercustomize.py") or fc.ext == ".pth":
            if fc.status in "ARC":
                out.append(make("tests.harness_tamper", fc.path, None,
                                f"{fc.name} runs automatically at every Python start-up", key=fc.path))
            continue
        if fc.name in ("pyproject.toml", "setup.cfg", "setup.py"):
            for line, text in fc.added.items():
                if "pytest11" in text:
                    out.append(make("tests.harness_tamper", fc.path, line, "new pytest11 entry point (auto-loaded plugin)",
                                    key="pytest11"))
        out += check_test_config(ctx, fc)
        if re.match(r"(?:jest|vitest|karma)\.config\.", fc.name) or fc.name == "package.json":
            for line, text in fc.added.items():
                if JS_TEST_CONFIG.search(text) or (fc.name == "package.json" and "--passWithNoTests" in text):
                    out.append(make("tests.config_changed", fc.path, line, text.strip()[:80], key=text.strip()))
        if fc.ext in PY_EXTS:
            tree = ctx.py_tree(fc)[0]
            if tree is None:
                continue
            old_tree = ctx.py_tree(fc, "old")[0] if fc.old_spec else None
            old_defs = {n.name for n in (old_tree.body if old_tree else []) if isinstance(n, ast.FunctionDef)}
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name in HARNESS_HOOKS and node.name not in old_defs:
                    out.append(make("tests.harness_tamper", fc.path, node.lineno,
                                    f"new pytest hook {node.name}() can change test outcomes", action="fix", key=node.name))
                if isinstance(node, (ast.Assign, ast.AugAssign)) and node.lineno in fc.added:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    names = {t.id for t in targets if isinstance(t, ast.Name)}
                    if "pytest_plugins" in names:
                        out.append(make("tests.harness_tamper", fc.path, node.lineno, "pytest_plugins changed",
                                        action="fix", key="pytest_plugins"))
                    if names & {"collect_ignore", "collect_ignore_glob"}:
                        out.append(make("tests.config_changed", fc.path, node.lineno,
                                        f"{', '.join(sorted(names))} excludes files from test collection",
                                        key="collect_ignore"))
            if not ctx.is_test(fc.path):
                for node in ast.walk(tree):
                    line = getattr(node, "lineno", 0)
                    if line not in fc.added:
                        continue
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                        bad = [m for m in mods if m.split(".")[0] in ("_pytest", "coverage") or m in ("unittest.case", "unittest.result")]
                        if bad:
                            out.append(make("tests.internals_touched", fc.path, line, f"imports {', '.join(bad)}",
                                            key=",".join(bad)))
                    elif isinstance(node, ast.Call) and dotted_name(node.func) in (
                            "os._exit", "sys.settrace", "sys.setprofile") or (
                            isinstance(node, ast.Call) and (dotted_name(node.func) or "").startswith("sys.monitoring.")):
                        out.append(make("tests.internals_touched", fc.path, line,
                                        f"{dotted_name(node.func)}() can hide failures or skew coverage",
                                        action="warn", key=dotted_name(node.func)))
    return out


# --------------------------------------------------------------------------- checks: suppressions

SUPPRESSIONS = [
    ("suppress.security", re.compile(r"#\s*nosec\b|(?:#|//)\s*nosemgrep\b|gitleaks:(?:allow)|(?:#|//)\s*NOSONAR\b|"
                                     r"pragma:\s*allowlist\s+secret|#\s*noqa:\s*S\d")),
    ("suppress.blanket", re.compile(r"#\s*(?:flake8|ruff):\s*noqa\s*$|#\s*mypy:\s*ignore-errors|#\s*pylint:\s*skip-file|"
                                    r"//\s*@ts-nocheck|/\*\s*eslint-disable\s*\*/|^\s*//\s*eslint-disable\s*$|"
                                    r"#\s*pyright:\s*basic\b")),
    ("suppress.type", re.compile(r"#\s*type:\s*ignore\b|#\s*pyright:\s*ignore\b|//\s*@ts-ignore\b")),
    ("suppress.coverage", re.compile(r"#\s*pragma:\s*no\s*(?:cover|branch)\b|(?:/\*|//)\s*(?:istanbul|c8|v8)\s+ignore\b")),
    ("suppress.lint", re.compile(r"#\s*noqa\b|#\s*pylint:\s*disable\b|//\s*eslint-disable-(?:next-)?line\b|"
                                 r"/\*\s*eslint-disable\b|//\s*@ts-expect-error\b|//\s*biome-ignore\b|//\s*nolint\b|"
                                 r"@SuppressWarnings\b|#\s*rubocop:\s*disable\b")),
]
TYPE_CAST = re.compile(r"\bas\s+any\b|:\s*any\b(?=\s*[,)=;\]>|])|<any>")


def check_suppressions(ctx) -> list:
    out = []
    for fc in ctx.code_changes(CODE_EXTS):
        out += ctx.results.run("suppressions", ctx, fc, lambda fc=fc: _suppressions_file(ctx, fc))
    return out


def _suppressions_file(ctx, fc) -> list:
    out = []
    before = Counter()
    for _, text in fc.removed:
        for rule, rx in SUPPRESSIONS:
            if rx.search(text):
                before[rule] += 1
                break
    for line, text in fc.added.items():
        for rule, rx in SUPPRESSIONS:
            m = rx.search(text)
            if m:
                if before[rule]:
                    before[rule] -= 1            # the same suppression existed on an edited line
                else:
                    marker = m.group(0).strip()                  # key: the whole line, so each one is distinct
                    out.append(make(rule, fc.path, line, f"`{marker}` added", key=f"{marker}:{' '.join(text.split())}",
                                    legacy_key=marker))
                break
        if fc.ext in TS_EXTS and TYPE_CAST.search(text) and not text.lstrip().startswith("//"):
            out.append(make("suppress.type_cast", fc.path, line, f"`{TYPE_CAST.search(text).group(0).strip()}` added",
                            key=text.strip()))
    return out


# --------------------------------------------------------------------------- checks: code (line level, all languages)

COMMENT_START = re.compile(r"#|//|/\*|<!--|^\s*\*|--\s")
PLACEHOLDER_RE = re.compile(
    r"(?i)\.\.\.\s*(?:the\s+)?(?:rest|remainder)\s+of\b|"
    r"\b(?:rest|remainder)\s+of\s+(?:the\s+)?(?:code|implementation|function|method|file|class|logic|component)\b|"
    r"\.\.\.\s*existing\s+(?:code|logic|implementation|imports|methods)\b|"
    r"\bexisting\s+(?:code|implementation)\s+(?:here|remains|unchanged|stays)\b|"
    r"\b(?:add|insert|put|write|implement)\s+(?:your|the|actual|real)\s+(?:own\s+)?(?:code|logic|implementation)\s+here\b|"
    r"\byour\s+(?:code|logic|implementation)\s+(?:goes\s+)?here\b|\bTODO:?\s*implement\b|\bimplement\s+(?:this|me)\b|"
    r"\bplaceholder\s+(?:for|implementation|logic|code)\b|"
    r"\bin\s+a\s+real\s+(?:implementation|application|app|system|project|scenario|world)\b|"
    r"\bthis\s+is\s+(?:a|just\s+a)\s+(?:simplified|simple|basic|naive|placeholder|mock|dummy|stub)\s+"
    r"(?:version|implementation|example)\b|\b(?:simplified|mock|dummy|stub)\s+implementation\b|"
    r"\breplace\s+(?:this\s+)?with\s+(?:actual|real|your)\b")
JS_PLACEHOLDER = re.compile(r"""throw\s+new\s+Error\(\s*['"`](?:not\s+(?:yet\s+)?implemented|todo|implement)""", re.I)
TODO_RE = re.compile(r"(?:#|//|/\*|\*)\s*(?:TODO|FIXME|XXX|HACK)\b")
SECRET_WORDS = r"(?:password|passwd|pwd|secret|token|otp|nonce|salt|session|api_?key)"
WEAK_CRYPTO = re.compile(
    r"(?i)(?=.*\b(?:md5|sha1)\b)(?=.*\b(?:password|passwd|pwd|secret|token)\b)|"
    r"(?=.*(?:\brandom\.(?:random|randint|choice|choices|randrange|getrandbits)\s*\(|Math\.random\(\)))"
    r"(?=.*\b" + SECRET_WORDS + r"\b)")
NON_PY_RULES = [
    ("security.verification_disabled", re.compile(
        r"rejectUnauthorized\s*:\s*false|NODE_TLS_REJECT_UNAUTHORIZED['\"]?\]?\s*=\s*['\"]?0|strictSSL\s*:\s*false|"
        r"algorithms\s*:\s*\[\s*['\"]none['\"]|(?i:insecureSkipVerify)\s*:\s*true|CURLOPT_SSL_VERIFYPEER\s*,\s*(?:false|0)"),
     "certificate or signature verification turned off"),
]
JS_RULES = [
    ("code.debug_leftover", re.compile(r"^\s*debugger\s*;?\s*(?://.*)?$"), "`debugger` statement"),
    ("security.eval", re.compile(r"(?<![\w$.])eval\s*\(|\bnew\s+Function\s*\(|\bset(?:Timeout|Interval)\s*\(\s*['\"`]"),
     "code built from a string at runtime"),
    ("security.shell", re.compile(r"\bexec(?:Sync)?\s*\(\s*(?:`[^`]*\$\{|['\"][^'\"]*['\"]\s*\+)|\bshell\s*:\s*true\b"),
     "shell command built from variables"),
    ("security.sql_injection", re.compile(
        r"(?i)\.(?:query|execute|raw|\$queryRawUnsafe|\$executeRawUnsafe)\s*\(\s*(?:`[^`]*\b(?:select|insert|update|delete)\b"
        r"[^`]*\$\{|['\"][^'\"]*\b(?:select|insert|update|delete)\b[^'\"]*['\"]\s*\+)"), "SQL built from variables"),
    ("security.insecure_default", re.compile(
        r"\bcors\(\s*\)|\borigin\s*:\s*['\"]\*['\"]|Access-Control-Allow-Origin['\"]?\s*[,:]\s*['\"]\*['\"]"),
     "CORS allows every origin"),
    ("security.xss", re.compile(r"dangerouslySetInnerHTML|\.innerHTML\s*=(?!=)|\bdocument\.write\s*\(|\bv-html\s*="),
     "raw HTML injected into the page"),
]
JS_CONSOLE = re.compile(r"\bconsole\.(?:log|debug|trace|dir)\s*\(")
JS_SWALLOW = re.compile(r"\bcatch\s*(?:\(\s*[\w$]*\s*\))?\s*\{\s*\}|"
                        r"\.catch\(\s*(?:\(\s*[\w$]*\s*\)|[\w$]+)?\s*=>\s*(?:\{\s*\}|null|undefined)\s*\)")


def _blocks(lines) -> list:
    blocks, cur, last = [], [], None
    for ln in sorted(lines):
        if last is not None and ln != last + 1:
            blocks.append(cur)
            cur = []
        cur.append(ln)
        last = ln
    return blocks + ([cur] if cur else [])


def check_code_lines(ctx) -> list:
    out = []
    for fc in ctx.code_changes(CODE_EXTS):
        out += ctx.results.run("code_lines", ctx, fc, lambda fc=fc: _code_lines_file(ctx, fc))
    return out


def _code_lines_file(ctx, fc) -> list:
    out = []
    is_js, is_py = fc.ext in JS_EXTS, fc.ext in PY_EXTS
    test = ctx.is_test(fc.path)
    for line, text in fc.added.items():
        if len(text) > 2000:
            continue
        m, c = PLACEHOLDER_RE.search(text), COMMENT_START.search(text)
        if m and c and c.start() <= m.start():
            out.append(make("code.placeholder", fc.path, line, text.strip()[:90], key=text.strip()))
        elif is_js and JS_PLACEHOLDER.search(text):
            out.append(make("code.placeholder", fc.path, line, "throws 'not implemented'", key=text.strip()))
        elif TODO_RE.search(text):
            out.append(make("code.todo", fc.path, line, text.strip()[:80], key=text.strip()))
        if WEAK_CRYPTO.search(text):
            out.append(make("security.weak_crypto", fc.path, line, text.strip()[:80], key=text.strip()))
        if not is_py:
            for rule, rx, what in NON_PY_RULES + (JS_RULES if is_js else []):
                if rx.search(text):
                    out.append(make(rule, fc.path, line, what, key=text.strip()))
        if is_js and not test and JS_CONSOLE.search(text) and not re.search(r"(?:^|/)(?:scripts|bin|cli)/|\.config\.", fc.path):
            out.append(make("code.console_log", fc.path, line, text.strip()[:80], key=text.strip()))
    if is_js:
        for block in _blocks(fc.added):
            joined = "\n".join(fc.added[ln] for ln in block)
            for m in JS_SWALLOW.finditer(joined):
                out.append(make("code.swallowed_exception", fc.path, block[0] + joined[:m.start()].count("\n"),
                                "empty catch", key=m.group(0)))
    return out


# --------------------------------------------------------------------------- checks: Python (AST)

SHELL_CALLS = {"os.system", "os.popen", "subprocess.getoutput", "subprocess.getstatusoutput", "commands.getoutput"}
DESER_CALLS = {"pickle.load", "pickle.loads", "cPickle.load", "cPickle.loads", "dill.load", "dill.loads",
               "marshal.load", "marshal.loads", "jsonpickle.decode", "pd.read_pickle", "pandas.read_pickle",
               "yaml.unsafe_load", "shelve.open"}
DEBUG_CALLS = {"breakpoint", "pdb.set_trace", "ipdb.set_trace", "pudb.set_trace", "IPython.embed", "pdb.post_mortem"}
SQL_KW = re.compile(r"(?i)\b(?:select|insert|update|delete|drop|create|alter)\b")
SQL_METHODS = {"execute", "executemany", "executescript", "raw", "read_sql", "read_sql_query", "text"}
NET_RECEIVERS = {"requests", "httpx", "session", "client", "aiohttp", "http"}
NET_METHODS = {"get", "post", "put", "patch", "delete", "request", "head"}
DB_RECEIVERS = {"cursor", "cur", "conn", "connection", "db", "session", "engine"}
DB_METHODS = {"execute", "executemany", "query", "fetchone", "fetchall", "scalar", "scalars"}
MEMO = {"cache", "lru_cache", "cached", "memoize", "memoized", "cached_property"}


def _is_const(node, value) -> bool:
    return isinstance(node, ast.Constant) and node.value is value


def _dynamic_sql(node) -> bool:
    if isinstance(node, ast.JoinedStr):
        text = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
        return any(isinstance(v, ast.FormattedValue) for v in node.values) and bool(SQL_KW.search(text))
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        strs = [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        return bool(SQL_KW.search(" ".join(strs))) and not all(isinstance(n, ast.Constant) for n in (node.left, node.right))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format" \
            and isinstance(node.func.value, ast.Constant) and isinstance(node.func.value.value, str):
        return bool(SQL_KW.search(node.func.value.value))
    return False


def _local_nodes(fn):
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(n))


def iter_functions(tree) -> list:
    out = []

    def visit(body, prefix, cls):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((n, cls, prefix + n.name))
                visit(n.body, prefix + n.name + ".", None)
            elif isinstance(n, ast.ClassDef):
                visit(n.body, prefix + n.name + ".", n)
            elif isinstance(n, (ast.If, ast.Try, ast.With, ast.AsyncWith)) or type(n).__name__ == "TryStar":
                visit(n.body, prefix, cls)
                visit(getattr(n, "orelse", []) or [], prefix, cls)
                for h in getattr(n, "handlers", []) or []:
                    visit(h.body, prefix, cls)
                visit(getattr(n, "finalbody", []) or [], prefix, cls)

    visit(tree.body, "", None)
    return out


def complexity(fn) -> int:
    c = 1
    for n in _local_nodes(fn):
        if isinstance(n, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.match_case)):
            c += 1
        elif isinstance(n, ast.comprehension):
            c += 1 + len(n.ifs)
        elif isinstance(n, ast.BoolOp):
            c += len(n.values) - 1
    return c


class LoopScan:
    """Walks one function and records loops, plus calls/comparisons made inside loops."""

    def __init__(self, fn):
        self.loops, self.calls, self.compares = [], [], []
        try:
            for stmt in fn.body:
                self.visit(stmt, ())
        except RecursionError:
            pass

    def visit(self, node, chain):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            self.visit(node.iter, chain)
            inner = chain + ((node.lineno, node.iter),)
            self.loops.append(inner)
            for s in node.body:
                self.visit(s, inner)
            for s in node.orelse:
                self.visit(s, chain)
            return
        if isinstance(node, ast.While):
            inner = chain + ((node.lineno, None),)
            self.loops.append(inner)
            self.visit(node.test, inner)
            for s in node.body:
                self.visit(s, inner)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            c = chain
            for gen in node.generators:
                self.visit(gen.iter, c)
                c = c + ((gen.iter.lineno, gen.iter),)
                self.loops.append(c)
                for cond in gen.ifs:
                    self.visit(cond, c)
            for e in ([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]):
                self.visit(e, c)
            return
        if chain and isinstance(node, ast.Call):
            self.calls.append((node, chain))
        if chain and isinstance(node, ast.Compare):
            self.compares.append((node, chain))
        for child in ast.iter_child_nodes(node):
            self.visit(child, chain)


def _collection(it):
    if it is None:
        return None
    if isinstance(it, ast.Call):
        fn = dotted_name(it.func)
        if fn == "range":
            for a in it.args:
                for sub in ast.walk(a):
                    if isinstance(sub, ast.Call) and dotted_name(sub.func) == "len" and sub.args:
                        return ast.dump(sub.args[0])
            return None
        if fn in ("enumerate", "sorted", "reversed", "list", "tuple", "iter") and it.args:
            return _collection(it.args[0]) or ast.dump(it.args[0])
        if isinstance(it.func, ast.Attribute) and it.func.attr in ("items", "keys", "values"):
            return ast.dump(it.func.value)
        return None
    return ast.dump(it) if isinstance(it, (ast.Name, ast.Attribute, ast.Subscript)) else None


def _listish(fn) -> set:
    names = set()
    for n in _local_nodes(fn):
        if isinstance(n, ast.Assign) and (isinstance(n.value, (ast.List, ast.ListComp)) or (
                isinstance(n.value, ast.Call) and dotted_name(n.value.func) in ("list", "sorted"))):
            names |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) \
                and re.match(r"(?:list|List)\b", ast.unparse(n.annotation)):
            names.add(n.target.id)
    for a in fn.args.args + fn.args.kwonlyargs:
        if a.annotation is not None and re.match(r"(?:list|List)\b", ast.unparse(a.annotation)):
            names.add(a.arg)
    return names


def _fn_metrics(fn):
    scan = LoopScan(fn)
    depth = max((len(c) for c in scan.loops), default=0)
    return (fn.end_lineno or fn.lineno) - fn.lineno + 1, complexity(fn), depth, scan


def _body_kind(fn):
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    if not body:
        return "empty"
    if len(body) != 1:
        return None
    s = body[0]
    if isinstance(s, ast.Pass):
        return "pass"
    if isinstance(s, ast.Expr) and _is_const(s.value, Ellipsis):
        return "ellipsis"
    if isinstance(s, ast.Raise) and s.exc is not None and \
            (dotted_name(s.exc.func if isinstance(s.exc, ast.Call) else s.exc) or "").endswith("NotImplementedError"):
        return "notimpl"
    return None


def _abstractish(fn, cls) -> bool:
    decos = {(dotted_name(d.func if isinstance(d, ast.Call) else d) or "").split(".")[-1] for d in fn.decorator_list}
    if decos & {"abstractmethod", "overload", "abstractproperty"}:
        return True
    if cls is not None:
        bases = {(dotted_name(b) or "").split(".")[-1] for b in cls.bases}
        if bases & {"ABC", "Protocol"} or any("ABCMeta" in (dotted_name(k.value) or "") for k in cls.keywords):
            return True
    return False


def check_python(ctx) -> list:
    out = []
    for fc in ctx.code_changes(PY_EXTS):
        out += ctx.results.run("python", ctx, fc, lambda fc=fc: _python_file(ctx, fc))
    return out


def _python_file(ctx, fc) -> list:
    out = []
    lim = ctx.cfg["limits"]
    tree, err = ctx.py_tree(fc)
    if tree is None:
        if err is not None and (fc.old_spec is None or ctx.py_tree(fc, "old")[0] is not None):
            out.append(make("code.syntax_error", fc.path, err.lineno,
                            f"{err.msg} (parsed with Python {sys.version_info[0]}.{sys.version_info[1]})", key=err.msg))
        return out
    added = fc.added
    test = ctx.is_test(fc.path)
    old_tree = ctx.py_tree(fc, "old")[0] if fc.old_spec else None
    old_metrics = {q: _fn_metrics(f)[:3] for f, _c, q in iter_functions(old_tree)} if old_tree else {}

    # functions: stubs, size, complexity, loops, recursion
    for fn, cls, qual in iter_functions(tree):
        end = fn.end_lineno or fn.lineno
        if not any(ln in added for ln in range(fn.lineno, end + 1)):
            continue
        new_fn = fn.lineno in added
        kind = _body_kind(fn)
        if new_fn and kind and not _abstractish(fn, cls) and not fc.path.endswith(".pyi"):
            if kind == "ellipsis" or (kind == "notimpl" and cls is None):
                out.append(make("code.placeholder", fc.path, fn.lineno, f"{qual}() has no implementation "
                                f"({'`...`' if kind == 'ellipsis' else 'raise NotImplementedError'})", key=qual))
            elif not test:
                out.append(make("code.stub", fc.path, fn.lineno, f"{qual}() has an empty body", key=qual))
        if new_fn:
            defaults = list(fn.args.defaults) + [d for d in fn.args.kw_defaults if d is not None]
            if any(isinstance(d, (ast.List, ast.Dict, ast.Set)) or (
                    isinstance(d, ast.Call) and dotted_name(d.func) in ("list", "dict", "set")) for d in defaults):
                out.append(make("code.mutable_default", fc.path, fn.lineno, f"{qual}() has a mutable default argument",
                                key=qual))
        length, cx, depth, scan = _fn_metrics(fn)
        old = old_metrics.get(qual)
        if length > lim["function_lines"] and (old is None or length > old[0]):
            out.append(make("size.function_length", fc.path, fn.lineno,
                            f"{qual}() is {length} lines (limit {lim['function_lines']})", key=qual))
        if cx > lim["complexity"] and (old is None or cx > old[1]):
            out.append(make("size.complexity", fc.path, fn.lineno,
                            f"{qual}() has complexity {cx} (limit {lim['complexity']})", key=qual))
        if depth >= lim["loop_depth"] and (old is None or depth > old[2]):
            deepest = max(scan.loops, key=len)
            out.append(make("perf.nested_loops", fc.path, deepest[-1][0],
                            f"{qual}() nests {depth} loops (≈O(n^{depth}))", key=qual))
        out += _loop_patterns(fc, qual, fn, scan)
        if not ({(dotted_name(d.func if isinstance(d, ast.Call) else d) or "").split(".")[-1]
                 for d in fn.decorator_list} & MEMO):
            for stmt in _local_nodes(fn):
                if isinstance(stmt, (ast.Return, ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr)):
                    calls = [n for n in ast.walk(stmt) if isinstance(n, ast.Call)
                             and dotted_name(n.func) in (fn.name, f"self.{fn.name}", f"cls.{fn.name}")]
                    if len(calls) >= 2 and stmt.lineno in added:
                        out.append(make("perf.exponential_recursion", fc.path, stmt.lineno,
                                        f"{qual}() calls itself {len(calls)} times per call without memoisation",
                                        key=qual))
                        break
    out += _python_nodes(ctx, fc, tree, test)
    out += _unused_imports(fc, tree)
    return out


def _loop_patterns(fc, qual, fn, scan) -> list:
    out, seen = [], set()

    def add(line, msg, rule="perf.quadratic_pattern"):
        if line in fc.added and (rule, msg) not in seen:
            seen.add((rule, msg))
            out.append(make(rule, fc.path, line, f"{qual}(): {msg}", key=f"{qual}:{msg}"))

    for chain in scan.loops:
        if len(chain) >= 2:
            inner = _collection(chain[-1][1])
            if inner and any(_collection(outer[1]) == inner for outer in chain[:-1]):
                add(chain[-1][0], "nested loops over the same collection (O(n²))")
    listish = _listish(fn)
    for call, _chain in scan.calls:
        name = dotted_name(call.func) or ""
        last = name.split(".")[-1]
        recv = name.split(".")[0].lower() if "." in name else ""
        if name == "sorted" or last == "sort":
            add(call.lineno, "sorts inside a loop")
        elif last in ("pop", "insert") and call.args and isinstance(call.args[0], ast.Constant) \
                and call.args[0].value == 0 and not isinstance(call.args[0].value, bool):
            add(call.lineno, f"list.{last}(0) inside a loop shifts the whole list; use collections.deque")
        elif last in ("index", "count", "remove") and recv and name.split(".")[0] in listish:
            add(call.lineno, f"list.{last}() scans the list on every iteration")
        elif (recv in NET_RECEIVERS and last in NET_METHODS) or last == "urlopen" \
                or (recv in DB_RECEIVERS and last in DB_METHODS) or (".objects." in name and last in (
                    "get", "filter", "create", "exclude", "count", "all")):
            add(call.lineno, f"{name}() runs once per iteration", "perf.call_in_loop")
    for cmp_, _chain in scan.compares:
        if any(isinstance(op, (ast.In, ast.NotIn)) for op in cmp_.ops) and any(
                isinstance(c, ast.Name) and c.id in listish for c in cmp_.comparators):
            add(cmp_.lineno, "membership test on a list inside a loop is O(n) each time; use a set")
    return out


def _python_nodes(ctx, fc, tree, test) -> list:
    out, added = [], fc.added
    imported_embed = False
    owners: dict = {}

    def qual(node) -> str:
        """Qualified name of the function around `node` (built on first use: most files have no findings)."""
        if not owners:
            owners[None] = "<module>"
            for fn, _cls, name in iter_functions(tree):          # outer first, so inner functions win
                for n in ast.walk(fn):
                    owners[id(n)] = name
        return owners.get(id(node), "<module>")
    for node in ast.walk(tree):
        line = getattr(node, "lineno", None)
        if line not in added:
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if any(m.split(".")[0] in ("pdb", "ipdb", "pudb") for m in mods):
                out.append(make("code.debug_leftover", fc.path, line, f"imports {mods[0]}", key=f"import:{mods[0]}"))
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("IPython") and \
                    any(a.name == "embed" for a in node.names):
                imported_embed = True
        elif isinstance(node, ast.Call):
            out += _python_call(fc, node, line, imported_embed, qual)
        elif isinstance(node, ast.ExceptHandler):
            types = [] if node.type is None else (node.type.elts if isinstance(node.type, ast.Tuple) else [node.type])
            broad = node.type is None or any((dotted_name(t) or "").split(".")[-1] in ("Exception", "BaseException")
                                             for t in types)
            empty = all(isinstance(s, (ast.Pass, ast.Continue)) or (isinstance(s, ast.Expr) and _is_const(s.value, Ellipsis))
                        for s in node.body)
            if broad and empty:
                out.append(make("code.swallowed_exception", fc.path, line,
                                f"`except{'' if node.type is None else ' ' + ast.unparse(node.type)}:` with an empty body",
                                key=f"except:{qual(node)}:{'' if node.type is None else ast.unparse(node.type)}",
                                legacy_key=f"except:{line}"))
            elif node.type is None:
                out.append(make("code.bare_except", fc.path, line, "bare `except:`", key=f"bare:{qual(node)}",
                                legacy_key=f"bare:{line}"))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                ce = item.context_expr
                if isinstance(ce, ast.Call) and (dotted_name(ce.func) or "").split(".")[-1] == "suppress" and any(
                        (dotted_name(a) or "").split(".")[-1] in ("Exception", "BaseException") for a in ce.args):
                    out.append(make("code.swallowed_exception", fc.path, line, "`suppress(Exception)` hides every error",
                                    key=f"suppress:{qual(node)}:{ast.unparse(ce)}", legacy_key=f"suppress:{line}"))
        elif isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            attrs = {t.attr for t in node.targets if isinstance(t, ast.Attribute)}
            if "check_hostname" in attrs and _is_const(node.value, False):
                out.append(make("security.verification_disabled", fc.path, line, "check_hostname = False", key="hostname"))
            if not test and "DEBUG" in names and _is_const(node.value, True):
                out.append(make("security.insecure_default", fc.path, line, "DEBUG = True", key="DEBUG"))
            if names & {"CORS_ORIGIN_ALLOW_ALL", "CORS_ALLOW_ALL_ORIGINS"} and _is_const(node.value, True):
                out.append(make("security.insecure_default", fc.path, line, "CORS allows every origin", key="cors"))
            if "ALLOWED_HOSTS" in names and "'*'" in ast.unparse(node.value):
                out.append(make("security.insecure_default", fc.path, line, "ALLOWED_HOSTS accepts any host", key="hosts"))
        elif isinstance(node, ast.Attribute) and node.attr == "CERT_NONE":
            out.append(make("security.verification_disabled", fc.path, line, "ssl.CERT_NONE", key="CERT_NONE"))
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "verify_signature" and _is_const(v, False):
                    out.append(make("security.verification_disabled", fc.path, line, "JWT signature verification disabled",
                                    key="jwt"))
    return out


def _python_call(fc, node, line, imported_embed, qual=lambda n: "<module>") -> list:
    out = []
    name = dotted_name(node.func) or ""
    last = name.split(".")[-1]
    kw = {k.arg: k.value for k in node.keywords if k.arg}

    def ids(tag):
        """Fingerprint key from the enclosing function and the call's source; the 0.1 key used the line."""
        return {"key": f"{tag}:{qual(node)}:{ast.unparse(node)}", "legacy_key": f"{tag}:{line}"}
    if name in ("eval", "exec"):
        out.append(make("security.eval", fc.path, line, f"{name}() runs code built at runtime", **ids(name)))
    if _is_const(kw.get("shell"), True) or name in SHELL_CALLS:
        out.append(make("security.shell", fc.path, line, f"{name}(...) runs through a shell",
                        **ids("shell")))
    if name in DESER_CALLS:
        out.append(make("security.deserialization", fc.path, line, f"{name}() can run code from the data", **ids("deser")))
    elif name in ("yaml.load", "yaml.load_all"):
        loader = kw.get("Loader", node.args[1] if len(node.args) > 1 else None)
        if (dotted_name(loader) or "").split(".")[-1] not in ("SafeLoader", "CSafeLoader", "BaseLoader"):
            out.append(make("security.deserialization", fc.path, line, f"{name}() without SafeLoader", **ids("yaml")))
    elif name == "torch.load" and _is_const(kw.get("weights_only"), False):
        out.append(make("security.deserialization", fc.path, line, "torch.load(weights_only=False) unpickles arbitrary code",
                        **ids("torch")))
    if last in SQL_METHODS and node.args and _dynamic_sql(node.args[0]):
        out.append(make("security.sql_injection", fc.path, line, f"{last}() with SQL built from variables", **ids("sql")))
    if _is_const(kw.get("verify"), False) or name == "ssl._create_unverified_context":
        out.append(make("security.verification_disabled", fc.path, line, f"{name or 'call'}(... verify=False)"
                        if name != "ssl._create_unverified_context" else "unverified SSL context", **ids("verify")))
    if last == "run" and _is_const(kw.get("debug"), True):
        out.append(make("security.insecure_default", fc.path, line, f"{name}(debug=True)", **ids("debug")))
    if "allow_origins" in kw and "'*'" in ast.unparse(kw["allow_origins"]):
        out.append(make("security.insecure_default", fc.path, line, "allow_origins=['*']", **ids("cors")))
    if last in ("mark_safe", "Markup"):
        out.append(make("security.xss", fc.path, line, f"{last}() marks content as safe HTML", **ids("xss")))
    if name in DEBUG_CALLS or (name == "embed" and imported_embed):
        out.append(make("code.debug_leftover", fc.path, line, f"{name}()", **ids("debug")))
    return out


def _unused_imports(fc, tree) -> list:
    if fc.name == "__init__.py":
        return []
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    strings = " ".join(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))
    out = []
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)) or node.lineno not in fc.added:
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        for a in node.names:
            if a.name == "*" or (a.asname and a.asname == a.name.split(".")[-1]):
                continue
            bound = a.asname or a.name.split(".")[0]
            if bound not in used and not re.search(r"\b" + re.escape(bound) + r"\b", strings):
                out.append(make("code.unused_import", fc.path, node.lineno, f"`{bound}` is imported but never used",
                                key=bound))
    return out


# --------------------------------------------------------------------------- checks: files, size, duplicates

JSONC_RE = re.compile(r"(?:^|/)(?:(?:ts|js)config[^/]*\.json|\.vscode/[^/]+\.json|devcontainer\.json|\.eslintrc\.json|"
                      r"tsconfig\.json)$")


def check_files(ctx) -> list:
    out, lim = [], ctx.cfg["limits"]
    total_added, files = 0, 0
    for fc in ctx.changes:
        if fc.binary or fc.symlink or ctx.ignored(fc.path) or is_lockfile(fc.path):
            continue
        files += 1
        total_added += len(fc.added)
        if fc.deleted:
            continue
        if fc.ext in (".json", ".toml") and not JSONC_RE.search(fc.path):
            new, old = ctx.new_text(fc), ctx.old_text(fc)
            parse = json.loads if fc.ext == ".json" else tomllib.loads
            def bad(text):
                try:
                    parse(text)
                    return None
                except (ValueError, tomllib.TOMLDecodeError) as e:
                    return e
            err = bad(new) if new is not None else None
            if err and (old is None or bad(old) is None):
                out.append(make("code.syntax_error", fc.path, getattr(err, "lineno", None), str(err).split("\n")[0][:120],
                                key=fc.path))
        if fc.ext in CODE_EXTS and fc.added:
            new = ctx.new_text(fc)
            n_new = len(new.splitlines()) if new else 0
            old = ctx.old_text(fc)
            n_old = len(old.splitlines()) if old else 0
            if n_new > lim["file_lines"] and n_old <= lim["file_lines"]:
                out.append(make("size.file_length", fc.path, None, f"{n_new} lines (limit {lim['file_lines']})", key=fc.path))
    if not ctx.merge and not ctx.full and (total_added > lim["diff_added_lines"] or files > lim["diff_files"]):
        out.append(make("size.diff_budget", "(commit)", None,
                        f"+{total_added} lines across {files} files (limits: {lim['diff_added_lines']} lines, "
                        f"{lim['diff_files']} files)", key="budget"))
    return out


def check_duplicates(ctx) -> list:
    import hashlib
    n = ctx.cfg["limits"]["duplicate_lines"]
    files = [fc for fc in ctx.code_changes(CODE_EXTS, tests=False) if fc.added]
    rows_by, index = {}, defaultdict(list)
    skip = re.compile(r"[{}()\[\];,]+|(?:else|try|finally|pass|break|continue|return|end)\s*:?;?")
    for fc in files:
        text = ctx.new_text(fc)
        if text is None:
            continue
        rows = []
        for i, raw in enumerate(text.splitlines(), 1):
            s = re.sub(r"\s+", " ", raw.strip())
            if len(s) >= 4 and not skip.fullmatch(s) and not s.startswith(("#", "//", "*", "/*", "import ", "from ", "@")):
                rows.append((i, s))
        rows_by[fc.path] = rows
        for k in range(len(rows) - n + 1):
            h = hashlib.sha1("\n".join(r[1] for r in rows[k:k + n]).encode()).hexdigest()
            index[h].append((fc.path, rows[k][0]))
    out, added_by = [], {fc.path: fc.added for fc in files}
    for fc in files:
        rows, covered = rows_by.get(fc.path, []), 0
        for k in range(len(rows) - n + 1):
            window = rows[k:k + n]
            if window[0][0] <= covered or not all(r[0] in fc.added for r in window):
                continue
            h = hashlib.sha1("\n".join(r[1] for r in window).encode()).hexdigest()
            me = (fc.path, window[0][0])
            # report the later copy only, unless the other copy already existed before this change
            others = sorted(o for o in index[h] if o != me and (o < me or o[1] not in added_by.get(o[0], ())))
            if not others:
                continue
            end = k + n
            while end < len(rows) and rows[end][0] in fc.added:
                end += 1
            covered = rows[end - 1][0]
            other = others[0]
            out.append(make("code.duplicate_block", fc.path, window[0][0],
                            f"{n}+ lines duplicate {other[0]}:{other[1]}", key=h))
    return out


# --------------------------------------------------------------------------- configured commands

def commands_hash(commands) -> str:
    """Identity of a validated [[commands]] list; any change to any command changes it."""
    import hashlib
    return hashlib.sha256(json.dumps(commands, sort_keys=True).encode("utf-8")).hexdigest()


def trusted_hashes(repo) -> set:
    try:
        with open(repo.private_path("aigate/trusted"), encoding="utf-8", errors="replace") as fh:
            return {ln.split()[0] for ln in fh if re.fullmatch(r"[0-9a-f]{64}", (ln.split() or [""])[0])}
    except OSError:
        return set()


def _shell_path(path: str):
    """A changed path as one shell word for {files}, or None when it cannot be passed safely.
    shell=True means /bin/sh on POSIX (shlex quoting) but cmd.exe on Windows, which ignores single quotes:
    there paths are double-quoted and any path with % or " (expanded or unbalanced inside quotes) is left
    out. A leading '-' would read as an option, so such paths get './'."""
    import shlex
    if path.startswith("-"):
        path = "./" + path
    if os.name != "nt":
        return shlex.quote(path)
    if "%" in path or '"' in path:
        return None
    return f'"{path}"'


def run_commands(ctx, stage, paths, trusted=False) -> list:
    """Run the configured commands for this stage. Commands that come from a repository's config run only
    after `aigate-lite trust` approved exactly this list (a cloned repo must not run code on the first
    commit); a config passed with --config is the user's own choice and counts as trusted."""
    out = []
    due = [c for c in ctx.cfg["commands"] if c["stage"] in (stage, "both")
           and (not c["when"] or any(path_matches(p, c["when"]) for p in paths))]
    if due and not trusted and commands_hash(ctx.cfg["commands"]) not in trusted_hashes(ctx.repo):
        names = ", ".join(f"`{c['name']}`" for c in due)
        return [make("commands.untrusted", "(command)", None,
                     f"not run: {names} from {ctx.cfg.origin} not approved yet; review them, then run `aigate-lite trust`",
                     key="untrusted",
                     hint="Commands in a repository's config run on your machine, so each new or changed list "
                          "needs one approval: `aigate-lite trust` shows them and records the approval in .git/aigate/trusted.")]
    for c in due:
        files = " ".join(q for q in (_shell_path(p) for p in paths if os.path.exists(os.path.join(ctx.repo.root, p)))
                         if q is not None)
        cmd = c["run"].replace("{files}", files)
        try:
            proc = subprocess.run(cmd, shell=True, cwd=ctx.repo.root, capture_output=True, text=True,
                                  timeout=c["timeout"])
            ok, msg = proc.returncode == 0, f"`{c['name']}` exited with code {proc.returncode}"
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-15:]
        except subprocess.TimeoutExpired:
            ok, msg, tail = False, f"`{c['name']}` timed out after {c['timeout']}s", []
        if not ok:
            out.append(make("commands.failed", "(command)", None, msg, action=c["action"], key=c["name"],
                            hint="\n".join(tail) or None))
    return out


# --------------------------------------------------------------------------- policy, score, verdict

@dataclasses.dataclass
class Result:
    findings: list
    verdict: str
    escalated: bool
    risk: float
    provenance: tuple
    label: str
    stats: dict
    config: str
    notes: list


def _baseline_accepts(ctx, f, baseline, granted) -> bool:
    """Whether .aigate/baseline.json covers `f`. In `check --all` the numbered fingerprint must be listed. In
    staged/range/push checks only findings on changed lines are seen, so twin numbers mean nothing there: a
    baseline group (rule, file, base key) accepts at most as many added findings as the change removed."""
    if getattr(ctx, "full", False):
        return (f.rule, f.path, f.fingerprint) in baseline
    if (f.rule, f.path, fingerprint_v2(f.rule, f.path, f.base_key or f.key or f.message)) not in baseline:
        return False
    group = (f.rule, f.path, f.base_key or f.key or f.message)
    if granted[group] >= _removed_keys(ctx, f.path)[(f.rule, group[2])]:
        return False
    granted[group] += 1
    return True


def apply_policy(findings, ctx, env) -> list:
    overrides, allows = ctx.cfg["rules"], ctx.cfg["allow"]
    env_allow = [r.strip() for r in env.get("AIGATE_ALLOW", "").split(",") if r.strip()]
    unique, seen = [], set()
    for f in findings:
        ident = (f.rule, f.path, f.key or f.message, f.line)
        if ident not in seen:
            seen.add(ident)
            unique.append(f)
    number_twins(unique)
    baseline = getattr(ctx, "baseline", None) or set()
    granted: Counter = Counter()
    legacy_used: set = set()       # a 0.1 fingerprint allows one finding per file (the first), never its copies
    out = []
    for f in sorted(unique, key=lambda f: (f.path, f.line or 0)):
        if f.rule in FIXED_RULES:
            pass
        elif f.rule in overrides:
            f.action = overrides[f.rule]
        else:
            for pattern, action in overrides.items():
                if fnmatch.fnmatchcase(f.rule, pattern):
                    f.action = action
                    break
        if f.action == "off":
            continue
        f.sensitive = not f.path.startswith("(") and ctx.sensitive(f.path)
        if f.sensitive and f.meta.escalate:
            f.action = {"warn": "fix", "fix": "block"}.get(f.action, f.action)
        legacy_id = (f.rule, f.path, f.legacy_fingerprint)
        for a in allows:
            if fnmatch.fnmatchcase(f.rule, a["rule"]) and (not a["paths"] or path_matches(f.path, a["paths"])) \
                    and (not a["fingerprint"] or a["fingerprint"] == f.fingerprint
                         or (a["fingerprint"] == f.legacy_fingerprint and legacy_id not in legacy_used)):
                old = ""
                if a["fingerprint"] and a["fingerprint"] != f.fingerprint:
                    legacy_used.add(legacy_id)
                    old = " (0.1 fingerprint: update it to " + f.fingerprint + ")"
                f.allowed = f"allow entry{old}: {a['reason']}"
                break
        if not f.allowed and baseline and _baseline_accepts(ctx, f, baseline, granted):
            f.allowed = "baseline"
        if not f.allowed and env_allow and not ctx.agent and f.action in ("warn", "fix") \
                and any(fnmatch.fnmatchcase(f.rule, p) for p in env_allow):
            f.allowed = "AIGATE_ALLOW"
        out.append(f)
    out.sort(key=lambda f: (-ACTION_RANK[f.action], f.path, f.line or 0, f.rule))
    return out


def risk_score(findings, agent: bool) -> float:
    fix_sum = warn_sum = 0.0
    for f in findings:
        if f.allowed:
            continue
        w = WEIGHTS[f.meta.severity] * f.meta.confidence * (1.5 if f.sensitive else 1.0)
        if f.action in ("fix", "block"):
            fix_sum += w
        elif f.action == "warn":
            warn_sum += w
    m = 1.2 if agent else 1.0
    return round(min(100.0, m * fix_sum + min(25.0, m * warn_sum)), 1)


def decide(findings, risk, escalate=True):
    """Verdict = the strongest action among findings that are not allowed. A Fix verdict becomes Block when the
    risk score reaches BLOCK_AT, except with escalate=False (`check --all`, an audit of the whole tree, where
    the score would only measure the repository's size)."""
    top = max((ACTION_RANK[f.action] for f in findings if not f.allowed), default=1)
    verdict = {1: "pass", 2: "warn", 3: "fix", 4: "block"}[top]
    if escalate and verdict == "fix" and risk >= BLOCK_AT:
        return "block", True
    return verdict, False


# --------------------------------------------------------------------------- output

STYLE = {"block": "\033[1;31m", "fix": "\033[1;33m", "warn": "\033[36m", "pass": "\033[1;32m", "dim": "\033[2m",
         "reset": "\033[0m"}


SYMBOLS_UTF = {"ok": "✔", "warn": "!", "fail": "✖", "arrow": "→", "dot": "·", "minus": "−", "ellipsis": "…",
               "dash": "—", "pass": "✔ PASS", "warnmark": "▲ WARN", "fixmark": "✖ FIX", "blockmark": "✖ BLOCK"}
SYMBOLS_ASCII = {"ok": "OK", "warn": "!", "fail": "X", "arrow": "->", "dot": "-", "minus": "-", "ellipsis": "...",
                 "dash": "-", "pass": "OK PASS", "warnmark": "! WARN", "fixmark": "X FIX", "blockmark": "X BLOCK"}
HOOK_CAP = 25


def symbols(stream) -> dict:
    """Unicode symbols when the stream can encode them, else ASCII (a cp1252 pipe on Windows printed `?`)."""
    try:
        "✔✖·→…−≈▲—".encode(getattr(stream, "encoding", None) or "ascii")
        return SYMBOLS_UTF
    except (UnicodeEncodeError, LookupError):
        return SYMBOLS_ASCII


def use_color(choice: str, stream, env) -> bool:
    """--color always/never wins; auto = colour on a TTY, NO_COLOR turns it off, FORCE_COLOR on."""
    if choice != "auto":
        return choice == "always"
    if env.get("NO_COLOR"):
        return False
    return bool(env.get("FORCE_COLOR")) or stream.isatty()


def _summary(res: Result, active, allowed, baselined, stage, sym, s) -> str:
    dot = sym["dot"]
    counts = Counter(f.action for f in active)
    tally = f" {dot} ".join(f"{counts[a]} {a}" for a in ("block", "fix", "warn") if counts[a])
    if allowed:
        tally += f"{f' {dot} ' if tally else ''}{len(allowed)} allowed"
    mark = sym[{"pass": "pass", "warn": "warnmark", "fix": "fixmark", "block": "blockmark"}[res.verdict]]
    tail = " (risk score escalated it)" if res.escalated else ""
    stopped = f" {sym['dash']} {stage} stopped" if res.verdict in ("fix", "block") and stage != "scan" else ""
    return (f"{s(res.verdict)}{mark}{s('reset')}{tail} {dot} risk {res.risk:g}/100{f' {dot} ' + tally if tally else ''}"
            f" {dot} {res.stats['seconds']:.1f}s{stopped}")


def _next_line(res: Result, active, baselined, stage) -> str | None:
    hidden = f" ({len(baselined)} baseline finding{'' if len(baselined) == 1 else 's'} hidden)" if baselined else ""
    act = "push" if stage == "push" else "commit"
    blocks = [f for f in active if f.action == "block"]
    fixes = [f for f in active if f.action == "fix"]
    if res.verdict == "block" and blocks:
        n = len(blocks)
        return f"next: fix the {n} BLOCK line{'' if n == 1 else 's'} above{hidden}"
    if res.verdict in ("block", "fix"):
        n, rules = len(fixes), sorted({f.rule for f in fixes})
        if stage == "scan":
            return f"next: fix the {n} FIX line{'' if n == 1 else 's'} above, or accept today's findings: " \
                   f"aigate-lite baseline{hidden}"
        if len(rules) == 1 and res.provenance[0] != "agent" and not res.escalated:
            return f"next: fix it, or for this {act} only (humans): AIGATE_ALLOW={rules[0]} git {act} ...{hidden}"
        return f"next: fix the {n} FIX line{'' if n == 1 else 's'} above (a false positive? aigate-lite explain " \
               f"<rule>){hidden}"
    if res.verdict == "warn":
        return f"next: {act if stage != 'scan' else 'nothing'} {'allowed' if stage != 'scan' else 'blocks'}; " \
               f"the warnings are worth a look{hidden}"
    return f"next: nothing to do{hidden}" if baselined else None


def _fit(text: str, width, sym) -> str:
    if width and len(text) > width:
        return text[:max(0, width - len(sym["ellipsis"]))] + sym["ellipsis"]
    return text


def render_text(res: Result, *, color=False, verbose=False, quiet=False, stage="commit", mode="staged",
                hook=False, width=None, sym=None) -> str:
    """The text report. Default: compact, grouped by file. -v: the detailed view. -q: only what stops the commit.
    `width` truncates lines (only passed when stdout is a terminal, so CI logs stay complete)."""
    sym = sym or SYMBOLS_UTF
    s = (lambda k: STYLE[k]) if color else (lambda k: "")
    active = [f for f in res.findings if not f.allowed]
    allowed = [f for f in res.findings if f.allowed and f.allowed != "baseline"]
    baselined = [f for f in res.findings if f.allowed == "baseline"]
    if quiet and res.verdict == "pass":
        return ""
    out = []
    st = res.stats
    if verbose or quiet:
        prov = res.provenance[0] + (f" via {res.provenance[1]}" if res.provenance[0] == "agent" else "")
        out.append(f"{s('dim')}aigate-lite {__version__} {sym['dot']} {res.label} {sym['dot']} {st['files']} "
                   f"file{'' if st['files'] == 1 else 's'} (+{st['added']} -{st['removed']}) {sym['dot']} {prov} "
                   f"{sym['dot']} config: {res.config}{s('reset')}")
        out += [f"{s('dim')}  note: {note}{s('reset')}" for note in res.notes]
        for f in active:
            if quiet and f.action == "warn":
                continue
            out.append(f"  {s(f.action)}{f.action.upper():<5}{s('reset')} {f.rule:<28} {f.path}"
                       f"{f':{f.line}' if f.line else ''}")
            out.append(f"        {f.message}")
            hint = f.hint if f.hint else (f.meta.hint if (verbose or f.action in ("fix", "block")) else None)
            for i, ln in enumerate((hint or "").splitlines()):
                out.append(f"        {s('dim')}{sym['arrow'] + ' ' if i == 0 else '  '}{ln}{s('reset')}")
            if verbose:
                out.append(f"        {s('dim')}fingerprint {f.fingerprint}{s('reset')}")
        if verbose:
            for f in allowed + baselined:
                tag = "BASELINE" if f.allowed == "baseline" else "ALLOW"
                out.append(f"  {s('dim')}{tag} {f.rule:<28} {f.path}{':' + str(f.line) if f.line else ''} "
                           f"({f.allowed}) fingerprint {f.fingerprint}{s('reset')}")
        summary = _summary(res, active, allowed, baselined, stage, sym, s)
        if baselined:
            summary += f" {sym['dot']} {len(baselined)} in baseline (hidden; -v shows them)" if not verbose \
                else f" {sym['dot']} {len(baselined)} in baseline"
        out.append(summary)
        if quiet:
            if res.verdict in ("fix", "block") and stage != "scan":
                out.append(f"{s('dim')}  Fix the findings, or for a false positive: `aigate-lite explain <rule>` shows "
                           f"how to allow it. Bypass once (not recommended): git "
                           f"{'push' if stage == 'push' else 'commit'} --no-verify{s('reset')}")
        else:
            nxt = _next_line(res, active, baselined, stage)
            if nxt:
                out.append(nxt)
        return "\n".join(out) + "\n"

    who = "human" if res.provenance[0] != "agent" else f"AI agent ({res.provenance[1].lstrip('$')})"
    head = (f"aigate-lite {__version__} {sym['dot']} {mode} {sym['dot']} {st['files']} file"
            f"{'' if st['files'] == 1 else 's'} +{st['added']} {sym['minus']}{st['removed']} {sym['dot']} {who}")
    out.append(f"{s('dim')}{_fit(head, width, sym)}{s('reset')}")
    out += [f"{s('dim')}{_fit('  note: ' + note, width, sym)}{s('reset')}" for note in res.notes]
    rank = {"block": 0, "fix": 1, "warn": 2}
    by_file: dict = defaultdict(list)
    for f in active:
        by_file[f.path].append(f)
    files = sorted(by_file, key=lambda p: (min(rank[f.action] for f in by_file[p]), p))
    lw = max((len(str(f.line or "-")) for f in active), default=1)
    rw = max((len(f.rule) for f in active), default=0)
    cap = HOOK_CAP if hook else None
    shown, hinted = 0, set()
    for path in files:
        if cap is not None and shown >= cap:
            break
        out.append(f"{s('dim')}{_fit(path, width, sym)}{s('reset')}")
        for f in sorted(by_file[path], key=lambda f: (f.line or 0, rank[f.action], f.rule)):
            if cap is not None and shown >= cap:
                break
            prefix = f"  {str(f.line or '-'):>{lw}}  "
            plain = f"{prefix}{f.action.upper():<5}  {f.rule:<{rw}}  {f.message}"
            plain = _fit(plain, width, sym)
            out.append(plain[:len(prefix)] + s(f.action) + plain[len(prefix):len(prefix) + 5] + s("reset")
                       + plain[len(prefix) + 5:])
            shown += 1
            hint = (f.hint or f.meta.hint or "").splitlines()
            if f.action in ("block", "fix") and hint and f.rule not in hinted:
                hinted.add(f.rule)
                out.append(f"{s('dim')}{_fit(' ' * (len(prefix) + 7) + sym['arrow'] + ' ' + hint[0], width, sym)}"
                           f"{s('reset')}")
    if cap is not None and len(active) > shown:
        out.append(f"  +{len(active) - shown} more {sym['dash']} run: aigate-lite check")
    out.append(_summary(res, active, allowed, baselined, stage, sym, s))
    nxt = _next_line(res, active, baselined, stage)
    if nxt:
        out.append(_fit(nxt, width, sym))
    return "\n".join(out) + "\n"


def report_text(res: Result, *, color: bool, verbose: bool, quiet: bool, stage: str, mode="staged",
                hook=False) -> None:
    import shutil
    width = shutil.get_terminal_size().columns if sys.stdout.isatty() else None
    sys.stdout.write(render_text(res, color=color, verbose=verbose, quiet=quiet, stage=stage, mode=mode, hook=hook,
                                 width=width, sym=symbols(sys.stdout)))


def report_json(res: Result) -> None:
    print(json.dumps({
        "tool": "aigate-lite", "version": __version__, "schema": 1, "label": res.label,
        "provenance": {"kind": res.provenance[0], "source": res.provenance[1]}, "config": res.config,
        "verdict": res.verdict, "escalated": res.escalated, "risk": res.risk,
        "exit_code": 1 if res.verdict in ("fix", "block") else 0, "stats": res.stats, "notes": res.notes,
        "findings": [{"rule": f.rule, "action": f.action, "severity": f.meta.severity, "confidence": f.meta.confidence,
                      "path": f.path, "line": f.line, "message": f.message, "hint": f.hint or f.meta.hint,
                      "fingerprint": f.fingerprint, "legacy_fingerprint": f.legacy_fingerprint,
                      "sensitive_path": f.sensitive, "allowed": f.allowed}
                     for f in res.findings],
    }, indent=2))


def report_github(res: Result) -> None:
    def esc(v, prop=False):
        v = str(v).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        return v.replace(":", "%3A").replace(",", "%2C") if prop else v
    for f in res.findings:
        if f.allowed:
            continue
        level = "warning" if f.action == "warn" else "error"
        where = "" if f.path.startswith("(") else f"file={esc(f.path, True)}" + (f",line={f.line}" if f.line else "") + ","
        print(f"::{level} {where}title={esc(f.rule, True)}::{esc(f.message)}")
    print(f"aigate-lite verdict: {res.verdict.upper()} (risk {res.risk:g}/100, {len(res.findings)} findings)")


def report_sarif(res: Result) -> None:
    """SARIF 2.1.0 for code-scanning uploads. Allowed findings are kept, marked as suppressed."""
    import urllib.parse
    level = {"block": "error", "fix": "error", "warn": "warning"}
    used = sorted({f.rule for f in res.findings})
    results = []
    for f in res.findings:
        r = {"ruleId": f.rule, "ruleIndex": used.index(f.rule), "level": level.get(f.action, "note"),
             "message": {"text": f.message}, "partialFingerprints": {"aigateFingerprint/v1": f.legacy_fingerprint,
                                                                 "aigateFingerprint/v2": f.fingerprint},
             "properties": {"action": f.action, "sensitivePath": f.sensitive}}
        # code scanning needs a location; repo-wide findings ("(gate)", "(commit)") point at the config
        path = CONFIG_PATH if f.path.startswith("(") else f.path
        loc = {"artifactLocation": {"uri": urllib.parse.quote(path, safe="/")}}
        if f.line and path == f.path:
            loc["region"] = {"startLine": f.line}
        r["locations"] = [{"physicalLocation": loc}]
        if f.allowed:
            r["suppressions"] = [{"kind": "external", "justification": f.allowed}]
        results.append(r)
    rules = [{"id": rid, "shortDescription": {"text": RULES[rid].title}, "help": {"text": RULES[rid].hint},
              "defaultConfiguration": {"level": level.get(RULES[rid].action, "note")},
              "properties": {"severity": RULES[rid].severity, "confidence": RULES[rid].confidence}} for rid in used]
    print(json.dumps({
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "aigate-lite", "version": __version__, "rules": rules}},
                  "results": results,
                  "properties": {"verdict": res.verdict, "risk": res.risk, "escalated": res.escalated}}],
    }, indent=2))


# --------------------------------------------------------------------------- running a check

LANGUAGE_EXTS = {"python": PY_EXTS, "javascript": JS_EXTS, "code": CODE_EXTS}


@dataclasses.dataclass(frozen=True)
class Checker:
    """One check. `languages` (keys of LANGUAGE_EXTS) names the files it reads; it is skipped when the change
    has none of them (None: any file can matter). `run(ctx)` returns a list of Findings; an exception becomes
    a gate.error finding, so a broken checker fails closed without hiding the others."""
    name: str
    languages: tuple | None
    needs_network: bool
    run: object

    def applies(self, ctx) -> bool:
        if self.languages is None:
            return True
        exts = set().union(*(LANGUAGE_EXTS[lang] for lang in self.languages))
        return any(True for _ in ctx.code_changes(exts))


CHECKERS = [
    Checker("check_secrets", None, False, check_secrets),
    Checker("check_env_files", None, False, check_env_files),
    Checker("check_env_not_ignored", None, False, check_env_not_ignored),
    Checker("check_paths", None, False, check_paths),
    Checker("check_deps", None, True, check_deps),
    Checker("check_tests", None, False, check_tests),
    Checker("check_suppressions", ("code",), False, check_suppressions),
    Checker("check_code_lines", ("code",), False, check_code_lines),
    Checker("check_python", ("python",), False, check_python),
    Checker("check_files", None, False, check_files),
    Checker("check_duplicates", ("code",), False, check_duplicates),
]
SECRET_CHECKERS = ("check_secrets", "check_env_files")
FULL_SCAN_SKIP = ("check_paths",)       # forbidden/scope/config-changed rules judge changes, not existing files   # in ranges these run commit by commit instead


def _commit_info(repo, shas) -> tuple:
    """(messages, authors, sha -> first parent or None)."""
    messages, authors, parents = [], [], {}
    for i in range(0, len(shas), 200):
        out = repo.git("log", "--no-walk=unsorted", "-z", "--format=%H%x1f%P%x1f%an%x1f%cn%x1f%B", *shas[i:i + 200])
        for rec in out.split("\0"):
            parts = rec.split("\x1f", 4)
            if len(parts) == 5:
                parents[parts[0]] = (parts[1].split() or [None])[0]
                authors += parts[2:4]
                messages.append(parts[4])
    return messages, authors, parents


def range_error(repo, spec, missing) -> GitError:
    """Why `--range` cannot be resolved, with the fix: no commits, a shallow CI clone, or an unknown ref."""
    if not repo.commit("HEAD"):
        return GitError("this repository has no commits yet", "make a first commit, then check a range")
    if run_git(repo.root, "rev-parse", "--is-shallow-repository", check=False).strip() == "true":
        return GitError(f"cannot resolve range {spec!r}: {missing!r} is unknown here (a typo, or missing because "
                        "this clone is shallow)", "check the name; in CI fetch the full history: fetch-depth: 0 "
                        "(GitHub Actions) / GIT_DEPTH: 0 (GitLab CI), or run: git fetch --unshallow")
    remotes = run_git(repo.root, "remote", check=False).split()
    remote, _, branch = missing.partition("/")
    fix = f"check the name, or run: git fetch {remote} {branch}:refs/remotes/{remote}/{branch}" \
        if branch and remote in remotes else "check the name (a branch, tag or commit that exists here)"
    return GitError(f"cannot resolve range {spec!r}: {missing!r} is not a commit or branch here", fix)


def plan_runs(repo, args, env) -> list:
    """Each run: (label, base, head, commits)."""
    if getattr(args, "all", False):
        head = repo.commit("HEAD")
        if not head:
            raise GitError("check --all needs at least one commit", "make a first commit")
        return [("full tree", repo.empty_tree(), head, [])]
    if args.range:
        spec = args.range
        a, b = re.split(r"\.\.\.?", spec, 1) if ".." in spec else (spec, "HEAD")
        a, b = a or "HEAD", b or "HEAD"                 # git reads an empty side as HEAD
        head, start = repo.commit(b), repo.commit(a)
        if not head or not start:
            raise range_error(repo, spec, a if not start else b)
        base = run_git(repo.root, "merge-base", start, head, check=False).strip() or start
        commits = repo.git("rev-list", "--reverse", "--no-merges", f"{start}..{head}").split()
        return [(f"range {spec}", base, head, commits)]
    if args.push:
        refs = []
        if not sys.stdin.isatty():
            for line in sys.stdin.read().splitlines():
                parts = line.split()
                if len(parts) == 4:
                    refs.append(parts)
        if not refs and env.get("PRE_COMMIT_FROM_REF") and env.get("PRE_COMMIT_TO_REF"):
            refs = [["(pre-commit)", env["PRE_COMMIT_TO_REF"], "(remote)", env["PRE_COMMIT_FROM_REF"]]]
        runs = []
        for local_ref, local_sha, _remote_ref, remote_sha in refs:
            if re.fullmatch(r"0+", local_sha):
                continue                                   # deleting a branch
            head = repo.commit(local_sha)
            remote = None if re.fullmatch(r"0+", remote_sha) else repo.commit(remote_sha)
            if not head:
                continue
            if remote:
                base = run_git(repo.root, "merge-base", remote, head, check=False).strip() or remote
                commits = repo.git("rev-list", "--reverse", "--no-merges", f"{remote}..{head}").split()
            else:
                not_remotes = [f"--remotes={args.remote}"] if args.remote else ["--remotes"]
                commits = repo.git("rev-list", "--reverse", "--no-merges", head, "--not", *not_remotes).split()
                if not commits:
                    continue
                parent = repo.commit(commits[0] + "^")
                base = parent or repo.empty_tree()
            runs.append((f"push {local_ref.replace('refs/heads/', '')}", base, head, commits))
        return runs
    head = repo.commit("HEAD")
    return [("staged changes", head or repo.empty_tree(), None, [])]


def run_check(args, env, collect=False):
    """Run the gate and print the report; with collect=True return the Result instead (int when nothing ran)."""
    started = time.monotonic()
    repo = Repo.discover()
    full = bool(getattr(args, "all", False))
    stage = "scan" if full else "push" if (args.push or args.range) else "commit"
    pre = load_config(repo, "worktree", args.config)
    quick = detect_provenance(env, pre["provenance"]["agent_env"], pre["provenance"]["default"])
    if env.get("AIGATE_SKIP") == "1" and quick[0] == "human":
        print("aigate-lite: skipped (AIGATE_SKIP=1)")
        return 0
    runs = plan_runs(repo, args, env)
    if not runs:
        if args.format == "text" and not args.quiet:
            print("aigate-lite: nothing to check")
        return 0

    registry = Registry(offline=args.offline or env.get("AIGATE_OFFLINE") == "1",
                        budget=float(pre["gate"].get("max_seconds", 20)), use_cache=not args.no_cache,
                        fixture=env.get("AIGATE_REGISTRY_FIXTURE"))
    results = ResultCache(default_cache_dir(),
                          enabled=(args.cache or pre["gate"].get("cache") is True) and not args.no_cache)
    findings, notes, stats, label = [], [], Counter(), runs[0][0] if len(runs) == 1 else f"push ({len(runs)} refs)"
    provenance, cfg = quick, pre
    for run_label, base, head, commits in runs:
        messages, authors, parents = _commit_info(repo, commits) if commits else ((), (), {})
        provenance = detect_provenance(env, pre["provenance"]["agent_env"], pre["provenance"]["default"],
                                       messages, authors)
        if args.config:
            cfg, bl_source = pre, "worktree"
        elif provenance[0] == "agent":                # an agent cannot loosen the gate for its own change
            # its change's base; for `check --all` (an audit of HEAD, not a change) HEAD itself
            source = (repo.commit("HEAD") or "") if head is None else head if full else base
            committed = bool(source) and source != repo.empty_tree()
            cfg = load_config(repo, source) if committed else load_config(repo, "__none__")
            bl_source = source if committed else None
        elif head is None:
            cfg, bl_source = pre, ""                  # the baseline as staged, not whatever is on disk
        else:
            cfg, bl_source = load_config(repo, head), head
        changes = collect_changes(repo, base, head)
        if full:
            changes = [fc for fc in changes if not path_matches(fc.path, cfg["paths"]["ignore"])]
        if head is None and not changes:
            if args.format == "text" and not args.quiet:
                print("aigate-lite: nothing staged to check")
            return 0
        merge = head is None and os.path.exists(repo.private_path("MERGE_HEAD"))
        ctx = Context(repo, cfg, changes, mode="staged" if head is None else "range", base=base, head=head,
                      provenance=provenance, registry=registry, merge=merge, label=run_label, results=results,
                      full=full)
        if not os.path.exists(os.path.join(repo.root, BASELINE_PATH)):
            bl_source = None                          # no git reads for repositories without a baseline
        ctx.baseline, bl_problems = load_baseline(repo, bl_source)
        if bl_source == "" and repo.read(":" + BASELINE_PATH) != repo.read("HEAD:" + BASELINE_PATH):
            bl_problems.append(f"using the staged {BASELINE_PATH}, which differs from HEAD")
        notes += [p for p in bl_problems if p not in notes]
        counted = [fc for fc in changes if not ctx.ignored(fc.path)]
        stats["files"] += len(counted)
        stats["added"] += sum(len(fc.added) for fc in counted)
        stats["removed"] += sum(len(fc.removed) for fc in counted)
        for problem in cfg.problems:
            findings.append(make("gate.config_invalid", CONFIG_PATH, None, problem, key=problem))
        fail_action = "block" if cfg["gate"].get("fail_closed", True) else "warn"
        for checker in CHECKERS:
            if head is not None and not full and checker.name in SECRET_CHECKERS:
                continue                               # scanned commit by commit below
            if full and checker.name in FULL_SCAN_SKIP:
                continue
            try:
                if checker.applies(ctx):
                    findings += checker.run(ctx)
            except Exception as e:                     # one broken checker must not hide the others
                findings.append(make("gate.error", "(gate)", None, f"{checker.name} failed: {type(e).__name__}: {e}",
                                     action=fail_action, key=checker.name))
        if head is not None:
            if len(commits) > 300:
                notes.append(f"{len(commits)} commits: secrets scanned in the first 300 only")
            for sha in commits[:300]:
                parent = (parents[sha] if sha in parents else repo.commit(sha + "^")) or repo.empty_tree()
                try:
                    per_commit = collect_changes(repo, parent, sha)
                    findings += check_secrets(ctx, per_commit, f" (commit {sha[:8]})")
                    findings += check_env_files(ctx, per_commit, f" (commit {sha[:8]})")
                except Exception as e:
                    findings.append(make("gate.error", "(gate)", None, f"secret scan of {sha[:8]} failed: {e}",
                                         action=fail_action, key=sha))
        if not args.no_commands and not full:
            paths = [fc.path for fc in changes if not fc.deleted]
            findings += run_commands(ctx, stage, paths, trusted=bool(args.config))
        if merge:
            notes.append("merge commit: incoming changes from the merged branch are checked too")
    registry.save()
    results.save()
    if registry.failures:
        notes.append(f"{registry.failures} registry lookup(s) failed ({registry.failure_reason or 'offline or blocked'}); "
                     "dependency checks are incomplete (--offline skips them)")

    final_ctx = ctx
    findings = apply_policy(findings, final_ctx, env)
    risk = risk_score(findings, provenance[0] == "agent")
    verdict, escalated = decide(findings, risk, escalate=not full)
    stats["seconds"] = round(time.monotonic() - started, 2)
    res = Result(findings, verdict, escalated, risk, provenance, label,
                 {"files": stats["files"], "added": stats["added"], "removed": stats["removed"],
                  "seconds": stats["seconds"]}, cfg.origin, notes)
    if collect:
        return res
    if args.format == "json":
        report_json(res)
    elif args.format == "github":
        report_github(res)
    elif args.format == "sarif":
        report_sarif(res)
    else:
        mode = "all" if full else "push" if args.push else "range" if args.range else "staged"
        report_text(res, color=use_color(args.color, sys.stdout, env), verbose=args.verbose, quiet=args.quiet,
                    stage=stage, mode=mode, hook=bool(args.staged or args.push))
    return 1 if verdict in ("fix", "block") else 0


# --------------------------------------------------------------------------- hooks and small commands

HOOK_MARK = "# aigate-lite managed hook"

# POSIX hooks start the gate through this loader (`python -P -c HOOK_LOADER script args...`) so its bytecode
# is reused instead of recompiling ~200 KB on every run. It keeps one .pyc per script path for the gate itself
# (stdlib imports are untouched) in <cache dir>/pycache. That directory is opened once without following
# symlinks and must be owned by this user; if it was not 0700 it is tightened and nothing in it is trusted on
# that run. A .pyc is used only when it is a regular file owned by this user, not group/other-writable, not
# hard-linked, opened relative to that directory, and its header carries this interpreter's magic number and
# the hash of the current source (any edit is noticed, whatever its size or mtime). In every other case
# (relative or unusable cache dir, any error) and on Windows (where ownership and ACLs cannot be checked
# without extra code) the source is compiled and run as before. The import system is never used for the
# gate, so a __pycache__ next to a vendored script is never read. -P keeps the repository root off sys.path;
# the script's real directory goes first, as with `python script.py`.
HOOK_LOADER = """\
import importlib.util, marshal, os, stat, sys
script = os.path.abspath(sys.argv[1])
sys.argv = [script] + sys.argv[2:]
sys.path.insert(0, os.path.dirname(os.path.realpath(script)))
CODE = type(compile("", "", "exec"))
MAX_AGE = 30 * 86400


def pinned_dir(path):
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None, False
    try:
        st = os.fstat(fd)
        if st.st_uid == os.getuid():
            if not st.st_mode & 0o077:
                return fd, True
            os.fchmod(fd, 0o700)                    # ours but loose: tighten it, trust nothing in it this run
            return fd, False
    except OSError:
        pass
    os.close(fd)
    return None, False


def read_pyc(dfd, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022 or st.st_nlink != 1:
            return b""
        return fh.read()


def write_pyc(dfd, name, data):
    tmp = f"{name}.{os.getpid()}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        now = os.stat(name, dir_fd=dfd).st_mtime
        for entry in os.listdir(dfd):              # a new .pyc is rare: drop ones unused for MAX_AGE
            if entry != name and entry.startswith("aigate_lite.") and entry.endswith((".pyc", ".tmp")):
                try:
                    if now - os.stat(entry, dir_fd=dfd, follow_symlinks=False).st_mtime > MAX_AGE:
                        os.unlink(entry, dir_fd=dfd)
                except OSError:
                    pass
    except OSError:
        pass
    finally:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass


with open(script, "rb") as fh:
    source = fh.read()
header = importlib.util.MAGIC_NUMBER + b"\\x03\\x00\\x00\\x00" + importlib.util.source_hash(source)
xdg = os.environ.get("XDG_CACHE_HOME", "")
base = xdg if os.path.isabs(xdg) else os.path.join(os.path.expanduser("~"), ".cache")
cache = os.environ.get("AIGATE_CACHE_DIR") or os.path.join(base, "aigate-lite")
dfd, trusted, code = None, False, None
if os.name != "nt" and os.path.isabs(cache) and os.listdir in os.supports_fd \\
        and {os.open, os.rename, os.unlink, os.stat} <= os.supports_dir_fd:
    dfd, trusted = pinned_dir(os.path.join(cache, "pycache"))
name = "aigate_lite.{}.{}.opt{}.pyc".format(importlib.util.source_hash(script.encode("utf-8", "surrogateescape")).hex(),
                                            sys.implementation.cache_tag, sys.flags.optimize)
if trusted:
    try:
        data = read_pyc(dfd, name)
        if data[:16] == header:
            code = marshal.loads(data[16:])
    except (OSError, ValueError, EOFError, TypeError):
        code = None
    if not isinstance(code, CODE):
        code = None
if code is None:
    code = compile(source, script, "exec")
    if dfd is not None:
        write_pyc(dfd, name, header + marshal.dumps(code))
if dfd is not None:
    os.close(dfd)
module = type(sys)("aigate_lite")
module.__file__ = script
sys.modules["aigate_lite"] = module
exec(code, module.__dict__)
sys.exit(module.main())
"""


# Interpreter discovery shared by hooks (as sh) and `install`/`doctor` (as Python, see discover_python).
# Order: $AIGATE_PYTHON, the interpreter pinned at install (non-vendored hooks; used without probing), then
# python3, python, `py -3`. A candidate qualifies when it runs PY_PROBE with exit 0 and prints a version, so
# the Windows Store `python3`/`python` stubs (non-zero exit, no version) are skipped. AIGATE_PYTHON is ignored
# while an AI agent runs the commit (like AIGATE_SKIP): it would let an agent swap the gate for any program.
# (Versioned names such as python3.12 are not tried yet: see PROGRESS.md, Phase 2 BLOCKED.)
PY_PROBE = "import sys; print(sys.version.split()[0]); sys.exit(0 if sys.version_info >= (3, 11) else 1)"
PY_GENERIC = (("python3",), ("python",), ("py", "-3"))
AIGATE_PYTHON_IGNORED = "AIGATE_PYTHON is ignored while an AI agent runs the commit"
HOOK_DISCOVERY = """\
PROBE=%(probe)s
TRIED=""
try_py() {
  v=$("$1" ${2:+"$2"} -c "$PROBE" </dev/null 2>/dev/null) && [ -n "$v" ] && { PY=$1; PYFLAG=$2; return 0; }
  if [ -n "$v" ]; then v="$v, too old"; else v="not found or not a working Python"; fi
  TRIED="$TRIED
  $1${2:+ $2}: $v"
  return 1
}
discover() {
  for c in python3 python 'py -3'; do try_py $c && return 0; done
  return 1
}
PY=""
PYFLAG=""
if [ -n "$AIGATE_PYTHON" ]; then
  if [ -n "%(agent_vars)s" ] || [ "$AIGATE_PROVENANCE" = agent ]; then
    echo "aigate-lite: %(ignored)s" >&2
  else try_py "$AIGATE_PYTHON"; fi
fi"""


def hook_script(kind: str, vendored: bool, py: str, script: str) -> str:
    local = f'"$HOOK_DIR/{kind}.local"'
    lines = ["#!/bin/sh", f"{HOOK_MARK} ({kind}). Remove with: aigate-lite uninstall",
             'HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"']
    if kind == "pre-push":
        lines += ['REFS="$(cat)"',
                  f'if [ -x {local} ]; then printf "%s\\n" "$REFS" | {local} "$@" || exit $?; fi']
    else:
        lines.append(f'if [ -x {local} ]; then {local} "$@" || exit $?; fi')
    if not vendored:
        lines += [f"PY={_sh_quote(py.replace(chr(92), '/'))}",
                  f"SCRIPT={_sh_quote(script.replace(chr(92), '/'))}",
                  'PINNED=$PY']
    # Resets PY and tries AIGATE_PYTHON. PY is always one word (quoted when used) and PYFLAG carries `py`'s
    # version flag, so paths with spaces work.
    lines += (HOOK_DISCOVERY % {"probe": _sh_quote(PY_PROBE), "ignored": AIGATE_PYTHON_IGNORED,
                                "agent_vars": "".join("$" + v for v in BUILTIN_AGENT_ENV)}).splitlines()
    if vendored:
        lines += ['[ -n "$PY" ] || discover',
                  'ROOT="$(git rev-parse --show-toplevel)" || exit 1',
                  'SCRIPT="$ROOT/.aigate/aigate_lite.py"']
    else:
        lines += ['if [ -z "$PY" ] && [ -x "$PINNED" ]; then PY=$PINNED',              # fast path: no probe
                  'elif [ -z "$PY" ]; then',
                  '  TRIED="$TRIED',
                  '  $PINNED: missing (pinned at install)"',
                  '  discover && echo "aigate-lite: $PINNED (pinned at install) is missing; using $PY${PYFLAG:+ $PYFLAG}.'
                  ' To pin it, re-run aigate-lite install." >&2',
                  'fi']
    what = "push" if kind == "pre-push" else "commit"
    lines.append(f'[ -n "$PY" ] || {{ printf "aigate-lite: no Python 3.11+ found, so the {what} is blocked. Tried:%s\\n'
                 f'  fix: install Python 3.11+ or set AIGATE_PYTHON=/path/to/python3 '
                 f'(bypass once with git {what} --no-verify)\\n" "$TRIED" >&2; exit 1; }}')
    run = '"$PY" $PYFLAG "$SCRIPT"'
    if os.name != "nt":                                # see HOOK_LOADER; Windows hooks run the script directly
        lines.append(f"LOADER={_sh_quote(HOOK_LOADER)}")
        run = run.replace('"$SCRIPT"', '-P -c "$LOADER" "$SCRIPT"')
    restore = ("restore it (git checkout -- .aigate/aigate_lite.py) or run: aigate-lite install --vendor"
               if vendored else "run: aigate-lite install")
    lines.append(f'[ -f "$SCRIPT" ] || {{ printf "aigate-lite: %s is missing\\n  fix: {restore} '
                 f'(or bypass once with --no-verify)\\n" "$SCRIPT" >&2; exit 1; }}')
    if kind == "pre-push":
        lines.append(f'printf "%s\\n" "$REFS" | {run} check --push --remote "$1"')
    else:
        lines.append(f'exec {run} check --staged')
    return "\n".join(lines) + "\n"


@dataclasses.dataclass
class PythonChoice:
    command: list            # e.g. ["/usr/bin/python3"] or ["py", "-3.12"]; empty when nothing qualifies
    reason: str
    tried: list              # (candidate, result) in the order tried
    pinned_missing: bool = False


def _probe_python(cmd) -> tuple:
    """(qualifies, version text) for one candidate, like the hook's try_py."""
    import shutil
    if not (os.path.isabs(cmd[0]) and os.path.exists(cmd[0])) and not shutil.which(cmd[0]):
        return False, ""
    try:
        proc = subprocess.run([*cmd, "-c", PY_PROBE], capture_output=True, text=True, timeout=15,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return proc.returncode == 0, (proc.stdout.strip().splitlines() or [""])[0]


def hook_python_line(vendored: bool) -> str:
    """One line for `install`: which interpreter the hooks will run and why (a dry run of the hook's search)."""
    choice = discover_python(os.environ, pinned=None if vendored else sys.executable,
                             exists=lambda p: os.access(p, os.X_OK))
    if choice.command:
        return f"hook Python: {choice.reason}"
    tried = "; ".join(f"{c}: {r}" for c, r in choice.tried)
    return (f"hook Python: no Python 3.11+ found ({tried}), so the hooks will block commits\n"
            "  fix: install Python 3.11+ or set AIGATE_PYTHON=/path/to/python3")


def discover_python(env, pinned=None, probe=None, exists=os.path.exists) -> PythonChoice:
    """The interpreter a hook would use, found in the same order as the hook script (dry run)."""
    probe = probe or _probe_python
    tried = []

    def attempt(cmd, why):
        ok, version = probe(list(cmd))
        ok = ok and bool(version)                     # like the hook: a program that prints nothing is not Python
        label = " ".join(cmd)
        tried.append((label, version + ("" if ok else ", too old") if version else "not found or not a working Python"))
        return PythonChoice(list(cmd), f"{label} ({version}): {why}", tried) if ok else None

    if env.get("AIGATE_PYTHON"):
        if any(env.get(v) for v in BUILTIN_AGENT_ENV) or env.get("AIGATE_PROVENANCE") == "agent":
            tried.append(("AIGATE_PYTHON", "ignored: " + AIGATE_PYTHON_IGNORED))
        else:
            found = attempt([env["AIGATE_PYTHON"]], "set by AIGATE_PYTHON")
            if found:
                return found
    if pinned:
        if exists(pinned):
            tried.append((pinned, "pinned at install"))
            version = f" ({sys.version.split()[0]})" if pinned == sys.executable else ""
            return PythonChoice([pinned], f"{pinned}{version}: pinned at install (used without probing)", tried)
        tried.append((pinned, "missing (pinned at install)"))
    for cmd in PY_GENERIC:
        found = attempt(cmd, "first working Python 3.11+ on PATH")
        if found:
            found.pinned_missing = bool(pinned)
            return found
    return PythonChoice([], "no Python 3.11+ found", tried, bool(pinned))


def _sh_quote(value: str) -> str:
    """Single-quote for POSIX sh: spaces, $, backticks and quotes stay literal."""
    return "'" + value.replace("'", "'\\''") + "'"


PRE_COMMIT_SNIPPET = """  - repo: local
    hooks:
      - id: aigate-lite
        name: aigate-lite
        entry: python3 .aigate/aigate_lite.py check --staged
        language: system
        pass_filenames: false
        always_run: true
      - id: aigate-lite-push
        name: aigate-lite (push)
        entry: python3 .aigate/aigate_lite.py check --push
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-push]"""


def cmd_install(args) -> int:
    repo = Repo.discover()
    hooks_dir = repo.git_path("hooks")
    hooks_path = run_git(repo.root, "config", "--get", "core.hooksPath", check=False).strip()
    if hooks_path and not args.force:
        print(f"core.hooksPath is set to {hooks_path!r} (husky or a similar tool manages your hooks).\n"
              "Add this line to that tool's pre-commit hook instead:\n"
              "    python3 .aigate/aigate_lite.py check --staged\n"
              "(run `aigate-lite install --vendor --force` to copy the script into .aigate/ and install anyway)")
        return 1
    if os.path.exists(os.path.join(repo.root, ".pre-commit-config.yaml")) and not args.force:
        print("This repo uses the pre-commit framework. Copy aigate_lite.py to .aigate/aigate_lite.py,\n"
              "then add this to .pre-commit-config.yaml under `repos:` and run `pre-commit install -t pre-commit -t pre-push`:\n"
              + PRE_COMMIT_SNIPPET +
              "\nOr install plain git hooks anyway with --force (the existing hook is kept and still runs first).")
        return 1
    script = os.path.abspath(__file__)
    if args.vendor:
        dest = os.path.join(repo.root, ".aigate", "aigate_lite.py")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.abspath(dest) != script:
            import shutil
            shutil.copy2(script, dest)
        print("copied the gate to .aigate/aigate_lite.py (commit it so teammates get the same version)")
    kinds = ["pre-commit"] + ([] if args.no_pre_push else ["pre-push"])
    for kind in kinds:
        path = os.path.join(hooks_dir, kind)
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                ours = HOOK_MARK in fh.read()
            if not ours and not args.force:
                print(f"{path} already exists. Re-run with --force to keep it as {kind}.local; it will still run first.")
                return 1
            if not ours and os.path.exists(path + ".local"):
                print(f"{path}.local already exists; resolve it by hand first.")
                return 1
    os.makedirs(hooks_dir, exist_ok=True)
    for kind in kinds:
        path = os.path.join(hooks_dir, kind)
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                ours = HOOK_MARK in fh.read()
            if not ours:                               # renamed after closing: Windows refuses open files
                os.replace(path, path + ".local")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(hook_script(kind, args.vendor, sys.executable, script))
        os.chmod(path, 0o755)
        print(f"installed {kind} hook -> {path}")
    print(hook_python_line(args.vendor))
    if not os.path.exists(os.path.join(repo.root, CONFIG_PATH)):
        print("optional: `aigate-lite init` writes .aigate/lite.toml to tune rules and add your test command")
    return 0


def cmd_uninstall(_args) -> int:
    repo = Repo.discover()
    hooks_dir = repo.git_path("hooks")
    for kind in ("pre-commit", "pre-push"):
        path = os.path.join(hooks_dir, kind)
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                ours = HOOK_MARK in fh.read()
            if ours:                                   # (file closed above, so Windows allows this)
                os.remove(path)
                if os.path.exists(path + ".local"):
                    os.replace(path + ".local", path)
                print(f"removed {kind} hook")
    return 0


LANG_EXT = {".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
            ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
            ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".fs": "F#", ".swift": "Swift", ".dart": "Dart"}
NPM_PLACEHOLDER = re.compile(r'echo "?Error: no test specified"?')
DEFAULT_REGISTRIES = ("https://pypi.org/", "https://pypi.python.org/", "https://registry.npmjs.org/")


@dataclasses.dataclass
class ProjectInfo:
    languages: list
    ecosystems: list
    test_commands: list
    private: list
    trusted_sources: list


def _read_small(root, rel, limit=1_000_000) -> str:
    try:
        with open(os.path.join(root, rel), "rb") as fh:
            return fh.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def _public_url(url: str):
    """A registry URL without credentials, query or fragment; None for the public default registries."""
    import urllib.parse
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    clean = urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))
    return None if clean.startswith(DEFAULT_REGISTRIES) else clean


def _registry_hints(root, files) -> tuple:
    private, trusted = [], []
    for rel in files:
        name = rel.rsplit("/", 1)[-1]
        if name == ".npmrc":
            for line in _read_small(root, rel).splitlines():
                m = re.match(r"\s*(@[\w.-]+):registry\s*=\s*(\S+)", line)
                if m:
                    private.append(f"{m.group(1)}/*")
                    trusted.append(_public_url(m.group(2)))
                else:
                    m = re.match(r"\s*registry\s*=\s*(\S+)", line)
                    trusted.append(_public_url(m.group(1)) if m else None)
        elif re.fullmatch(r"requirements[\w.-]*\.txt|constraints[\w.-]*\.txt", name):
            for line in _read_small(root, rel).splitlines():
                m = re.match(r"\s*(?:--(?:extra-)?index-url|-i)[=\s]+(\S+)", line)
                trusted.append(_public_url(m.group(1)) if m else None)
        elif name == "pyproject.toml":
            try:
                tool = tomllib.loads(_read_small(root, rel)).get("tool", {})
            except tomllib.TOMLDecodeError:
                continue
            uv = tool.get("uv", {}) if isinstance(tool.get("uv"), dict) else {}
            sources = [*(tool.get("poetry", {}) or {}).get("source", []), *uv.get("index", []),
                       *(tool.get("pdm", {}) or {}).get("source", [])]
            urls = [s.get("url") for s in sources if isinstance(s, dict)]
            urls += [uv.get("index-url"), *(uv.get("extra-index-url") or [])]
            trusted += [_public_url(u) for u in urls if isinstance(u, str)]
    return list(dict.fromkeys(p for p in private if p)), list(dict.fromkeys(t for t in trusted if t))


def detect_project(root, files) -> ProjectInfo:
    """Languages, package ecosystems, test commands and private-registry hints, from tracked files only."""
    names = {f.rsplit("/", 1)[-1] for f in files}

    def has(*wanted):
        return any(n in names for n in wanted)
    langs = list(dict.fromkeys(LANG_EXT[os.path.splitext(f)[1].lower()] for f in files
                               if os.path.splitext(f)[1].lower() in LANG_EXT))
    pyproject = _read_small(root, "pyproject.toml") if "pyproject.toml" in files else ""
    eco = []
    if has("poetry.lock") or "[tool.poetry" in pyproject:
        eco.append("poetry")
    if has("uv.lock"):
        eco.append("uv")
    if has("Pipfile", "Pipfile.lock"):
        eco.append("pipenv")
    if has("environment.yml", "environment.yaml"):
        eco.append("conda")
    if any(re.fullmatch(r"requirements[\w.-]*\.txt", n) for n in names) or has("setup.py", "setup.cfg") \
            or (pyproject and not eco):
        eco.append("pip")
    js = [e for e, marker in (("deno", ("deno.json", "deno.jsonc")), ("bun", ("bun.lockb", "bun.lock")),
                              ("pnpm", ("pnpm-lock.yaml",)), ("yarn", ("yarn.lock",)), ("npm", ("package-lock.json",)))
          if has(*marker)]
    if not js and has("package.json"):
        js = ["npm"]
    eco += js
    for e, marker in (("go", ("go.mod",)), ("cargo", ("Cargo.toml",)), ("maven", ("pom.xml",)),
                      ("gradle", ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")),
                      ("bundler", ("Gemfile",)), ("composer", ("composer.json",))):
        if has(*marker):
            eco.append(e)
    if any(n.endswith((".csproj", ".fsproj", ".sln")) for n in names):
        eco.append("dotnet")

    tests = []
    if has("pytest.ini", "conftest.py") or "[tool.pytest" in pyproject \
            or "[tool:pytest]" in (_read_small(root, "setup.cfg") if "setup.cfg" in files else "") \
            or "[pytest]" in (_read_small(root, "tox.ini") if "tox.ini" in files else ""):
        tests.append("python -m pytest -q")
    if "package.json" in files:
        try:
            pkg = json.loads(_read_small(root, "package.json") or "{}")
        except ValueError:
            pkg = {}
        script = ((pkg.get("scripts") or {}) if isinstance(pkg, dict) else {}).get("test")
        deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})} if isinstance(pkg, dict) else {}
        if isinstance(script, str) and script.strip() and not NPM_PLACEHOLDER.search(script):
            runner = next((e for e in js if e in ("npm", "yarn", "pnpm", "bun")), "npm")
            tests.append({"npm": "npm test", "yarn": "yarn test", "pnpm": "pnpm test", "bun": "bun run test"}[runner])
        elif "vitest" in deps:
            tests.append("npx vitest run")
        elif "jest" in deps:
            tests.append("npx jest")
    if "deno" in eco:
        tests.append("deno test")
    for e, cmd in (("go", "go test ./..."), ("cargo", "cargo test"), ("maven", "mvn -q test"),
                   ("gradle", "./gradlew test" if "gradlew" in files else "gradle test"), ("dotnet", "dotnet test")):
        if e in eco:
            tests.append(cmd)
    if "bundler" in eco and (any(f.endswith("_spec.rb") for f in files) or "rspec" in _read_small(root, "Gemfile")):
        tests.append("bundle exec rspec")
    if "composer" in eco and has("phpunit.xml", "phpunit.xml.dist"):
        tests.append("vendor/bin/phpunit")
    private, trusted = _registry_hints(root, files)
    return ProjectInfo(langs, eco, list(dict.fromkeys(tests)), private, trusted)


def init_text(preset: str, info: ProjectInfo) -> str:
    """The .aigate/lite.toml `init` writes: the preset, detected non-default keys, commented test commands."""
    lines = ["# aigate-lite configuration (written by `aigate-lite init`). Every key is optional; see",
             "# examples/lite.toml for all of them. Rule ids: `aigate-lite explain`.",
             "version = 1",
             f'preset = "{preset}"            # relaxed | balanced | strict']
    if info.private or info.trusted_sources:
        lines += ["", "[deps]"]
        if info.private:
            lines.append("private = [" + ", ".join(json.dumps(p) for p in info.private) + "]   # internal packages")
        if info.trusted_sources:
            lines.append("trusted_sources = [" + ", ".join(json.dumps(u) for u in info.trusted_sources) + "]")
    if info.test_commands:
        lines += ["", "# Detected test command(s). Uncomment to run them before every push. Commands in a repository's",
                  "# config run only after you approve them: aigate-lite trust"]
        for i, cmd in enumerate(info.test_commands):
            lines += ["# [[commands]]", f'# name = "{"tests" if i == 0 else f"tests-{i + 1}"}"',
                      f"# run = {json.dumps(cmd)}", '# stage = "push"']
    return "\n".join(lines) + "\n"


def cmd_init(args) -> int:
    repo = Repo.discover()
    path = os.path.join(repo.root, CONFIG_PATH)
    files = [f for f in run_git(repo.root, "ls-files", "-z").split("\0") if f]
    info = detect_project(repo.root, files)
    what = []
    if info.languages:
        what.append(", ".join(info.languages))
    if info.ecosystems:
        what.append("packages: " + ", ".join(info.ecosystems))
    if info.test_commands:
        what.append("tests: " + "; ".join(info.test_commands))
    print("Detected: " + ("; ".join(what) if what else "nothing (no tracked project files)"))
    print(f"Preset: {args.preset}")
    text = init_text(args.preset, info)
    if args.print:
        print(text, end="")
        return 0
    if os.path.exists(path) and not args.force:
        print(f"{CONFIG_PATH} already exists; to replace it run: aigate-lite init --force (keeps a .bak copy)")
        return 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    backup = ""
    if os.path.exists(path):
        os.replace(path, path + ".bak")
        backup = f" (previous file kept as {CONFIG_PATH}.bak)"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"Wrote {CONFIG_PATH}{backup}")
    print("next: aigate-lite doctor")
    return 0


def cmd_baseline(args) -> int:
    """Write .aigate/baseline.json: the findings a full offline scan reports today, so an existing project can
    adopt the gate and only new findings stop commits. Never run for an AI agent."""
    env = dict(os.environ)
    repo = Repo.discover()
    cfg = load_config(repo, "worktree")
    kind, why = detect_provenance(env, cfg["provenance"]["agent_env"], cfg["provenance"]["default"])
    if kind == "agent":
        print(f"aigate-lite: baseline refuses to run for an AI agent ({why.lstrip('$')}): accepting existing findings "
              "is a person's decision", file=sys.stderr)
        return 1
    ns = build_parser().parse_args(["check", "--all", "--offline", "--no-commands", "--format", "json"])
    res = run_check(ns, env, collect=True)
    found = [] if isinstance(res, int) else [f for f in res.findings if f.allowed in (None, "baseline")
                                              and not f.rule.startswith(BASELINE_NEVER)]
    secrets = [f for f in found if f.rule.startswith("secrets.")]
    current = {(f.rule, f.path, f.fingerprint) for f in found
               if args.include_secrets or not f.rule.startswith("secrets.")}
    old, _problems = load_baseline(repo, "worktree")
    entries = current if args.prune else old | current
    rows = [{"rule": r, "path": p, "fingerprint": fp} for r, p, fp in sorted(entries, key=lambda e: (e[1], e[0], e[2]))]
    path = os.path.join(repo.root, BASELINE_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"version": 1, "tool": __version__, "entries": rows}, indent=2) + "\n")
    removed = f", {len(old - entries)} removed" if args.prune else ""
    print(f"Wrote {BASELINE_PATH}: {len(rows)} entries ({len(current - old)} new{removed})")
    left = 0 if args.include_secrets else len(secrets)
    print(f"{left} secret finding{'' if left == 1 else 's'} not baselined; rotate {'it' if left == 1 else 'them'}"
          if left else "0 secret findings not baselined")
    print(f"next: review and commit {BASELINE_PATH}")
    return 0


FIXABLE = ("secrets.env_file", "code.debug_leftover", "tests.focused")
DEBUG_MODULES = ("pdb", "ipdb", "pudb")
JS_DEBUGGER_LINE = re.compile(r"^\s*debugger\s*;?\s*$")
FOCUS_FIXES = ((re.compile(r"^(\s*)f(it|describe|test)\("), r"\1\2("),
               (re.compile(r"^(\s*)(it|describe|test)\.only\("), r"\1\2("))


@dataclasses.dataclass
class FixPlan:
    edits: dict          # path -> (old bytes, new bytes)
    untrack: list        # paths to `git rm --cached`
    gitignore: tuple     # (old bytes, new bytes) or None
    skipped: list        # (path, line, why)
    notes: list = dataclasses.field(default_factory=list)


def _unchanged_since_staged(repo, path) -> bool:
    """True when the work tree file matches the index (git's own comparison: autocrlf and filters included)."""
    return subprocess.run(["git", "diff", "--quiet", "--no-ext-diff", "--", f":(literal){path}"],
                          cwd=repo.root, capture_output=True).returncode == 0


def _python_debug_lines(text) -> set:
    """1-based lines whose statements are all one-line debugger calls or debugger imports (so deleting the line
    removes exactly those statements and nothing else)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    by_line: dict = defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            by_line[node.lineno].append(node)

    def debug_stmt(n) -> bool:
        if n.lineno != n.end_lineno:
            return False
        if isinstance(n, ast.Import):
            return all(a.name in DEBUG_MODULES and not a.asname for a in n.names)
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and not n.value.args and not n.value.keywords:
            name = dotted_name(n.value.func) or ""
            return name == "breakpoint" or name in {f"{m}.set_trace" for m in DEBUG_MODULES}
        return False
    return {line for line, stmts in by_line.items() if all(debug_stmt(n) for n in stmts)}


def _plan_file(repo, path, findings, plan) -> None:
    full = os.path.join(repo.root, path)
    root = os.path.realpath(repo.root)
    if os.path.islink(full) or not os.path.realpath(full).startswith(root + os.sep):
        plan.skipped.append((path, None, "a symlink or outside the repository"))
        return
    if not _unchanged_since_staged(repo, path):
        plan.skipped.append((path, None, "has unstaged changes; stage or stash them first"))
        return
    try:
        with open(full, "rb") as fh:
            data = fh.read()
        text = data.decode("utf-8")
    except OSError as e:
        plan.skipped.append((path, None, f"cannot read it ({e.strerror})"))
        return
    except UnicodeDecodeError:
        plan.skipped.append((path, None, "not UTF-8"))
        return
    lines = text.split("\n")                       # like git's line numbers: only \n ends a line
    is_py = path.endswith((".py", ".pyw"))
    debug_ok = _python_debug_lines(text) if is_py else set()
    drop, changed = set(), {}
    for f in findings:
        i = (f.line or 0) - 1
        if not 0 <= i < len(lines):
            continue
        body = lines[i].rstrip("\r")
        if f.rule == "tests.focused":
            for rx, repl in FOCUS_FIXES:
                new = rx.sub(repl, body, count=1)
                if new != body:
                    changed[i] = new + lines[i][len(body):]
                    break
            else:
                plan.skipped.append((path, f.line, "the focused call is not at the start of the line; fix it by hand"))
        elif is_py and (i + 1) in debug_ok:
            drop.add(i)
        elif not is_py and JS_DEBUGGER_LINE.match(body) and "\n".join(lines[:i]).count("`") % 2 == 0:
            drop.add(i)                             # (an odd number of backticks before it: inside a template)
        else:
            plan.skipped.append((path, f.line, "other code on the line or a multi-line statement; fix it by hand"))
    for i in [i for i in drop if is_py and lines[i].strip().startswith("import ")]:
        module = lines[i].split()[1].rstrip(";")      # keep `import pdb` while something else still uses pdb
        rest = "\n".join(changed.get(j, ln) for j, ln in enumerate(lines) if j not in drop)
        if re.search(rf"\b{re.escape(module)}\b", rest):
            drop.discard(i)
    new_text = "\n".join(changed.get(i, ln) for i, ln in enumerate(lines) if i not in drop)
    if is_py:
        try:
            ast.parse(new_text)
        except SyntaxError:
            plan.skipped.append((path, None, "removing the line would leave an empty block; fix it by hand"))
            return
    if new_text != text:
        plan.edits[path] = (data, new_text.encode("utf-8"))


def _gitignore_line(path: str) -> str:
    """An anchored .gitignore pattern that matches exactly `path` (special characters escaped)."""
    escaped = re.sub(r"([\\[\]*?!#])", r"\\\1", path)
    stripped = escaped.rstrip(" ")
    return "/" + stripped + "\\ " * (len(escaped) - len(stripped))


def _covers(repo, line: str, path: str) -> bool:
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix="aigate-ignore-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
        return subprocess.run(["git", "-c", f"core.excludesFile={tmp}", "check-ignore", "-q", "--no-index", "--",
                               path], cwd=repo.root, capture_output=True).returncode == 0
    finally:
        os.remove(tmp)


def _plan_gitignore(repo, paths, plan) -> None:
    def ignored(p) -> bool:                    # --no-index: would .gitignore cover it once it is untracked?
        return subprocess.run(["git", "check-ignore", "-q", "--no-index", "--", p], cwd=repo.root,
                              capture_output=True).returncode == 0
    add = [p for p in paths if not ignored(p)]
    if not add:
        return
    gi = os.path.join(repo.root, ".gitignore")
    if os.path.islink(gi):
        plan.skipped.append((".gitignore", None, "is a symlink; add the lines by hand"))
        return
    if os.path.exists(gi) and not _unchanged_since_staged(repo, ".gitignore"):
        plan.skipped.append((".gitignore", None, "has unstaged changes; stage or stash them first"))
        return
    try:
        with open(gi, "rb") as fh:
            old = fh.read()
    except OSError:
        old = b""
    eol = b"\r\n" if b"\r\n" in old else b"\n"
    present = {ln.strip() for ln in old.splitlines()}
    lines = []
    for p in add:
        line = _gitignore_line(p)
        if line.encode() in present:
            continue
        if _covers(repo, line, p):
            lines.append(line.encode())
        else:
            plan.skipped.append((p, None, "cannot be written as a .gitignore line; add it to .gitignore by hand"))
    if lines:
        plan.gitignore = (old, old + (eol if old and not old.endswith(b"\n") else b"") + eol.join(lines) + eol)


def plan_fixes(repo, findings, only) -> FixPlan:
    """What `fix` would change, from the staged check's findings. Nothing is written here."""
    wanted = set(only or FIXABLE) & set(FIXABLE)
    plan = FixPlan({}, [], None, [])
    by_path: dict = defaultdict(list)
    for f in findings:
        if f.rule in wanted and not f.allowed and f.action in ("block", "fix"):
            if f.rule == "secrets.env_file":
                plan.untrack.append(f.path)
            else:
                by_path[f.path].append(f)
    for path, fs in sorted(by_path.items()):
        _plan_file(repo, path, fs, plan)
    plan.untrack = list(dict.fromkeys(plan.untrack))
    _plan_gitignore(repo, plan.untrack, plan)
    for p in plan.untrack:
        if repo.read("HEAD:" + p) is not None:
            plan.notes.append(f"{p} is already in HEAD: this commit will delete it from the repository (the file stays "
                              "on disk); it stays in history, so rotate what it contains")
    return plan


def _diff(path, old: bytes, new: bytes) -> str:
    import difflib
    a, b = old.decode("utf-8", "replace").splitlines(True), new.decode("utf-8", "replace").splitlines(True)
    return "".join(ln if ln.endswith("\n") else ln + "\n"
                   for ln in difflib.unified_diff(a, b, fromfile=f"a/{path}", tofile=f"b/{path}"))


def _apply_fixes(repo, plan) -> None:
    """Write every planned edit, or none: all files are opened (and re-checked against the plan) first."""
    writes = [(path, old, new) for path, (old, new) in plan.edits.items()]
    if plan.gitignore:
        writes.append((".gitignore", *plan.gitignore))
    handles, done = [], []
    try:
        for path, old, new in writes:
            full = os.path.join(repo.root, path)
            fh = open(full, "r+b") if os.path.exists(full) else open(full, "w+b")
            handles.append((path, fh, new))
            if fh.read() != old:
                raise UserError(f"{path} changed while fix was running", "run: aigate-lite fix --yes again")
        for path, fh, new in handles:
            fh.seek(0)
            fh.write(new)                          # in place: keeps the file mode
            fh.truncate()
            done.append(path)
    except OSError as e:
        raise UserError(f"cannot write {e.filename or 'a file'} ({e.strerror})",
                        "make it writable and run: aigate-lite fix --yes again") from None
    finally:
        for _path, fh, _new in handles:
            fh.close()
        if done:
            run_git(repo.root, "add", "--", *(f":(literal){p}" for p in done))
    for path in plan.untrack:
        run_git(repo.root, "rm", "--cached", "-q", "--", f":(literal){path}")


def cmd_fix(args) -> int:
    """Mechanical fixes for staged findings. Shows the diff and changes nothing unless --yes; never run by hooks."""
    repo = Repo.discover()
    env = dict(os.environ)
    ns = build_parser().parse_args(["check", "--staged", "--offline", "--no-commands", "--format", "json"])
    res = run_check(ns, env, collect=True)
    findings = [] if isinstance(res, int) else res.findings
    only = [r.strip() for r in (args.only or "").split(",") if r.strip()]
    plan = plan_fixes(repo, findings, only)
    for path, line, why in plan.skipped:
        print(f"skipped {path}{f':{line}' if line else ''}: {why}")
    if not (plan.edits or plan.untrack or plan.gitignore):
        print("nothing to fix" + (" automatically" if plan.skipped else ""))
        return 0
    if not args.yes:
        for path in plan.untrack:
            print(f"would run: git rm --cached -- {path}   (the file stays on disk)")
        for note in plan.notes:
            print(f"note: {note}")
        if plan.gitignore:
            sys.stdout.write(_diff(".gitignore", *plan.gitignore))
        for path, (old, new) in plan.edits.items():
            sys.stdout.write(_diff(path, old, new))
        print("next: aigate-lite fix --yes")
        return 1
    _apply_fixes(repo, plan)
    for note in plan.notes:
        print(f"note: {note}")
    print(f"fixed: {len(plan.edits)} file(s) edited, {len(plan.untrack)} unstaged"
          + (", .gitignore updated" if plan.gitignore else "") + "; checking again:")
    recheck = build_parser().parse_args(["check", "--staged", "--offline", "--no-commands", "--color", "never"])
    return run_check(recheck, env)


def cmd_scope(args) -> int:
    repo = Repo.discover()
    path = scope_path(repo)
    current = load_scope(repo) if not os.environ.get("AIGATE_SCOPE") else []
    if args.action == "show":
        print("\n".join(current) if current else "no scope set: every path is in scope")
        return 0
    if args.action == "clear":
        if os.path.exists(path):
            os.remove(path)
        print("scope cleared")
        return 0
    if not args.globs:
        print("give one or more globs, e.g. aigate-lite scope set 'src/auth/**' 'tests/auth/**'")
        return 2
    globs = (current if args.action == "add" else []) + [g for g in args.globs if g not in current or args.action == "set"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# files this task may touch (one glob per line); aigate-lite flags the rest\n" + "\n".join(globs) + "\n")
    print(f"scope: {', '.join(globs)}")
    return 0


def _visible(value) -> str:
    """Text as it really is: control characters (which could hide or rewrite what the terminal shows) are
    escaped, and the whole value is printed however long."""
    text = str(value)
    return text if text.isprintable() else repr(text)


def cmd_trust(args) -> int:
    """Approve the [[commands]] in .aigate/lite.toml (the working tree's, or --rev's): exactly this list."""
    repo = Repo.discover()
    agent = detect_provenance(dict(os.environ))
    if agent[0] == "agent":
        print(f"aigate-lite: refusing to trust commands for an AI agent (detected via {agent[1]}); "
              "a person has to run `aigate-lite trust`", file=sys.stderr)
        return 1
    if args.rev:
        rev = repo.commit(args.rev)
        if not rev:
            raise GitError(f"cannot resolve {args.rev!r}")
        cfg = load_config(repo, rev)
    else:
        cfg = load_config(repo, "worktree")
    for problem in cfg.problems:
        print(f"warning: {problem}")
    commands = cfg["commands"]
    if not commands:
        print(f"no [[commands]] in {cfg.origin}: nothing to trust")
        return 0
    print(f"Approving every command below from {cfg.origin}. They run on this machine through the shell:")
    for c in commands:
        print(f"  - name: {_visible(c['name'])}\n    run: {_visible(c['run'])}\n    stage: {c['stage']}  "
              f"action: {c['action']}  timeout: {c['timeout']}s  when: {_visible(c['when'] or 'always')}")
    path = repo.private_path("aigate/trusted")
    known = trusted_hashes(repo)
    digest = commands_hash(commands)
    if digest not in known:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _write_private(path, "".join(f"{h}\n" for h in sorted(known) + [digest]).encode("utf-8"))
        except OSError as e:
            print(f"aigate-lite: cannot record the approval in {path}: {e}", file=sys.stderr)
            return 2
    print(f"trusted (recorded in {path}); any change to these commands needs a new `aigate-lite trust`")
    return 0


def cmd_explain(args) -> int:
    if not args.rule:
        print(f"{'rule':<30} {'default':<8} {'severity':<9} what it catches")
        for r in RULES.values():
            print(f"{r.id:<30} {r.action:<8} {r.severity:<9} {r.title}")
        return 0
    r = RULES.get(args.rule)
    if r is None:
        print(f"unknown rule {args.rule!r}; run `aigate-lite explain` for the list")
        return 2
    allow = (f'[[allow]]\nrule = "{r.id}"\npath = "<glob>"            # or fingerprint = "<from -v output>"\n'
             f'reason = "<why this is safe>"')
    print(f"{r.id}: {r.title}\n  default action: {r.action}   severity: {r.severity}   confidence: {r.confidence}"
          f"{'   stricter in sensitive paths' if r.escalate else ''}\n  fix: {r.hint}\n\n"
          f"Change it for the whole repo in .aigate/lite.toml:\n  [rules]\n  \"{r.id}\" = \"warn\"   # or off\n\n"
          f"Allow one known false positive:\n  " + allow.replace("\n", "\n  ") +
          ("" if r.action == "block" else f"\n\nOr allow it for a single commit (not available to AI agents):\n"
           f"  AIGATE_ALLOW={r.id} git commit ..."))
    return 0


# --------------------------------------------------------------------------- doctor

# `git rev-parse --absolute-git-dir` (Repo.discover) is the newest git feature the tool relies on: added by
# git commit a2f5a8762693, first released in v2.13.0. Every other command and flag it runs is older.
GIT_MIN = (2, 13, 0)
DOCTOR_URLS = (("PyPI", "https://pypi.org/pypi/pip/json"),
               ("npm", "https://registry.npmjs.org/-/package/react/dist-tags"),
               ("OSV", "https://api.osv.dev/v1/query"),
               ("npm downloads", "https://api.npmjs.org/downloads/point/last-week/react"),
               ("pypistats", "https://pypistats.org/api/packages/pip/recent"))
DOCTOR_SNIPPETS = {
    "husky": "add this line to .husky/pre-commit: python3 .aigate/aigate_lite.py check --staged",
    "lefthook": "add to lefthook.yml: pre-commit: { commands: { aigate-lite: { run: python3 .aigate/aigate_lite.py "
                "check --staged } } }",
}


def parse_git_version(text: str):
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def _check(cid, status, detail, fix=""):
    return {"id": cid, "status": status, "detail": detail, "fix": fix}


def _git_text(cwd, *args):
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _doctor_python(env, hooks_dir):
    out = [_check("python", "ok", f"running Python {sys.version.split()[0]} at {sys.executable}")]
    pinned = vendored = None
    try:
        with open(os.path.join(hooks_dir, "pre-commit"), encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if HOOK_MARK in text:
            m = re.search(r"^PY='((?:[^']|'\\'')*)'$", text, re.M)
            pinned = m.group(1).replace("'\\''", "'") if m else None
            vendored = m is None
    except OSError:
        pass
    choice = discover_python(env, pinned=pinned, exists=lambda p: os.access(p, os.X_OK))
    label = "vendored hook" if vendored else "hook" if pinned else "a hook installed now"
    if not choice.command:
        tried = "; ".join(f"{c}: {r}" for c, r in choice.tried)
        out.append(_check("python.hook", "fail", f"{label} would find no Python 3.11+ ({tried})",
                          "install Python 3.11+ or set AIGATE_PYTHON=/path/to/python3"))
    elif choice.pinned_missing:
        out.append(_check("python.hook", "warn", f"pinned interpreter {pinned} is missing; {label} falls back to "
                          f"{choice.reason}", "run: aigate-lite install (pins the current Python)"))
    else:
        out.append(_check("python.hook", "ok", f"{label} uses {choice.reason}"))
    return out


def _doctor_git(cwd):
    text = _git_text(cwd, "--version")
    if text is None:
        return _check("git", "fail", "git not found on PATH", "install git and make sure `git --version` works")
    version = parse_git_version(text)
    if version is None or version < GIT_MIN:
        need = ".".join(map(str, GIT_MIN[:2]))
        return _check("git", "warn", f"{text}: older than {need} (needed for `git rev-parse --absolute-git-dir`)",
                      f"upgrade git to {need} or newer")
    return _check("git", "ok", text)


def _doctor_hooks(repo, hooks_dir):
    out = []
    for kind, missing in (("pre-commit", "fail"), ("pre-push", "warn")):
        path = os.path.join(hooks_dir, kind)
        if not os.path.exists(path):
            out.append((missing, f"{kind} hook not installed", "run: aigate-lite install"))
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if HOOK_MARK not in text:
            out.append((missing, f"{kind} hook is not aigate-lite's",
                        f"run: aigate-lite install --force (keeps the current one as {kind}.local)"))
            continue
        if os.name != "nt" and not os.access(path, os.X_OK):
            out.append(("fail", f"{kind} hook is not executable", f"run: chmod +x {path}"))
            continue
        m = re.search(r"^SCRIPT='((?:[^']|'\\'')*)'$", text, re.M)
        script = m.group(1).replace("'\\''", "'") if m else os.path.join(repo.root, ".aigate", "aigate_lite.py")
        if not os.path.isfile(script):
            out.append(("fail", f"{kind} hook runs {script}, which is missing", "run: aigate-lite install"))
            continue
        chained = " (runs your old hook first: " + kind + ".local)" if os.path.exists(path + ".local") else ""
        out.append(("ok", f"{kind}{chained}", ""))
    worst = next((s for s in ("fail", "warn") if any(o[0] == s for o in out)), "ok")
    bad = [o for o in out if o[0] != "ok"]
    detail = "; ".join(o[1] for o in bad) if bad else "pre-commit and pre-push installed: " + ", ".join(o[1] for o in out)
    fix = next((o[2] for o in bad if o[2]), "")
    return _check("hooks", worst, detail, fix)


def _doctor_hook_manager(repo):
    hooks_path = _git_text(repo.root, "config", "--get", "core.hooksPath")
    root = repo.root
    if os.path.exists(os.path.join(root, ".pre-commit-config.yaml")):
        return _check("hooks.manager", "warn", "the pre-commit framework manages hooks (.pre-commit-config.yaml)",
                      "add under `repos:` in .pre-commit-config.yaml:\n" + PRE_COMMIT_SNIPPET)
    if hooks_path or os.path.isdir(os.path.join(root, ".husky")):
        which = "husky" if ".husky" in (hooks_path or ".husky") else None
        return _check("hooks.manager", "warn", f"core.hooksPath is {hooks_path or 'unset'}"
                      f"{' (husky)' if which else ''}: git runs hooks from there, not .git/hooks",
                      DOCTOR_SNIPPETS["husky"] if which else
                      f"add `python3 .aigate/aigate_lite.py check --staged` to {hooks_path}/pre-commit")
    if any(os.path.exists(os.path.join(root, n)) for n in ("lefthook.yml", "lefthook.yaml", ".lefthook.yml")):
        return _check("hooks.manager", "warn", "lefthook manages hooks", DOCTOR_SNIPPETS["lefthook"])
    return _check("hooks.manager", "ok", "no other hook manager found")


def _doctor_version(repo):
    path = os.path.join(repo.root, ".aigate", "aigate_lite.py")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            m = re.search(r'^__version__ = "([^"]+)"', fh.read(), re.M)
    except OSError:
        return _check("version", "ok", f"aigate-lite {__version__} (not vendored)")
    vendored = m.group(1) if m else "unknown"
    if vendored != __version__:
        return _check("version", "warn", f"vendored .aigate/aigate_lite.py is {vendored}, this is {__version__}",
                      "run: aigate-lite install --vendor (then commit .aigate/aigate_lite.py)")
    return _check("version", "ok", f"aigate-lite {__version__}, vendored copy matches")


def _doctor_config(cfg):
    if any("not valid TOML" in p or p.startswith("cannot read") for p in cfg.problems):
        return _check("config", "fail", "; ".join(cfg.problems), f"fix {CONFIG_PATH} (the gate uses built-in defaults)")
    if cfg.problems:
        return _check("config", "warn", "; ".join(cfg.problems), f"fix the entries named above in {CONFIG_PATH}")
    legacy = [a for a in cfg["allow"] if re.fullmatch(r"[0-9a-f]{12}", a.get("fingerprint") or "")]
    if legacy:
        return _check("config", "warn", f"{len(legacy)} [[allow]] entr{'y uses' if len(legacy) == 1 else 'ies use'} 0.1 "
                      "fingerprints (12 characters); they still match during 0.2.x",
                      "run: aigate-lite check --all -v, and copy each finding's new fingerprint into its entry")
    return _check("config", "ok", f"{cfg.origin}: no problems")


def _doctor_baseline(repo):
    if not os.path.exists(os.path.join(repo.root, BASELINE_PATH)):
        return _check("baseline", "ok", "no baseline (every finding counts)")
    entries, problems = load_baseline(repo, "worktree")
    if problems:
        return _check("baseline", "warn", "; ".join(problems), "run: aigate-lite baseline --prune")
    legacy = sum(1 for e in entries if re.fullmatch(r"[0-9a-f]{12}", e[2]))
    if legacy:
        return _check("baseline", "warn", f"{len(entries)} entries, {legacy} with 0.1 fingerprints",
                      "run: aigate-lite baseline --prune (rewrites them in the 0.2 format)")
    return _check("baseline", "ok", f"{len(entries)} accepted findings in {BASELINE_PATH}")


def _doctor_commands(repo, cfg):
    cmds = cfg["commands"]
    if not cmds:
        return _check("commands", "ok", "no [[commands]] configured")
    if commands_hash(cmds) in trusted_hashes(repo):
        return _check("commands", "ok", f"{len(cmds)} configured command(s), approved")
    names = ", ".join(c["name"] for c in cmds)
    return _check("commands", "warn", f"[[commands]] not approved yet, so they do not run: {names}",
                  "review them, then run: aigate-lite trust")


def _doctor_cache(cfg):
    path = default_cache_dir()
    state = "result cache on" if cfg["gate"].get("cache") is True else "result cache off (enable: --cache)"
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, f".doctor-{os.getpid()}")
        with open(probe, "w") as fh:
            fh.write("")
        os.remove(probe)
    except OSError as e:
        return _check("cache", "warn", f"{state}; cache dir {path} is not writable ({e.strerror})",
                      "set AIGATE_CACHE_DIR to a writable directory")
    size, loose = 0, []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if os.path.isfile(full):
            size += os.path.getsize(full)
            if os.name != "nt" and name.endswith((".json", ".key")) and os.stat(full).st_mode & 0o077:
                loose.append(name)
    detail = f"{state}; {path} ({size // 1024} KB)"
    if loose:
        return _check("cache", "warn", f"{detail}; readable by others: {', '.join(loose)}",
                      f"run: chmod 600 {path}/*.json {path}/*.key")
    return _check("cache", "ok", detail)


def _redact_proxy(value: str) -> str:
    return re.sub(r"//[^/@]*@", "//", value)


def _default_fetch(url, timeout):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": f"aigate-lite/{__version__} doctor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def _doctor_network(env, cfg, fetch):
    import ssl
    import urllib.error
    from concurrent.futures import ThreadPoolExecutor
    proxy = next((env[k] for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy") if env.get(k)), None)
    via = f" via proxy {_redact_proxy(proxy)}" if proxy else ""

    def one(item):
        name, url = item
        try:
            fetch(url, 3)
            return name, None
        except urllib.error.URLError as e:
            return name, "tls" if isinstance(e.reason, ssl.SSLError) else str(e.reason)
        except ssl.SSLError:
            return name, "tls"
        except (OSError, ValueError) as e:
            return name, str(e) or type(e).__name__
    with ThreadPoolExecutor(max_workers=len(DOCTOR_URLS)) as pool:
        results = list(pool.map(one, DOCTOR_URLS))
    failed = [(n, why) for n, why in results if why]
    if not failed:
        return _check("network", "ok", f"PyPI, npm, OSV, npm downloads and pypistats reachable{via}")
    status = "fail" if cfg["gate"].get("offline") == "block" else "warn"
    names = ", ".join(n for n, _ in failed)
    if any(why == "tls" for _, why in failed):
        return _check("network", status, f"TLS certificate verification failed for {names}{via}",
                      "behind a corporate proxy? point SSL_CERT_FILE at your company's CA bundle "
                      "(SSL_CERT_FILE=/path/to/ca.pem)")
    return _check("network", status, f"not reachable: {names}{via} ({failed[0][1]}); dependency checks will be "
                  "incomplete", "check your connection or proxy settings (HTTPS_PROXY), or use --offline")


def _doctor_agent(env, cfg):
    kind, why = detect_provenance(env, cfg["provenance"]["agent_env"], cfg["provenance"]["default"])
    if kind == "agent":
        return _check("agent", "ok", f"AI agent detected via {why.lstrip('$')}: commits from this shell count as "
                      "AI-agent commits")
    return _check("agent", "ok", "no AI agent detected in this shell: commits count as human commits")


def doctor_report(env, offline=False, fetch=None) -> list:
    """Every doctor check as {id, status: ok|warn|fail, detail, fix}, sorted fail -> warn -> ok."""
    cwd = os.getcwd()
    checks = [_doctor_git(cwd)]
    try:
        repo = Repo.discover(cwd)
    except GitError as e:
        repo = None
        checks.append(_check("repo", "fail", f"not inside a git work tree ({e})",
                             "run doctor inside your project (or run: git init)"))
    if repo is not None:
        hooks_dir = repo.git_path("hooks")
        checks += _doctor_python(env, hooks_dir)
        shallow = _git_text(repo.root, "rev-parse", "--is-shallow-repository") == "true"
        has_commits = repo.commit("HEAD") is not None
        if shallow:
            checks.append(_check("repo", "warn", f"{repo.root}: shallow clone; --range and --push checks may miss "
                                 "their base", "run: git fetch --unshallow (CI: fetch-depth: 0)"))
        elif not has_commits:
            checks.append(_check("repo", "warn", f"{repo.root}: no commits yet", "make a first commit"))
        else:
            checks.append(_check("repo", "ok", repo.root))
        cfg = load_config(repo, "worktree")
        checks += [_doctor_hooks(repo, hooks_dir), _doctor_hook_manager(repo), _doctor_version(repo),
                   _doctor_config(cfg), _doctor_commands(repo, cfg), _doctor_cache(cfg), _doctor_baseline(repo)]
        scope = load_scope(repo)
        checks.append(_check("scope", "ok", "task scope: " + ", ".join(scope) if scope else
                             "no task scope set (every path is in scope)"))
    else:
        checks += _doctor_python(env, os.path.join(cwd, ".git", "hooks"))
        cfg = merge_config({}, [])
    checks.append(_check("network", "ok", "skipped (--offline)") if offline
                  else _doctor_network(env, cfg, fetch or _default_fetch))
    checks.append(_doctor_agent(env, cfg))
    return sorted(checks, key=lambda c: ("fail", "warn", "ok").index(c["status"]))


def cmd_doctor(args) -> int:
    checks = doctor_report(dict(os.environ), offline=args.offline)
    if args.format == "json":
        print(json.dumps(checks, indent=2))
    else:
        sym = symbols(sys.stdout)
        width = max(len(c["id"]) for c in checks)
        pad = " " * (width + 5)
        for c in checks:
            print(f"{sym[c['status']]:<2} {c['id']:<{width}}  {c['detail']}")
            if c["status"] != "ok" and c["fix"]:
                print(pad + sym["arrow"] + " " + c["fix"].replace("\n", "\n" + pad + "  "))
        first = next((c for c in checks if c["status"] != "ok" and c["fix"]), None)
        hint = first["fix"].splitlines()[0].removeprefix("run: ") if first else "all good; commit as usual"
        print(f"next: {hint}")
    return 1 if any(c["status"] == "fail" for c in checks) else 0


def build_parser():
    import argparse

    class Parser(argparse.ArgumentParser):
        def error(self, message):
            self.print_usage(sys.stderr)
            self.exit(2, f"aigate-lite: {message}\n  see: aigate-lite --help\n")
    p = Parser(prog="aigate-lite", description="Commit-time gate for AI-written code. "
                                               "--debug (or AIGATE_DEBUG=1) shows tracebacks.")
    p.add_argument("--version", action="version", version=f"aigate-lite {__version__}")
    sub = p.add_subparsers(dest="command", parser_class=Parser)
    c = sub.add_parser("check", help="check staged changes, a range, or a push")
    target = c.add_mutually_exclusive_group()
    target.add_argument("--staged", action="store_true", help="check what `git commit` would record (default)")
    target.add_argument("--range", metavar="BASE..HEAD", help="check commits in a range, e.g. origin/main..HEAD")
    target.add_argument("--push", action="store_true", help="pre-push hook mode: read refs from stdin")
    target.add_argument("--all", action="store_true",
                        help="scan every tracked file in HEAD as if newly added (an audit; not for hooks)")
    c.add_argument("--remote", default=None, help=argparse.SUPPRESS)
    c.add_argument("--format", choices=("text", "json", "github", "sarif"), default="text")
    c.add_argument("--json", dest="format", action="store_const", const="json", help="same as --format json")
    c.add_argument("--offline", action="store_true", help="skip registry lookups")
    c.add_argument("--cache", action="store_true",
                   help="use the result cache (remembers files found clean; off unless [gate] cache = true)")
    c.add_argument("--no-cache", action="store_true", help="ignore the local lookup and result caches")
    c.add_argument("--no-commands", action="store_true", help="skip [[commands]] from the config")
    c.add_argument("--config", help="use this config file instead of .aigate/lite.toml")
    c.add_argument("-q", "--quiet", action="store_true", help="print only problems that stop the commit")
    c.add_argument("-v", "--verbose", action="store_true", help="show hints, fingerprints and allowed findings")
    c.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    i = sub.add_parser("install", help="install the pre-commit and pre-push git hooks")
    i.add_argument("--force", action="store_true", help="install even if other hooks exist (they keep running)")
    i.add_argument("--vendor", action="store_true", help="copy the script into .aigate/ and point the hooks there")
    i.add_argument("--no-pre-push", action="store_true", help="install only the pre-commit hook")
    sub.add_parser("uninstall", help="remove the hooks this tool installed")
    n = sub.add_parser("init", help="detect the project and write .aigate/lite.toml")
    n.add_argument("--preset", choices=PRESET_NAMES, default="balanced")
    n.add_argument("--print", action="store_true", help="show the file instead of writing it")
    n.add_argument("--force", action="store_true", help="replace an existing file (kept as lite.toml.bak)")
    s = sub.add_parser("scope", help="declare which paths the current task may touch")
    s.add_argument("action", choices=("set", "add", "show", "clear"))
    s.add_argument("globs", nargs="*")
    t = sub.add_parser("trust", help="approve the [[commands]] in .aigate/lite.toml so they may run")
    t.add_argument("--rev", help="approve the config as committed in this revision (e.g. a branch you push)")
    x = sub.add_parser("fix", help="fix debugger lines, focused tests and staged env files (shows a diff; --yes applies)")
    x.add_argument("--yes", action="store_true", help="apply the changes and stage them")
    x.add_argument("--only", metavar="RULE[,RULE]", help=f"only these rules ({', '.join(FIXABLE)})")
    b = sub.add_parser("baseline", help="accept today's findings so only new ones stop commits (.aigate/baseline.json)")
    b.add_argument("--include-secrets", action="store_true", help="also accept secrets (rotate them instead)")
    b.add_argument("--prune", action="store_true", help="drop entries that no longer occur")
    d = sub.add_parser("doctor", help="check this setup: Python, git, hooks, config, cache, network")
    d.add_argument("--offline", action="store_true", help="skip the network checks")
    d.add_argument("--format", choices=("text", "json"), default="text")
    e = sub.add_parser("explain", help="list rules, or explain one")
    e.add_argument("rule", nargs="?")
    return p


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # A pipe on Windows uses the ANSI code page (cp1252), which cannot encode the → and · the text
        # report prints; replace those characters instead of crashing.
        if (getattr(stream, "encoding", None) or "").lower().replace("-", "") not in ("utf8", "utf8sig") \
                and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    debug = "--debug" in argv or os.environ.get("AIGATE_DEBUG") == "1"
    argv = [a for a in argv if a != "--debug"]
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version"):
        argv = ["check", *argv]
    command = None
    try:
        args = build_parser().parse_args(argv)
        command = args.command
        if args.command == "check":
            return run_check(args, dict(os.environ))
        return {"install": cmd_install, "uninstall": cmd_uninstall, "init": cmd_init, "scope": cmd_scope,
                "explain": cmd_explain, "trust": cmd_trust,
                "doctor": cmd_doctor, "baseline": cmd_baseline,
                "fix": cmd_fix}[args.command](args)
    except UserError as e:
        if debug:
            raise
        print(f"aigate-lite: {e.cause}" + (f"\n  fix: {e.fix}" if e.fix else ""), file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:                   # the reader went away (e.g. `| head`): nothing to report
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        return 2
    except Exception as e:                    # fail closed: exit 2 stops the hook, without a traceback
        if debug:
            raise
        bypass = ""
        if command == "check" and detect_provenance(dict(os.environ))[0] != "agent":
            bypass = f"; to {'push' if '--push' in argv else 'commit'} anyway once (humans): git " \
                     f"{'push' if '--push' in argv else 'commit'} --no-verify"
        print(f"aigate-lite: internal error ({type(e).__name__}: {str(e)[:200]})\n"
              "  fix: run it again with --debug (or AIGATE_DEBUG=1) and report the traceback" + bypass, file=sys.stderr)
        return 2
    finally:
        if os.environ.get("AIGATE_PROFILE") == "1":
            sys.stderr.write("aigate-profile: " + json.dumps(dict(sorted(PROFILE.items()))) + "\n")


if __name__ == "__main__":
    sys.exit(main())
