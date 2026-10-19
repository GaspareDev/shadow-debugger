"""Report consumers over synthetic, already-redacted data; no capture or provider calls."""

from __future__ import annotations

import copy
import inspect
import json
import logging
import math
import re
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, get_args, get_origin, get_type_hints

import pytest

import shadow_debugger
import shadow_debugger.core as core_module
import shadow_debugger.reporter as reporter_module
from shadow_debugger.config import ShadowConfig
from shadow_debugger.reporter import ReportManager


STAMP = datetime(2026, 10, 18, 12, 0, 1, 123456, tzinfo=timezone.utc)
DIAGNOSIS = "\n### Résumé\nKeep café / 例外 and [REDACTED].\n"


@pytest.fixture(autouse=True)
def synthetic_boundaries(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return STAMP if tz is None else STAMP.astimezone(tz)

    def no_network(*args, **kwargs):
        raise AssertionError("report-format tests must not use network")

    monkeypatch.setattr(reporter_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)


@pytest.fixture
def incident():
    return {
        "exception_type": "SyntheticError",
        "exception_message": "café / 例外 [REDACTED]",
        "file": "/synthetic/example.py",
        "line": 7,
        "function": "synthetic_example",
        "fingerprint": "synthetic-fixture",
        "occurrence_count": 2,
        "traceback": "SyntheticError: [REDACTED]\n",
        "extra_metadata": {
            "Authorization": "[REDACTED]",
            "values": ["例外", None, True, 3, 1.25],
        },
    }


def load_strict(path: Path):
    def reject_constant(token):
        raise ValueError(f"non-JSON numeric token: {token}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)


def as_utc(timestamp: str) -> datetime:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    return parsed.astimezone(timezone.utc)


def test_default_report_remains_markdown(tmp_path, incident):
    saved = ReportManager(str(tmp_path)).report(incident, DIAGNOSIS)
    assert isinstance(saved, str)
    path = Path(saved)
    assert path.suffix == ".md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Shadow Debugger Incident Report")
    assert DIAGNOSIS.strip() in text
    assert "[REDACTED]" in text
    assert set(tmp_path.iterdir()) == {path}


@pytest.mark.parametrize("report_format", ["json", "both"])
def test_json_consumer_preserves_envelope_and_shared_timestamp(tmp_path, incident, report_format):
    original = copy.deepcopy(incident)
    saved = ReportManager(str(tmp_path), False, report_format).report(incident, DIAGNOSIS)
    assert isinstance(saved, str)
    path = Path(saved)
    assert path.suffix == (".json" if report_format == "json" else ".md")
    json_path = path.with_suffix(".json")
    payload = load_strict(json_path)
    assert {"timestamp", "error_context", "diagnosis"} <= payload.keys()
    assert payload["error_context"] == original
    assert payload["error_context"]["extra_metadata"]["Authorization"] == "[REDACTED]"
    assert payload["diagnosis"] == DIAGNOSIS
    assert as_utc(payload["timestamp"]) == STAMP
    assert incident == original
    if report_format == "both":
        text = path.read_text(encoding="utf-8")
        match = re.search(r"\*\*Timestamp:\*\* ([^\r\n]+)", text)
        assert match is not None
        assert as_utc(match.group(1).strip()) == as_utc(payload["timestamp"])
        assert DIAGNOSIS.strip() in text
        assert json_path.stem == path.stem
        assert set(tmp_path.iterdir()) == {path, json_path}
    else:
        assert set(tmp_path.iterdir()) == {json_path}


@pytest.mark.parametrize("report_format", ["markdown", "json", "both"])
def test_disabled_output_keeps_log_and_console_announcements(
    tmp_path, monkeypatch, caplog, capsys, incident, report_format
):
    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.INFO, logger="shadow_debugger"):
        saved = ReportManager(None, True, report_format).report(incident, DIAGNOSIS)
    assert saved is None
    assert list(tmp_path.iterdir()) == []
    records = [record for record in caplog.records if hasattr(record, "shadow_incident")]
    assert len(records) == 1
    assert records[0].shadow_incident == incident
    assert records[0].shadow_analysis == DIAGNOSIS
    assert "not saved" in records[0].getMessage()
    stderr = capsys.readouterr().err
    assert "[Shadow Debugger]" in stderr
    assert DIAGNOSIS in stderr
    assert "Report written to:" not in stderr


@pytest.mark.parametrize("existing_format", ["markdown", "json"])
def test_both_reserves_a_stem_without_overwriting_opposite_sidecars(tmp_path, incident, existing_format):
    first = ReportManager(str(tmp_path), False, existing_format).report(incident, "existing synthetic report")
    assert isinstance(first, str)
    existing_path = Path(first)
    existing_bytes = existing_path.read_bytes()
    saved = ReportManager(str(tmp_path), False, "both").report(incident, DIAGNOSIS)
    assert isinstance(saved, str)
    markdown_path = Path(saved)
    json_path = markdown_path.with_suffix(".json")
    assert markdown_path.suffix == ".md"
    assert markdown_path.stem == json_path.stem != existing_path.stem
    assert existing_path.read_bytes() == existing_bytes
    assert load_strict(json_path)["diagnosis"] == DIAGNOSIS
    assert set(tmp_path.iterdir()) == {existing_path, markdown_path, json_path}


