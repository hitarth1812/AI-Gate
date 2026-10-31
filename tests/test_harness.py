import codecs
import os

from conftest import child_encoding, decode_output

MIDDOT_CP1252 = b"a \xb7 b"                         # what the text report writes for "·" on a cp1252 pipe


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    return {**env, **extra}


def same(a, b):
    return codecs.lookup(a).name == codecs.lookup(b).name


def test_output_is_decoded_with_the_childs_pythonioencoding():
    enc = child_encoding(clean_env(PYTHONIOENCODING="cp1252"))
    assert same(enc, "cp1252") and decode_output(MIDDOT_CP1252, enc) == "a · b"


def test_encoding_comes_from_the_child_not_the_parent():
    assert same(child_encoding(clean_env(PYTHONUTF8="1")), "utf-8")
    assert same(child_encoding(clean_env(PYTHONUTF8="1", PYTHONIOENCODING="cp1252")), "cp1252")
    assert same(child_encoding(clean_env(PYTHONIOENCODING="cp1252:strict")), "cp1252")
    assert child_encoding(clean_env(PYTHONIOENCODING=":replace")) == child_encoding(clean_env())


def test_undecodable_bytes_never_raise():
    assert decode_output(MIDDOT_CP1252, "utf-8") == "a � b"
    assert decode_output(None, "utf-8") is None


def test_newlines_are_translated_like_text_mode():
    assert decode_output(b"a\r\nb\rc\n", "utf-8") == "a\nb\nc\n"


def test_run_survives_cp1252_output_whatever_the_parent_uses(repo):
    repo.write("a.py", "eval(x)\n")
    repo.add()
    proc = repo.run("check", "--color", "never", env={"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "1"})
    assert "commit stopped" in proc.stdout and "�" not in proc.stdout and "\r" not in proc.stdout
