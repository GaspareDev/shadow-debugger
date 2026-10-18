"""Web framework integrations: FastAPI / Starlette (pure ASGI), Flask, and Django.

All integrations attach sanitized HTTP request metadata to the incident and re-raise the
exception so the framework's normal error handling (status code, response body, logging)
is preserved.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qsl, urlencode

from ..core import ShadowCore

_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-csrf-token",
    "x-xsrf-token",
}


def _resolve_core(core: Optional[ShadowCore]) -> Optional[ShadowCore]:
    if core is not None:
        return core
    from .. import get_core

    return get_core()


def _sanitize_headers(raw_headers: Any) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    try:
        items = raw_headers.items() if hasattr(raw_headers, "items") else raw_headers
        for k, v in items:
            key = k.decode("latin-1") if isinstance(k, (bytes, bytearray)) else str(k)
            val = v.decode("latin-1") if isinstance(v, (bytes, bytearray)) else str(v)
            if key.lower() in _SENSITIVE_HEADER_NAMES:
                headers[key] = "[REDACTED]"
            else:
                headers[key] = val[:200]
    except Exception:
        pass
    return headers


class ASGIWatcherMiddleware:
    """Pure ASGI middleware compatible with FastAPI, Starlette, Litestar, Quart, etc.

    Unlike Starlette's ``BaseHTTPMiddleware``, a pure ASGI middleware does not buffer
    streaming responses, break ``contextvars``, or interfere with background tasks.
    """

    def __init__(self, app: Any, core: Optional[ShadowCore] = None) -> None:
        self.app = app
        self.core = core

    async def __call__(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            active_core = _resolve_core(self.core)
            if active_core is not None:
                http_meta = _extract_asgi_metadata(scope)
                exc_type, exc_value, exc_tb = sys.exc_info()
                if exc_type is not None and exc_value is not None:
                    active_core.capture(
                        exc_type,
                        exc_value,
                        exc_tb,
                        extra_metadata={"http_request": http_meta},
                    )
                else:
                    active_core.capture_exception(exc, extra_metadata={"http_request": http_meta})
            raise


# Alias kept for backward compatibility and clarity in FastAPI apps.
FastAPIWatcherMiddleware = ASGIWatcherMiddleware


def _extract_asgi_metadata(scope: Dict[str, Any]) -> Dict[str, Any]:
    method = scope.get("method", "UNKNOWN")
    scheme = scope.get("scheme", "http")
    path = scope.get("path", "")
    raw_qs = scope.get("query_string", b"")
    qs = raw_qs.decode("latin-1", errors="replace") if isinstance(raw_qs, (bytes, bytearray)) else str(raw_qs)

    headers = _sanitize_headers(scope.get("headers") or [])
    host = headers.get("host") or headers.get("Host") or "localhost"
    url = f"{scheme}://{host}{path}" + (f"?{qs}" if qs else "")

    client = scope.get("client")
    client_ip = client[0] if isinstance(client, (list, tuple)) and client else "unknown"
    query_params = dict(parse_qsl(qs, keep_blank_values=True)) if qs else {}

    return {
        "framework": "ASGI/FastAPI",
        "http_method": method,
        "url": url,
        "path": path,
        "client_ip": client_ip,
        "query_params": query_params,
        "headers": headers,
    }


def init_flask(app: Any, core: Optional[ShadowCore] = None) -> None:
    """Attach Shadow Debugger to a Flask app without breaking 404/4xx HTTPExceptions.

    Usage::

        app = Flask(__name__)
        shadow_debugger.init_flask(app)
    """
    try:
        from flask import got_request_exception, request
    except ImportError as exc:
        raise ImportError("Flask is not installed. Run: pip install 'shadow-debugger[flask]'") from exc

    def _on_request_exception(sender: Any, exception: BaseException, **extra: Any) -> None:
        # Ignore standard 4xx Werkzeug HTTPExceptions (404 Not Found, 405 Method Not Allowed, etc.)
        status_code = getattr(exception, "code", None)
        if isinstance(status_code, int) and status_code < 500:
            return

        active_core = _resolve_core(core)
        if active_core is None:
            return

        try:
            http_meta = {
                "framework": "Flask",
                "http_method": getattr(request, "method", "UNKNOWN"),
                "url": getattr(request, "url", ""),
                "path": getattr(request, "path", ""),
                "endpoint": getattr(request, "endpoint", None),
                "client_ip": getattr(request, "remote_addr", "unknown"),
                "query_params": dict(getattr(request, "args", {}) or {}),
                "headers": _sanitize_headers(getattr(request, "headers", {}) or {}),
            }
        except Exception:
            http_meta = {"framework": "Flask"}

        active_core.capture(
            type(exception),
            exception,
            exception.__traceback__,
            extra_metadata={"http_request": http_meta},
        )

    got_request_exception.connect(_on_request_exception, app, weak=False)


class DjangoWatcherMiddleware:
    """Django middleware that captures unhandled view exceptions (HTTP 500).

    Add to ``MIDDLEWARE`` in ``settings.py``::

        MIDDLEWARE = [
            "shadow_debugger.integrations.DjangoWatcherMiddleware",
            ...
        ]
    """

    sync_capable = True
    async_capable = False

    def __init__(self, get_response: Callable, core: Optional[ShadowCore] = None) -> None:
        self.get_response = get_response
        self.core = core

    def __call__(self, request: Any) -> Any:
        return self.get_response(request)

    def process_exception(self, request: Any, exception: BaseException) -> None:
        # Skip Django's Http404 / PermissionDenied
        exc_name = type(exception).__name__
        if exc_name in ("Http404", "PermissionDenied", "SuspiciousOperation"):
            return None

        active_core = _resolve_core(self.core)
        if active_core is None:
            return None

        try:
            query_params = dict(getattr(request, "GET", {}) or {})
            path = getattr(request, "path", "")
            qs = urlencode(query_params, doseq=True) if query_params else ""
            http_meta = {
                "framework": "Django",
                "http_method": getattr(request, "method", "UNKNOWN"),
                "path": path,
                "url": f"{path}?{qs}" if qs else path,
                "query_params": query_params,
                "user": str(getattr(request, "user", "anonymous")),
            }
        except Exception:
            http_meta = {"framework": "Django"}

        active_core.capture(
            type(exception),
            exception,
            exception.__traceback__,
            extra_metadata={"http_request": http_meta},
        )
        return None


__all__ = [
    "ASGIWatcherMiddleware",
    "FastAPIWatcherMiddleware",
    "init_flask",
    "DjangoWatcherMiddleware",
]
