from footnote.models import Family
from tests.fakes import make_result


def test_result_id_is_stable_and_prefers_doi() -> None:
    a = make_result(1, url="https://a.org/x", doi="10.1/ABC")
    b = make_result(1, url="https://b.org/y", doi="10.1/abc")
    c = make_result(1, url="https://a.org/x")
    assert a.id == b.id
    assert a.id != c.id
    assert c.id == make_result(1, url="https://a.org/x", family=Family.NEWS).id
