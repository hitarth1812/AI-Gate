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


def measure_review(script, agent=False):
    """Commit fx.REVIEW_BASE, stage fx.REVIEW_FILES on top, and run `review` with the fixture commit messages. A script
    without the reviewer (an older ref) has nothing to say: that is the "before" column."""
    if "def cmd_review" not in Path(script).read_text(encoding="utf-8"):
        return {"must_fix": [], "should_check": []}
    with tempfile.TemporaryDirectory() as tmp:
        repo, cache = Path(tmp) / "repo", Path(tmp) / "cache"
        repo.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("AIGATE_") and k not in ("CLAUDECODE", "AI_AGENT")}
        env.update({"AIGATE_CACHE_DIR": str(cache), "HOME": str(cache), "GOENV": "off", "GIT_CONFIG_NOSYSTEM": "1"})
        git(repo, "init", "-q", "-b", "main", env=env)
        for key, val in (("user.email", "dev@example.com"), ("user.name", "Dev"), ("commit.gpgsign", "false")):
            git(repo, "config", key, val, env=env)
        for files, message in ((fx.REVIEW_BASE, "base"), (fx.REVIEW_FILES, None)):
            for rel, text in files.items():
                p = repo / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(text.encode())
            git(repo, "add", "-A", env=env)
            if message:
                git(repo, "commit", "-q", "--no-verify", "-m", message, env=env)
        if agent:
            env["CLAUDECODE"] = "1"
        args = [a for m in fx.REVIEW_MESSAGES for a in ("--message", m)]
        proc = subprocess.run([sys.executable, str(script), "review", "--format", "json", "--offline", *args], cwd=repo,
                              env=env, capture_output=True, text=True, encoding="utf-8")
        return json.loads(proc.stdout)


def evaluate_review(data, agent=False):
    """[(case id, rule, path, wanted, got, ok)] for fx.REVIEW_CASES."""
    texts = {**fx.REVIEW_BASE, **fx.REVIEW_FILES}
    rows, found = [], data["must_fix"] + data["should_check"]
    for cid, rule, path, needle, human, agt in fx.REVIEW_CASES:
        want = agt if agent else human
        if path == "(commit)":
            hit = [f for f in found if f["rule"] == rule and f["path"] == path and needle.lower() in f["message"].lower()]
        else:
            line = line_of(texts[path], needle)
            hit = [f for f in found if f["rule"] == rule and f["path"] == path and f["line"] == line]
        got = sorted({f["action"] for f in hit})
        got = got[0] if len(got) == 1 else (None if not got else "/".join(got))
        rows.append((cid, rule, path, want, got, got == want))
    return rows


def review_table(rows):
    """Per rule: true positives (a finding wanted and found), missed (wanted, not found or wrong action), false positives
    (a look-alike that got a finding) and clean look-alikes."""
    per = collections.defaultdict(lambda: collections.Counter())
    for _cid, rule, _path, want, got, ok in rows:
        if want is None:
            per[rule]["clean" if got is None else "false_positive"] += 1
        else:
            per[rule]["true_positive" if ok else "missed"] += 1
    return {r: dict(c) for r, c in sorted(per.items())}


def main_review(args, script):
    data = measure_review(script, args.agent)
    rows = evaluate_review(data, args.agent)
    table = review_table(rows)
    if args.json:
        print(json.dumps({"cases": rows, "per_rule": table}, indent=1))
        return
    for cid, rule, path, want, got, ok in rows:
        print(f"{'ok  ' if ok else 'DIFF'} {cid:22} {rule:26} want={want!s:5} got={got!s:5} {path}")
    print(f"\ncases matching expectation: {sum(r[5] for r in rows)}/{len(rows)} ({'agent' if args.agent else 'human'})")
    print(f"{'rule':28}{'true_pos':>9}{'missed':>8}{'false_pos':>10}{'clean':>7}")
    for rule, c in table.items():
        print(f"{rule:28}{c.get('true_positive', 0):>9}{c.get('missed', 0):>8}{c.get('false_positive', 0):>10}{c.get('clean', 0):>7}")
    total = collections.Counter()
    for c in table.values():
        total.update(c)
    print(f"{'total':28}{total['true_positive']:>9}{total['missed']:>8}{total['false_positive']:>10}{total['clean']:>7}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--against", help="git ref to run instead of this checkout")
    ap.add_argument("--agent", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--review", action="store_true", help="the Phase 6A reviewer fixtures (review command) instead of the gate's")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        script = ROOT / "aigate_lite.py"
        if args.against:
            script = Path(tmp) / "old_aigate_lite.py"
            script.write_bytes(subprocess.run(["git", "show", f"{args.against}:aigate_lite.py"], cwd=ROOT,
                                              capture_output=True, check=True).stdout)
        if args.review:
            return main_review(args, script)
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
