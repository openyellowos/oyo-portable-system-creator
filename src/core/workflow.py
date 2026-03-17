from __future__ import annotations

import tempfile
from pathlib import Path

from src.core.errors import AppError
from src.core.state import ExecutionState
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
    ) -> None:
        self.device_service = device_service
        self.partition_service = partition_service
        self.copy_service = copy_service
        self.boot_service = boot_service
        self.optimize_service = optimize_service
        self.firstboot_service = firstboot_service

    def precheck(self, state: ExecutionState) -> None:
        self.device_service.check_os()
        self.device_service.check_root()
        self.device_service.check_required_commands()
        if not state.target_device:
            raise AppError("E201", "コピー先デバイスを指定してください")
        required = self.device_service.estimate_required_bytes("/")
        state.required_bytes = required

    def run_create(self, state: ExecutionState) -> ExecutionState:
        return self._run(state, "create")

    def run_backup(self, state: ExecutionState) -> ExecutionState:
        return self._run(state, "backup")

    def _run(self, state: ExecutionState, mode: str) -> ExecutionState:
        workdir = Path(tempfile.mkdtemp(prefix="oyo-portable-"))
        try:
            state.set_progress(5, "precheck")
            self.precheck(state)
            state.set_progress(20, "partition")
            efi, root = self.partition_service.prepare_device(state.target_device or "")
            root_mount, _ = self.partition_service.make_filesystems_and_mount(efi, root, workdir)
            state.set_progress(45, "copy")
            source = self.copy_service.resolve_source(mode, state.source_device)
            self.copy_service.rsync_copy(source, root_mount)
            root_uuid = self._blkid(root)
            efi_uuid = self._blkid(efi)
            self.copy_service.write_fstab(root_mount, root_uuid, efi_uuid)
            state.set_progress(70, "bootloader")
            self.boot_service.install_grub(root_mount, state.target_device or "")
            self.boot_service.update_initramfs(root_mount)
            state.set_progress(85, "firstboot")
            self.optimize_service.apply(root_mount)
            self.firstboot_service.install(root_mount)
            state.set_progress(100, "done")
            return state
        finally:
            self.cleanup(state)

    def _blkid(self, part: str) -> str:
        result = self.device_service.runner.run(["blkid", "-s", "UUID", "-o", "value", part], check=False)
        return result.stdout.strip() or "UNKNOWN"

    def cleanup(self, state: ExecutionState) -> None:
        mounts = state.mounted_paths[:]
        for p in reversed(mounts):
            self.device_service.runner.run(["umount", "-lf", p], check=False)
