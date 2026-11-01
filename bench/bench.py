#!/usr/bin/env python3
"""Benchmark harness for aigate-lite.

Builds deterministic fixture repos, then times the gate on each scenario and reads the
AIGATE_PROFILE=1 counters (git subprocesses, blobs read) from stderr.

    python bench/bench.py                                  # print results
    python bench/bench.py --save bench/baseline.json       # record a baseline
    python bench/bench.py --compare bench/baseline.json    # perf gates against a baseline

Scenarios (all staged-mode, --offline, warm cache):
    A  1 file / 20 lines added to a small repo
    B  20 files / 400 lines, mixed Python + JS
    C  300-file change
    D  the A change on top of a 50,000-file repo
    E  the A change with a 1,000-entry .aigate/baseline.json present (lookups must stay O(1))
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.path.join(HERE, ".work")
FIXTURE_VERSION = "3"            # bump when generated content changes; forces a rebuild
SCENARIOS = ("A", "B", "C", "D", "E")
BASELINE_ENTRIES = 1000          # scenario E: the A change with a baseline of this size present
COLD = ("A", "C")                # also measured with each EXTRA flag below, not gated
EXTRA = {"cold": "--no-cache",   # result and lookup caches off
         "cache": "--cache"}     # result cache on (it is off by default); the first run fills it
EXTRA_ROWS = tuple(f"{s}-{tag}" for tag in EXTRA for s in COLD)
D_FILES = 50_000
STRIP = ("CLAUDECODE", "AI_AGENT", "GEMINI_CLI", "AIGATE_AGENT", "GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME",
         "VIRTUAL_ENV", "CONDA_PREFIX", "PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF")


# ---------------------------------------------------------------------------- fixtures

def base_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in STRIP and not k.startswith("AIGATE_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "HOME": os.path.join(WORK, "home"),
                "AIGATE_CACHE_DIR": os.path.join(WORK, "cache"), "AIGATE_NOW": "2026-10-31T00:00:00+00:00",
                "GIT_AUTHOR_DATE": "2026-10-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-10-01T00:00:00Z"})
    return env


def git(cwd, *args, input=None):
    return subprocess.run(["git", *args], cwd=cwd, env=base_env(), input=input, capture_output=True, check=True)


def py_module(i: int, lines: int = 20) -> str:
    """Realistic, clean Python: stdlib + local imports, a dataclass and two small functions."""
    body = [
        f'"""Order helpers, part {i}."""',
        "import json",
        "from dataclasses import dataclass",
        "",
        "from app import util",
        "",
        "",
        "@dataclass",
        f"class Item{i}:",
        "    name: str",
        "    qty: int = 0",
        "",
        "",
        f"def total_{i}(items):",
        "    return sum(item.qty for item in items if item.qty > 0)",
        "",
        "",
        f"def dump_{i}(items):",
        "    return json.dumps([util.as_dict(item) for item in items])",
    ]
    while len(body) < lines:
        body.append(f"# note {len(body)}")
    return "\n".join(body[:lines]) + "\n"


def js_module(i: int, lines: int = 20) -> str:
    body = [
        "import { format } from './format.js';",
        "",
        f"export function total{i}(items) {{",
        "  let sum = 0;",
        "  for (const item of items) {",
        "    if (item.qty > 0) {",
        "      sum += item.qty;",
        "    }",
        "  }",
        "  return sum;",
        "}",
        "",
        f"export function label{i}(item) {{",
        "  return format(item.name, item.qty);",
        "}",
        "",
        f"export const NAME_{i} = 'orders-{i}';",
    ]
    while len(body) < lines:
        body.append(f"// note {len(body)}")
    return "\n".join(body[:lines]) + "\n"


def write(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def init_small(repo):
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    for k, v in (("user.email", "bench@example.com"), ("user.name", "Bench"), ("commit.gpgsign", "false"),
                 ("core.autocrlf", "false")):
        git(repo, "config", k, v)
    write(repo, "README.md", "# bench\n")
    write(repo, ".gitignore", "node_modules/\n.env\n")
    write(repo, "pyproject.toml", '[project]\nname = "bench"\nversion = "1.0"\nrequires-python = ">=3.11"\n'
                                  "dependencies = []\n")
    write(repo, "package.json", '{"name": "bench", "version": "1.0.0", "dependencies": {}}\n')
    write(repo, "app/__init__.py", "")
    write(repo, "app/util.py", "def as_dict(obj):\n    return dict(vars(obj))\n")
    write(repo, "web/format.js", "export function format(name, qty) {\n  return `${name} x${qty}`;\n}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")


def init_large(repo, n):
    """A small repo plus n generated files, committed through fast-import (no checkout needed)."""
    init_small(repo)
    blobs = [py_module(k, 12).encode() for k in range(50)]
    out = ["blob\nmark :%d\ndata %d\n" % (k + 1, len(b)) for k, b in enumerate(blobs)]
    stream = bytearray()
    for k, b in enumerate(blobs):
        stream += out[k].encode() + b + b"\n"
    msg = b"vendor tree"
    stream += b"commit refs/heads/main\ncommitter Bench <bench@example.com> 1790000000 +0000\n"
    stream += b"data %d\n%s\nfrom refs/heads/main^0\n" % (len(msg), msg)
    for i in range(n):
        stream += b"M 100644 :%d pkg%03d/mod%05d.py\n" % (i % 50 + 1, i // 500, i)
    stream += b"\n"
    git(repo, "fast-import", "--quiet", input=bytes(stream))
    git(repo, "read-tree", "HEAD")          # index matches HEAD; the files are not checked out


def stage_change(repo, scenario):
    if scenario in ("A", "D"):
        write(repo, "app/orders.py", py_module(0, 20))
    elif scenario == "B":
        for i in range(10):
            write(repo, f"app/orders_{i}.py", py_module(i, 20))
            write(repo, f"web/orders_{i}.js", js_module(i, 20))
    elif scenario == "C":
        for i in range(300):
            if i % 2:
                write(repo, f"web/gen/m{i:03d}.js", js_module(i, 10))
            else:
                write(repo, f"app/gen/m{i:03d}.py", py_module(i, 10))
    git(repo, "add", "-A", "--", "app", "web")


def write_baseline(repo, n):
    """A baseline of n entries that match nothing in the change (the worst case: every lookup misses)."""
    rows = [{"rule": "code.todo", "path": f"src/legacy_{i:04d}.py", "fingerprint": f"{i:016x}"} for i in range(n)]
    os.makedirs(os.path.join(repo, ".aigate"), exist_ok=True)
    with open(os.path.join(repo, ".aigate", "baseline.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"version": 1, "tool": "bench", "entries": rows}, fh, indent=2)
    git(repo, "add", ".aigate/baseline.json")
    git(repo, "commit", "-q", "-m", "baseline")


def remove_tree(path):
    """rmtree that also removes read-only files (git objects on Windows), and fails loudly otherwise."""
    import stat

    def retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    if os.path.exists(path):
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=retry)
        else:
            shutil.rmtree(path, onerror=retry)


def build_fixtures(force=False) -> dict:
    stamp = os.path.join(WORK, "stamp")
    repos = {s: os.path.join(WORK, s) for s in SCENARIOS}
    if not force and os.path.exists(stamp) and open(stamp).read() == FIXTURE_VERSION:
        return repos
    remove_tree(WORK)
    os.makedirs(os.path.join(WORK, "home"))
    for s, repo in repos.items():
        t = time.monotonic()
        (init_large(repo, D_FILES) if s == "D" else init_small(repo))
        if s == "E":
            write_baseline(repo, BASELINE_ENTRIES)        # committed first: staged checks read it from the index
        stage_change(repo, "A" if s == "E" else s)
        print(f"  built fixture {s} in {time.monotonic() - t:.1f}s", file=sys.stderr)
    with open(stamp, "w") as fh:
        fh.write(FIXTURE_VERSION)
    return repos


# ---------------------------------------------------------------------------- measuring

def run_once(cmd, cwd, env):
    t = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    ms = (time.perf_counter() - t) * 1000
    if proc.returncode not in (0, 1):
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr}")
    return ms, proc


def profile_counters(stderr: str) -> dict:
    for line in stderr.splitlines():
        if line.startswith("aigate-profile: "):
            return json.loads(line[len("aigate-profile: "):])
    return {}


def summarize(samples) -> dict:
    s = sorted(samples)
    p95 = s[min(len(s) - 1, max(0, -(-95 * len(s) // 100) - 1))]       # nearest rank
    return {"median_ms": round(statistics.median(s), 1), "p95_ms": round(p95, 1), "runs": len(s)}


def hook_loader(script):
    """The script's HOOK_LOADER source (read with ast, not run), or None for builds without one."""
    import ast
    with open(script, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == ["HOOK_LOADER"]:
            return ast.literal_eval(node.value)
    return None


def measure(script, runs, py) -> dict:
    repos = build_fixtures()
    env = base_env()
    result = {"meta": {"python": platform.python_version(), "platform": platform.platform(),
                       "machine": platform.node(), "script": os.path.relpath(script, ROOT),
                       "runs": runs, "fixture_version": FIXTURE_VERSION,
                       "date": time.strftime("%Y-%m-%dT%H:%M:%S")}}
    base = [py, "-c", "pass"]
    result["python_noop"] = summarize([run_once(base, ROOT, env)[0] for _ in range(runs)])
    ver = [py, script, "--version"]
    run_once(ver, ROOT, env)
    result["startup"] = summarize([run_once(ver, ROOT, env)[0] for _ in range(runs)])
    loader = hook_loader(script)
    if loader and os.name != "nt":                 # what a POSIX hook runs; Windows hooks run the script directly
        hook = [py, "-P", "-c", loader, script, "--version"]
        run_once(hook, ROOT, env)                                      # fills the bytecode cache
        result["startup-hook"] = summarize([run_once(hook, ROOT, env)[0] for _ in range(runs)])
    for s in SCENARIOS:
        cmd = [py, script, "check", "--staged", "--offline", "--no-commands", "--format", "json"]
        _, warm = run_once(cmd, repos[s], env)                         # warm the OS and registry caches
        verdict = json.loads(warm.stdout)["verdict"]
        _, prof = run_once(cmd, repos[s], {**env, "AIGATE_PROFILE": "1"})
        times = [run_once(cmd, repos[s], env)[0] for _ in range(runs)]
        result[s] = {**summarize(times), "verdict": verdict, "counters": profile_counters(prof.stderr)}
    for tag, flag in EXTRA.items():
        for s in COLD:
            cmd = [py, script, "check", "--staged", "--offline", "--no-commands", "--format", "json", flag]
            if run_once(cmd, repos[s], env)[1].returncode not in (0, 1):   # e.g. an older script without the flag
                continue
            times = [run_once(cmd, repos[s], env)[0] for _ in range(runs)]
            result[f"{s}-{tag}"] = summarize(times)
    return result


# ---------------------------------------------------------------------------- reporting and gates

def table(res, base=None) -> str:
    rows = [f"{'measure':<12} {'median ms':>10} {'p95 ms':>9}  {'git calls':>9} {'blobs':>6}"
            + ("   vs baseline (median / p95)" if base else "")]
    for key in ("python_noop", "startup", "startup-hook", *SCENARIOS, *EXTRA_ROWS):
        if key not in res:
            continue
        r = res[key]
        c = r.get("counters") or {}
        line = (f"{key:<12} {r['median_ms']:>10.1f} {r['p95_ms']:>9.1f}  {c.get('git_calls', ''):>9} "
                f"{c.get('blobs_read', ''):>6}")
        if base and key in base:
            b = base[key]
            line += f"   {pct(r['median_ms'], b['median_ms'])} / {pct(r['p95_ms'], b['p95_ms'])}"
            bc = b.get("counters") or {}
            if bc:
                line += f"   (baseline git {bc.get('git_calls')}, blobs {bc.get('blobs_read')})"
        rows.append(line)
    return "\n".join(rows)


def pct(new, old) -> str:
    return f"{(new - old) / old * 100:+.1f}%" if old else "n/a"


def gates(res, base) -> list:
    """Failures as strings; empty means every gate passed."""
    fails = []
    if "E" in res and res["E"]["median_ms"] > res["A"]["median_ms"] * 1.10:
        fails.append(f"E: median {res['E']['median_ms']} ms > A {res['A']['median_ms']} ms x 1.10 (baseline lookups)")
    for s in ("startup", *SCENARIOS):
        if s not in base:                       # an older baseline file without this scenario
            continue
        new, old = res[s], base[s]
        for counter in ("git_calls", "blobs_read"):
            n, o = (new.get("counters") or {}).get(counter), (old.get("counters") or {}).get(counter)
            if n is not None and o is not None and n > o:
                fails.append(f"{s}: {counter} rose from {o} to {n} (hard gate)")
        if new["median_ms"] > old["median_ms"] * 1.15 and new["p95_ms"] > old["p95_ms"] * 1.15:
            fails.append(f"{s}: median {old['median_ms']} -> {new['median_ms']} ms and "
                         f"p95 {old['p95_ms']} -> {new['p95_ms']} ms both regressed >15%")
    return fails


def targets(res, base) -> list:
    """Phase 1 targets: (name, target, achieved, met)."""
    out = [("startup (--version)", "<= 40 ms", f"{res['startup']['median_ms']} ms",
            res["startup"]["median_ms"] <= 40)]
    for s in ("A", "B"):
        gain = (base[s]["median_ms"] - res[s]["median_ms"]) / base[s]["median_ms"] * 100
        out.append((f"scenario {s}", ">= 40% faster", f"{gain:.1f}% faster", gain >= 40))
    ratio = res["D"]["median_ms"] / res["A"]["median_ms"]
    out.append(("D / A", "<= 1.2", f"{ratio:.2f}", ratio <= 1.2))
    return out


def markdown(res, base, cmd) -> str:
    """docs/PERF.md, generated from one run (never edited by hand)."""
    m = res["meta"]
    out = ["# Performance", "", "<!-- Generated by bench/bench.py --markdown. Do not edit by hand. -->", "",
           f"Measured {m['date']} on {m['platform']}, Python {m['python']}, {m['runs']} runs per row, "
           f"warm OS cache, `--offline`. Command:", "", f"    {cmd}", "",
           "Scenarios: A = 1 file / 20 lines; B = 20 files / 400 lines (Python + JS); C = 300-file change; "
           "D = the A change in a 50,000-file repo. A-D run with the defaults (result cache off). `-cold` rows add "
           "`--no-cache`; `-cache` rows add `--cache` (result cache on, filled by a first run). "
           "`startup-hook` is `--version` started the way POSIX hooks start the gate (reusing its bytecode); "
           "it is not measured on Windows, where hooks run the script directly. "
           "`python -c pass` is the interpreter's own start-up on the same machine.", "",
           "| measure | median ms | p95 ms | git calls | blobs read |"
           + (" baseline median ms | change |" if base else ""),
           "|---|---:|---:|---:|---:|" + ("---:|---:|" if base else "")]
    for key in ("python_noop", "startup", "startup-hook", *SCENARIOS, *EXTRA_ROWS):
        if key not in res:
            continue
        r, c = res[key], res[key].get("counters") or {}
        name = {"python_noop": "`python -c pass`", "startup": "`--version`",
                "startup-hook": "`--version` via hook loader"}.get(key, key)
        row = f"| {name} | {r['median_ms']:.1f} | {r['p95_ms']:.1f} | {c.get('git_calls', '')} | {c.get('blobs_read', '')} |"
        if base:
            b = base.get(key)
            row += f" {b['median_ms']:.1f} | {pct(r['median_ms'], b['median_ms'])} |" if b else " | |"
        out.append(row)
    if base:
        out += ["", f"Baseline: v0.1.0 measured {base['meta']['date']} on the same machine (`bench/baseline.json`).",
                "", "Most of the baseline's time went to `importlib.metadata.packages_distributions()` scanning every "
                    "package installed in the interpreter, which v0.1.0 ran on every check. The large gains over the "
                    "baseline therefore depend on the environment: with few installed packages the baseline, and the "
                    "gain, are much smaller. Compare rows within one run for anything else.",
                "", "| Phase 1 target | goal | achieved | met |", "|---|---|---|---|"]
        out += [f"| {n} | {w} | {g} | {'yes' if ok else 'no'} |" for n, w, g, ok in targets(res, base)]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", type=int, default=30)
    p.add_argument("--script", default=os.path.join(ROOT, "aigate_lite.py"))
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--save", metavar="FILE", help="write results as JSON (e.g. bench/baseline.json)")
    p.add_argument("--compare", metavar="FILE", help="apply the perf gates against this baseline")
    p.add_argument("--rebuild", action="store_true", help="regenerate the fixture repos")
    p.add_argument("--json", metavar="FILE", help="also write this run's results as JSON")
    p.add_argument("--markdown", metavar="FILE", help="write a Markdown report (e.g. docs/PERF.md)")
    args = p.parse_args(argv)
    if args.rebuild:
        build_fixtures(force=True)
    res = measure(os.path.abspath(args.script), args.runs, args.python)
    base = None
    if args.compare:
        with open(args.compare, encoding="utf-8") as fh:
            base = json.load(fh)
    print(f"python {res['meta']['python']} on {res['meta']['platform']}, {args.runs} runs each")
    print(table(res, base))
    for path in (args.save, args.json):
        if path:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(res, fh, indent=2)
                fh.write("\n")
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)), exist_ok=True)
        cmd = "python bench/bench.py " + " ".join(argv if argv is not None else sys.argv[1:])
        with open(args.markdown, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(markdown(res, base, cmd))
    if base is None:
        return 0
    print("\nPhase 1 targets:")
    for name, want, got, ok in targets(res, base):
        print(f"  {'met ' if ok else 'MISS'} {name:<22} target {want:<14} achieved {got}")
    fails = gates(res, base)
    print("\nPerf gates: " + ("PASS" if not fails else "FAIL"))
    for f in fails:
        print("  " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
