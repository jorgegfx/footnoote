from tests.fakes import make_result


def test_result_id_is_stable_per_url() -> None:
    a = make_result(1, url="https://a.org/x")
    b = make_result(2, url="https://a.org/x", title="Other title")
    c = make_result(1, url="https://a.org/y")
    assert a.id == b.id
    assert a.id != c.id
