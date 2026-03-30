from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.services.partition_service import PartitionService


class DummyLogger:
    def info(self, msg: str) -> None:
        return None


class DummyRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        command: list[str],
        *,
        check: bool = True,
        mask_values: list[str] | None = None,
        cwd: str | None = None,
        input_text: str | None = None,
    ):
        self.calls.append(
            {
                "command": command,
                "check": check,
                "mask_values": mask_values,
                "cwd": cwd,
                "input_text": input_text,
            }
        )
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()


class PartitionServiceTests(unittest.TestCase):
    def test_luks_passphrase_is_written_without_trailing_newline(self) -> None:
        runner = DummyRunner()
        service = PartitionService(runner, DummyLogger())

        with tempfile.TemporaryDirectory() as tmpdir:
            root_mount, root_device = service.make_filesystems_and_mount(
                "/dev/fake-efi",
                "/dev/fake-root",
                Path(tmpdir),
                encryption_enabled=True,
                luks_passphrase="Abcd1234test!",
                mapper_name="oyoport-cryptroot",
            )

        self.assertTrue(str(root_mount).endswith("/root"))
        self.assertEqual(root_device, "/dev/mapper/oyoport-cryptroot")
        luks_format = runner.calls[1]
        luks_open = runner.calls[2]
        self.assertEqual(luks_format["command"][:2], ["cryptsetup", "luksFormat"])
        self.assertEqual(luks_open["command"][:2], ["cryptsetup", "open"])
        self.assertIn("--type", luks_format["command"])
        self.assertIn("luks2", luks_format["command"])
        self.assertIn("--pbkdf", luks_format["command"])
        self.assertIn("pbkdf2", luks_format["command"])
        self.assertEqual(luks_format["input_text"], "Abcd1234test!")
        self.assertEqual(luks_open["input_text"], "Abcd1234test!")


if __name__ == "__main__":
    unittest.main()
