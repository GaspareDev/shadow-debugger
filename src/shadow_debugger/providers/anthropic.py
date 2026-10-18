"""Anthropic Claude provider (``pip install shadow-debugger[anthropic]``)."""

from __future__ import annotations

import threading
from typing import Any, Optional

from .base import Provider, ProviderError, register_provider


@register_provider("anthropic", "claude")
class AnthropicProvider(Provider):
    default_model = "claude-haiku-5-5"
    env_key = "ANTHROPIC_API_KEY"
    max_tokens = 2048

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client: Optional[Any] = None
        self._client_lock = threading.Lock()

    def _get_client(self) -> Any:
        with self._client_lock:
            if self._client is None:
                try:
                    import anthropic
                except ImportError as exc:
                    raise ProviderError(
                        "anthropic: package not installed. Run: pip install 'shadow-debugger[anthropic]'"
                    ) from exc
                kwargs: dict = {"api_key": self.api_key, "timeout": self.timeout, "max_retries": 2}
                if self.base_url:
                    kwargs["base_url"] = self.base_url
                self._client = anthropic.Anthropic(**kwargs)
            return self._client

    def complete(self, system: str, prompt: str) -> str:
        self.check_configuration()
        client = self._get_client()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise ProviderError(f"anthropic: request to {self.model} failed: {exc}") from exc
        text = "".join(
            getattr(block, "text", "") for block in (response.content or []) if getattr(block, "type", "") == "text"
        )
        if not text:
            raise ProviderError(f"anthropic: empty response from {self.model}")
        return text
