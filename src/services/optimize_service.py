from __future__ import annotations

from pathlib import Path


class OptimizeService:
    def apply(self, target_root: Path) -> None:
        self._setup_journald(target_root)
        self._setup_tmpfs(target_root)
        self._setup_profile_cache_redirect(target_root)
        self._setup_session_cache_redirect(target_root)
        self._setup_session_autostart(target_root)
        self._disable_gnome_software_autostart(target_root)
        self._setup_apt_cache_redirect(target_root)

    def _setup_journald(self, target_root: Path) -> None:
        conf_dir = target_root / "etc/systemd/journald.conf.d"
        conf_dir.mkdir(parents=True, exist_ok=True)
        (conf_dir / "portable.conf").write_text(
            "[Journal]\n"
            "Storage=volatile\n"
            "RuntimeMaxUse=64M\n"
            "SystemMaxUse=64M\n",
            encoding="utf-8",
        )

    def _setup_tmpfs(self, target_root: Path) -> None:
        tmpfiles_dir = target_root / "etc/tmpfiles.d"
        tmpfiles_dir.mkdir(parents=True, exist_ok=True)
        template = Path(__file__).resolve().parent.parent / "templates/tmpfs.conf"
        base = template.read_text(encoding="utf-8")
        (tmpfiles_dir / "oyo-portable.conf").write_text(
            base
            + "d /tmp/apt-archives 0755 root root -\n"
            + "d /tmp/apt-archives/partial 0700 root root -\n"
            + "d /var/log 0755 root root -\n"
            + "d /var/log/journal 0755 root root -\n"
            + "d /var/log/apt 0755 root root -\n"
            + "d /var/log/private 0700 root root -\n"
            + "d /var/log/PackageKit 0755 root root -\n"
            + "d /var/cache/apt/archives 0755 root root -\n",
            encoding="utf-8",
        )

    def _setup_profile_cache_redirect(self, target_root: Path) -> None:
        profile = target_root / "etc/profile.d/oyo-portable-cache.sh"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(
            "#!/bin/bash\n"
            "# Keep user cache in RAM on portable systems\n"
            "if [ -n \"${XDG_RUNTIME_DIR:-}\" ]; then\n"
            "    export XDG_CACHE_HOME=\"$XDG_RUNTIME_DIR/oyo-cache\"\n"
            "    mkdir -p \"$XDG_CACHE_HOME\"\n"
            "    chmod 700 \"$XDG_CACHE_HOME\" 2>/dev/null || true\n"
            "fi\n"
            "export MOZ_LEGACY_PROFILES=1\n"
            "export MOZ_DISABLE_CONTENT_SANDBOX=0\n",
            encoding="utf-8",
        )
        profile.chmod(0o755)

    def _setup_session_cache_redirect(self, target_root: Path) -> None:
        script = target_root / "usr/local/bin/oyo-portable-session-cache"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "#!/bin/bash\n"
            "set -eu\n\n"
            "redirect_path_to_runtime_dir() {\n"
            "    local src=\"$1\"\n"
            "    local dst=\"$2\"\n\n"
            "    mkdir -p \"$(dirname \"$src\")\"\n"
            "    mkdir -p \"$dst\"\n"
            "    chmod 700 \"$dst\" 2>/dev/null || true\n\n"
            "    if [ -L \"$src\" ]; then\n"
            "        local current target\n"
            "        current=\"$(readlink -f \"$src\" 2>/dev/null || true)\"\n"
            "        target=\"$(readlink -f \"$dst\" 2>/dev/null || true)\"\n"
            "        if [ -n \"$current\" ] && [ -n \"$target\" ] && [ \"$current\" = \"$target\" ]; then\n"
            "            return 0\n"
            "        fi\n"
            "        rm -f \"$src\"\n"
            "    elif [ -d \"$src\" ]; then\n"
            "        rm -rf \"$src\"\n"
            "    elif [ -e \"$src\" ]; then\n"
            "        rm -f \"$src\"\n"
            "    fi\n\n"
            "    ln -sfn \"$dst\" \"$src\"\n"
            "}\n\n"
            "redirect_home_cache_to_ram() {\n"
            "    if [ -z \"${HOME:-}\" ] || [ -z \"${XDG_RUNTIME_DIR:-}\" ]; then\n"
            "        return 0\n"
            "    fi\n\n"
            "    local ram_cache=\"$XDG_RUNTIME_DIR/oyo-cache\"\n"
            "    local home_cache=\"$HOME/.cache\"\n\n"
            "    mkdir -p \"$ram_cache\"\n"
            "    chmod 700 \"$ram_cache\" 2>/dev/null || true\n"
            "    mkdir -p \"$HOME\"\n\n"
            "    if [ -L \"$home_cache\" ]; then\n"
            "        local current target\n"
            "        current=\"$(readlink -f \"$home_cache\" 2>/dev/null || true)\"\n"
            "        target=\"$(readlink -f \"$ram_cache\" 2>/dev/null || true)\"\n"
            "        if [ -n \"$current\" ] && [ -n \"$target\" ] && [ \"$current\" = \"$target\" ]; then\n"
            "            return 0\n"
            "        fi\n"
            "        rm -f \"$home_cache\"\n"
            "    elif [ -d \"$home_cache\" ]; then\n"
            "        rm -rf \"$home_cache\"\n"
            "    elif [ -e \"$home_cache\" ]; then\n"
            "        rm -f \"$home_cache\"\n"
            "    fi\n\n"
            "    ln -sfn \"$ram_cache\" \"$home_cache\"\n"
            "}\n\n"
            "redirect_chrome_runtime_paths_to_ram() {\n"
            "    if [ -z \"${HOME:-}\" ] || [ -z \"${XDG_RUNTIME_DIR:-}\" ]; then\n"
            "        return 0\n"
            "    fi\n\n"
            "    redirect_browser_runtime_paths_to_ram \\\n"
            "        \"$HOME/.config/google-chrome\" \\\n"
            "        \"$XDG_RUNTIME_DIR/oyo-google-chrome\"\n\n"
            "    redirect_browser_runtime_paths_to_ram \\\n"
            "        \"$HOME/.config/chromium\" \\\n"
            "        \"$XDG_RUNTIME_DIR/oyo-chromium\"\n\n"
            "    redirect_browser_runtime_paths_to_ram \\\n"
            "        \"$HOME/.config/microsoft-edge\" \\\n"
            "        \"$XDG_RUNTIME_DIR/oyo-microsoft-edge\"\n\n"
            "    redirect_browser_runtime_paths_to_ram \\\n"
            "        \"$HOME/.config/microsoft-edge-beta\" \\\n"
            "        \"$XDG_RUNTIME_DIR/oyo-microsoft-edge-beta\"\n\n"
            "    redirect_browser_runtime_paths_to_ram \\\n"
            "        \"$HOME/.config/microsoft-edge-dev\" \\\n"
            "        \"$XDG_RUNTIME_DIR/oyo-microsoft-edge-dev\"\n"
            "}\n\n"
            "redirect_browser_runtime_paths_to_ram() {\n"
            "    local browser_config_dir=\"$1\"\n"
            "    local browser_runtime_base=\"$2\"\n\n"
            "    if [ ! -d \"$browser_config_dir\" ] && [ ! -L \"$browser_config_dir\" ]; then\n"
            "        mkdir -p \"$browser_config_dir\"\n"
            "    fi\n\n"
            "    mkdir -p \"$browser_runtime_base\"\n"
            "    chmod 700 \"$browser_runtime_base\" 2>/dev/null || true\n\n"
            "    redirect_path_to_runtime_dir \\\n"
            "        \"$browser_config_dir/Service Worker\" \\\n"
            "        \"$browser_runtime_base/Service Worker\"\n\n"
            "    redirect_path_to_runtime_dir \\\n"
            "        \"$browser_config_dir/Code Cache\" \\\n"
            "        \"$browser_runtime_base/Code Cache\"\n\n"
            "    redirect_path_to_runtime_dir \\\n"
            "        \"$browser_config_dir/GPUCache\" \\\n"
            "        \"$browser_runtime_base/GPUCache\"\n"
            "}\n\n"
            "redirect_home_cache_to_ram\n"
            "redirect_chrome_runtime_paths_to_ram\n",
            encoding="utf-8",
        )
        script.chmod(0o755)

    def _setup_session_autostart(self, target_root: Path) -> None:
        desktop = target_root / "etc/xdg/autostart/oyo-portable-session-cache.desktop"
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=oYo Portable Session Cache\n"
            "Comment=Move volatile browser caches to RAM on login\n"
            "Exec=/usr/local/bin/oyo-portable-session-cache\n"
            "Terminal=false\n"
            "OnlyShowIn=GNOME;XFCE;KDE;LXQt;MATE;Cinnamon;\n"
            "X-GNOME-Autostart-enabled=true\n",
            encoding="utf-8",
        )

    def _disable_gnome_software_autostart(self, target_root: Path) -> None:
        desktop = target_root / "etc/xdg/autostart/org.gnome.Software.desktop"
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text(
            "[Desktop Entry]\n"
            "Hidden=true\n"
            "X-GNOME-Autostart-enabled=false\n",
            encoding="utf-8",
        )

    def _setup_apt_cache_redirect(self, target_root: Path) -> None:
        apt_conf = target_root / "etc/apt/apt.conf.d/90oyo-portable-cache"
        apt_conf.parent.mkdir(parents=True, exist_ok=True)
        apt_conf.write_text(
            'Dir::Cache::archives "/tmp/apt-archives";\n',
            encoding="utf-8",
        )
