"""A provider key behind one decoder call, or assembled from literal pieces in a few statements, is one key at run time. The
gate decodes a string literal that is the argument of a known decoder call (at most two levels, at most 4,096 characters),
runs ONLY the provider patterns on the result, and folds a list or string built from literals only. A person and an AI agent
get the same BLOCK. Every key, and every encoded form of it, is built when the test runs: this file holds none (the
repository's own self-scan decodes encoded literals now)."""
import base64
import binascii
import codecs
import hashlib
import re
from types import SimpleNamespace

import pytest

from conftest import aigate, find

HEAD, TAIL = "AK" + "IA", "qwertyuiopasdfgh".upper()
KEY = HEAD + TAIL                                  # a fake AWS access key id, built so this file holds no key (nor a foldable one)
AGENT = {"CLAUDECODE": "1"}
WHAT = "AWS access key ID (AKIA…GH)"
B64 = "base64-decoded from a literal"
HEX = "hex-decoded from a literal"


def b64(s, urlsafe=False):
    raw = s.encode() if isinstance(s, str) else s
    return (base64.urlsafe_b64encode(raw) if urlsafe else base64.b64encode(raw)).decode()


def hexed(s):
    return s.encode().hex()


def rev(s):
    return s[::-1]


def rot13(s):
    return codecs.encode(s, "rot13")


def _short_id(value):
    """A test id from a short string only: a long source text as an id would be in the test id and in the environment."""
    return value if isinstance(value, str) and len(value) <= 40 else f"text{len(value)}"


def parametrize_short(names, values):
    return pytest.mark.parametrize(names, values, ids=_short_id)


def staged(repo, rel, text):
    repo.write(rel, text)
    repo.add()


def hits(res, rule="secrets.hardcoded"):
    return sorted((f["line"], f["action"], f["message"]) for f in find(res, rule))


def both(repo, rel, text, *extra):
    """The secrets.hardcoded findings for a person and for an agent, which must be identical."""
    staged(repo, rel, text)
    person, agent = repo.check(*extra), repo.check(*extra, env=AGENT)
    assert hits(person) == hits(agent)
    return hits(person)


def drop(repo, rel):
    """Remove a staged file (index and work tree), so the next `both` sees only the next file."""
    repo.git("rm", "-q", "-f", rel)


def line_of(text, needle):
    return next(i for i, ln in enumerate(text.split("\n"), 1) if needle in ln)


def block_with(got, line, how):
    assert [(ln, act) for ln, act, _ in got] == [(line, "block")], got
    assert got[0][2].startswith(WHAT) and how in got[0][2], got
    return True


# ------------------------------------------------------------------------------------------ Python: one test per decoder

PY = {
    "b64decode": (f'import base64\n\nk = base64.b64decode("{b64(KEY)}")\n', B64),
    "b64decode_then_decode": (f'import base64\n\nk = base64.b64decode("{b64(KEY)}").decode()\n', B64),
    "b64decode_bytes_literal": (f'import base64\n\nk = base64.b64decode(b"{b64(KEY)}").decode("utf-8")\n', B64),
    "urlsafe_b64decode": (f'import base64\n\nk = base64.urlsafe_b64decode("{b64(KEY, True)}")\n', B64),
    "bytes_fromhex": (f'k = bytes.fromhex("{hexed(KEY)}")\n', HEX),
    "unhexlify": (f'import binascii\n\nk = binascii.unhexlify("{hexed(KEY)}")\n', HEX),
    "codecs_rot13": (f'import codecs\n\nk = codecs.decode("{rot13(KEY)}", "rot13")\n', "rot13-decoded from a literal"),
    "codecs_hex": (f'import codecs\n\nk = codecs.decode("{hexed(KEY)}", "hex")\n', HEX),
    "codecs_base64": (f'import codecs\n\nk = codecs.decode(b"{b64(KEY)}", "base64")\n', B64),
    "slice_reverse": (f'k = "{rev(KEY)}"[::-1]\n', "reversed from a literal"),
    "join_reversed": (f'k = "".join(reversed("{rev(KEY)}"))\n', "reversed from a literal"),
}


@pytest.mark.parametrize("name", sorted(PY))
def test_python_decoder_call_is_a_block_that_names_the_transformation(repo, name):
    src, how = PY[name]
    assert block_with(both(repo, "src/k.py", src), line_of(src, "k ="), how)


def test_python_decoder_call_inside_a_call_or_a_dict_is_found(repo):
    src = f'import base64\n\nconfig = dict(\n    key=base64.b64decode("{b64(KEY)}").decode(),\n)\n'
    assert block_with(both(repo, "src/k.py", src), 4, B64)


def test_python_decoder_works_on_a_literal_joined_in_the_call(repo):
    src = f'import base64\n\nk = base64.b64decode("{b64(KEY)[:12]}" + "{b64(KEY)[12:]}")\n'
    assert block_with(both(repo, "src/k.py", src), 3, B64)


def test_python_reversed_literal_with_a_decode_chain_names_both_steps(repo):
    src = f'import base64\n\nk = base64.b64decode("{b64(rev(KEY))}").decode()[::-1]\n'
    got = both(repo, "src/k.py", src)
    assert [(ln, a) for ln, a, _ in got] == [(3, "block")] and "base64-decoded, then reversed" in got[0][2], got


# ------------------------------------------------------------------------------------------ JS and TS: one test per call

