from app.config import Settings
from app.llm.extractive import ExtractiveProvider
from app.llm.provider import LiteLLMProvider, LLMProvider


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "extractive":
        return ExtractiveProvider()
    return LiteLLMProvider(settings)
