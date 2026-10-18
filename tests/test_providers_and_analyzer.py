"""Tests for prompt building, custom providers, Gemini Interactions API, and Ollama host normalization."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shadow_debugger.analyzer import AIAnalyzer, build_prompt
from shadow_debugger.core import ShadowCore
from shadow_debugger.providers import Provider, ProviderError, create_provider, register_provider
from shadow_debugger.providers.gemini import GeminiProvider
from shadow_debugger.providers.ollama import normalize_host


def test_build_prompt_includes_extra_metadata_and_incident_fence() -> None:
    ctx = {
        "exception_type": "ValueError",
        "exception_message": "bad item id",
        "file": "routes.py",
        "line": 19,
        "function": "get_item",
        "occurrence_count": 4,
        "code_context": ">>   19: raise ValueError('bad item id')",
        "locals": {"item_id": -1},
        "extra_metadata": {"http_request": {"url": "http://testserver/items/-1", "http_method": "GET"}},
        "traceback": "Traceback...",
    }
    prompt = build_prompt(ctx)
    assert "http://testserver/items/-1" in prompt
    assert "occurred 4 times" in prompt
    assert prompt.startswith("<incident>")
    assert prompt.endswith("</incident>")


def test_custom_provider_registration() -> None:
    @register_provider("dummy-test-llm")
    class DummyProvider(Provider):
        default_model = "dummy-1"
        requires_api_key = False

        def complete(self, system: str, prompt: str) -> str:
            return "### 1. Root Cause Summary\nSolved."

    p = create_provider("dummy-test-llm")
    assert p.model == "dummy-1"
    analyzer = AIAnalyzer(provider=p)
    assert "Solved." in analyzer.analyze({"exception_type": "RuntimeError"})


def test_gemini_provider_missing_key_raises_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider = GeminiProvider(api_key=None)
    with pytest.raises(ProviderError, match="missing API key"):
        provider.complete("sys", "user")


def test_gemini_provider_uses_interactions_api_with_store_false() -> None:
    provider = GeminiProvider(api_key="fake-key")
    fake_client = MagicMock()
    fake_interaction = MagicMock()
    fake_interaction.output_text = "### 1. Root Cause Summary\nFixed via Gemini 3.8 Flash."
    fake_client.interactions.create.return_value = fake_interaction

    with patch.object(provider, "_get_client", return_value=fake_client):
        out = provider.complete("system instructions", "prompt body")

    assert "Fixed via Gemini 3.8 Flash." in out
    fake_client.interactions.create.assert_called_once_with(
        model="gemini-3.8-flash",
        system_instruction="system instructions",
        input="prompt body",
        store=False,
    )


def test_ollama_host_normalization() -> None:
    assert normalize_host("0.0.0.0:11434") == "http://localhost:11434"
    assert normalize_host("127.0.0.1:11434/") == "http://127.0.0.1:11434"
    assert normalize_host("http://my-box:11434/") == "http://my-box:11434"


def test_provider_failure_does_not_write_fake_report(tmp_path: Path) -> None:
    class FailingProvider(Provider):
        default_model = "broken"
        requires_api_key = False

        def complete(self, system: str, prompt: str) -> str:
            raise ProviderError("API key invalid")

    core = ShadowCore(provider=FailingProvider(), output_dir=str(tmp_path))
    try:
        try:
            1 / 0  # noqa: B018
        except ZeroDivisionError as exc:
            core.capture_exception(exc)
        core.flush(timeout=5.0)
        assert list(tmp_path.glob("*.md")) == []
    finally:
        core.shutdown(timeout=2.0)
