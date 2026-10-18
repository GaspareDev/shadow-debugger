"""Ollama provider: fully local, no API key, no extra dependency."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .base import Provider, ProviderError, register_provider

DEFAULT_OLLAMA_HOST = "http://localhost:11434"


def normalize_host(host: str) -> str:
    host = host.strip().rstrip("/")
    if "://" not in host:
        # OLLAMA_HOST is often set as "0.0.0.0:11434" or "myhost:11434"
        host = "http://" + host
    return host.replace("://0.0.0.0", "://localhost")


@register_provider("ollama", "local")
class OllamaProvider(Provider):
    default_model = "llama3.2"
    requires_api_key = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.base_url = normalize_host(self.base_url or os.getenv("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST)

    def complete(self, system: str, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.2},
        }
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise ProviderError(f"ollama: HTTP {exc.code} from {self.base_url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(
                f"ollama: cannot reach {self.base_url} (is `ollama serve` running?): {exc.reason}"
            ) from exc
        except (TimeoutError, OSError, ValueError) as exc:
            raise ProviderError(f"ollama: request failed: {exc}") from exc

        content = (data.get("message") or {}).get("content")
        if not content:
            raise ProviderError(f"ollama: empty response from model {self.model}")
        return content
