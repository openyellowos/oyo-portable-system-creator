from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.copy_service import CopyService, RsyncProgressParser


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
        self.assertIn("+ /home/*/Downloads", command)
        self.assertIn("+ /home/*/Downloads/", command)
        self.assertIn("- /home/*/Downloads/***", command)
        self.assertIn("+ /home/*/Documents", command)
        self.assertIn("+ /home/*/Documents/", command)
        self.assertIn("- /home/*/Documents/***", command)
        self.assertIn("+ /home/*/Desktop", command)
        self.assertIn("+ /home/*/Desktop/", command)
        self.assertIn("- /home/*/Desktop/***", command)
        self.assertIn("+ /home/*/デスクトップ", command)
        self.assertIn("+ /home/*/デスクトップ/", command)
        self.assertIn("- /home/*/デスクトップ/***", command)
        self.assertIn("+ /home/*/Public", command)
        self.assertIn("+ /home/*/Public/", command)
        self.assertIn("- /home/*/Public/***", command)
        self.assertIn("+ /home/*/公開", command)
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

    def test_progress_parser_handles_carriage_returns_and_split_chunks(self) -> None:
        progress: list[int] = []
        parser = RsyncProgressParser(progress.append, minimum_interval=0)

        parser.feed("stderr", "123,456 3")
        parser.feed("stderr", "8% 1.2MB/s 0:04\r987,654 72% 2.1MB/s 0:01\r")

        self.assertEqual(progress, [123456, 987654])

    def test_progress_parser_reads_actual_progress2_cumulative_byte_field(self) -> None:
        progress: list[int] = []
        parser = RsyncProgressParser(progress.append, minimum_interval=0)

        parser.feed(
            "stderr",
            "5,864,112,507 99% 102.88MB/s 0:00:54 (xfr#164783, ir-chk=2436/350354)\r"
            "5,864,375,238 99% 102.37MB/s 0:00:54 (xfr#164797, ir-chk=1010/350757)\r",
        )

        self.assertEqual(progress, [5864112507, 5864375238])

    def test_progress_parser_ignores_invalid_and_non_progress_output(self) -> None:
        progress: list[int] = []
        parser = RsyncProgressParser(progress.append, minimum_interval=0)

        parser.feed(
            "stderr",
            "warning: only 38% available\r123,456B 101% 1.2MB/s\r"
            "123,456B xx% 1.2MB/s\r123,456B 42% 1.2MB/s\r",
        )

        self.assertEqual(progress, [123456])

    def test_progress_parser_emits_throttled_value_when_same_record_repeats(self) -> None:
        progress: list[int] = []
        parser = RsyncProgressParser(progress.append, minimum_interval=0.25)

        with patch("src.services.copy_service.time.monotonic", side_effect=[10.0, 10.1, 10.5]):
            parser.feed("stderr", "1,000B 1% 1.0MB/s 0:10\r")
            parser.feed("stderr", "50,000B 50% 1.0MB/s 0:05\r")
            parser.feed("stderr", "50,000B 50% 1.0MB/s 0:05\r")

        self.assertEqual(progress, [1000, 50000])

    def test_progress_parser_flushes_pending_value_when_stream_finishes(self) -> None:
        progress: list[int] = []
        parser = RsyncProgressParser(progress.append, minimum_interval=0.25)

        with patch("src.services.copy_service.time.monotonic", side_effect=[10.0, 10.1, 10.2]):
            parser.feed("stderr", "1,000B 1% 1.0MB/s 0:10\r")
            parser.feed("stderr", "50,000B 50% 1.0MB/s 0:05\r")
            parser.finish()

        self.assertEqual(progress, [1000, 50000])

    def test_rsync_copy_requests_progress_only_when_callback_is_available(self) -> None:
        class Runner:
            def __init__(self) -> None:
                self.calls: list[tuple[list[str], object]] = []

            def run(self, command: list[str], *, check: bool, output_callback=None):
                self.calls.append((command, output_callback))
                if output_callback is not None:
                    output_callback("stderr", "123,456 38% 1.2MB/s 0:04\r")
                return type("Result", (), {"returncode": 0})()

        runner = Runner()
        self.service.runner = runner  # type: ignore[assignment]
        progress: list[int] = []
        self.service.rsync_copy("/source", Path("/target"), "backup", progress.append)

        self.assertIn("--info=progress2", runner.calls[0][0])
        self.assertEqual(progress, [123456])
        self.service.rsync_copy("/source", Path("/target"), "backup")
        self.assertNotIn("--info=progress2", runner.calls[1][0])
        self.assertIsNone(runner.calls[1][1])

    def test_rsync_copy_continues_without_progress_records(self) -> None:
        class Runner:
            def run(self, command: list[str], *, check: bool, output_callback=None):
                if output_callback is not None:
                    output_callback("stderr", "warning: transfer continues without progress data\n")
                return type("Result", (), {"returncode": 0})()

        self.service.runner = Runner()  # type: ignore[assignment]
        progress: list[int] = []

        self.service.rsync_copy("/source", Path("/target"), "backup", progress.append)

        self.assertEqual(progress, [])

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
