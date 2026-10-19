"""Mistral AI provider (``pip install shadow-debugger[mistral]``)."""

from __future__ import annotations

import threading
from typing import Any, Optional

from .base import Provider, ProviderError, register_provider


@register_provider("mistral")
class MistralProvider(Provider):
    default_model = "mistral-small-latest"
    env_key = "MISTRAL_API_KEY"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client: Optional[Any] = None
        self._client_lock = threading.Lock()

    def _get_client(self) -> Any:
        with self._client_lock:
            if self._client is None:
                try:
                    from mistralai.client import Mistral
                except ImportError as exc:
                    raise ProviderError(
                        "mistral: package not installed. Run: pip install 'shadow-debugger[mistral]'"
                    ) from exc
                kwargs: dict[str, Any] = {"api_key": self.api_key}
                if self.base_url:
                    kwargs["server_url"] = self.base_url
                if self.timeout:
                    kwargs["timeout_ms"] = int(self.timeout * 1000)
                self._client = Mistral(**kwargs)

            return self._client

    def complete(self, system: str, prompt: str) -> str:
        self.check_configuration()
        client = self._get_client()
        try:
            response = client.chat.complete(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception as exc:
            raise ProviderError(f"mistral: request to {self.model} failed: {exc}") from exc
        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise ProviderError(f"mistral: empty response from {self.model}")
        return str(content)