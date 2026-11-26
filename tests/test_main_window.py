from __future__ import annotations

import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QProgressBar
from PyQt6.QtGui import QCloseEvent

from src.gui.main_window import CreateWorker, MainWindow
from src.gui.i18n import build_translator


class MainWindowPasswordValidationTests(unittest.TestCase):
    def test_validate_luks_password_accepts_ascii_printable(self) -> None:
        MainWindow._validate_luks_password("Abcd1234!_-")

    def test_validate_luks_password_rejects_surrounding_whitespace(self) -> None:
        with self.assertRaisesRegex(ValueError, "surrounding_whitespace"):
            MainWindow._validate_luks_password(" Abcd1234")

    def test_validate_luks_password_rejects_non_ascii(self) -> None:
        with self.assertRaisesRegex(ValueError, "non_ascii"):
            MainWindow._validate_luks_password("あいうえお")


class MainWindowDeviceSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        with patch.object(MainWindow, "reload_devices", autospec=True):
            self.window = MainWindow(language="ja")

    def tearDown(self) -> None:
        self.window.close()

    def test_on_devices_loaded_selects_first_device(self) -> None:
        devices = [{"name": "sdb", "path": "/dev/sdb", "size": 8 * 1024**3, "vendor": "USB", "model": "Memory"}]

        self.window._on_devices_loaded(devices)

        self.assertEqual(self.window.device_combo.count(), 1)
        self.assertEqual(self.window.device_combo.currentIndex(), 0)
        self.assertEqual(self.window._selected_device(), "/dev/sdb")

    def test_on_devices_loaded_shows_empty_message_only_when_no_devices(self) -> None:
        self.window._on_devices_loaded([])

        self.assertEqual(self.window.device_combo.count(), 1)
        self.assertEqual(self.window.device_combo.currentText(), self.window.t("status.no_devices"))
        self.assertIsNone(self.window._selected_device())

    def test_on_progress_updates_bar_without_appending_duplicate_log(self) -> None:
        self.window._append_log("existing")

        self.window._on_progress(20, "パーティションを作成")

        self.assertEqual(self.window.progress_bar.value(), 20)
        self.assertEqual(self.window.status_label.text(), "パーティションを作成")
        self.assertEqual(self.window.log_view.toPlainText().splitlines(), ["existing"])

    def test_copy_progress_uses_existing_status_label_and_single_progress_bar(self) -> None:
        self.window._on_progress(48, "システムをコピー (1/2) — コピー進捗 38%")

        self.assertEqual(self.window.progress_bar.value(), 48)
        self.assertEqual(self.window.status_label.text(), "システムをコピー (1/2) — コピー進捗 38%")
        self.assertEqual(len(self.window.findChildren(QProgressBar)), 1)
        self.assertEqual(self.window.minimumSize().width(), 920)
        self.assertEqual(self.window.minimumSize().height(), 620)

    def test_copy_progress_text_is_translated_in_japanese_and_english(self) -> None:
        ja = build_translator("ja")
        en = build_translator("en")

        self.assertIn("コピー進捗 38%", ja("workflow.copy.progress", step=ja("workflow.copy.first"), progress=38))
        self.assertIn("38% copied", en("workflow.copy.progress", step=en("workflow.copy.first"), progress=38))
        self.assertIn("処理中", ja("workflow.copy.estimate_reached", step=ja("workflow.copy.first")))
        self.assertIn("continuing", en("workflow.copy.estimate_reached", step=en("workflow.copy.first")))
        self.assertIn("コピー処理中", ja("workflow.copy.processing", step=ja("workflow.copy.first")))
        self.assertIn("rsync is working", en("workflow.copy.processing", step=en("workflow.copy.first")))
        self.assertIn("ファイル確認中", ja("workflow.copy.verifying", step=ja("workflow.copy.second")))
        self.assertIn("checking files", en("workflow.copy.verifying", step=en("workflow.copy.second")))

    def test_close_during_create_requests_safe_cancel_and_defers_close(self) -> None:
        class RunningThread:
            def isRunning(self) -> bool:
                return True

        worker = CreateWorker("/dev/sdz", "ja")
        self.window.worker = worker
        self.window.worker_thread = RunningThread()  # type: ignore[assignment]
        event = QCloseEvent()

        self.window.closeEvent(event)

        self.assertTrue(worker.cancel_event.is_set())
        self.assertFalse(event.isAccepted())
        self.assertTrue(self.window._close_when_worker_stops)
        self.window.worker_thread = None
        self.window.worker = None


if __name__ == "__main__":
    unittest.main()
