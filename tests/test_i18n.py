from __future__ import annotations

import unittest

from src.core.errors import AppError, set_error_language
from src.gui.i18n import build_translator, normalize_language


class I18nTests(unittest.TestCase):
    def test_normalize_language_defaults_to_japanese(self) -> None:
        self.assertEqual(normalize_language(None), "ja")
        self.assertEqual(normalize_language(""), "ja")

    def test_normalize_language_maps_supported_prefix(self) -> None:
        self.assertEqual(normalize_language("ja_JP"), "ja")
        self.assertEqual(normalize_language("en-US"), "en")

    def test_normalize_language_falls_back_to_english(self) -> None:
        self.assertEqual(normalize_language("fr_FR"), "en")

    def test_translator_returns_english_message(self) -> None:
        translator = build_translator("en_US")
        self.assertEqual(translator("button.reload"), "Reload")
        self.assertEqual(translator("security.show_password"), "Show password")

    def test_app_error_uses_current_language(self) -> None:
        set_error_language("en")
        error = AppError.translated("E201", "error.target_required")
        self.assertEqual(str(error), "E201: Specify a target device.")


if __name__ == "__main__":
    unittest.main()
