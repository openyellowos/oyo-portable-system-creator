from __future__ import annotations

import unittest
import threading
import time
import sys
import os
import tempfile
from pathlib import Path

from src.core.errors import AppError
from src.infra.command_runner import CommandRunner


class DummyLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, msg: str) -> None:
        self.messages.append(msg)


class CommandRunnerTests(unittest.TestCase):
    def test_sanitize_output_for_log_removes_control_characters(self) -> None:
        text = "Discarding device blocks:\r0/31126784\x08\x08\x08done\n\x00next line"

        sanitized = CommandRunner._sanitize_output_for_log(text)

        self.assertEqual(sanitized, "Discarding device blocks:\n0/31126784done\nnext line")

    def test_cancellation_terminates_and_reaps_external_command(self) -> None:
        cancel_event = threading.Event()
        runner = CommandRunner(DummyLogger(), cancel_event=cancel_event)
        outcome: list[BaseException] = []

        def run_command() -> None:
            try:
                runner.run([sys.executable, "-c", "import time; time.sleep(30)"])
            except BaseException as exc:
                outcome.append(exc)

        thread = threading.Thread(target=run_command)
        thread.start()
        time.sleep(0.2)
        cancel_event.set()
        thread.join(5)

        self.assertFalse(thread.is_alive(), "cancelled child should be stopped and reaped")
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], AppError)
        self.assertEqual(outcome[0].code, "E123")  # type: ignore[union-attr]

    def test_cancellation_kills_descendant_that_closed_pipes(self) -> None:
        cancel_event = threading.Event()
        runner = CommandRunner(DummyLogger(), cancel_event=cancel_event)
        runner.TERMINATION_GRACE_SECONDS = 0.25
        outcome: list[BaseException] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "child.pid"
            child_code = (
                "import os, signal, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
                "os.close(1); os.close(2); time.sleep(30)"
            )
            parent_code = (
                "import subprocess, sys, time; "
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
                "time.sleep(30)"
            )

            def run_command() -> None:
                try:
                    runner.run([sys.executable, "-c", parent_code])
                except BaseException as exc:
                    outcome.append(exc)

            thread = threading.Thread(target=run_command)
            thread.start()
            deadline = time.monotonic() + 5
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pid_file.exists(), "descendant did not start")
            child_pid = int(pid_file.read_text(encoding="ascii"))
            cancel_event.set()
            thread.join(4)

        self.assertFalse(thread.is_alive(), "cancel must wait only for the managed process group")
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], AppError)
        self.assertEqual(outcome[0].code, "E123")  # type: ignore[union-attr]
        self.assertFalse(self._pid_is_running(child_pid), "descendant must not survive into workflow cleanup")

    def test_cancellation_kills_descendant_holding_pipes_after_leader_exits(self) -> None:
        cancel_event = threading.Event()
        runner = CommandRunner(DummyLogger(), cancel_event=cancel_event)
        runner.TERMINATION_GRACE_SECONDS = 0.25
        outcome: list[BaseException] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "child.pid"
            child_code = (
                "import os, signal, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
                "time.sleep(30)"
            )
            parent_code = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {child_code!r}])"

            def run_command() -> None:
                try:
                    runner.run([sys.executable, "-c", parent_code])
                except BaseException as exc:
                    outcome.append(exc)

            thread = threading.Thread(target=run_command)
            thread.start()
            deadline = time.monotonic() + 5
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pid_file.exists(), "descendant did not start")
            child_pid = int(pid_file.read_text(encoding="ascii"))
            cancel_event.set()
            thread.join(4)

        self.assertFalse(thread.is_alive(), "cancel must stop a descendant-held pipe")
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], AppError)
        self.assertEqual(outcome[0].code, "E123")  # type: ignore[union-attr]
        self.assertFalse(self._pid_is_running(child_pid), "descendant must not survive into workflow cleanup")

    @staticmethod
    def _pid_is_running(pid: int) -> bool:
        try:
            contents = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        except FileNotFoundError:
            return False
        end = contents.rfind(")")
        state = contents[end + 2 :].split()[0]
        return state not in {"Z", "X"}


if __name__ == "__main__":
    unittest.main()
