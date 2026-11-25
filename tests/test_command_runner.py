from __future__ import annotations

import unittest
import threading
import time
import sys

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


if __name__ == "__main__":
    unittest.main()
