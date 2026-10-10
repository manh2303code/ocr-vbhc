import unittest
from pathlib import Path
from unittest.mock import patch

import settings_dialog


ROOT = Path(__file__).resolve().parents[1]


class _Font:
    def __init__(self):
        self.point_size = None

    def setPointSize(self, value):
        self.point_size = value


class _App:
    def __init__(self):
        self.value = _Font()
        self.applied = None

    def font(self):
        return self.value

    def setFont(self, font):
        self.applied = font


class _StatusBar:
    def setVisible(self, visible):
        self.visible = visible


class RuntimeSettingsTests(unittest.TestCase):
    def test_ui_preferences_apply_font_and_status_visibility(self):
        app, status = _App(), _StatusBar()
        values = {"font_size": 13, "ui_show_status_bar": False}

        with patch.object(settings_dialog, "get_setting", side_effect=values.get):
            settings_dialog.apply_ui_settings(app, status)

        self.assertEqual(13, app.applied.point_size)
        self.assertFalse(status.visible)

    def test_main_window_starts_autosave_and_honors_keepalive_setting(self):
        source = (ROOT / "main_window_1.py").read_text(encoding="utf-8-sig")

        self.assertIn("self.autosave_timer.start()", source)
        self.assertIn('get_setting("ocr_gpu_keepalive")', source)


if __name__ == "__main__":
    unittest.main()
