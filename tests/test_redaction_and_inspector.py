"""Tests for two-layer secret redaction, side-effect-free serialization, and user-frame selection."""

from __future__ import annotations

import json
import sys

from shadow_debugger.inspector import capture_exception_context, redact_sensitive_data
from shadow_debugger.redaction import REDACTED, Redactor


def test_key_redaction_and_no_false_positive_on_author() -> None:
    r = Redactor()
    assert r.sanitize("password", "hunter2") == REDACTED
    assert r.sanitize("user_auth_token", "xyz") == REDACTED
    assert r.sanitize("api_key", "12345") == REDACTED
    # 'author' / 'authority' contain 'auth' as a substring and shouldn't be masked
    assert r.sanitize("author", "Mario Rossi") == "Mario Rossi"
    assert r.sanitize("authority", " municipal ") == " municipal "


def test_value_redaction_in_urls_jwts_and_api_keys() -> None:
    r = Redactor()
    url = "https://example.com/callback?access_token=SUPERSECRET123&state=ok"
    sanitized_url = r.sanitize("url", url)
    assert "SUPERSECRET123" not in sanitized_url
    assert "access_token=[REDACTED]" in sanitized_url
    assert "state=ok" in sanitized_url

    # JWT
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    assert r.sanitize("payload", jwt) == REDACTED

    # OpenAI / Google keys and Bearer headers inside arbitrary text
    msg = "Failed calling with Bearer abcdef0123456789xyz and key AIzaSyA123456789012345678901234567890"
    scrubbed = r.redact_text(msg)
    assert "abcdef0123456789xyz" not in scrubbed
    assert "AIzaSyA" not in scrubbed


def test_recursive_dict_and_circular_reference_safety() -> None:
    cyclic: dict = {"normal": "ok", "nested": {"client_secret": "shh"}}
    cyclic["self"] = cyclic
    out = redact_sensitive_data("data", cyclic)
    assert out["normal"] == "ok"
    assert out["nested"]["client_secret"] == REDACTED
    assert "circular reference" in out["self"]


def test_lazy_object_never_calls_repr_or_str() -> None:
    class ExplodingQuerySet:
        def __init__(self) -> None:
            self.model = "Order"
            self.api_token = "secret_token"

        def __repr__(self) -> str:
            raise RuntimeError("DB query triggered inside __repr__!")

        def __str__(self) -> str:
            raise RuntimeError("DB query triggered inside __str__!")

    obj = ExplodingQuerySet()
    sanitized = Redactor().sanitize("queryset", obj)
    assert sanitized["model"] == "Order"
    assert sanitized["api_token"] == REDACTED


def test_inspector_selects_user_frame_over_stdlib_frame() -> None:
    # json.loads raises inside the stdlib 'json.decoder' module,
    # but the inspector must select THIS user function's frame and locals.
    bad_json_input = "{invalid json"
    try:
        json.loads(bad_json_input)
    except Exception:
        exc_type, exc_val, exc_tb = sys.exc_info()
        assert exc_type is not None and exc_val is not None
        ctx = capture_exception_context(exc_type, exc_val, exc_tb)

    assert ctx["function"] == "test_inspector_selects_user_frame_over_stdlib_frame"
    assert ctx["locals"].get("bad_json_input") == "{invalid json"
    assert ctx["raised_in"] is not None
    assert "json" in ctx["raised_in"]
