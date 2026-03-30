from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.services.copy_service import CopyService


class DummyRunner:
    pass


class CopyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CopyService(DummyRunner())

    def test_create_mode_uses_home_filter_rules(self) -> None:
        command = self.service._build_rsync_command("/", "/target", mode="create")

        self.assertIn("--filter", command)
        self.assertIn("+ /home/*/Templates/***", command)
        self.assertIn("+ /home/*/テンプレート/***", command)
        self.assertIn("+ /home/*/.*/***", command)
        self.assertIn("+ /home/*/.*", command)
        self.assertIn("+ /home/*/Downloads/", command)
        self.assertIn("- /home/*/Downloads/***", command)
        self.assertIn("+ /home/*/Documents/", command)
        self.assertIn("- /home/*/Documents/***", command)
        self.assertIn("+ /home/*/Desktop/", command)
        self.assertIn("- /home/*/Desktop/***", command)
        self.assertIn("+ /home/*/デスクトップ/", command)
        self.assertIn("- /home/*/デスクトップ/***", command)
        self.assertIn("+ /home/*/Public/", command)
        self.assertIn("- /home/*/Public/***", command)
        self.assertIn("+ /home/*/公開/", command)
        self.assertIn("- /home/*/公開/***", command)
        self.assertIn("- /home/*/*", command)

    def test_create_mode_no_longer_excludes_all_hidden_user_data(self) -> None:
        excludes = self.service._exclude_patterns_for_mode("create")

        self.assertNotIn("/home/*/.mozilla/*", excludes)
        self.assertNotIn("/home/*/.local/share/*", excludes)
        self.assertNotIn("/home/*/.config/google-chrome/*", excludes)
        self.assertNotIn("/opt/google/*", excludes)
        self.assertNotIn("/opt/wine-stable/*", excludes)
        self.assertNotIn("/opt/CSV+/*", excludes)
        self.assertNotIn("/var/lib/flatpak/*", excludes)
        self.assertNotIn("/var/lib/snapd/*", excludes)
        self.assertNotIn("/usr/share/locale/*", excludes)
        self.assertIn("/home/*/.cache/*", excludes)
        self.assertIn("/home/*/.local/share/Trash/*", excludes)

    def test_backup_mode_does_not_use_home_whitelist_filters(self) -> None:
        command = self.service._build_rsync_command("/", "/target", mode="backup")

        self.assertNotIn("--filter", command)

    def test_write_fstab_uses_mapper_when_encryption_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            self.service.write_fstab(
                root,
                "ROOT-UUID",
                "BOOT-UUID",
                "EFI-UUID",
                encryption_enabled=True,
                mapper_name="oyoport-cryptroot",
                luks_uuid="LUKS-UUID",
            )

            fstab = (root / "etc/fstab").read_text(encoding="utf-8")
            crypttab = (root / "etc/crypttab").read_text(encoding="utf-8")

            self.assertIn("/dev/mapper/oyoport-cryptroot / ext4", fstab)
            self.assertIn("UUID=BOOT-UUID /boot ext4", fstab)
            self.assertIn("UUID=EFI-UUID /boot/efi", fstab)
            self.assertEqual(crypttab, "oyoport-cryptroot UUID=LUKS-UUID none luks,discard\n")


if __name__ == "__main__":
    unittest.main()
