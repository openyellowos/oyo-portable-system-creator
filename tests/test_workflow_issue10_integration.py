from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.errors import AppError
from src.core.state import ExecutionState
from src.core.workflow import Workflow
from src.infra.chroot import ChrootHelper
from src.services.boot_service import BootService


class DummyLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, msg: str) -> None:
        self.messages.append(msg)


class DummyRunnerResult:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


class DummyRunner:
    def __init__(self) -> None:
        self.umount_returncode = 0

    def run(self, command: list[str], check: bool = False) -> DummyRunnerResult:
        if command[:3] == ["blkid", "-s", "UUID"]:
            return DummyRunnerResult(stdout="DUMMY-UUID\n")
        if command and command[0] == "umount":
            return DummyRunnerResult(returncode=self.umount_returncode)
        return DummyRunnerResult(stdout="")


class DummyDeviceService:
    def __init__(self) -> None:
        self.runner = DummyRunner()

    def check_os(self) -> None:
        return None

    def check_root(self) -> None:
        return None

    def check_required_commands(self, *, encryption_enabled: bool = False) -> None:
        return None

    def validate_target_device(self, target_device: str) -> None:
        return None

    def estimate_required_bytes(self, copy_bytes: int) -> int:
        return copy_bytes

    def check_capacity(self, target_device: str, required: int) -> None:
        return None


class DummyPartitionService:
    def __init__(self, work_root: Path) -> None:
        self.work_root = work_root
        self.calls: list[tuple[str, str]] = []

    def prepare_device(self, target_device: str) -> tuple[str, str, str]:
        return ("/dev/fake-efi", "/dev/fake-boot", "/dev/fake-root")

    def make_filesystems_and_mount(self, efi: str, boot: str, root: str, workdir: Path, **_: object) -> Path:
        self.calls.append(("root", efi))
        root_mount = self.work_root / "root"
        root_mount.mkdir(parents=True, exist_ok=True)
        boot_mount = root_mount / "boot"
        boot_mount.mkdir(parents=True, exist_ok=True)
        return root_mount, "/dev/fake-root", boot_mount

    def mount_efi_partition(self, efi: str, root_mount: Path, **_: object) -> Path:
        self.calls.append(("efi", efi))
        efi_mount = self.work_root / "efi"
        efi_mount.mkdir(parents=True, exist_ok=True)
        return efi_mount

    def close_encrypted_root(self, mapper_name: str | None) -> None:
        return None


class DummyCopyService:
    def __init__(self) -> None:
        self.rsync_calls: list[tuple[str, Path, str]] = []

    def resolve_source(self, mode: str, source_device: str | None = None) -> str:
        return "/"

    def estimate_copy_bytes(self, source: str, mode: str) -> int:
        return 1024

    def rsync_copy(self, source: str, target_root: Path, mode: str) -> None:
        self.rsync_calls.append((source, target_root, mode))

    def write_fstab(self, target_root: Path, root_uuid: str, boot_uuid: str, efi_uuid: str, **_: object) -> None:
        return None


class DummyBootService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path]] = []

    def install_grub(self, root_mount: Path, target_device: str, boot_uuid: str, **_: object) -> None:
        self.calls.append(("install_grub", root_mount))
        return None

    def update_initramfs(self, root_mount: Path) -> None:
        self.calls.append(("update_initramfs", root_mount))
        return None

    def refresh_grub_config(self, root_mount: Path) -> None:
        self.calls.append(("refresh_grub_config", root_mount))
        return None


class DummyOptimizeService:
    def apply(self, target_root: Path) -> None:
        return None


class DummyFirstbootService:
    def install(self, target_root: Path) -> None:
        return None


class DummyInhibitor:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self):
        self.events.append("inhibit-start")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.events.append("inhibit-release")


