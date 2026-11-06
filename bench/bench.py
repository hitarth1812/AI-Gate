#!/usr/bin/env python3
"""Benchmark harness for aigate-lite.

Builds deterministic fixture repos, then times the gate on each scenario and reads the
AIGATE_PROFILE=1 counters (git subprocesses, blobs read) from stderr.

    python bench/bench.py                                  # print results
    python bench/bench.py --save bench/baseline.json       # record a baseline
    python bench/bench.py --compare bench/baseline.json    # perf gates against a baseline
    python bench/bench.py --against main                   # interleaved old/new runs against a git ref

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
FIXTURE_VERSION = "5"            # bump when generated content changes; forces a rebuild
SCENARIOS = ("A", "B", "C", "D", "E", "F", "G")
# recorded, not gated: F is the polyglot commit (Go + TypeScript + Python), G is `check --all` over a 500-file polyglot repo
INFO_SCENARIOS = ("F", "G")
G_FILES = 500
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
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GOENV": "off", "HOME": os.path.join(WORK, "home"),
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


def go_module(i: int, lines: int = 30) -> str:
    """Realistic, clean Go: stdlib imports, a struct, two small functions and a switch."""
    body = [
        f"// Package orders{i} has order helpers.",
        f"package orders{i}",
        "",
        "import (",
        '\t"encoding/json"',
        '\t"fmt"',
        ")",
        "",
        "type Item struct {",
        "\tName string",
        "\tQty  int",
        "}",
        "",
        f"func Total{i}(items []Item) int {{",
        "\tsum := 0",
        "\tfor _, item := range items {",
        "\t\tif item.Qty > 0 {",
        "\t\t\tsum += item.Qty",
        "\t\t}",
        "\t}",
        "\treturn sum",
        "}",
        "",
        f"func Dump{i}(items []Item) (string, error) {{",
        "\tb, err := json.Marshal(items)",
        "\tif err != nil {",
        '\t\treturn "", fmt.Errorf("dump: %w", err)',
        "\t}",
        "\treturn string(b), nil",
        "}",
    ]
    while len(body) < lines:
        body.append(f"// note {len(body)}")
    return "\n".join(body[:lines]) + "\n"


def go_test_module(i: int) -> str:
    return (f"package orders{i}\n\nimport \"testing\"\n\nfunc TestTotal{i}(t *testing.T) {{\n"
            f"\tif got := Total{i}([]Item{{{{\"a\", 2}}}}); got != 2 {{\n\t\tt.Errorf(\"total = %d\", got)\n\t}}\n}}\n")


def ts_module(i: int, lines: int = 30) -> str:
    body = [
        "import { format } from './format';",
        "",
        f"export interface Item{i} {{ name: string; qty: number }}",
        "",
        f"export function total{i}(items: Item{i}[]): number {{",
        "  let sum = 0;",
        "  for (const item of items) {",
        "    if (item.qty > 0) {",
        "      sum += item.qty;",
        "    }",
        "  }",
        "  return sum;",
        "}",
        "",
        f"export const label{i} = (item: Item{i}): string => {{",
        "  return format(item.name, item.qty);",
        "};",
    ]
    while len(body) < lines:
        body.append(f"// note {len(body)}")
    return "\n".join(body[:lines]) + "\n"


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


def init_polyglot(repo, n):
    """Scenario G: a committed repository of n source files in four languages (an audit target, nothing staged)."""
    init_small(repo)
    write(repo, "svc/go.mod", "module example.com/svc\n\ngo 1.21\n\nrequire github.com/pkg/errors v0.9.1\n")
    each = n // 5
    for i in range(each):
        write(repo, f"app/gen/m{i:03d}.py", py_module(i, 20))
        write(repo, f"web/gen/m{i:03d}.js", js_module(i, 20))
        write(repo, f"web/ts/m{i:03d}.ts", ts_module(i, 30))
        write(repo, f"svc/pkg{i % 20:02d}/m{i:03d}.go", go_module(i, 30))
        write(repo, f"svc/pkg{i % 20:02d}/m{i:03d}_test.go", go_test_module(i))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "polyglot tree")


def scenario_args(s):
    return ["check", "--all", "--offline", "--no-commands", "--format", "json"] if s == "G" else         ["check", "--staged", "--offline", "--no-commands", "--format", "json"]


def stage_change(repo, scenario):
    if scenario == "G":
        return
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
    elif scenario == "F":
        write(repo, "app/orders.py", py_module(0, 20))
        write(repo, "svc/go.mod", "module example.com/svc\n\ngo 1.21\n\nrequire github.com/pkg/errors v0.9.1\n")
        for i in range(4):
            write(repo, f"svc/orders{i}/orders.go", go_module(i, 30))
            write(repo, f"svc/orders{i}/orders_test.go", go_test_module(i))
            write(repo, f"web/orders_{i}.ts", ts_module(i, 30))
    git(repo, "add", "-A", "--", *[p for p in ("app", "web", "svc") if os.path.exists(os.path.join(repo, p))])


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
        (init_large(repo, D_FILES) if s == "D" else init_polyglot(repo, G_FILES) if s == "G" else init_small(repo))
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
        cmd = [py, script, *scenario_args(s)]
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


# ---------------------------------------------------------------------------- interleaved comparison

AGAINST_MEASURES = ("startup", "startup-direct", *SCENARIOS)


def worktree_script(ref):
    """Check `ref` out into a temporary detached worktree; returns (directory, its aigate_lite.py)."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="aigate-bench-")
    proc = subprocess.run(["git", "-C", ROOT, "worktree", "add", "--detach", tmp, ref], capture_output=True, text=True)
    if proc.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        raise SystemExit(f"cannot check out {ref!r}: {proc.stderr.strip()}")
    return tmp, os.path.join(tmp, "aigate_lite.py")


