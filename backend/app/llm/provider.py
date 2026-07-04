"""One place to build the LLM model, shared by the Copilot and the transaction parser.

Gemini via pydantic-ai (the installed provider), but everything downstream is
provider-agnostic — swap the model here to point at any pydantic-ai-supported endpoint.
Built lazily and cached so a missing key only errors when the AI features are actually used.
"""

from functools import lru_cache

from ..config import settings


class LLMNotConfigured(RuntimeError):
    """Raised when an AI feature is invoked without an API key configured."""


@lru_cache(maxsize=1)
def get_model():
    """Return a cached pydantic-ai model. Raises LLMNotConfigured if no key is set."""
    if not settings.llm_configured:
        raise LLMNotConfigured(
            "LLM not configured — set GEMINI_API_KEY (or LLM_API_KEY) to use the Copilot."
        )
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider

    provider = GoogleProvider(api_key=settings.llm_api_key)
    return GoogleModel(settings.llm_model, provider=provider)
