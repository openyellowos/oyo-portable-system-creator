from __future__ import annotations

from pathlib import Path

from src.core.errors import AppError
from src.infra.command_runner import CommandRunner

EXCLUDES = [
    "/dev/*", "/proc/*", "/sys/*", "/run/*", "/tmp/*", "/mnt/*", "/media/*", "lost+found"
]


class CopyService:
    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    def resolve_source(self, mode: str, source_device: str | None = None) -> str:
        if mode == "create":
            return "/"
        if mode == "backup" and source_device:
            return source_device
        raise AppError("E201", "コピー元が不正です")

    def rsync_copy(self, source: str, target_root: Path) -> None:
        command = ["rsync", "-aHAX", "--delete"]
        for pattern in EXCLUDES:
            command.extend(["--exclude", pattern])
        command.extend([f"{source.rstrip('/')}/", str(target_root)])
        result = self.runner.run(command, check=False)
        if result.returncode != 0:
            raise AppError("E401", "rsync に失敗しました")

    def write_fstab(self, target_root: Path, root_uuid: str, efi_uuid: str) -> None:
        fstab = target_root / "etc/fstab"
        fstab.parent.mkdir(parents=True, exist_ok=True)
        body = (
            f"UUID={root_uuid} / ext4 noatime,errors=remount-ro 0 1\n"
            f"UUID={efi_uuid} /boot/efi vfat umask=0077 0 1\n"
            "tmpfs /tmp tmpfs defaults,noatime,mode=1777 0 0\n"
        )
        try:
            fstab.write_text(body, encoding="utf-8")
        except OSError as exc:
            raise AppError("E402", f"fstab 生成失敗: {exc}") from exc
