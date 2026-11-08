import pytest
from pydantic import ValidationError

from footnote.config import Settings, SourceConfig, SourcesConfig
from footnote.models import Family
from footnote.sources.news.nyt import NYTAdapter


def test_shipped_sources_yaml_loads(sources: SourcesConfig) -> None:
    assert {s.id for s in sources.native()} == {"guardian", "nyt"}  # gdelt is disabled
    assert "reuters.com" in sources.search_domains(Family.NEWS)
    assert {s.family for s in sources.sources} == {Family.NEWS}


def test_search_source_for_url_uses_family_and_trust(sources: SourcesConfig) -> None:
    reuters = sources.search_source_for_url("https://www.reuters.com/a", Family.NEWS)
    assert reuters is not None
    assert reuters.id == "reuters"
    assert reuters.trust == 0.95
    assert sources.search_source_for_url("https://www.nature.com/a", Family.NEWS) is None
    assert sources.search_source_for_url("https://evilreuters.com/a", Family.NEWS) is None


def test_disabled_sources_are_excluded() -> None:
    config = SourcesConfig(
        sources=[
            SourceConfig(id="a", family=Family.NEWS, kind="search", domains=["a.com"], trust=1),
            SourceConfig(
                id="b", family=Family.NEWS, kind="search", domains=["b.com"], trust=1, enabled=False
            ),
        ]
    )
    assert config.search_domains(Family.NEWS) == ["a.com"]
    assert config.search_source_for_url("https://b.com/x", Family.NEWS) is None


def test_search_source_requires_domains() -> None:
    with pytest.raises(ValidationError, match="at least one domain"):
        SourceConfig(id="x", family=Family.NEWS, kind="search", trust=0.5)


def test_trust_must_be_between_zero_and_one() -> None:
    with pytest.raises(ValidationError):
        SourceConfig(id="x", family=Family.NEWS, kind="native", trust=1.5)


def test_duplicate_ids_rejected() -> None:
    entry = {"id": "x", "family": "news", "kind": "native", "trust": 0.5}
    with pytest.raises(ValidationError, match="duplicate"):
        SourcesConfig.model_validate({"sources": [entry, entry]})


def test_unknown_family_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceConfig.model_validate(
            {"id": "x", "family": "academic", "kind": "native", "trust": 0.5}
        )


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_api_keys_count_as_unset(value: str) -> None:
    settings = Settings(_env_file=None, nyt_api_key=value, guardian_api_key=value)  # type: ignore[call-arg, arg-type]
    assert settings.nyt_api_key is None
    assert settings.guardian_api_key is None
    assert not NYTAdapter.is_configured(settings)
