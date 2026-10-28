import json
from pathlib import Path
from typing import Any

import pytest

from footnote.config import PROJECT_ROOT, Settings, SourcesConfig, load_sources

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def settings() -> Settings:
    """Isolated from the developer's `.env` and environment-specific keys."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        contact_email="dev@example.org",
        search_api_key="test-search-key",  # type: ignore[arg-type]
        adapter_timeout_seconds=1.0,
        sources_path=PROJECT_ROOT / "config" / "sources.yaml",
    )


@pytest.fixture
def sources(settings: Settings) -> SourcesConfig:
    return load_sources(settings.sources_path)
