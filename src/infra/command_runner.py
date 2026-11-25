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
from pathlib import Path

from src.core.errors import AppError
from src.infra.logger import AppLogger


@dataclass(slots=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    TERMINATION_GRACE_SECONDS = 3.0

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
                    # communicate() may finish when the leader exits even if a
                    # descendant closed its inherited pipes. Cancellation can
                    # arrive while communicate() is blocked.
                    if not self._ignore_cancel and self.cancel_event is not None and self.cancel_event.is_set():
                        self._terminate_and_reap(process)
                        raise AppError.translated("E123", "error.operation_cancelled")
                    # Keep ownership of the process group through completion:
                    # a descendant may outlive the command leader and continue
                    # writing to the target during later workflow phases.
                    if self._process_group_alive(process.pid):
                        completed = self._terminate_and_reap(process)
                    if not self._ignore_cancel and self.cancel_event is not None and self.cancel_event.is_set():
                        raise AppError.translated("E123", "error.operation_cancelled")
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

        def signal_group(sig: int, label: str) -> None:
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            except OSError as exc:
                notify_stalled(f"Could not send {label} to command group {process.pid}: {exc}; cleanup remains blocked.")

        # Always address the process group, even if the command leader exited
        # first. Descendants may have closed inherited pipes but still be alive.
        signal_group(signal.SIGTERM, "SIGTERM")
        started_at = time.monotonic()
        kill_sent = False
        kill_started_at = 0.0
        last_kill_at = 0.0
        while True:
            try:
                output = process.communicate(timeout=0.2)
            except subprocess.TimeoutExpired:
                output = None

            group_alive = self._process_group_alive(process.pid)
            if output is not None and not group_alive:
                return output

            elapsed = time.monotonic() - started_at
            if not kill_sent and elapsed >= self.TERMINATION_GRACE_SECONDS:
                signal_group(signal.SIGKILL, "SIGKILL")
                kill_sent = True
                kill_started_at = time.monotonic()
                last_kill_at = time.monotonic()
            elif kill_sent and group_alive and time.monotonic() - last_kill_at >= 0.5:
                # Repeat in case a member entered a transient uninterruptible
                # wait while the previous signal was pending.
                signal_group(signal.SIGKILL, "SIGKILL")
                last_kill_at = time.monotonic()

            if kill_sent and not stalled_notified and time.monotonic() - kill_started_at >= 5:
                notify_stalled(
                    f"Command group {process.pid} is still running after SIGKILL; keeping the operation lock and suspend inhibitor, and not starting cleanup."
                )
            if output is not None:
                time.sleep(0.1)

    @staticmethod
    def _process_group_alive(process_group_id: int) -> bool:
        """Check for executable members, excluding zombies that cannot do work."""
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        proc = Path("/proc")
        if not proc.is_dir():
            return True
        observed_member = False
        try:
            entries = list(proc.iterdir())
        except OSError:
            return True
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                contents = (entry / "stat").read_text(encoding="ascii")
                end = contents.rfind(")")
                fields = contents[end + 2 :].split()
                state, group_id = fields[0], int(fields[2])
            except (OSError, ValueError, IndexError):
                continue
            if group_id == process_group_id:
                observed_member = True
                if state not in {"Z", "X"}:
                    return True
        # If /proc hides the group, don't mistake lack of visibility for exit.
        return not observed_member

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
