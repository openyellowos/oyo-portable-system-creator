from __future__ import annotations

import json
import stat
import tempfile
import unittest
from types import SimpleNamespace
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
        next_minor = 101

        def add_majmin(entries: list[dict[str, object]]) -> None:
            nonlocal next_minor
            for entry in entries:
                entry.setdefault("maj:min", f"0:{next_minor}")
                next_minor += 1
                children = entry.get("children")
                if isinstance(children, list):
                    add_majmin(children)

        rows = list(nodes)
        add_majmin(rows)
        return json.dumps({"blockdevices": rows})

    @staticmethod
    def lsblk_command(device: str = "/dev/fake") -> tuple[str, ...]:
        return ("lsblk", "--json", "--paths", "--output", "PATH,TYPE,MOUNTPOINTS", device)

    @staticmethod
    def target_inspection_command(device: str = "/dev/fake") -> tuple[str, ...]:
        return ("lsblk", "--json", "--paths", "--tree", "--output", "PATH,TYPE,MOUNTPOINTS,MAJ:MIN", device)

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


    def test_mount_source_for_preserves_nbsp_and_topmost_source(self) -> None:
        path = "/mnt/USB\u00a0volume"
        fixture = (
            f"36 25 8:1 / {path} rw - ext4 /dev/fake1 rw\n"
            f"37 25 8:2 / {path} rw - ext4 /dev/fake2 rw\n"
        )
        with patch("pathlib.Path.read_text", return_value=fixture):
            self.assertEqual(PartitionService.mount_source_for(path), "/dev/fake2")
            self.assertIsNone(PartitionService.mount_source_for("/mnt/other"))

    def test_mount_source_for_rejects_malformed_snapshot(self) -> None:
        for fixture in ("", "broken entry\n", "36 25 8:1 / relative rw - ext4 /dev/fake rw\n"):
            with self.subTest(fixture=fixture):
                with patch("pathlib.Path.read_text", return_value=fixture):
                    with self.assertRaises(RuntimeError):
                        PartitionService.mount_source_for("/mnt/USB\u00a0volume")

    def test_prepare_device_refuses_new_residuals_without_releasing_them(self) -> None:
        runner = DummyRunner()
        service = PartitionService(runner, DummyLogger())
        with patch.object(service, "assert_no_target_residuals", side_effect=RuntimeError("late mount")):
            with self.assertRaisesRegex(AppError, "late mount"):
                service.prepare_device("/dev/fake")
        commands = [call["command"] for call in runner.calls]
        self.assertFalse(any(command[0] in {"umount", "swapoff", "cryptsetup", "dd", "parted"} for command in commands))

    def test_prepare_device_checks_at_boundary_and_never_auto_unmounts(self) -> None:
        runner = DummyRunner()
        service = PartitionService(runner, DummyLogger())
        identity = {"major": 0, "minor": 101, "diskseq": 7}
        with patch.object(service, "assert_no_target_residuals", return_value=identity) as inspect, \
             patch.object(service, "_block_identity", return_value=identity), \
             patch.object(service, "unmount_device") as unmount:
            service.prepare_device("/dev/fake", expected_identity=identity)
        inspect.assert_called_once_with("/dev/fake", expected_identity=identity)
        unmount.assert_not_called()
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

    def test_target_residual_inspection_accepts_clean_device_by_block_identity(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.target_inspection_command()] = (
            0,
            self.inventory({"path": "/dev/fake", "type": "disk", "mountpoints": None,
                            "children": [{"path": "/dev/fake1", "type": "part", "mountpoints": None}]}),
            "",
        )
        service = PartitionService(runner, DummyLogger())
        identities = {"/dev/fake": 101, "/dev/fake1": 102}

        def fake_stat(path: str):
            return SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=identities[path])

        with patch("src.services.partition_service.os.stat", side_effect=fake_stat):
            with patch.object(PartitionService, "_active_swaps", return_value=set()):
                service.assert_no_target_residuals("/dev/fake")

    def test_target_inventory_rejects_last_observed_diskseq_change(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.target_inspection_command()] = (
            0,
            self.inventory({"path": "/dev/fake", "type": "disk", "mountpoints": None}),
            "",
        )
        service = PartitionService(runner, DummyLogger())
        with patch("src.services.partition_service.os.stat", return_value=SimpleNamespace(
            st_mode=stat.S_IFBLK, st_rdev=101
        )), patch("pathlib.Path.read_text", side_effect=["7\n", "7\n", "8\n"]):
            with self.assertRaisesRegex(RuntimeError, "identity changed during inspection"):
                service.assert_no_target_residuals(
                    "/dev/fake", expected_identity={"major": 0, "minor": 101, "diskseq": 7}
                )
        self.assertEqual(len(runner.calls), 1)

    def test_prepare_device_rechecks_full_identity_immediately_before_writes(self) -> None:
        runner = DummyRunner()
        service = PartitionService(runner, DummyLogger())
        verified = {"major": 0, "minor": 101, "diskseq": 7}
        replaced = {"major": 0, "minor": 101, "diskseq": 8}
        with patch.object(service, "assert_no_target_residuals", return_value=verified), \
             patch.object(service, "_block_identity", return_value=replaced):
            with self.assertRaisesRegex(AppError, "changed before preparation"):
                service.prepare_device("/dev/fake", expected_identity=verified)
        commands = [call["command"][0] for call in runner.calls]
        self.assertNotIn("dd", commands)
        self.assertNotIn("parted", commands)

    def test_target_residual_mount_stops_without_any_release_command(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.target_inspection_command()] = (
            0,
            self.inventory({"path": "/dev/fake", "type": "disk", "mountpoints": None,
                            "children": [{"path": "/dev/fake1", "type": "part", "mountpoints": ["/media/usb"]}]}),
            "",
        )
        service = PartitionService(runner, DummyLogger())
        identities = {"/dev/fake": 101, "/dev/fake1": 102}

        with patch("src.services.partition_service.os.stat", side_effect=lambda path: SimpleNamespace(
            st_mode=stat.S_IFBLK, st_rdev=identities[path]
        )):
            with patch.object(PartitionService, "_active_swaps", return_value=set()):
                with self.assertRaisesRegex(RuntimeError, "mount /media/usb"):
                    service.assert_no_target_residuals("/dev/fake")
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0]["command"][0], "lsblk")

    def test_target_residual_swap_mapper_and_device_alias_are_detected(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.target_inspection_command()] = (
            0,
            self.inventory({"path": "/dev/fake", "type": "disk", "mountpoints": None,
                            "children": [
                                {"path": "/dev/fake1", "type": "part", "mountpoints": None},
                                {"path": "/dev/mapper/oyoport-cryptroot", "type": "crypt", "mountpoints": None},
                            ]}),
            "",
        )
        service = PartitionService(runner, DummyLogger())
        identities = {"/dev/fake": 101, "/dev/fake1": 102, "/dev/mapper/oyoport-cryptroot": 103}
        with patch("src.services.partition_service.os.stat", side_effect=lambda path: SimpleNamespace(
            st_mode=stat.S_IFBLK, st_rdev=identities[path]
        )):
            with patch.object(PartitionService, "_active_swaps", return_value={"/dev/fake1"}):
                with self.assertRaisesRegex(RuntimeError, "crypt mapping.*active swap"):
                    service.assert_no_target_residuals("/dev/fake")

        identities["/dev/alias"] = 101
        identities["/dev/fake"] = 999
        runner.outputs[self.target_inspection_command("/dev/alias")] = runner.outputs[self.target_inspection_command()]
        with patch("src.services.partition_service.os.stat", side_effect=lambda path: SimpleNamespace(
            st_mode=stat.S_IFBLK, st_rdev=identities[path]
        )):
            with patch.object(PartitionService, "_active_swaps", return_value=set()):
                with self.assertRaisesRegex(RuntimeError, "identity changed"):
                    service.assert_no_target_residuals("/dev/alias")

    def test_target_resource_snapshot_failures_stop(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.target_inspection_command()] = (0, "not-json", "")
        service = PartitionService(runner, DummyLogger())
        with patch("src.services.partition_service.os.stat", return_value=SimpleNamespace(
            st_mode=stat.S_IFBLK, st_rdev=101
        )):
            with self.assertRaisesRegex(RuntimeError, "could not parse target device inventory"):
                service.assert_no_target_residuals("/dev/fake")

    def test_target_residual_check_rejects_identity_change_since_precheck(self) -> None:
        runner = DummyRunner()
        service = PartitionService(runner, DummyLogger())
        with patch("src.services.partition_service.os.stat", return_value=SimpleNamespace(
            st_mode=stat.S_IFBLK, st_rdev=101
        )), patch("pathlib.Path.read_text", return_value="8\n"):
            with self.assertRaisesRegex(RuntimeError, "changed since validation"):
                service.assert_no_target_residuals(
                    "/dev/fake", expected_identity={"major": 0, "minor": 101, "diskseq": 7}
                )
        self.assertEqual(runner.calls, [])

    def test_target_residual_check_rejects_inventory_majmin_mismatch(self) -> None:
        runner = DummyRunner()
        runner.outputs[self.target_inspection_command()] = (
            0,
            json.dumps({"blockdevices": [{
                "path": "/dev/fake", "type": "disk", "mountpoints": None, "maj:min": "0:999"
            }]}),
            "",
        )
        service = PartitionService(runner, DummyLogger())
        with patch("src.services.partition_service.os.stat", return_value=SimpleNamespace(
            st_mode=stat.S_IFBLK, st_rdev=101
        )), patch.object(PartitionService, "_active_swaps", return_value=set()):
            with self.assertRaisesRegex(RuntimeError, "inventory identity"):
                service.assert_no_target_residuals("/dev/fake")

    def test_requested_mapper_conflict_stops_before_any_device_write(self) -> None:
        runner = DummyRunner()
        service = PartitionService(runner, DummyLogger())
        with patch.object(service, "mapper_backing_device", return_value="/dev/other-usb4"):
            with self.assertRaisesRegex(RuntimeError, "already active"):
                service.assert_mapper_available("oyoport-cryptroot")
        self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
