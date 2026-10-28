"""Test doubles: a scripted chat model and in-memory adapters."""

import asyncio
from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, Field

from footnote.llm import ModelFactory, Role
from footnote.models import Family, QueryPlan, SourceResult


class FakeChatModel(BaseChatModel):
    """Returns scripted text replies and scripted structured outputs, recording every call."""

    texts: list[str] = Field(default_factory=list)
    structured: list[BaseModel] = Field(default_factory=list)
    calls: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(self.texts.pop(0)))])

    def with_structured_output(  # type: ignore[override]
        self, schema: Any, **kwargs: Any
    ) -> Runnable[Any, Any]:
        def respond(messages: list[BaseMessage]) -> BaseModel:
            self.calls.append(list(messages))
            return self.structured.pop(0)

        return RunnableLambda(respond)


def fake_models(fast: FakeChatModel, strong: FakeChatModel) -> ModelFactory:
    def factory(role: Role) -> BaseChatModel:
        return fast if role == "fast" else strong

    return factory


class StaticAdapter:
    """Adapter that returns fixed results, or raises / hangs to exercise failure handling."""

    def __init__(
        self,
        source_id: str,
        family: Family,
        results: Sequence[SourceResult] = (),
        error: Exception | None = None,
        delay: float = 0.0,
    ):
        self.id = source_id
        self.family = family
        self.results = list(results)
        self.error = error
        self.delay = delay
        self.plans: list[QueryPlan] = []

    async def search(self, plan: QueryPlan) -> list[SourceResult]:
        self.plans.append(plan)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.results


def make_result(n: int, family: Family = Family.ACADEMIC, **overrides: Any) -> SourceResult:
    fields: dict[str, Any] = {
        "source_id": "openalex" if family == Family.ACADEMIC else "reuters",
        "family": family,
        "title": f"Result {n}",
        "url": f"https://example.org/{family.value}/{n}",
        "snippet": f"Snippet for result {n}.",
        "trust": 0.9,
    }
    return SourceResult(**(fields | overrides))
