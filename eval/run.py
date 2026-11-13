#!/usr/bin/env python3
"""The fixed yardstick: run every corpus case through the real CLI and score it against its labels.

    py eval/run.py                      # run everything, write eval/scoreboard.json, print a summary
    py eval/run.py --check              # also exit 1 on ANY regression against eval/ratchet.json
    py eval/run.py --update-ratchet     # tighten the ratchet (never accepts a new failure for a known case)
    py eval/run.py --families real,seeded,adversarial     # without the cloned clean repositories (offline, fast)
    py eval/run.py --case seed-py-halluc-1                # one case, with its findings printed
    py eval/run.py --script PATH --jobs 1                 # another aigate_lite.py; serial (honest timings)

Standard library only. Each case runs as a person AND as an AI agent (CLAUDECODE set), through
`python aigate_lite.py check --deep --format json` in a throw-away git repository with the registry fixture
(eval/registry_fixture.json), the clock pinned and the caches private. Cases with commit messages also run
`review --message`. Clean repositories are cloned once into a cache folder (EVAL_CACHE, default eval/.cache) and
audited with `check --all --offline`.

Corpus layout: eval/corpus/<id>/ (the training set a fixer may read) and eval/holdout/<id>/ (never shown to a fixer:
only totals are printed or written to eval/scoreboard.json; the case list goes to the git-ignored
eval/.holdout_detail.json). See eval/README.md for case.toml.
"""
from __future__ import annotations

import argparse
import base64
import collections
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CORPUS, HOLDOUT = HERE / "corpus", HERE / "holdout"
SCOREBOARD, RATCHET = HERE / "scoreboard.json", HERE / "ratchet.json"
HOLDOUT_DETAIL = HERE / ".holdout_detail.json"
FIXTURE = HERE / "registry_fixture.json"
NOW = "2026-10-31T00:00:00+00:00"
RANK = {"pass": 1, "warn": 2, "fix": 3, "block": 4}
STRIP = {"CLAUDECODE", "AI_AGENT", "GEMINI_CLI", "GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME", "VIRTUAL_ENV", "CONDA_PREFIX",
         "NO_COLOR", "PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF", "SSLKEYLOGFILE"}

# Provider-shaped secrets are written in the corpus as @@NAME@@ and filled in here, so no tracked file holds one.
_PEM = base64.b64encode(bytes(range(256))).decode()
SUBST = {
    "@@PEMBODY@@": "\n".join(_PEM[i:i + 64] for i in range(0, len(_PEM), 64)),
    "@@ZWSP@@": "​", "@@RLO@@": "‮", "@@LRI@@": "⁦", "@@PDI@@": "⁩",   # invisible characters, never stored
    "@@AWS@@": "AKIA" + "QWERTYUIOPASDFGH",
    "@@GHTOKEN@@": "ghp_" + "Zx9kQ2vB7mN4pL1wT8yR5uJ3cH6dF0aSeGiO",
    "@@STRIPE@@": "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc",
    "@@SLACK@@": "xoxb-" + "123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx",
    "@@PEMBEGIN@@": "-----BEGIN " + "RSA PRIVATE KEY-----",
    "@@PEMEND@@": "-----END " + "RSA PRIVATE KEY-----",
}


def fill(data: bytes) -> bytes:
    for k, v in SUBST.items():
        data = data.replace(k.encode(), v.encode())
    return data


# ---------------------------------------------------------------------------------------------------- loading

class Case:
    def __init__(self, path: Path, holdout: bool):
        self.path, self.holdout = path, holdout
        self.meta = tomllib.loads((path / "case.toml").read_text(encoding="utf-8"))
        m = self.meta
        self.id = m["id"]
        self.family = m["source"]
        self.who = m.get("who", "both")
        self.contested = bool(m.get("contested", False))
        self.must_fire = m.get("must_fire", [])
        self.must_not_fire = m.get("must_not_fire", [])
        self.messages = m.get("messages", [])
        self.mode = m.get("mode", "staged")


def load_cases(families=None, only=None) -> list:
    cases = []
    for base, hold in ((CORPUS, False), (HOLDOUT, True)):
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if (d / "case.toml").is_file():
                c = Case(d, hold)
                if (families is None or c.family in families) and (only is None or c.id in only):
                    cases.append(c)
    return cases


