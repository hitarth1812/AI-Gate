"""0.5.3 round 2 (Part 3, performance budget): `bench/bench.py --against REF --budget 20` fails when a timed hook scenario is more than
20% slower than REF in BOTH median and p95 (one noisy statistic does not fail a shared CI runner), and still hard-fails when a
scenario makes more git calls or reads more blobs. Without --budget the gates are the strict ones of 0.5.x (unchanged)."""
import importlib.util

from conftest import ROOT

spec = importlib.util.spec_from_file_location("bench", ROOT / "bench" / "bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def row(med, p95, git=6, blobs=1):
    return {"median_ms": med, "p95_ms": p95, "counters": {"git_calls": git, "blobs_read": blobs}}


def res(old, new, name="B"):
    return {name: {"old": old, "new": new}}


def test_a_budget_fails_when_median_and_p95_are_both_over(monkeypatch):
    fails = bench.against_gates(res(row(100, 120), row(125, 150)), budget=20)
    assert fails and "B" in fails[0] and "20" in fails[0]


def test_a_budget_ignores_one_noisy_statistic():
    assert bench.against_gates(res(row(100, 120), row(125, 124)), budget=20) == []      # median up 25%, p95 +3%
    assert bench.against_gates(res(row(100, 120), row(104, 160)), budget=20) == []      # p95 up 33%, median +4%


def test_a_budget_passes_a_small_change():
    assert bench.against_gates(res(row(100, 120), row(112, 130)), budget=20) == []


def test_the_hard_counters_still_fail_under_a_budget():
    fails = bench.against_gates(res(row(100, 120), row(100, 120, git=7)), budget=20)
    assert fails and "git_calls rose from 6 to 7" in fails[0]


def test_the_startup_row_is_covered_and_informational_rows_are_not():
    assert bench.against_gates(res(row(100, 120), row(130, 160), "startup"), budget=20)
    assert bench.against_gates(res(row(100, 120), row(130, 160), "startup-direct"), budget=20) == []
    assert bench.against_gates(res(row(100, 120), row(130, 160), "G"), budget=20) == []      # G is informational


def test_without_a_budget_the_strict_gates_are_unchanged():
    assert bench.against_gates(res(row(100, 120), row(108, 125), "A"))                       # +8% > the 5% limit for A
    assert bench.against_gates(res(row(100, 120), row(108, 125), "A"), budget=20) == []      # inside a 20% budget
