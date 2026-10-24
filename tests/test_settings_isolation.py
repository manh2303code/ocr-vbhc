"""The suite must never write the installed app's settings (see conftest.py)."""
import tempfile
import unittest

from PyQt5.QtCore import QSettings

import theme_manager_v2


class SettingsIsolationTests(unittest.TestCase):
    def test_app_settings_go_to_a_temporary_ini_file(self):
        for org, app in (("OCR VBHC", "OCR_VBHC"), ("OCRApp", "DocumentManagement")):
            path = QSettings(org, app).fileName()
            self.assertTrue(path.lower().endswith(".ini"), path)
            self.assertIn(tempfile.gettempdir().lower().replace("\\", "/"),
                          path.lower().replace("\\", "/"))

    def test_the_theme_manager_persists_into_the_test_store(self):
        self.assertIs(theme_manager_v2.QSettings, QSettings)
        theme_manager_v2.ThemeManager.instance().save_persisted("dark")
        self.assertEqual("dark", QSettings("OCR VBHC", "OCR_VBHC").value("theme"))


if __name__ == "__main__":
    unittest.main()
