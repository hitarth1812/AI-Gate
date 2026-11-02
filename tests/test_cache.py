import json
import os
import shutil
import sys

from conftest import SCRIPT, find

AWS = "AKIA" + "QWERTYUIOPASDFGH"                 # split so this file is not itself a finding
CLEAN = "import json\n\n\ndef dump(x):\n    return json.dumps(x)\n"


def profiled(repo, *args, env=None, script=None):
    import subprocess
    cmd = [sys.executable, str(script or SCRIPT), "check", "--format", "json",
           *(() if "--no-cache" in args else ("--cache",)), *args]          # the result cache is opt-in
    proc = subprocess.run(cmd, cwd=repo.path, env={**repo.env, "AIGATE_PROFILE": "1", **(env or {})},
                          text=True, capture_output=True)
    assert proc.returncode in (0, 1), proc.stderr
    line = [ln for ln in proc.stderr.splitlines() if ln.startswith("aigate-profile: ")][0]
    return json.loads(proc.stdout), json.loads(line[len("aigate-profile: "):])


def cache_file(repo):
    return os.path.join(repo.env["AIGATE_CACHE_DIR"], "results-v2.json")


def strip(res):
    return [(f["rule"], f["path"], f["line"], f["message"], f["action"], f["fingerprint"]) for f in res["findings"]]


def test_second_run_hits_the_cache_with_identical_findings(repo):
    repo.write("app/keys.py", f'K = "{AWS}"\nx = eval(y)  # type: ignore\n# TODO later\n')
    repo.write("app/ok.py", CLEAN)
    repo.add()
    first, p1 = profiled(repo)
    second, p2 = profiled(repo)
    assert p1.get("cache_hits", 0) == 0 and p2["cache_hits"] >= 4
    assert strip(first) == strip(second) and find(second, "secrets.hardcoded")
    assert AWS not in open(cache_file(repo), encoding="utf-8").read()     # the secret itself is never stored


def test_clean_code_is_cached_as_clean(repo):
    repo.write("app/ok.py", CLEAN)
    repo.add()
    profiled(repo)
    res, prof = profiled(repo)
    assert res["verdict"] == "pass" and not res["findings"] and prof["cache_hits"] >= 1


def test_config_change_invalidates(repo):
    body = "".join(f"    x{i} = {i}\n" for i in range(12))
    repo.write("app/long.py", f"def f():\n{body}    return 0\n")
    repo.add()
    assert not find(profiled(repo)[0], "size.function_length")
    repo.write(".aigate/lite.toml", "[limits]\nfunction_lines = 5\n")
    res, prof = profiled(repo)
    assert find(res, "size.function_length") and prof.get("cache_hits", 0) == 0


def test_tool_version_or_source_change_invalidates(repo, tmp_path):
    repo.write("app/keys.py", f'K = "{AWS}"\n')
    repo.add()
    profiled(repo)
    copy = tmp_path / "aigate_copy.py"
    shutil.copy(SCRIPT, copy)
    with open(copy, "a", encoding="utf-8") as fh:
        fh.write("# a different build\n")
    res, prof = profiled(repo, script=copy)
    assert prof.get("cache_hits", 0) == 0 and find(res, "secrets.hardcoded")
    text = copy.read_text(encoding="utf-8").replace('__version__ = "0.2.0"', '__version__ = "0.2.1"')
    copy.write_text(text, encoding="utf-8")
    assert profiled(repo, script=copy)[1].get("cache_hits", 0) == 0


def test_tampered_cache_cannot_hide_a_finding(repo):
    repo.write("app/keys.py", f'K = "{AWS}"\n')
    repo.add()
    profiled(repo)
    path = cache_file(repo)
    doc = json.load(open(path, encoding="utf-8"))
    forged = dict(doc["entries"])
    forged.update({format(i, "064x"): 9e9 for i in range(50)})   # claim more files were clean, keep the old MAC
    doc["entries"] = forged
    json.dump(doc, open(path, "w", encoding="utf-8"))
    res, prof = profiled(repo)
    assert find(res, "secrets.hardcoded") and res["verdict"] == "block" and prof.get("cache_hits", 0) == 0


