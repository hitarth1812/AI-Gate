"""Phase 6A: the reviewer's synthetic AI-mistake fixtures (tests/precision_fixtures.py, REVIEW_*) behave as written, for a
person and for an AI agent. `python bench/precision.py --review [--agent]` prints the per-rule table.

The fixtures are written by hand from typical AI mistakes; a full match here shows the rules do what they are designed
to do on those cases, not how precise they are on real repositories."""
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "bench"))
import precision as pr                                   # noqa: E402
import precision_fixtures as fx                          # noqa: E402


@pytest.fixture(scope="module")
def outcomes():
    return {agent: pr.evaluate_review(pr.measure_review(ROOT / "aigate_lite.py", agent), agent) for agent in (False, True)}


@pytest.mark.parametrize("agent", [False, True], ids=["person", "agent"])
def test_every_review_fixture_case_matches_its_expectation(outcomes, agent):
    wrong = [(cid, want, got) for cid, _rule, _path, want, got, ok in outcomes[agent] if not ok]
    assert wrong == []


def test_every_new_rule_has_true_positive_and_look_alike_cases():
    table = pr.review_table(pr.evaluate_review({"must_fix": [], "should_check": []}))
    wanted = {"api.unknown_symbol", "api.bad_call", "api.broken_caller", "tests.missing_for_change", "review.claim_mismatch",
              "code.error_check_removed"}
    assert set(table) == wanted
    assert all(c.get("missed", 0) >= 2 and c.get("clean", 0) >= 1 for c in table.values())   # >= 2 positives, >= 1 look-alike


def test_comment_and_string_look_alikes_exist_for_the_rules_that_read_code():
    names = {c[0] for c in fx.REVIEW_CASES}
    assert {"us-ok-comment", "us-ok-string", "us-ok-go-comment", "us-ok-go-string", "us-ok-ts-comment", "bc-ok-comment",
            "bc-ok-string", "bc-ok-go-comment", "ec-ok-go-comment"} <= names


def test_agents_are_never_softer_than_people_on_any_case(outcomes):
    rank = {None: 0, "pass": 1, "warn": 2, "fix": 3, "block": 4}
    for (cid, _r, _p, _w, human, _ok1), (_cid, _r2, _p2, _w2, agent, _ok2) in zip(outcomes[False], outcomes[True]):
        assert rank[agent] >= rank[human], cid
