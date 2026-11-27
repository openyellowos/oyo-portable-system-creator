from __future__ import annotations

import tempfile
import unittest
from contextlib import nullcontext
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from src.core.errors import AppError
from src.core.state import ExecutionState
from src.core.workflow import Workflow
from src.infra.operation_lock import OperationLock
from src.services.partition_service import PartitionService


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
        self.calls: list[dict[str, object]] = []

    def run(self, command: list[str], check: bool = False, **_: object) -> DummyRunnerResult:
        self.calls.append({"command": command})
        if command[:3] == ["blkid", "-s", "UUID"]:
            return DummyRunnerResult(stdout="DUMMY-UUID\n")
        if command[:2] == ["cryptsetup", "status"]:
            return DummyRunnerResult(stdout="", returncode=1)
        return DummyRunnerResult(stdout="")


class UnmountFailureRunner(DummyRunner):
    def __init__(self, failure: BaseException | int) -> None:
        super().__init__()
        self.failure = failure
        self.unmounts: list[str] = []

    def run(self, command: list[str], check: bool = False) -> DummyRunnerResult:
        if command[0] == "umount":
            self.unmounts.append(command[-1])
            if isinstance(self.failure, BaseException):
                raise self.failure
            return DummyRunnerResult(returncode=self.failure)
        return super().run(command, check)


class PartialBootMountFailureRunner(DummyRunner):
    def __init__(self) -> None:
        super().__init__()
        self.commands: list[list[str]] = []

    def run(self, command: list[str], check: bool = False, **kwargs: object) -> DummyRunnerResult:
        self.commands.append(command)
        if command[0] == "mount" and command[-1].endswith("/root/boot"):
            raise RuntimeError("boot mount failed")
        if command[0] == "umount" and command[-1].endswith("/root/boot"):
            return DummyRunnerResult(returncode=32)
        return super().run(command, check=check, **kwargs)


class MapperCloseFailureRunner(DummyRunner):
    def __init__(self) -> None:
        super().__init__()
        self.commands: list[list[str]] = []
        self.status_calls = 0

    def run(self, command: list[str], check: bool = False, **kwargs: object) -> DummyRunnerResult:
        self.commands.append(command)
        if command[:2] == ["cryptsetup", "status"]:
            self.status_calls += 1
            if self.status_calls == 1:
                return DummyRunnerResult(returncode=1)
            return DummyRunnerResult(stdout="  device: /dev/root\n")
        if command[:2] == ["cryptsetup", "close"]:
            return DummyRunnerResult(returncode=9)
        return super().run(command, check=check, **kwargs)


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

    def target_device_identity(self, target_device: str) -> dict[str, int | None]:
        return {"major": 0, "minor": 101, "diskseq": 7}

    def estimate_required_bytes(self, copy_bytes: int) -> int:
        return copy_bytes

    def check_capacity(self, target_device: str, required: int) -> None:
        return None


class DummyPartitionService:
    def __init__(self, work_root: Path) -> None:
        self.work_root = work_root
        self.calls: list[tuple[str, str]] = []
        self.expected_identity: dict[str, int | None] | None = None

    def prepare_device(
        self,
        target_device: str,
        *,
        expected_identity: dict[str, int | None] | None = None,
    ) -> tuple[str, str, str]:
        self.expected_identity = expected_identity
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
        self.progress_callbacks: list[object] = []

    def resolve_source(self, mode: str, source_device: str | None = None) -> str:
        return "/"

    def estimate_copy_bytes(self, source: str, mode: str) -> int:
        return 1024

    def rsync_copy(self, source: str, target_root: Path, mode: str, progress_callback=None) -> None:
        self.rsync_calls.append((source, target_root, mode))
        self.progress_callbacks.append(progress_callback)

    def write_fstab(self, target_root: Path, root_uuid: str, boot_uuid: str, efi_uuid: str, **_: object) -> None:
        return None


class DummyBootService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path]] = []

    def install_grub(self, root_mount: Path, target_device: str, boot_uuid: str, **_: object) -> None:
        self.calls.append(("install_grub", root_mount))
        return None

    def update_initramfs(self, root_mount: Path, **_: object) -> None:
        self.calls.append(("update_initramfs", root_mount))
        return None

    def refresh_grub_config(self, root_mount: Path, **_: object) -> None:
        self.calls.append(("refresh_grub_config", root_mount))
        return None


