from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

ProgressListener = Callable[[int, str], None]


@dataclass(slots=True)
class ExecutionState:
    mode: str = "create"
    source_device: str | None = None
    target_device: str | None = None
    used_bytes: int = 0
    required_bytes: int = 0
    options: dict[str, Any] = field(default_factory=dict)
    progress_percent: int = 0
    current_step: str = ""
    error_code: str | None = None
    error_message: str | None = None
    mounted_paths: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    progress_listeners: list[ProgressListener] = field(default_factory=list, repr=False)

    def add_progress_listener(self, listener: ProgressListener) -> None:
        self.progress_listeners.append(listener)

    def set_progress(self, percent: int, step: str) -> None:
        self.progress_percent = max(0, min(100, percent))
        self.current_step = step
        for listener in list(self.progress_listeners):
            listener(self.progress_percent, self.current_step)
