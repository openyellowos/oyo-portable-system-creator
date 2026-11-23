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

    def prepare_device(self, device: str) -> tuple[str, str, str]:
        try:
            self.unmount_device(device)
            self._clear_device_signatures(device)
            self.runner.run(["parted", "-s", device, "mklabel", "gpt"])
            self.runner.run(["parted", "-s", device, "mkpart", "BIOSBOOT", "1MiB", "3MiB"])
            self.runner.run(["parted", "-s", device, "set", "1", "bios_grub", "on"])
            self.runner.run(["parted", "-s", device, "mkpart", "ESP", "fat32", "3MiB", "515MiB"])
            self.runner.run(["parted", "-s", device, "set", "2", "esp", "on"])
            self.runner.run(["parted", "-s", device, "mkpart", "BOOT", "ext4", "515MiB", "1539MiB"])
            self.runner.run(["parted", "-s", device, "mkpart", "root", "ext4", "1539MiB", "100%"])
            self.runner.run(["partprobe", device], check=False)
            time.sleep(1)
        except AppError:
            raise
        except Exception as exc:
            raise AppError.translated("E301", "error.partition_prepare_failed", reason=str(exc)) from exc

        suffix = "p" if device.startswith("/dev/nvme") or device.startswith("/dev/mmcblk") else ""
        efi = f"{device}{suffix}2"
        boot = f"{device}{suffix}3"
        root = f"{device}{suffix}4"
        return efi, boot, root

    def _clear_device_signatures(self, device: str) -> None:
        self.runner.run(["dd", "if=/dev/zero", f"of={device}", "bs=1M", "count=16", "conv=fsync"], check=True)

    def make_filesystems_and_mount(
        self,
        efi_part: str,
        boot_part: str,
        root_part: str,
        workdir: Path,
        *,
        encryption_enabled: bool = False,
        luks_passphrase: str | None = None,
        mapper_name: str = "oyoport-cryptroot",
    ) -> tuple[Path, str, Path]:
        root_mount = workdir / "root"
        boot_mount = root_mount / "boot"
        root_mount.mkdir(parents=True, exist_ok=True)
        root_device = root_part
        try:
            self.runner.run(["mkfs.vfat", "-F", "32", "-n", "OYOPORT_EFI", efi_part])
            self.runner.run(["mkfs.ext4", "-F", "-L", "OYOPORT_BOOT", boot_part])
            if encryption_enabled:
                passphrase = luks_passphrase or ""
                self.runner.run(
                    [
                        "cryptsetup",
                        "luksFormat",
                        "--batch-mode",
                        "--type",
                        "luks2",
                        "--pbkdf",
                        "pbkdf2",
                        "--key-file",
                        "-",
                        root_part,
                    ],
                    input_text=passphrase,
                    mask_values=[passphrase],
                )
                self.runner.run(
                    ["cryptsetup", "open", "--key-file", "-", root_part, mapper_name],
                    input_text=passphrase,
                    mask_values=[passphrase],
                )
                root_device = f"/dev/mapper/{mapper_name}"
            self.runner.run(["mkfs.ext4", "-F", "-L", "OYOPORT_ROOT", root_device])
        except AppError:
            raise
        except Exception as exc:
            raise AppError.translated("E302", "error.mkfs_failed", reason=str(exc)) from exc
        try:
            self.runner.run(["mount", root_device, str(root_mount)])
            boot_mount.mkdir(parents=True, exist_ok=True)
            self.runner.run(["mount", boot_part, str(boot_mount)])
        except AppError:
            raise
        except Exception as exc:
            raise AppError.translated("E303", "error.mount_failed", reason=str(exc)) from exc
        return root_mount, root_device, boot_mount

    def mount_efi_partition(self, efi_part: str, root_mount: Path) -> Path:
        efi_mount = root_mount / "boot/efi"
        efi_mount.mkdir(parents=True, exist_ok=True)
        try:
            self.runner.run(["mount", efi_part, str(efi_mount)])
        except AppError:
            raise
        except Exception as exc:
            raise AppError.translated("E303", "error.efi_mount_failed", reason=str(exc)) from exc
        return efi_mount

    def unmount_device(self, device: str) -> None:
        active_swaps = self._active_swaps()
        result = self.runner.run(
            ["lsblk", "-nrpo", "PATH,TYPE,MOUNTPOINT", device],
            check=False,
        )
        rows: list[tuple[str, str, str]] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split(None, 2)
            path = parts[0]
            node_type = parts[1] if len(parts) > 1 else ""
            mountpoint = parts[2] if len(parts) > 2 else ""
            rows.append((path, node_type, mountpoint))

        for path, _, mountpoint in reversed(rows):
            if mountpoint:
                self.runner.run(["umount", "-lf", mountpoint], check=False)
            if path in active_swaps:
                self.runner.run(["swapoff", path], check=False)
        for path, node_type, _ in reversed(rows):
            if node_type == "crypt":
                self.runner.run(["cryptsetup", "close", Path(path).name], check=False)

        self.runner.run(["partprobe", device], check=False)
        self.runner.run(["udevadm", "settle"], check=False)

    def close_encrypted_root(self, mapper_name: str | None) -> None:
        if not mapper_name:
            return
        self.runner.run(["cryptsetup", "close", mapper_name], check=False)

    @staticmethod
    def _active_swaps() -> set[str]:
        try:
            lines = Path("/proc/swaps").read_text(encoding="utf-8").splitlines()
        except OSError:
            return set()
        return {line.split()[0] for line in lines[1:] if line.strip()}
