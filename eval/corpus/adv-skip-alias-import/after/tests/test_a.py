from pytest import mark as m


@m.skip
def test_a():
    assert 1
