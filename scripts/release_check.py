#!/usr/bin/env python3
"""Release guards used by .github/workflows/release.yml (standard library only).

    release_check.py tag [REF_NAME]   REF_NAME is the tag (vX.Y.Z); empty or missing = not a tag run (dispatch).
                                      Fails unless the tag without its leading "v" equals __version__ in aigate_lite.py.
                                      Prints the version.
    release_check.py scoreboard       Fails unless eval/scoreboard.json was made for this __version__ and for the case folders
                                      on disk (the same count of training cases per family, and of holdout cases). A run for a
                                      tag (`tag vX.Y.Z`) does this check too; a dispatch run (empty REF_NAME) does not.
    release_check.py dist [DIST_DIR]  Fails unless the wheel and the sdist in DIST_DIR (default dist/) both carry exactly
                                      one version, equal to __version__, and the wheel holds only aigate_lite.py and its
                                      dist-info (with LICENSE and NOTICE), and the wheel metadata carries the
                                      required classifiers and project URLs (REQUIRED_CLASSIFIERS, REQUIRED_URLS) and
                                      no License :: classifier. Prints the version.

The scoreboard is made by a full `py eval/run.py` on the reference runner and committed by the maintainer; it is checked for a
tag, never in a pull request.

Errors go to stderr, the version to stdout, and the exit status is 1 on any failure.
"""
import json
import re
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?")


REQUIRED_CLASSIFIERS = (
    "Development Status :: ",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Programming Language :: Python :: 3.14",
    "Topic :: Software Development :: Quality Assurance",
    "Topic :: Security",
)
REQUIRED_URLS = ("Homepage", "Repository", "Issues", "Changelog", "Documentation")


class Failure(Exception):
    pass


