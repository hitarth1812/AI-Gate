import pytest

later = pytest.mark.skip


@later
def test_a():
    assert 1
