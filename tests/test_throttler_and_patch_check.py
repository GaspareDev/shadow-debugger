"""Tests for ErrorThrottler (cooldown + bounded LRU) and static patch syntax checking."""

from __future__ import annotations

from pathlib import Path

from shadow_debugger.patch_check import check_patch_syntax
from shadow_debugger.throttler import ErrorThrottler


def test_throttler_cooldown_and_lru_eviction() -> None:
    now = [1000.0]
    throttler = ErrorThrottler(cooldown_seconds=60.0, max_entries=3, clock=lambda: now[0])

    err = {"exception_type": "ZeroDivisionError", "file": "app.py", "line": 42}
    ok1, fp1, count1 = throttler.should_analyze(err)
    assert (ok1, count1) == (True, 1)

    # Duplicate within cooldown window is blocked
    now[0] += 10.0
    ok2, fp2, count2 = throttler.should_analyze(err)
    assert (ok2, fp2, count2) == (False, fp1, 2)

    # After cooldown expires, analysis is allowed again
    now[0] += 60.0
    ok3, _, count3 = throttler.should_analyze(err)
    assert (ok3, count3) == (True, 3)

    # Bounded LRU eviction: inserting 4 distinct fingerprints caps size at 3
    for i in range(10):
        throttler.hit(f"fp-{i}")
    assert len(throttler) == 3


def test_patch_syntax_check_never_executes_code(tmp_path: Path) -> None:
    marker = tmp_path / "pwned.txt"
    malicious_markdown = f"""
### 3. Proposed Fix
```python
import io
io.open({str(marker)!r}, "w").write("pwned")
```
"""
    res = check_patch_syntax(malicious_markdown)
    assert res.attempted is True
    assert res.success is True
    assert not marker.exists()


def test_patch_syntax_check_detects_invalid_python() -> None:
    bad_markdown = """
```python
def broken_fix(x, y)
    return x / y
```
"""
    res = check_patch_syntax(bad_markdown)
    assert res.attempted is True
    assert res.success is False
    assert "syntax error" in res.message.lower()


def test_patch_syntax_check_strips_context_line_numbers() -> None:
    numbered_markdown = """
```python
   10: def safe_div(a, b):
>> 11:     return a / b if b else 0
```
"""
    res = check_patch_syntax(numbered_markdown)
    assert res.attempted is True
    assert res.success is True
