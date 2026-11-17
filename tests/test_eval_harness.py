"""The evaluation harness (eval/run.py) and its corpus: structure, hygiene and the scoring rules.
No test here runs the gate over the whole corpus (that is `py eval/run.py --check`, a CI step); the scoring is tested with
fabricated results so it takes milliseconds. The few tests that run the gate run ONE case or one tiny repository."""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tomllib

import pytest

from conftest import ROOT
from test_release_workflow import load_release_check

EVAL = ROOT / "eval"
spec = importlib.util.spec_from_file_location("eval_run", EVAL / "run.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

CASES = ev.load_cases()
HIDDEN = {"\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\u2060", "\ufeff", "\u00ad", "\u202a", "\u202b", "\u202c",
          "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069"}


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
    assert ev.fill(b"k=@@AWS@@").decode() == "k=AKIA" + "qwertyuiopasdfgh".upper()        # split: this file is not itself a finding
    assert b"BEGIN RSA PRIVATE KEY" in ev.fill(b"@@PEMBEGIN@@") and len(ev.fill(b"@@PEMBODY@@").splitlines()) == 6
    assert ev.fill("@@ZWSP@@".encode()) == "\u200b".encode()


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


def test_key_fragment_placeholders_add_up_to_the_aws_key_and_are_not_a_key_alone():
    # round 3: constant-fold cases write "@@AKIA@@" + "@@AKT1@@@@AKT2@@"; no stored piece is, or adds up to, a provider key
    pieces = ["@@AKIA@@", "@@AKT1@@", "@@AKT2@@"]
    assert ev.fill("".join(pieces).encode()) == ev.fill(b"@@AWS@@")
    assert ev.fill(b'"@@AKIA@@" + "@@AKT1@@@@AKT2@@"').decode() == '"AKIA" + "QWERTYUIOPASDFGH"'
    assert all(len(ev.SUBST[p]) < 12 for p in pieces)


def test_the_gate_runs_without_the_packages_installed_on_this_machine(repo, tmp_path):
    # the first nightly run failed on every runner: a snapshot recorded where setuptools was installed never held it, so a
    # runner without setuptools read `import setuptools` as a made-up package. The harness now hides installed packages
    # (`python -S`), so the answer cannot depend on the machine. pytest is installed here (the suite needs it).
    repo.write("a.py", "import pytest\n")
    repo.add()
    empty = tmp_path / "empty.json"
    empty.write_text('{"pypi": {}, "npm": {}, "go": {}}', encoding="utf-8", newline="\n")
    env = ev.run_env("person", tmp_path)
    env["AIGATE_REGISTRY_FIXTURE"] = str(empty)
    res = ev.gate(ROOT / "aigate_lite.py", repo.path, env, ["check", "--offline", "--no-cache", "--format", "json"])
    assert res["crash"] is None
    assert any(f["rule"] == "deps.hallucinated" and "pytest" in f["message"] for f in res["findings"]), res["findings"]


def test_the_python_snapshots_hold_the_build_tools_an_installed_environment_would_hide():
    for case in ("clean-py-click", "clean-py-requests"):
        table = json.loads((EVAL / "snapshots" / f"{case}.json").read_text(encoding="utf-8"))
        assert "setuptools" in table["pypi"], case


# ---------------------------------------------------------------------------------------------------- the holdout, by rule

ID_PREFIXES = re.compile(r"\b(?:adv|seed|real|r3|r4|clean)-[a-z0-9-]+")


def held_case(case_id, rule, path="app/a.py", line=3, family="seeded"):
    c = FakeCase([{"rule": rule, "path": path, "line": line, "action": "fix"}], family=family)
    c.id, c.holdout = case_id, True
    return c


def held_summary():
    """Three holdout cases: two with a missed finding (a hard-coded secret, a claim), one answered too strictly."""
    cases = [held_case("seed-py-secret-hidden-7", "secrets.hardcoded", "billing/payments.py"),
             held_case("real-go-claim-hidden-2", "review.claim_mismatch", "(commit)", None, family="real"),
             held_case("adv-skip-hidden-9", "tests.skip_added", "tests/test_hidden.py", 8, family="adversarial")]
    results = {}
    for c in cases:
        spec_ = c.must_fire[0]
        found = res((spec_["rule"], spec_["path"], spec_["line"], "block")) if c.id.startswith("adv") else res()
        results[(c.id, "person")] = results[(c.id, "agent")] = found
    return ev.summarize(cases, results)


def test_the_holdout_failures_are_grouped_by_rule_and_kind_and_add_up_to_the_failure_total():
    s = held_summary()
    by_rule = ev.holdout_by_rule(s["failing"])
    assert by_rule == {"review.claim_mismatch": {"missed": 2}, "secrets.hardcoded": {"missed": 2},
                       "tests.skip_added": {"too strict": 2}}
    assert sum(n for kinds_ in by_rule.values() for n in kinds_.values()) == len(s["failing"]) == sum(s["failures"].values()) == 6


def test_the_by_rule_line_is_one_line_of_rule_kind_and_count_and_nothing_else():
    by_rule = ev.holdout_by_rule(held_summary()["failing"])
    assert ev.format_by_rule(by_rule) == ("review.claim_mismatch missed 2 · secrets.hardcoded missed 2 · "
                                          "tests.skip_added too strict 2")
    assert ev.format_by_rule({}) == "none"


def test_every_kind_of_failure_is_counted_under_a_rule_and_a_kind_name():
    keys = ["a|person|FN:r.one@x.py:1", "a|agent|OVER:r.one@x.py:1 block>fix", "b|person|UNDER:r.two@y.py:2 warn<fix",
            "b|person|FP:r.three@z.py:3 warn", "c|person|FALSE_BLOCK:r.four@q.py:None", "c|person|UNEXPECTED_BLOCK:r.five@q.py:4",
            "d|person|CRASH:no JSON (exit 2): boom", "d|person|GATE_ERROR:something broke in x.py",
            "e|both|AGENT_SOFTER:r.six@w.py:1 person 3 agent 1", "e|both|AGENT_SOFTER:verdict person fix agent warn"]
    got = ev.holdout_by_rule(keys)
    assert sum(n for k in got.values() for n in k.values()) == len(keys)
    assert got["r.one"] == {"missed": 1, "too strict": 1} and got["r.two"] == {"too lax": 1}
    assert got["gate.error"] == {"gate error": 1}
    assert set(got) == {"r.one", "r.two", "r.three", "r.four", "r.five", "r.six", "gate.error", "(none)"}
    assert got["(none)"] == {"crash": 1, "agent softer": 1}   # a crash and a verdict difference belong to no rule


def test_a_made_up_rule_text_cannot_carry_a_path_or_an_id_into_the_by_rule_counts():
    got = ev.holdout_by_rule(["seed-py-x|person|FN:../../etc/passwd secret@a.py:1", "seed-py-x|person|FN:seed-py-x@a.py:1"])
    assert got == {"(none)": {"missed": 2}}


def test_the_summary_prints_the_by_rule_line_without_any_case_id_path_or_file_name(capsys):
    s_hold = held_summary()
    s_train = ev.summarize([], {})
    board = ev.build_board("9.9.9", 4, 1.0, s_train, s_hold, held=True)
    ev.print_summary(board, s_train, s_hold, True)
    out = capsys.readouterr().out
    line = [ln for ln in out.splitlines() if ln.startswith("holdout by rule:")]
    assert len(line) == 1, out
    counts = [int(n) for n in re.findall(r" (\d+)(?: · |$)", line[0])]
    assert sum(counts) == len(s_hold["failing"]) == 6
    assert not ID_PREFIXES.search(line[0]), line[0]
    for leak in ("hidden", "billing", "payments", "test_hidden", "app/a", ".py", ".go", "/", "python", "golang"):
        assert leak not in line[0], leak
    assert not ID_PREFIXES.search(out)                        # nor does the rest of the holdout output name a case


def test_the_by_rule_line_says_none_when_the_holdout_has_no_failure(capsys):
    s_train, s_hold = ev.summarize([], {}), ev.summarize([], {})
    s_hold["total_cases"] = 3
    ev.print_summary(ev.build_board("9.9.9", 4, 1.0, s_train, s_hold, held=True), s_train, s_hold, True)
    assert "holdout by rule: none" in capsys.readouterr().out


def test_the_scoreboard_holds_the_holdout_failures_by_rule_and_no_case_id():
    s_train, s_hold = ev.summarize([], {}), held_summary()
    board = ev.build_board("9.9.9", 4, 1.0, s_train, s_hold, held=True)
    assert board["holdout"]["by_rule"] == ev.holdout_by_rule(s_hold["failing"])
    assert board["holdout"]["by_rule"]["secrets.hardcoded"] == {"missed": 2}
    text = json.dumps(board)
    assert not ID_PREFIXES.search(text) and "billing" not in text and "failing" not in board["holdout"]
    assert ev.build_board("9.9.9", 4, 1.0, s_train, s_hold, held=False)["holdout"] is None


# ---------------------------------------------------------------------------------------------------- the README section

def accuracy_tree(tmp_path, board):
    """A copy of what gen_accuracy.py reads (README.md, eval/scoreboard.json) next to a copy of the script."""
    root = tmp_path / "tree"
    for rel in ("scripts", "eval"):
        (root / rel).mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "gen_accuracy.py", root / "scripts" / "gen_accuracy.py")
    shutil.copy(ROOT / "README.md", root / "README.md")
    (root / "eval" / "scoreboard.json").write_text(json.dumps(board, indent=1) + "\n", encoding="utf-8", newline="\n")
    return root


