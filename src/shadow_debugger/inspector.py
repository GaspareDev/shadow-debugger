"""Collects a redacted snapshot of an exception: location, source, locals, traceback."""

from __future__ import annotations

import hashlib
import linecache
import os
import platform
import sys
import sysconfig
import traceback
from types import FrameType, TracebackType
from typing import Any, Dict, List, Optional, Tuple, Type

from .redaction import Redactor

MAX_TRACEBACK_CHARS = 8000
MAX_MESSAGE_CHARS = 2000

_DEFAULT_REDACTOR = Redactor()


def redact_sensitive_data(key: str, value: Any, max_depth: int = 4) -> Any:
    """Convenience wrapper around :class:`Redactor` for single-value sanitization."""
    redactor = _DEFAULT_REDACTOR if max_depth == 4 else Redactor(max_depth=max_depth)
    return redactor.sanitize(key, value)


_PACKAGE_DIR = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))


def _library_prefixes() -> Tuple[str, ...]:
    prefixes = set()
    for name in ("stdlib", "platstdlib", "purelib", "platlib"):
        path = sysconfig.get_paths().get(name)
        if path:
            prefixes.add(os.path.normcase(os.path.abspath(path)))
    return tuple(prefixes)


_LIBRARY_PREFIXES = _library_prefixes()


def is_user_code(filename: str) -> bool:
    """True if the file belongs to the application rather than stdlib/site-packages/us."""
    if not filename or filename.startswith("<"):
        return False
    path = os.path.normcase(os.path.abspath(filename))
    if path.startswith(_PACKAGE_DIR):
        return False
    if "site-packages" in path or "dist-packages" in path:
        return False
    return not any(path.startswith(prefix) for prefix in _LIBRARY_PREFIXES)


def _walk(tb: Optional[TracebackType]) -> List[Tuple[FrameType, int]]:
    frames = []
    while tb is not None:
        frames.append((tb.tb_frame, tb.tb_lineno))
        tb = tb.tb_next
    return frames


def select_frame(tb: Optional[TracebackType]) -> Optional[Tuple[FrameType, int]]:
    """The most relevant frame: the deepest one in user code, else the deepest overall."""
    frames = _walk(tb)
    if not frames:
        return None
    for frame, lineno in reversed(frames):
        if is_user_code(frame.f_code.co_filename):
            return frame, lineno
    return frames[-1]


def compute_fingerprint(exc_type: Type[BaseException], filename: str, lineno: int) -> str:
    raw = f"{getattr(exc_type, '__qualname__', exc_type)}:{filename}:{lineno}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def fingerprint_exception(exc_type: Type[BaseException], exc_tb: Optional[TracebackType]) -> str:
    """Cheap fingerprint (no locals, no source) used for throttling before full capture."""
    selected = select_frame(exc_tb)
    if selected is None:
        return compute_fingerprint(exc_type, "", -1)
    frame, lineno = selected
    return compute_fingerprint(exc_type, frame.f_code.co_filename, lineno)


def extract_code_context(filepath: str, lineno: int, context_lines: int = 5) -> str:
    if not filepath or lineno <= 0:
        return ""
    start = max(1, lineno - context_lines)
    lines = []
    for i in range(start, lineno + context_lines + 1):
        line = linecache.getline(filepath, i)
        if not line and i > lineno:
            break
        marker = ">> " if i == lineno else "   "
        lines.append(f"{marker}{i:4d}: {line.rstrip()}")
    return "\n".join(lines)


def capture_exception_context(
    exc_type: Type[BaseException],
    exc_value: BaseException,
    exc_tb: Optional[TracebackType],
    extra_metadata: Optional[Dict[str, Any]] = None,
    redactor: Optional[Redactor] = None,
    context_lines: int = 5,
    include_source: bool = True,
    include_locals: bool = True,
) -> Dict[str, Any]:
    """Build the (redacted) incident payload. Never raises."""
    redactor = redactor or Redactor()

    try:
        formatted_tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    except Exception:  # pragma: no cover - defensive
        formatted_tb = f"{exc_type.__name__}: <traceback unavailable>"
    if len(formatted_tb) > MAX_TRACEBACK_CHARS:
        # Keep the tail: that's where the actual error is.
        formatted_tb = "... (truncated)\n" + formatted_tb[-MAX_TRACEBACK_CHARS:]

    try:
        message = str(exc_value)
    except Exception:
        message = "<unprintable exception message>"

    error_file, error_line, function = "", -1, ""
    code_context = ""
    locals_dict: Dict[str, Any] = {}

    selected = select_frame(exc_tb)
    if selected is not None:
        frame, error_line = selected
        error_file = frame.f_code.co_filename
        function = frame.f_code.co_name
        if include_source:
            code_context = redactor.redact_text(extract_code_context(error_file, error_line, context_lines))
        if include_locals:
            for name, value in list(frame.f_locals.items()):
                try:
                    locals_dict[name] = redactor.sanitize(name, value)
                except Exception:
                    locals_dict[name] = f"<{type(value).__name__} (unserializable)>"

    deepest = _walk(exc_tb)[-1] if exc_tb is not None else None
    raised_in = None
    if deepest is not None and selected is not None and deepest[0] is not selected[0]:
        raised_in = f"{deepest[0].f_code.co_filename}:{deepest[1]} in {deepest[0].f_code.co_name}"

    metadata: Dict[str, Any] = {}
    for key, value in (extra_metadata or {}).items():
        try:
            metadata[str(key)] = redactor.sanitize(str(key), value)
        except Exception:
            metadata[str(key)] = "<unserializable>"

    return {
        "exception_type": getattr(exc_type, "__name__", str(exc_type)),
        "exception_message": redactor.redact_text(message, limit=MAX_MESSAGE_CHARS),
        "traceback": redactor.redact_text(formatted_tb),
        "file": error_file,
        "line": error_line,
        "function": function,
        "raised_in": raised_in,
        "code_context": code_context,
        "locals": locals_dict,
        "extra_metadata": metadata,
        "fingerprint": compute_fingerprint(exc_type, error_file, error_line),
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": sys.platform,
        },
    }
