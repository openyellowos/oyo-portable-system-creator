from __future__ import annotations

import io
import os
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch

from src.infra.suspend_inhibitor import _process_start_time, _watch_parent


class SuspendInhibitorTests(unittest.TestCase):
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
