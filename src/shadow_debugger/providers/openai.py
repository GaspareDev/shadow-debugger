"""OpenAI provider (``pip install shadow-debugger[openai]``).

Also works with any OpenAI-compatible server (Groq, Together, LM Studio, vLLM...) via ``base_url``.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from .base import Provider, ProviderError, register_provider


@register_provider("openai")
class OpenAIProvider(Provider):
    default_model = "gpt-5.4-nano"
    env_key = "OPENAI_API_KEY"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client: Optional[Any] = None
        self._client_lock = threading.Lock()

    def check_configuration(self) -> None:
        # Local OpenAI-compatible servers usually don't need a key.
        if self.base_url:
            return
        super().check_configuration()

    def _get_client(self) -> Any:
        with self._client_lock:
            if self._client is None:
                try:
                    from openai import OpenAI
                except ImportError as exc:
                    raise ProviderError(
                        "openai: package not installed. Run: pip install 'shadow-debugger[openai]'"
                    ) from exc
                self._client = OpenAI(
                    api_key=self.api_key or "not-needed",
                    base_url=self.base_url,
                    timeout=self.timeout,
                    max_retries=2,
                )
            return self._client

    def complete(self, system: str, prompt: str) -> str:
        self.check_configuration()
        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception as exc:
            raise ProviderError(f"openai: request to {self.model} failed: {exc}") from exc
        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise ProviderError(f"openai: empty response from {self.model}")
        return content
