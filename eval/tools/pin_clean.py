#!/usr/bin/env python3
"""Pin the CLEAN family: resolve each release tag to a commit SHA with `git ls-remote` (annotated tags are peeled) and
write eval/corpus|holdout/clean-*/case.toml. Network needed; run once, commit the result.

    py eval/tools/pin_clean.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
REPOS = [  # (id, repo, tag, language)
    ("clean-py-requests", "psf/requests", "v2.32.3", "python"),
    ("clean-py-flask", "pallets/flask", "3.0.3", "python"),
    ("clean-py-click", "pallets/click", "8.1.7", "python"),
    ("clean-py-attrs", "python-attrs/attrs", "24.2.0", "python"),
    ("clean-py-jinja", "pallets/jinja", "3.1.4", "python"),
    ("clean-go-cobra", "spf13/cobra", "v1.8.1", "go"),
    ("clean-go-mux", "gorilla/mux", "v1.8.1", "go"),
    ("clean-go-logrus", "sirupsen/logrus", "v1.9.3", "go"),
    ("clean-go-chi", "go-chi/chi", "v5.1.0", "go"),
    ("clean-go-testify", "stretchr/testify", "v1.9.0", "go"),
    ("clean-ts-zod", "colinhacks/zod", "v3.23.8", "typescript"),
    ("clean-js-express", "expressjs/express", "4.19.2", "javascript"),
    ("clean-ts-axios", "axios/axios", "v1.7.2", "typescript"),
]


def resolve(repo: str, tag: str) -> str:
    out = subprocess.run(["git", "ls-remote", f"https://github.com/{repo}", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    rows = {ref: sha for sha, ref in (ln.split("\t") for ln in out if ln)}
    sha = rows.get(f"refs/tags/{tag}^{{}}") or rows.get(f"refs/tags/{tag}")
    if not sha:
        raise SystemExit(f"{repo}: tag {tag} not found")
    return sha


def main():
    for cid, repo, tag, lang in REPOS:
        sha = resolve(repo, tag)
        d = (HERE / ("holdout" if zlib.crc32(cid.encode()) % 5 == 0 else "corpus")) / cid
        d.mkdir(parents=True, exist_ok=True)
        lines = [f"id = {json.dumps(cid)}", 'source = "clean"', f"language = {json.dumps(lang)}",
                 f"origin = {json.dumps(f'https://github.com/{repo} @ {tag} (ls-remote, peeled)')}", 'who = "person"',
                 'mode = "clean"', 'label_status = "frozen"', f"repo = {json.dumps(f'https://github.com/{repo}')}",
                 f"sha = {json.dumps(sha)}", 'note = "a popular, well-maintained project: a person must see 0 BLOCK"']
        (d / "case.toml").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        print(f"{cid:20} {tag:8} {sha}  -> {d.parent.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
