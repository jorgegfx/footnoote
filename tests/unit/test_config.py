import pytest
from pydantic import ValidationError

from footnote.config import SourceConfig, SourcesConfig
from footnote.models import Family


def test_shipped_sources_yaml_loads(sources: SourcesConfig) -> None:
    assert "openalex" in {s.id for s in sources.native()}
    assert "reuters.com" in sources.search_domains(Family.NEWS)
    assert "nature.com" in sources.search_domains(Family.ACADEMIC)
    assert "nature.com" not in sources.search_domains(Family.NEWS)


def test_search_source_for_url_uses_family_and_trust(sources: SourcesConfig) -> None:
    reuters = sources.search_source_for_url("https://www.reuters.com/a", Family.NEWS)
    assert reuters is not None
    assert reuters.id == "reuters"
    assert reuters.trust == 0.95
    assert sources.search_source_for_url("https://www.reuters.com/a", Family.ACADEMIC) is None
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
        SourceConfig(id="x", family=Family.ACADEMIC, kind="native", trust=1.5)


def test_duplicate_ids_rejected() -> None:
    entry = {"id": "x", "family": "academic", "kind": "native", "trust": 0.5}
    with pytest.raises(ValidationError, match="duplicate"):
        SourcesConfig.model_validate({"sources": [entry, entry]})
