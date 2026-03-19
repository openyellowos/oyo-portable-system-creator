from __future__ import annotations

import unittest

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
        self.assertIn("/home/*/.cache/*", excludes)
        self.assertIn("/home/*/.local/share/Trash/*", excludes)

    def test_backup_mode_does_not_use_home_whitelist_filters(self) -> None:
        command = self.service._build_rsync_command("/", "/target", mode="backup")

        self.assertNotIn("--filter", command)


if __name__ == "__main__":
    unittest.main()
