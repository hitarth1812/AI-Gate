"""0.5.3 round 2 (Part 3, fuzzing): property tests for the parsers that read untrusted text. A parser must never crash with anything
but its own documented error, must never hang, and must stay inside a memory bound. They need `hypothesis` (a dev-only dependency,
pinned in requirements-dev.txt; the tool itself has none) and are skipped when it is not installed."""
import time
import tracemalloc

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings, strategies as st   # noqa: E402

from conftest import aigate   # noqa: E402

LIMIT_S = 5.0                              # one call may not take longer than this (the tool's own per-file budget is 2 s)
CODEISH = st.text(alphabet=st.sampled_from(list("(){}[]<>\"'`/*#\\\n\t =;:,.+-_$@!?|&%^~abcxyz019")), max_size=2000)
ANYTEXT = st.text(max_size=2000)
SETTINGS = settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow], derandomize=True)


def timed(fn, *args):
    t0 = time.perf_counter()
    out = fn(*args)
    assert time.perf_counter() - t0 < LIMIT_S, "a parser took longer than the time limit"
    return out


@pytest.mark.parametrize("path", ["a.go", "a.js", "a.ts", "a.tsx", "a.py", "a.sh", "Dockerfile", "a.yml"])
@SETTINGS
@given(text=st.one_of(CODEISH, ANYTEXT))
def test_the_lexer_never_crashes_or_hangs(path, text):
    spec = aigate.language_for(path)
    if spec is None or getattr(spec, "lex", None) is None:
        return
    try:
        lx = timed(aigate.lex_text, text, spec.lex)
    except aigate.LexError:
        return                                            # its documented way of saying "fall back to the line-based checks"
    assert lx is not None


@SETTINGS
@given(text=st.one_of(CODEISH, ANYTEXT))
def test_the_diff_parser_never_crashes(text):
    assert isinstance(timed(aigate.parse_patch, text), list)


@SETTINGS
@given(body=CODEISH, header=st.sampled_from(["", "diff --git a/x b/x\n", "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,2 +1,3 @@\n"]))
def test_the_diff_parser_survives_garbage_after_a_valid_header(body, header):
    assert isinstance(timed(aigate.parse_patch, header + body), list)


@SETTINGS
@given(text=st.one_of(CODEISH, ANYTEXT))
def test_the_symbol_indexes_never_crash(text):
    timed(aigate.py_symbols, text)                        # None for a file that does not parse
    timed(aigate.go_symbols, text)
    timed(aigate.js_symbols, text, "x.ts")


JSON_LEAF = st.one_of(st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False), st.text(max_size=20))
JSON_TREE = st.recursive(JSON_LEAF, lambda kids: st.one_of(st.lists(kids, max_size=4), st.dictionaries(st.text(max_size=12), kids, max_size=4)),
                         max_leaves=25)


@SETTINGS
@given(raw=st.dictionaries(st.sampled_from(["version", "preset", "rules", "gate", "paths", "limits", "deps", "allow", "commands",
                                            "provenance", "scope", "review", "nonsense"]), JSON_TREE, max_size=8))
def test_the_config_merge_never_crashes_and_always_returns_a_complete_config(raw):
    problems: list = []
    cfg = aigate.merge_config(raw, problems)
    assert isinstance(cfg, dict) and {"gate", "paths", "limits", "rules", "allow"} <= set(cfg)
    assert all(isinstance(p, str) for p in problems)


@SETTINGS
@given(text=ANYTEXT)
def test_a_garbage_config_file_is_a_problem_not_a_crash(text, tmp_path_factory):
    import subprocess
    repo_dir = tmp_path_factory.mktemp("cfg")
    subprocess.run(["git", "init", "-q", str(repo_dir)], check=True)
    (repo_dir / ".aigate").mkdir()
    (repo_dir / ".aigate" / "lite.toml").write_text(text, encoding="utf-8", errors="replace")
    repo = aigate.Repo(str(repo_dir))
    cfg = aigate.load_config(repo, "worktree")
    assert cfg is not None and hasattr(cfg, "problems")


# ---- a deterministic memory and time bound on the pathological shapes fuzzing finds first
PATHOLOGICAL = {
    "nested brackets": "(" * 20000 + ")" * 20000,
    "unterminated string": "'" + "a" * 100000,
    "many quotes": "\"'" * 50000,
    "long comment": "/*" + "x" * 150000,
    "many lines": "x = 1\n" * 40000,
    "deep braces": "{" * 5000 + "}" * 5000,
}


@pytest.mark.parametrize("name", sorted(PATHOLOGICAL))
@pytest.mark.parametrize("path", ["a.go", "a.ts", "a.py"])
def test_pathological_input_stays_inside_the_time_and_memory_bound(name, path):
    text = PATHOLOGICAL[name]
    spec = aigate.language_for(path)
    tracemalloc.start()
    try:
        try:
            if spec is not None:                           # Python is read with ast, not the generic lexer
                timed(aigate.lex_text, text, spec.lex)
        except aigate.LexError:
            pass
        timed(aigate.py_symbols if path.endswith(".py") else aigate.go_symbols if path.endswith(".go") else aigate.js_symbols, text,
              *(() if path.endswith((".py", ".go")) else (path,)))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 400 * 1024 * 1024, f"{name} in {path}: peak {peak // 1024 // 1024} MB"
