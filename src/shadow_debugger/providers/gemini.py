"""Google Gemini provider (``pip install shadow-debugger[gemini]``)."""

from __future__ import annotations

import threading
from typing import Any, Optional

from .base import Provider, ProviderError, register_provider

# Status codes worth trying the next fallback model for. The SDK already retries
# transient errors with exponential backoff before raising.
_FALLBACK_CODES = {404, 429, 500, 502, 503, 504}


@register_provider("gemini", "google")
class GeminiProvider(Provider):
    default_model = "gemini-3.8-flash"
    env_key = "GEMINI_API_KEY"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client: Optional[Any] = None
        self._client_lock = threading.Lock()

    def _get_client(self) -> Any:
        with self._client_lock:
            if self._client is None:
                try:
                    from google import genai
                except ImportError as exc:
                    raise ProviderError(
                        "gemini: google-genai is not installed. Run: pip install 'shadow-debugger[gemini]'"
                    ) from exc
                self._client = genai.Client(
                    api_key=self.api_key,
                    http_options={"timeout": int(self.timeout * 1000)},  # milliseconds
                )
            return self._client

    def complete(self, system: str, prompt: str) -> str:
        self.check_configuration()
        client = self._get_client()
        last_error: Optional[BaseException] = None

        for model in [self.model, *self.fallback_models]:
            try:
                interaction = client.interactions.create(
                    model=model,
                    system_instruction=system,
                    input=prompt,
                    store=False,  # crash data must not be retained server-side
                )
                text = getattr(interaction, "output_text", None)
                if not text:
                    raise ProviderError(f"gemini: empty response from {model}")
                return str(text)
            except ProviderError:
                raise
            except Exception as exc:
                last_error = exc
                code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
                if code in _FALLBACK_CODES:
                    continue
                raise ProviderError(f"gemini: request to {model} failed: {exc}") from exc

        raise ProviderError(f"gemini: all models failed; last error: {last_error}") from last_error
