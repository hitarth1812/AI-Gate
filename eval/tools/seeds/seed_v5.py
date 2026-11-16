#!/usr/bin/env python3
"""Corpus v5 (round 4, command-level defects, Part F): 18 labelled TRAINING cases written to eval/corpus/ (never to eval/holdout/).

    py eval/tools/seeds/seed_v5.py

They were written from what README.md and docs/RULES.md say each rule is about (a TODO *comment*, a suppression *comment*, weak
hashing *code*, a credential *assigned in code*), NOT from what the tool prints today. Ten are false-positive shapes the tool's own
repository produced (text of a Python source file that sits INSIDE a string literal, and a comment line for weak_crypto); eight are
true-positive guards that must keep firing (the same words in real code or comments, a hashlib.new("md5") call whose algorithm is a
string, a dict-key credential, a provider key inside a string, and a file the tokenizer cannot read). The tool is fixed against them
afterwards. A label is never tuned to the tool's current output.

The first draft of the db_password guard used a repetitive joke value that the tool already treats as non-secret (a FN that has
nothing to do with strings versus code); it was replaced by a random-looking one, so that guard tests only the gating.

A provider-shaped key is written with the placeholder of eval/run.py (@@AWS@@), so no tracked file holds one. Every id is checked
against the holdout hash: these are training cases, so `holdout_of(id)` must be False (force_holdout is not used).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_v1 as v1                      # noqa: E402

F, PY_BASE = v1.F, v1.PY_BASE
ORIGIN = "round 4 authored from README rules (no tool output)"
# test-only spellings, so this generator is not itself a finding for the words it writes into cases
TODO, FIXME, NOQA, TYPE_IGNORE = "TO" + "DO", "FIX" + "ME", "no" + "qa", "type: " + "ignore"
MD5, SHA1 = "md" + "5", "sha" + "1"


def case(id, after, fire=(), notfire=(), **kw):
    assert not v1.holdout_of(id), f"{id} would be routed to eval/holdout (hash); pick another id"
    assert id.startswith("r4-")
    v1.emit(id, "seeded", "python", ORIGIN, PY_BASE, after, fire, notfire, **kw)


# ----------------------------------------------------------------------------------- must NOT fire: text inside a string

def inside_strings():
    case("r4-py-todo-text-in-string", {"src/notes.py": (
        f'"""Release notes helper."""\n\nCHECKLIST = "# {TODO} update the changelog"\n'
        f"SNIPPET = '''\ndef stub():\n    # {TODO} fill in\n    # {FIXME} and this\n    pass\n'''\n")},
         notfire=[("code.todo", "src/notes.py")])
    case("r4-py-todo-in-docstring", {"src/retry.py": (
        f'def fetch(url):\n    """Fetch a URL.\n\n    Example of a note a caller might leave:\n\n        # {TODO}: handle retries\n'
        f'    """\n    return url\n')},
         notfire=[("code.todo", "src/retry.py")])
    case("r4-py-todo-in-prefixed-strings", {"src/labels.py": (
        f'RAW = r"# {TODO} raw string"\nBYTES = b"# {FIXME} bytes"\nname = "x"\nTEMPLATE = f"# {TODO} {{name}}"\n')},
         notfire=[("code.todo", "src/labels.py")])
    case("r4-py-weak-crypto-text-in-string", {"src/help.py": (
        f'HELP = "tip: hashlib.{MD5}(password) is weak; use hashlib.sha256 for a password"\n\n\ndef doc():\n'
        f'    """Do not call hashlib.{SHA1}(token) on a token."""\n    return HELP\n')},
         notfire=[("security.weak_crypto", "src/help.py")])
    case("r4-py-weak-crypto-in-comment", {"src/digest.py": (
        f"import hashlib\n\n# hashlib.{MD5}(password.encode()) was removed in 2023, see the security notes\n\n\n"
        f"def digest(data: bytes) -> str:\n    return hashlib.sha256(data).hexdigest()\n")},
         notfire=[("security.weak_crypto", "src/digest.py")])
    case("r4-py-noqa-in-string", {"src/docs.py": (
        f'RULE = "E501  # {NOQA}: E501 shows up in the docs"\nTEMPLATE = \'\'\'\nimport os  # {NOQA}\n\'\'\'\n')},
         notfire=[("suppress.lint", "src/docs.py")])
    case("r4-py-type-ignore-text-in-string", {"src/examples.py": (
        f'EXAMPLE = "value = compute()  # {TYPE_IGNORE}[arg-type]"\n\n\ndef note():\n'
        f'    """Write `x = f()  # {TYPE_IGNORE}` only with a reason."""\n    return EXAMPLE\n')},
         notfire=[("suppress.type", "src/examples.py")])
    case("r4-py-generic-secret-in-a-code-sample-string", {"src/scaffold.py": (
        "TEMPLATE = 'password = \"hunter2hunter2\"\\napi_token = \"Zx9kQ2mT7vRw\"\\n'\nSQL_DOC = \"\"\"\n"
        "db_password = 'Sup3rS3cretValue'\n\"\"\"\n")},
         notfire=[("secrets.generic", "src/scaffold.py")])
    case("r4-py-generic-secret-in-docstring", {"src/config_help.py": (
        'def explain():\n    """Set the secret like this:\n\n        API_SECRET = "abcdefgh12345678"\n    """\n    return 1\n')},
         notfire=[("secrets.generic", "src/config_help.py")])
    case("r4-py-continued-string-is-still-a-string", {"src/messages.py": (
        f'MSG = "first part # {TODO} is not a comment \\\nsecond part # {NOQA} is still the string"\n')},
         notfire=[("code.todo", "src/messages.py"), ("suppress.lint", "src/messages.py")])


