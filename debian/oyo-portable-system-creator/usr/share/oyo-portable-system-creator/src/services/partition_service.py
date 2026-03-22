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
            self._clear_device_signatures(device)
            self.runner.run(["parted", "-s", device, "mklabel", "gpt"])
            self.runner.run(["parted", "-s", device, "mkpart", "BIOSBOOT", "1MiB", "3MiB"])
            self.runner.run(["parted", "-s", device, "set", "1", "bios_grub", "on"])
            self.runner.run(["parted", "-s", device, "mkpart", "ESP", "fat32", "3MiB", "515MiB"])
            self.runner.run(["parted", "-s", device, "set", "2", "esp", "on"])
            self.runner.run(["parted", "-s", device, "mkpart", "root", "ext4", "515MiB", "100%"])
            self.runner.run(["partprobe", device], check=False)
            time.sleep(1)
        except Exception as exc:
            raise AppError("E301", f"パーティション作成失敗: {exc}") from exc

        suffix = "p" if device.startswith("/dev/nvme") or device.startswith("/dev/mmcblk") else ""
        efi = f"{device}{suffix}2"
        root = f"{device}{suffix}3"
        return efi, root

    def _clear_device_signatures(self, device: str) -> None:
        self.runner.run(["dd", "if=/dev/zero", f"of={device}", "bs=1M", "count=16", "conv=fsync"], check=True)

    def make_filesystems_and_mount(self, efi_part: str, root_part: str, workdir: Path) -> Path:
        root_mount = workdir / "root"
        root_mount.mkdir(parents=True, exist_ok=True)
        try:
            self.runner.run(["mkfs.vfat", "-F", "32", "-n", "OYOPORT_EFI", efi_part])
            self.runner.run(["mkfs.ext4", "-F", "-L", "OYOPORT_ROOT", root_part])
        except Exception as exc:
            raise AppError("E302", f"mkfs 失敗: {exc}") from exc
        try:
            self.runner.run(["mount", root_part, str(root_mount)])
        except Exception as exc:
            raise AppError("E303", f"mount 失敗: {exc}") from exc
        return root_mount

    def mount_efi_partition(self, efi_part: str, root_mount: Path) -> Path:
        efi_mount = root_mount / "boot/efi"
        efi_mount.mkdir(parents=True, exist_ok=True)
        try:
            self.runner.run(["mount", efi_part, str(efi_mount)])
        except Exception as exc:
            raise AppError("E303", f"EFI mount 失敗: {exc}") from exc
        return efi_mount

    def unmount_device(self, device: str) -> None:
        escaped = device.replace("/", "\\/")
        self.runner.run(["bash", "-lc", f"mount | awk '$1 ~ /^{escaped}/ {{print $3}}' | xargs -r -n1 umount"], check=False)
