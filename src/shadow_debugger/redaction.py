"""Secret scrubbing and safe serialization for local variables and request metadata."""

from __future__ import annotations

import datetime as _dt
import decimal
import enum
import fractions
import itertools
import pathlib
import re
import types
import uuid
from typing import Any, Iterable, List, Optional, Pattern

REDACTED = "[REDACTED]"

_DEFAULT_KEY_PATTERN = (
    r"passw|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|"
    r"priv(?:ate)?[_-]?key|credential|bearer|cookie|session[_-]?id|"
    r"credit[_-]?card|card[_-]?number|cvv|ssn|signature|"
    # Match 'auth' as a word or 'authorization', so keys like 'author' are left alone
    r"(?:^|[^a-z])auth(?:$|[^a-z]|orization)"
)

_DEFAULT_VALUE_PATTERNS: List[str] = [
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}",
    r"\bsk-[A-Za-z0-9_-]{16,}",
    r"\bAIza[0-9A-Za-z_-]{30,}",
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    r"\bgithub_pat_[A-Za-z0-9_]{30,}",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{10,}",
]

_PREFIXED_PATTERNS: List[Pattern[str]] = [
    re.compile(r"(?i)\b((?:bearer|basic)\s+)[A-Za-z0-9\-._~+/]{16,}=*"),
    re.compile(
        r"(?i)([?&;](?:[^=&\s#]*?(?:token|key|secret|passw|pwd|auth|signature|sig|code|session)"
        r"[^=&\s#]*)=)[^&\s#'\"]+"
    ),
    re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s/]+(?=@)"),
    re.compile(
        r"(?i)((?:passw(?:or)?d|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key)"
        r"[\"']?\s*[=:]\s*)([\"'])[^\"'\n]{3,}\2"
    ),
    re.compile(
        r"(?i)((?:passw(?:or)?d|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key)"
        r"\s*[=:]\s*)(?![\"'])[^\s'\",;&()]{8,}(?![(\w])"
    ),
]

_SAFE_REPR_TYPES = (
    _dt.datetime,
    _dt.date,
    _dt.time,
    _dt.timedelta,
    decimal.Decimal,
    fractions.Fraction,
    uuid.UUID,
    complex,
    pathlib.PurePath,
    enum.Enum,
    range,
    type,
    types.ModuleType,
    types.FunctionType,
    types.BuiltinFunctionType,
    types.MethodType,
    BaseException,
)


class Redactor:
    """Turns runtime values into JSON-safe structures while stripping secrets."""

    def __init__(
        self,
        extra_key_patterns: Iterable[str] = (),
        extra_value_patterns: Iterable[str] = (),
        max_depth: int = 4,
        max_items: int = 30,
        max_string: int = 300,
    ) -> None:
        key_sources = [_DEFAULT_KEY_PATTERN, *extra_key_patterns]
        self._key_re = re.compile("|".join(f"(?:{p})" for p in key_sources), re.IGNORECASE)
        self._value_res = [re.compile(p) for p in (*_DEFAULT_VALUE_PATTERNS, *extra_value_patterns)]
        self.max_depth = max_depth
        self.max_items = max_items
        self.max_string = max_string

    def is_sensitive_key(self, key: str) -> bool:
        return bool(key) and bool(self._key_re.search(key))

    def redact_text(self, text: Optional[str], limit: Optional[int] = None) -> str:
        if not text:
            return text or ""
        for pattern in _PREFIXED_PATTERNS:
            text = pattern.sub(lambda m: m.group(1) + REDACTED, text)
        for pattern in self._value_res:
            text = pattern.sub(REDACTED, text)
        if limit is not None and len(text) > limit:
            text = text[:limit] + f"... (truncated, {len(text) - limit} more chars)"
        return text

    def sanitize(self, key: str, value: Any, _depth: int = 0, _seen: Optional[set] = None) -> Any:
        if self.is_sensitive_key(str(key)):
            return REDACTED

        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            return self.redact_text(value[: self.max_string * 4], limit=self.max_string)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return f"<{type(value).__name__} len={len(value)}>"

        if _depth >= self.max_depth:
            return f"<{_type_name(value)} ... (max depth)>"

        seen = _seen if _seen is not None else set()
        if id(value) in seen:
            return f"<{_type_name(value)} (circular reference)>"

        if isinstance(value, dict):
            seen.add(id(value))
            try:
                result = {}
                for i, (k, v) in enumerate(value.items()):
                    if i >= self.max_items:
                        result["..."] = f"{len(value) - self.max_items} more keys"
                        break
                    result[str(k)] = self.sanitize(str(k), v, _depth + 1, seen)
                return result
            finally:
                seen.discard(id(value))

        if isinstance(value, (list, tuple, set, frozenset)):
            seen.add(id(value))
            try:
                out = [
                    self.sanitize("", item, _depth + 1, seen)
                    for item in itertools.islice(value, self.max_items)
                ]
                if len(value) > self.max_items:
                    out.append(f"... ({len(value) - self.max_items} more items)")
                return out
            finally:
                seen.discard(id(value))

        if isinstance(value, _SAFE_REPR_TYPES):
            return self.redact_text(_safe_repr(value), limit=self.max_string)

        # For arbitrary objects (like Django QuerySets), read __dict__ directly
        # instead of calling repr()/str(), which could trigger lazy DB queries.
        attrs = _instance_dict(value)
        if attrs is None:
            return f"<{_type_name(value)}>"
        seen.add(id(value))
        try:
            public = {k: v for k, v in attrs.items() if not k.startswith("__")}
            return {"__type__": _type_name(value), **self.sanitize("", public, _depth + 1, seen)}
        finally:
            seen.discard(id(value))


def _type_name(value: Any) -> str:
    cls = type(value)
    module = getattr(cls, "__module__", "")
    if module in ("builtins", "", None):
        return cls.__qualname__
    return f"{module}.{cls.__qualname__}"


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception:
        return f"<{_type_name(value)} (repr failed)>"


def _instance_dict(value: Any) -> Optional[dict]:
    try:
        d = object.__getattribute__(value, "__dict__")
    except Exception:
        return None
    return d if isinstance(d, dict) else None
