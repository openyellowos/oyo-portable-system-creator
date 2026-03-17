from __future__ import annotations

import json
import os
import shutil
import re
from pathlib import Path

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
    "chroot",
    "rsync",
    "grub-install",
    "update-grub",
    "update-initramfs",
]


class DeviceService:
    def __init__(self, runner: CommandRunner, logger: AppLogger) -> None:
        self.runner = runner
        self.logger = logger

    def check_os(self) -> None:
        os_release = Path("/etc/os-release")
        if not os_release.exists():
            raise AppError("E120", "/etc/os-release が見つかりません")
        content = os_release.read_text(encoding="utf-8", errors="ignore")
        if "open.Yellow.os" not in content and "openyellow" not in content.lower():
            raise AppError("E120", "open.Yellow.os 以外は非対応です")

    def check_root(self) -> None:
        if os.geteuid() != 0:
            raise AppError("E121", "root 権限で実行してください")

    def check_required_commands(self) -> None:
        missing = [c for c in REQUIRED_COMMANDS if shutil.which(c) is None]
        if missing:
            raise AppError("E122", f"必須コマンド不足: {', '.join(missing)}")

    def list_target_devices(self) -> list[dict]:
        result = self.runner.run(["lsblk", "--json", "-b", "-o", "NAME,PATH,TYPE,SIZE,RM,TRAN"], check=True)
        data = json.loads(result.stdout)
        devices = []
        root_disk = self._root_disk_path()
        for item in data.get("blockdevices", []):
            if item.get("type") != "disk":
                continue
            path = item.get("path") or f"/dev/{item['name']}"
            if path == root_disk:
                continue
            removable = bool(item.get("rm")) or item.get("tran") == "usb"
            if removable:
                devices.append(item)
        return devices

    def validate_target_device(self, target_device: str) -> None:
        candidates = self.list_target_devices()
        candidate_paths = {(d.get("path") or f"/dev/{d['name']}") for d in candidates}
        root_disk = self._root_disk_path()
        if target_device == root_disk:
            raise AppError("E203", "システムディスクは指定できません")
        if target_device not in candidate_paths:
            raise AppError("E201", "コピー先デバイスが不正です（USB/リムーバブルのみ指定可）")

    def estimate_required_bytes(self, copy_bytes: int) -> int:
        required = int(copy_bytes * 1.15) + (4 * 1024**3)
        self.logger.info(f"容量見積: copy_bytes={copy_bytes} required={required}")
        return required

    def check_capacity(self, target_device: str, required_bytes: int) -> None:
        size = self.get_device_size_bytes(target_device)
        self.logger.info(f"容量確認: target={target_device} size={size} required={required_bytes}")
        if size < required_bytes:
            raise AppError("E202", f"容量不足です: required={required_bytes}, device={size}")

    def get_device_size_bytes(self, target_device: str) -> int:
        result = self.runner.run(["lsblk", "--json", "-b", "-o", "PATH,SIZE"], check=True)
        data = json.loads(result.stdout)
        for item in data.get("blockdevices", []):
            path = item.get("path")
            if path == target_device:
                return int(item.get("size") or 0)
        raise AppError("E201", f"対象デバイスが見つかりません: {target_device}")

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
