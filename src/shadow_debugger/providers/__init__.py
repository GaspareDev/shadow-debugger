"""Built-in LLM providers. SDKs are imported lazily, only when a provider is used."""

from .base import (
    Provider,
    ProviderError,
    available_providers,
    create_provider,
    register_provider,
)

# Importing the modules registers the built-in providers.
from . import anthropic as _anthropic  # noqa: F401,E402
from . import gemini as _gemini  # noqa: F401,E402
from . import mistral as _mistral  # noqa: F401,E402
from . import ollama as _ollama  # noqa: F401,E402
from . import openai as _openai  # noqa: F401,E402

__all__ = [
    "Provider",
    "ProviderError",
    "available_providers",
    "create_provider",
    "register_provider",
]