def drop_worktree(tmp):
    subprocess.run(["git", "-C", ROOT, "worktree", "remove", "--force", tmp], capture_output=True, text=True)
    remove_tree(tmp)
    subprocess.run(["git", "-C", ROOT, "worktree", "prune"], capture_output=True, text=True)


def hook_style(script):
    """The loader this script's hooks start it with on this OS, or None (they then run the script directly)."""
    loader = hook_loader(script)
    return loader if loader and (os.name != "nt" or "NT = os.name" in loader) else None


def command_for(measure, py, script):
    """Commands run the way an installed hook runs them (through the bytecode loader where the build has one);
    `startup-direct` is `python script --version`, the way a person types it."""
    args = ["--version"] if measure.startswith("startup") else scenario_args(measure)
    loader = None if measure == "startup-direct" else hook_style(script)
    return [py, "-P", "-c", loader, script, *args] if loader else [py, script, *args]


def measure_against(old, new, pairs, py) -> dict:
    """Alternate old and new runs of each measure (old first on even pairs, new first on odd ones)."""
    import tempfile
    repos = build_fixtures()
    env = base_env()
    out = {}
    # the loader refuses a cache dir inside the repository (bench/.work is), so use one outside it
    env["AIGATE_CACHE_DIR"] = tempfile.mkdtemp(prefix="aigate-bench-cache-")
    try:
        return _measure_against(old, new, pairs, py, repos, env, out)
    finally:
        remove_tree(env["AIGATE_CACHE_DIR"])


def _measure_against(old, new, pairs, py, repos, env, out) -> dict:
    for name in AGAINST_MEASURES:
        cwd = repos.get(name, ROOT)
        cmds = {"old": command_for(name, py, old), "new": command_for(name, py, new)}
        for c in cmds.values():
            run_once(c, cwd, env)                                    # warm the OS cache and any bytecode cache
        samples = {"old": [], "new": []}
        for i in range(pairs):
            for side in (("old", "new") if i % 2 == 0 else ("new", "old")):
                samples[side].append(run_once(cmds[side], cwd, env)[0])
        row = {side: summarize(samples[side]) for side in samples}
        if name in SCENARIOS:
            for side in cmds:
                prof = run_once(cmds[side], cwd, {**env, "AIGATE_PROFILE": "1"})[1]
                row[side]["counters"] = profile_counters(prof.stderr)
        out[name] = row
    return out


def against_gates(res) -> list:
    fails = []
    for name, row in res.items():
        o, n = row["old"], row["new"]
        med, p95 = n["median_ms"] / o["median_ms"] - 1, n["p95_ms"] / o["p95_ms"] - 1
        if name == "startup-direct":
            continue                                   # informational: not how hooks start the gate
        if name in INFO_SCENARIOS:
            continue                                   # new in 0.3.0: recorded, no target
        if name in ("startup", "A"):
            if med > 0.05:
                fails.append(f"{name}: median {o['median_ms']} -> {n['median_ms']} ms ({med * 100:+.1f}%, limit +5%)")
        elif name in SCENARIOS:
            if med > 0.15 or p95 > 0.15:
                fails.append(f"{name}: median {med * 100:+.1f}% / p95 {p95 * 100:+.1f}% (limit +15% for both)")
        for counter in ("git_calls", "blobs_read"):
            a, b = (o.get("counters") or {}).get(counter), (n.get("counters") or {}).get(counter)
            if name == "E" and counter == "blobs_read":
                continue               # E has a baseline file that older builds ignore: not like-for-like
            if a is not None and b is not None and b > a:
                fails.append(f"{name}: {counter} rose from {a} to {b} (hard gate)")
    return fails


def counter_pair(row, key) -> str:
    o, n = (row["old"].get("counters") or {}).get(key), (row["new"].get("counters") or {}).get(key)
    return "" if o is None and n is None else f"{o}/{n}"


