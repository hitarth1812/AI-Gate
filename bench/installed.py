"""Installed mode vs vendored mode: startup and one scenario-A check, interleaved, in the way each mode's hook starts the gate.

    python bench/installed.py [--pairs 40]

installed  : `aigate-lite ...`         the console script of an installed package (its module is a cached .pyc in site-packages)
installed-m: `python -P -m aigate_lite` the hook's fallback when the command is not on PATH
vendored   : `python -P -c LOADER .aigate/aigate_lite.py ...` what a vendored hook runs (the bytecode loader)
direct     : `python aigate_lite.py ...`  a loose file, the way a person types it (compiles the file every time)
The package is a hand-built wheel of this checkout installed into a temporary virtual environment (pip when it can bootstrap,
otherwise unpacked like an installer would)."""
import argparse
import base64
import hashlib
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import zipfile

import bench

SCRIPT = os.path.join(bench.ROOT, "aigate_lite.py")


def version():
    with open(SCRIPT, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("__version__ = "):
                return line.split('"')[1]
    raise SystemExit("no __version__")


def build_wheel(dest):
    ver = version()
    dist = f"aigate_lite-{ver}.dist-info"
    with open(SCRIPT, "rb") as fh:
        files = {"aigate_lite.py": fh.read(),
                 f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: aigate-lite\nVersion: {ver}\nRequires-Python: >=3.11\n".encode(),
                 f"{dist}/WHEEL": b"Wheel-Version: 1.0\nGenerator: bench\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
                 f"{dist}/entry_points.txt": b"[console_scripts]\naigate-lite = aigate_lite:main\n"}
    rows = [f"{n},sha256={base64.urlsafe_b64encode(hashlib.sha256(b).digest()).rstrip(b'=').decode()},{len(b)}"
            for n, b in files.items()] + [f"{dist}/RECORD,,"]
    wheel = os.path.join(dest, f"aigate_lite-{ver}-py3-none-any.whl")
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in files.items():
            z.writestr(n, b)
        z.writestr(f"{dist}/RECORD", "\n".join(rows) + "\n")
    return wheel


def install(tmp):
    """(python, console script) of a fresh venv with the package installed."""
    venv = os.path.join(tmp, "venv")
    wheel = build_wheel(tmp)
    if subprocess.run([sys.executable, "-m", "venv", venv], capture_output=True).returncode:
        shutil.rmtree(venv, ignore_errors=True)
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", venv], check=True, capture_output=True)
        bindir = os.path.join(venv, "Scripts" if os.name == "nt" else "bin")
        py = os.path.join(bindir, "python.exe" if os.name == "nt" else "python")
        purelib = subprocess.run([py, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        with zipfile.ZipFile(wheel) as z:
            z.extractall(purelib)
        launcher = os.path.join(bindir, "aigate-lite")
        with open(launcher, "w", newline="\n") as fh:
            fh.write(f"#!/bin/sh\nexec '{py}' -m aigate_lite \"$@\"\n")
        os.chmod(launcher, 0o755)
        subprocess.run([py, "-m", "compileall", "-q", purelib], check=False, capture_output=True)   # as pip does at install
        return py, launcher
    bindir = os.path.join(venv, "Scripts" if os.name == "nt" else "bin")
    py = os.path.join(bindir, "python.exe" if os.name == "nt" else "python")
    subprocess.run([py, "-m", "pip", "install", "--no-index", "--no-deps", "--disable-pip-version-check", wheel],
                   check=True, capture_output=True)
    return py, os.path.join(bindir, "aigate-lite" + (".exe" if os.name == "nt" else ""))


def timed(cmd, cwd, env):
    return bench.run_once(cmd, cwd, env)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=40)
    args = ap.parse_args()
    repos = bench.build_fixtures()
    env = bench.base_env()
    with tempfile.TemporaryDirectory() as tmp:
        env["AIGATE_CACHE_DIR"] = os.path.join(tmp, "cache")                  # outside the repository, as hooks require
        py, exe = install(tmp)
        loader = bench.hook_loader(SCRIPT)
        vendored = [sys.executable, "-P", "-c", loader, SCRIPT] if loader else [sys.executable, SCRIPT]
        modes = {"installed": [exe], "installed-m": [py, "-P", "-m", "aigate_lite"], "vendored": vendored,
                 "direct": [sys.executable, SCRIPT]}
        rows = {"startup": ("--version",), "A": ("check", "--staged", "--offline", "--no-commands", "--format", "json")}
        print(f"python {platform.python_version()} on {platform.platform()}; aigate-lite {version()}; {args.pairs} interleaved pairs")
        for name, tail in rows.items():
            cwd = repos["A"] if name == "A" else bench.ROOT
            for cmd in modes.values():
                timed([*cmd, *tail], cwd, env)                                   # warm caches, fill the bytecode cache
            samples = {m: [] for m in modes}
            for i in range(args.pairs):
                order = list(modes) if i % 2 == 0 else list(reversed(list(modes)))
                for m in order:
                    samples[m].append(timed([*modes[m], *tail], cwd, env))
            base = statistics.median(samples["vendored"])
            print(f"\n{name}: median / p95 ms (vs vendored)")
            for m, xs in samples.items():
                xs.sort()
                med, p95 = statistics.median(xs), xs[int(len(xs) * 0.95) - 1]
                print(f"  {m:<12} {med:8.1f} / {p95:8.1f}   {(med / base - 1) * 100:+6.1f}%")


if __name__ == "__main__":
    main()
