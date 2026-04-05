from __future__ import annotations

import json
import os
import shutil
import re

from src.core.errors import AppError
from src.infra.command_runner import CommandRunner
from src.infra.logger import AppLogger

REQUIRED_COMMANDS = [
    "lsblk",
    "findmnt",
    "blkid",
    "parted",
    "partprobe",
    "mkfs.vfat",
    "mkfs.ext4",
    "mount",
    "umount",
    "swapoff",
    "chroot",
    "rsync",
    "grub-install",
    "update-initramfs",
]

ENCRYPTION_REQUIRED_COMMANDS = [
    "cryptsetup",
]


class DeviceService:
    def __init__(self, runner: CommandRunner, logger: AppLogger) -> None:
        self.runner = runner
        self.logger = logger

    def check_os(self) -> None:
        return None

    def check_root(self) -> None:
        if os.geteuid() != 0:
            raise AppError.translated("E121", "error.root_required")

    def check_required_commands(self, *, encryption_enabled: bool = False) -> None:
        required_commands = REQUIRED_COMMANDS[:]
        if encryption_enabled:
            required_commands.extend(ENCRYPTION_REQUIRED_COMMANDS)
        missing = [c for c in required_commands if shutil.which(c) is None]
        if missing:
            raise AppError.translated("E122", "error.required_commands_missing", commands=", ".join(missing))

    def list_target_devices(self) -> list[dict]:
        result = self.runner.run(
            ["lsblk", "--json", "-b", "-o", "NAME,PATH,TYPE,SIZE,RM,HOTPLUG,TRAN,VENDOR,MODEL"],
            check=True,
        )
        data = json.loads(result.stdout)
        devices = []
        root_disk = self._root_disk_path()
        for item in data.get("blockdevices", []):
            if item.get("type") != "disk":
                continue
            path = item.get("path") or f"/dev/{item['name']}"
            if path == root_disk:
                continue
            removable = bool(item.get("rm")) or bool(item.get("hotplug")) or item.get("tran") == "usb"
            if removable:
                devices.append(item)
        return devices

    def validate_target_device(self, target_device: str) -> None:
        candidates = self.list_target_devices()
        candidate_paths = {(d.get("path") or f"/dev/{d['name']}") for d in candidates}
        root_disk = self._root_disk_path()
        if target_device == root_disk:
            raise AppError.translated("E203", "error.system_disk_not_allowed")
        if target_device not in candidate_paths:
            raise AppError.translated("E201", "error.invalid_target_device")

    def estimate_required_bytes(self, copy_bytes: int) -> int:
        required = int(copy_bytes * 1.15) + (4 * 1024**3)
        self.logger.info(
            "容量見積: "
            f"copy={self._format_gib(copy_bytes)} "
            f"required={self._format_gib(required)}"
        )
        return required

    def check_capacity(self, target_device: str, required_bytes: int) -> None:
        size = self.get_device_size_bytes(target_device)
        self.logger.info(
            "容量確認: "
            f"target={target_device} "
            f"size={self._format_gib(size)} "
            f"required={self._format_gib(required_bytes)}"
        )
        if size < required_bytes:
            raise AppError.translated(
                "E202",
                "error.insufficient_capacity",
                required_bytes=required_bytes,
                device_bytes=size,
            )

    def get_device_size_bytes(self, target_device: str) -> int:
        result = self.runner.run(["lsblk", "--json", "-b", "-o", "PATH,SIZE"], check=True)
        data = json.loads(result.stdout)
        for item in data.get("blockdevices", []):
            path = item.get("path")
            if path == target_device:
                return int(item.get("size") or 0)
        raise AppError.translated("E201", "error.target_device_not_found", target_device=target_device)

    def _root_disk_path(self) -> str:
        result = self.runner.run(["findmnt", "-n", "-o", "SOURCE", "/"], check=True)
        root_source = result.stdout.strip()
        if root_source.startswith("/dev/"):
            parent = self.runner.run(["lsblk", "-n", "-o", "PKNAME", root_source], check=False).stdout.strip()
            if parent:
                return f"/dev/{parent}"
            # Fallback for environments where PKNAME is unavailable.
            if re.match(r"^/dev/(nvme\dn\d+)p\d+$", root_source):
                return re.sub(r"p\d+$", "", root_source)
            if re.match(r"^/dev/(mmcblk\d+)p\d+$", root_source):
                return re.sub(r"p\d+$", "", root_source)
            if root_source[-1].isdigit():
                return root_source.rstrip("0123456789")
        return root_source

    @staticmethod
    def _format_gib(size_bytes: int) -> str:
        gib = size_bytes / (1024**3)
        return f"{gib:.1f} GiB"
