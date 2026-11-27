from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.errors import AppError
from src.infra.command_runner import CommandRunner
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

    def test_estimate_uses_c_locale_for_create_and_backup(self) -> None:
        class Runner:
            def __init__(self) -> None:
                self.calls = []

            def run(self, command, *, check, env_overrides):
                self.calls.append((command, check, env_overrides))
                return type("Result", (), {
                    "returncode": 0,
                    "stdout": "total transferred file size: 1,234 bytes\n",
                    "stderr": "",
                })()

        runner = Runner()
        service = CopyService(runner)  # type: ignore[arg-type]
        for mode in ("create", "backup"):
            self.assertEqual(service.estimate_copy_bytes("/source", mode), 1234)
        self.assertEqual(len(runner.calls), 2)
        for command, check, env_overrides in runner.calls:
            self.assertIn("--dry-run", command)
            self.assertIn("--stats", command)
            self.assertFalse(check)
            self.assertEqual(env_overrides, {"LC_ALL": "C"})

    def test_stats_parser_accepts_exact_integer_and_well_formed_comma_groups(self) -> None:
        samples = {
            "total transferred file size: 5864375238 bytes\n": 5864375238,
            "total transferred file size: 5,864,375,238 bytes\n": 5864375238,
            "total transferred file size: 0 bytes\n": 0,
        }
        for stats, expected in samples.items():
            with self.subTest(stats=stats):
                self.assertEqual(self.service._parse_total_transferred_file_size(stats, ""), expected)

    def test_stats_parser_reads_stats_from_stdout_or_stderr(self) -> None:
        stats = "total transferred file size: 1,234 bytes\n"
        self.assertEqual(self.service._parse_total_transferred_file_size(stats, ""), 1234)
        self.assertEqual(self.service._parse_total_transferred_file_size("", stats), 1234)
        self.assertEqual(
            self.service._parse_total_transferred_file_size("rsync header\n", stats),
            1234,
        )

    def test_stats_parser_fails_closed_for_missing_or_malformed_values(self) -> None:
        malformed = (
            ("", ""),
            ("some other rsync output\n", ""),
            ("total transferred file size: 12,34 bytes\n", ""),
            ("total transferred file size: 1.234 bytes\n", ""),
            ("total transferred file size: -1 bytes\n", ""),
            ("total transferred file size: 1,234x bytes\n", ""),
            ("total transferred file size: 1 bytes\ntotal transferred file size: 2 bytes\n", ""),
        )
        for stdout, stderr in malformed:
            with self.subTest(stdout=stdout, stderr=stderr):
                with self.assertRaises(AppError) as raised:
                    self.service._parse_total_transferred_file_size(stdout, stderr)
                self.assertEqual(raised.exception.code, "E401")

    def test_estimate_fails_closed_when_rsync_dry_run_fails(self) -> None:
        class Runner:
            def run(self, command, *, check, env_overrides):
                return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "failed"})()

        with self.assertRaises(AppError) as raised:
            CopyService(Runner()).estimate_copy_bytes("/source", "create")  # type: ignore[arg-type]
        self.assertEqual(raised.exception.code, "E401")

    def test_real_rsync_estimate_is_stable_under_parent_locale_variations(self) -> None:
        import shutil

        import os

        if shutil.which("rsync") is None:
            self.skipTest("rsync is not installed")
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source"
            source.mkdir()
            payload = source / "payload.bin"
            payload.write_bytes(b"x" * (5 * 1024 * 1024 + 123))
            expected_size = payload.stat().st_size
            service = CopyService(CommandRunner(type("Logger", (), {"debug": lambda *_: None})()))
            parent_envs = (
                {"LC_ALL": "C"},
                {"LANG": "ja_JP.UTF-8"},
                {"LANG": "en_US.UTF-8"},
                {"LANG": "de_DE.UTF-8"},
                {"LANG": "fr_FR.UTF-8"},
                {"LANG": "ja_JP.UTF-8", "LC_NUMERIC": "de_DE.UTF-8", "LC_MESSAGES": "fr_FR.UTF-8"},
            )
            estimates = []
            for parent_env in parent_envs:
                with self.subTest(parent_env=parent_env), patch.dict(os.environ, parent_env, clear=True):
                    estimates.append(service.estimate_copy_bytes(str(source), "backup"))

        self.assertEqual(estimates, [expected_size] * len(parent_envs))

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

    def test_progress_parser_emits_unchanged_bytes_as_activity_sample(self) -> None:
        progress: list[int] = []
        parser = RsyncProgressParser(progress.append, minimum_interval=0.25)

        with patch("src.services.copy_service.time.monotonic", side_effect=[10.0, 10.5]):
            parser.feed("stderr", "500,000 50% 1.0MB/s 0:05\r")
            parser.feed("stderr", "500,000 50% 1.0MB/s 0:05\r")

        self.assertEqual(progress, [500000, 500000])

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
