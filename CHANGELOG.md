# Changelog

## 0.2.0 (2026-10-19)

- Added optional JSON incident reports (`report_format="json"` or `"both"`) alongside Markdown (#6).
- Added built-in `mistral` LLM provider (`MistralProvider`, `MISTRAL_API_KEY`, `pip install "shadow-debugger[mistral]"`) (#7).
- Added `python -m shadow_debugger list|show` CLI to inspect local Markdown reports in `./shadow_reports` (#8).

## 0.1.0 (2026-10-18)

Initial public release:

- Background worker queue (`ShadowCore`) with LRU cooldown throttling (`ErrorThrottler`).
- Providers for Ollama (`llama3.2`), Google Gemini (`gemini-3.8-flash`), OpenAI (`gpt-5.4-nano`), and Anthropic (`claude-haiku-5-5`), plus `@register_provider` for custom backends.
- Secret redaction across local variables, headers, URLs, tracebacks, and source context (`Redactor`).
- Automatic selection of user-code stack frames over stdlib / `site-packages` frames.
- Sync and `async def` support in `@shadow_debugger.watch`.
- FastAPI/Starlette ASGI middleware, Flask signal hook, and Django middleware.
- `flush()` and `shutdown()` helpers, with automatic flush on uncaught exceptions.