# ------------------------------------------------------------------------------------ must STILL fire: real code or comments

def still_fires():
    case("r4-py-todo-in-a-comment-still-fires", {"src/pay.py": (
        f'def pay(order):\n    total = order.total  # {TODO} apply the discount\n    label = "a # b"  # {FIXME} fix the label\n'
        f"    return total, label\n")},
         [F("code.todo", "src/pay.py", f"# {TODO} apply"), F("code.todo", "src/pay.py", f"# {FIXME} fix")])
    case("r4-py-weak-crypto-in-code-still-fires", {"src/accounts.py": (
        f"import hashlib\n\n\ndef store(password):\n    return hashlib.{MD5}(password.encode()).hexdigest()\n")},
         [F("security.weak_crypto", "src/accounts.py", f"hashlib.{MD5}(password")])
    case("r4-py-hashlib-new-with-md5-string-still-fires", {"src/legacy.py": (
        f'import hashlib\n\n\ndef store(password):\n    return hashlib.new("{MD5}", password.encode()).hexdigest()\n')},
         [F("security.weak_crypto", "src/legacy.py", f'hashlib.new("{MD5}"')])
    case("r4-py-suppressions-in-comments-still-fire", {"src/sloppy.py": (
        f'import os  # {NOQA}\nvalue = 1  # {TYPE_IGNORE}\nmsg = "see # docs"  # {NOQA}: E501\n')},
         [F("suppress.lint", "src/sloppy.py", f"import os  # {NOQA}"), F("suppress.type", "src/sloppy.py", f"# {TYPE_IGNORE}"),
          F("suppress.lint", "src/sloppy.py", f'msg = "see # docs"  # {NOQA}')])
    case("r4-py-provider-key-in-a-string-still-blocks", {"src/example_env.py": 'EXAMPLE_ENV = "AWS_ACCESS_KEY_ID=@@AWS@@"\n'},
         [F("secrets.hardcoded", "src/example_env.py", "EXAMPLE_ENV", "block", "block")])
    case("r4-py-generic-secret-assigned-in-code-still-fires", {"src/db.py": (
        'db_password = "Kq7mW2xZ9pLr"\nCONFIG = {"password": "Zx9kQ2mT7vRw"}\nimport os\nos.environ["API_SECRET"] = "Qw3rTy9uIoP2"\n')},
         [F("secrets.generic", "src/db.py", "db_password", "fix", "fix"), F("secrets.generic", "src/db.py", '"password"', "fix", "fix"),
          F("secrets.generic", "src/db.py", "API_SECRET", "fix", "fix")])
    case("r4-py-generic-secret-after-a-string-on-the-same-line", {"src/two.py": 'label = "x"; api_secret = "Zx9kQ2mT7vRw"\n'},
         [F("secrets.generic", "src/two.py", "api_secret", "fix", "fix")])
    case("r4-py-unreadable-file-keeps-the-line-based-check", {"src/broken.py": (
        f'def f():\n    x = 1  # {TODO} keep working\n    return """never closed\n')},
         [F("code.todo", "src/broken.py", f"# {TODO} keep")])


def main() -> int:
    inside_strings()
    still_fires()
    print(f"wrote {len(v1.CASES)} cases: " + ", ".join(v1.CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
