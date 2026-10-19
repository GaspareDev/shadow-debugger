from __future__ import annotations

from .base import Provider, ProviderError, register_provider
from typing import Any, Optional
import threading


@register_provider("mistral")
class MistralProvider(Provider):
    default_model = "mistral-small-latest"
    env_key = "MISTRAL_API_KEY"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client: Optional[Any] = None
        self._client_lock = threading.Lock()

    def _get_client(self):
        with self._client_lock:
            if self._client is None:
                try:
                    from mistralai.client import Mistral
                except ImportError as exc:
                    raise ProviderError(
                        "mistral: pacakge is not installed. Run: pip install 'shadow-debugger[mistral]'"
                    ) from exc
                self._client = Mistral(api_key=self.api_key)

            return self._client

    def complete(self, system: str, prompt: str) -> str:
        self.check_configuration()
        client = self._get_client()
        try:
            response = client.chat.complete(
                model=self.default_model,
                messages=[
                    {
                        "role": "system",
                        "content": system
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )
        except Exception as exc:
            raise ProviderError(f"mistral: request to {self.model} failed: {exc}") from exc
        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise ProviderError(f"mistral: empty response from {self.model}")
        return str(content)