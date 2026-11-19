#!/usr/bin/env python3
"""Corpus v6 (round 5, keys behind a decoder call or assembled from pieces): 30 labelled TRAINING cases written to eval/corpus/
(never to eval/holdout/).

    py eval/tools/seeds/seed_v6.py                       # write every case (it rewrites the same bytes)
    py eval/tools/seeds/seed_v6.py --only ID[,ID...]     # write only these ids
    py eval/tools/seeds/seed_v6.py --out DIR             # write somewhere else (to check that the bytes are the same)

They were written from what README.md and docs/RULES.md say `secrets.hardcoded` is about (a provider key in the diff, however it
is written), NOT from what the tool prints today, and no tool output was looked at.

  A. seeded, must fire (20): one case for each decoder call the tool knows, in each language (13; the Buffer.from case has a base64
     and a hex line), one for a key assembled in a few statements (a Python list, a JavaScript array and a Go slice in one case:
     three files, three findings), and six for the other spellings (a name imported with `from`, an alias, the global object, the
     other encodings, a Go import alias)
  B. adversarial, must NOT fire (10): harmless encoded data that a decoder call turns into text with no provider key in it, a blob
     decoded through an aliased import, and a name that only looks like the standard decoder (another module's `b64decode`)

A key hidden behind a decoder call is written with the encoded-form placeholders of eval/run.py (@@AWSB64@@, @@AWSHEX@@, @@AWSREV@@,
@@AWSROT13@@) and an assembled key with its fragment placeholders (@@AKIA@@, @@AKT1@@, @@AKT2@@), so no tracked file holds a key,
an encoded key or a statement that builds one. The harmless data below is stored as it is (it is not a key in any form).

Not a case, on purpose: a key inside a base64 literal over 4,096 characters. The tool documents that it does not decode such a
literal (README "Limits"), so a `must_not_fire` label would call a missed key correct, and a `must_fire` label would be a case the
tool is documented to fail. The skip is pinned by a unit test (tests/test_encoded_keys.py) and stays visible with -v.

Every id is checked against the holdout hash: these are training cases, so `holdout_of(id)` must be False (force_holdout is not used).
"""
from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_v1 as v1                      # noqa: E402

F, PY_BASE, GO_BASE, TS_BASE = v1.F, v1.PY_BASE, v1.GO_BASE, v1.TS_BASE
ORIGIN = "round 5 authored from README rules (no tool output)"
BLOCK = ("block", "block")
PLAIN = {"python": PY_BASE, "go": GO_BASE, "typescript": TS_BASE, "javascript": TS_BASE}
HEADS = "@@AKIA@@", "@@AKT1@@", "@@AKT2@@"            # "AKIA", "QWERTYUI", "OPASDFGH": the three pieces of @@AWS@@


def case(id, family, language, after, fire=(), notfire=(), **kw):
    assert not v1.holdout_of(id), f"{id} would be routed to eval/holdout (hash); pick another id"
    assert id.startswith("r5-")
    v1.emit(id, family, language, ORIGIN, PLAIN[language], after, fire, notfire, **kw)


def hide(id, language, path, text, needle):
    """A seeded case: one file, one key behind a decoder, a BLOCK for a person and for an agent."""
    case(id, "seeded", language, {path: text}, [F("secrets.hardcoded", path, needle, *BLOCK)])


def harmless(id, language, path, text):
    case(id, "adversarial", language, {path: text}, notfire=[("secrets.hardcoded", path)])


GO_IMPORTS = 'package shop\n\nimport (\n\t"encoding/base64"\n\t"encoding/hex"\n)\n\n'


