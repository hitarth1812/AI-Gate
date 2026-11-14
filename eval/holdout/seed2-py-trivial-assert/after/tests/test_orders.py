from app.orders import compute_total, load_amounts


def test_total():
    assert compute_total([1, 2], 0.0) == 3
    assert True


def test_empty():
    assert compute_total([], 0.0) == 0
    assert load_amounts([]) == []
