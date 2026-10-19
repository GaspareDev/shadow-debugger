"""CLI for inspecting local Shadow Debugger reports.

Usage:
    python -m shadow_debugger list [--dir PATH]
    python -m shadow_debugger show latest [--dir PATH]
    python -m shadow_debugger show <filename> [--dir PATH]
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _reports_dir(path: Optional[str]) -> Path:
    return Path(path or "./shadow_reports").resolve()


def _parse_report(path: Path) -> Dict[str, Any]:
    """Extract key fields from a report Markdown file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    result: Dict[str, Any] = {
        "path": path,
        "filename": path.name,
        "timestamp": None,
        "exception_type": None,
        "exception_message": None,
        "location": None,
        "fingerprint": None,
    }

    # Timestamp from content or filename
    ts_match = re.search(r"\*\*Timestamp:\*\*\s*([^\n]+)", text)
    if ts_match:
        result["timestamp"] = ts_match.group(1).strip()
    else:
        # Fallback: incident_YYYYMMDD_HHMMSS_...
        name_match = re.match(r"incident_(\d{8}_\d{6})", path.name)
        if name_match:
            try:
                dt = datetime.strptime(name_match.group(1), "%Y%m%d_%H%M%S")
                result["timestamp"] = dt.isoformat()
            except ValueError:
                pass

    exc_match = re.search(r"\*\*Exception:\*\*\s*`([^`]+)`:\s*([^\n]+)", text)
    if exc_match:
        result["exception_type"] = exc_match.group(1).strip()
        result["exception_message"] = exc_match.group(2).strip()

    loc_match = re.search(r"\*\*Location:\*\*\s*`([^`]+)`", text)
    if loc_match:
        result["location"] = loc_match.group(1).strip()

    fp_match = re.search(r"\*\*Fingerprint:\*\*\s*`([^`]+)`", text)
    if fp_match:
        result["fingerprint"] = fp_match.group(1).strip()

    return result


def list_reports(reports_dir: Path) -> List[dict]:
    if not reports_dir.is_dir():
        return []
    reports = []
    for path in sorted(reports_dir.glob("*.md"), reverse=True):
        reports.append(_parse_report(path))
    return reports


def print_table(reports: List[dict]) -> None:
    if not reports:
        print("No reports found.")
        return

    # Simple aligned table
    headers = ("Timestamp", "Exception", "Location", "Fingerprint")
    rows = []
    for r in reports:
        ts = (r["timestamp"] or "")[:19]
        exc = r["exception_type"] or "?"
        loc = r["location"] or "?"
        fp = (r["fingerprint"] or "")[:8]
        rows.append((ts, exc, loc, fp))

    widths = [max(len(h), max((len(row[i]) for row in rows), default=0)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)

    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))


def show_report(reports_dir: Path, which: str) -> int:
    reports = list_reports(reports_dir)
    if not reports:
        print("No reports found.", file=sys.stderr)
        return 1

    if which == "latest":
        target = reports[0]
    else:
        # Match by filename (exact or suffix)
        candidates = [r for r in reports if which in r["filename"]]
        if not candidates:
            print(f"No report matching '{which}'.", file=sys.stderr)
            return 1
        target = candidates[0]

    print(target["path"].read_text(encoding="utf-8", errors="replace"))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m shadow_debugger", description="Inspect local Shadow Debugger reports.")
    parser.add_argument("--dir", default=None, help="Reports directory (default: ./shadow_reports)")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List recent incident reports")
    list_parser.add_argument("--dir", dest="list_dir", default=None)

    show_parser = sub.add_parser("show", help="Print a report")
    show_parser.add_argument("which", help="'latest' or a filename / fragment")
    show_parser.add_argument("--dir", dest="show_dir", default=None)

    args = parser.parse_args(argv)
    reports_dir = _reports_dir(args.dir or getattr(args, "list_dir", None) or getattr(args, "show_dir", None))

    if args.command == "list":
        reports = list_reports(reports_dir)
        print_table(reports)
        return 0
    if args.command == "show":
        return show_report(reports_dir, args.which)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
