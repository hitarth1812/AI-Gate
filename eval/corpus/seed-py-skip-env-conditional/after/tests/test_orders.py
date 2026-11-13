from app.orders import compute_total, load_amounts


import os
import pytest


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is not set")
def test_total():
    assert compute_total([1, 2], 0.0) == 3
    assert compute_total([10], 0.5) == 15


def test_empty():
    assert compute_total([], 0.0) == 0
    assert load_amounts([]) == []
