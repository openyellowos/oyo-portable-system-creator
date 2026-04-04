from __future__ import annotations

import re
import tempfile
from pathlib import Path

from src.core.errors import AppError
from src.infra.command_runner import CommandRunner

COMMON_EXCLUDES = [
    "/dev/*",
    "/proc/*",
    "/sys/*",
    "/run/*",
    "/tmp/*",
    "/mnt/*",
    "/media/*",
    "lost+found",
    "/var/tmp/*",
    "/var/cache/*",
    "/var/cache/apt/archives/*",
    "/var/lib/apt/lists/*",
    "/var/log/journal/*",
    "/swapfile",
    "/home/*/.cache/*",
    "/home/*/.local/share/Trash/*",
]

CREATE_EXCLUDES = [
    "/root/*",
    "/var/log/*",
    "/var/backups/*",
    "/var/lib/apt/*",
    "/var/lib/docker/*",
    "/var/lib/containers/*",
    "/usr/include/*",
    "/usr/src/*",
    "/usr/share/doc/*",
    "/usr/share/info/*",
    "/usr/share/lintian/*",
    "/usr/share/man/*",
    "/usr/share/help/*",
]

HOME_DIRS_COPY_EMPTY = [
    "Downloads",
    "ダウンロード",
    "Documents",
    "ドキュメント",
    "Desktop",
    "デスクトップ",
    "Public",
    "公開",
    "Videos",
    "ビデオ",
    "Music",
    "音楽",
    "Pictures",
    "画像",
]

HOME_DIRS_COPY_FULL = [
    "Templates",
    "テンプレート",
]


class CopyService:
    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    def resolve_source(self, mode: str, source_device: str | None = None) -> str:
        if mode == "create":
            if source_device:
                return self._validate_create_source(source_device)
            return "/"
        if mode == "backup" and source_device:
            return self._validate_backup_source(source_device)
        raise AppError.translated("E201", "error.invalid_source")

    def _validate_create_source(self, source_path: str) -> str:
        path = Path(source_path)
        if not path.exists() or not path.is_dir():
            raise AppError.translated("E201", "error.invalid_create_source")
        return str(path.resolve())

    def _validate_backup_source(self, source_path: str) -> str:
        path = Path(source_path)
        if not path.exists() or not path.is_dir():
            raise AppError.translated("E201", "error.invalid_backup_source")

        fstype = self.runner.run(["findmnt", "-n", "-o", "FSTYPE", "-T", str(path)], check=False).stdout.strip()
        if fstype and fstype != "ext4":
            raise AppError.translated("E201", "error.unsupported_backup_filesystem", fstype=fstype)

        marker_files = [
            path / "etc/fstab",
            path / "etc/systemd/system/oyo-firstboot.service",
        ]
        if not all(m.exists() for m in marker_files):
            raise AppError.translated("E201", "error.backup_source_not_portable")
        return str(path.resolve())

    def estimate_copy_bytes(self, source: str, mode: str) -> int:
        with tempfile.TemporaryDirectory(prefix="oyo-rsync-dryrun-") as tmpdir:
            command = self._build_rsync_command(source, tmpdir, mode=mode, dry_run=True)
            result = self.runner.run(command, check=False)
        if result.returncode != 0:
            raise AppError.translated("E401", "error.rsync_dry_run_failed")
        return self._parse_total_transferred_file_size(result.stdout, result.stderr)

    def rsync_copy(self, source: str, target_root: Path, mode: str) -> None:
        command = self._build_rsync_command(source, str(target_root), mode=mode)
        result = self.runner.run(command, check=False)
        if result.returncode != 0:
            raise AppError.translated("E401", "error.rsync_failed")

    def _build_rsync_command(self, source: str, target: str, *, mode: str, dry_run: bool = False) -> list[str]:
        command = ["rsync", "-aHAX", "--delete"]
        if dry_run:
            command.extend(["--dry-run", "--stats"])
        for pattern in self._exclude_patterns_for_mode(mode):
            command.extend(["--exclude", pattern])
        for rule in self._filter_rules_for_mode(mode):
            command.extend(["--filter", rule])
        command.extend([f"{source.rstrip('/')}/", target])
        return command

    def _exclude_patterns_for_mode(self, mode: str) -> list[str]:
        if mode == "create":
            return [*COMMON_EXCLUDES, *CREATE_EXCLUDES]
        return COMMON_EXCLUDES

    def _filter_rules_for_mode(self, mode: str) -> list[str]:
        if mode == "create":
            return self._create_home_filter_rules()
        return []

    def _create_home_filter_rules(self) -> list[str]:
        rules = [
            "+ /home/",
            "+ /home/*/",
            "- /home/*/.cache/***",
            "- /home/*/.local/share/Trash/***",
            "+ /home/*/.*/",
            "+ /home/*/.*/***",
            "+ /home/*/.*",
        ]

        for directory in HOME_DIRS_COPY_EMPTY:
            rules.extend(
                [
                    f"+ /home/*/{directory}",
                    f"+ /home/*/{directory}/",
                    f"- /home/*/{directory}/***",
                ]
            )

        for directory in HOME_DIRS_COPY_FULL:
            rules.extend(
                [
                    f"+ /home/*/{directory}",
                    f"+ /home/*/{directory}/",
                    f"+ /home/*/{directory}/***",
                ]
            )

        rules.append("- /home/*/*")
        return rules

    def _parse_total_transferred_file_size(self, stdout: str, stderr: str) -> int:
        output = "\n".join(part for part in (stdout, stderr) if part)
        match = re.search(r"total transferred file size:\s*([0-9,]+)\s+bytes", output, flags=re.IGNORECASE)
        if not match:
            raise AppError.translated("E401", "error.rsync_stats_parse_failed")
        return int(match.group(1).replace(",", ""))

    def write_fstab(
        self,
        target_root: Path,
        root_uuid: str,
        boot_uuid: str,
        efi_uuid: str,
        *,
        encryption_enabled: bool = False,
        mapper_name: str = "oyoport-cryptroot",
        luks_uuid: str | None = None,
    ) -> None:
        fstab = target_root / "etc/fstab"
        fstab.parent.mkdir(parents=True, exist_ok=True)
        template = Path(__file__).resolve().parent.parent / "templates/fstab.portable"
        try:
            body = template.read_text(encoding="utf-8")
            root_spec = f"/dev/mapper/{mapper_name}" if encryption_enabled else f"UUID={root_uuid}"
            body = (
                body.replace("{{ROOT_SPEC}}", root_spec)
                .replace("{{BOOT_UUID}}", boot_uuid)
                .replace("{{EFI_UUID}}", efi_uuid)
            )
            fstab.write_text(body, encoding="utf-8")
            crypttab = target_root / "etc/crypttab"
            if encryption_enabled and luks_uuid:
                crypttab.write_text(
                    f"{mapper_name} UUID={luks_uuid} none luks,discard\n",
                    encoding="utf-8",
                )
            elif crypttab.exists():
                crypttab.unlink()
        except OSError as exc:
            raise AppError.translated("E402", "error.fstab_generate_failed", reason=str(exc)) from exc