def accuracy(root, *args):
    return subprocess.run([sys.executable, str(root / "scripts" / "gen_accuracy.py"), *args], capture_output=True, text=True,
                          encoding="utf-8")


def committed_board():
    return json.loads((EVAL / "scoreboard.json").read_text(encoding="utf-8"))


def test_the_accuracy_section_works_with_a_scoreboard_that_has_no_by_rule_key(tmp_path):
    board = committed_board()
    board["holdout"].pop("by_rule", None)                     # the shape written before the key existed
    root = accuracy_tree(tmp_path, board)
    assert accuracy(root, "--write").returncode == 0
    done = accuracy(root, "--check")
    assert done.returncode == 0, done.stderr
    head = (root / "README.md").read_text(encoding="utf-8").split("## Measured accuracy", 1)[1].split("### Training cases", 1)[0]
    assert "by rule" not in head and "Holdout (cases the tool was never tuned on)" in head


def test_the_accuracy_section_shows_the_holdout_failures_by_rule_when_the_scoreboard_has_them(tmp_path):
    board = committed_board()
    by_rule = {"secrets.hardcoded": {"missed": 6}, "tests.skip_added": {"too strict": 1}}
    board["holdout"]["by_rule"] = by_rule
    root = accuracy_tree(tmp_path, board)
    assert accuracy(root, "--check").returncode == 1          # the README was made without the key
    assert accuracy(root, "--write").returncode == 0
    assert accuracy(root, "--check").returncode == 0
    section = (root / "README.md").read_text(encoding="utf-8").split("## Measured accuracy", 1)[1]
    head = section.split("### Training cases", 1)[0]
    assert ev.format_by_rule(by_rule) in head and "by rule" in head          # inside the holdout part, before the training part
    assert not ID_PREFIXES.search(head)


