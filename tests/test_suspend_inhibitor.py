from __future__ import annotations

import io
import os
import signal
from contextlib import redirect_stdout
import unittest
from unittest.mock import MagicMock, patch

from src.infra.suspend_inhibitor import SuspendInhibitor, _process_start_time, _watch_parent


class SuspendInhibitorTests(unittest.TestCase):
    def test_acquires_sleep_and_idle_locks_and_releases_on_all_exits(self) -> None:
        for failure in (None, RuntimeError("workflow failed"), KeyboardInterrupt()):
            with self.subTest(exit=type(failure).__name__ if failure else "normal"):
                process = MagicMock()
                process.stdout.readline.return_value = "READY\n"
                process.poll.return_value = None
                with (
                    patch("src.infra.suspend_inhibitor.shutil.which", return_value="/usr/bin/systemd-inhibit"),
                    patch("src.infra.suspend_inhibitor.os.getpid", return_value=42),
                    patch("src.infra.suspend_inhibitor._process_start_time", return_value=123),
                    patch("src.infra.suspend_inhibitor.subprocess.Popen", return_value=process) as popen,
                    patch("src.infra.suspend_inhibitor.select.select", return_value=([process.stdout], [], [])),
                    patch("src.infra.suspend_inhibitor.os.killpg") as killpg,
                ):
                    if failure is None:
                        with SuspendInhibitor():
                            pass
                    else:
                        with self.assertRaises(type(failure)):
                            with SuspendInhibitor():
                                raise failure

                command = popen.call_args.args[0]
                self.assertIn("--what=sleep:idle", command)
                self.assertIn("--mode=block", command)
                self.assertTrue(popen.call_args.kwargs["start_new_session"])
                killpg.assert_called_once_with(process.pid, signal.SIGTERM)
                process.wait.assert_called_once_with(timeout=3)

    def test_startup_timeout_stops_helper_without_entering_workflow(self) -> None:
        process = MagicMock()
        process.poll.return_value = None
        entered = False
        with (
            patch("src.infra.suspend_inhibitor.shutil.which", return_value="/usr/bin/systemd-inhibit"),
            patch("src.infra.suspend_inhibitor.os.getpid", return_value=42),
            patch("src.infra.suspend_inhibitor._process_start_time", return_value=123),
            patch("src.infra.suspend_inhibitor.subprocess.Popen", return_value=process),
            patch("src.infra.suspend_inhibitor.select.select", return_value=([], [], [])),
            patch("src.infra.suspend_inhibitor.os.killpg") as killpg,
        ):
            with self.assertRaisesRegex(RuntimeError, "did not acquire the sleep lock"):
                with SuspendInhibitor(startup_timeout=0.01):
                    entered = True

        self.assertFalse(entered)
        killpg.assert_called_once_with(process.pid, signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=3)

    def test_failed_acquisition_does_not_enter_workflow(self) -> None:
        process = MagicMock()
        process.poll.return_value = 1
        process.stderr.read.return_value = "logind denied the inhibitor"
        entered = False
        with (
            patch("src.infra.suspend_inhibitor.shutil.which", return_value="/usr/bin/systemd-inhibit"),
            patch("src.infra.suspend_inhibitor.os.getpid", return_value=42),
            patch("src.infra.suspend_inhibitor._process_start_time", return_value=123),
            patch("src.infra.suspend_inhibitor.subprocess.Popen", return_value=process),
            patch("src.infra.suspend_inhibitor.select.select", return_value=([], [], [])),
            patch("src.infra.suspend_inhibitor.os.killpg") as killpg,
        ):
            with self.assertRaisesRegex(RuntimeError, "logind denied the inhibitor"):
                with SuspendInhibitor():
                    entered = True

        self.assertFalse(entered)
        killpg.assert_not_called()
        process.stdout.close.assert_called_once_with()
        process.stderr.close.assert_called_once_with()

    def test_process_start_time_identifies_current_process(self) -> None:
        self.assertIsNotNone(_process_start_time(os.getpid()))

    def test_parent_watcher_exits_after_creator_disappears(self) -> None:
        observed = iter([123, 123, None])
        with patch("src.infra.suspend_inhibitor._process_start_time", side_effect=lambda _pid: next(observed)):
            with patch("src.infra.suspend_inhibitor.time.sleep"):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(_watch_parent(42, 123), 0)
    def test_process_start_time_treats_zombie_as_terminated(self) -> None:
        fields_after_state = ["0"] * 18 + ["987654"]
        stat = f"42 (a name ) with parens) Z {' '.join(fields_after_state)}"
        with patch("builtins.open", return_value=io.StringIO(stat)):
            self.assertIsNone(_process_start_time(42))

    def test_parent_watcher_exits_when_parent_becomes_zombie(self) -> None:
        observed = iter([123, 123, None])
        with patch("src.infra.suspend_inhibitor._process_start_time", side_effect=lambda _pid: next(observed)):
            with patch("src.infra.suspend_inhibitor.time.sleep"):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(_watch_parent(42, 123), 0)

    def test_parent_watcher_refuses_stale_parent_identity(self) -> None:
        with patch("src.infra.suspend_inhibitor._process_start_time", return_value=456):
            self.assertEqual(_watch_parent(42, 123), 1)


if __name__ == "__main__":
    unittest.main()
