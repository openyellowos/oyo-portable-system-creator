from __future__ import annotations

from pathlib import Path


class OptimizeService:
    def apply(self, target_root: Path) -> None:
        (target_root / "etc/systemd/journald.conf.d").mkdir(parents=True, exist_ok=True)
        (target_root / "etc/systemd/journald.conf.d/portable.conf").write_text("[Journal]\nStorage=volatile\n", encoding="utf-8")
        (target_root / "etc/tmpfiles.d").mkdir(parents=True, exist_ok=True)
        (target_root / "etc/tmpfiles.d/oyo-portable.conf").write_text("d /tmp 1777 root root -\nd /var/tmp 1777 root root -\n", encoding="utf-8")