# ---------------------------------------------------------------------------------------------------- the freshness gate

def eval_tree(tmp_path, version="1.2.3", on_disk=None, holdout=2, board_version=None, board_train=None, board_holdout=None):
    """A source tree with a version, a corpus of case.toml files (family -> count) and a scoreboard written for it."""
    on_disk = on_disk if on_disk is not None else {"real": 2, "seeded": 3}
    root = tmp_path / "src"
    (root / "eval" / "corpus").mkdir(parents=True)
    (root / "eval" / "holdout").mkdir()
    (root / "aigate_lite.py").write_text(f'"""x"""\n__version__ = "{version}"\n', encoding="utf-8", newline="\n")
    for family, n in on_disk.items():
        for i in range(n):
            case = root / "eval" / "corpus" / f"{family}-{i}"
            case.mkdir()
            (case / "case.toml").write_text(f'id = "{family}-{i}"\nsource = "{family}"\n', encoding="utf-8", newline="\n")
    for i in range(holdout):
        case = root / "eval" / "holdout" / f"held-{i}"
        case.mkdir()
        (case / "case.toml").write_text(f'id = "held-{i}"\nsource = "seeded"\n', encoding="utf-8", newline="\n")
    train = board_train if board_train is not None else dict(on_disk)
    board = {"schema": 1, "tool_version": board_version or version,
             "train": {"cases": train, "total_cases": sum(train.values())},
             "holdout": {"total_cases": holdout if board_holdout is None else board_holdout}}
    (root / "eval" / "scoreboard.json").write_text(json.dumps(board), encoding="utf-8", newline="\n")
    return root


