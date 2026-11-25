from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Callable, Iterator

from src.core.errors import AppError
from src.infra.logger import AppLogger


@dataclass(slots=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    def __init__(
        self,
        logger: AppLogger,
        cancel_event: threading.Event | None = None,
        cancel_stalled_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.logger = logger
        self.cancel_event = cancel_event
        self.cancel_stalled_callback = cancel_stalled_callback
        self._ignore_cancel = False

    def check_cancelled(self) -> None:
        if not self._ignore_cancel and self.cancel_event is not None and self.cancel_event.is_set():
            raise AppError.translated("E123", "error.operation_cancelled")

    @contextmanager
    def allow_cleanup_after_cancel(self) -> Iterator[None]:
        previous, self._ignore_cancel = self._ignore_cancel, True
        try:
            yield
        finally:
            self._ignore_cancel = previous

    def run(
        self,
        command: list[str],
        *,
        check: bool = True,
        mask_values: list[str] | None = None,
        cwd: str | None = None,
        input_text: str | None = None,
    ) -> CommandResult:
        self.check_cancelled()
        masked = self._masked(command, mask_values or [])
        self.logger.debug(f"$ {' '.join(masked)}")
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if input_text is not None else None,
            text=True,
            cwd=cwd,
            start_new_session=True,
        )
        first_communicate = True
        try:
            while True:
                if not self._ignore_cancel and self.cancel_event is not None and self.cancel_event.is_set():
                    self._terminate_and_reap(process)
                    raise AppError.translated("E123", "error.operation_cancelled")
                try:
                    completed = process.communicate(
                        input=input_text if first_communicate else None,
                        timeout=0.2,
                    )
                    break
                except subprocess.TimeoutExpired:
                    first_communicate = False
        except KeyboardInterrupt:
            self._terminate_and_reap(process)
            raise
        result = CommandResult(
            command=command,
            returncode=process.returncode,
            stdout=completed[0] or "",
            stderr=completed[1] or "",
        )
        sanitized_stdout = self._sanitize_output_for_log(result.stdout)
        sanitized_stderr = self._sanitize_output_for_log(result.stderr)
        if sanitized_stdout:
            self.logger.debug(sanitized_stdout)
        if sanitized_stderr:
            self.logger.debug(sanitized_stderr)
        if check and result.returncode != 0:
            raise AppError.translated("E999", "error.command_failed", command=" ".join(masked))
        return result

    def _terminate_and_reap(self, process: subprocess.Popen[str]) -> tuple[str, str]:
        """Stop the full command session and reap it before workflow cleanup starts."""
        if process.poll() is not None:
            return process.communicate()
        stalled_notified = False

        def notify_stalled(reason: str) -> None:
            nonlocal stalled_notified
            if stalled_notified:
                return
            stalled_notified = True
            self.logger.error(reason)
            if self.cancel_stalled_callback is not None:
                try:
                    self.cancel_stalled_callback(reason)
                except Exception:
                    pass

        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            notify_stalled(f"Could not send SIGTERM to command {process.pid}: {exc}; waiting before cleanup.")
        try:
            return process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as exc:
                notify_stalled(f"Could not send SIGKILL to command {process.pid}: {exc}; cleanup remains blocked.")
        # Keep the workflow lock and inhibitor held while the killed child is
        # still present (including transient uninterruptible kernel waits).
        kill_sent_at = time.monotonic()
        while True:
            try:
                return process.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                if not stalled_notified and time.monotonic() - kill_sent_at >= 5:
                    notify_stalled(
                        f"Command {process.pid} is still running after SIGKILL; keeping the operation lock and suspend inhibitor, and not starting cleanup."
                    )
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    notify_stalled(f"Could not stop command {process.pid}: {exc}; cleanup remains blocked.")

    @staticmethod
    def _masked(command: list[str], masks: list[str]) -> list[str]:
        out = []
        for token in command:
            replaced = token
            for m in masks:
                replaced = replaced.replace(m, "***")
            out.append(replaced)
        return out

    @staticmethod
    def _sanitize_output_for_log(text: str) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        normalized = re.sub(r"[^\S\n\t]+", " ", normalized)
        normalized = "".join(ch for ch in normalized if ch in "\n\t" or ch.isprintable())
        return normalized.strip()
