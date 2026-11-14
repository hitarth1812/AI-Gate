#!/usr/bin/env python3
"""Record a dated registry snapshot for every CLEAN repository (decision C).

    py eval/tools/snapshot_registry.py [--case clean-py-requests]   # network needed; writes eval/snapshots/<id>.json

For each clean case it audits the pinned checkout with the network ON and a private cache folder, repeating until a pass fetches
nothing new (the tool's per-run lookup budget can cut a first pass short), then converts the tool's own lookup cache
(registry-v1.json) into the fixture format that AIGATE_REGISTRY_FIXTURE reads. eval/run.py replays the snapshot (no network, the
clock pinned to the snapshot's `_meta.now`), so the dependency checks run on real projects and still give the same answer
tomorrow. A snapshot is tied to what the tool looks up: after a change that adds lookups, record the snapshots again and commit
the diff for review (a name the snapshot does not hold reads as "does not exist"). Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import run as ev                                    # noqa: E402  (eval/run.py: case loading and the pinned checkouts)

SNAP = HERE / "snapshots"
MISSING = "missing"


def convert(entries: dict) -> dict:
    """The tool's cache entries -> a registry fixture ({eco: {name: entry}}); names that do not exist stay absent."""
    table: dict = {"pypi": {}, "npm": {}, "go": {}}
    names: dict = {}
    for key, ent in entries.items():
        parts = key.split("|")
        kind, eco, name = parts[0], parts[1], parts[2]
        if eco not in table or not isinstance(ent, dict) or "v" not in ent:
            continue
        names.setdefault((eco, name), []).append((kind, parts[3:], ent["v"]))
    for (eco, name), items in sorted(names.items()):
        e: dict = {}
        for kind, rest, val in items:                         # pass 1: the facts that stand alone
            if kind == "exists" and val is True:
                e.setdefault("_present", True)
            elif kind == "info" and isinstance(val, dict):
                e.update({"created": val.get("created"), "versions": dict(val.get("versions") or {}), "latest": val.get("latest"),
                          "yanked": val.get("yanked") or [], "deprecated": val.get("deprecated")})
            elif kind == "downloads" and isinstance(val, int):
                e["downloads"] = val
        for kind, rest, val in items:                         # pass 2: per-version facts
            if kind == "osv" and isinstance(val, list):
                e.setdefault("vulns", {})[rest[0]] = val
            elif kind == "fixed" and isinstance(val, str):
                e.setdefault("fixed", {})[rest[0]] = val
            elif kind == "vtime" and isinstance(val, str) and val != MISSING:
                e.setdefault("versions", {})[rest[0]] = val
        if e:
            e.pop("_present", None)
            table[eco][name] = e
    return table


def audit(script: Path, repo: Path, cache: Path) -> int:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AIGATE_") and k not in ev.STRIP}
    env.update({"AIGATE_CACHE_DIR": str(cache), "HOME": str(cache), "GOENV": "off", "GIT_CONFIG_NOSYSTEM": "1",
                "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    subprocess.run([sys.executable, str(script), "check", "--all", "--format", "json"], cwd=repo, env=env, capture_output=True,
                   timeout=900)
    path = cache / "registry-v1.json"
    return len(json.loads(path.read_text(encoding="utf-8")).get("entries", {})) if path.is_file() else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append")
    ap.add_argument("--script", default=str(HERE.parent / "aigate_lite.py"))
    ap.add_argument("--cache", default=os.environ.get("EVAL_CACHE", str(HERE / ".cache")))
    args = ap.parse_args(argv)
    script, cache_root = Path(args.script), Path(args.cache)
    version = subprocess.run([sys.executable, str(script), "--version"], capture_output=True, text=True).stdout.split()[1:2]
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    SNAP.mkdir(exist_ok=True)
    for case in ev.load_cases({"clean"}, set(args.case) if args.case else None):
        repo, why = ev.clean_checkout(case, cache_root)
        if repo is None:
            print(f"{case.id}: skipped ({why})")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            cache, seen = Path(tmp), -1
            for _ in range(6):                                # until a pass adds nothing
                count = audit(script, repo, cache)
                if count == seen:
                    break
                seen = count
            entries = json.loads((cache / "registry-v1.json").read_text(encoding="utf-8")).get("entries", {}) \
                if (cache / "registry-v1.json").is_file() else {}
        table = convert(entries)
        table["_meta"] = {"case": case.id, "sha": case.meta["sha"], "date": now.date().isoformat(), "now": now.isoformat(),
                          "tool_version": version[0] if version else "?", "lookups": len(entries),
                          "note": "recorded from the tool's own lookup cache; replayed by eval/run.py (no network)"}
        (SNAP / f"{case.id}.json").write_text(json.dumps(table, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{case.id}: {len(entries)} lookups -> {sum(len(v) for k, v in table.items() if k != '_meta')} packages "
              f"({now.date()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
