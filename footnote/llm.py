"""Provider-agnostic chat model factory. The only module that knows about LLM providers."""

from collections.abc import Callable
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from footnote.config import Settings

Role = Literal["fast", "strong"]
ModelFactory = Callable[[Role], BaseChatModel]


class LLMNotConfiguredError(RuntimeError):
    pass


def get_chat_model(role: Role, settings: Settings) -> BaseChatModel:
    """`fast` handles planning and scoring; `strong` handles synthesis and verification."""
    model = settings.llm_model_fast if role == "fast" else settings.llm_model_strong
    if not settings.llm_provider or not model:
        raise LLMNotConfiguredError(
            f"Set LLM_PROVIDER and LLM_MODEL_{role.upper()} to use the {role!r} model."
        )
    chat_model = init_chat_model(model, model_provider=settings.llm_provider)
    if not isinstance(chat_model, BaseChatModel):  # only configurable models aren't
        raise LLMNotConfiguredError(f"Could not build a chat model for {model!r}.")
    return chat_model


def model_factory(settings: Settings) -> ModelFactory:
    cache: dict[Role, BaseChatModel] = {}

    def factory(role: Role) -> BaseChatModel:
        if role not in cache:
            cache[role] = get_chat_model(role, settings)
        return cache[role]

    return factory
