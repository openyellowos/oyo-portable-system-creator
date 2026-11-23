from __future__ import annotations

import argparse
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from types import TracebackType

from src.core.errors import AppError


def _process_start_time(pid: int) -> int | None:
    """Return Linux /proc start time, avoiding false matches after PID reuse."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stat_file:
            stat = stat_file.read()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    # comm (field 2) may contain spaces or parentheses; fields after its final ')'
    # begin with field 3 (state), so starttime (field 22) is item 19.
    fields = stat[stat.rfind(")") + 2 :].split()
    try:
        state = fields[0]
        if state in {"Z", "X", "x"}:
            return None
        return int(fields[19])
    except (IndexError, ValueError):
        return None


def _watch_parent(pid: int, start_time: int) -> int:
    if _process_start_time(pid) != start_time:
        return 1
    print("READY", flush=True)
    while _process_start_time(pid) == start_time:
        time.sleep(0.5)
    return 0


class SuspendInhibitor:
    """Hold a systemd sleep inhibitor for a workflow and its cleanup."""

    def __init__(self, *, startup_timeout: float = 10.0) -> None:
        self.startup_timeout = startup_timeout
        self._process: subprocess.Popen[str] | None = None

    def __enter__(self) -> SuspendInhibitor:
        executable = shutil.which("systemd-inhibit")
        if executable is None:
            raise AppError.translated(
                "E122", "error.required_commands_missing", commands="systemd-inhibit"
            )

        parent_pid = os.getpid()
        parent_start_time = _process_start_time(parent_pid)
        if parent_start_time is None:
            raise RuntimeError("Cannot identify the current process for suspend inhibition")

        command = [
            executable,
            # sleep blocks explicit suspend/hibernate requests; idle separately
            # blocks logind's automatic IdleAction handling.
            "--what=sleep:idle",
            "--mode=block",
            "--no-ask-password",
            "--who=oyo-portable-system-creator",
            "--why=Creating a portable system on USB",
            sys.executable,
            "-m",
            "src.infra.suspend_inhibitor",
            "--watch-parent",
            str(parent_pid),
            str(parent_start_time),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise RuntimeError(f"Could not start systemd-inhibit: {exc}") from exc
        self._process = process

        try:
            ready, _, _ = select.select([process.stdout], [], [], self.startup_timeout)
            if ready and process.stdout is not None and process.stdout.readline().strip() == "READY":
                return self
            stderr = ""
            if process.poll() is not None and process.stderr is not None:
                stderr = process.stderr.read().strip()
            detail = stderr or "systemd-inhibit did not acquire the sleep lock in time"
            raise RuntimeError(detail)
        except BaseException:
            self._stop_process()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop_process()

    def _stop_process(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch-parent", nargs=2, metavar=("PID", "START_TIME"))
    args = parser.parse_args(argv)
    if args.watch_parent is None:
        return 2
    pid, start_time = (int(value) for value in args.watch_parent)
    return _watch_parent(pid, start_time)


if __name__ == "__main__":
    raise SystemExit(main())