JS = {
    "atob": (f'const k = atob("{b64(KEY)}");\n', B64),
    "atob_single_quotes": (f"const k = atob('{b64(KEY)}');\n", B64),
    "atob_template_literal": (f"const k = atob(`{b64(KEY)}`);\n", B64),
    "buffer_base64": (f'const k = Buffer.from("{b64(KEY)}", "base64").toString();\n', B64),
    "buffer_base64_utf8": (f"const k = Buffer.from('{b64(KEY)}', 'base64').toString('utf8');\n", B64),
    "buffer_hex": (f'const k = Buffer.from("{hexed(KEY)}", "hex").toString();\n', HEX),
    "split_reverse_join": (f'const k = "{rev(KEY)}".split("").reverse().join("");\n', "reversed from a literal"),
    "atob_of_a_concatenation": (f'const k = atob("{b64(KEY)[:8]}" + "{b64(KEY)[8:]}");\n', B64),
}


@pytest.mark.parametrize("path", ["src/k.js", "src/k.ts"])
@pytest.mark.parametrize("name", sorted(JS))
def test_js_decoder_call_is_a_block_that_names_the_transformation(repo, path, name):
    src, how = JS[name]
    assert block_with(both(repo, path, src), 1, how)


def test_js_reversed_literal_after_a_decoder_is_found(repo):
    src = f'const k = atob("{b64(rev(KEY))}").split("").reverse().join("");\n'
    got = both(repo, "src/k.js", src)
    assert [(ln, a) for ln, a, _ in got] == [(1, "block")] and "base64-decoded, then reversed" in got[0][2], got


def test_js_decoder_call_over_several_lines_is_reported_on_the_line_where_it_starts(repo):
    src = f'const a = 1;\n\nexport const k = atob(\n  "{b64(KEY)}",\n);\n'
    assert block_with(both(repo, "src/k.js", src), 3, B64)


# ------------------------------------------------------------------------------------------ Go: one test per call

GO_HEAD = 'package shop\n\nimport (\n\t"encoding/base64"\n\t"encoding/hex"\n)\n\n'
GO = {
    "std_encoding": (f'func k() []byte {{\n\tb, _ := base64.StdEncoding.DecodeString("{b64(KEY)}")\n\treturn b\n}}\n', B64),
    "url_encoding": (f'func k() []byte {{\n\tb, _ := base64.URLEncoding.DecodeString("{b64(KEY, True)}")\n\treturn b\n}}\n', B64),
    "hex_decode": (f'func k() []byte {{\n\tb, _ := hex.DecodeString("{hexed(KEY)}")\n\treturn b\n}}\n', HEX),
    "raw_string": (f'func k() []byte {{\n\tb, _ := hex.DecodeString(`{hexed(KEY)}`)\n\treturn b\n}}\n', HEX),
}


@pytest.mark.parametrize("name", sorted(GO))
def test_go_decoder_call_is_a_block_that_names_the_transformation(repo, name):
    src, how = GO[name]
    text = GO_HEAD + src
    assert block_with(both(repo, "shop/k.go", text), line_of(text, "Decode"), how)


# ------------------------------------------------------------------------------------------ assembled in a few statements

PIECES = (KEY[:4], KEY[4:12], KEY[12:])


def py_list(n=0):
    a, b, c = PIECES
    return "\n".join([f'parts = ["{a}"]', f'parts.append("{b}")', f'parts.extend(["{c}"])'] + ["x = 1"] * n + ['k = "".join(parts)']) + "\n"


def js_list(n=0):
    a, b, c = PIECES
    return "\n".join([f'const parts = ["{a}"];', f'parts.push("{b}");', f'parts.push("{c}");'] + ["const x = 1;"] * n
                     + ['const k = parts.join("");']) + "\n"


def go_list(n=0):
    a, b, c = PIECES
    body = [f'\tparts := []string{{"{a}"}}', f'\tparts = append(parts, "{b}")', f'\tparts = append(parts, "{c}")'] \
        + ["\tx := 1"] * n + ['\tk := strings.Join(parts, "")']
    return 'package shop\n\nimport "strings"\n\nfunc k() string {\n' + "\n".join(body) + "\n\t_ = x\n\treturn k\n}\n"


ASSEMBLED = "assembled from literals"
# (path, source, the line where the key is complete)
ASSEMBLY = {
    "py_list_join": ("src/k.py", py_list(), 4),
    "py_string_plus_equals": ("src/k.py", f'k = "{PIECES[0]}"\nk += "{PIECES[1]}"\nk += "{PIECES[2]}"\n', 3),
    "py_list_one_line": ("src/k.py", f'parts = ["{PIECES[0]}"]; parts.append("{PIECES[1] + PIECES[2]}"); k = "".join(parts)\n', 1),
    "js_push_join": ("src/k.js", js_list(), 4),
    "js_string_plus_equals": ("src/k.ts", f'let k = "{PIECES[0]}";\nk += "{PIECES[1]}";\nk += "{PIECES[2]}";\n', 3),
    "js_push_join_one_line": ("src/k.js", f'const p = ["{PIECES[0]}"]; p.push("{PIECES[1] + PIECES[2]}"); const k = p.join("");\n', 1),
    "go_append_join": ("shop/k.go", go_list(), 9),
    "go_string_plus_equals": ("shop/k.go", f'package shop\n\nfunc k() string {{\n\tk := "{PIECES[0]}"\n\tk += "{PIECES[1]}"\n'
                              f'\tk += "{PIECES[2]}"\n\treturn k\n}}\n', 6),
}


