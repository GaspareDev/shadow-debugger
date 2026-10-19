"""Tests for the shadow_debugger CLI (list / show)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing}" if existing else SRC_DIR
    return subprocess.run(
        [sys.executable, "-m", "shadow_debugger", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


SAMPLE_REPORT = """# Shadow Debugger Incident Report

**Timestamp:** 2026-10-18T12:34:56+00:00  
**Exception:** `ValueError`: something went wrong  
**Location:** `app.py:42` in `handle`  
**Fingerprint:** `abc12345` (occurrences so far: 1)  

---

Analysis text here.

---
### Raw Traceback (redacted)
```text
Traceback...
```
"""


def test_list_and_show(tmp_path: Path) -> None:
    reports = tmp_path / "shadow_reports"
    reports.mkdir()
    (reports / "incident_20261018_123456_ValueError_abc12345.md").write_text(SAMPLE_REPORT, encoding="utf-8")
    (reports / "incident_20261018_130000_TypeError_def67890.md").write_text(
        SAMPLE_REPORT.replace("ValueError", "TypeError").replace("abc12345", "def67890"), encoding="utf-8"
    )

    # list
    result = _run_cli("list", "--dir", str(reports))
    assert result.returncode == 0
    assert "ValueError" in result.stdout
    assert "TypeError" in result.stdout
    assert "abc12345" in result.stdout or "abc12345"[:8] in result.stdout

    # show latest
    result = _run_cli("show", "latest", "--dir", str(reports))
    assert result.returncode == 0
    assert "Shadow Debugger Incident Report" in result.stdout
    # latest is the one sorted reverse (130000 after 123456)
    assert "TypeError" in result.stdout

    # show by fragment
    result = _run_cli("show", "abc12345", "--dir", str(reports))
    assert result.returncode == 0
    assert "ValueError" in result.stdout


def test_empty_dir(tmp_path: Path) -> None:
    reports = tmp_path / "empty"
    reports.mkdir()
    result = _run_cli("list", "--dir", str(reports))
    assert result.returncode == 0
    assert "No reports found." in result.stdout
