from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path
from types import TracebackType

from src.core.errors import AppError


LOCK_PATH = Path("/run/oyo-portable-system-creator.lock")


class OperationLock:
    """Host-wide advisory lock shared by CLI and GUI workflow processes."""

    def __init__(self, path: Path = LOCK_PATH) -> None:
        self.path = path
        self._fd: int | None = None

    def __enter__(self) -> OperationLock:
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.path, flags, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                os.close(fd)
                raise AppError.translated(
                    "E124",
                    "error.operation_lock_unavailable",
                    reason="lock path is not a private caller-owned regular file",
                )
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                os.close(fd)
                raise AppError.translated("E124", "error.operation_in_progress") from exc
            self._fd = fd
            return self
        except AppError:
            raise
        except OSError as exc:
            raise AppError.translated(
                "E124", "error.operation_lock_unavailable", reason=str(exc)
            ) from exc

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