def hidden_keys():
    # ---- Python: every call the tool treats as a decoder
    hide("r5-base64-decode-python", "python", "app/creds.py",
         'import base64\n\nAWS_KEY = base64.b64decode("@@AWSB64@@").decode()\n', "AWS_KEY")
    hide("r5-urlsafe-b64-decode-python", "python", "app/creds.py",
         'import base64\n\nAWS_KEY = base64.urlsafe_b64decode("@@AWSB64@@")\n', "AWS_KEY")
    hide("r5-fromhex-python", "python", "app/creds.py", 'AWS_KEY = bytes.fromhex("@@AWSHEX@@").decode()\n', "AWS_KEY")
    hide("r5-unhexlify-python", "python", "app/creds.py",
         'import binascii\n\nAWS_KEY = binascii.unhexlify("@@AWSHEX@@")\n', "AWS_KEY")
    hide("r5-codecs-rot13-python", "python", "app/creds.py",
         'import codecs\n\nAWS_KEY = codecs.decode("@@AWSROT13@@", "rot13")\n', "AWS_KEY")
    hide("r5-slice-reverse-python", "python", "app/creds.py", 'AWS_KEY = "@@AWSREV@@"[::-1]\n', "AWS_KEY")
    hide("r5-join-reversed-python", "python", "app/creds.py", 'AWS_KEY = "".join(reversed("@@AWSREV@@"))\n', "AWS_KEY")
    # ---- JavaScript and TypeScript
    hide("r5-atob-javascript", "javascript", "src/creds.js", 'const awsKey = atob("@@AWSB64@@");\nmodule.exports = { awsKey };\n', "awsKey")
    case("r5-buffer-from-two-encodings-typescript", "seeded", "typescript",
         {"src/creds.ts": 'export const awsKey = Buffer.from("@@AWSB64@@", "base64").toString("utf8");\n'
                          'export const awsKeyHex = Buffer.from("@@AWSHEX@@", "hex").toString();\n'},
         [F("secrets.hardcoded", "src/creds.ts", "awsKey =", *BLOCK), F("secrets.hardcoded", "src/creds.ts", "awsKeyHex", *BLOCK)])
    hide("r5-split-reverse-join-typescript", "typescript", "src/creds.ts",
         'export const awsKey = "@@AWSREV@@".split("").reverse().join("");\n', "awsKey")
    # ---- Go
    hide("r5-std-encoding-decode-go", "go", "shop/creds.go",
         GO_IMPORTS + 'func awsKey() []byte {\n\tb, _ := base64.StdEncoding.DecodeString("@@AWSB64@@")\n\treturn b\n}\n', "StdEncoding")
    hide("r5-url-encoding-decode-go", "go", "shop/creds.go",
         GO_IMPORTS + 'func awsKey() []byte {\n\tb, _ := base64.URLEncoding.DecodeString("@@AWSB64@@")\n\treturn b\n}\n', "URLEncoding")
    hide("r5-hex-decodestring-go", "go", "shop/creds.go",
         GO_IMPORTS + 'func awsKey() []byte {\n\tb, _ := hex.DecodeString("@@AWSHEX@@")\n\treturn b\n}\n', "hex.DecodeString")
    # ---- assembled in a few statements: one case, three files
    a, b, c = HEADS
    py = f'parts = ["{a}"]\nparts.append("{b}")\nparts.append("{c}")\nAWS_KEY = "".join(parts)\n'
    js = f'const parts = ["{a}"];\nparts.push("{b}");\nparts.push("{c}");\nconst awsKey = parts.join("");\nmodule.exports = {{ awsKey }};\n'
    go = (f'package shop\n\nimport "strings"\n\nfunc awsKey() string {{\n\tparts := []string{{"{a}"}}\n\tparts = append(parts, "{b}")\n'
          f'\tparts = append(parts, "{c}")\n\treturn strings.Join(parts, "")\n}}\n')
    case("r5-assembled-in-statements", "seeded", "python", {"app/creds.py": py, "src/creds.js": js, "shop/creds.go": go},
         [F("secrets.hardcoded", "app/creds.py", "AWS_KEY", *BLOCK), F("secrets.hardcoded", "src/creds.js", "awsKey =", *BLOCK),
          F("secrets.hardcoded", "shop/creds.go", "strings.Join", *BLOCK)])