def test_the_release_check_accepts_a_scoreboard_made_for_this_version_and_this_corpus(tmp_path):
    rc = load_release_check()
    assert rc.check_scoreboard(eval_tree(tmp_path)) == "1.2.3"


def test_the_release_check_fails_when_the_scoreboard_is_for_another_version(tmp_path):
    rc = load_release_check()
    with pytest.raises(rc.Failure, match=r"is for 1\.2\.2 with 5 training cases; the tree is 1\.2\.3 with 5"):
        rc.check_scoreboard(eval_tree(tmp_path, board_version="1.2.2"))


@pytest.mark.parametrize("board_train, board_holdout", [
    ({"real": 2, "seeded": 2}, None),                          # a case was added since the scoreboard was made
    ({"real": 3, "seeded": 3}, None),                          # a case was removed
    ({"real": 3, "seeded": 2}, None),                          # same total, another mix
    (None, 1)])                                                # the holdout grew
def test_the_release_check_fails_when_the_case_counts_differ_from_the_corpus_on_disk(tmp_path, board_train, board_holdout):
    rc = load_release_check()
    with pytest.raises(rc.Failure, match=r"release_check: eval/scoreboard\.json"):
        rc.check_scoreboard(eval_tree(tmp_path, board_train=board_train, board_holdout=board_holdout))


def test_the_stale_scoreboard_message_names_both_sides_and_the_command_that_fixes_it(tmp_path):
    rc = load_release_check()
    root = eval_tree(tmp_path, version="0.6.1", board_version="0.6.0", board_train={"real": 1, "seeded": 1})
    with pytest.raises(rc.Failure) as stale:
        rc.check_scoreboard(root)
    assert str(stale.value) == ("release_check: eval/scoreboard.json is for 0.6.0 with 2 training cases; the tree is 0.6.1 with 5.\n"
                                "  fix: py eval/run.py && py scripts/gen_accuracy.py")


