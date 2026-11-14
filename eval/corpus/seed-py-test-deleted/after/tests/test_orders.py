from app.orders import compute_total, load_amounts


def test_total():
    assert compute_total([1, 2], 0.0) == 3
    assert compute_total([10], 0.5) == 15
