from app.orders import compute_total, load_amounts


import sys
import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes only")
def test_total():
    assert compute_total([1, 2], 0.0) == 3
    assert compute_total([10], 0.5) == 15


def test_empty():
    assert compute_total([], 0.0) == 0
    assert load_amounts([]) == []
