"""Writes diagnoses to Markdown/JSON files, the logger and (optionally) stderr."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from .config import REPORT_FORMATS, ReportFormat

logger = logging.getLogger("shadow_debugger")

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


def _json_context(value: Any) -> Any:
    """Keep already-redacted data, representing nonfinite numbers as JSON strings."""
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {key: _json_context(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_context(item) for item in value]
    return value


class ReportManager:
    def __init__(
        self,
        output_dir: Optional[str] = "./shadow_reports",
        print_to_console: bool = False,
        report_format: ReportFormat = "markdown",
    ) -> None:
        if report_format not in REPORT_FORMATS:
            raise ValueError("report_format must be 'markdown', 'json', or 'both'")
        self.output_dir = output_dir
        self.print_to_console = print_to_console
        self.report_format = report_format
        self._dir_lock = threading.Lock()
        self._dir_ready = False

    def _ensure_dir(self) -> str:
        assert self.output_dir is not None
        # Created lazily: importing/initializing the library must not touch the filesystem.
        with self._dir_lock:
            if not self._dir_ready:
                os.makedirs(self.output_dir, exist_ok=True)
                self._dir_ready = True
        return self.output_dir

    @staticmethod
    def _remove_empty_reservations(paths: Sequence[str]) -> None:
        for path in paths:
            try:
                if os.path.getsize(path) == 0:
                    os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning("Could not remove empty report reservation %s", path, exc_info=True)

    def _new_report_paths(
        self, error_context: Dict[str, Any], now: datetime, extensions: Sequence[str]
    ) -> List[str]:
        directory = self._ensure_dir()
        exc_type = _UNSAFE_FILENAME_CHARS.sub("_", str(error_context.get("exception_type", "Error")))[:60]
        fingerprint = str(error_context.get("fingerprint", ""))[:8] or "nofp"
        base = f"incident_{now.strftime('%Y%m%d_%H%M%S')}_{exc_type}_{fingerprint}"
        for suffix in range(1000):
            stem = f"{base}{'' if suffix == 0 else f'_{suffix}'}"
            paths = [os.path.join(directory, f"{stem}.{extension}") for extension in extensions]
            reserved: List[str] = []
            try:
                for path in paths:
                    # Reserve every sidecar before writing; never overwrite another incident.
                    with open(path, "x", encoding="utf-8"):
                        reserved.append(path)
            except FileExistsError:
                self._remove_empty_reservations(reserved)
                continue
            except Exception:
                self._remove_empty_reservations(reserved)
                raise
            return paths
        raise OSError(f"Could not allocate a unique report filename for {base}")

    def render(self, error_context: Dict[str, Any], ai_analysis: str, timestamp: datetime) -> str:
        exc_type = error_context.get("exception_type", "Error")
        parts = [
            "# Shadow Debugger Incident Report",
            "",
            f"**Timestamp:** {timestamp.isoformat()}  ",
            f"**Exception:** `{exc_type}`: {error_context.get('exception_message')}  ",
            f"**Location:** `{error_context.get('file')}:{error_context.get('line')}`"
            f" in `{error_context.get('function') or '?'}`  ",
            f"**Fingerprint:** `{error_context.get('fingerprint')}`"
            f" (occurrences so far: {error_context.get('occurrence_count', 1)})  ",
        ]
        if error_context.get("model"):
            parts.append(f"**Model:** `{error_context['model']}`  ")
        parts += ["", "---", "", ai_analysis.strip(), ""]

        check = error_context.get("patch_check")
        if check and check.get("attempted"):
            status = "Passed" if check.get("success") else "Failed"
            parts += ["### Proposed Code Syntax Check", f"**Status:** {status}. {check.get('message')}", ""]

        if error_context.get("extra_metadata"):
            parts += [
                "### Request / Application Metadata",
                "```json",
                json.dumps(error_context["extra_metadata"], indent=2, ensure_ascii=False, default=str),
                "```",
                "",
            ]

        parts += ["---", "### Raw Traceback (redacted)", "```text", str(error_context.get("traceback", "")).rstrip(), "```", ""]
        return "\n".join(parts)

    def report(self, error_context: Dict[str, Any], ai_analysis: str) -> Optional[str]:
        """Persist and announce a diagnosis; return the Markdown path in ``both`` mode.

        JSON mode returns its JSON path. With file output disabled, return None.
        The caller supplies the already-redacted context; serialization does not
        capture or redact additional data.
        """
        now = datetime.now(timezone.utc)
        exc_type = error_context.get("exception_type", "Error")
        saved_path: Optional[str] = None

        if self.output_dir:
            contents: List[str] = []
            extensions: List[str] = []
            if self.report_format in ("json", "both"):
                contents.append(
                    json.dumps(
                        {
                            "timestamp": now.isoformat(),
                            "error_context": _json_context(error_context),
                            "diagnosis": ai_analysis,
                        },
                        indent=2,
                        ensure_ascii=False,
                        allow_nan=False,
                    ) + "\n"
                )
                extensions.append("json")
            if self.report_format in ("markdown", "both"):
                # Validate JSON before Markdown's legacy object-string fallback,
                # while retaining Markdown as the primary output in both mode.
                contents.insert(0, self.render(error_context, ai_analysis, now))
                extensions.insert(0, "md")
            # Render first so serialization errors do not leave reserved files.
            paths = self._new_report_paths(error_context, now, extensions)
            try:
                for path, content in zip(paths, contents):
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(content)
            except Exception:
                self._remove_empty_reservations(paths)
                raise
            saved_path = paths[0]

        logger.info(
            "Incident diagnosed: %s: %s (report: %s)",
            exc_type,
            error_context.get("exception_message"),
            saved_path or "not saved",
            extra={"shadow_incident": error_context, "shadow_analysis": ai_analysis},
        )

        if self.print_to_console:
            self._print(error_context, ai_analysis, saved_path)
        return saved_path

    @staticmethod
    def _print(error_context: Dict[str, Any], ai_analysis: str, saved_path: Optional[str]) -> None:
        bar = "=" * 60
        lines = [
            "",
            bar,
            "[Shadow Debugger] Incident diagnosed in background",
            f"Exception: {error_context.get('exception_type')}: {error_context.get('exception_message')}",
        ]
        check = error_context.get("patch_check")
        if check and check.get("attempted"):
            lines.append(f"Syntax check: {check.get('message')}")
        if saved_path:
            lines.append(f"Report written to: {saved_path}")
        lines += [bar, ai_analysis, bar, ""]
        text = "\n".join(lines)
        stream = sys.stderr
        try:
            stream.write(text + "\n")
        except UnicodeEncodeError:
            encoding = getattr(stream, "encoding", None) or "ascii"
            stream.write(text.encode(encoding, errors="replace").decode(encoding) + "\n")
        stream.flush()
