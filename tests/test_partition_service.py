from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.errors import AppError
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
    @staticmethod
    def inventory(*nodes: dict[str, object]) -> str:
        return json.dumps({"blockdevices": list(nodes)})

    @staticmethod
    def lsblk_command(device: str = "/dev/fake") -> tuple[str, ...]:
        return ("lsblk", "--json", "--paths", "--output", "PATH,TYPE,MOUNTPOINTS", device)

    def test_luks_passphrase_is_written_without_trailing_newline(self) -> None:
        runner = DummyRunner()
        runner.outputs[("cryptsetup", "status", "oyoport-cryptroot")] = (1, "", "inactive")
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
        self.assertEqual(runner.calls[3]["command"], ["cryptsetup", "status", "oyoport-cryptroot"])
        luks_open = runner.calls[4]
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
        runner.outputs[self.lsblk_command()] = (
            0,
            self.inventory(
                {"path": "/dev/fake", "type": "disk", "mountpoints": None,
                 "children": [
                     {"path": "/dev/fake1", "type": "part", "mountpoints": ["/media/usb-efi"]},
                     {"path": "/dev/fake2", "type": "part", "mountpoints": ["[SWAP]"]},
                     {"path": "/dev/mapper/oyoport-cryptroot", "type": "crypt", "mountpoints": ["/media/usb-root"]},
                 ]},
            ),
            "",
        )
        service = PartitionService(runner, DummyLogger())

        with patch.object(PartitionService, "_active_swaps", return_value={"/dev/fake2"}):
            service.unmount_device("/dev/fake")

        commands = [call["command"] for call in runner.calls]
        self.assertIn(["umount", "/media/usb-root"], commands)
        self.assertIn(["umount", "/media/usb-efi"], commands)
        self.assertNotIn("-l", [arg for command in commands for arg in command])
        self.assertIn(["swapoff", "/dev/fake2"], commands)
        self.assertIn(["cryptsetup", "close", "oyoport-cryptroot"], commands)
        self.assertEqual(commands[-2], ["partprobe", "/dev/fake"])
        self.assertEqual(commands[-1], ["udevadm", "settle"])

    def test_close_encrypted_root_propagates_nonzero_status(self) -> None:
        runner = DummyRunner()
        runner.outputs[("cryptsetup", "close", "test-mapper")] = (7, "", "busy")
        service = PartitionService(runner, DummyLogger())

        with self.assertRaises(AppError):
            service.close_encrypted_root("test-mapper")

        self.assertEqual(runner.calls[-1]["command"], ["cryptsetup", "close", "test-mapper"])

    def test_unmount_device_skips_swapoff_for_non_swap_paths(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.lsblk_command()] = (
            0,
            self.inventory(
                {"path": "/dev/fake", "type": "disk", "mountpoints": None,
                 "children": [
                     {"path": "/dev/fake1", "type": "part", "mountpoints": ["/media/usb-efi"]},
                     {"path": "/dev/fake2", "type": "part", "mountpoints": None},
                 ]},
            ),
            "",
        )
        service = PartitionService(runner, DummyLogger())

        with patch.object(PartitionService, "_active_swaps", return_value=set()):
            service.unmount_device("/dev/fake")

        commands = [call["command"] for call in runner.calls]
        self.assertNotIn(["swapoff", "/dev/fake"], commands)
        self.assertNotIn(["swapoff", "/dev/fake1"], commands)
        self.assertNotIn(["swapoff", "/dev/fake2"], commands)


    def test_mountinfo_source_parser_decodes_mountpoint_and_source(self) -> None:
        content = "36 25 8:1 / /mnt/a\\040b rw,relatime - ext4 /dev/sda1 rw\n"
        with patch("pathlib.Path.read_text", return_value=content):
            self.assertEqual(PartitionService.mount_source_for("/mnt/a b"), "/dev/sda1")
            self.assertIsNone(PartitionService.mount_source_for("/not-mounted"))

    def test_prepare_device_unmounts_every_mountpoint_including_paths_with_spaces(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.lsblk_command()] = (
            0,
            self.inventory({"path": "/dev/fake", "type": "disk", "mountpoints": None,
                           "children": [{"path": "/dev/fake1", "type": "part",
                                        "mountpoints": ["/media/user/My USB", "/mnt/backup"]}]}),
            "",
        )
        service = PartitionService(runner, DummyLogger())
        with patch.object(PartitionService, "_active_swaps", return_value=set()), \
             patch.object(PartitionService, "mount_source_for", return_value=None) as source_lookup:
            service.prepare_device("/dev/fake")

        commands = [call["command"] for call in runner.calls]
        self.assertIn(["umount", "/media/user/My USB"], commands)
        self.assertIn(["umount", "/mnt/backup"], commands)
        self.assertNotIn(["umount", "[SWAP]"], commands)
        self.assertEqual(source_lookup.call_count, 2)
        dd_index = commands.index(["dd", "if=/dev/zero", "of=/dev/fake", "bs=1M", "count=16", "conv=fsync"])
        for mountpoint in ("/media/user/My USB", "/mnt/backup"):
            self.assertLess(commands.index(["umount", mountpoint]), dd_index)

    def test_prepare_device_unmounts_nested_mounts_globally_child_before_parent(self) -> None:
        runner = DummyRunner()
        # Deliberately enumerate EFI, root, boot; that order must not determine
        # release order for the nested target layout.
        runner.outputs[self.lsblk_command()] = (
            0,
            self.inventory({
                "path": "/dev/fake", "type": "disk", "mountpoints": None,
                "children": [
                    {"path": "/dev/efi", "type": "part", "mountpoints": ["/old/root/boot/efi"]},
                    {"path": "/dev/root", "type": "part", "mountpoints": ["/old/root"]},
                    {"path": "/dev/boot", "type": "part", "mountpoints": ["/old/root/boot"]},
                ],
            }),
            "",
        )
        service = PartitionService(runner, DummyLogger())

        with patch.object(PartitionService, "_active_swaps", return_value=set()), \
             patch.object(PartitionService, "mount_source_for", return_value=None):
            service.prepare_device("/dev/fake")

        commands = [call["command"] for call in runner.calls]
        unmounts = [command[-1] for command in commands if command[0] == "umount"]
        self.assertEqual(
            unmounts,
            ["/old/root/boot/efi", "/old/root/boot", "/old/root"],
        )
        dd_index = commands.index(["dd", "if=/dev/zero", "of=/dev/fake", "bs=1M", "count=16", "conv=fsync"])
        self.assertTrue(all(commands.index(["umount", path]) < dd_index for path in unmounts))

    def test_prepare_device_fails_closed_when_nested_child_unmount_fails(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.lsblk_command()] = (
            0,
            self.inventory({
                "path": "/dev/fake", "type": "disk", "mountpoints": None,
                "children": [
                    {"path": "/dev/efi", "type": "part", "mountpoints": ["/old/root/boot/efi"]},
                    {"path": "/dev/root", "type": "part", "mountpoints": ["/old/root"]},
                    {"path": "/dev/boot", "type": "part", "mountpoints": ["/old/root/boot"]},
                ],
            }),
            "",
        )
        runner.outputs[("umount", "/old/root/boot/efi")] = (1, "", "busy")
        service = PartitionService(runner, DummyLogger())

        with patch.object(PartitionService, "_active_swaps", return_value=set()):
            with self.assertRaises(AppError):
                service.prepare_device("/dev/fake")

        commands = [call["command"] for call in runner.calls]
        self.assertIn(["umount", "/old/root/boot/efi"], commands)
        self.assertNotIn(["umount", "/old/root/boot"], commands)
        self.assertNotIn(["umount", "/old/root"], commands)
        self.assertFalse(any(command[0] in {"dd", "parted", "partprobe"} for command in commands))

    def test_prepare_device_releases_lsblk_swap_marker_even_without_proc_entry(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.lsblk_command()] = (
            0,
            self.inventory({"path": "/dev/fake", "type": "disk", "mountpoints": None,
                           "children": [{"path": "/dev/fake2", "type": "part", "mountpoints": ["[SWAP]"]}]}),
            "",
        )
        service = PartitionService(runner, DummyLogger())
        with patch.object(PartitionService, "_active_swaps", return_value=set()):
            service.prepare_device("/dev/fake")

        commands = [call["command"] for call in runner.calls]
        self.assertIn(["swapoff", "/dev/fake2"], commands)
        self.assertNotIn(["umount", "[SWAP]"], commands)
        dd_index = commands.index(["dd", "if=/dev/zero", "of=/dev/fake", "bs=1M", "count=16", "conv=fsync"])
        self.assertLess(commands.index(["swapoff", "/dev/fake2"]), dd_index)

    def test_prepare_device_aborts_if_mount_is_still_present_after_umount(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.lsblk_command()] = (
            0,
            self.inventory({"path": "/dev/fake", "type": "disk", "mountpoints": None,
                           "children": [{"path": "/dev/fake1", "type": "part", "mountpoints": ["/mnt/busy"]}]}),
            "",
        )
        service = PartitionService(runner, DummyLogger())
        with patch.object(PartitionService, "_active_swaps", return_value=set()), \
             patch.object(PartitionService, "mount_source_for", return_value="/dev/fake1"):
            with self.assertRaises(AppError):
                service.prepare_device("/dev/fake")

        commands = [call["command"] for call in runner.calls]
        self.assertIn(["umount", "/mnt/busy"], commands)
        self.assertFalse(any(command[0] in {"dd", "parted", "partprobe"} for command in commands))

    def test_prepare_device_stops_before_writes_if_unmount_fails(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.lsblk_command()] = (
            0, self.inventory({"path": "/dev/fake", "type": "disk", "mountpoints": None,
                               "children": [{"path": "/dev/fake1", "type": "part", "mountpoints": ["/media/usb"]}]}), "")
        runner.outputs[("umount", "/media/usb")] = (1, "", "busy")
        service = PartitionService(runner, DummyLogger())
        with self.assertRaises(AppError):
            service.prepare_device("/dev/fake")
        commands = [call["command"] for call in runner.calls]
        self.assertIn(["umount", "/media/usb"], commands)
        self.assertFalse(any(command[0] in {"dd", "parted", "partprobe"} for command in commands))

    def test_prepare_device_stops_before_writes_if_swapoff_fails(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.lsblk_command()] = (
            0, self.inventory({"path": "/dev/fake", "type": "disk", "mountpoints": None,
                               "children": [{"path": "/dev/fake1", "type": "part", "mountpoints": None},
                                            {"path": "/dev/fake2", "type": "part", "mountpoints": ["[SWAP]"]}]}), "")
        runner.outputs[("swapoff", "/dev/fake2")] = (1, "", "busy")
        service = PartitionService(runner, DummyLogger())
        with patch.object(PartitionService, "_active_swaps", return_value={"/dev/fake2"}):
            with self.assertRaises(AppError):
                service.prepare_device("/dev/fake")
        commands = [call["command"] for call in runner.calls]
        self.assertIn(["swapoff", "/dev/fake2"], commands)
        self.assertFalse(any(command[0] in {"dd", "parted", "partprobe"} for command in commands))

    def test_prepare_device_stops_before_writes_if_mapper_close_fails(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.lsblk_command()] = (
            0, self.inventory({"path": "/dev/fake", "type": "disk", "mountpoints": None,
                               "children": [{"path": "/dev/mapper/foreign", "type": "crypt", "mountpoints": None}]}), "")
        runner.outputs[("cryptsetup", "close", "foreign")] = (1, "", "busy")
        service = PartitionService(runner, DummyLogger())
        with self.assertRaises(AppError):
            service.prepare_device("/dev/fake")
        commands = [call["command"] for call in runner.calls]
        self.assertIn(["cryptsetup", "close", "foreign"], commands)
        self.assertFalse(any(command[0] in {"dd", "parted", "partprobe"} for command in commands))

    def test_prepare_device_with_no_resources_preserves_normal_flow(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.lsblk_command()] = (0, self.inventory({"path": "/dev/fake", "type": "disk", "mountpoints": None}), "")
        service = PartitionService(runner, DummyLogger())
        with patch.object(PartitionService, "_active_swaps", return_value=set()):
            service.prepare_device("/dev/fake")
        commands = [call["command"] for call in runner.calls]
        self.assertIn("dd", [command[0] for command in commands])
        self.assertIn("parted", [command[0] for command in commands])

    def test_mapper_preexisting_is_rejected_before_open_and_not_registered(self) -> None:
        runner = DummyRunner()
        runner.outputs[("cryptsetup", "status", "owned-by-other")] = (0, "device: /dev/other\n", "")
        service = PartitionService(runner, DummyLogger())
        registered: list[tuple[str, str]] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(Exception):
                service.make_filesystems_and_mount(
                    "/dev/efi", "/dev/boot", "/dev/root", Path(tmpdir),
                    encryption_enabled=True, mapper_name="owned-by-other",
                    on_mapper_open_attempt=lambda name, backing: registered.append((name, backing)),
                )
        commands = [call["command"] for call in runner.calls]
        self.assertNotIn(["cryptsetup", "open", "--key-file", "-", "/dev/root", "owned-by-other"], commands)
        self.assertEqual(registered, [])


if __name__ == "__main__":
    unittest.main()
