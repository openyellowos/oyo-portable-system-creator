from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from src.core.errors import AppError
from src.core.state import ExecutionState
from src.gui.i18n import build_translator
from src.infra.logger import AppLogger
from src.infra.mountinfo import read_mountinfo_entries
from src.infra.operation_lock import OperationLock
from src.infra.suspend_inhibitor import SuspendInhibitor
from src.services.boot_service import BootService
from src.services.copy_service import CopyService
from src.services.device_service import DeviceService
from src.services.firstboot_service import FirstbootService
from src.services.optimize_service import OptimizeService
from src.services.partition_service import PartitionService


class Workflow:
    def __init__(
        self,
        device_service: DeviceService,
        partition_service: PartitionService,
        copy_service: CopyService,
        boot_service: BootService,
        optimize_service: OptimizeService,
        firstboot_service: FirstbootService,
        logger: AppLogger,
        language: str = "ja",
        suspend_inhibitor_factory: Callable[[], AbstractContextManager[object]] = SuspendInhibitor,
        operation_lock_factory: Callable[[], AbstractContextManager[object]] = OperationLock,
    ) -> None:
        self.device_service = device_service
        self.partition_service = partition_service
        self.copy_service = copy_service
        self.boot_service = boot_service
        self.optimize_service = optimize_service
        self.firstboot_service = firstboot_service
        self.logger = logger
        self.suspend_inhibitor_factory = suspend_inhibitor_factory
        self.operation_lock_factory = operation_lock_factory
        self.translate = build_translator(language)

    def precheck(self, state: ExecutionState) -> None:
        self.device_service.check_os()
        self.device_service.check_root()
        encryption_enabled = bool(state.options.get("encryption_enabled"))
        self.device_service.check_required_commands(encryption_enabled=encryption_enabled)
        if not state.target_device:
            raise AppError.translated("E201", "error.target_required")
        self.device_service.validate_target_device(state.target_device)
        identity_reader = getattr(self.device_service, "target_device_identity", None)
        if identity_reader is not None:
            state.metadata["_target_device_identity"] = identity_reader(state.target_device)
        source_path = self.copy_service.resolve_source(state.mode, state.source_device)
        state.metadata["source_path"] = source_path
        copy_bytes = self.copy_service.estimate_copy_bytes(source_path, state.mode)
        state.metadata["copy_bytes"] = copy_bytes
        state.used_bytes = copy_bytes
        required = self.device_service.estimate_required_bytes(copy_bytes)
        state.required_bytes = required
        self.device_service.check_capacity(state.target_device, required)

    def run_create(self, state: ExecutionState) -> ExecutionState:
        return self._run(state, "create")

    def run_backup(self, state: ExecutionState) -> ExecutionState:
        return self._run(state, "backup")

    def _run(self, state: ExecutionState, mode: str) -> ExecutionState:
        with self.operation_lock_factory():
            self._update_progress(state, 5, self.translate("workflow.precheck.start"))
            self.precheck(state)
            self._check_cancelled()
            self._assert_no_stale_work_mounts()
            self._assert_no_target_residuals(
                state.target_device or "", state.metadata.get("_target_device_identity")
            )
            if bool(state.options.get("encryption_enabled")):
                mapper_name = str(state.options.get("luks_mapper_name") or "oyoport-cryptroot")
                self._assert_mapper_available(mapper_name)
            self._update_progress(state, 15, self.translate("workflow.precheck.done"))
            return self._run_after_precheck(state, mode)

    def _run_after_precheck(self, state: ExecutionState, mode: str) -> ExecutionState:
        # Keep the inhibitor active through cleanup so suspend cannot interrupt unmounts.
        with self.suspend_inhibitor_factory():
            workdir = Path(tempfile.mkdtemp(prefix="oyo-portable-"))
            state.metadata["_workflow_workdir"] = str(workdir)
            completed = False
            try:
                self._check_cancelled()
                target_identity = state.metadata.get("_target_device_identity")
                self._assert_no_target_residuals(state.target_device or "", target_identity)
                self._update_progress(state, 20, self.translate("workflow.partition.create"))
                efi, boot, root = self.partition_service.prepare_device(
                    state.target_device or "", expected_identity=target_identity
                )
                self._check_cancelled()
                self._update_progress(state, 30, self.translate("workflow.fs.mount"))
                encryption_enabled = bool(state.options.get("encryption_enabled"))
                mapper_name = str(state.options.get("luks_mapper_name") or "oyoport-cryptroot")
                def register_mount_attempt(path: Path, source: str | None = None) -> None:
                    mount_path = PartitionService.canonical_mountpoint(path)
                    if mount_path not in state.mounted_paths:
                        state.mounted_paths.append(mount_path)
                    if source is not None:
                        sources = state.metadata.setdefault("mount_sources", {})
                        sources[mount_path] = source

                def register_mapper_open_attempt(name: str, backing_device: str) -> None:
                    state.metadata["luks_mapper_name"] = name
                    state.metadata["luks_mapper_backing_device"] = backing_device

                def latch_bind_cleanup_failure() -> None:
                    # Persist each failed chroot bind release immediately,
                    # even if a later umount is cancelled.
                    state.metadata["_chroot_bind_cleanup_failed"] = True

                root_mount, root_device, boot_mount = self.partition_service.make_filesystems_and_mount(
                    efi,
                    boot,
                    root,
                    workdir,
                    encryption_enabled=encryption_enabled,
                    luks_passphrase=state.options.get("luks_passphrase"),
                    mapper_name=mapper_name,
                    on_mount_attempt=register_mount_attempt,
                    on_mapper_open_attempt=register_mapper_open_attempt,
                )
                register_mount_attempt(root_mount, root_device)
                register_mount_attempt(boot_mount, boot)
                state.metadata["root_device"] = root_device
                if encryption_enabled:
                    state.metadata["luks_uuid"] = self._blkid(root)
                    state.metadata["luks_mapper_name"] = mapper_name

                self._check_cancelled()
                self._update_progress(state, 45, self.translate("workflow.copy.first"))
                source_path = str(state.metadata.get("source_path") or self.copy_service.resolve_source(mode, state.source_device))
                self._rsync_copy_with_progress(state, source_path, root_mount, mode, 1)
                self._check_cancelled()
                self._rsync_copy_with_progress(state, source_path, root_mount, mode, 2)
                self._check_cancelled()

                efi_mount = self.partition_service.mount_efi_partition(efi, root_mount, on_mount_attempt=register_mount_attempt)
                register_mount_attempt(efi_mount, efi)

                self._check_cancelled()
                self._update_progress(state, 60, self.translate("workflow.fstab"))
                root_uuid = self._blkid(str(state.metadata.get("root_device") or root))
                boot_uuid = self._blkid(boot)
                efi_uuid = self._blkid(efi)
                self.copy_service.write_fstab(
                    root_mount,
                    root_uuid,
                    boot_uuid,
                    efi_uuid,
                    encryption_enabled=encryption_enabled,
                    mapper_name=mapper_name,
                    luks_uuid=state.metadata.get("luks_uuid"),
                )

                self._check_cancelled()
                self._update_progress(state, 70, self.translate("workflow.grub.install"))
                self.boot_service.install_grub(
                    root_mount,
                    state.target_device or "",
                    boot_uuid,
                    on_bind_cleanup_failure=latch_bind_cleanup_failure,
                )
                self._check_cancelled()
                self._update_progress(state, 78, self.translate("workflow.initramfs"))
                self.boot_service.update_initramfs(
                    root_mount, on_bind_cleanup_failure=latch_bind_cleanup_failure,
                )
                self._check_cancelled()
                self._update_progress(state, 82, self.translate("workflow.grub.config"))
                self.boot_service.refresh_grub_config(
                    root_mount, on_bind_cleanup_failure=latch_bind_cleanup_failure,
                )

                self._check_cancelled()
                self._update_progress(state, 85, self.translate("workflow.optimize"))
                self.optimize_service.apply(root_mount)
                self._check_cancelled()
                self._update_progress(state, 92, self.translate("workflow.firstboot"))
                self.firstboot_service.install(root_mount)

                completed = True
            except AppError as exc:
                # A failed chroot bind release is unsafe even if a later mountinfo
                # snapshot says the bind is absent (e.g. lazy umount).
                if exc.code == "E699":
                    state.metadata["_chroot_bind_cleanup_failed"] = True
                raise
            finally:
                cleanup_scope = getattr(self.device_service.runner, "allow_cleanup_after_cancel", None)
                with cleanup_scope() if cleanup_scope is not None else nullcontext():
                    self.cleanup(state)
                    self._assert_no_mounts_beneath(workdir)
                    shutil.rmtree(workdir, ignore_errors=True)
        if completed:
            # A late cancellation during cleanup remains cancellation; only
            # report completion after cleanup and inhibitor release.
            self._check_cancelled()
            self._update_progress(state, 100, self.translate("workflow.done"))
            return state

    def _check_cancelled(self) -> None:
        checker = getattr(self.device_service.runner, "check_cancelled", None)
        if checker is not None:
            checker()

    def _assert_no_target_residuals(
        self,
        device: str,
        expected_identity: dict[str, int | None] | None = None,
    ) -> None:
        inspector = getattr(self.partition_service, "assert_no_target_residuals", None)
        if inspector is None:
            return
        try:
            inspector(device, expected_identity=expected_identity)
        except AppError:
            raise
        except Exception as exc:
            raise AppError.translated("E125", "error.residual_resources", reason=str(exc)) from exc

    def _assert_mapper_available(self, mapper_name: str) -> None:
        inspector = getattr(self.partition_service, "assert_mapper_available", None)
        if inspector is None:
            return
        try:
            inspector(mapper_name)
        except AppError:
            raise
        except Exception as exc:
            raise AppError.translated("E125", "error.residual_resources", reason=str(exc)) from exc

    @staticmethod
    def _stale_work_mounts() -> list[str]:
        def is_workdir_component(part: str) -> bool:
            suffix = part.removeprefix("oyo-portable-")
            return (
                part.startswith("oyo-portable-")
                and len(suffix) == 8
                and all(char in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in suffix)
            )

        stale: list[str] = []
        for mountpoint, _source in read_mountinfo_entries():
            candidate = os.path.normpath(mountpoint)
            # Match the eight-character tempfile suffix anywhere so the scan
            # also catches a previous run that used a different TMPDIR.
            if any(is_workdir_component(part) for part in Path(candidate).parts):
                stale.append(candidate)
        return sorted(set(stale))

    def _assert_no_stale_work_mounts(self) -> None:
        try:
            mounts = self._stale_work_mounts()
        except Exception as exc:
            raise AppError.translated("E125", "error.mount_state_unknown", reason=str(exc)) from exc
        if mounts:
            raise AppError.translated(
                "E125", "error.residual_resources", reason="; ".join(mounts)
            )

    def _update_progress(self, state: ExecutionState, percent: int, step: str) -> None:
        # Progress values are user-visible estimates; never let a late or
        # incremental-crawl update move the overall indicator backwards.
        state.set_progress(max(state.progress_percent, percent), step)
        self.logger.info(f"[{state.progress_percent:>3}%] {state.current_step}")

    def _copy_progress_callback(self, state: ExecutionState, pass_number: int) -> Callable[[int], None] | None:
        if not state.progress_listeners or pass_number != 1:
            return None
        estimated_bytes = state.metadata.get("copy_bytes")
        if not isinstance(estimated_bytes, int) or estimated_bytes <= 0:
            return None
        start, width = 45, 9
        step_key = "workflow.copy.first"
        base_step = self.translate(step_key)
        last_copy_percent = -1
        last_transferred_bytes = 0
        estimate_reached = False

        def update(transferred_bytes: int) -> None:
            nonlocal last_copy_percent, last_transferred_bytes, estimate_reached
            last_transferred_bytes = max(last_transferred_bytes, max(0, transferred_bytes))
            # progress2's percentage uses rsync's incrementally growing total_size.
            # Use the dry-run's fixed transferred-file-size estimate as the denominator
            # instead, while reserving the final 1% for the process-completion boundary.
            if last_transferred_bytes * 100 >= estimated_bytes * 99:
                if not estimate_reached:
                    estimate_reached = True
                    overall = start + (98 * width // 100)
                    step = self.translate("workflow.copy.estimate_reached", step=base_step)
                    self._update_progress(state, overall, step)
                return

            bounded = min(98, (last_transferred_bytes * 100) // estimated_bytes)
            if bounded == last_copy_percent:
                return
            last_copy_percent = bounded
            overall = start + (bounded * width // 100)
            step = self.translate("workflow.copy.progress", step=base_step, progress=bounded)
            self._update_progress(state, overall, step)

        return update

    def _rsync_copy_with_progress(self, state: ExecutionState, source: str, target: Path, mode: str, pass_number: int) -> None:
        if pass_number == 1:
            callback = self._copy_progress_callback(state, pass_number)
            if callback is None:
                self.copy_service.rsync_copy(source, target, mode)
            else:
                self.copy_service.rsync_copy(source, target, mode, progress_callback=callback)
            # The last estimated byte count is not proof of completion. Advance into
            # the final portion only after rsync returned successfully.
            self._update_progress(state, 54, self.translate("workflow.copy.first"))
            return

        second_step = self.translate("workflow.copy.second")
        self._update_progress(
            state,
            55,
            self.translate("workflow.copy.verifying", step=second_step),
        )
        # The second pass is mostly a comparison/metadata check, so byte progress is
        # not a useful completion measure. Keep a stable, descriptive status instead.
        self.copy_service.rsync_copy(source, target, mode)
        self._update_progress(state, 59, second_step)

    def _blkid(self, part: str) -> str:
        result = self.device_service.runner.run(["blkid", "-s", "UUID", "-o", "value", part], check=False)
        return result.stdout.strip() or "UNKNOWN"

    def cleanup(self, state: ExecutionState) -> None:
        mounts = state.mounted_paths[:]
        failures: list[BaseException] = []
        if state.metadata.get("_chroot_bind_cleanup_failed"):
            failures.append(RuntimeError("chroot bind unmount failed; workdir removal is unsafe"))
        mount_sources = state.metadata.get("mount_sources", {})
        for path in reversed(mounts):
            try:
                canonical_path = PartitionService.canonical_mountpoint(path)
                mount_state_reader = getattr(self.partition_service, "mount_source_for", None)
                if mount_state_reader is not None:
                    actual_source = mount_state_reader(canonical_path)
                    if actual_source is None:
                        state.mounted_paths.remove(path)
                        continue
                    expected_source = mount_sources.get(canonical_path, mount_sources.get(path))
                    if expected_source is None or os.path.realpath(actual_source) != os.path.realpath(expected_source):
                        failures.append(RuntimeError(f"mount identity mismatch or unknown for {path}"))
                        continue
                result = self.device_service.runner.run(["umount", canonical_path], check=False)
                returncode = getattr(result, "returncode", None)
                if returncode != 0:
                    failures.append(RuntimeError(f"umount failed for {path} (exit status {returncode})"))
                if mount_state_reader is not None:
                    remaining_source = mount_state_reader(canonical_path)
                    if remaining_source is None:
                        if returncode == 0:
                            state.mounted_paths.remove(path)
                    elif returncode == 0:
                        failures.append(RuntimeError(f"mount remains active after umount: {path}"))
                elif returncode == 0:
                    state.mounted_paths.remove(path)
            except BaseException as exc:
                # Unknown mount state is unsafe: retain the workdir and mapper.
                failures.append(exc)

        workdir = state.metadata.get("_workflow_workdir")
        if workdir:
            try:
                self._assert_no_mounts_beneath(Path(str(workdir)))
            except Exception as exc:
                failures.append(RuntimeError(f"could not confirm workdir mounts are absent: {exc}"))

        mapper_name = state.metadata.get("luks_mapper_name")
        if not failures and mapper_name:
            try:
                expected_backing = state.metadata.get("luks_mapper_backing_device")
                backing_reader = getattr(self.partition_service, "mapper_backing_device", None)
                if backing_reader is not None:
                    actual_backing = backing_reader(str(mapper_name))
                    if actual_backing is not None:
                        if not expected_backing or os.path.realpath(actual_backing) != os.path.realpath(str(expected_backing)):
                            raise RuntimeError(f"mapper {mapper_name} has unexpected backing device")
                        self.partition_service.close_encrypted_root(str(mapper_name))
                elif expected_backing is None:
                    raise RuntimeError(f"cannot verify backing device for mapper {mapper_name}")
                else:
                    # Compatibility path for service fakes; production has the
                    # mapper_backing_device verifier above.
                    self.partition_service.close_encrypted_root(str(mapper_name))
            except BaseException as exc:
                failures.append(exc)

        if failures:
            # Keep attempted resources available for any later reconciliation.
            # Some unmounts may have succeeded; a retry will recheck actual state.
            state.mounted_paths[:] = mounts
            raise failures[0]

    @staticmethod
    def _mountpoints_in_process_namespace() -> set[str]:
        """Use the same strict mountinfo parser as PartitionService."""
        return {mountpoint for mountpoint, _ in read_mountinfo_entries()}

    @classmethod
    def _mountpoint_present(cls, path: str) -> bool:
        target = os.path.normpath(os.path.realpath(path))
        return target in cls._mountpoints_in_process_namespace()

    @classmethod
    def _assert_no_mounts_beneath(cls, path: Path) -> None:
        target = os.path.normpath(os.path.realpath(path))
        remaining = [
            mountpoint
            for mountpoint in cls._mountpoints_in_process_namespace()
            if os.path.commonpath((target, mountpoint)) == target
        ]
        if remaining:
            raise RuntimeError(
                f"mounts remain beneath workflow workdir {target}: {', '.join(sorted(remaining))}"
            )
