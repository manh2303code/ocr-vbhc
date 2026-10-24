"""Test-wide setup that keeps the suite away from the installed app's state.

The app stores its theme, panel width and window geometry with
QSettings("OCR VBHC", "OCR_VBHC") / QSettings("OCRApp", "DocumentManagement"),
which on Windows is the registry of the user running the tests — the same
place the installed app reads them from. Tests that switch the theme through
ThemeManager.apply were therefore changing the user's saved theme. Before any
app module is imported, QSettings is replaced by a subclass that sends every
organisation/application store to an INI file in a per-run temporary folder.
"""
import os
import tempfile

import PyQt5.QtCore as QtCore

_SETTINGS_DIR = tempfile.mkdtemp(prefix="ocr-vbhc-test-settings-")
_RealQSettings = QtCore.QSettings


class _TestSettings(_RealQSettings):
    """QSettings(org, app) and QSettings(scope, org, app) go to a temp INI
    file; an explicit (file name, format) or the parent-only form is kept."""

    def __init__(self, *args, **kwargs):
        names = [arg for arg in args if isinstance(arg, str)]
        explicit_file = len(args) >= 2 and isinstance(args[0], str) and isinstance(
            args[1], _RealQSettings.Format)
        if names and not explicit_file:
            path = os.path.join(_SETTINGS_DIR, "_".join(names) + ".ini")
            super().__init__(path, _RealQSettings.IniFormat)
        else:
            super().__init__(*args, **kwargs)


QtCore.QSettings = _TestSettings
