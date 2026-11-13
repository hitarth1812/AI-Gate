from app.orders import compute_total, load_amounts


def test_total():
    assert compute_total([10], 0.5) == 15


def test_empty():
    assert load_amounts([]) == []