@pytest.mark.parametrize("name", sorted(ASSEMBLY))
def test_a_key_assembled_from_literal_pieces_is_a_block(repo, name):
    path, src, line = ASSEMBLY[name]
    assert block_with(both(repo, path, src), line, ASSEMBLED)


@pytest.mark.parametrize("make,path,first", [(py_list, "src/k.py", 1), (js_list, "src/k.js", 1), (go_list, "shop/k.go", 6)])
def test_assembly_within_25_lines_is_a_block_and_at_26_lines_is_no_finding(repo, make, path, first):
    # `first` is the line of the first statement; 22 filler lines put the join exactly 25 lines after it, 23 put it 26 after
    inside = make(22)
    join = next(i for i, ln in enumerate(inside.split("\n"), 1) if "join" in ln.lower() and "parts" in ln)
    assert join - first == 25
    assert [(ln, a) for ln, a, _ in both(repo, path, inside)] == [(join, "block")]
    assert both(repo, path, make(23)) == []


@parametrize_short("path,src", [
    ("src/k.py", f'tail = input()\nparts = ["{PIECES[0]}"]\nparts.append(tail)\nparts.append("{PIECES[2]}")\nk = "".join(parts)\n'),
    ("src/k.py", f'import os\nk = "{PIECES[0]}"\nk += os.environ["X"]\nk += "{PIECES[1] + PIECES[2]}"\n'),
    ("src/k.js", f'const parts = ["{PIECES[0]}"];\nparts.push(tail);\nparts.push("{PIECES[1] + PIECES[2]}");\nconst k = parts.join("");\n'),
    ("src/k.js", f'let k = "{PIECES[0]}";\nk += tail;\nk += "{PIECES[1] + PIECES[2]}";\n'),
    ("shop/k.go", f'package shop\n\nfunc k(tail string) string {{\n\tparts := []string{{"{PIECES[0]}"}}\n\tparts = append(parts, tail)\n'
                  f'\tparts = append(parts, "{PIECES[1] + PIECES[2]}")\n\treturn strings.Join(parts, "")\n}}\n'),
    ("shop/k.go", f'package shop\n\nfunc k(tail string) string {{\n\tk := "{PIECES[0]}"\n\tk += tail\n\tk += "{PIECES[1] + PIECES[2]}"\n\treturn k\n}}\n'),
])
def test_assembly_with_a_piece_that_is_a_variable_is_no_finding(repo, path, src):
    assert both(repo, path, src) == []


def test_assembly_that_is_reset_in_between_is_no_finding(repo):
    src = f'k = "{PIECES[0]}"\nk += "{PIECES[1]}"\nk = other()\nk += "{PIECES[2]}"\n'
    assert both(repo, "src/k.py", src) == []


# ------------------------------------------------------------------------------------------ harmless encoded data

SHA256 = hashlib.sha256(b"release").hexdigest()
TEXT = "The quick brown fox jumps over the lazy dog, and the build is green again this week."
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(200))


@parametrize_short("path,src", [
    ("src/k.py", f'import base64\n\nk = base64.b64decode("{b64(TEXT)}")\n'),
    ("src/k.py", f'import base64\n\nk = base64.b64decode("{b64(PNG)}")\n'),
    ("src/k.py", f'k = bytes.fromhex("{SHA256}")\n'),
    ("src/k.py", f'k = "{SHA256}"[::-1]\n'),
    ("src/k.js", f'const k = atob("{b64(TEXT)}");\n'),
    ("src/k.js", f'const k = atob("{b64(PNG)}");\n'),
    ("src/k.js", f'const k = Buffer.from("{SHA256}", "hex");\n'),
    ("shop/k.go", GO_HEAD + f'func k() []byte {{\n\tb, _ := hex.DecodeString("{SHA256}")\n\treturn b\n}}\n'),
    ("shop/k.go", GO_HEAD + f'func k() []byte {{\n\tb, _ := base64.StdEncoding.DecodeString("{b64(PNG)}")\n\treturn b\n}}\n'),
])
def test_harmless_encoded_data_is_no_finding(repo, path, src):
    got = both(repo, path, src)
    assert got == []


def test_an_encoded_key_inside_a_longer_word_fails_the_boundary_rule(repo):
    src = f'import base64\n\nk = base64.b64decode("{b64("x" + KEY + "x")}")\n'
    assert both(repo, "src/k.py", src) == []


def test_an_encoded_key_with_a_word_boundary_is_found_inside_text(repo):
    src = f'import base64\n\nk = base64.b64decode("{b64("id: " + KEY + ", region: eu")}")\n'
    assert block_with(both(repo, "src/k.py", src), 3, B64)


def test_the_documented_example_key_stays_unflagged_when_encoded(repo):
    example = "AK" + "IA" + "IOSFODNN7" + "EXAMPLE"
    py = f'import base64\n\nk = base64.b64decode("{b64(example)}")\n'
    js = f'const k = atob("{b64(example)}");\n'
    assert both(repo, "src/k.py", py) == []
    drop(repo, "src/k.py")
    assert both(repo, "src/k.js", js) == []


@parametrize_short("path,src", [
    ("src/k.py", 'import base64\n\nk = base64.b64decode("{}")\n'),
    ("src/k.js", 'const k = atob("{}");\n'),
])
def test_the_generic_secret_rule_is_never_applied_to_decoded_text(repo, path, src):
    decoded = 'db_password = "Zx9kQ2mT7vRw3pLs"\napi_token = "Qw3rTy9uIoP2aSdF"\n'
    staged(repo, path, src.format(b64(decoded)))
    for env in (None, AGENT):
        res = repo.check(env=env)
        assert not [f for f in res["findings"] if f["rule"].startswith("secrets.")], res["findings"]