def against_table(res, ref) -> str:
    rows = [f"{'measure':<13} {ref + ' med':>10} {'new med':>9} {'change':>8}   {ref + ' p95':>9} {'new p95':>8} {'change':>8}"
            f"   {'git o/n':>8} {'blobs o/n':>10}"]
    for name, row in res.items():
        o, n = row["old"], row["new"]
        rows.append(f"{name:<13} {o['median_ms']:>10.1f} {n['median_ms']:>9.1f} {pct(n['median_ms'], o['median_ms']):>8}   "
                    f"{o['p95_ms']:>9.1f} {n['p95_ms']:>8.1f} {pct(n['p95_ms'], o['p95_ms']):>8}"
                    f"   {counter_pair(row, 'git_calls'):>8} {counter_pair(row, 'blobs_read'):>10}")
    return "\n".join(rows)


def markdown_against(ref, attempts, cmd) -> str:
    out = ["# Performance", "", "<!-- Generated by bench/bench.py --against --markdown. Do not edit by hand. -->", "",
           f"Measured {time.strftime('%Y-%m-%dT%H:%M:%S')} on {platform.platform()}, Python {platform.python_version()}, "
           f"`--offline`, warm OS cache. Command:", "", f"    {cmd}", "",
           f"Each row alternates runs of `{ref}` (checked out into a temporary worktree) and the working tree, so slow drift "
           "on the machine hits both sides equally. Scenarios: A = 1 file / 20 lines; B = 20 files / 400 lines (Python + JS); "
           "C = 300-file change; D = the A change in a 50,000-file repo; E = A with a 1,000-entry baseline present. "
           "Rows run the way an installed hook starts the gate (through the bytecode loader when the build has one for this OS); "
           "`startup-direct` is `python aigate_lite.py --version` as typed by a person and is not gated. "
           "E blob counts are not gated: only E has a baseline file, which older builds do not read.", "",
           "Gates: startup and A median <= +5%; B-E median and p95 <= +15%; git calls and blobs read never rise."]
    for i, a in enumerate(attempts, 1):
        out += ["", f"## Attempt {i} ({a['pairs']} pairs per row): {'PASS' if not a['failures'] else 'FAIL'}", "",
                f"| measure | {ref} median ms | new median ms | change | {ref} p95 ms | new p95 ms | change "
                "| git calls (old/new) | blobs (old/new) |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for name, row in a["results"].items():
            o, n = row["old"], row["new"]
            out.append(f"| {name} | {o['median_ms']:.1f} | {n['median_ms']:.1f} | {pct(n['median_ms'], o['median_ms'])} | "
                       f"{o['p95_ms']:.1f} | {n['p95_ms']:.1f} | {pct(n['p95_ms'], o['p95_ms'])} | "
                       f"{counter_pair(row, 'git_calls')} | {counter_pair(row, 'blobs_read')} |")
        out += [""] + [f"- {f}" for f in a["failures"]]
    return "\n".join(out).rstrip() + "\n"


def run_against(args) -> int:
    pairs = max(args.pairs, 40)
    tmp, old = worktree_script(args.against)
    try:
        new = os.path.abspath(args.script)
        sha = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", args.against], capture_output=True, text=True)
        print(f"python {platform.python_version()} on {platform.platform()}; {args.against} ({sha.stdout.strip()}) "
              f"vs {os.path.relpath(new, ROOT)}; {pairs} interleaved pairs per row")
        attempts = []
        for attempt in (1, 2):
            n = pairs * attempt
            res = measure_against(old, new, n, args.python)
            fails = against_gates(res)
            attempts.append({"pairs": n, "results": res, "failures": fails})
            print(f"\nattempt {attempt} ({n} pairs):")
            print(against_table(res, args.against))
            print("Gates: " + ("PASS" if not fails else "FAIL"))
            for f in fails:
                print("  " + f)
            if not fails:
                break
            if attempt == 1:
                print("\nre-running once with 2x pairs; both results are recorded")
        if args.json:
            with open(args.json, "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"against": args.against, "python": platform.python_version(),
                           "platform": platform.platform(), "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "attempts": attempts}, fh, indent=2)
                fh.write("\n")
        if args.markdown:
            os.makedirs(os.path.dirname(os.path.abspath(args.markdown)), exist_ok=True)
            cmd = "python bench/bench.py " + " ".join(sys.argv[1:])
            with open(args.markdown, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(markdown_against(args.against, attempts, cmd))
        return 1 if attempts[-1]["failures"] else 0
    finally:
        drop_worktree(tmp)


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
    p.add_argument("--against", metavar="REF", help="interleave runs of the git ref REF with this tree and apply the gates")
    p.add_argument("--pairs", type=int, default=40, help="with --against: pairs per row (at least 40)")
    args = p.parse_args(argv)
    if args.rebuild:
        build_fixtures(force=True)
    if args.against:
        return run_against(args)
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
