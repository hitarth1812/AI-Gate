"""0.5.1 Part C1: started by a Python older than 3.11, aigate-lite says so in one line and exits 2, instead of a
SyntaxError or an ImportError. The check must sit above anything an old interpreter cannot parse."""
import ast
import os
import re
import shutil
import subprocess

import pytest

from conftest import SCRIPT

OLD = ("3.10", "3.9", "3.8")


def header():
    """The file up to and including the version check (the first sys.exit(2))."""
    text = SCRIPT.read_text(encoding="utf-8")
    end = text.index("sys.exit(2)") + len("sys.exit(2)")
    return text[:end] + "\n"


def test_the_version_check_is_the_first_statement_after_import_sys():
    tree = ast.parse(header())
    body = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    assert isinstance(body[0], ast.Import) and [a.name for a in body[0].names] == ["sys"]
    assert isinstance(body[1], ast.If) and "version_info" in ast.unparse(body[1].test)


@pytest.mark.parametrize("version", [(3, 8), (3, 6)])
def test_the_header_parses_with_an_old_grammar(version):
    ast.parse(header(), feature_version=version)


def old_python():
    """A real interpreter older than 3.11 on this machine, as an argv prefix, or None."""
    for v in OLD:
        exe = shutil.which(f"python{v}")
        if exe:
            return [exe]
    if os.name == "nt" and shutil.which("py"):
        listed = subprocess.run(["py", "--list"], capture_output=True, text=True).stdout
        for v in OLD:
            if re.search(rf"-V:{re.escape(v)}\b", listed):
                return ["py", f"-{v}"]
    return None


def test_a_real_old_python_gets_one_line_and_exit_2():
    argv = old_python()
    if argv is None:
        pytest.skip("no Python older than 3.11 on this machine")
    for args in ([str(SCRIPT), "--version"], [str(SCRIPT), "check"]):
        proc = subprocess.run(argv + args, capture_output=True, text=True)
        assert proc.returncode == 2, proc.stderr
        lines = proc.stderr.strip().splitlines()
        assert len(lines) == 1 and re.match(r"aigate-lite needs Python 3\.11\+; this is 3\.\d+\. Try: ", lines[0])
        assert "Traceback" not in proc.stderr and "SyntaxError" not in proc.stderr