def other_spellings():
    hide("r5-from-import-decoder-python", "python", "app/creds.py",
         'from base64 import b64decode\n\nAWS_KEY = b64decode("@@AWSB64@@").decode()\n', "AWS_KEY")
    hide("r5-module-alias-hex-python", "python", "app/creds.py",
         'import binascii as bx\n\nAWS_KEY = bx.a2b_hex("@@AWSHEX@@")\n', "AWS_KEY")
    hide("r5-from-codecs-decode-alias-python", "python", "app/creds.py",
         'from codecs import decode as dec\n\nAWS_KEY = dec("@@AWSROT13@@", encoding="rot_13")\n', "AWS_KEY")
    case("r5-global-object-atob-javascript", "seeded", "javascript",
         {"src/creds.js": 'const awsKey = window.atob("@@AWSB64@@");\nconst awsKeyToo = Buffer.from("@@AWSB64@@", "BASE64").toString();\n'
                          'module.exports = { awsKey, awsKeyToo };\n'},
         [F("secrets.hardcoded", "src/creds.js", "awsKey =", *BLOCK), F("secrets.hardcoded", "src/creds.js", "awsKeyToo =", *BLOCK)])
    hide("r5-import-alias-base64-go", "go", "shop/creds.go",
         'package shop\n\nimport b64 "encoding/base64"\n\nfunc awsKey() []byte {\n\tb, _ := b64.StdEncoding.DecodeString("@@AWSB64@@")\n\treturn b\n}\n',
         "b64.StdEncoding")
    hide("r5-import-alias-hex-go", "go", "shop/creds.go",
         'package shop\n\nimport (\n\thx "encoding/hex"\n)\n\nfunc awsKey() []byte {\n\tb, _ := hx.DecodeString("@@AWSHEX@@")\n\treturn b\n}\n',
         "hx.DecodeString")
    # a name that only looks like the standard decoder. The label says the tool does NOT treat another module's function as a decoder
    # (it is not followed, README "Limits"); the data is a harmless sentence, so nothing here depends on a missed key.
    text = "The nightly job copies the report to the shared drive and posts the link in the team channel when it is done."
    case("r5-lookalike-import-python", "adversarial", "python",
         {"app/textcodec.py": 'def b64decode(text):\n    return text.upper()\n',
          "app/notes.py": f'from app.textcodec import b64decode\n\nNOTE = b64decode("{b64(text)}")\n'},
         notfire=[("secrets.hardcoded", "app/notes.py")])
    blob = bytes((i * 53 + (i >> 2) * 7) % 256 for i in range(900))
    harmless("r5-aliased-import-image-blob-go", "go", "shop/sprite.go",
             'package shop\n\nimport b64 "encoding/base64"\n\nfunc sprite() []byte {\n\tb, _ := b64.StdEncoding.DecodeString("' + b64(blob) + '")\n\treturn b\n}\n')


def b64(data) -> str:
    return base64.b64encode(data if isinstance(data, bytes) else data.encode()).decode()


def harmless_data():
    text = "Release notes: the importer now keeps going when a row is empty, and the report says how many rows were skipped."
    harmless("r5-b64-ordinary-text-python", "python", "app/banner.py", f'import base64\n\nBANNER = base64.b64decode("{b64(text)}").decode()\n')
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10\x08\x06\x00\x00\x00" + bytes(range(64))
    harmless("r5-png-header-javascript", "javascript", "src/icon.js", f'const icon = atob("{b64(png)}");\nmodule.exports = {{ icon }};\n')
    digest = hashlib.sha256(b"release-2026-11").hexdigest()
    harmless("r5-sha256-digest-hex-python", "python", "app/digest.py", f'RELEASE = bytes.fromhex("{digest}")\n')
    harmless("r5-sha1-hex-go", "go", "shop/digest.go",
             GO_IMPORTS + f'func digest() []byte {{\n\tb, _ := hex.DecodeString("{hashlib.sha1(b"release").hexdigest()}")\n\treturn b\n}}\n')
    blob = bytes((i * 37 + (i >> 3) * 11) % 256 for i in range(2900))                      # image-like bytes, about 3,900 characters
    harmless("r5-image-like-blob-javascript", "javascript", "src/sprite.js", f'const sprite = atob("{b64(blob)}");\nmodule.exports = {{ sprite }};\n')
    header = b64('{"alg":"HS256","typ":"JWT"}').rstrip("=").replace("+", "-").replace("/", "_")
    harmless("r5-jwt-segment-typescript", "typescript", "src/token.ts", f'export const header = atob("{header}");\n')
    head, tail = "AK" + "IA", "QWERTYUI" + "OPASDFGH"                                  # the key is built here, never written out
    decoded = "build=4821 region=eu-west-1 x" + head + tail + "x notes=none"           # a key-shaped run inside a longer word
    harmless("r5-key-shaped-inside-a-word-python", "python", "app/meta.py", f'import base64\n\nMETA = base64.b64decode("{b64(decoded)}").decode()\n')
    example = "AK" + "IA" + "IOSFODNN7" + "EXAMPLE"                                      # the AWS documentation example key
    harmless("r5-documented-example-key-encoded-typescript", "typescript", "src/example.ts", f'export const sample = atob("{b64(example)}");\n')


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="write the round-5 training cases")
    ap.add_argument("--only", help="comma-separated ids: write only these")
    ap.add_argument("--out", help="write the cases under this folder instead of eval/corpus")
    args = ap.parse_args(argv)
    only = set(args.only.split(",")) if args.only else None
    if args.out:
        v1.CORPUS = Path(args.out)
    seen, real = [], v1.emit

    def emit(id, *a, **kw):
        seen.append(id)
        if only is None or id in only:
            real(id, *a, **kw)
    v1.emit = emit
    hidden_keys()
    other_spellings()
    harmless_data()
    assert len(set(seen)) == len(seen) == 30, len(seen)
    assert only is None or only <= set(seen), only - set(seen)
    print(len(v1.CASES), "round-5 cases written to", v1.CORPUS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
