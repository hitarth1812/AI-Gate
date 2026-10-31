import json
import os
import subprocess
import sys

from conftest import SCRIPT, find

CLEAN = "import json\n\n\ndef dump(x):\n    return json.dumps(x)\n"


def twice(repo, *args):
    """Run the same check twice; return (second result, second run's profile counters)."""
    out = None
    for _ in range(2):
        proc = subprocess.run([sys.executable, str(SCRIPT), "check", "--format", "json", *args], cwd=repo.path,
                              env={**repo.env, "AIGATE_PROFILE": "1"}, text=True, capture_output=True)
        assert proc.returncode in (0, 1), proc.stderr
        line = [ln for ln in proc.stderr.splitlines() if ln.startswith("aigate-profile: ")][0]
        out = json.loads(proc.stdout), json.loads(line[len("aigate-profile: "):])
    return out


def cache_file(repo):
    return os.path.join(repo.env["AIGATE_CACHE_DIR"], "results-v2.json")


def staged_clean(repo, config=None):
    if config is not None:
        repo.write(".aigate/lite.toml", config)
    repo.write("app/ok.py", CLEAN)
    repo.add()


def test_result_cache_is_off_by_default(repo):
    staged_clean(repo)
    res, prof = twice(repo)
    assert res["verdict"] == "pass" and prof.get("cache_hits", 0) == 0 and not os.path.exists(cache_file(repo))


def test_cache_flag_turns_it_on(repo):
    staged_clean(repo)
    res, prof = twice(repo, "--cache")
    assert res["verdict"] == "pass" and prof["cache_hits"] >= 1 and os.path.exists(cache_file(repo))


def test_config_key_turns_it_on(repo):
    staged_clean(repo, "[gate]\ncache = true\n")
    res, prof = twice(repo)
    assert prof["cache_hits"] >= 1 and not find(res, "gate.config_invalid")


def test_config_false_is_the_default_and_the_flag_still_wins(repo):
    staged_clean(repo, "[gate]\ncache = false\n")
    assert twice(repo)[1].get("cache_hits", 0) == 0 and not os.path.exists(cache_file(repo))
    assert twice(repo, "--cache")[1]["cache_hits"] >= 1


def test_no_cache_beats_flag_and_config(repo):
    staged_clean(repo, "[gate]\ncache = true\n")
    _, prof = twice(repo, "--cache", "--no-cache")
    assert prof.get("cache_hits", 0) == 0 and not os.path.exists(cache_file(repo))


def test_non_boolean_config_value_is_reported_and_leaves_it_off(repo):
    staged_clean(repo, '[gate]\ncache = "yes"\n')
    res, prof = twice(repo)
    assert [f["message"] for f in find(res, "gate.config_invalid")] == ["gate.cache must be true or false"]
    assert prof.get("cache_hits", 0) == 0 and not os.path.exists(cache_file(repo))
