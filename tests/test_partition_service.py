from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.partition_service import PartitionService


class DummyLogger:
    def info(self, msg: str) -> None:
        return None


class DummyRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.outputs: dict[tuple[str, ...], tuple[int, str, str]] = {}

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
        returncode, stdout, stderr = self.outputs.get(tuple(command), (0, "", ""))
        return type("Result", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()


class PartitionServiceTests(unittest.TestCase):
    def test_luks_passphrase_is_written_without_trailing_newline(self) -> None:
        runner = DummyRunner()
        service = PartitionService(runner, DummyLogger())

        with tempfile.TemporaryDirectory() as tmpdir:
            root_mount, root_device, boot_mount = service.make_filesystems_and_mount(
                "/dev/fake-efi",
                "/dev/fake-boot",
                "/dev/fake-root",
                Path(tmpdir),
                encryption_enabled=True,
                luks_passphrase="Abcd1234test!",
                mapper_name="oyoport-cryptroot",
            )

        self.assertTrue(str(root_mount).endswith("/root"))
        self.assertEqual(root_device, "/dev/mapper/oyoport-cryptroot")
        self.assertEqual(boot_mount, root_mount / "boot")
        self.assertEqual(runner.calls[1]["command"][:3], ["mkfs.ext4", "-F", "-L"])
        self.assertIn("OYOPORT_BOOT", runner.calls[1]["command"])
        luks_format = runner.calls[2]
        luks_open = runner.calls[3]
        self.assertEqual(luks_format["command"][:2], ["cryptsetup", "luksFormat"])
        self.assertEqual(luks_open["command"][:2], ["cryptsetup", "open"])
        self.assertIn("--type", luks_format["command"])
        self.assertIn("luks2", luks_format["command"])
        self.assertIn("--pbkdf", luks_format["command"])
        self.assertIn("pbkdf2", luks_format["command"])
        self.assertEqual(luks_format["input_text"], "Abcd1234test!")
        self.assertEqual(luks_open["input_text"], "Abcd1234test!")
        self.assertEqual(runner.calls[-2]["command"], ["mount", "/dev/mapper/oyoport-cryptroot", str(root_mount)])
        self.assertEqual(runner.calls[-1]["command"], ["mount", "/dev/fake-boot", str(root_mount / "boot")])

    def test_unmount_device_closes_mounts_swap_and_crypt_mappers(self) -> None:
        runner = DummyRunner()
        runner.outputs[("lsblk", "-nrpo", "PATH,TYPE,MOUNTPOINT", "/dev/fake")] = (
            0,
            "/dev/fake disk \n"
            "/dev/fake1 part /media/usb-efi\n"
            "/dev/fake2 part [SWAP]\n"
            "/dev/mapper/oyoport-cryptroot crypt /media/usb-root\n",
            "",
        )
        service = PartitionService(runner, DummyLogger())

        with patch.object(PartitionService, "_active_swaps", return_value={"/dev/fake2"}):
            service.unmount_device("/dev/fake")

        commands = [call["command"] for call in runner.calls]
        self.assertIn(["umount", "-lf", "/media/usb-root"], commands)
        self.assertIn(["umount", "-lf", "/media/usb-efi"], commands)
        self.assertIn(["swapoff", "/dev/fake2"], commands)
        self.assertIn(["cryptsetup", "close", "oyoport-cryptroot"], commands)
        self.assertEqual(commands[-2], ["partprobe", "/dev/fake"])
        self.assertEqual(commands[-1], ["udevadm", "settle"])

    def test_unmount_device_skips_swapoff_for_non_swap_paths(self) -> None:
        runner = DummyRunner()
        runner.outputs[("lsblk", "-nrpo", "PATH,TYPE,MOUNTPOINT", "/dev/fake")] = (
            0,
            "/dev/fake disk \n"
            "/dev/fake1 part /media/usb-efi\n"
            "/dev/fake2 part \n",
            "",
        )
        service = PartitionService(runner, DummyLogger())

        with patch.object(PartitionService, "_active_swaps", return_value=set()):
            service.unmount_device("/dev/fake")

        commands = [call["command"] for call in runner.calls]
        self.assertNotIn(["swapoff", "/dev/fake"], commands)
        self.assertNotIn(["swapoff", "/dev/fake1"], commands)
        self.assertNotIn(["swapoff", "/dev/fake2"], commands)


if __name__ == "__main__":
    unittest.main()
