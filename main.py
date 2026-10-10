#!/usr/bin/env python
"""Offline OCR VBHC desktop entry point."""
import logging
import os
import sys
from pathlib import Path

os.environ.update({
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
})

# Import before QApplication so Qt WebEngine can initialize its process model.
from startup_controller import AppController

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from font_loader import load_application_fonts
from logging_config import configure_logging


logger = logging.getLogger("OCRApp")
ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("OCR VBHC")
    app.setApplicationVersion("2.0.0")
    app.setOrganizationName("OCR VBHC")
    load_application_fonts(app)
    app.setWindowIcon(QIcon(str(ASSETS_DIR / "app_icon.ico")))

    controller = AppController(app)
    app._controller = controller
    controller.start()
    return app.exec_()


if __name__ == "__main__":
    configure_logging()
    try:
        sys.exit(main())
    except Exception:
        logger.exception("Application startup failed")
        raise