@pytest.mark.parametrize(
    "formats",
    [("both",) * 8, ("json", "both", "markdown", "both") * 2],
    ids=["both-only", "mixed-formats"],
)
def test_concurrent_reports_preserve_each_diagnosis_and_both_sidecars(tmp_path, incident, formats):
    def write(item):
        index, report_format = item
        diagnosis = f"Synthetic diagnosis {index}: café / 例外"
        saved = ReportManager(str(tmp_path), False, report_format).report(copy.deepcopy(incident), diagnosis)
        assert isinstance(saved, str)
        return Path(saved), diagnosis, report_format

    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(write, enumerate(formats)))
    paths = [path for path, _, _ in reports]
    assert len(set(paths)) == 8
    assert len(list(tmp_path.iterdir())) == sum(2 if fmt == "both" else 1 for fmt in formats)
    for path, diagnosis, report_format in reports:
        assert path.suffix == (".json" if report_format == "json" else ".md")
        if report_format != "json":
            assert diagnosis in path.read_text(encoding="utf-8")
        if report_format != "markdown":
            payload = load_strict(path.with_suffix(".json"))
            assert payload["diagnosis"] == diagnosis
            assert payload["error_context"] == incident
            assert as_utc(payload["timestamp"]) == STAMP


@pytest.mark.parametrize("report_format", ["json", "both"])
def test_nonfinite_context_values_are_strict_json_strings(tmp_path, incident, report_format):
    incident["extra_metadata"] = {
        "values": [float("nan"), {"positive": float("inf"), "negative": -float("inf")}]
    }
    saved = ReportManager(str(tmp_path), False, report_format).report(incident, DIAGNOSIS)
    assert isinstance(saved, str)
    payload = load_strict(Path(saved).with_suffix(".json"))
    assert payload["error_context"]["extra_metadata"] == {
        "values": ["NaN", {"positive": "Infinity", "negative": "-Infinity"}]
    }
    assert math.isnan(incident["extra_metadata"]["values"][0])
    assert incident["extra_metadata"]["values"][1]["positive"] == float("inf")
    assert incident["extra_metadata"]["values"][1]["negative"] == -float("inf")


@pytest.mark.parametrize("report_format", ["json", "both"])
def test_json_rejects_unsupported_objects_without_stringifying_or_creating_files(tmp_path, incident, report_format):
    class Unsupported:
        def __str__(self):
            raise AssertionError("JSON output must not invoke an arbitrary __str__")

    incident["extra_metadata"] = {"unsupported": Unsupported()}
    with pytest.raises(TypeError):
        ReportManager(str(tmp_path), False, report_format).report(incident, DIAGNOSIS)
    assert list(tmp_path.iterdir()) == []


def test_second_sidecar_reservation_error_removes_only_this_calls_empty_file(tmp_path, monkeypatch, incident):
    unrelated_empty = tmp_path / "previous-empty.json"
    unrelated_empty.write_bytes(b"")
    unrelated_completed = tmp_path / "previous-completed.md"
    unrelated_completed.write_bytes(b"existing synthetic report\n")
    real_open = open
    reserved = []
    failure = OSError("synthetic second-sidecar reservation failure")

    def injected_open(file, mode="r", *args, **kwargs):
        path = Path(file)
        if path.parent == tmp_path and mode == "x":
            if path.suffix == ".json":
                assert len(reserved) == 1
                assert reserved[0].exists() and reserved[0].stat().st_size == 0
                raise failure
            stream = real_open(file, mode, *args, **kwargs)
            reserved.append(path)
            return stream
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(reporter_module, "open", injected_open, raising=False)
    with pytest.raises(OSError, match="synthetic second-sidecar") as caught:
        ReportManager(str(tmp_path), False, "both").report(incident, DIAGNOSIS)
    assert caught.value is failure
    assert len(reserved) == 1 and not reserved[0].exists()
    assert set(tmp_path.iterdir()) == {unrelated_empty, unrelated_completed}
    assert unrelated_empty.read_bytes() == b""
    assert unrelated_completed.read_bytes() == b"existing synthetic report\n"


def test_json_write_open_error_keeps_completed_markdown_and_removes_empty_sidecar(tmp_path, monkeypatch, incident):
    unrelated_empty = tmp_path / "previous-empty.json"
    unrelated_empty.write_bytes(b"")
    real_open = open
    completed = []
    failure = OSError("synthetic JSON write-open failure")

    def injected_open(file, mode="r", *args, **kwargs):
        path = Path(file)
        if path.parent == tmp_path and path.suffix == ".json" and mode == "w":
            markdown = path.with_suffix(".md")
            markdown_bytes = markdown.read_bytes()
            assert DIAGNOSIS.strip() in markdown.read_text(encoding="utf-8")
            assert path.exists() and path.stat().st_size == 0
            completed.append((markdown, markdown_bytes, path))
            raise failure
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(reporter_module, "open", injected_open, raising=False)
    with pytest.raises(OSError, match="synthetic JSON write-open") as caught:
        ReportManager(str(tmp_path), False, "both").report(incident, DIAGNOSIS)
    assert caught.value is failure
    assert len(completed) == 1
    markdown, markdown_bytes, json_path = completed[0]
    assert markdown.read_bytes() == markdown_bytes
    assert not json_path.exists()
    assert set(tmp_path.iterdir()) == {unrelated_empty, markdown}
    assert unrelated_empty.read_bytes() == b""


