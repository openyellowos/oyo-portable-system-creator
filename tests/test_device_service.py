from __future__ import annotations

import unittest

from src.services.device_service import DeviceService


class DummyRunner:
    def run(self, command: list[str], check: bool = True):
        raise AssertionError("runner should not be called in this test")


class DummyLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, msg: str) -> None:
        self.messages.append(msg)


class DeviceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = DummyLogger()
        self.service = DeviceService(DummyRunner(), self.logger)

    def test_estimate_required_bytes_logs_gib(self) -> None:
        required = self.service.estimate_required_bytes(95556331463)

        self.assertEqual(required, 114184748478)
        self.assertEqual(
            self.logger.messages[-1],
            "容量見積: copy=89.0 GiB required=106.3 GiB",
        )

    def test_check_capacity_logs_gib(self) -> None:
        self.service.get_device_size_bytes = lambda target_device: 128035676160

        self.service.check_capacity("/dev/sdb", 114184748478)

        self.assertEqual(
            self.logger.messages[-1],
            "容量確認: target=/dev/sdb size=119.2 GiB required=106.3 GiB",
        )


if __name__ == "__main__":
    unittest.main()
