from __future__ import annotations

from pathlib import Path

from src.core.errors import AppError

FIRSTBOOT_DONE_PATH = "/var/lib/oyo-portable/firstboot.done"


class FirstbootService:
    def install(self, target_root: Path) -> None:
        try:
            script = target_root / "usr/local/sbin/oyo-firstboot"
            unit = target_root / "etc/systemd/system/oyo-firstboot.service"
            script.parent.mkdir(parents=True, exist_ok=True)
            unit.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(
                "#!/bin/bash\nset -euo pipefail\n"
                "mkdir -p /var/lib/oyo-portable\n"
                f"[ -f {FIRSTBOOT_DONE_PATH} ] && exit 0\n"
                "systemd-machine-id-setup\n"
                "rm -f /etc/ssh/ssh_host_*\n"
                "dpkg-reconfigure openssh-server || true\n"
                f"touch {FIRSTBOOT_DONE_PATH}\n"
                "systemctl disable oyo-firstboot.service\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
            unit.write_text(
                "[Unit]\nDescription=OYO Portable Firstboot\nAfter=network.target\n"
                "[Service]\nType=oneshot\nExecStart=/usr/local/sbin/oyo-firstboot\n"
                "[Install]\nWantedBy=multi-user.target\n",
                encoding="utf-8",
            )
            wants = target_root / "etc/systemd/system/multi-user.target.wants"
            wants.mkdir(parents=True, exist_ok=True)
            link = wants / "oyo-firstboot.service"
            if not link.exists():
                link.symlink_to("/etc/systemd/system/oyo-firstboot.service")
        except Exception as exc:
            raise AppError("E601", f"firstboot 準備失敗: {exc}") from exc
