"""Writes diagnoses to Markdown files, the ``shadow_debugger`` logger and (optionally) stderr."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("shadow_debugger")

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


class ReportManager:
    def __init__(self, output_dir: Optional[str] = "./shadow_reports", print_to_console: bool = False) -> None:
        self.output_dir = output_dir
        self.print_to_console = print_to_console
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

    def _new_report_path(self, error_context: Dict[str, Any], now: datetime) -> str:
        directory = self._ensure_dir()
        exc_type = _UNSAFE_FILENAME_CHARS.sub("_", str(error_context.get("exception_type", "Error")))[:60]
        fingerprint = str(error_context.get("fingerprint", ""))[:8] or "nofp"
        base = f"incident_{now.strftime('%Y%m%d_%H%M%S')}_{exc_type}_{fingerprint}"
        for suffix in range(1000):
            path = os.path.join(directory, f"{base}{'' if suffix == 0 else f'_{suffix}'}.md")
            try:
                # 'x' mode fails if the file exists: no silent overwrite between workers.
                with open(path, "x", encoding="utf-8"):
                    return path
            except FileExistsError:
                continue
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
        """Persist and announce a diagnosis. Returns the report path (or None)."""
        now = datetime.now(timezone.utc)
        exc_type = error_context.get("exception_type", "Error")
        saved_path: Optional[str] = None

        if self.output_dir:
            saved_path = self._new_report_path(error_context, now)
            with open(saved_path, "w", encoding="utf-8") as fh:
                fh.write(self.render(error_context, ai_analysis, now))

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
