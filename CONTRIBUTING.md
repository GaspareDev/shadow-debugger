# Contributing

Contributions, bug reports, and feature ideas are welcome!

## Local setup

```bash
git clone https://github.com/GaspareDev/shadow-debugger.git
cd shadow-debugger
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e ".[all,dev]"
```

Run the test suite and linters before opening a PR:

```bash
pytest
ruff check src tests
mypy
```

## Adding a provider

Each provider lives in its own file under `src/shadow_debugger/providers/`. Subclass `Provider`, decorate with `@register_provider("name")`, and implement `complete(self, system: str, prompt: str) -> str`:

```python
from .base import Provider, ProviderError, register_provider

@register_provider("mistral")
class MistralProvider(Provider):
    default_model = "mistral-small-latest"
    env_key = "MISTRAL_API_KEY"

    def complete(self, system: str, prompt: str) -> str:
        self.check_configuration()
        # Call the API and return the response text, or raise ProviderError
```

Then import the module in `src/shadow_debugger/providers/__init__.py` and add a unit test in `tests/test_providers_and_analyzer.py`.