def code_version(root=ROOT):
    text = (Path(root) / "aigate_lite.py").read_text(encoding="utf-8")
    found = re.findall(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if len(found) != 1:
        raise Failure(f"expected exactly one __version__ in aigate_lite.py, found {len(found)}")
    return found[0]


def check_tag(ref_name, root=ROOT):
    version = code_version(root)
    if ref_name:
        if not ref_name.startswith("v") or ref_name[1:] != version:
            raise Failure(f"the tag is {ref_name!r} but __version__ in aigate_lite.py is {version!r}: "
                          f"the tag must be v{version}. Fix __version__ (and CHANGELOG.md), merge, and tag again.")
        if not SEMVER.fullmatch(version):
            raise Failure(f"{version!r} is not a plain release version (X.Y.Z, optionally with a/b/rc/.post/.dev)")
    return version


def _metadata_version(text, where):
    found = re.findall(r"^Version: (\S+)\s*$", text, re.M)
    if len(found) != 1:
        raise Failure(f"{where}: expected exactly one Version: line, found {len(found)}")
    return found[0]


def check_metadata(text, where):
    """The PyPI page's facts: classifiers (a prefix ending in ": " matches any value) and project URLs."""
    classifiers = re.findall(r"^Classifier: (.+?)\s*$", text, re.M)
    missing = [c for c in REQUIRED_CLASSIFIERS
               if not any(have == c or (c.endswith(": ") and have.startswith(c)) for have in classifiers)]
    if missing:
        raise Failure(f"{where}: classifiers missing from pyproject.toml: {missing}")
    licence = [c for c in classifiers if c.startswith("License ::")]
    if licence:
        raise Failure(f"{where}: {licence} - use the SPDX `license` field only")
    urls = {u.split(",", 1)[0].strip().lower() for u in re.findall(r"^Project-URL: (.+?)\s*$", text, re.M)}
    missing = [u for u in REQUIRED_URLS if u.lower() not in urls]
    if missing:
        raise Failure(f"{where}: [project.urls] missing: {missing}")


def check_dist(dist, root=ROOT):
    version = code_version(root)
    dist = Path(dist)
    if not dist.is_dir():
        raise Failure(f"{dist} does not exist: build first (python -m build)")
    wheels, sdists = sorted(dist.glob("*.whl")), sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise Failure(f"{dist}: expected exactly one wheel and one sdist, found {[p.name for p in dist.iterdir()]}")
    with zipfile.ZipFile(wheels[0]) as z:
        names = z.namelist()
        dist_info = f"aigate_lite-{version}.dist-info/"
        meta_names = [n for n in names if n.endswith(".dist-info/METADATA")]
        if meta_names != [dist_info + "METADATA"]:
            raise Failure(f"{wheels[0].name}: metadata is {meta_names}, expected only {dist_info}METADATA "
                          f"(built version differs from __version__ {version!r})")
        metadata = z.read(meta_names[0]).decode("utf-8")
        built = _metadata_version(metadata, wheels[0].name)
        check_metadata(metadata, wheels[0].name)
        stray = [n for n in names if n != "aigate_lite.py" and not n.startswith(dist_info)]
        if stray:
            raise Failure(f"{wheels[0].name} holds files besides aigate_lite.py and {dist_info}: {stray}")
        if "aigate_lite.py" not in names:
            raise Failure(f"{wheels[0].name} does not contain aigate_lite.py")
        for needed in ("LICENSE", "NOTICE"):
            if not [n for n in names if n.startswith(dist_info) and n.rsplit("/", 1)[-1] == needed]:
                raise Failure(f"{wheels[0].name} does not carry {needed} inside {dist_info}")
    if built != version:
        raise Failure(f"wheel metadata says {built!r} but __version__ is {version!r}")
    with tarfile.open(sdists[0]) as t:
        pkg_info = [m for m in t.getmembers() if m.name.count("/") == 1 and m.name.endswith("/PKG-INFO")]
        if len(pkg_info) != 1:
            raise Failure(f"{sdists[0].name}: expected one top-level PKG-INFO, found {[m.name for m in pkg_info]}")
        sd_version = _metadata_version(t.extractfile(pkg_info[0]).read().decode("utf-8"), sdists[0].name)
    if sd_version != version:
        raise Failure(f"sdist metadata says {sd_version!r} but __version__ is {version!r}")
    return version


class StaleScoreboard(Failure):
    """The message is printed as it is: it names both sides and the command that fixes it."""


def _case_counts(folder):
    """({family: n}, total) of the cases in a folder of case.toml files, the way eval/run.py counts them (a contested case is
    not counted). Only the `source` and `contested` keys are read."""
    by_family = {}
    for toml in sorted(Path(folder).glob("*/case.toml")):
        meta = tomllib.loads(toml.read_text(encoding="utf-8"))
        if not meta.get("contested", False):
            by_family[meta["source"]] = by_family.get(meta["source"], 0) + 1
    return by_family, sum(by_family.values())


def check_scoreboard(root=None):
    """eval/scoreboard.json must describe this tree: the version in aigate_lite.py and the case folders on disk."""
    root = Path(root) if root else ROOT
    version = code_version(root)
    board_path = root / "eval" / "scoreboard.json"
    fix = "  fix: py eval/run.py && py scripts/gen_accuracy.py"
    try:
        board = json.loads(board_path.read_text(encoding="utf-8"))
        board_version, board_train = board["tool_version"], board["train"]
        board_families, board_total = dict(board_train["cases"]), int(board_train["total_cases"])
        board_holdout = int((board.get("holdout") or {}).get("total_cases", 0))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise StaleScoreboard(f"release_check: eval/scoreboard.json cannot be read ({type(e).__name__}: {e}).\n{fix}")
    families, total = _case_counts(root / "eval" / "corpus")
    _, holdout = _case_counts(root / "eval" / "holdout")
    if board_version != version or board_total != total or board_families != families:
        raise StaleScoreboard(f"release_check: eval/scoreboard.json is for {board_version} with {board_total} training cases; "
                              f"the tree is {version} with {total}.\n{fix}")
    if board_holdout != holdout:
        raise StaleScoreboard(f"release_check: eval/scoreboard.json lists {board_holdout} holdout cases; the tree has {holdout}.\n{fix}")
    return version


def main(argv, root=None):
    root = Path(root) if root else ROOT
    try:
        if argv[:1] == ["tag"] and len(argv) <= 2:
            version = check_tag(argv[1] if len(argv) == 2 else "", root)
            if len(argv) == 2 and argv[1]:                      # a tag run publishes: the scoreboard must be for this version
                check_scoreboard(root)
        elif argv[:1] == ["scoreboard"] and len(argv) == 1:
            version = check_scoreboard(root)
        elif argv[:1] == ["dist"] and len(argv) <= 2:
            version = check_dist(argv[1] if len(argv) == 2 else root / "dist", root)
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except StaleScoreboard as e:
        print(e, file=sys.stderr)
        return 1
    except Failure as e:
        print(f"release check failed: {e}", file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
