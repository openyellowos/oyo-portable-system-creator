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
            return self._validate_backup_source(source_device)
        raise AppError("E201", "コピー元が不正です")

    def _validate_backup_source(self, source_path: str) -> str:
        path = Path(source_path)
        if not path.exists() or not path.is_dir():
            raise AppError("E201", "backup 元はマウント済みのディレクトリを指定してください")

        fstype = self.runner.run(["findmnt", "-n", "-o", "FSTYPE", "-T", str(path)], check=False).stdout.strip()
        if fstype and fstype != "ext4":
            raise AppError("E201", f"backup 元 filesystem が非対応です: {fstype}")

        marker_files = [
            path / "etc/fstab",
            path / "etc/systemd/system/oyo-firstboot.service",
        ]
        if not all(m.exists() for m in marker_files):
            raise AppError("E201", "backup 元が portable USB root と判定できません")
        return str(path)

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
        template = Path(__file__).resolve().parent.parent / "templates/fstab.portable"
        try:
            body = template.read_text(encoding="utf-8")
            body = body.replace("{{ROOT_UUID}}", root_uuid).replace("{{EFI_UUID}}", efi_uuid)
            fstab.write_text(body, encoding="utf-8")
        except OSError as exc:
            raise AppError("E402", f"fstab 生成失敗: {exc}") from exc
