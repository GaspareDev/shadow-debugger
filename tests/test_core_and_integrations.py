"""End-to-end tests for ShadowCore, @watch (sync + async), capture_exception, uncaught crashes, and web middlewares."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import List

import shadow_debugger
from shadow_debugger.core import ShadowCore
from shadow_debugger.integrations import DjangoWatcherMiddleware, FastAPIWatcherMiddleware
from shadow_debugger.providers import Provider


class FakeLLMProvider(Provider):
    name = "fake"
    default_model = "fake-fast-1"
    requires_api_key = False

    def __init__(self) -> None:
        super().__init__()
        self.prompts: List[str] = []

    def complete(self, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return (
            "### 1. Root Cause Summary\nDivision by zero.\n\n"
            "### 3. Proposed Fix\n```python\nresult = a / b if b else 0\n```\n"
        )


def test_capture_exception_respects_explicit_argument_inside_another_except(tmp_path: Path) -> None:
    provider = FakeLLMProvider()
    shadow_debugger.init(provider=provider, output_dir=str(tmp_path))
    try:
        explicit_exc = ValueError("the explicit value error I passed")
        try:
            raise KeyError("unrelated active exception")
        except KeyError:
            shadow_debugger.capture_exception(explicit_exc)

        shadow_debugger.flush(timeout=5.0)
        assert len(provider.prompts) == 1
        assert "ValueError: the explicit value error I passed" in provider.prompts[0]
        assert "KeyError" not in provider.prompts[0]
    finally:
        shadow_debugger.shutdown(timeout=2.0)


def test_watch_supports_sync_and_async_functions(tmp_path: Path) -> None:
    provider = FakeLLMProvider()
    shadow_debugger.init(provider=provider, output_dir=str(tmp_path), cooldown_seconds=0)
    try:

        @shadow_debugger.watch(reraise=False)
        def sync_fn(x: int) -> float:
            return 10 / x

        @shadow_debugger.watch(reraise=False)
        async def async_fn(y: int) -> float:
            await asyncio.sleep(0.01)
            return 20 / y

        assert sync_fn(0) is None
        assert asyncio.run(async_fn(0)) is None

        shadow_debugger.flush(timeout=5.0)
        assert len(provider.prompts) == 2
        reports = list(tmp_path.glob("incident_*.md"))
        assert len(reports) == 2
    finally:
        shadow_debugger.shutdown(timeout=2.0)


def test_bounded_queue_and_pre_capture_throttling(tmp_path: Path) -> None:
    provider = FakeLLMProvider()
    core = ShadowCore(
        provider=provider,
        output_dir=str(tmp_path),
        cooldown_seconds=300.0,
        max_queue_size=5,
    )
    try:
        for _ in range(50):
            try:
                1 / 0  # noqa: B018
            except ZeroDivisionError as exc:
                core.capture_exception(exc)

        core.flush(timeout=5.0)
        assert len(provider.prompts) == 1
        assert core.dropped_incidents == 0
    finally:
        core.shutdown(timeout=2.0)


def test_uncaught_exception_flushes_report_before_process_exit(tmp_path: Path) -> None:
    script = textwrap.dedent(
        f"""
        import time
        import shadow_debugger
        from shadow_debugger.providers import Provider

        class SlowProvider(Provider):
            name = "slow"
            default_model = "slow-1"
            requires_api_key = False
            def complete(self, system: str, prompt: str) -> str:
                time.sleep(0.4)
                return "### 1. Root Cause Summary\\nUncaught crash analyzed."

        shadow_debugger.init(provider=SlowProvider(), output_dir={str(tmp_path)!r})
        raise RuntimeError("boom on main thread")
        """
    )
    import os

    src_dir = str(Path(__file__).resolve().parents[1] / "src")
    env = {**os.environ, "PYTHONPATH": src_dir}
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert proc.returncode != 0, proc.stderr
    reports = list(tmp_path.glob("incident_*_RuntimeError_*.md"))
    assert len(reports) == 1, f"stderr={proc.stderr}"
    assert "Uncaught crash analyzed." in reports[0].read_text(encoding="utf-8")


def test_fastapi_middleware_captures_500_and_redacts_headers(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    provider = FakeLLMProvider()
    core = ShadowCore(provider=provider, output_dir=str(tmp_path))
    try:
        app = FastAPI()
        app.add_middleware(FastAPIWatcherMiddleware, core=core)

        @app.get("/divide/{n}")
        async def divide(n: int) -> dict:
            return {"res": 100 / n}

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/divide/0?api_key=TOPSECRET",
            headers={"Authorization": "Bearer super_secret_header_token_12345"},
        )
        assert resp.status_code == 500

        core.flush(timeout=5.0)
        assert len(provider.prompts) == 1
        prompt = provider.prompts[0]
        assert "/divide/0" in prompt
        assert "TOPSECRET" not in prompt
        assert "super_secret_header_token_12345" not in prompt
    finally:
        core.shutdown(timeout=2.0)


def test_django_middleware_ignores_http404_and_captures_500(tmp_path: Path) -> None:
    provider = FakeLLMProvider()
    core = ShadowCore(provider=provider, output_dir=str(tmp_path))
    try:
        mw = DjangoWatcherMiddleware(lambda req: None, core=core)

        class Http404(Exception):
            pass

        class DummyRequest:
            method = "POST"
            path = "/orders/create"
            GET = {"token": "secret123"}
            user = "alice"

        assert mw.process_exception(DummyRequest(), Http404("not found")) is None
        assert provider.prompts == []

        mw.process_exception(DummyRequest(), ZeroDivisionError("order total zero"))
        core.flush(timeout=5.0)
        assert len(provider.prompts) == 1
        assert "/orders/create" in provider.prompts[0]
        assert "secret123" not in provider.prompts[0]
    finally:
        core.shutdown(timeout=2.0)


def test_flask_integration_ignores_404_and_captures_500(tmp_path: Path, monkeypatch) -> None:
    import types

    handlers = []

    class FakeSignal:
        def connect(self, fn, app, weak=False):
            handlers.append(fn)

    fake_flask = types.ModuleType("flask")
    fake_flask.got_request_exception = FakeSignal()  # type: ignore[attr-defined]
    fake_flask.request = types.SimpleNamespace(  # type: ignore[attr-defined]
        method="GET",
        url="http://localhost/crash?token=SECRET_FLASK_TOKEN",
        path="/crash",
        endpoint="crash_view",
        remote_addr="127.0.0.1",
        args={"token": "SECRET_FLASK_TOKEN"},
        headers={"Authorization": "Bearer secret_flask_header_1234567890"},
    )
    monkeypatch.setitem(sys.modules, "flask", fake_flask)

    provider = FakeLLMProvider()
    core = ShadowCore(provider=provider, output_dir=str(tmp_path))
    try:
        shadow_debugger.init_flask(object(), core=core)
        assert len(handlers) == 1
        handler = handlers[0]

        class NotFound(Exception):
            code = 404

        # 404 must be ignored
        handler(object(), exception=NotFound("not found"))
        assert provider.prompts == []

        # 500 must be captured with redacted URL/headers
        handler(object(), exception=RuntimeError("database pool exhausted"))
        core.flush(timeout=5.0)
        assert len(provider.prompts) == 1
        assert "/crash" in provider.prompts[0]
        assert "SECRET_FLASK_TOKEN" not in provider.prompts[0]
        assert "secret_flask_header_1234567890" not in provider.prompts[0]
    finally:
        core.shutdown(timeout=2.0)