# ---------------------------------------------------------------------------------------------------- building a repo

def git(cwd, *args, env=None, check=True, input=None):
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, check=check, input=input)


def copy_tree(src: Path, dst: Path):
    if not src.is_dir():
        return
    for p in sorted(src.rglob("*")):
        if p.is_file():
            rel = p.relative_to(src)
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(fill(p.read_bytes()))


def apply_generators(case: Case, repo: Path):
    """Content that must not live in the repository (huge, binary, odd encodings) is made here."""
    for g in case.meta.get("generate", []):
        p = repo / g["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        kind, n = g["kind"], int(g.get("size", 1000))
        if kind == "long_line":                      # one line of n characters, optional text before/after
            p.write_bytes(fill(g.get("prefix", "").encode()) + b"x" * n + fill(g.get("suffix", "").encode()) + b"\n")
        elif kind == "many_lines":
            p.write_bytes(b"".join(b"value_%d = %d\n" % (i, i) for i in range(n)))
        elif kind == "binary":
            p.write_bytes(bytes(range(256)) * (n // 256 + 1))
        elif kind == "deep_json":
            p.write_bytes(b"[" * n + b"]" * n)
        elif kind == "deep_python":
            p.write_bytes(b"x = " + b"(" * n + b"1" + b")" * n + b"\n")
        elif kind == "big_text":                     # n bytes of ordinary python
            line = b"print('padding line')\n"
            p.write_bytes(fill(g.get("prefix", "").encode()) + line * (n // len(line)))
        elif kind == "many_files":
            for i in range(n):
                q = repo / g["path"] / f"f{i:05d}.py"
                q.parent.mkdir(parents=True, exist_ok=True)
                q.write_bytes(b"VALUE = %d\n" % i)
            continue
        elif kind == "empty":
            p.write_bytes(b"")
        else:
            raise SystemExit(f"{case.id}: unknown generator {kind}")
    for t in case.meta.get("transform", []):
        p = repo / t["path"]
        data = p.read_bytes()
        kind = t["kind"]
        if kind == "crlf":
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        elif kind == "latin1":
            data = data.decode("utf-8").encode("latin-1", "replace")
        elif kind == "bom":
            data = b"\xef\xbb\xbf" + data
        elif kind == "nul":
            data = data[:len(data) // 2] + b"\x00" + data[len(data) // 2:]
        elif kind == "mixed":                                   # every second line ends in CRLF, the others in LF
            lines = data.replace(b"\r\n", b"\n").split(b"\n")
            data = b"".join(ln + (b"\r\n" if i % 2 else b"\n") for i, ln in enumerate(lines[:-1])) + lines[-1]
        elif kind == "utf16":
            data = b"\xff\xfe" + data.decode("utf-8").encode("utf-16-le")
        p.write_bytes(data)


def build_repo(case: Case, root: Path, env: dict) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main", env=env)
    for k, v in (("user.email", "dev@example.com"), ("user.name", "Dev"), ("commit.gpgsign", "false"),
                 ("core.autocrlf", "false"), ("core.safecrlf", "false")):
        git(repo, "config", k, v, env=env)
    copy_tree(case.path / "before", repo)
    if case.mode != "empty":
        if not any(p for p in repo.iterdir() if p.name != ".git"):
            (repo / "README.md").write_text("# base\n")
        git(repo, "add", "-A", env=env)
        git(repo, "commit", "-q", "--no-verify", "-m", "base", env=env)
    if case.mode == "detached":
        git(repo, "checkout", "-q", "--detach", env=env)
    if case.mode == "merge":
        git(repo, "checkout", "-q", "-b", "topic", env=env)
    for rn in case.meta.get("rename", []):
        git(repo, "mv", rn["from"], rn["to"], env=env)
    copy_tree(case.path / "after", repo)
    for d in case.meta.get("delete", []):
        if (repo / d).exists():
            (repo / d).unlink()
    apply_generators(case, repo)
    git(repo, "add", "-A", "--", ".", env=env)
    for s in case.meta.get("symlink", []):
        blob = git(repo, "hash-object", "-w", "--stdin", env=env, input=s["target"].encode()).stdout.decode().strip()
        git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},{s['path']}", env=env)
    for s in case.meta.get("gitlink", []):
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{s['sha']},{s['path']}", env=env)
    if case.mode == "merge":
        git(repo, "commit", "-q", "--no-verify", "-m", "topic work", env=env)
        git(repo, "checkout", "-q", "main", env=env)
        (repo / "MAIN_ONLY.md").write_text("main moved on\n")
        git(repo, "add", "-A", env=env)
        git(repo, "commit", "-q", "--no-verify", "-m", "main work", env=env)
        git(repo, "merge", "--no-commit", "--no-ff", "topic", env=env, check=False)
    elif case.mode == "range":
        git(repo, "commit", "-q", "--no-verify", "-m", "the change", env=env)
    return repo


def clean_checkout(case: Case, cache: Path):
    """Clone the pinned commit of a CLEAN repository into the cache (once). None when it cannot be fetched."""
    m = case.meta
    dest = cache / case.id
    sha = m["sha"]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        # a half-made folder (an interrupted clone) would make git resolve to a PARENT repository: only trust a folder
        # that is its own work tree and has the pinned commit
        probe = subprocess.run(["git", "-C", str(dest), "rev-parse", "--show-toplevel"], capture_output=True, text=True) \
            if dest.is_dir() else None
        if probe is None or probe.returncode != 0 or os.path.normcase(os.path.realpath(probe.stdout.strip())) != os.path.normcase(os.path.realpath(dest)):
            shutil.rmtree(dest, ignore_errors=True)
        if not (dest / ".git").is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            git(dest, "init", "-q", env=env)
            git(dest, "remote", "add", "origin", m["repo"], env=env)
            git(dest, "fetch", "-q", "--depth", "1", "origin", sha, env=env)
            git(dest, "checkout", "-q", "--detach", "FETCH_HEAD", env=env)
        head = git(dest, "rev-parse", "HEAD", env=env).stdout.decode().strip()
        if head != sha:
            return None, f"pinned {sha} but checked out {head}"
        return dest, ""
    except (subprocess.CalledProcessError, OSError) as e:
        shutil.rmtree(dest, ignore_errors=True)
        return None, f"could not fetch {m['repo']}@{sha[:10]}: {getattr(e, 'stderr', b'').decode(errors='replace')[-120:] or e}"


# ---------------------------------------------------------------------------------------------------- running the CLI

def run_env(who: str, tmp: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in STRIP and not k.startswith("AIGATE_")}
    env.update({"AIGATE_REGISTRY_FIXTURE": str(FIXTURE), "AIGATE_NOW": NOW, "AIGATE_CACHE_DIR": str(tmp / "cache"),
                "HOME": str(tmp), "USERPROFILE": str(tmp), "GIT_CONFIG_NOSYSTEM": "1", "GOENV": "off",
                "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    if who == "agent":
        env["CLAUDECODE"] = "1"
    return env


def gate(script: Path, cwd: Path, env: dict, args: list, timeout=300):
    t0 = time.perf_counter()
    try:
        proc = subprocess.run([sys.executable, str(script), *args], cwd=cwd, env=env, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"crash": "timeout", "ms": (time.perf_counter() - t0) * 1000, "findings": [], "verdict": None}
    ms = (time.perf_counter() - t0) * 1000
    out, err = proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")
    res = {"ms": ms, "code": proc.returncode, "findings": [], "verdict": None, "crash": None}
    if "Traceback (most recent call last)" in err or "Traceback (most recent call last)" in out:
        res["crash"] = "traceback"
    try:
        data = json.loads(out)
    except ValueError:
        res["crash"] = res["crash"] or f"no JSON (exit {proc.returncode}): {(err or out)[-120:].strip()}"
        return res
    if proc.returncode not in (0, 1) and not res["crash"]:
        res["crash"] = f"exit {proc.returncode}"
    res["verdict"] = data.get("verdict")
    res["findings"] = data.get("findings", [])
    res["data"] = data
    return res


def active(f) -> bool:
    return not f.get("allowed") and f.get("action") in RANK and f["action"] != "pass"


def run_case(case: Case, script: Path, who: str, cache: Path, tmp_root: Path, clones: dict | None = None) -> dict:
    tmp = tmp_root / f"{case.id}-{who}"
    tmp.mkdir(parents=True, exist_ok=True)
    env = run_env(who, tmp)
    if case.family == "clean":
        repo, why = (clones or {}).get(case.id) or clean_checkout(case, cache)
        env["AIGATE_REGISTRY_FIXTURE"] = ""            # real projects: the fixture knows nothing about their packages
        if repo is None:
            return {"skipped": why, "findings": [], "ms": 0, "crash": None, "verdict": None}
        res = gate(script, repo, env, ["check", "--all", "--offline", "--no-cache", "--format", "json"])
        return res
    try:
        repo = build_repo(case, tmp, env)
    except subprocess.CalledProcessError as e:
        return {"crash": "case setup failed: " + (e.stderr or b"").decode(errors="replace")[-160:], "findings": [],
                "ms": 0, "verdict": None}
    args = ["check", "--deep", "--no-cache", "--format", "json"]
    if case.mode == "range":
        args += ["--range", "HEAD~1..HEAD"]
    res = gate(script, repo, env, args)
    if case.messages and not res["crash"]:
        rargs = ["review", "--no-cache", "--format", "json"] + [a for m in case.messages for a in ("--message", m)]
        rev = gate(script, repo, env, rargs)
        res["ms"] += rev["ms"]
        if rev["crash"]:
            res["crash"] = "review: " + rev["crash"]
        seen = {(f["rule"], f["path"], f.get("line")) for f in res["findings"]}
        data = rev.get("data", {})
        for f in data.get("must_fix", []) + data.get("should_check", []):
            if (f["rule"], f["path"], f.get("line")) not in seen:
                res["findings"].append({**f, "allowed": f.get("allowed", False)})
    shutil.rmtree(tmp, ignore_errors=True)
    return res


# ---------------------------------------------------------------------------------------------------- scoring

def matching(findings, spec):
    return [f for f in findings if active(f) and f["rule"] == spec["rule"] and f["path"] == spec["path"]
            and (spec.get("line") is None or (f.get("line") is not None and abs(f["line"] - spec["line"]) <= spec.get("tol", 0)))]


def score_one(case: Case, who: str, res: dict) -> dict:
    """The observations for one (case, who) run: per-rule counters and failure keys."""
    obs = {"rules": collections.defaultdict(collections.Counter), "fail": [], "block_unexpected": 0}
    if res.get("skipped"):
        return obs
    key = f"{case.id}|{who}"
    if res["crash"]:
        obs["fail"].append(f"{key}|CRASH:{res['crash'][:80]}")
    errors = [f for f in res["findings"] if f["rule"] == "gate.error" and not f.get("allowed")]
    for f in errors:
        obs["fail"].append(f"{key}|GATE_ERROR:{f['message'][:80]}")
    if case.contested or res["crash"]:
        return obs
    applies = case.who in ("both", who)
    if applies and case.family == "clean":
        for f in res["findings"]:
            if active(f) and f["action"] == "block":
                obs["fail"].append(f"{key}|FALSE_BLOCK:{f['rule']}@{f['path']}:{f.get('line')}")
    if applies:
        for spec in case.must_fire:
            exp = spec.get("agent_action", spec.get("action")) if who == "agent" else spec.get("action")
            hit = matching(res["findings"], spec)
            c = obs["rules"][spec["rule"]]
            where = f"{spec['rule']}@{spec['path']}:{spec.get('line')}"
            if not hit:
                c["fn"] += 1
                obs["fail"].append(f"{key}|FN:{where}")
                continue
            got = max((f["action"] for f in hit), key=RANK.get)
            if exp and RANK[got] > RANK[exp]:
                c["over"] += 1
                obs["fail"].append(f"{key}|OVER:{where} {got}>{exp}")
            elif exp and RANK[got] < RANK[exp]:
                c["under"] += 1
                obs["fail"].append(f"{key}|UNDER:{where} {got}<{exp}")
            else:
                c["tp"] += 1
        for spec in case.must_not_fire:
            hit = matching(res["findings"], spec)
            c = obs["rules"][spec["rule"]]
            if hit:
                c["fp"] += 1
                obs["fail"].append(f"{key}|FP:{spec['rule']}@{spec['path']}:{hit[0].get('line')} {hit[0]['action']}")
            else:
                c["clean"] += 1
        if case.family in ("real", "seeded", "adversarial"):
            labelled = {(s["rule"], s["path"]) for s in case.must_fire}
            for f in res["findings"]:
                if active(f) and f["action"] == "block" and (f["rule"], f["path"]) not in labelled \
                        and not any(m["rule"] == f["rule"] and m["path"] == f["path"] for m in case.must_not_fire):
                    obs["block_unexpected"] += 1
                    obs["fail"].append(f"{key}|UNEXPECTED_BLOCK:{f['rule']}@{f['path']}:{f.get('line')}")
    return obs


def agent_softer(case: Case, person: dict, agent: dict) -> list:
    """Failures where the agent's result is softer than the person's for the same case."""
    if person.get("skipped") or person["crash"] or agent["crash"]:
        return []
    keyed = lambda res: {(f["rule"], f["path"], f.get("line")): RANK[f["action"]] for f in res["findings"] if active(f)}
    p, a = keyed(person), keyed(agent)
    worst_on_path = {}
    for (_r, pa, _ln), ar in a.items():
        worst_on_path[pa] = max(worst_on_path.get(pa, 0), ar)
    # softer = the agent has no equal-or-stricter finding for the same rule/line AND nothing at least as strict on that path
    # (an agent's edit of .aigate/ is a BLOCKing forbidden_path where a person's is a config_changed warning)
    out = [f"{case.id}|both|AGENT_SOFTER:{r}@{pa}:{ln} person {pr} agent {a.get((r, pa, ln), 0)}"
           for (r, pa, ln), pr in sorted(p.items(), key=str) if a.get((r, pa, ln), 0) < pr and worst_on_path.get(pa, 0) < pr]
    order = {"pass": 0, "warn": 1, "fix": 2, "block": 3}
    if order.get(agent["verdict"], 0) < order.get(person["verdict"], 0):
        out.append(f"{case.id}|both|AGENT_SOFTER:verdict person {person['verdict']} agent {agent['verdict']}")
    return out


def pct(values, q):
    values = sorted(values)
    return round(values[min(len(values) - 1, int(len(values) * q))], 1) if values else 0.0


def summarize(cases, results) -> dict:
    """results: {(case id, who): res}. One summary dict (overall + per rule)."""
    rules = collections.defaultdict(collections.Counter)
    fails, times, softer, skipped = [], [], 0, []
    contested = 0
    per_family = collections.Counter()
    for case in cases:
        if case.contested:
            contested += 1
            continue
        per_family[case.family] += 1
        person, agent = results[(case.id, "person")], results[(case.id, "agent")]
        if person.get("skipped"):
            skipped.append(case.id)
            continue
        for who, res in (("person", person), ("agent", agent)):
            obs = score_one(case, who, res)
            for r, c in obs["rules"].items():
                rules[r].update(c)
            fails += obs["fail"]
            times.append(res["ms"])
        extra = agent_softer(case, person, agent)
        softer += len(extra)
        fails += extra
    table = {}
    for r, c in sorted(rules.items()):
        tp, fp, fn, over, under = (c.get(k, 0) for k in ("tp", "fp", "fn", "over", "under"))
        table[r] = {"tp": tp, "fp": fp, "fn": fn, "over": over, "under": under, "clean": c.get("clean", 0),
                    "precision": round(tp / (tp + fp + over), 3) if tp + fp + over else None,
                    "recall": round(tp / (tp + fn + under), 3) if tp + fn + under else None}
    kinds = collections.Counter(f.split("|", 2)[2].split(":", 1)[0] for f in fails)
    return {"cases": dict(per_family), "total_cases": sum(per_family.values()), "skipped": skipped, "contested": contested,
            "rules": table, "failures": dict(kinds), "failing": sorted(fails),
            "overall": {"false_block_clean": kinds.get("FALSE_BLOCK", 0), "unexpected_block": kinds.get("UNEXPECTED_BLOCK", 0),
                        "crashes": kinds.get("CRASH", 0), "gate_errors": kinds.get("GATE_ERROR", 0),
                        "agent_softer": softer, "fn": kinds.get("FN", 0), "fp": kinds.get("FP", 0),
                        "over": kinds.get("OVER", 0), "under": kinds.get("UNDER", 0),
                        "runtime_ms_p50": pct(times, 0.5), "runtime_ms_p95": pct(times, 0.95), "runs": len(times)}}


# ---------------------------------------------------------------------------------------------------- driver

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 on any regression against eval/ratchet.json")
    ap.add_argument("--update-ratchet", action="store_true")
    ap.add_argument("--families", help="comma list of real,seeded,adversarial,clean (default: all)")
    ap.add_argument("--case", action="append", help="run only this case id (repeatable); prints its findings")
    ap.add_argument("--script", default=str(ROOT / "aigate_lite.py"))
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--cache", default=os.environ.get("EVAL_CACHE", str(HERE / ".cache")))
    ap.add_argument("--no-write", action="store_true", help="do not write eval/scoreboard.json")
    ap.add_argument("--failures", action="store_true", help="list every failing key of the training set")
    ap.add_argument("--perf", action="store_true", help="with --check: also fail when p95 runtime is >25%% over the ratchet")
    args = ap.parse_args(argv)
    families = set(args.families.split(",")) if args.families else None
    cases = load_cases(families, set(args.case) if args.case else None)
    if not cases:
        print("no cases found", file=sys.stderr)
        return 2
    script, cache = Path(args.script).resolve(), Path(args.cache)
    tmp_root = Path(tempfile.mkdtemp(prefix="aigate-eval-"))
    results = {}
    t0 = time.perf_counter()
    clones = {c.id: clean_checkout(c, cache) for c in cases if c.family == "clean"}     # serial: no two clones race
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            futs = {pool.submit(run_case, c, script, who, cache, tmp_root, clones): (c, who) for c in cases
                    for who in ("person", "agent")}
            for fut in concurrent.futures.as_completed(futs):
                c, who = futs[fut]
                try:
                    results[(c.id, who)] = fut.result()
                except Exception as e:                                  # the harness itself must never hide a case
                    results[(c.id, who)] = {"crash": f"harness: {type(e).__name__}: {e}", "findings": [], "ms": 0,
                                            "verdict": None}
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    train = [c for c in cases if not c.holdout]
    held = [c for c in cases if c.holdout]
    s_train, s_hold = summarize(train, results), summarize(held, results)
    if args.case:
        for c in cases:
            for who in ("person", "agent"):
                r = results[(c.id, who)]
                print(f"== {c.id} [{who}] exit={r.get('code')} verdict={r['verdict']} {r['ms']:.0f}ms crash={r['crash']}")
                for f in r["findings"]:
                    if active(f):
                        print(f"   {f['action']:5} {f['rule']:26} {f['path']}:{f.get('line')}  {f['message'][:80]}")
    version = subprocess.run([sys.executable, str(script), "--version"], capture_output=True, text=True).stdout.split()[1:2]
    board = {"schema": 1, "tool_version": version[0] if version else "?", "python": sys.version.split()[0],
             "platform": sys.platform, "jobs": args.jobs, "seconds": round(time.perf_counter() - t0, 1),
             "train": {k: v for k, v in s_train.items() if k != "failing"},
             "holdout": {k: v for k, v in s_hold.items() if k not in ("failing", "skipped")} if held else None}
    if not args.no_write and not args.case and families is None:
        SCOREBOARD.write_text(json.dumps(board, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        HOLDOUT_DETAIL.write_text(json.dumps(s_hold["failing"], indent=1) + "\n", encoding="utf-8")
    print_summary(board, s_train, s_hold, held)
    if args.failures:
        print("\n".join(s_train["failing"]))
    ratchet = json.loads(RATCHET.read_text(encoding="utf-8")) if RATCHET.is_file() else None
    full_run = families is None and not args.case
    if args.update_ratchet:
        if not full_run:
            print("--update-ratchet needs a full run (no --families / --case)", file=sys.stderr)
            return 2
        return update_ratchet(ratchet, s_train, s_hold)
    if args.check:
        return check(ratchet, s_train, s_hold, args.perf, full_run)
    return 0


def print_summary(board, s_train, s_hold, held):
    o = s_train["overall"]
    print(f"\ncases: {s_train['cases']} (train) contested: {s_train['contested']} skipped: {s_train['skipped']}")
    print(f"failures (train): {s_train['failures'] or 'none'}")
    print(f"overall: false-BLOCK on clean {o['false_block_clean']}, unexpected BLOCK {o['unexpected_block']}, crashes {o['crashes']}, "
          f"gate.error {o['gate_errors']}, agent-softer {o['agent_softer']}, runtime p50 {o['runtime_ms_p50']} ms p95 {o['runtime_ms_p95']} ms "
          f"({o['runs']} runs, {board['jobs']} jobs, {board['seconds']} s wall)")
    if held:
        ho = s_hold["overall"]
        print(f"holdout: {s_hold['total_cases']} cases, failures {s_hold['failures'] or 'none'} (details: eval/.holdout_detail.json, "
              f"not for fixers), agent-softer {ho['agent_softer']}")
    worst = sorted(((r, v) for r, v in s_train["rules"].items() if v["fp"] or v["fn"] or v["over"] or v["under"]),
                   key=lambda kv: -(kv[1]["fp"] + kv[1]["fn"] + kv[1]["over"] + kv[1]["under"]))[:12]
    for r, v in worst:
        print(f"  {r:30} tp {v['tp']:3} fp {v['fp']:2} fn {v['fn']:2} over {v['over']:2} under {v['under']:2} "
              f"precision {v['precision']} recall {v['recall']}")


def check(ratchet, s_train, s_hold, perf, full_run) -> int:
    if ratchet is None:
        print("eval: no ratchet.json yet (run with --update-ratchet)", file=sys.stderr)
        return 1
    known = set(ratchet["failing"])
    new = [f for f in s_train["failing"] if f not in known and not skipped_case(f, s_train)]
    bad = False
    if new:
        bad = True
        print(f"\nREGRESSION: {len(new)} failure(s) not in the ratchet:")
        for f in new[:40]:
            print("  ", f)
    if full_run and s_hold["total_cases"] and len(s_hold["failing"]) > ratchet["holdout_failing_count"]:
        bad = True
        print(f"\nREGRESSION: holdout failures {len(s_hold['failing'])} > ratchet {ratchet['holdout_failing_count']}")
    if perf and s_train["overall"]["runtime_ms_p95"] > ratchet["best"]["runtime_ms_p95"] * 1.25:
        bad = True
        print(f"\nREGRESSION: p95 runtime {s_train['overall']['runtime_ms_p95']} ms > 125% of {ratchet['best']['runtime_ms_p95']} ms")
    fixed = [f for f in known if f not in set(s_train["failing"])]
    if fixed and not bad:
        print(f"\neval: {len(fixed)} ratcheted failure(s) now pass; run --update-ratchet to lock the gain in")
    print("\neval --check:", "FAILED" if bad else "ok")
    return 1 if bad else 0


def skipped_case(failure_key, s_train) -> bool:
    return failure_key.split("|", 1)[0] in set(s_train["skipped"])


def update_ratchet(ratchet, s_train, s_hold) -> int:
    new_failing = set(s_train["failing"])
    if ratchet is not None:
        known_cases = set(ratchet["cases"])
        added = [f for f in new_failing - set(ratchet["failing"]) if f.split("|", 1)[0] in known_cases]
        if added:
            print("refusing to update: new failure(s) for known cases:", file=sys.stderr)
            for f in added[:40]:
                print("  ", f, file=sys.stderr)
            return 1
    best = {k: s_train["overall"][k] for k in ("false_block_clean", "unexpected_block", "crashes", "gate_errors",
                                                "agent_softer", "runtime_ms_p50", "runtime_ms_p95")}
    if ratchet is not None:
        for k in ("runtime_ms_p50", "runtime_ms_p95"):
            best[k] = min(best[k], ratchet["best"][k])
    cases = sorted({c for c in (ratchet["cases"] if ratchet else [])} | set(case_ids(s_train)))
    out = {"schema": 1, "cases": cases, "failing": sorted(new_failing),
           "holdout_failing_count": len(s_hold["failing"]) if s_hold["total_cases"] else 0, "best": best}
    RATCHET.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"ratchet updated: {len(new_failing)} known failing, holdout {out['holdout_failing_count']}")
    return 0


def case_ids(s_train):
    return [c.id for c in load_cases(None, None) if not c.holdout]


if __name__ == "__main__":
    sys.exit(main())