class FailingCopyService(DummyCopyService):
    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self.failure = failure

    def rsync_copy(self, source: str, target_root: Path, mode: str) -> None:
        raise self.failure


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.work_root = Path(self.tempdir.name)
        self.copy_service = DummyCopyService()
        self.logger = DummyLogger()
        self.partition_service = DummyPartitionService(self.work_root)
        self.boot_service = DummyBootService()
        self.workflow = Workflow(
            DummyDeviceService(),
            self.partition_service,
            self.copy_service,
            self.boot_service,
            DummyOptimizeService(),
            DummyFirstbootService(),
            self.logger,
            suspend_inhibitor_factory=lambda: DummyInhibitor([]),
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _assert_failed_cleanup_preserves_workdir(self, mount_check) -> None:
        workdir = self.work_root / "workflow-workdir"
        workdir.mkdir()
        marker = workdir / "marker"
        marker.write_text("preserve me")
        state = ExecutionState(mode="create", target_device="/dev/sdz")
        with patch("src.core.workflow.tempfile.mkdtemp", return_value=str(workdir)):
            with patch("src.core.workflow.shutil.rmtree") as rmtree:
                def simulate_workdir_check(path: Path) -> None:
                    if mount_check(str(path)):
                        raise RuntimeError("workdir remains mounted")

                with patch.object(self.workflow, "_assert_no_mounts_beneath", side_effect=simulate_workdir_check):
                    with patch.object(self.partition_service, "close_encrypted_root") as close_mapper:
                        with self.assertRaises(RuntimeError):
                            self.workflow.run_create(state)

        rmtree.assert_not_called()
        close_mapper.assert_not_called()
        self.assertTrue(workdir.is_dir())
        self.assertEqual(marker.read_text(), "preserve me")
        self.assertTrue(state.mounted_paths)

    def test_nonzero_umount_preserves_workdir(self) -> None:
        self.workflow.device_service.runner.umount_returncode = 32
        self._assert_failed_cleanup_preserves_workdir(lambda _path: False)

    def test_successful_umount_still_mounted_preserves_workdir(self) -> None:
        self._assert_failed_cleanup_preserves_workdir(lambda _path: True)

    def test_successful_umount_with_unreadable_mount_state_preserves_workdir(self) -> None:
        def unreadable(_path: str) -> bool:
            raise OSError("mount table unavailable")

        self._assert_failed_cleanup_preserves_workdir(unreadable)

    def test_partial_mount_failure_preserves_workdir_and_skips_mapper_close(self) -> None:
        workdir = self.work_root / "workflow\u00a0workdir"
        root_mount = workdir / "root"
        root_mount.mkdir(parents=True)
        marker = root_mount / "marker"
        marker.write_text("preserve mounted contents")
        mountinfo = f"36 25 8:1 / {root_mount} rw - ext4 /dev/fake-root rw\n"
        state = ExecutionState(
            mode="create",
            target_device="/dev/sdz",
            options={"encryption_enabled": True},
        )

        def fail_after_root_mount(_efi, _boot, _root, _workdir, **_kwargs):
            raise RuntimeError("boot mount failed after root mount")

        with patch("src.core.workflow.tempfile.mkdtemp", return_value=str(workdir)):
            with patch.object(
                self.partition_service,
                "make_filesystems_and_mount",
                side_effect=fail_after_root_mount,
            ):
                with patch("pathlib.Path.read_text", return_value=mountinfo):
                    with patch("src.core.workflow.shutil.rmtree") as rmtree:
                        with patch.object(self.partition_service, "close_encrypted_root") as close_mapper:
                            with self.assertRaisesRegex(RuntimeError, "could not confirm workdir mounts are absent"):
                                self.workflow.run_create(state)

        rmtree.assert_not_called()
        close_mapper.assert_not_called()
        self.assertEqual(marker.read_text(), "preserve mounted contents")

    def test_mountinfo_parser_decodes_escaped_mountpoint(self) -> None:
        mountpoint = self.work_root / "mounted volume"
        mountinfo = f"36 25 8:1 / {mountpoint.parent}/mounted\\040volume rw shared:7 - ext4 /dev/fake rw\n"
        with patch("pathlib.Path.read_text", return_value=mountinfo):
            self.assertTrue(Workflow._mountpoint_present(str(mountpoint)))

    def test_mountinfo_parser_preserves_unicode_whitespace_in_mountpoint(self) -> None:
        mountpoint = self.work_root / "mounted\u00a0volume"
        mountinfo = f"36 25 8:1 / {mountpoint} rw - ext4 /dev/fake rw\n"
        with patch("pathlib.Path.read_text", return_value=mountinfo):
            self.assertTrue(Workflow._mountpoint_present(str(mountpoint)))

    def test_mountinfo_parser_rejects_empty_or_malformed_snapshot(self) -> None:
        malformed = (
            "broken entry\n",
            "36 25 8:1 / relative rw - ext4 /dev/fake rw\n",
            "not-an-id 25 8:1 / /tmp/root rw - ext4 /dev/fake rw\n",
        )
        for mountinfo in ("", *malformed):
            with self.subTest(mountinfo=mountinfo):
                with patch("pathlib.Path.read_text", return_value=mountinfo):
                    with self.assertRaises(RuntimeError):
                        Workflow._mountpoints_in_process_namespace()

    def test_mountinfo_parser_fails_closed_when_unreadable(self) -> None:
        with patch("pathlib.Path.read_text", side_effect=OSError("permission denied")):
            with self.assertRaisesRegex(OSError, "permission denied"):
                Workflow._mountpoints_in_process_namespace()

    def test_run_create_runs_rsync_twice(self) -> None:
        state = ExecutionState(mode="create", target_device="/dev/sdz")

        self.workflow.run_create(state)

        self.assertEqual(len(self.copy_service.rsync_calls), 2)
        self.assertEqual(self.copy_service.rsync_calls[0][0], "/")
        self.assertEqual(self.copy_service.rsync_calls[1][0], "/")
        self.assertEqual(self.copy_service.rsync_calls[0][2], "create")
        self.assertEqual(self.copy_service.rsync_calls[1][2], "create")
        self.assertEqual(self.partition_service.calls, [("root", "/dev/fake-efi"), ("efi", "/dev/fake-efi")])
        self.assertEqual(
            [name for name, _ in self.boot_service.calls],
            ["install_grub", "update_initramfs", "refresh_grub_config"],
        )
        self.assertTrue(any("システムをコピー (1/2)" in msg for msg in self.logger.messages))
        self.assertTrue(any("システムを再同期 (2/2)" in msg for msg in self.logger.messages))


    def test_real_chroot_cancel_after_failed_bind_unmount_latches_cleanup(self) -> None:
        # Real Workflow -> BootService -> ChrootHelper, with fake commands.
        class InterruptingChrootRunner(DummyRunner):
            def __init__(self) -> None:
                super().__init__()
                self.bind_releases = 0

            def run(self, command: list[str], check: bool = False) -> DummyRunnerResult:
                if command[0] == "umount":
                    self.bind_releases += 1
                    if self.bind_releases == 1:
                        return DummyRunnerResult(returncode=32)
                    if self.bind_releases == 2:
                        raise KeyboardInterrupt()
                return super().run(command, check)

        runner = InterruptingChrootRunner()
        self.workflow.boot_service = BootService(runner, ChrootHelper(runner))
        state = ExecutionState(
            mode="create", target_device="/dev/sdz",
            options={"encryption_enabled": True},
        )
        workdir = self.work_root / "cancel-after-failed-bind"
        workdir.mkdir()
        marker = workdir / "sentinel"
        marker.write_text("keep", encoding="utf-8")
        events: list[str] = []
        self.workflow.suspend_inhibitor_factory = lambda: DummyInhibitor(events)

        with (
            patch("src.core.workflow.tempfile.mkdtemp", return_value=str(workdir)),
            patch.object(self.workflow, "_assert_no_mounts_beneath") as mounts_absent,
            patch.object(self.partition_service, "close_encrypted_root") as mapper_close,
            patch("src.core.workflow.shutil.rmtree") as remove_workdir,
        ):
            with self.assertRaisesRegex(RuntimeError, "chroot bind unmount failed"):
                self.workflow.run_create(state)
        self.assertEqual(runner.bind_releases, 2)
        mounts_absent.assert_called()
        mapper_close.assert_not_called()
        remove_workdir.assert_not_called()
        self.assertTrue(state.metadata.get("_chroot_bind_cleanup_failed"))
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual(events, ["inhibit-start", "inhibit-release"])

    def test_chroot_bind_unmount_error_blocks_workdir_and_mapper_cleanup(self) -> None:
        state = ExecutionState(
            mode="create",
            target_device="/dev/sdz",
            options={"encryption_enabled": True},
        )
        workdir = self.work_root / "chroot-bind-failed"
        workdir.mkdir()
        events: list[str] = []
        self.workflow.suspend_inhibitor_factory = lambda: DummyInhibitor(events)
        error = AppError.translated("E699", "error.bind_cleanup_failed", fatal=False)
        with (
            patch("src.core.workflow.tempfile.mkdtemp", return_value=str(workdir)),
            patch.object(self.boot_service, "install_grub", side_effect=error),
            patch.object(self.partition_service, "close_encrypted_root") as close_mapper,
            patch("src.core.workflow.shutil.rmtree") as remove_workdir,
        ):
            with self.assertRaisesRegex(RuntimeError, "chroot bind unmount failed"):
                self.workflow.run_create(state)
        close_mapper.assert_not_called()
        remove_workdir.assert_not_called()
        self.assertTrue(state.metadata.get("_chroot_bind_cleanup_failed"))
        self.assertTrue(workdir.exists())
        self.assertEqual(events, ["inhibit-start", "inhibit-release"])

    def test_inhibitor_covers_creation_and_is_released_after_mount_cleanup(self) -> None:
        events: list[str] = []
        self.workflow.suspend_inhibitor_factory = lambda: DummyInhibitor(events)
        cleanup = self.workflow.cleanup

        def record_cleanup(state: ExecutionState) -> None:
            events.append("cleanup")
            cleanup(state)

        self.workflow.cleanup = record_cleanup  # type: ignore[method-assign]
        state = ExecutionState(mode="create", target_device="/dev/sdz")

        self.workflow.run_create(state)

        self.assertEqual(events, ["inhibit-start", "cleanup", "inhibit-release"])
        self.assertEqual(state.mounted_paths, [])

    def test_inhibitor_is_released_after_workflow_error_and_cleanup(self) -> None:
        events: list[str] = []
        self.workflow.suspend_inhibitor_factory = lambda: DummyInhibitor(events)
        self.workflow.copy_service = FailingCopyService(RuntimeError("copy failed"))
        cleanup = self.workflow.cleanup

        def record_cleanup(state: ExecutionState) -> None:
            events.append("cleanup")
            cleanup(state)

        self.workflow.cleanup = record_cleanup  # type: ignore[method-assign]
        state = ExecutionState(mode="create", target_device="/dev/sdz")

        with self.assertRaisesRegex(RuntimeError, "copy failed"):
            self.workflow.run_create(state)

        self.assertEqual(events, ["inhibit-start", "cleanup", "inhibit-release"])
        self.assertEqual(state.mounted_paths, [])

    def test_cleanup_failure_preserves_workdir_and_releases_inhibitor(self) -> None:
        events: list[str] = []
        self.workflow.suspend_inhibitor_factory = lambda: DummyInhibitor(events)
        workdir = self.work_root / "workflow-workdir"
        workdir.mkdir()
        marker = workdir / "marker"
        marker.write_text("preserve me")

        def fail_cleanup(state: ExecutionState) -> None:
            events.append("cleanup")
            raise RuntimeError("cleanup failed")

        self.workflow.cleanup = fail_cleanup  # type: ignore[method-assign]
        state = ExecutionState(mode="create", target_device="/dev/sdz")

        with patch("src.core.workflow.tempfile.mkdtemp", return_value=str(workdir)):
            with patch("src.core.workflow.shutil.rmtree") as rmtree:
                with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                    self.workflow.run_create(state)

        rmtree.assert_not_called()
        self.assertTrue(workdir.is_dir())
        self.assertEqual(marker.read_text(), "preserve me")
        self.assertEqual(events, ["inhibit-start", "cleanup", "inhibit-release"])

    def test_inhibitor_is_released_on_cancellation(self) -> None:
        events: list[str] = []
        self.workflow.suspend_inhibitor_factory = lambda: DummyInhibitor(events)
        self.workflow.copy_service = FailingCopyService(KeyboardInterrupt())
        state = ExecutionState(mode="create", target_device="/dev/sdz")

        with self.assertRaises(KeyboardInterrupt):
            self.workflow.run_create(state)

        self.assertEqual(events, ["inhibit-start", "inhibit-release"])
        self.assertEqual(state.mounted_paths, [])


if __name__ == "__main__":
    unittest.main()