# ------------------------------------------------------------------------------------------ size and depth limits

def test_a_literal_of_4096_characters_is_decoded_and_one_over_is_skipped_and_the_skip_is_visible(repo):
    def literal(n):                                       # a base64 literal of exactly n characters that holds the key
        raw = (KEY + "\n").encode() + b"z" * (n * 3 // 4)
        text = b64(raw)[:n]
        assert len(text) == n
        return text
    inside = f'import base64\n\nk = base64.b64decode("{literal(4096)}")\n'
    assert block_with(both(repo, "src/in.py", inside), 3, B64)
    drop(repo, "src/in.py")
    over = f'import base64\n\nk = base64.b64decode("{literal(5000)}")\n'
    assert both(repo, "src/big.py", over) == []
    for _ in range(2):                                   # the second run must say it too (a clean verdict is not cached)
        notes = repo.check("-v")["notes"]
        assert any("big.py" in n and "4096" in n and "not decoded" in n for n in notes), notes
    assert not any("4096" in n for n in repo.check()["notes"])        # only with -v


def test_a_long_js_literal_is_skipped_too(repo):
    text = b64((KEY + "\n").encode() + b"z" * 3900)
    assert len(text) > 4096
    assert both(repo, "src/big.js", f'const k = atob("{text}");\n') == []
    notes = repo.check("-v")["notes"]
    assert any("big.js" in n and "4096" in n for n in notes), notes


def test_a_key_encoded_twice_is_a_block_at_depth_two(repo):
    py = f'import base64\n\nk = base64.b64decode(base64.b64decode("{b64(b64(KEY))}"))\n'
    got = both(repo, "src/k.py", py)
    assert [(ln, a) for ln, a, _ in got] == [(3, "block")] and "base64-decoded, then base64-decoded" in got[0][2], got
    drop(repo, "src/k.py")
    js = f'const k = atob(atob("{b64(b64(KEY))}"));\n'
    assert [(ln, a) for ln, a, _ in both(repo, "src/k.js", js)] == [(1, "block")]


def test_a_hex_of_base64_key_is_a_block_at_depth_two(repo):
    py = f'import base64\n\nk = bytes.fromhex(base64.b64decode("{b64(hexed(KEY))}").decode())\n'
    got = both(repo, "src/k.py", py)
    assert [(ln, a) for ln, a, _ in got] == [(3, "block")] and "base64-decoded, then hex-decoded" in got[0][2], got


def test_a_key_encoded_three_times_is_no_finding(repo):
    py = f'import base64\n\nk = base64.b64decode(base64.b64decode(base64.b64decode("{b64(b64(b64(KEY)))}")))\n'
    assert both(repo, "src/k.py", py) == []
    drop(repo, "src/k.py")
    js = f'const k = atob(atob(atob("{b64(b64(b64(KEY)))}")));\n'
    assert both(repo, "src/k.js", js) == []


# ------------------------------------------------------------------------------------------ what is not detected

@parametrize_short("path,src", [
    ("src/k.py", f'import base64\n\ndef my_b64(x):\n    return base64.b64decode(x)\n\nk = my_b64("{b64(KEY)}")\n'),
    ("src/k.py", f'import base64\n\nraw = "{b64(KEY)}"\nk = base64.b64decode(raw)\n'),
    ("src/k.py", 'import base64, os\n\nk = base64.b64decode(os.environ["X"])\n'),
    ("src/k.js", f'const decode = (s) => atob(s);\nconst k = decode("{b64(KEY)}");\n'),
    ("src/k.js", f'const raw = "{b64(KEY)}";\nconst k = atob(raw);\n'),
])
def test_custom_decoders_and_run_time_values_are_not_detected(repo, path, src):
    assert both(repo, path, src) == []


def test_a_decoder_call_on_an_unchanged_line_gives_no_finding(repo):
    old = f'import base64\n\nk = base64.b64decode("{b64(KEY)}")\n'
    repo.write("src/k.py", old)
    repo.commit("old")                                    # the key is already in the history: this change does not add it
    staged(repo, "src/k.py", old + "\nother = 1\n")
    assert hits(repo.check()) == []


# ------------------------------------------------------------------------------------------ allow entries, the control, the table

def test_an_allow_entry_works_with_the_fingerprint_of_the_decoded_value(repo):
    src = f'import base64\n\nk = base64.b64decode("{b64(KEY)}")\n'
    staged(repo, "src/k.py", src)
    key = find(repo.check(), "secrets.hardcoded")[0]
    expected = aigate.fingerprint_v2("secrets.hardcoded", "src/k.py", hashlib.sha1(KEY.encode()).hexdigest())
    assert key["fingerprint"] == expected
    repo.write(".aigate/lite.toml", 'version = 1\n[[allow]]\nrule = "secrets.hardcoded"\npath = "src/k.py"\n'
               f'fingerprint = "{expected}"\nreason = "test fixture: a throwaway key used by the unit tests"\n')
    repo.add()
    res = repo.check()
    assert find(res, "secrets.hardcoded") and all(f["allowed"] for f in find(res, "secrets.hardcoded")), res["findings"]


def test_the_message_keeps_the_standard_fix_text(repo):
    staged(repo, "src/k.py", f'import base64\n\nk = base64.b64decode("{b64(KEY)}")\n')
    plain = find(repo.check(), "secrets.hardcoded")[0]
    other = repo.path / "src" / "p.py"
    other.write_text(f'k = "{KEY}"\n', encoding="utf-8", newline="\n")
    repo.add()
    direct = [f for f in find(repo.check(), "secrets.hardcoded") if f["path"] == "src/p.py"][0]
    assert plain["hint"].split("\n")[0] == direct["hint"].split("\n")[0]
    assert "rotate" in plain["hint"].lower()


@parametrize_short("path,src", [
    ("src/k.py", f'k = "AKIA" + "{KEY[4:]}"\n'),
    ("src/k.js", f'const k = "AKIA" + "{KEY[4:]}";\n'),
    ("shop/k.go", f'package shop\n\nconst k = "AKIA" + "{KEY[4:]}"\n'),
    ("src/k.py", f'k = "{KEY}"\n'),
])
def test_the_plain_and_the_joined_key_are_still_a_block_and_do_not_name_a_transformation(repo, path, src):
    got = both(repo, path, src)
    assert [a for _, a, _ in got] == ["block"] and not any(w in got[0][2] for w in ("decoded", "reversed", "assembled")), got


def test_a_file_without_a_decoder_name_is_not_parsed_for_it():
    # zero cost: the check looks at the added lines once and returns before it touches the file
    fc = SimpleNamespace(ext=".py", path="a.py", added={1: "x = 1", 2: 'name = "AK" + "IA"'})
    assert aigate._encoded_key_findings(None, fc, "", set()) == []
    fc = SimpleNamespace(ext=".js", path="a.js", added={1: "const x = 1;"})
    assert aigate._encoded_key_findings(None, fc, "", set()) == []


def test_an_empty_decoder_table_restores_the_old_behaviour(monkeypatch):
    monkeypatch.setattr(aigate, "DECODER_CALLS", {})
    fc = SimpleNamespace(ext=".py", path="a.py", added={1: f'k = base64.b64decode("{b64(KEY)}")', 2: 'parts.append("x")'})
    assert aigate._encoded_key_findings(None, fc, "", set()) == []     # no table: nothing is read (ctx would raise)


def test_the_decoders_agree_with_the_standard_library():
    # the table maps a call to a transformation; these are the ones the gate applies, checked on a value built here
    assert aigate._decode_literal("base64", b64(KEY)) == KEY
    assert aigate._decode_literal("base64", b64(KEY, True)) == KEY
    odd = b"\xfb\xff\xbf"                                  # bytes whose text differs between the two alphabets
    assert b64(odd, True) != b64(odd) and aigate._decode_literal("base64", b64(odd, True)) == odd.decode("latin-1")
    assert aigate._decode_literal("base64", b64(odd)) == odd.decode("latin-1")
    assert aigate._decode_literal("base64", b64(KEY).rstrip("=")) == KEY                    # missing padding is fine
    assert aigate._decode_literal("hex", hexed(KEY)) == KEY
    assert aigate._decode_literal("rot13", rot13(KEY)) == KEY
    assert aigate._decode_literal("reverse", rev(KEY)) == KEY
    assert aigate._decode_literal("base64", "not base64!") is None
    assert aigate._decode_literal("hex", "abc") is None and aigate._decode_literal("hex", "zz") is None
    assert binascii.hexlify(KEY.encode()).decode() == hexed(KEY)
    assert re.fullmatch(r"[0-9a-f]+", hexed(KEY))


# ------------------------------------------------------------------------------------------ other spellings of the same decoders
# A decoder is also known by a name the same file imports it under. Same-file and static only.

PY_SPELLINGS = {
    "from_import": (f'from base64 import b64decode\n\nk = b64decode("{b64(KEY)}")\n', B64),
    "from_import_with_alias": (f'from base64 import b64decode as dec\n\nk = dec("{b64(KEY)}")\n', B64),
    "from_import_urlsafe": (f'from base64 import urlsafe_b64decode\n\nk = urlsafe_b64decode("{b64(KEY, True)}")\n', B64),
    "from_import_standard": (f'from base64 import standard_b64decode as s\n\nk = s("{b64(KEY)}")\n', B64),
    "from_import_decodebytes": (f'from base64 import decodebytes\n\nk = decodebytes(b"{b64(KEY)}")\n', B64),
    "from_import_among_others": (f'from base64 import b64encode, b64decode\n\nk = b64decode("{b64(KEY)}")\n', B64),
    "module_alias": (f'import base64 as b\n\nk = b.b64decode("{b64(KEY)}")\n', B64),
    "module_alias_urlsafe": (f'import base64 as b\n\nk = b.urlsafe_b64decode("{b64(KEY, True)}")\n', B64),
    "binascii_alias_unhexlify": (f'import binascii as x\n\nk = x.unhexlify("{hexed(KEY)}")\n', HEX),
    "binascii_alias_a2b_hex": (f'import binascii as x\n\nk = x.a2b_hex("{hexed(KEY)}")\n', HEX),
    "binascii_alias_a2b_base64": (f'import binascii as x\n\nk = x.a2b_base64("{b64(KEY)}")\n', B64),
    "from_binascii": (f'from binascii import unhexlify, a2b_hex, a2b_base64\n\nk = a2b_hex("{hexed(KEY)}")\n', HEX),
    "from_binascii_a2b_base64": (f'from binascii import a2b_base64\n\nk = a2b_base64("{b64(KEY)}")\n', B64),
    "codecs_alias": (f'import codecs as c\n\nk = c.decode("{rot13(KEY)}", "rot13")\n', "rot13-decoded from a literal"),
    "from_codecs": (f'from codecs import decode\n\nk = decode("{rot13(KEY)}", "rot13")\n', "rot13-decoded from a literal"),
    "from_codecs_with_alias": (f'from codecs import decode as d\n\nk = d("{hexed(KEY)}", "hex")\n', HEX),
    "keyword_validate": (f'import base64\n\nk = base64.b64decode("{b64(KEY)}", validate=True)\n', B64),
    "keyword_altchars_none": (f'import base64\n\nk = base64.b64decode("{b64(KEY)}", altchars=None, validate=False)\n', B64),
    "positional_altchars_none": (f'import base64\n\nk = base64.b64decode("{b64(KEY)}", None, True)\n', B64),
    "keyword_data": (f'import base64\n\nk = base64.b64decode(s="{b64(KEY)}")\n', B64),
    "codecs_keyword_encoding": (f'import codecs\n\nk = codecs.decode("{rot13(KEY)}", encoding="rot13", errors="strict")\n',
                                "rot13-decoded from a literal"),
    "codecs_rot_13": (f'import codecs\n\nk = codecs.decode("{rot13(KEY)}", "rot_13")\n', "rot13-decoded from a literal"),
    "codecs_rot_13_dashed_upper": (f'import codecs\n\nk = codecs.decode("{rot13(KEY)}", "ROT-13")\n', "rot13-decoded from a literal"),
    "codecs_base64_codec": (f'import codecs\n\nk = codecs.decode(b"{b64(KEY)}", "base64_codec")\n', B64),
    "codecs_hex_codec": (f'import codecs\n\nk = codecs.decode("{hexed(KEY)}", "hex_codec")\n', HEX),
    "codecs_hex_with_errors": (f'import codecs\n\nk = codecs.decode("{hexed(KEY)}", "hex", "strict")\n', HEX),
}


@pytest.mark.parametrize("name", sorted(PY_SPELLINGS))
def test_python_decoder_imported_by_name_or_alias_is_a_block(repo, name):
    src, how = PY_SPELLINGS[name]
    assert block_with(both(repo, "src/k.py", src), line_of(src, "k ="), how)


def test_python_decoder_alias_works_in_a_function_and_a_nested_call(repo):
    src = f'from base64 import b64decode as dec\n\n\ndef load():\n    return dict(key=dec("{b64(KEY)}").decode())\n'
    assert block_with(both(repo, "src/k.py", src), 5, B64)


JS_SPELLINGS = {
    "buffer_base64url": (f'const k = Buffer.from("{b64(KEY, True)}", "base64url").toString();\n', B64),
    "buffer_upper_base64": (f'const k = Buffer.from("{b64(KEY)}", "BASE64").toString();\n', B64),
    "buffer_mixed_case_hex": (f'const k = Buffer.from("{hexed(KEY)}", "Hex").toString();\n', HEX),
    "window_atob": (f'const k = window.atob("{b64(KEY)}");\n', B64),
    "global_this_atob": (f'const k = globalThis.atob("{b64(KEY)}");\n', B64),
    "self_atob": (f'const k = self.atob("{b64(KEY)}");\n', B64),
    "global_buffer": (f'const k = global.Buffer.from("{hexed(KEY)}", "hex").toString();\n', HEX),
}


@pytest.mark.parametrize("path", ["src/k.js", "src/k.ts"])
@pytest.mark.parametrize("name", sorted(JS_SPELLINGS))
def test_js_other_spellings_of_the_decoders_are_a_block(repo, path, name):
    src, how = JS_SPELLINGS[name]
    assert block_with(both(repo, path, src), 1, how)


GO_ALIAS_HEAD = 'package shop\n\nimport (\n\tb64 "encoding/base64"\n\thx "encoding/hex"\n)\n\n'
RAW = b64(KEY).rstrip("=")
RAW_URL = b64(KEY, True).rstrip("=")
GO_SPELLINGS = {
    "raw_std_encoding": (GO_HEAD + f'func k() []byte {{\n\tb, _ := base64.RawStdEncoding.DecodeString("{RAW}")\n\treturn b\n}}\n', B64),
    "raw_url_encoding": (GO_HEAD + f'func k() []byte {{\n\tb, _ := base64.RawURLEncoding.DecodeString("{RAW_URL}")\n\treturn b\n}}\n', B64),
    "aliased_base64_import": (GO_ALIAS_HEAD + f'func k() []byte {{\n\tb, _ := b64.StdEncoding.DecodeString("{b64(KEY)}")\n\treturn b\n}}\n', B64),
    "aliased_raw_import": (GO_ALIAS_HEAD + f'func k() []byte {{\n\tb, _ := b64.RawURLEncoding.DecodeString("{RAW_URL}")\n\treturn b\n}}\n', B64),
    "aliased_hex_import": (GO_ALIAS_HEAD + f'func k() []byte {{\n\tb, _ := hx.DecodeString("{hexed(KEY)}")\n\treturn b\n}}\n', HEX),
    "single_line_import_alias": ('package shop\n\nimport b64 "encoding/base64"\n\n'
                                 f'func k() []byte {{\n\tb, _ := b64.StdEncoding.DecodeString("{b64(KEY)}")\n\treturn b\n}}\n', B64),
}


@pytest.mark.parametrize("name", sorted(GO_SPELLINGS))
def test_go_other_spellings_of_the_decoders_are_a_block(repo, name):
    src, how = GO_SPELLINGS[name]
    assert block_with(both(repo, "shop/k.go", src), line_of(src, "Decode"), how)


GO_TAIL = f'func k() []byte {{\n\tb, _ := %s.StdEncoding.DecodeString("{b64(KEY)}")\n\treturn b\n}}\n'
# a name is a decoder only when the file brings it from the standard library: nothing else is followed
NOT_DECODERS = {
    "py_from_another_module": ("src/k.py", f'from mylib import b64decode\n\nk = b64decode("{b64(KEY)}")\n'),
    "py_from_another_module_with_alias": ("src/k.py", f'from mylib import b64decode as base64_decode\n\nk = base64_decode("{b64(KEY)}")\n'),
    "py_another_module_under_the_name": ("src/k.py", f'import base64x as base64\n\nk = base64.b64decode("{b64(KEY)}")\n'),
    "py_another_module_aliased": ("src/k.py", f'import mylib as b\n\nk = b.b64decode("{b64(KEY)}")\n'),
    "py_codecs_alias_of_another_module": ("src/k.py", f'from mylib import decode\n\nk = decode("{rot13(KEY)}", "rot13")\n'),
    "py_local_def": ("src/k.py", f'def b64decode(x):\n    return x\n\n\nk = b64decode("{b64(KEY)}")\n'),
    "py_local_def_after_the_import": ("src/k.py", 'from base64 import b64decode\n\n\ndef b64decode(x):\n    return x\n\n\n'
                                      f'k = b64decode("{b64(KEY)}")\n'),
    "py_shadowed_by_assignment": ("src/k.py", f'from base64 import b64decode\n\nb64decode = lambda s: s\nk = b64decode("{b64(KEY)}")\n'),
    "py_module_name_shadowed": ("src/k.py", f'import base64\n\nbase64 = object()\nk = base64.b64decode("{b64(KEY)}")\n'),
    "py_parameter_named_like_it": ("src/k.py", f'from base64 import b64decode\n\n\ndef load(b64decode):\n    return b64decode("{b64(KEY)}")\n'),
    "py_bare_name_never_imported": ("src/k.py", f'k = b64decode("{b64(KEY)}")\n'),
    "py_star_import_is_not_followed": ("src/k.py", f'from base64 import *\n\nk = b64decode("{b64(KEY)}")\n'),
    "py_relative_import": ("src/k.py", f'from .base64 import b64decode\n\nk = b64decode("{b64(KEY)}")\n'),
    "py_getattr": ("src/k.py", f'import base64\n\nk = getattr(base64, "b64decode")("{b64(KEY)}")\n'),
    "py_dynamic_import": ("src/k.py", f'k = __import__("base64").b64decode("{b64(KEY)}")\n'),
    "py_custom_alphabet": ("src/k.py", f'import base64\n\nk = base64.b64decode("{b64(KEY)}", altchars="-_")\n'),
    "py_codec_that_is_not_ours": ("src/k.py", f'import codecs\n\nk = codecs.decode("{b64(KEY)}", "utf-8")\n'),
    "py_unknown_keyword": ("src/k.py", f'import base64\n\nk = base64.b64decode("{b64(KEY)}", mode="x")\n'),
    "js_method_of_another_object": ("src/k.js", f'const k = other.atob("{b64(KEY)}");\n'),
    "js_local_function_named_atob": ("src/k.js", f'function atob(s) {{ return s; }}\nconst k = atob("{b64(KEY)}");\n'),
    "js_local_const_named_buffer": ("src/k.js", f'const Buffer = require("my-buffer");\nconst k = Buffer.from("{hexed(KEY)}", "hex");\n'),
    "js_encoding_that_is_not_ours": ("src/k.js", f'const k = Buffer.from("{b64(KEY)}", "latin1");\n'),
    "go_package_name_of_another_module": ("shop/k.go", 'package shop\n\nimport base64 "example.com/mylib/base64"\n\n' + GO_TAIL % "base64"),
    "go_alias_of_another_module": ("shop/k.go", 'package shop\n\nimport b64 "example.com/mylib/codec"\n\n' + GO_TAIL % "b64"),
    "go_alias_that_is_not_imported_here": ("shop/k.go", 'package shop\n\nimport "encoding/base64"\n\n' + GO_TAIL % "b64"),
    "go_package_name_is_a_variable": ("shop/k.go", 'package shop\n\nimport "encoding/base64"\n\n'
                                      + GO_TAIL.replace("\tb, _", "\tbase64 := newCodec()\n\tb, _") % "base64"),
}


@pytest.mark.parametrize("name", sorted(NOT_DECODERS))
def test_a_name_that_is_not_the_standard_decoder_is_not_one(repo, name):
    path, src = NOT_DECODERS[name]
    assert both(repo, path, src) == []


def test_a_decoder_name_in_another_file_is_not_followed(repo):
    repo.write("src/util.py", "from base64 import b64decode as unwrap\n")
    assert both(repo, "src/k.py", f'from util import unwrap\n\nk = unwrap("{b64(KEY)}")\n') == []


def test_the_gate_looks_for_every_decoder_name_including_the_imported_ones():
    def runs(ext, line):                               # True when the check went on to read the file (ctx is None: it raises)
        fc = SimpleNamespace(ext=ext, path="a" + ext, added={1: line})
        try:
            aigate._encoded_key_findings(None, fc, "", set())
        except AttributeError:
            return True
        return False
    for ext, line in [(".py", 'k = b64decode("x")'), (".py", 'k = x.urlsafe_b64decode("x")'), (".py", 'k = a2b_hex("x")'),
                      (".py", 'k = c.decode("x", "rot_13")'), (".py", 'k = bytes.fromhex("x")'),
                      (".js", 'const k = self.atob("x");'), (".js", 'const k = Buffer.from("x", "hex");'),
                      (".go", 'b, _ := b64.RawURLEncoding.DecodeString("x")')]:
        assert runs(ext, line), line
    for ext, line in [(".py", "import base64"), (".py", 'x = "abc".decode("utf-8")'), (".py", "k = decode(x)"),
                      (".js", "const b = Buffer.alloc(4);"), (".js", "const x = 1;"), (".go", 'import "encoding/hex"'),
                      (".go", "x := hex.EncodeToString(b)")]:
        assert not runs(ext, line), line


# ------------------------------------------------------------------------------------------ the work stays linear

class _Counted(str):
    """A piece that counts how often the assembly engine asks its length."""
    calls = 0

    def __len__(self):
        _Counted.calls += 1
        return super().__len__()


def _assembly_work(n):
    _Counted.calls = 0
    events = [(1, "set_list", "parts", [_Counted("")])] + [(1, "add_list", "parts", [_Counted("")]) for _ in range(n)]
    events += [(1, "join", "parts", "")]
    aigate._assembled_values(events, {1: "x"})
    return _Counted.calls


def test_assembly_work_does_not_grow_with_the_square_of_the_statements():
    small, large = _assembly_work(600), _assembly_work(2400)
    assert large <= 2 * small + 50, (small, large)          # the name is dropped after a fixed number of statements


def test_an_assembled_name_is_dropped_when_a_statement_is_too_far_away():
    tail = "QWERTYUIOPASDFGH"
    near = [(1, "set_str", "k", HEAD), (2, "add_str", "k", tail)]
    assert aigate._assembled_values(near, {1: "a", 2: "b"}) == [(2, KEY)]
    edge = [(1, "set_str", "k", HEAD), (26, "add_str", "k", tail)]
    assert aigate._assembled_values(edge, {1: "a", 26: "b"}) == [(26, KEY)]
    far = [(1, "set_str", "k", HEAD), (27, "add_str", "k", tail)]
    assert aigate._assembled_values(far, {1: "a", 27: "b"}) == []


_REAL_WS = aigate._WS


class _CountingWs:
    n = 0

    def __init__(self, rx):
        self.rx = rx

    def match(self, *args):
        _CountingWs.n += 1
        return self.rx.match(*args)


def _chain_work(monkeypatch, pieces, prefix):
    src = prefix + "const s = " + " + ".join(['"ab"'] * pieces) + ";\n"
    lx = aigate.lex_text(src, aigate.language_for("a.js").lex)
    fc = SimpleNamespace(path="a.js", added={i: ln for i, ln in enumerate(src.split("\n"), 1)})
    monkeypatch.setattr(aigate, "_WS", _CountingWs(_REAL_WS))
    _CountingWs.n = 0
    aigate._js_go_decoder_hits(fc, lx, aigate.DECODER_CALLS["js"], False, set())
    return _CountingWs.n


@pytest.mark.parametrize("prefix", ["[].reverse();\n", 'atob("QQ==");\n', 'const t = ["x"].reverse();\n'])
def test_a_long_literal_chain_is_read_once_not_once_per_piece(monkeypatch, prefix):
    small, large = _chain_work(monkeypatch, 400, prefix), _chain_work(monkeypatch, 1600, prefix)
    assert large <= 5 * small, (small, large)               # 4 times the pieces, about 4 times the work (a square would be 16)


def _big_file(name):
    """The generated source of one shape, made inside the test so no large text is a parameter (it would be in the test id)."""
    return {
        "python_30000_plus_equals": ("src/a.py", 'k = ""\n' + 'k += ""\n' * 30_000),
        "python_30000_appends": ("src/a.py", "parts = []\n" + 'parts.append("")\n' * 30_000 + 'k = "".join(parts)\n'),
        "python_60000_on_one_line": ("src/a.py", 'k = ""\n' + 'k += "";' * 60_000 + "\n"),
        "js_30000_plus_equals": ("src/a.js", 'let k = "";\n' + 'k+="";\n' * 30_000),
        "js_decoder_and_a_2000_piece_chain": ("src/a.js", 'atob("QQ==");\nconst s = ' + " + ".join(['"ab"'] * 2000) + ";\n"),
        "js_reverse_and_an_8000_piece_chain": ("src/a.js", "[].reverse();\nconst s = " + " + ".join(['"ab"'] * 8000) + ";\n"),
    }[name]


@pytest.mark.serial  # asserts a wall-clock limit; CPU contention from parallel tests would make it flaky
@pytest.mark.parametrize("name", ["python_30000_plus_equals", "python_30000_appends", "python_60000_on_one_line",
                                  "js_30000_plus_equals", "js_decoder_and_a_2000_piece_chain",
                                  "js_reverse_and_an_8000_piece_chain"])
def test_the_scans_of_big_generated_files_finish_in_seconds(repo, name):
    import time
    path, text = _big_file(name)
    staged(repo, path, text)
    start = time.time()
    res = repo.check("--no-cache")
    seconds = time.time() - start
    print(f"{name}: {seconds:.1f} s")
    assert seconds < 15, name
    assert not find(res, "secrets.hardcoded")
