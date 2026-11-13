"""The evaluation harness (eval/run.py) and its corpus: structure, hygiene and the scoring rules.
No test here runs the gate over the corpus (that is `py eval/run.py --check`, a CI step); the scoring is tested with
fabricated results so it takes milliseconds."""
import importlib.util
import json
import re
import tomllib


from conftest import ROOT

EVAL = ROOT / "eval"
spec = importlib.util.spec_from_file_location("eval_run", EVAL / "run.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

CASES = ev.load_cases()
HIDDEN = {"​", "‌", "‍", "‎", "‏", "⁠", "﻿", "­", "‪", "‫", "‬",
          "‭", "‮", "⁦", "⁧", "⁨", "⁩"}


def test_the_corpus_meets_the_v1_minimum_counts():
    by = {}
    for c in CASES:
        by[c.family] = by.get(c.family, 0) + 1
    assert by["real"] >= 60 and by["seeded"] >= 100 and by["adversarial"] >= 40 and by["clean"] >= 10, by


def test_holdout_is_a_stable_fifth_of_each_family_and_not_empty():
    held = [c for c in CASES if c.holdout]
    assert 0.12 <= len(held) / len(CASES) <= 0.28 and {c.family for c in held} >= {"real", "seeded", "adversarial", "clean"}
    assert all("/holdout/" in str(c.path).replace("\\", "/") for c in held)


def test_every_case_is_well_formed():
    seen = set()
    for c in CASES:
        m = c.meta
        assert c.id == c.path.name and c.id not in seen, c.id
        seen.add(c.id)
        assert m["source"] in ("real", "seeded", "adversarial", "clean") and m.get("origin") and m["language"]
        assert c.who in ("person", "agent", "both") and m.get("label_status") in ("frozen", "proposed")
        if c.family == "clean":
            assert re.fullmatch(r"[0-9a-f]{40}", m["sha"]) and m["repo"].startswith("https://github.com/")
            continue
        assert c.mode in ("staged", "range", "merge", "detached", "empty")
        for spec_ in c.must_fire + c.must_not_fire:
            assert spec_["rule"] and spec_["path"], c.id
        for spec_ in c.must_fire:
            for k in ("action", "agent_action"):
                assert spec_.get(k, "warn") in ev.RANK, (c.id, k)
            if spec_.get("line") is not None:
                assert spec_["line"] >= 1


def test_labelled_paths_exist_in_the_case(tmp_path):
    for c in CASES:
        if c.family == "clean":
            continue
        files = {p.relative_to(c.path / d).as_posix() for d in ("before", "after") for p in (c.path / d).rglob("*") if p.is_file()}
        generated = {g["path"] for g in c.meta.get("generate", [])} | {r["to"] for r in c.meta.get("rename", [])} \
            | {s["path"] for s in c.meta.get("symlink", [])}
        for spec_ in c.must_fire + c.must_not_fire:
            p = spec_["path"]
            assert p.startswith("(") or p in files or p in generated or any(f.startswith(p) for f in generated), (c.id, p)


def test_no_provider_secret_and_no_invisible_character_is_stored_in_the_corpus():
    for p in list((EVAL / "corpus").rglob("*")) + list((EVAL / "holdout").rglob("*")):
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        assert not (HIDDEN & set(text)), p
        for secret in ev.SUBST.values():
            if len(secret) >= 12 and "@@" not in secret and "\n" not in secret:
                assert secret not in text, (p, "stored a provider-shaped value; write the @@NAME@@ placeholder")


def test_placeholders_expand_to_what_the_gate_recognises():
    assert ev.fill(b"k=@@AWS@@").decode() == "k=AKIAQWERTYUIOPASDFGH"
    assert b"BEGIN RSA PRIVATE KEY" in ev.fill(b"@@PEMBEGIN@@") and len(ev.fill(b"@@PEMBODY@@").splitlines()) == 6
    assert ev.fill("@@ZWSP@@".encode()) == "​".encode()


# ---------------------------------------------------------------------------------------------------- scoring

class FakeCase:
    def __init__(self, fire=(), notfire=(), who="both", family="seeded", contested=False):
        self.id, self.family, self.who, self.contested = "c1", family, who, contested
        self.must_fire, self.must_not_fire, self.holdout = list(fire), list(notfire), False


def res(*findings, crash=None, verdict="fix"):
    return {"findings": [{"rule": r, "path": p, "line": ln, "action": a, "allowed": False, "message": "m"} for r, p, ln, a in findings],
            "crash": crash, "verdict": verdict, "ms": 5.0}


def kinds(obs):
    return sorted(f.split("|", 2)[2].split(":", 1)[0] for f in obs["fail"])


def test_a_reported_finding_with_the_right_action_is_a_true_positive():
    c = FakeCase([{"rule": "r", "path": "a.py", "line": 3, "action": "fix"}])
    o = ev.score_one(c, "person", res(("r", "a.py", 3, "fix")))
    assert o["fail"] == [] and o["rules"]["r"]["tp"] == 1


def test_missing_wrong_line_over_and_under_are_all_failures():
    c = FakeCase([{"rule": "r", "path": "a.py", "line": 3, "action": "fix"}])
    assert kinds(ev.score_one(c, "person", res())) == ["FN"]
    assert kinds(ev.score_one(c, "person", res(("r", "a.py", 4, "fix")))) == ["FN"]
    assert kinds(ev.score_one(c, "person", res(("r", "a.py", 3, "block")))) == ["OVER"]
    assert kinds(ev.score_one(c, "person", res(("r", "a.py", 3, "warn")))) == ["UNDER"]


def test_line_tolerance_and_agent_action():
    c = FakeCase([{"rule": "r", "path": "a.py", "line": 3, "tol": 2, "action": "warn", "agent_action": "fix"}])
    assert ev.score_one(c, "person", res(("r", "a.py", 5, "warn")))["fail"] == []
    assert ev.score_one(c, "agent", res(("r", "a.py", 5, "fix")))["fail"] == []
    assert kinds(ev.score_one(c, "agent", res(("r", "a.py", 5, "warn")))) == ["UNDER"]


def test_a_must_not_fire_label_makes_any_active_finding_a_false_positive():
    c = FakeCase(notfire=[{"rule": "r", "path": "a.py"}], who="person")
    assert kinds(ev.score_one(c, "person", res(("r", "a.py", 1, "warn")))) == ["FP"]
    assert ev.score_one(c, "person", res())["rules"]["r"]["clean"] == 1
    assert ev.score_one(c, "agent", res(("r", "a.py", 1, "warn")))["fail"] == []          # who=person: the label is not applied


def test_unexpected_blocks_crashes_and_gate_errors_are_counted():
    c = FakeCase([{"rule": "r", "path": "a.py"}])
    assert kinds(ev.score_one(c, "person", res(("r", "a.py", 1, "warn"), ("other", "b.py", 1, "block")))) == ["UNEXPECTED_BLOCK"]
    assert kinds(ev.score_one(c, "person", res(crash="no JSON"))) == ["CRASH"]
    assert kinds(ev.score_one(c, "person", res(("r", "a.py", 1, "warn"), ("gate.error", "(gate)", None, "block")))) \
        == ["GATE_ERROR", "UNEXPECTED_BLOCK"]


def test_a_clean_repository_must_not_block_a_person():
    c = FakeCase(family="clean", who="person")
    assert kinds(ev.score_one(c, "person", res(("r", "a.py", 1, "block")))) == ["FALSE_BLOCK"]
    assert ev.score_one(c, "person", res(("r", "a.py", 1, "fix")))["fail"] == []


def test_a_contested_case_is_excluded_from_scoring():
    c = FakeCase([{"rule": "r", "path": "a.py"}], contested=True)
    assert ev.score_one(c, "person", res())["fail"] == []


def test_agent_softer_is_detected_per_finding_by_path_and_by_verdict():
    c = FakeCase()
    person = res(("r", "a.py", 1, "fix"), verdict="fix")
    assert ev.agent_softer(c, person, res(("r", "a.py", 1, "fix"), verdict="fix")) == []
    assert ev.agent_softer(c, person, res(("r", "a.py", 1, "block"), verdict="block")) == []
    assert ev.agent_softer(c, person, res(("other", "a.py", 9, "block"), verdict="block")) == []     # a stricter finding on the same path
    softer = ev.agent_softer(c, person, res(("r", "a.py", 1, "warn"), verdict="warn"))
    assert len(softer) == 2 and all("AGENT_SOFTER" in f for f in softer)
    assert any("verdict" in f for f in softer)
    assert ev.agent_softer(c, person, res(("r", "b.py", 1, "fix"), verdict="fix"))               # nothing on a.py: softer


def test_info_only_and_allowed_findings_are_not_active():
    f = {"rule": "r", "path": "a.py", "line": 1, "action": "warn", "allowed": "info only"}
    assert not ev.active(f) and not ev.active({**f, "allowed": False, "action": "pass"}) and ev.active({**f, "allowed": False})


def test_summarize_builds_per_rule_precision_and_recall():
    c = FakeCase([{"rule": "r", "path": "a.py", "line": 1}, {"rule": "r", "path": "b.py", "line": 1}])
    c.id = "c1"
    results = {("c1", "person"): res(("r", "a.py", 1, "fix")), ("c1", "agent"): res(("r", "a.py", 1, "fix"))}
    s = ev.summarize([c], results)
    assert s["rules"]["r"]["tp"] == 2 and s["rules"]["r"]["fn"] == 2 and s["rules"]["r"]["recall"] == 0.5
    assert s["overall"]["fn"] == 2 and s["total_cases"] == 1


# ---------------------------------------------------------------------------------------------------- ratchet

def board(failing, holdout_failing=()):
    return ({"failing": sorted(failing), "skipped": [], "overall": {"runtime_ms_p95": 100.0}, "total_cases": 1},
            {"failing": list(holdout_failing), "total_cases": 1, "overall": {}})


def test_check_fails_on_a_new_failure_and_passes_on_known_ones(capsys):
    ratchet = {"failing": ["a|person|FN:r@x:1"], "holdout_failing_count": 0, "best": {"runtime_ms_p95": 100.0}, "cases": ["a"]}
    train, hold = board(["a|person|FN:r@x:1"])
    assert ev.check(ratchet, train, hold, False, True) == 0
    train, hold = board(["a|person|FN:r@x:1", "b|person|CRASH:boom"])
    assert ev.check(ratchet, train, hold, False, True) == 1 and "REGRESSION" in capsys.readouterr().out
    train, hold = board(["a|person|FN:r@x:1"], holdout_failing=["h|person|FN:r@x:1"])
    assert ev.check(ratchet, train, hold, False, True) == 1


def test_an_improvement_is_not_a_regression_and_perf_is_only_checked_on_request(capsys):
    ratchet = {"failing": ["a|person|FN:r@x:1"], "holdout_failing_count": 0, "best": {"runtime_ms_p95": 100.0}, "cases": ["a"]}
    train, hold = board([])
    assert ev.check(ratchet, train, hold, False, True) == 0
    train["overall"]["runtime_ms_p95"] = 200.0
    assert ev.check(ratchet, train, hold, False, True) == 0 and ev.check(ratchet, train, hold, True, True) == 1


def test_update_ratchet_refuses_a_new_failure_for_a_known_case(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ev, "RATCHET", tmp_path / "ratchet.json")
    monkeypatch.setattr(ev, "case_ids", lambda s: ["a", "b"])
    ratchet = {"failing": ["a|person|FN:r@x:1"], "holdout_failing_count": 0, "cases": ["a"],
               "best": {k: 0 for k in ("false_block_clean", "unexpected_block", "crashes", "gate_errors", "agent_softer")} | {"runtime_ms_p50": 1, "runtime_ms_p95": 1}}
    s = {"failing": ["a|person|FN:r@x:1", "a|person|CRASH:new"], "overall": {**ratchet["best"]}}
    assert ev.update_ratchet(ratchet, s, {"failing": [], "total_cases": 0}) == 1
    s = {"failing": ["b|person|FN:r@y:1"], "overall": {**ratchet["best"]}}          # a failure of a NEW case is accepted
    assert ev.update_ratchet(ratchet, s, {"failing": [], "total_cases": 0}) == 0
    saved = json.loads((tmp_path / "ratchet.json").read_text(encoding="utf-8"))
    assert saved["cases"] == ["a", "b"] and saved["failing"] == ["b|person|FN:r@y:1"]


def test_the_corpus_case_files_parse_as_toml():
    for p in list((EVAL / "corpus").glob("*/case.toml")) + list((EVAL / "holdout").glob("*/case.toml")):
        tomllib.loads(p.read_text(encoding="utf-8"))
