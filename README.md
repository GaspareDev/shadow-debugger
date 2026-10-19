# shadow-debugger

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

Background error diagnosis for Python apps and web APIs.

When an exception or HTTP 500 happens, `shadow-debugger` captures the stack frame from your code, strips secrets from local variables and request data, and sends the context to an LLM in a background worker thread. Your app returns its normal response right away; a few seconds later you get a Markdown postmortem in `./shadow_reports/`.

It works offline with **Ollama** (zero external dependencies) or with **Gemini**, **OpenAI** (including OpenAI-compatible endpoints like Groq, vLLM, LM Studio), and **Anthropic**.

---

## What a report looks like

Given a crashing endpoint:

```python
@app.get("/checkout/{order_id}")
async def checkout(order_id: int, discount_items: int = 0):
    unit_discount = 50 / discount_items
    return {"order_id": order_id, "discount": unit_discount}
```

`shadow-debugger` writes `shadow_reports/incident_20261018_103012_ZeroDivisionError_9f2a1c4b.md`:

````markdown
# Shadow Debugger Incident Report

**Timestamp:** 2026-10-18T10:30:12.418290+00:00  
**Exception:** `ZeroDivisionError`: division by zero  
**Location:** `app/routes.py:42` in `checkout`  
**Fingerprint:** `9f2a1c4b8e1032d1` (occurrences so far: 1)  

---

### 1. Root Cause Summary
`checkout()` divides `50` by `discount_items` without checking for `0`. When the query parameter is omitted or set to `0`, Python raises `ZeroDivisionError`.

### 2. Trigger Condition
`GET /checkout/1042?discount_items=0` with local state `{"order_id": 1042, "discount_items": 0}`.

### 3. Proposed Fix
```python
unit_discount = 50 / discount_items if discount_items > 0 else 0.0
```
```diff
-    unit_discount = 50 / discount_items
+    unit_discount = 50 / discount_items if discount_items > 0 else 0.0
```

### 4. Prevention Advice
Validate numeric query parameters with `Query(gt=0)` in FastAPI or guard denominators before dividing.
````

---

## Installation

```bash
# Core package (stdlib only — works with local Ollama out of the box)
pip install shadow-debugger

# Or install with the provider / framework you use:
pip install "shadow-debugger[gemini]"
pip install "shadow-debugger[openai]"
pip install "shadow-debugger[anthropic]"
pip install "shadow-debugger[mistral]"
pip install "shadow-debugger[fastapi]"
pip install "shadow-debugger[flask]"
pip install "shadow-debugger[django]"
```

---

## Quick start

### Local with Ollama (no API key)

```python
import shadow_debugger

shadow_debugger.init(
    provider="ollama",
    model="llama3.2",
    print_to_console=True,
)
```

### Cloud providers

```python
import shadow_debugger

# Uses GEMINI_API_KEY
shadow_debugger.init(provider="gemini", model="gemini-3.8-flash")

# Uses OPENAI_API_KEY (or pass base_url=... for Groq / vLLM / LM Studio)
shadow_debugger.init(provider="openai", model="gpt-5.4-nano")

# Uses ANTHROPIC_API_KEY
shadow_debugger.init(provider="anthropic", model="claude-haiku-5-5")

# Uses MISTRAL_API_KEY
shadow_debugger.init(provider="mistral", model="mistral-small-latest")
```

---

## Web frameworks

### FastAPI / Starlette

```python
from fastapi import FastAPI
import shadow_debugger

shadow_debugger.init(provider="ollama", model="llama3.2")

app = FastAPI()
app.add_middleware(shadow_debugger.FastAPIWatcherMiddleware)
```

### Flask

```python
from flask import Flask
import shadow_debugger

app = Flask(__name__)
shadow_debugger.init(provider="ollama", model="llama3.2")
shadow_debugger.init_flask(app)
```

`init_flask` hooks into `got_request_exception`, so normal 404/4xx responses are left untouched.

### Django

In `settings.py`:

```python
MIDDLEWARE = [
    "shadow_debugger.integrations.DjangoWatcherMiddleware",
    # ...
]
```

---

## Decorator & manual capture

`@watch` works on both regular and `async def` functions:

```python
import shadow_debugger

@shadow_debugger.watch(reraise=False)
async def process_order(payload: dict):
    return payload["items"]["price"] / payload["items"]["quantity"]

# Or inside an existing try/except block:
try:
    sync_inventory()
except Exception as exc:
    shadow_debugger.capture_exception(exc, extra_metadata={"job": "inventory"})
```

---

## Privacy & what gets sent

If you use a cloud provider, the prompt includes the traceback, the 5 lines of source around the error, and the local variables of the failing function. To keep sensitive data out of prompts:

- **Variable & header names** matching common secret names (`password`, `secret`, `token`, `api_key`, `authorization`, `cookie`, etc.) are replaced with `[REDACTED]`.
- **String values** matching JWTs, API keys (`sk-...`, `AIza...`, `AKIA...`, `ghp_...`), `Bearer` tokens, PEM private keys, and URL query tokens (`?access_token=...`) are scrubbed across variables, tracebacks, and exception messages.
- **Lazy objects** (like Django `QuerySet`s) are inspected via `__dict__` without calling `repr()`/`str()`, so capturing an error won't trigger extra database queries.
- You can turn off parts of the payload with `send_locals=False` or `send_source_code=False`, add your own regexes via `redact_keys=[...]` and `redact_patterns=[...]`, or run everything locally with `provider="ollama"`.
- Proposed code patches are only checked with `ast.parse()` — generated code is never executed.

---

## Configuration

All options can be passed to `shadow_debugger.init(...)`:

| Option | Default | Description |
| :--- | :--- | :--- |
| `provider` | `"gemini"` | `"gemini"`, `"ollama"`, `"openai"`, `"anthropic"`, `"mistral"`, or a `Provider` instance. |
| `model` | `None` | Model name. Uses the provider default if omitted. |
| `api_key` | `None` | API key (falls back to `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `MISTRAL_API_KEY`). |
| `base_url` | `None` | Custom URL for Ollama (`OLLAMA_HOST`) or OpenAI-compatible servers. |
| `output_dir` | `"./shadow_reports"` | Folder for reports (`None` disables file output). |
| `report_format` | `"markdown"` | `"markdown"`, `"json"`, or `"both"`. Paired reports use the same filename stem. |
| `print_to_console` | `False` | Print diagnoses to `stderr` (handy in local dev). |
| `cooldown_seconds` | `300.0` | Minimum seconds before re-analyzing the same error at the same line. |
| `send_source_code` | `True` | Include source lines around the crash. |
| `send_locals` | `True` | Include redacted local variables. |
| `redact_keys` | `()` | Extra regexes for variable/key names to redact. |
| `redact_patterns` | `()` | Extra regexes for secret value patterns to scrub. |
| `request_timeout` | `60.0` | Timeout in seconds for LLM calls. |
| `shutdown_timeout` | `10.0` | Seconds to wait on process exit for pending reports to finish. |
| `max_queue_size` | `100` | Max queued incidents before dropping new ones. |
| `catch_uncaught` | `True` | Hook `sys.excepthook` and `threading.excepthook`. |

### JSON reports

Set `report_format="json"` for machine-readable reports, or `report_format="both"`
to keep Markdown and JSON side by side. Each JSON file contains:

```json
{
  "timestamp": "2026-10-18T10:30:12.418290+00:00",
  "error_context": {
    "exception_type": "ZeroDivisionError",
    "exception_message": "division by zero",
    "locals": {"order_id": 1042, "api_key": "[REDACTED]"}
  },
  "diagnosis": "### 1. Root Cause Summary\nDivision by zero."
}
```

`error_context` contains the full already-redacted incident payload, including
any captured locals, source context, metadata and traceback. It respects the
existing capture/privacy options; the serializer does not collect additional
data or redact caller-supplied contexts or diagnosis text. Nonfinite numbers
in the context become the strings `"NaN"`, `"Infinity"`, and `"-Infinity"` so
the file remains valid JSON. Diagnosis text is preserved exactly.

`ReportManager.report()` returns the JSON path in `"json"` mode, the Markdown
path in `"both"` mode, and `None` when file output is disabled. Existing
Markdown output remains the default.

---

## CLI (`list` & `show`)

Inspect local Markdown reports from the terminal:

```bash
python -m shadow_debugger list
python -m shadow_debugger show latest
python -m shadow_debugger show <filename-or-fingerprint> --dir ./shadow_reports
```

---

## Writing a custom provider

```python
import shadow_debugger
from shadow_debugger import Provider, register_provider

@register_provider("my-gateway")
class MyGatewayProvider(Provider):
    default_model = "my-model"
    env_key = "MY_GATEWAY_KEY"

    def complete(self, system: str, prompt: str) -> str:
        self.check_configuration()
        # Call your LLM endpoint and return Markdown
        return "..."

shadow_debugger.init(provider="my-gateway")
```

---

## Roadmap & contributing

I'm actively looking for feedback and contributors. A few areas where PRs are very welcome:

- [ ] Webhook / notification sinks (Slack, Discord, Telegram, GitHub Issues)
- [x] Additional providers (Mistral added; LiteLLM, AWS Bedrock welcome)
- [x] Structured JSON output option alongside Markdown reports
- [ ] Celery / background worker task integration
- [x] CLI command to browse and filter local reports in `./shadow_reports`

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup instructions.

## License

[MIT](LICENSE)
