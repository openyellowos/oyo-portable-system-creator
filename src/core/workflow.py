from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from src.core.errors import AppError
from src.core.state import ExecutionState
from src.gui.i18n import build_translator
from src.infra.logger import AppLogger
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
    ) -> None:
        self.device_service = device_service
        self.partition_service = partition_service
        self.copy_service = copy_service
        self.boot_service = boot_service
        self.optimize_service = optimize_service
        self.firstboot_service = firstboot_service
        self.logger = logger
        self.translate = build_translator(language)

    def precheck(self, state: ExecutionState) -> None:
        self.device_service.check_os()
        self.device_service.check_root()
        encryption_enabled = bool(state.options.get("encryption_enabled"))
        self.device_service.check_required_commands(encryption_enabled=encryption_enabled)
        if not state.target_device:
            raise AppError.translated("E201", "error.target_required")
        self.device_service.validate_target_device(state.target_device)
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
        workdir = Path(tempfile.mkdtemp(prefix="oyo-portable-"))
        try:
            self._update_progress(state, 5, self.translate("workflow.precheck.start"))
            self.precheck(state)
            self._update_progress(state, 15, self.translate("workflow.precheck.done"))
            self._update_progress(state, 20, self.translate("workflow.partition.create"))
            efi, boot, root = self.partition_service.prepare_device(state.target_device or "")
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
            # Retain compatibility with service implementations that do not
            # report individual attempts; the real service registers before
            # each command to cover interruption windows.
            register_mount_attempt(root_mount, root_device)
            register_mount_attempt(boot_mount, boot)
            state.metadata["root_device"] = root_device
            if encryption_enabled:
                state.metadata["luks_uuid"] = self._blkid(root)
                state.metadata["luks_mapper_name"] = mapper_name

            self._update_progress(state, 45, self.translate("workflow.copy.first"))
            source_path = str(state.metadata.get("source_path") or self.copy_service.resolve_source(mode, state.source_device))
            self.copy_service.rsync_copy(source_path, root_mount, mode)
            self._update_progress(state, 55, self.translate("workflow.copy.second"))
            self.copy_service.rsync_copy(source_path, root_mount, mode)

            efi_mount = self.partition_service.mount_efi_partition(
                efi,
                root_mount,
                on_mount_attempt=register_mount_attempt,
            )
            register_mount_attempt(efi_mount, efi)

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

            self._update_progress(state, 70, self.translate("workflow.grub.install"))
            self.boot_service.install_grub(
                root_mount,
                state.target_device or "",
                boot_uuid,
            )
            self._update_progress(state, 78, self.translate("workflow.initramfs"))
            self.boot_service.update_initramfs(root_mount)
            self._update_progress(state, 82, self.translate("workflow.grub.config"))
            self.boot_service.refresh_grub_config(root_mount)

            self._update_progress(state, 85, self.translate("workflow.optimize"))
            self.optimize_service.apply(root_mount)
            self._update_progress(state, 92, self.translate("workflow.firstboot"))
            self.firstboot_service.install(root_mount)

            self._update_progress(state, 100, self.translate("workflow.done"))
            return state
        finally:
            # Removing this directory can destroy the only copy of data under
            # a still-mounted filesystem. Preserve it whenever cleanup fails.
            self.cleanup(state)
            shutil.rmtree(workdir)

    def _update_progress(self, state: ExecutionState, percent: int, step: str) -> None:
        state.set_progress(percent, step)
        self.logger.info(f"[{state.progress_percent:>3}%] {state.current_step}")

    def _blkid(self, part: str) -> str:
        result = self.device_service.runner.run(["blkid", "-s", "UUID", "-o", "value", part], check=False)
        return result.stdout.strip() or "UNKNOWN"

    def cleanup(self, state: ExecutionState) -> None:
        mounts = state.mounted_paths[:]
        failures: list[BaseException] = []
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
                returncode = getattr(result, "returncode", 0)
                if mount_state_reader is not None:
                    remaining_source = mount_state_reader(canonical_path)
                    if remaining_source is None:
                        state.mounted_paths.remove(path)
                    else:
                        reason = (
                            f"umount failed for {path} (exit status {returncode})"
                            if returncode != 0
                            else f"mount remains active after umount: {path}"
                        )
                        failures.append(RuntimeError(reason))
                elif returncode != 0:
                    failures.append(RuntimeError(f"umount failed for {path} (exit status {returncode})"))
                else:
                    state.mounted_paths.remove(path)
            except BaseException as exc:
                # Unknown mount state is unsafe: retain the workdir and mapper.
                failures.append(exc)

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
            raise failures[0]
