from typing import Any

import pytest
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from footnote.config import Settings
from footnote.llm import get_chat_model, structured
from tests.fakes import FakeChatModel


class Answer(BaseModel):
    value: int


class RecordingModel(FakeChatModel):
    methods: list[str | None] = Field(default_factory=list)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:
        self.methods.append(kwargs.get("method"))
        return super().with_structured_output(schema, **kwargs)


def ollama_settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="ollama",
        llm_model_fast="gemma4:31b",
        llm_model_strong="nemotron-3-super",
        **overrides,
    )


def test_structured_uses_provider_default_when_unset() -> None:
    model = RecordingModel(structured=[Answer(value=1)])
    structured(model, Answer).invoke("q")
    assert model.methods == [None]


def test_structured_uses_configured_method() -> None:
    model = RecordingModel(
        structured=[Answer(value=1)],
        metadata={"footnote_structured_output_method": "function_calling"},
    )
    assert structured(model, Answer).invoke("q") == Answer(value=1)
    assert model.methods == ["function_calling"]


def test_get_chat_model_applies_base_url_and_method() -> None:
    pytest.importorskip("langchain_ollama")
    settings = ollama_settings(
        llm_base_url="https://ollama.example",
        llm_structured_output_method="function_calling",
    )
    model = get_chat_model("strong", settings)
    assert getattr(model, "base_url", None) == "https://ollama.example"
    assert (model.metadata or {})["footnote_structured_output_method"] == "function_calling"