def test_reservation_close_error_removes_the_created_empty_file(tmp_path, monkeypatch, incident):
    unrelated = tmp_path / "previous-empty.md"
    unrelated.write_bytes(b"")
    real_open = open
    failure = OSError("synthetic reservation close failure")

    class FailingClose:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self.stream.__enter__()

        def __exit__(self, *args):
            self.stream.__exit__(*args)
            raise failure

    def injected_open(file, mode="r", *args, **kwargs):
        stream = real_open(file, mode, *args, **kwargs)
        if Path(file).parent == tmp_path and mode == "x":
            return FailingClose(stream)
        return stream

    monkeypatch.setattr(reporter_module, "open", injected_open, raising=False)
    with pytest.raises(OSError, match="synthetic reservation close") as caught:
        ReportManager(str(tmp_path), False, "both").report(incident, DIAGNOSIS)
    assert caught.value is failure
    assert set(tmp_path.iterdir()) == {unrelated}
    assert unrelated.read_bytes() == b""


@pytest.mark.parametrize("bad_format", ["yaml", None])
def test_invalid_formats_are_rejected_by_config_and_manager(tmp_path, bad_format):
    with pytest.raises(ValueError):
        ShadowConfig(report_format=bad_format)
    with pytest.raises(ValueError):
        ReportManager(str(tmp_path), False, bad_format)


def test_config_report_format_is_a_literal_with_markdown_default():
    assert ShadowConfig().report_format == "markdown"
    annotation = get_type_hints(ShadowConfig, localns={"Provider": object})["report_format"]
    assert get_origin(annotation) is Literal
    assert set(get_args(annotation)) == {"markdown", "json", "both"}


def test_existing_public_positional_parameter_order_is_preserved():
    """Keep the public positional parameters from the baseline before report_format was added."""
    expected = [
        (ShadowConfig, (
            "provider model api_key base_url request_timeout fallback_models send_source_code send_locals "
            "redact_keys redact_patterns output_dir print_to_console check_patch_syntax cooldown_seconds "
            "max_tracked_errors max_workers max_queue_size shutdown_timeout context_lines"
        ).split()),
        (shadow_debugger.init, (
            "provider model api_key base_url output_dir print_to_console cooldown_seconds verify_patches "
            "check_patch_syntax max_queue_size max_workers catch_uncaught request_timeout shutdown_timeout "
            "send_source_code send_locals redact_keys redact_patterns fallback_models config"
        ).split()),
        (ReportManager, ["output_dir", "print_to_console"]),
    ]
    for api, names in expected:
        parameters = inspect.signature(api).parameters
        assert list(parameters)[:len(names)] == names
        assert all(parameters[name].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for name in names)


@pytest.mark.parametrize("report_format", [None, "markdown", "json", "both"])
def test_init_passes_report_format_to_config_without_initializing_a_real_core(monkeypatch, report_format):
    def fake_core(*, config):
        return SimpleNamespace(config=config)

    monkeypatch.setattr(shadow_debugger, "ShadowCore", fake_core)
    monkeypatch.setattr(shadow_debugger, "_CORE_INSTANCE", None)
    kwargs = {} if report_format is None else {"report_format": report_format}
    core = shadow_debugger.init(output_dir=None, catch_uncaught=False, **kwargs)
    assert core.config.report_format == ("markdown" if report_format is None else report_format)


@pytest.mark.parametrize("report_format", ["markdown", "json", "both"])
def test_core_passes_report_format_without_providers_or_worker_threads(monkeypatch, report_format):
    class NoWorker:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    def fake_reporter(output_dir=None, print_to_console=False, report_format="markdown"):
        return SimpleNamespace(output_dir=output_dir, print_to_console=print_to_console, report_format=report_format)

    monkeypatch.setattr(core_module, "AIAnalyzer", lambda **kwargs: object())
    monkeypatch.setattr(core_module, "Redactor", lambda *args: object())
    monkeypatch.setattr(core_module, "ErrorThrottler", lambda *args: object())
    monkeypatch.setattr(core_module, "ReportManager", fake_reporter)
    monkeypatch.setattr(
        core_module,
        "threading",
        SimpleNamespace(Lock=threading.Lock, Event=threading.Event, Thread=NoWorker),
    )
    monkeypatch.setattr(core_module, "atexit", SimpleNamespace(register=lambda callback: None))
    config = ShadowConfig(output_dir=None, print_to_console=True, report_format=report_format)
    core = core_module.ShadowCore(config=config)
    assert core.reporter.output_dir is None
    assert core.reporter.print_to_console is True
    assert core.reporter.report_format == report_format
