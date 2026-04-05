from __future__ import annotations

import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from src.gui.main_window import MainWindow


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


if __name__ == "__main__":
    unittest.main()
