"""Deduplicates identical errors so the LLM is not called repeatedly for the same bug."""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Tuple


class ErrorThrottler:
    """Thread-safe, memory-bounded (LRU) cooldown tracker keyed by error fingerprint."""

    def __init__(
        self,
        cooldown_seconds: float = 300,
        max_entries: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cooldown_seconds = cooldown_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        # fingerprint -> [last_analyzed_timestamp, occurrence_count]
        self._seen: "OrderedDict[str, list]" = OrderedDict()

    @staticmethod
    def compute_fingerprint(error_context: Dict[str, Any]) -> str:
        if error_context.get("fingerprint"):
            return str(error_context["fingerprint"])
        exc_type = str(error_context.get("exception_type", ""))
        filepath = str(error_context.get("file", ""))
        lineno = str(error_context.get("line", ""))
        raw_key = f"{exc_type}:{filepath}:{lineno}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]

    def hit(self, fingerprint: str) -> Tuple[bool, int]:
        """Record an occurrence. Returns ``(should_analyze, occurrence_count)``."""
        now = self._clock()
        with self._lock:
            record = self._seen.get(fingerprint)
            if record is None:
                self._seen[fingerprint] = [now, 1]
                if len(self._seen) > self.max_entries:
                    self._seen.popitem(last=False)
                return True, 1

            self._seen.move_to_end(fingerprint)
            record[1] += 1
            if now - record[0] >= self.cooldown_seconds:
                record[0] = now
                return True, record[1]
            return False, record[1]

    def should_analyze(self, error_context: Dict[str, Any]) -> Tuple[bool, str, int]:
        """Check and update cooldown state for ``error_context``."""
        fingerprint = self.compute_fingerprint(error_context)
        allowed, count = self.hit(fingerprint)
        return allowed, fingerprint, count

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)
