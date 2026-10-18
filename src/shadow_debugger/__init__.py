"""Shadow Debugger: non-blocking AI error diagnosis for Python applications and web APIs."""

from __future__ import annotations

import inspect
import logging
import sys
import threading
import warnings
from functools import wraps
from typing import Any, Callable, Dict, Optional, Sequence, TypeVar, Union, overload

from ._version import __version__
from .config import ShadowConfig
from .core import ShadowCore
from .integrations import (
    ASGIWatcherMiddleware,
    DjangoWatcherMiddleware,
    FastAPIWatcherMiddleware,
    init_flask,
)
from .providers import (
    Provider,
    ProviderError,
    available_providers,
    create_provider,
    register_provider,
)

logger = logging.getLogger("shadow_debugger")
logger.addHandler(logging.NullHandler())

_STATE_LOCK = threading.Lock()
_CORE_INSTANCE: Optional[ShadowCore] = None
_HOOKS_INSTALLED = False
_ORIGINAL_SYS_EXCEPTHOOK: Optional[Callable] = None
_ORIGINAL_THREADING_EXCEPTHOOK: Optional[Callable] = None

F = TypeVar("F", bound=Callable[..., Any])


def init(
    provider: Union[str, Provider] = "gemini",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    output_dir: Optional[str] = "./shadow_reports",
    print_to_console: bool = False,
    cooldown_seconds: float = 300.0,
    verify_patches: Optional[bool] = None,
    check_patch_syntax: bool = True,
    max_queue_size: int = 100,
    max_workers: int = 2,
    catch_uncaught: bool = True,
    request_timeout: float = 60.0,
    shutdown_timeout: float = 10.0,
    send_source_code: bool = True,
    send_locals: bool = True,
    redact_keys: Sequence[str] = (),
    redact_patterns: Sequence[str] = (),
    fallback_models: Sequence[str] = (),
    config: Optional[ShadowConfig] = None,
) -> ShadowCore:
    """Initialize (or re-initialize) Shadow Debugger.

    If called again, the previous background worker pool is cleanly shut down first.
    """
    global _CORE_INSTANCE

    if verify_patches is not None:
        warnings.warn(
            "'verify_patches' is deprecated and no longer executes code in a subprocess; "
            "use 'check_patch_syntax' for static AST syntax verification.",
            DeprecationWarning,
            stacklevel=2,
        )
        check_patch_syntax = bool(verify_patches)

    if config is None:
        config = ShadowConfig(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            output_dir=output_dir,
            print_to_console=print_to_console,
            cooldown_seconds=cooldown_seconds,
            check_patch_syntax=check_patch_syntax,
            max_queue_size=max_queue_size,
            max_workers=max_workers,
            request_timeout=request_timeout,
            shutdown_timeout=shutdown_timeout,
            send_source_code=send_source_code,
            send_locals=send_locals,
            redact_keys=redact_keys,
            redact_patterns=redact_patterns,
            fallback_models=fallback_models,
        )

    with _STATE_LOCK:
        old_core = _CORE_INSTANCE
        _CORE_INSTANCE = ShadowCore(config=config)
        if catch_uncaught:
            _install_global_hooks_locked()

    if old_core is not None:
        old_core.shutdown(timeout=2.0)

    return _CORE_INSTANCE


def get_core(auto_init: bool = True) -> Optional[ShadowCore]:
    """Return the active :class:`ShadowCore` instance.

    If ``init()`` was never called and ``auto_init=True``, initializes with default settings
    (without installing global excepthooks behind the user's back) and logs a warning.
    """
    global _CORE_INSTANCE
    if _CORE_INSTANCE is not None and not _CORE_INSTANCE.is_closed:
        return _CORE_INSTANCE
    if not auto_init:
        return None
    with _STATE_LOCK:
        if _CORE_INSTANCE is None or _CORE_INSTANCE.is_closed:
            logger.warning(
                "shadow_debugger.init() was not called explicitly; initializing with default settings."
            )
            _CORE_INSTANCE = ShadowCore()
        return _CORE_INSTANCE


