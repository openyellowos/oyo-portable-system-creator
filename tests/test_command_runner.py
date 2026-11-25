from __future__ import annotations

import unittest
import threading
import time
import sys
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

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

    def test_cancellation_during_communicate_stops_descendant_after_leader_exits(self) -> None:
        cancel_event = threading.Event()
        runner = CommandRunner(DummyLogger(), cancel_event=cancel_event)
        runner.TERMINATION_GRACE_SECONDS = 0.25
        outcome: list[BaseException] = []
        communication_completed = threading.Event()
        descendant_alive_at_completion: list[bool] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "child.pid"
            child_code = (
                "import os, signal, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
                "os.close(1); os.close(2); time.sleep(30)"
            )
            parent_code = (
                "import os, subprocess, sys, time\n"
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
                "deadline = time.monotonic() + 5\n"
                f"while not os.path.exists({str(pid_file)!r}) and time.monotonic() < deadline:\n"
                "    time.sleep(0.01)\n"
            )
            command = [sys.executable, "-c", parent_code]
            original_communicate = subprocess.Popen.communicate
            cancellation_injected = False

            def communicate_then_cancel(process, *args, **kwargs):
                nonlocal cancellation_injected
                result = original_communicate(process, *args, **kwargs)
                if process.args == command and not cancellation_injected:
                    cancellation_injected = True
                    child_pid = int(pid_file.read_text(encoding="ascii"))
                    descendant_alive_at_completion.append(self._pid_is_running(child_pid))
                    communication_completed.set()
                    cancel_event.set()
                return result

            def run_command() -> None:
                try:
                    runner.run(command)
                except BaseException as exc:
                    outcome.append(exc)

            with patch("subprocess.Popen.communicate", new=communicate_then_cancel):
                thread = threading.Thread(target=run_command)
                thread.start()
                thread.join(4)
            child_pid = int(pid_file.read_text(encoding="ascii"))

        self.assertFalse(thread.is_alive(), "cancellation must be rechecked after communicate returns")
        self.assertTrue(communication_completed.is_set(), "leader communication did not complete")
        self.assertEqual(descendant_alive_at_completion, [True], "race must be injected while descendant is live")
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], AppError)
        self.assertEqual(outcome[0].code, "E123")  # type: ignore[union-attr]
        self.assertFalse(self._pid_is_running(child_pid), "descendant must not survive cancellation")

    def test_normal_completion_reaps_descendant_that_closed_pipes(self) -> None:
        runner = CommandRunner(DummyLogger())
        runner.TERMINATION_GRACE_SECONDS = 0.25
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
                "time.sleep(0.15)"
            )
            result = runner.run([sys.executable, "-c", parent_code])
            child_pid = int(pid_file.read_text(encoding="ascii"))

        self.assertEqual(result.returncode, 0)
        self.assertFalse(self._pid_is_running(child_pid), "same-group descendant must not outlive command")

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
