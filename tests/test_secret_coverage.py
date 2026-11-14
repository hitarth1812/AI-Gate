"""0.5.2 round 1 (D1): a provider key is never missed because of its line length.

A line longer than the per-line limit (4,000 characters) still gets the provider-key check, which uses bounded patterns. A file
over 1 MB is NOT a gap: the key is found at 1.1, 2.1 and 5 MB (tested in the regression guard below)."""
import pytest

from conftest import find

AWS = "AKIA" + "qwertyuiopasdfgh".upper()


def stage(repo, rel, text):
    repo.write(rel, text)
    repo.add()
    return repo


@pytest.mark.parametrize("who_env", [{}, {"CLAUDECODE": "1"}], ids=["person", "agent"])
def test_a_provider_key_on_a_very_long_line_is_found(repo, who_env):
    line = "var bundle = '" + "x" * 5000 + "';" + "var k = '" + AWS + "';\n"
    stage(repo, "bundle.js", line)
    res = repo.check("--offline", env=who_env) if who_env else repo.check("--offline")
    hits = find(res, "secrets.hardcoded")
    assert hits and hits[0]["path"] == "bundle.js" and not hits[0]["allowed"], res["findings"]


def test_a_key_in_a_file_over_one_megabyte_is_still_a_block(repo):
    # the size of a file does not stop the provider-key check (checked on 1.1, 2.1 and 5 MB): kept as a regression guard
    big = "KEY = '" + AWS + "'\n" + "print('pad')\n" * 100_000
    stage(repo, "big.py", big)
    res = repo.check("--offline")
    assert [f for f in find(res, "secrets.hardcoded") if not f["allowed"]], res["findings"]


def test_a_small_file_with_a_key_is_still_a_block(repo):
    stage(repo, "app.py", "KEY = '" + AWS + "'\n")
    res = repo.check("--offline")
    assert [f for f in find(res, "secrets.hardcoded") if not f["allowed"]]
    assert not find(res, "secrets.unscanned")