def capture_exception(
    exc: Optional[BaseException] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Capture an exception and enqueue it for background AI diagnosis.

    If ``exc`` is explicitly provided, that exact exception is always captured (even when
    called from inside another ``except`` block). If omitted, uses ``sys.exc_info()``.
    """
    core = get_core()
    if core is None:
        return None

    if exc is not None:
        return core.capture(type(exc), exc, exc.__traceback__, extra_metadata=extra_metadata)

    exc_type, exc_value, exc_tb = sys.exc_info()
    if exc_type is not None and exc_value is not None:
        return core.capture(exc_type, exc_value, exc_tb, extra_metadata=extra_metadata)
    return None


@overload
def watch(func: F) -> F: ...


@overload
def watch(
    *,
    reraise: bool = True,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Callable[[F], F]: ...


def watch(
    func: Optional[F] = None,
    *,
    reraise: bool = True,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Any:
    """Decorator that captures unhandled exceptions from sync or async functions.

    Can be used as ``@shadow_debugger.watch`` or ``@shadow_debugger.watch(reraise=False)``.
    """

    def decorator(target: F) -> F:
        if inspect.iscoroutinefunction(target):

            @wraps(target)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await target(*args, **kwargs)
                except Exception as exc:
                    capture_exception(exc, extra_metadata=extra_metadata)
                    if reraise:
                        raise
                    return None

            return async_wrapper  # type: ignore[return-value]

        @wraps(target)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return target(*args, **kwargs)
            except Exception as exc:
                capture_exception(exc, extra_metadata=extra_metadata)
                if reraise:
                    raise
                return None

        return sync_wrapper  # type: ignore[return-value]

    if func is not None and callable(func):
        return decorator(func)
    return decorator


def flush(timeout: Optional[float] = None) -> bool:
    """Wait until all queued incidents have been analyzed."""
    if _CORE_INSTANCE is None:
        return True
    return _CORE_INSTANCE.flush(timeout=timeout)


def shutdown(timeout: Optional[float] = None) -> bool:
    """Flush pending incidents and stop background worker threads."""
    global _CORE_INSTANCE
    with _STATE_LOCK:
        core = _CORE_INSTANCE
        _CORE_INSTANCE = None
    if core is None:
        return True
    return core.shutdown(timeout=timeout)


def _install_global_hooks_locked() -> None:
    global _HOOKS_INSTALLED, _ORIGINAL_SYS_EXCEPTHOOK, _ORIGINAL_THREADING_EXCEPTHOOK
    if _HOOKS_INSTALLED:
        return

    _ORIGINAL_SYS_EXCEPTHOOK = sys.excepthook
    _ORIGINAL_THREADING_EXCEPTHOOK = getattr(threading, "excepthook", None)

    def _sys_excepthook(exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            core = _CORE_INSTANCE
            if core is not None and not core.is_closed:
                core.capture(exc_type, exc_value, exc_tb, extra_metadata={"uncaught": True})
                # The process is about to exit; flush right here so the report is written.
                core.flush(timeout=core.config.shutdown_timeout)
        if _ORIGINAL_SYS_EXCEPTHOOK is not None:
            _ORIGINAL_SYS_EXCEPTHOOK(exc_type, exc_value, exc_tb)

    def _threading_excepthook(args: Any) -> None:
        exc_type = getattr(args, "exc_type", None)
        exc_value = getattr(args, "exc_value", None)
        exc_tb = getattr(args, "exc_traceback", None)
        thread = getattr(args, "thread", None)
        thread_name = getattr(thread, "name", "")
        if (
            exc_type is not None
            and exc_value is not None
            and not issubclass(exc_type, (KeyboardInterrupt, SystemExit))
            and not thread_name.startswith("ShadowDebugger-")
        ):
            core = _CORE_INSTANCE
            if core is not None and not core.is_closed:
                core.capture(
                    exc_type,
                    exc_value,
                    exc_tb,
                    extra_metadata={"uncaught": True, "thread": thread_name},
                )
        if _ORIGINAL_THREADING_EXCEPTHOOK is not None:
            _ORIGINAL_THREADING_EXCEPTHOOK(args)

    sys.excepthook = _sys_excepthook
    if _ORIGINAL_THREADING_EXCEPTHOOK is not None:
        threading.excepthook = _threading_excepthook
    _HOOKS_INSTALLED = True


__all__ = [
    "__version__",
    "ASGIWatcherMiddleware",
    "DjangoWatcherMiddleware",
    "FastAPIWatcherMiddleware",
    "Provider",
    "ProviderError",
    "ShadowConfig",
    "ShadowCore",
    "available_providers",
    "capture_exception",
    "create_provider",
    "flush",
    "get_core",
    "init",
    "init_flask",
    "register_provider",
    "shutdown",
    "watch",
]