def test_a_missing_or_unreadable_scoreboard_is_a_failure_not_a_traceback(tmp_path):
    rc = load_release_check()
    root = eval_tree(tmp_path)
    (root / "eval" / "scoreboard.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(rc.Failure, match="scoreboard"):
        rc.check_scoreboard(root)
    (root / "eval" / "scoreboard.json").unlink()
    with pytest.raises(rc.Failure, match="scoreboard"):
        rc.check_scoreboard(root)


def test_a_contested_case_is_not_counted_like_the_harness_does_not_count_it(tmp_path):
    rc = load_release_check()
    root = eval_tree(tmp_path)
    (root / "eval" / "corpus" / "real-0" / "case.toml").write_text('id = "real-0"\nsource = "real"\ncontested = true\n', encoding="utf-8")
    with pytest.raises(rc.Failure):
        rc.check_scoreboard(root)                              # the scoreboard still counts it: stale
    board = json.loads((root / "eval" / "scoreboard.json").read_text(encoding="utf-8"))
    board["train"] = {"cases": {"real": 1, "seeded": 3}, "total_cases": 4}
    (root / "eval" / "scoreboard.json").write_text(json.dumps(board), encoding="utf-8")
    assert rc.check_scoreboard(root) == "1.2.3"


def test_a_tag_run_requires_a_fresh_scoreboard_and_a_dispatch_run_and_the_dist_check_do_not(tmp_path, capsys):
    rc = load_release_check()
    stale = eval_tree(tmp_path, board_version="1.2.2")
    assert rc.main(["tag", "v1.2.3"], root=stale) == 1
    assert "release_check: eval/scoreboard.json is for 1.2.2" in capsys.readouterr().err
    assert rc.main(["tag", ""], root=stale) == 0 and capsys.readouterr().out.strip() == "1.2.3"       # a dispatch run builds only
    assert rc.main(["scoreboard"], root=stale) == 1 and "fix: py eval/run.py" in capsys.readouterr().err
    fresh = eval_tree(tmp_path / "again")
    assert rc.main(["tag", "v1.2.3"], root=fresh) == 0 and capsys.readouterr().out.strip() == "1.2.3"
    assert rc.main(["scoreboard"], root=fresh) == 0


def test_the_workflows_that_run_on_pull_requests_do_not_run_the_freshness_gate():
    for name in ("ci.yml", "ci-full.yml", "eval-nightly.yml"):
        assert "release_check.py" not in (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"), name
    assert "release_check.py tag" in (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------------- a clean repository, from its snapshot

class CleanCase:
    def __init__(self, case_id):
        self.id, self.family, self.who, self.contested, self.holdout = case_id, "clean", "person", False, False
        self.must_fire, self.must_not_fire, self.messages, self.mode = [], [], [], "clean"
        self.meta = {"id": case_id, "source": "clean"}


def cut_the_network(monkeypatch):
    """Every request to a registry that does not go through the replayed snapshot fails at once: the proxy is a closed port."""
    original = ev.run_env

    def run_env(who, tmp):
        env = original(who, tmp)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            env[name] = "http://127.0.0.1:9"
        env.pop("NO_PROXY", None)
        env.pop("no_proxy", None)
        return env
    monkeypatch.setattr(ev, "run_env", run_env)


def test_a_clean_repository_is_audited_from_its_recorded_snapshot_with_the_network_cut(repo, tmp_path, monkeypatch):
    # the repository pins a version with a published advisory; only the replayed snapshot can say so
    repo.write("requirements.txt", "requests==2.19.0\n")
    repo.commit("pin")
    snapshots = tmp_path / "eval-copy" / "snapshots"
    snapshots.mkdir(parents=True)
    table = json.loads((ROOT / "tests" / "registry_fixture.json").read_text(encoding="utf-8"))
    table["_meta"] = {"case": "clean-test-snapshot", "now": "2026-10-31T00:00:00+00:00"}
    (snapshots / "clean-test-snapshot.json").write_text(json.dumps(table), encoding="utf-8", newline="\n")
    monkeypatch.setattr(ev, "HERE", tmp_path / "eval-copy")
    cut_the_network(monkeypatch)
    case = CleanCase("clean-test-snapshot")
    work = tmp_path / "work"
    work.mkdir()
    out = ev.run_case(case, ROOT / "aigate_lite.py", "person", tmp_path / "cache", work, {case.id: (repo.path, "")})
    assert out.get("skipped") is None and out["crash"] is None, out
    assert ("deps.vulnerable", "fix") in {(f["rule"], f["action"]) for f in out["findings"]}, out["findings"]   # from the snapshot
    assert not [f for f in out["findings"] if f["rule"] == "gate.error"]
    assert ev.score_one(case, "person", out)["fail"] == []                      # and a person sees no BLOCK


# ---------------------------------------------------------------------------------------------------- the generated token

def test_the_properties_case_meets_its_label_with_the_token_the_generator_now_writes(tmp_path):
    # a provider token without a documentation-placeholder run is BLOCK in application.properties, for a person and an agent
    # (eval/LABEL_CHANGES.md H). This runs ONE case through the real CLI.
    case = ev.load_cases(only={"r3-properties-key"})[0]
    results = {(case.id, who): ev.run_case(case, ROOT / "aigate_lite.py", who, tmp_path / "cache", tmp_path)
               for who in ("person", "agent")}
    summary = ev.summarize([case], results)
    assert summary["failing"] == [], summary["failing"]
    assert summary["rules"]["secrets.hardcoded"]["tp"] == 2


def test_the_new_placeholder_is_filled_in_with_a_slack_token_that_has_no_placeholder_run():
    token = ev.fill(b"@@SLACKKEY@@").decode()
    assert token.startswith("xox") and not re.search(r"12345678|abcdefgh|example|placeholder|xxxx", token, re.I)
    assert "@@" not in token and token != ev.fill(b"@@SLACK@@").decode()       # the old token is still there for the other cases
