from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from src.core.errors import AppError
from src.core.state import ExecutionState
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
    ) -> None:
        self.device_service = device_service
        self.partition_service = partition_service
        self.copy_service = copy_service
        self.boot_service = boot_service
        self.optimize_service = optimize_service
        self.firstboot_service = firstboot_service
        self.logger = logger

    def precheck(self, state: ExecutionState) -> None:
        self.device_service.check_os()
        self.device_service.check_root()
        self.device_service.check_required_commands()
        if not state.target_device:
            raise AppError("E201", "コピー先デバイスを指定してください")
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
            self._update_progress(state, 5, "事前チェックを開始")
            self.precheck(state)
            self._update_progress(state, 15, "事前チェックが完了")
            self._update_progress(state, 20, "パーティションを作成")
            efi, root = self.partition_service.prepare_device(state.target_device or "")
            self._update_progress(state, 30, "ファイルシステムを作成してマウント")
            root_mount = self.partition_service.make_filesystems_and_mount(efi, root, workdir)
            state.mounted_paths.append(str(root_mount))

            self._update_progress(state, 45, "システムをコピー (1/2)")
            source_path = str(state.metadata.get("source_path") or self.copy_service.resolve_source(mode, state.source_device))
            self.copy_service.rsync_copy(source_path, root_mount, mode)
            self._update_progress(state, 55, "システムを再同期 (2/2)")
            self.copy_service.rsync_copy(source_path, root_mount, mode)

            efi_mount = self.partition_service.mount_efi_partition(efi, root_mount)
            state.mounted_paths.append(str(efi_mount))

            self._update_progress(state, 60, "fstab を生成")
            root_uuid = self._blkid(root)
            efi_uuid = self._blkid(efi)
            self.copy_service.write_fstab(root_mount, root_uuid, efi_uuid)

            self._update_progress(state, 70, "GRUB をインストール")
            self.boot_service.install_grub(root_mount, state.target_device or "", root_uuid)
            self._update_progress(state, 78, "initramfs を更新")
            self.boot_service.update_initramfs(root_mount)

            self._update_progress(state, 85, "可搬向け設定を適用")
            self.optimize_service.apply(root_mount)
            self._update_progress(state, 92, "初回起動設定を組み込み")
            self.firstboot_service.install(root_mount)

            self._update_progress(state, 100, "完了")
            return state
        finally:
            self.cleanup(state)
            shutil.rmtree(workdir, ignore_errors=True)

    def _update_progress(self, state: ExecutionState, percent: int, step: str) -> None:
        state.set_progress(percent, step)
        self.logger.info(f"[{state.progress_percent:>3}%] {state.current_step}")

    def _blkid(self, part: str) -> str:
        result = self.device_service.runner.run(["blkid", "-s", "UUID", "-o", "value", part], check=False)
        return result.stdout.strip() or "UNKNOWN"

    def cleanup(self, state: ExecutionState) -> None:
        mounts = state.mounted_paths[:]
        for p in reversed(mounts):
            self.device_service.runner.run(["umount", "-lf", p], check=False)
        state.mounted_paths.clear()
