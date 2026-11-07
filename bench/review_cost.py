#!/usr/bin/env python3
"""What the reviewer costs, measured on the bench fixture repositories (built by bench/bench.py).

    python bench/review_cost.py                 # 15 interleaved rounds per scenario
    python bench/review_cost.py --rounds 25 --json out.json

For each staged scenario (A, B, C, D, F) it times, interleaved so drift hits all three alike:
  hook    `check --staged`           what the commit hook runs (the reviewer is not part of it)
  deep    `check --staged --deep`    the same plus the reviewer, warm symbol cache
  cold    `check --staged --deep`    the same with an empty cache directory every time (every blob parsed)
  review  `review`                   the report command, warm
The first line of each row is the median in milliseconds; the hook row is the budget that must not move.
"""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bench                                                    # noqa: E402

SCENARIOS = ("A", "B", "C", "D", "F")
COMMON = ["--offline", "--no-commands"]


def timed(cmd, cwd, env):
    t = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr[-600:]}")
    return (time.perf_counter() - t) * 1000


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=15)
    ap.add_argument("--script", default=os.path.join(bench.ROOT, "aigate_lite.py"))
    ap.add_argument("--json", metavar="FILE")
    args = ap.parse_args(argv)
    repos = bench.build_fixtures()
    env = bench.base_env()
    py, script = sys.executable, args.script
    out = {}
    for s in SCENARIOS:
        repo = repos[s]
        warm = os.path.join(tempfile.mkdtemp(prefix="review-cost-"), "cache")
        os.makedirs(warm)
        cmds = {
            "hook": ([py, script, "check", "--staged", *COMMON, "--format", "json"], env),
            "deep": ([py, script, "check", "--staged", "--deep", *COMMON, "--format", "json"], {**env, "AIGATE_CACHE_DIR": warm}),
            "review": ([py, script, "review", "--offline", "--format", "json"], {**env, "AIGATE_CACHE_DIR": warm}),
        }
        for cmd, e in cmds.values():                           # fill the caches (not measured)
            timed(cmd, repo, e)
        samples = {k: [] for k in ("hook", "deep", "cold", "review")}
        for _ in range(args.rounds):
            for k, (cmd, e) in cmds.items():
                samples[k].append(timed(cmd, repo, e))
            cold = tempfile.mkdtemp(prefix="review-cold-")
            try:
                samples["cold"].append(timed(cmds["deep"][0], repo, {**env, "AIGATE_CACHE_DIR": cold}))
            finally:
                shutil.rmtree(cold, ignore_errors=True)
        shutil.rmtree(os.path.dirname(warm), ignore_errors=True)
        out[s] = {k: {"median_ms": round(statistics.median(v), 1), "min_ms": round(min(v), 1), "max_ms": round(max(v), 1)}
                  for k, v in samples.items()}
        print(f"{s}: " + "  ".join(f"{k} {out[s][k]['median_ms']:>7.1f}" for k in ("hook", "deep", "cold", "review")) +
              f"   (+{out[s]['deep']['median_ms'] - out[s]['hook']['median_ms']:.0f} ms deep, "
              f"+{out[s]['cold']['median_ms'] - out[s]['hook']['median_ms']:.0f} ms cold)", flush=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"python": sys.version.split()[0], "rounds": args.rounds, "scenarios": out}, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
