from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
