from __future__ import annotations

from pathlib import Path

from src.core.errors import AppError
from src.infra.chroot import ChrootHelper
from src.infra.command_runner import CommandRunner


class BootService:
    def __init__(self, runner: CommandRunner, chroot: ChrootHelper) -> None:
        self.runner = runner
        self.chroot = chroot

    def install_grub(self, root_mount: Path, target_device: str) -> None:
        try:
            self.chroot.run_in_chroot(root_mount, ["/usr/sbin/grub-install", "--target=i386-pc", target_device])
            self.chroot.run_in_chroot(
                root_mount,
                ["/usr/sbin/grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi", "--bootloader-id=OYOPORT"],
            )
            self.chroot.run_in_chroot(root_mount, ["/usr/sbin/update-grub"])
        except Exception as exc:
            raise AppError("E501", f"grub 設定失敗: {exc}") from exc

    def update_initramfs(self, root_mount: Path) -> None:
        try:
            self.chroot.run_in_chroot(root_mount, ["/usr/sbin/update-initramfs", "-u"])
        except Exception as exc:
            raise AppError("E502", f"initramfs 更新失敗: {exc}") from exc
