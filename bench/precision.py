"""Run a copy of aigate_lite.py over the synthetic precision fixtures (tests/precision_fixtures.py) and compare each
case with its expectation. Used for the before/after counts in PROGRESS.md:

    python bench/precision.py                          # this checkout, as a human
    python bench/precision.py --agent                  # this checkout, with an AI agent's provenance
    python bench/precision.py --against REF            # an older commit, for the "before" column
"""
import argparse
import collections
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import precision_fixtures as fx          # noqa: E402


def git(cwd, *args, env=None):
    return subprocess.run(["git", *args], cwd=cwd, env=env, text=True, capture_output=True, check=True)


def run_gate(script, repo, env, *extra):
    proc = subprocess.run([sys.executable, str(script), "check", "--format", "json", "--offline", *extra], cwd=repo,
                          env=env, capture_output=True, text=True, encoding="utf-8")
    return json.loads(proc.stdout)


def line_of(text, needle):
    for i, line in enumerate(text.split("\n"), 1):
        if needle in line:
            return i
    raise SystemExit(f"needle {needle!r} is not in the fixture")


def measure(script, agent=False, extra=()):
    with tempfile.TemporaryDirectory() as tmp:
        repo, cache = Path(tmp) / "repo", Path(tmp) / "cache"
        repo.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("AIGATE_") and k not in ("CLAUDECODE", "AI_AGENT")}
        env.update({"AIGATE_CACHE_DIR": str(cache), "HOME": str(cache), "GOENV": "off", "GIT_CONFIG_NOSYSTEM": "1"})
        git(repo, "init", "-q", "-b", "main", env=env)
        for key, val in (("user.email", "dev@example.com"), ("user.name", "Dev"), ("commit.gpgsign", "false")):
            git(repo, "config", key, val, env=env)
        (repo / "README.md").write_text("# demo\n")
        git(repo, "add", "-A", env=env)
        git(repo, "commit", "-q", "--no-verify", "-m", "init", env=env)
        for rel, text in fx.FILES.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(text.encode())
        git(repo, "add", "-A", env=env)
        if agent:
            env["CLAUDECODE"] = "1"
        data = run_gate(script, repo, env, *extra)
    return data


def evaluate(data, agent=False):
    rows, hits = [], 0
    for cid, rule, path, needle, human, agt in fx.CASES:
        want = agt if agent else human
        line = line_of(fx.FILES[path], needle)
        got = sorted({"pass" if f["allowed"] == "info only" else f["action"] for f in data["findings"]
                      if f["rule"] == rule and f["path"] == path and f["line"] == line
                      and (not f["allowed"] or f["allowed"] == "info only")})
        got = got[0] if len(got) == 1 else (None if not got else "/".join(got))
        ok = got == want
        hits += ok
        rows.append((cid, rule, path, want, got, ok))
    return rows, hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--against", help="git ref to run instead of this checkout")
    ap.add_argument("--agent", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        script = ROOT / "aigate_lite.py"
        if args.against:
            script = Path(tmp) / "old_aigate_lite.py"
            script.write_bytes(subprocess.run(["git", "show", f"{args.against}:aigate_lite.py"], cwd=ROOT,
                                              capture_output=True, check=True).stdout)
        data = measure(script, args.agent)
    rows, hits = evaluate(data, args.agent)
    by_rule = collections.Counter(f["rule"] for f in data["findings"] if not f["allowed"])
    by_action = collections.Counter(f["action"] for f in data["findings"] if not f["allowed"])
    if args.json:
        print(json.dumps({"cases": rows, "by_rule": by_rule, "by_action": by_action}, indent=1))
        return
    for cid, rule, path, want, got, ok in rows:
        print(f"{'ok  ' if ok else 'DIFF'} {cid:18} {rule:24} want={want!s:6} got={got!s:10} {path}")
    print(f"\ncases matching expectation: {hits}/{len(rows)} ({'agent' if args.agent else 'human'})")
    print("findings by action:", dict(sorted(by_action.items())), "total", sum(by_action.values()))
    print("findings by rule:", dict(sorted(by_rule.items())))


if __name__ == "__main__":
    main()
