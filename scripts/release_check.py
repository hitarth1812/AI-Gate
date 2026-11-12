#!/usr/bin/env python3
"""Release guards used by .github/workflows/release.yml (standard library only).

    release_check.py tag [REF_NAME]   REF_NAME is the tag (vX.Y.Z); empty or missing = not a tag run (dispatch).
                                      Fails unless the tag without its leading "v" equals __version__ in aigate_lite.py.
                                      Prints the version.
    release_check.py dist [DIST_DIR]  Fails unless the wheel and the sdist in DIST_DIR (default dist/) both carry exactly
                                      one version, equal to __version__, and the wheel holds only aigate_lite.py and its
                                      dist-info (with LICENSE and NOTICE). Prints the version.

Errors go to stderr, the version to stdout, and the exit status is 1 on any failure.
"""
import re
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?")


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
        built = _metadata_version(z.read(meta_names[0]).decode("utf-8"), wheels[0].name)
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


def main(argv):
    try:
        if argv[:1] == ["tag"] and len(argv) <= 2:
            version = check_tag(argv[1] if len(argv) == 2 else "")
        elif argv[:1] == ["dist"] and len(argv) <= 2:
            version = check_dist(argv[1] if len(argv) == 2 else ROOT / "dist")
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except Failure as e:
        print(f"release check failed: {e}", file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
