from __future__ import annotations

import unittest
import threading
import time
import sys
import os
import selectors
import signal
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

    def error(self, msg: str) -> None:
        self.messages.append(msg)


class CommandRunnerTests(unittest.TestCase):
    def test_sanitize_output_for_log_removes_control_characters(self) -> None:
        text = "Discarding device blocks:\r0/31126784\x08\x08\x08done\n\x00next line"

        sanitized = CommandRunner._sanitize_output_for_log(text)

        self.assertEqual(sanitized, "Discarding device blocks:\n0/31126784done\nnext line")

    def test_streaming_output_delivers_split_pipe_chunks_and_keeps_capture(self) -> None:
        runner = CommandRunner(DummyLogger())
        received: list[tuple[str, str]] = []
        script = (
            "import os, time; os.write(2, b'123,456B 3'); time.sleep(.05); "
            "os.write(2, b'8% 1.2MB/s 0:04\\r'); "
            "os.write(1, b'copy complete\\n')"
        )

        result = runner.run(
            [sys.executable, "-c", script],
            output_callback=lambda stream, text: received.append((stream, text)),
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("copy complete", result.stdout)
        self.assertIn("38%", result.stderr)
        self.assertTrue(any(name == "stderr" and "3" in text for name, text in received))
        self.assertTrue(any(name == "stdout" and "copy complete" in text for name, text in received))

    def test_streaming_setup_and_read_failures_stop_the_started_process_group(self) -> None:
        for failure_point in ("setup", "select", "persistent_read"):
            with self.subTest(failure_point=failure_point), tempfile.TemporaryDirectory() as tmpdir:
                runner = CommandRunner(DummyLogger())
                runner.TERMINATION_GRACE_SECONDS = 0.25
                pid_file = Path(tmpdir) / "command.pid"
                ready_file = Path(tmpdir) / "command.ready"
                if failure_point == "persistent_read":
                    script = (
                        "import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                        f"open({str(ready_file)!r}, 'w').write('ready'); "
                        "os.write(1, b'ready\\n'); time.sleep(30)"
                    )
                else:
                    script = (
                        "import os, time; "
                        f"open({str(ready_file)!r}, 'w').write('ready'); "
                        "time.sleep(30)"
                    )
                stdout_fd: list[int] = []
                original_popen = subprocess.Popen

                def capture_process(*args, **kwargs):
                    process = original_popen(*args, **kwargs)
                    pid_file.write_text(str(process.pid), encoding="ascii")
                    if process.stdout is not None:
                        stdout_fd.append(process.stdout.fileno())
                    return process

                def wait_until_ready() -> None:
                    deadline = time.monotonic() + 5
                    while not ready_file.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    if not ready_file.exists():
                        raise AssertionError("test command did not start before failure injection")

                if failure_point == "setup":
                    def selector_factory():
                        wait_until_ready()
                        raise OSError("EMFILE injected")
                elif failure_point == "select":
                    real_selector_factory = selectors.DefaultSelector

                    class FailingSelector:
                        def __init__(self) -> None:
                            self._selector = real_selector_factory()

                        def __getattr__(self, name):
                            return getattr(self._selector, name)

                        def select(self, timeout=None):
                            wait_until_ready()
                            raise OSError("EIO injected")

                    selector_factory = FailingSelector
                else:
                    selector_factory = selectors.DefaultSelector

                real_os_read = os.read

                def failing_read(fd: int, size: int) -> bytes:
                    if stdout_fd and fd == stdout_fd[0]:
                        wait_until_ready()
                        raise OSError(5, "EIO persistent injected")
                    return real_os_read(fd, size)

                try:
                    with patch("src.infra.command_runner.subprocess.Popen", new=capture_process):
                        with patch("src.infra.command_runner.selectors.DefaultSelector", new=selector_factory):
                            if failure_point == "persistent_read":
                                with patch("src.infra.command_runner.os.read", side_effect=failing_read):
                                    with self.assertRaisesRegex(OSError, "injected"):
                                        runner.run(
                                            [sys.executable, "-c", script],
                                            output_callback=lambda _stream, _text: None,
                                        )
                            else:
                                with self.assertRaisesRegex(OSError, "injected"):
                                    runner.run(
                                        [sys.executable, "-c", script],
                                        output_callback=lambda _stream, _text: None,
                                    )
                    self.assertTrue(pid_file.exists(), "command must have started before the injected failure")
                    self.assertTrue(ready_file.exists(), "failure must happen after the command has started")
                    process_group_id = int(pid_file.read_text(encoding="ascii"))
                    self.assertFalse(
                        runner._process_group_alive(process_group_id),
                        "streaming failure must terminate the complete process group before returning",
                    )
                finally:
                    if pid_file.exists():
                        process_group_id = int(pid_file.read_text(encoding="ascii"))
                        if runner._process_group_alive(process_group_id):
                            try:
                                os.killpg(process_group_id, signal.SIGKILL)
                            except ProcessLookupError:
                                pass

    def test_streaming_mode_stops_descendant_holding_pipes_after_leader_exit(self) -> None:
        runner = CommandRunner(DummyLogger())
        runner.TERMINATION_GRACE_SECONDS = 0.25
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "child.pid"
            child_code = (
                "import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(30)"
            )
            parent_code = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {child_code!r}])"
            outcome: list[BaseException] = []

            def run_command() -> None:
                try:
                    runner.run(
                        [sys.executable, "-c", parent_code],
                        output_callback=lambda _stream, _text: None,
                    )
                except BaseException as exc:
                    outcome.append(exc)

            thread = threading.Thread(target=run_command)
            thread.start()
            deadline = time.monotonic() + 5
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pid_file.exists(), "descendant did not start")
            child_pid = int(pid_file.read_text(encoding="ascii"))
            pgid = os.getpgid(child_pid)
            thread.join(4)
            completed_before_fallback = not thread.is_alive()
            if thread.is_alive():
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                thread.join(3)

        self.assertTrue(completed_before_fallback, "runner must stop inherited-pipe descendants")
        self.assertFalse(thread.is_alive(), "test cleanup must not leave a runner behind")
        self.assertEqual(outcome, [])
        self.assertFalse(self._pid_is_running(child_pid))

    def test_streaming_cancellation_stops_the_command_group_before_return(self) -> None:
        cancel_event = threading.Event()
        runner = CommandRunner(DummyLogger(), cancel_event=cancel_event)
        runner.TERMINATION_GRACE_SECONDS = 0.25
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "child.pid"
            child_code = (
                "import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(30)"
            )
            parent_code = (
                "import subprocess, sys, time; "
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); time.sleep(30)"
            )
            outcome: list[BaseException] = []

            def run_command() -> None:
                try:
                    runner.run(
                        [sys.executable, "-c", parent_code],
                        output_callback=lambda _stream, _text: None,
                    )
                except BaseException as exc:
                    outcome.append(exc)

            thread = threading.Thread(target=run_command)
            thread.start()
            deadline = time.monotonic() + 5
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pid_file.exists(), "descendant did not start")
            child_pid = int(pid_file.read_text(encoding="ascii"))
            pgid = os.getpgid(child_pid)
            cancel_event.set()
            thread.join(4)
            completed_before_fallback = not thread.is_alive()
            if thread.is_alive():
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                thread.join(3)

        self.assertTrue(completed_before_fallback, "cancel must wait for group termination")
        self.assertFalse(thread.is_alive(), "test cleanup must not leave a runner behind")
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], AppError)
        self.assertEqual(outcome[0].code, "E123")  # type: ignore[union-attr]
        self.assertFalse(self._pid_is_running(child_pid))

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

    def test_normal_completion_reaps_descendant_holding_pipes_without_cancellation(self) -> None:
        runner = CommandRunner(DummyLogger())
        runner.TERMINATION_GRACE_SECONDS = 0.25
        outcome: list[object] = []
        launched: list[subprocess.Popen[str]] = []
        child_alive_when_runner_returned: bool | None = None
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "child.pid"
            child_code = (
                "import os, signal, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
                "time.sleep(30)"
            )
            parent_code = (
                "import os, subprocess, sys, time\n"
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
                "deadline = time.monotonic() + 5\n"
                f"while not os.path.exists({str(pid_file)!r}) and time.monotonic() < deadline:\n"
                "    time.sleep(0.01)\n"
            )

            command = [sys.executable, "-c", parent_code]
            original_popen = subprocess.Popen

            def capture_process(*args, **kwargs):
                process = original_popen(*args, **kwargs)
                launched.append(process)
                return process

            def run_command() -> None:
                try:
                    outcome.append(runner.run(command))
                except BaseException as exc:
                    outcome.append(exc)

            thread = threading.Thread(target=run_command)
            try:
                with patch("src.infra.command_runner.subprocess.Popen", new=capture_process):
                    thread.start()
                    thread.join(3)
                completed_within_bound = not thread.is_alive()
                if completed_within_bound and pid_file.exists():
                    child_alive_when_runner_returned = self._pid_is_running(
                        int(pid_file.read_text(encoding="ascii"))
                    )
            finally:
                # On a regression, don't leave the deliberate 30-second child
                # behind. The command runner starts each process in its own
                # session, so this PID identifies only this test's process group.
                if thread.is_alive() and launched:
                    try:
                        os.killpg(launched[0].pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    thread.join(3)
                elif launched and runner._process_group_alive(launched[0].pid):
                    try:
                        os.killpg(launched[0].pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if thread.is_alive():
                    thread.join(3)

            child_pid = int(pid_file.read_text(encoding="ascii")) if pid_file.exists() else None

        self.assertTrue(
            completed_within_bound,
            "normal completion must not wait for a descendant that holds inherited pipes",
        )
        self.assertEqual(len(outcome), 1)
        self.assertFalse(isinstance(outcome[0], BaseException), f"command failed: {outcome[0]!r}")
        self.assertEqual(outcome[0].returncode, 0)  # type: ignore[union-attr]
        self.assertIsNotNone(child_pid, "descendant did not start")
        self.assertIsNotNone(child_alive_when_runner_returned)
        self.assertFalse(
            child_alive_when_runner_returned,
            "descendant holding inherited pipes must be stopped before normal completion",
        )

    def test_normal_mode_reports_and_blocks_on_pipe_held_by_detached_descendant(self) -> None:
        self._assert_detached_pipe_holder_blocks_cleanup(streaming=False)

    def test_streaming_mode_reports_and_blocks_on_pipe_held_by_detached_descendant(self) -> None:
        self._assert_detached_pipe_holder_blocks_cleanup(streaming=True)

    def _assert_detached_pipe_holder_blocks_cleanup(self, *, streaming: bool) -> None:
        cancel_event = threading.Event()
        stalled = threading.Event()
        stall_messages: list[str] = []
        runner = CommandRunner(
            DummyLogger(),
            cancel_event=cancel_event,
            cancel_stalled_callback=lambda reason: (stall_messages.append(reason), stalled.set()),
        )
        runner.TERMINATION_GRACE_SECONDS = 0.15
        runner.PIPE_EOF_STALL_SECONDS = 0.2
        outcome: list[object] = []
        managed_processes: list[subprocess.Popen[bytes] | subprocess.Popen[str]] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            pid_file = Path(tmpdir) / "detached-child.pid"
            release_file = Path(tmpdir) / "release-child"
            child_code = (
                "import os, time\n"
                f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
                f"release = {str(release_file)!r}\n"
                "deadline = time.monotonic() + 20\n"
                "while not os.path.exists(release) and time.monotonic() < deadline:\n"
                "    time.sleep(.01)\n"
            )
            parent_code = (
                "import os, subprocess, sys, time\n"
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}], start_new_session=True)\n"
                "deadline = time.monotonic() + 5\n"
                f"while not os.path.exists({str(pid_file)!r}) and time.monotonic() < deadline:\n"
                "    time.sleep(.01)\n"
            )
            command = [sys.executable, "-c", parent_code]
            original_popen = subprocess.Popen

            def capture_process(*args, **kwargs):
                process = original_popen(*args, **kwargs)
                managed_processes.append(process)
                return process

            def run_command() -> None:
                try:
                    kwargs = {"output_callback": lambda _stream, _text: None} if streaming else {}
                    outcome.append(runner.run(command, **kwargs))
                except BaseException as exc:
                    outcome.append(exc)

            thread = threading.Thread(target=run_command)
            notified_while_blocked = False
            detached_child_alive_when_notified = False
            try:
                with patch("src.infra.command_runner.subprocess.Popen", new=capture_process):
                    thread.start()
                    deadline = time.monotonic() + 5
                    while not pid_file.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(pid_file.exists(), "detached descendant did not start")
                    child_pid = int(pid_file.read_text(encoding="ascii"))
                    cancel_event.set()
                    self.assertTrue(stalled.wait(3), "pipe EOF stall must notify the caller")
                    notified_while_blocked = thread.is_alive()
                    detached_child_alive_when_notified = self._pid_is_running(child_pid)
            finally:
                # The detached process is deliberately outside the runner's
                # process group; release it explicitly so the fail-closed test
                # can finish without leaving a process behind.
                release_file.touch(exist_ok=True)
                if pid_file.exists():
                    child_pid = int(pid_file.read_text(encoding="ascii"))
                    deadline = time.monotonic() + 1
                    while self._pid_is_running(child_pid) and time.monotonic() < deadline:
                        time.sleep(0.01)
                    if self._pid_is_running(child_pid):
                        try:
                            os.kill(child_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                if thread.is_alive() and managed_processes:
                    try:
                        os.killpg(managed_processes[0].pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                thread.join(3)

        self.assertTrue(notified_while_blocked, "runner must not return while an unmanaged writer holds pipes")
        self.assertTrue(detached_child_alive_when_notified, "the pipe-holding detached process must remain live")
        self.assertTrue(stall_messages)
        self.assertIn("not starting cleanup", stall_messages[0])
        self.assertFalse(thread.is_alive(), "test cleanup must release the detached pipe holder")
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], AppError)
        self.assertEqual(outcome[0].code, "E123")  # type: ignore[union-attr]

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
