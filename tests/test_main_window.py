from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