class DummyOptimizeService:
    def apply(self, target_root: Path) -> None:
        return None


class DummyFirstbootService:
    def install(self, target_root: Path) -> None:
        return None


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
            suspend_inhibitor_factory=lambda: nullcontext(),
            operation_lock_factory=lambda: nullcontext(),
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_copy_progress_maps_each_rsync_pass_and_never_moves_backwards(self) -> None:
        state = ExecutionState(mode="create", target_device="/dev/sdz")
        state.metadata["copy_bytes"] = 100
        updates: list[tuple[int, str]] = []
        state.add_progress_listener(lambda percent, step: updates.append((percent, step)))

        first = self.workflow._copy_progress_callback(state, 1)
        second = self.workflow._copy_progress_callback(state, 2)
        assert first is not None
        self.assertIsNone(second)
        for transferred_bytes in (0, 50, 94, 95, 100, 25):
            first(transferred_bytes)

        self.assertEqual([percent for percent, _ in updates], [45, 49, 53, 53])
        self.assertIn("(1/2)", updates[1][1])
        self.assertIn("50%", updates[1][1])
        self.assertIn("データ転送は終盤です。処理を継続中…", updates[-1][1])
        self.assertNotIn("100%", " ".join(step for _, step in updates))
        self.assertTrue(all(a <= b for a, b in zip((v[0] for v in updates), (v[0] for v in updates[1:]))))

    def test_copy_progress_falls_back_when_estimate_is_missing_or_zero(self) -> None:
        state = ExecutionState(mode="create", target_device="/dev/sdz")
        state.add_progress_listener(lambda _percent, _step: None)

        self.assertIsNone(self.workflow._copy_progress_callback(state, 1))
        state.metadata["copy_bytes"] = 0
        self.assertIsNone(self.workflow._copy_progress_callback(state, 1))

    def test_copy_progress_shows_processing_when_percent_stays_unchanged(self) -> None:
        state = ExecutionState(mode="create", target_device="/dev/sdz")
        state.metadata["copy_bytes"] = 1000
        updates: list[tuple[int, str]] = []
        state.add_progress_listener(lambda percent, step: updates.append((percent, step)))
        callback = self.workflow._copy_progress_callback(state, 1)
        assert callback is not None

        callback(500)
        for _ in range(4):
            callback(500)
        self.assertIn("コピー処理中", updates[-1][1])
        callback(510)
        self.assertIn("51%", updates[-1][1])
        self.assertTrue(all(a <= b for a, b in zip((p for p, _ in updates), (p for p, _ in updates[1:]))))

    def test_copy_pass_two_uses_verification_status_without_byte_percentage(self) -> None:
        state = ExecutionState(mode="create", target_device="/dev/sdz")
        state.metadata["copy_bytes"] = 1024
        updates: list[tuple[int, str]] = []
        state.add_progress_listener(lambda percent, step: updates.append((percent, step)))

        self.workflow._rsync_copy_with_progress(state, "/", Path("/target"), "create", 1)
        self.workflow._rsync_copy_with_progress(state, "/", Path("/target"), "create", 2)

        self.assertIsNotNone(self.copy_service.progress_callbacks[0])
        self.assertIsNone(self.copy_service.progress_callbacks[1])
        self.assertEqual([percent for percent, _ in updates], [54, 55, 59])
        self.assertIn("ファイル確認中", updates[1][1])
        self.assertEqual(updates[2][1], self.workflow.translate("workflow.copy.second"))

    def test_precheck_runs_before_target_preparation(self) -> None:
        events: list[str] = []
        self.workflow.precheck = lambda state: events.append("precheck")  # type: ignore[method-assign]
        prepare_device = self.partition_service.prepare_device

        def record_prepare(target_device: str, **kwargs: object) -> tuple[str, str, str]:
            events.append("prepare")
            return prepare_device(target_device, **kwargs)

        self.partition_service.prepare_device = record_prepare  # type: ignore[method-assign]
        self.workflow.run_create(ExecutionState(mode="create", target_device="/dev/sdz"))
        self.assertEqual(events, ["precheck", "prepare"])

    def test_precheck_identity_is_carried_to_destructive_boundary(self) -> None:
        state = ExecutionState(mode="create", target_device="/dev/sdz")
        self.workflow.run_create(state)
        expected = {"major": 0, "minor": 101, "diskseq": 7}
        self.assertEqual(state.metadata["_target_device_identity"], expected)
        self.assertEqual(self.partition_service.expected_identity, expected)

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

    def test_stale_work_mount_on_another_usb_blocks_before_device_preparation(self) -> None:
        self.workflow.precheck = lambda _state: None  # type: ignore[method-assign]
        with patch.object(
            self.workflow,
            "_stale_work_mounts",
            return_value=["/tmp/oyo-portable-old/root/boot/efi"],
        ):
            with self.assertRaises(AppError) as raised:
                self.workflow.run_create(ExecutionState(mode="create", target_device="/dev/sdz"))
        self.assertEqual(raised.exception.code, "E125")
        self.assertEqual(self.partition_service.calls, [])

    def test_late_target_mount_blocks_at_destructive_boundary(self) -> None:
        self.workflow.precheck = lambda _state: None  # type: ignore[method-assign]
        inspection_count = 0

        def inspect_target(_device: str, *, expected_identity=None) -> None:
            nonlocal inspection_count
            inspection_count += 1
            if inspection_count == 2:
                raise RuntimeError("late mount appeared")

        self.partition_service.assert_no_target_residuals = inspect_target  # type: ignore[attr-defined,method-assign]
        with patch.object(self.partition_service, "prepare_device") as prepare:
            with self.assertRaises(AppError) as raised:
                self.workflow.run_create(ExecutionState(mode="create", target_device="/dev/sdz"))
        self.assertEqual(raised.exception.code, "E125")
        self.assertEqual(inspection_count, 2)
        prepare.assert_not_called()

    def test_mountinfo_read_failure_blocks_before_device_preparation(self) -> None:
        self.workflow.precheck = lambda _state: None  # type: ignore[method-assign]
        with patch.object(self.workflow, "_stale_work_mounts", side_effect=OSError("mountinfo unavailable")):
            with self.assertRaises(AppError) as raised:
                self.workflow.run_create(ExecutionState(mode="create", target_device="/dev/sdz"))
        self.assertEqual(raised.exception.code, "E125")
        self.assertEqual(self.partition_service.calls, [])

    def test_stale_mount_scanner_matches_bind_mounts_under_workdir_prefix(self) -> None:
        with patch(
            "src.core.workflow.read_mountinfo_entries",
            return_value=[("/var/tmp/oyo-portable-a1b2c3d4/root/dev", "/dev/other-usb3")],
        ):
            self.assertEqual(
                self.workflow._stale_work_mounts(),
                ["/var/tmp/oyo-portable-a1b2c3d4/root/dev"],
            )

    def test_stale_mount_scanner_matches_tempfile_suffix_with_underscore(self) -> None:
        with patch(
            "src.core.workflow.read_mountinfo_entries",
            return_value=[("/old-tmp/oyo-portable-ab_cd123/root/dev", "/dev/other-usb3")],
        ):
            self.assertEqual(
                self.workflow._stale_work_mounts(),
                ["/old-tmp/oyo-portable-ab_cd123/root/dev"],
            )

    def test_lock_contention_fails_before_precheck_or_device_mutation(self) -> None:
        self.workflow.precheck = lambda _state: self.fail("precheck must not run without the lock")  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "operation.lock"
            self.workflow.operation_lock_factory = lambda: OperationLock(lock_path)
            with OperationLock(lock_path):
                with self.assertRaises(AppError) as raised:
                    self.workflow.run_create(ExecutionState(mode="create", target_device="/dev/sdz"))
        self.assertEqual(raised.exception.code, "E124")
        self.assertEqual(self.partition_service.calls, [])

    def test_operation_lock_remains_held_through_cleanup_and_releases_afterward(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "operation.lock"
            self.workflow.operation_lock_factory = lambda: OperationLock(lock_path)
            cleanup = self.workflow.cleanup

            def assert_locked_during_cleanup(state: ExecutionState) -> None:
                with self.assertRaises(AppError) as raised:
                    with OperationLock(lock_path):
                        self.fail("lock was released before cleanup")
                self.assertEqual(raised.exception.code, "E124")
                cleanup(state)

            self.workflow.cleanup = assert_locked_during_cleanup  # type: ignore[method-assign]
            self.workflow.run_create(ExecutionState(mode="create", target_device="/dev/sdz"))
            with OperationLock(lock_path):
                pass

    def test_cancel_after_mount_attempt_runs_cleanup_before_inhibitor_release(self) -> None:
        events: list[str] = []

        class CancellingRunner(DummyRunner):
            checks = 0

            def check_cancelled(self) -> None:
                self.checks += 1
                if self.checks == 4:
                    raise AppError.translated("E123", "error.operation_cancelled")

            @contextmanager
            def allow_cleanup_after_cancel(self):
                yield

            def run(self, command: list[str], check: bool = False, **_: object) -> DummyRunnerResult:
                if command[0] == "umount":
                    events.append("umount")
                return DummyRunnerResult()

        class TrackingInhibitor:
            def __enter__(self):
                events.append("inhibitor_enter")
                return self

            def __exit__(self, *_args):
                events.append("inhibitor_release")

        runner = CancellingRunner()
        device_service = DummyDeviceService()
        device_service.runner = runner
        self.workflow.device_service = device_service
        self.workflow.suspend_inhibitor_factory = TrackingInhibitor
        with self.assertRaises(AppError) as raised:
            self.workflow.run_create(ExecutionState(mode="create", target_device="/dev/sdz"))

        self.assertEqual(raised.exception.code, "E123")
        self.assertIn("umount", events)
        self.assertEqual(events[-1], "inhibitor_release")
        self.assertEqual(self.copy_service.rsync_calls, [])

    def _assert_cleanup_failure_preserves_workdir(self, failure: BaseException | int) -> None:
        runner = UnmountFailureRunner(failure)
        device_service = DummyDeviceService()
        device_service.runner = runner
        self.workflow.device_service = device_service
        workdir = self.work_root / "workflow-workdir"
        workdir.mkdir()
        marker = workdir / "valuable-data"
        marker.write_text("preserve me")

        with patch("src.core.workflow.tempfile.mkdtemp", return_value=str(workdir)):
            with self.assertRaises((RuntimeError, KeyboardInterrupt)):
                self.workflow.run_create(ExecutionState(mode="create", target_device="/dev/sdz"))

        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_text(), "preserve me")
        self.assertEqual(len(runner.unmounts), 3)

    def test_unmount_exception_preserves_workdir(self) -> None:
        self._assert_cleanup_failure_preserves_workdir(KeyboardInterrupt())

    def test_nonzero_unmount_preserves_workdir(self) -> None:
        self._assert_cleanup_failure_preserves_workdir(32)

    def test_failed_boot_mount_attempts_reconcile_confirmed_absence(self) -> None:
        runner = PartialBootMountFailureRunner()
        device_service = DummyDeviceService()
        device_service.runner = runner
        partition_service = PartitionService(runner, DummyLogger())
        self.workflow.device_service = device_service
        self.workflow.partition_service = partition_service
        workdir = self.work_root / "partial-mount-workdir"
        workdir.mkdir()
        state = ExecutionState(mode="create", target_device="/dev/sdz")

        with patch.object(partition_service, "prepare_device", return_value=("/dev/efi", "/dev/boot", "/dev/root")):
            with patch.object(partition_service, "mount_source_for", return_value=None):
                with patch.object(partition_service, "assert_no_target_residuals"):
                    with patch("src.core.workflow.tempfile.mkdtemp", return_value=str(workdir)):
                        with self.assertRaises(AppError):
                            self.workflow.run_create(state)

        self.assertIn(["mount", "/dev/root", str(workdir / "root")], runner.commands)
        self.assertIn(["mount", "/dev/boot", str(workdir / "root" / "boot")], runner.commands)
        self.assertFalse(any(command[0] == "umount" for command in runner.commands))
        self.assertEqual(state.mounted_paths, [])
        self.assertFalse(workdir.exists())

    def test_symlinked_tmpdir_active_mount_prevents_recursive_workdir_removal(self) -> None:
        runner = DummyRunner()
        device_service = DummyDeviceService()
        device_service.runner = runner
        partition_service = PartitionService(runner, DummyLogger())
        self.workflow.device_service = device_service
        self.workflow.partition_service = partition_service

        physical_tmp = self.work_root / "physical-tmp"
        physical_tmp.mkdir()
        tmp_alias = self.work_root / "tmp-alias"
        tmp_alias.symlink_to(physical_tmp, target_is_directory=True)
        alias_workdir = tmp_alias / "workflow-workdir"
        alias_workdir.mkdir()
        marker = alias_workdir / "valuable-data"
        marker.write_text("preserve while mounted")
        canonical_workdir = alias_workdir.resolve()
        canonical_root = canonical_workdir / "root"
        mountinfo = f"36 25 8:1 / {canonical_root} rw,relatime - ext4 /dev/fake-root rw\n"
        state = ExecutionState(mode="create", target_device="/dev/sdz")

        with patch.object(partition_service, "prepare_device", return_value=("/dev/efi", "/dev/boot", "/dev/fake-root")):
            with patch.object(partition_service, "assert_no_target_residuals"):
                with patch("src.core.workflow.tempfile.mkdtemp", return_value=str(alias_workdir)):
                    with patch("pathlib.Path.read_text", return_value=mountinfo):
                        with patch("src.core.workflow.shutil.rmtree") as remove_tree:
                            with self.assertRaisesRegex(RuntimeError, "remains active"):
                                self.workflow.run_create(state)

        expected_mount = str(canonical_root)
        # On uncertain cleanup, retain every tracked attempt for a future
        # reconciliation rather than dropping paths that appeared absent.
        self.assertEqual(
            state.mounted_paths,
            [expected_mount, expected_mount + "/boot", expected_mount + "/boot/efi"],
        )
        self.assertEqual(state.metadata["mount_sources"][expected_mount], "/dev/fake-root")
        self.assertIn({"command": ["umount", expected_mount]}, runner.calls)
        remove_tree.assert_not_called()
        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_text(), "preserve while mounted")

    def test_absent_root_boot_and_efi_attempts_do_not_block_mapper_close(self) -> None:
        runner = DummyRunner()
        device_service = DummyDeviceService()
        device_service.runner = runner
        partition_service = PartitionService(runner, DummyLogger())
        self.workflow.device_service = device_service
        self.workflow.partition_service = partition_service
        state = ExecutionState(mode="create", target_device="/dev/sdz")
        paths = ["/work/root", "/work/root/boot", "/work/root/boot/efi"]
        state.mounted_paths.extend(paths)
        state.metadata["mount_sources"] = dict(zip(paths, ["/dev/root", "/dev/boot", "/dev/efi"]))
        state.metadata["luks_mapper_name"] = "test-map"
        state.metadata["luks_mapper_backing_device"] = "/dev/root"
        with patch.object(partition_service, "mount_source_for", return_value=None):
            with patch.object(partition_service, "mapper_backing_device", return_value="/dev/root"):
                self.workflow.cleanup(state)
        self.assertEqual(state.mounted_paths, [])
        self.assertFalse(any(call["command"][0] == "umount" for call in runner.calls))
        self.assertIn(["cryptsetup", "close", "test-map"], [call["command"] for call in runner.calls])

    def test_interrupted_mapper_open_is_closed_only_when_backing_matches(self) -> None:
        for actual_backing, should_close in (("/dev/root", True), ("/dev/foreign", False)):
            with self.subTest(actual_backing=actual_backing):
                runner = DummyRunner()
                device_service = DummyDeviceService()
                device_service.runner = runner
                partition_service = PartitionService(runner, DummyLogger())
                self.workflow.device_service = device_service
                self.workflow.partition_service = partition_service
                state = ExecutionState(mode="create", target_device="/dev/sdz")
                state.metadata["luks_mapper_name"] = "raced-map"
                state.metadata["luks_mapper_backing_device"] = "/dev/root"
                with patch.object(partition_service, "mapper_backing_device", return_value=actual_backing):
                    if should_close:
                        self.workflow.cleanup(state)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "unexpected backing"):
                            self.workflow.cleanup(state)
                commands = [call["command"] for call in runner.calls]
                self.assertEqual(["cryptsetup", "close", "raced-map"] in commands, should_close)

    def test_foreign_mount_identity_is_not_unmounted_or_closed(self) -> None:
        runner = DummyRunner()
        device_service = DummyDeviceService()
        device_service.runner = runner
        partition_service = PartitionService(runner, DummyLogger())
        self.workflow.device_service = device_service
        self.workflow.partition_service = partition_service
        path = "/tmp/possibly-foreign-root"
        state = ExecutionState(mode="create", target_device="/dev/sdz")
        state.mounted_paths.append(path)
        state.metadata.update({"mount_sources": {path: "/dev/root"}, "luks_mapper_name": "test-map", "luks_mapper_backing_device": "/dev/root"})
        with patch.object(partition_service, "mount_source_for", return_value="/dev/foreign"):
            with self.assertRaisesRegex(RuntimeError, "mount identity mismatch"):
                self.workflow.cleanup(state)
        self.assertEqual(state.mounted_paths, [path])
        self.assertFalse(any(call["command"][0] == "umount" for call in runner.calls))
        self.assertFalse(any(call["command"][:2] == ["cryptsetup", "close"] for call in runner.calls))

    def test_successful_umount_that_leaves_mount_active_is_not_trusted(self) -> None:
        runner = DummyRunner()
        device_service = DummyDeviceService()
        device_service.runner = runner
        partition_service = PartitionService(runner, DummyLogger())
        self.workflow.device_service = device_service
        self.workflow.partition_service = partition_service
        path = "/tmp/still-mounted-root"
        state = ExecutionState(mode="create", target_device="/dev/sdz")
        state.mounted_paths.append(path)
        state.metadata["mount_sources"] = {path: "/dev/root"}
        with patch.object(partition_service, "mount_source_for", side_effect=["/dev/root", "/dev/root"]):
            with self.assertRaisesRegex(RuntimeError, "remains active"):
                self.workflow.cleanup(state)
        self.assertEqual(state.mounted_paths, [path])

    def test_real_unmount_failure_keeps_path_and_skips_mapper_close(self) -> None:
        runner = UnmountFailureRunner(32)
        device_service = DummyDeviceService()
        device_service.runner = runner
        partition_service = PartitionService(runner, DummyLogger())
        self.workflow.device_service = device_service
        self.workflow.partition_service = partition_service
        path = "/tmp/active-root"
        state = ExecutionState(mode="create", target_device="/dev/sdz")
        state.mounted_paths.append(path)
        state.metadata.update({"mount_sources": {path: "/dev/root"}, "luks_mapper_name": "test-map", "luks_mapper_backing_device": "/dev/root"})
        with patch.object(partition_service, "mount_source_for", return_value="/dev/root"):
            with self.assertRaisesRegex(RuntimeError, "umount failed"):
                self.workflow.cleanup(state)
        self.assertEqual(state.mounted_paths, [path])
        self.assertFalse(any(call["command"][:2] == ["cryptsetup", "close"] for call in runner.calls))

    def test_mapper_close_nonzero_preserves_workdir(self) -> None:
        runner = MapperCloseFailureRunner()
        device_service = DummyDeviceService()
        device_service.runner = runner
        partition_service = PartitionService(runner, DummyLogger())
        self.workflow.device_service = device_service
        self.workflow.partition_service = partition_service
        workdir = self.work_root / "mapper-close-workdir"
        workdir.mkdir()
        marker = workdir / "valuable-data"
        marker.write_text("preserve me")
        state = ExecutionState(
            mode="create",
            target_device="/dev/sdz",
            options={"encryption_enabled": True, "luks_passphrase": "test-passphrase", "luks_mapper_name": "test-mapper"},
        )

        with patch.object(partition_service, "prepare_device", return_value=("/dev/efi", "/dev/boot", "/dev/root")):
            with patch.object(partition_service, "assert_no_target_residuals"):
                with patch.object(partition_service, "assert_mapper_available"):
                    with patch("src.core.workflow.tempfile.mkdtemp", return_value=str(workdir)):
                        with self.assertRaises(AppError):
                            self.workflow.run_create(state)

        self.assertIn(["cryptsetup", "close", "test-mapper"], runner.commands)
        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_text(), "preserve me")

if __name__ == "__main__":
    unittest.main()
