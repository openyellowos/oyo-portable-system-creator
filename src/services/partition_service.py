from __future__ import annotations

import time
from pathlib import Path

from src.core.errors import AppError
from src.infra.command_runner import CommandRunner
from src.infra.logger import AppLogger


class PartitionService:
    def __init__(self, runner: CommandRunner, logger: AppLogger) -> None:
        self.runner = runner
        self.logger = logger

    def prepare_device(self, device: str) -> tuple[str, str]:
        try:
            self.unmount_device(device)
            self.runner.run(["parted", "-s", device, "mklabel", "gpt"])
            self.runner.run(["parted", "-s", device, "mkpart", "ESP", "fat32", "1MiB", "513MiB"])
            self.runner.run(["parted", "-s", device, "set", "1", "esp", "on"])
            self.runner.run(["parted", "-s", device, "mkpart", "root", "ext4", "513MiB", "100%"])
            self.runner.run(["partprobe", device], check=False)
            time.sleep(1)
        except Exception as exc:
            raise AppError("E301", f"パーティション作成失敗: {exc}") from exc

        efi = f"{device}1"
        root = f"{device}2"
        return efi, root

    def make_filesystems_and_mount(self, efi_part: str, root_part: str, workdir: Path) -> tuple[Path, Path]:
        root_mount = workdir / "root"
        efi_mount = root_mount / "boot/efi"
        root_mount.mkdir(parents=True, exist_ok=True)
        efi_mount.mkdir(parents=True, exist_ok=True)
        try:
            self.runner.run(["mkfs.vfat", "-F", "32", "-n", "OYOPORT_EFI", efi_part])
            self.runner.run(["mkfs.ext4", "-F", "-L", "OYOPORT_ROOT", root_part])
        except Exception as exc:
            raise AppError("E302", f"mkfs 失敗: {exc}") from exc
        try:
            self.runner.run(["mount", root_part, str(root_mount)])
            self.runner.run(["mount", efi_part, str(efi_mount)])
        except Exception as exc:
            raise AppError("E303", f"mount 失敗: {exc}") from exc
        return root_mount, efi_mount

    def unmount_device(self, device: str) -> None:
        self.runner.run(["bash", "-lc", f"mount | awk '$1 ~ /^{device}/ {{print $3}}' | xargs -r -n1 umount"], check=False)