def test_replaced_key_or_garbage_is_ignored_without_errors(repo):
    repo.write("app/keys.py", f'K = "{AWS}"\n')
    repo.add()
    profiled(repo)
    key = os.path.join(repo.env["AIGATE_CACHE_DIR"], "results.key")
    with open(key, "wb") as fh:
        fh.write(os.urandom(32))                          # attacker re-keys but cannot re-sign old entries
    res, prof = profiled(repo)
    assert find(res, "secrets.hardcoded") and prof.get("cache_hits", 0) == 0
    with open(cache_file(repo), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    res, _ = profiled(repo)
    assert find(res, "secrets.hardcoded") and not find(res, "gate.error")


def test_same_blob_in_test_and_source_paths_is_keyed_by_path(repo):
    body = 'def test_x():\n    secret = "Zx9!kQ2#vB7$mN4p"\n    assert secret\n'
    repo.write("tests/test_api.py", body)
    repo.write("app/api.py", body)
    repo.add()
    profiled(repo)
    res, prof = profiled(repo)
    actions = {f["path"]: f["action"] for f in find(res, "secrets.generic")}
    assert actions == {"tests/test_api.py": "warn", "app/api.py": "fix"} and prof["cache_hits"] >= 2


def test_no_cache_flag_neither_reads_nor_writes(repo):
    repo.write("app/ok.py", CLEAN)
    repo.add()
    res, prof = profiled(repo, "--no-cache")
    assert not os.path.exists(cache_file(repo)) and prof.get("cache_hits", 0) == 0


def test_cache_files_are_private_and_pruned(repo):
    from conftest import aigate
    repo.write("app/ok.py", CLEAN)
    repo.add()
    profiled(repo)
    if os.name != "nt":                                    # POSIX permissions only
        assert os.stat(cache_file(repo)).st_mode & 0o777 == 0o600
    cache = aigate.ResultCache(repo.env["AIGATE_CACHE_DIR"])
    assert cache.entries
    for k in cache.entries:
        cache.entries[k] -= 15 * 86400                     # older than the 14-day limit
    cache.dirty = True
    cache.save()
    assert aigate.ResultCache(repo.env["AIGATE_CACHE_DIR"]).entries == {}


def test_empty_crlf_and_binary_files(repo):
    repo.write("app/empty.py", "")
    (repo.path / "app" / "crlf.py").write_bytes(b"import os\r\nx = eval(os.environ['X'])\r\n")
    (repo.path / "app" / "blob.bin").write_bytes(b"\0\1\2" + AWS.encode() + b"\0")
    repo.add()
    first, _ = profiled(repo)
    second, prof = profiled(repo)
    assert strip(first) == strip(second) and find(second, "security.eval")[0]["line"] == 2
    assert not [f for f in second["findings"] if f["path"] == "app/blob.bin"]


def test_files_with_findings_are_rechecked_and_never_stored(repo):
    repo.write("app/k.py", f'token = "{AWS}"  # TODO rotate\nx = 1  # TODO {AWS[::-1]}\n')
    repo.add()
    profiled(repo)
    res, prof = profiled(repo)
    assert find(res, "secrets.hardcoded") and len(find(res, "code.todo")) == 2
    assert prof.get("cache_hits", 0) == 2                   # only the clean checks (suppressions, python) hit
    text = open(cache_file(repo), encoding="utf-8").read()
    assert AWS not in text and AWS[::-1] not in text and "TODO" not in text


def test_oversized_or_deeply_nested_cache_is_ignored(repo):
    repo.write("app/ok.py", CLEAN)
    repo.add()
    profiled(repo)
    with open(cache_file(repo), "w", encoding="utf-8") as fh:
        fh.write("[" * 200000)
    res, prof = profiled(repo)
    assert res["verdict"] == "pass" and not find(res, "gate.error") and prof.get("cache_hits", 0) == 0
