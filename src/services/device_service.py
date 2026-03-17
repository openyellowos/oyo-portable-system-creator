from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from src.core.errors import AppError
from src.infra.command_runner import CommandRunner
from src.infra.logger import AppLogger

REQUIRED_COMMANDS = [
    "lsblk",
    "parted",
    "mkfs.vfat",
    "mkfs.ext4",
    "mount",
    "umount",
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
        result = self.runner.run(["lsblk", "--json", "-o", "NAME,PATH,TYPE,SIZE,RM,TRAN"], check=True)
        data = json.loads(result.stdout)
        devices = []
        for item in data.get("blockdevices", []):
            if item.get("type") != "disk":
                continue
            path = item.get("path") or f"/dev/{item['name']}"
            if path == self._root_disk_path():
                continue
            removable = bool(item.get("rm")) or item.get("tran") == "usb"
            if removable:
                devices.append(item)
        return devices

    def estimate_required_bytes(self, source_path: str = "/") -> int:
        st = os.statvfs(source_path)
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        required = int(used * 1.15) + (4 * 1024**3)
        self.logger.info(f"容量見積: used={used} required={required}")
        return required

    def _root_disk_path(self) -> str:
        result = self.runner.run(["findmnt", "-n", "-o", "SOURCE", "/"], check=True)
        root_source = result.stdout.strip()
        if root_source.startswith("/dev/") and root_source[-1].isdigit():
            return root_source.rstrip("0123456789")
        return root_source
