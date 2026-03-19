from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.services.optimize_service import OptimizeService


class OptimizeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.service = OptimizeService()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_apply_writes_portable_tmpfs_and_journald_settings(self) -> None:
        self.service.apply(self.root)

        journald = (self.root / "etc/systemd/journald.conf.d/portable.conf").read_text(encoding="utf-8")
        tmpfiles = (self.root / "etc/tmpfiles.d/oyo-portable.conf").read_text(encoding="utf-8")
        apt_conf = (self.root / "etc/apt/apt.conf.d/90oyo-portable-cache").read_text(encoding="utf-8")
        profile = (self.root / "etc/profile.d/oyo-portable-cache.sh").read_text(encoding="utf-8")
        session_script = (self.root / "usr/local/bin/oyo-portable-session-cache").read_text(encoding="utf-8")
        autostart = (
            self.root / "etc/xdg/autostart/oyo-portable-session-cache.desktop"
        ).read_text(encoding="utf-8")

        self.assertIn("Storage=volatile", journald)
        self.assertIn("RuntimeMaxUse=64M", journald)
        self.assertIn("SystemMaxUse=64M", journald)
        self.assertIn("d /tmp 1777 root root -", tmpfiles)
        self.assertIn("d /var/tmp 1777 root root -", tmpfiles)
        self.assertIn("d /var/log 0755 root root -", tmpfiles)
        self.assertIn("d /var/log/journal 0755 root root -", tmpfiles)
        self.assertIn("d /var/log/apt 0755 root root -", tmpfiles)
        self.assertIn("d /var/log/private 0700 root root -", tmpfiles)
        self.assertIn("d /var/log/PackageKit 0755 root root -", tmpfiles)
        self.assertIn('Dir::Cache::archives "/tmp/apt-archives";', apt_conf)
        self.assertIn('export XDG_CACHE_HOME="$XDG_RUNTIME_DIR/oyo-cache"', profile)
        self.assertIn("redirect_home_cache_to_ram", session_script)
        self.assertIn("redirect_chrome_runtime_paths_to_ram", session_script)
        self.assertIn("redirect_browser_runtime_paths_to_ram", session_script)
        self.assertIn("$HOME/.config/google-chrome", session_script)
        self.assertIn("$HOME/.config/chromium", session_script)
        self.assertIn("$HOME/.config/microsoft-edge", session_script)
        self.assertIn("$HOME/.config/microsoft-edge-beta", session_script)
        self.assertIn("$HOME/.config/microsoft-edge-dev", session_script)
        self.assertIn("Exec=/usr/local/bin/oyo-portable-session-cache", autostart)


if __name__ == "__main__":
    unittest.main()
