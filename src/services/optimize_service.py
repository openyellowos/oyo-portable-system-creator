from __future__ import annotations

from pathlib import Path


class OptimizeService:
    def apply(self, target_root: Path) -> None:
        self._setup_journald(target_root)
        self._setup_tmpfs(target_root)
        self._setup_profile_cache_redirect(target_root)
        self._setup_apt_cache_redirect(target_root)

    def _setup_journald(self, target_root: Path) -> None:
        conf_dir = target_root / "etc/systemd/journald.conf.d"
        conf_dir.mkdir(parents=True, exist_ok=True)
        (conf_dir / "portable.conf").write_text("[Journal]\nStorage=volatile\n", encoding="utf-8")

    def _setup_tmpfs(self, target_root: Path) -> None:
        tmpfiles_dir = target_root / "etc/tmpfiles.d"
        tmpfiles_dir.mkdir(parents=True, exist_ok=True)
        template = Path(__file__).resolve().parent.parent / "templates/tmpfs.conf"
        base = template.read_text(encoding="utf-8")
        (tmpfiles_dir / "oyo-portable.conf").write_text(
            base + "d /var/cache/apt/archives 0755 root root -\n",
            encoding="utf-8",
        )

    def _setup_profile_cache_redirect(self, target_root: Path) -> None:
        profile = target_root / "etc/profile.d/oyo-portable-cache.sh"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(
            "#!/bin/bash\n"
            "# Keep user/browser cache in RAM on portable systems\n"
            "export XDG_CACHE_HOME=/tmp/${USER}-cache\n"
            "mkdir -p \"$XDG_CACHE_HOME\"\n"
            "export MOZ_LEGACY_PROFILES=1\n"
            "export MOZ_DISABLE_CONTENT_SANDBOX=0\n",
            encoding="utf-8",
        )
        profile.chmod(0o755)

    def _setup_apt_cache_redirect(self, target_root: Path) -> None:
        apt_conf = target_root / "etc/apt/apt.conf.d/90oyo-portable-cache"
        apt_conf.parent.mkdir(parents=True, exist_ok=True)
        apt_conf.write_text(
            'Dir::Cache::archives "/tmp/apt-archives";\n',
            encoding="utf-8",
        )
