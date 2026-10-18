"""Configuration for Shadow Debugger."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Sequence, Union

if TYPE_CHECKING:
    from .providers.base import Provider


@dataclass
class ShadowConfig:
    """Runtime settings passed to ``shadow_debugger.init()`` or ``ShadowCore``."""

    provider: Union[str, "Provider"] = "gemini"
    model: Optional[str] = None
    api_key: Optional[str] = field(default=None, repr=False)
    base_url: Optional[str] = None
    request_timeout: float = 60.0
    fallback_models: Sequence[str] = ()

    # Privacy controls
    send_source_code: bool = True
    send_locals: bool = True
    redact_keys: Sequence[str] = ()
    redact_patterns: Sequence[str] = ()

    # Output
    output_dir: Optional[str] = "./shadow_reports"
    print_to_console: bool = False
    check_patch_syntax: bool = True

    # Worker & throttling limits
    cooldown_seconds: float = 300.0
    max_tracked_errors: int = 1000
    max_workers: int = 2
    max_queue_size: int = 100
    shutdown_timeout: float = 10.0
    context_lines: int = 5

    def __post_init__(self) -> None:
        if self.max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if self.max_queue_size < 1:
            raise ValueError("max_queue_size must be >= 1")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")
        if self.max_tracked_errors < 1:
            raise ValueError("max_tracked_errors must be >= 1")
        if self.request_timeout <= 0:
            raise ValueError("request_timeout must be > 0")
        if self.shutdown_timeout < 0:
            raise ValueError("shutdown_timeout must be >= 0")
        if self.context_lines < 0:
            raise ValueError("context_lines must be >= 0")
