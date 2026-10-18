"""Background engine: bounded queue + worker threads that never block the host application."""

from __future__ import annotations

import atexit
import dataclasses
import logging
import queue
import threading
import time
from types import TracebackType
from typing import Any, Dict, List, Optional, Type

from .analyzer import AIAnalyzer
from .config import ShadowConfig
from .inspector import capture_exception_context, compute_fingerprint, fingerprint_exception
from .patch_check import check_patch_syntax
from .providers import ProviderError
from .redaction import Redactor
from .reporter import ReportManager
from .throttler import ErrorThrottler

logger = logging.getLogger("shadow_debugger")

_CAPTURED_FLAG = "__shadow_debugger_captured__"


class ShadowCore:
    """Owns the analysis pipeline. Usually created through :func:`shadow_debugger.init`.

    Accepts either a :class:`ShadowConfig` or the same keyword arguments::

        ShadowCore(provider="ollama", max_queue_size=10)
    """

    def __init__(self, config: Optional[ShadowConfig] = None, **overrides: Any) -> None:
        if "verify_patches" in overrides:
            vp = overrides.pop("verify_patches")
            overrides.setdefault("check_patch_syntax", bool(vp))
        if config is None:
            config = ShadowConfig(**overrides)
        elif overrides:
            config = dataclasses.replace(config, **overrides)
        self.config = config

        self.redactor = Redactor(config.redact_keys, config.redact_patterns)
        self.analyzer = AIAnalyzer(
            provider=config.provider,
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.request_timeout,
            fallback_models=config.fallback_models,
        )
        self.reporter = ReportManager(
            output_dir=config.output_dir,
            print_to_console=config.print_to_console,
            report_format=config.report_format,
        )
        self.throttler = ErrorThrottler(config.cooldown_seconds, config.max_tracked_errors)

        # Bounded queue: an error flood can never exhaust memory.
        self.queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=config.max_queue_size)
        self._lock = threading.Lock()
        self._dropped = 0
        self._closed = False
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        for i in range(config.max_workers):
            t = threading.Thread(target=self._worker_loop, name=f"ShadowDebugger-{i}", daemon=True)
            t.start()
            self._threads.append(t)

        atexit.register(self._atexit)

    @property
    def dropped_incidents(self) -> int:
        with self._lock:
            return self._dropped

    @property
    def _dropped_incidents_count(self) -> int:
        return self.dropped_incidents

    @property
    def is_closed(self) -> bool:
        return self._closed

    def capture(
        self,
        exc_type: Type[BaseException],
        exc_value: BaseException,
        exc_tb: Optional[TracebackType],
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Hot-path entry point. Returns the error fingerprint, or None if ignored.

        Never raises: a bug in the debugger must never break the host application.
        """
        try:
            if self._closed:
                return None
            if getattr(exc_value, _CAPTURED_FLAG, False):
                return None  # already captured by an inner layer (decorator + middleware)
            try:
                setattr(exc_value, _CAPTURED_FLAG, True)
            except Exception:
                pass

            # Throttle BEFORE the expensive part (locals serialization, source reading).
            fingerprint = fingerprint_exception(exc_type, exc_tb)
            should_analyze, count = self.throttler.hit(fingerprint)
            if not should_analyze:
                logger.debug("Throttled duplicate %s (seen %d times, %s)", exc_type.__name__, count, fingerprint)
                return fingerprint

            context = capture_exception_context(
                exc_type,
                exc_value,
                exc_tb,
                extra_metadata,
                redactor=self.redactor,
                context_lines=self.config.context_lines,
                include_source=self.config.send_source_code,
                include_locals=self.config.send_locals,
            )
            context["occurrence_count"] = count
            self._enqueue(context)
            return fingerprint
        except Exception:  # pragma: no cover - defensive
            logger.exception("Shadow Debugger failed to capture an exception")
            return None

    def capture_exception(
        self, exc: BaseException, extra_metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        return self.capture(type(exc), exc, exc.__traceback__, extra_metadata)

    def submit_incident(self, error_context: Dict[str, Any]) -> None:
        """Queue a pre-built incident dict (advanced use). Applies throttling."""
        fingerprint = error_context.get("fingerprint") or compute_fingerprint(
            type(str(error_context.get("exception_type", ""))),  # type: ignore[arg-type]
            str(error_context.get("file", "")),
            int(error_context.get("line", -1) or -1),
        )
        should_analyze, count = self.throttler.hit(fingerprint)
        if not should_analyze:
            return
        error_context = {**error_context, "fingerprint": fingerprint, "occurrence_count": count}
        self._enqueue(error_context)

    def flush(self, timeout: Optional[float] = None) -> bool:
        """Block until all queued incidents are processed. Returns False on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self.queue.all_tasks_done:
            while self.queue.unfinished_tasks:
                if deadline is None:
                    self.queue.all_tasks_done.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.queue.all_tasks_done.wait(remaining)
        return True

    def shutdown(self, timeout: Optional[float] = None, wait: Optional[bool] = None) -> bool:
        """Stop accepting incidents, wait up to ``timeout`` s for pending ones, stop workers.

        ``timeout=None`` uses ``config.shutdown_timeout``. Returns True if everything finished.
        """
        if self._closed:
            return True
        self._closed = True
        if wait is False:
            timeout = 0.0
        elif timeout is None:
            timeout = self.config.shutdown_timeout
        finished = self.flush(timeout) if timeout > 0 else (self.queue.unfinished_tasks == 0)
        if not finished:
            logger.warning(
                "Shadow Debugger shutdown: %d incident(s) were still being analyzed after %.1fs and were abandoned.",
                self.queue.unfinished_tasks,
                timeout,
            )
        self._stop.set()
        for t in self._threads:
            t.join(timeout=1.0)
        try:
            atexit.unregister(self._atexit)
        except Exception:  # pragma: no cover
            pass
        return finished

    def _atexit(self) -> None:
        pending = self.queue.unfinished_tasks
        if pending:
            logger.warning(
                "Shadow Debugger: waiting up to %.0fs for %d pending diagnosis(es) before exit...",
                self.config.shutdown_timeout,
                pending,
            )
        self.shutdown()

    def _enqueue(self, context: Dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(context)
        except queue.Full:
            with self._lock:
                self._dropped += 1
                dropped = self._dropped
            if dropped == 1 or dropped % 100 == 0:
                logger.warning(
                    "Shadow Debugger queue is full (%d). Dropped incident %s; total dropped: %d",
                    self.queue.maxsize,
                    context.get("exception_type", "?"),
                    dropped,
                )

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                context = self.queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._process(context)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Shadow Debugger worker crashed while processing an incident")
            finally:
                self.queue.task_done()

    def _process(self, context: Dict[str, Any]) -> None:
        try:
            analysis = self.analyzer.analyze(context)
        except ProviderError as exc:
            logger.error("Shadow Debugger could not analyze %s: %s", context.get("exception_type"), exc)
            return
        except Exception:
            logger.exception("Shadow Debugger: unexpected provider failure")
            return

        context["model"] = self.analyzer.model
        if self.config.check_patch_syntax:
            context["patch_check"] = check_patch_syntax(analysis).to_dict()
        try:
            self.reporter.report(context, analysis)
        except Exception:
            logger.exception("Shadow Debugger failed to write the report")
