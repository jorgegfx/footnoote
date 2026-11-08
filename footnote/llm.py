"""Provider-agnostic chat model factory. The only module that knows about LLM providers."""

from collections.abc import Callable
from typing import Any, Literal

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from footnote.config import Settings

Role = Literal["fast", "strong"]
ModelFactory = Callable[[Role], BaseChatModel]

_METHOD_KEY = "footnote_structured_output_method"


class LLMNotConfiguredError(RuntimeError):
    pass


def get_chat_model(role: Role, settings: Settings) -> BaseChatModel:
    """`fast` handles planning and scoring; `strong` handles synthesis and verification."""
    model = settings.llm_model_fast if role == "fast" else settings.llm_model_strong
    if not settings.llm_provider or not model:
        raise LLMNotConfiguredError(
            f"Set LLM_PROVIDER and LLM_MODEL_{role.upper()} to use the {role!r} model."
        )
    extra: dict[str, Any] = {"base_url": settings.llm_base_url} if settings.llm_base_url else {}
    chat_model = init_chat_model(model, model_provider=settings.llm_provider, **extra)
    if not isinstance(chat_model, BaseChatModel):  # only configurable models aren't
        raise LLMNotConfiguredError(f"Could not build a chat model for {model!r}.")
    if settings.llm_structured_output_method:
        chat_model.metadata = {
            **(chat_model.metadata or {}),
            _METHOD_KEY: settings.llm_structured_output_method,
        }
    return chat_model


def structured(
    model: BaseChatModel, schema: type[BaseModel]
) -> Runnable[LanguageModelInput, dict[str, Any] | BaseModel]:
    """`model.with_structured_output(schema)`, using LLM_STRUCTURED_OUTPUT_METHOD when set."""
    method = (model.metadata or {}).get(_METHOD_KEY)
    if method:
        return model.with_structured_output(schema, method=method)
    return model.with_structured_output(schema)


def model_factory(settings: Settings) -> ModelFactory:
    cache: dict[Role, BaseChatModel] = {}

    def factory(role: Role) -> BaseChatModel:
        if role not in cache:
            cache[role] = get_chat_model(role, settings)
        return cache[role]

    return factory
