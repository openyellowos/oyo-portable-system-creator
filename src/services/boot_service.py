from __future__ import annotations

from pathlib import Path

from src.core.errors import AppError
from src.infra.chroot import ChrootHelper
from src.infra.command_runner import CommandRunner


class BootService:
    def __init__(self, runner: CommandRunner, chroot: ChrootHelper) -> None:
        self.runner = runner
        self.chroot = chroot

    def install_grub(self, root_mount: Path, target_device: str, root_uuid: str) -> None:
        try:
            if not target_device:
                raise AppError("E501", "grub インストール先デバイスが空です")
            if not root_uuid or root_uuid == "UNKNOWN":
                raise AppError("E501", "root UUID を取得できませんでした")

            self.chroot.run_in_chroot(
                root_mount,
                [
                    "/usr/sbin/grub-install",
                    "--target=i386-pc",
                    "--boot-directory=/boot/efi/boot",
                    "--modules=part_gpt fat ext2",
                    "--recheck",
                    target_device,
                ],
            )
            self.chroot.run_in_chroot(
                root_mount,
                [
                    "/usr/sbin/grub-install",
                    "--target=x86_64-efi",
                    "--efi-directory=/boot/efi",
                    "--bootloader-id=OYOPORT",
                    "--no-nvram",
                    "--removable",
                ],
            )
            self._write_portable_grub_configs(root_mount)
            self._ensure_portable_efi_bootloader(root_mount)
        except Exception as exc:
            raise AppError("E501", f"grub 設定失敗: {exc}") from exc

    def update_initramfs(self, root_mount: Path) -> None:
        try:
            self.chroot.run_in_chroot(root_mount, ["/usr/sbin/update-initramfs", "-u"])
        except Exception as exc:
            raise AppError("E502", f"initramfs 更新失敗: {exc}") from exc

    def refresh_grub_config(self, root_mount: Path) -> None:
        try:
            self.chroot.run_in_chroot(
                root_mount,
                ["/usr/sbin/grub-mkconfig", "-o", "/boot/grub/grub.cfg"],
            )
        except Exception as exc:
            raise AppError("E503", f"grub.cfg 更新失敗: {exc}") from exc

    def _write_portable_grub_configs(self, root_mount: Path) -> None:
        portable_cfg = root_mount / "boot/efi/boot/grub/grub.cfg"
        efi_chain_cfg_paths = self._efi_chain_config_paths(root_mount)

        try:
            portable_cfg.parent.mkdir(parents=True, exist_ok=True)
            portable_cfg.write_text(self._efi_chain_grub_config("/boot/grub/grub.cfg"), encoding="utf-8")
            portable_cfg.chmod(0o644)

            efi_chain = self._efi_chain_grub_config("/boot/grub/grub.cfg")
            for path in efi_chain_cfg_paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(efi_chain, encoding="utf-8")
                path.chmod(0o644)
        except OSError as exc:
            raise AppError("E501", f"grub 設定ファイル生成失敗: {exc}") from exc

    @staticmethod
    def _efi_chain_config_paths(root_mount: Path) -> list[Path]:
        paths = [
            root_mount / "boot/efi/EFI/BOOT/grub.cfg",
            root_mount / "boot/efi/EFI/OYOPORT/grub.cfg",
        ]
        efi_root = root_mount / "boot/efi/EFI"
        if efi_root.exists():
            for pattern in ("**/*.efi", "**/*.EFI"):
                for efi_binary in sorted(efi_root.glob(pattern)):
                    if efi_binary.is_file():
                        paths.append(efi_binary.with_name("grub.cfg"))

        unique: list[Path] = []
        seen: set[Path] = set()
        for path in paths:
            if path not in seen:
                seen.add(path)
                unique.append(path)
        return unique

    def _ensure_portable_efi_bootloader(self, root_mount: Path) -> None:
        source = self._find_existing_efi_binary(root_mount)
        if source is None:
            raise AppError("E501", "利用可能な EFI 起動バイナリが見つかりません")

        targets = [
            root_mount / "boot/efi/EFI/BOOT/BOOTX64.EFI",
            root_mount / "boot/efi/EFI/BOOT/grubx64.efi",
            root_mount / "boot/efi/EFI/OYOPORT/grubx64.efi",
        ]

        try:
            payload = source.read_bytes()
            for path in targets:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
        except OSError as exc:
            raise AppError("E501", f"EFI grub バイナリ配置失敗: {exc}") from exc

    @staticmethod
    def _find_existing_efi_binary(root_mount: Path) -> Path | None:
        preferred = [
            root_mount / "boot/efi/EFI/BOOT/BOOTX64.EFI",
            root_mount / "boot/efi/EFI/BOOT/bootx64.efi",
        ]
        for path in preferred:
            if path.exists():
                return path

        efi_root = root_mount / "boot/efi/EFI"
        if not efi_root.exists():
            return None

        patterns = [
            "**/grubx64.efi",
            "**/GRUBX64.EFI",
            "**/shimx64.efi",
            "**/SHIMX64.EFI",
            "**/*.efi",
            "**/*.EFI",
        ]
        for pattern in patterns:
            for path in sorted(efi_root.glob(pattern)):
                if path.is_file():
                    return path
        return None

    @staticmethod
    def _efi_chain_grub_config(target_config: str) -> str:
        return (
            "set default=0\n"
            "set timeout=5\n"
            "insmod fat\n"
            "insmod part_gpt\n"
            "insmod ext2\n"
            "search --no-floppy --set=root --file "
            f"{target_config}\n"
            f"configfile {target_config}\n"
        )
