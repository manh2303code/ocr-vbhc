"""Coordinate the lightweight landing window and the existing heavy workspace."""
from __future__ import annotations

import logging
import traceback
from pathlib import Path

from PyQt5.QtCore import QObject, QPropertyAnimation, QSettings, QTimer, pyqtSlot
from PyQt5.QtWidgets import QApplication

from landing_data import LandingSnapshotService
from landing_page import LandingWindow


logger = logging.getLogger("OCRApp.Startup")
ROOT = Path(__file__).resolve().parent


def dispatch_startup_intent(window, action, payload):
    """Route a validated landing action to the already-existing workspace API."""
    routes = {
        "open_file": window.add_file,
        "open_files": window.add_files,
        "import_folder": window.add_folder,
    }
    if action in routes:
        routes[action]()
    elif action == "open_library":
        # The pane may be collapsed from a previous session, so opening the
        # workspace is not enough on its own: this action has to reveal the
        # library, which is the whole reason it exists.
        window.act_toggle_left_panel.setChecked(True)
        window._toggle_left_panel(True)
    elif action == "open_chat":
        window.act_chat.setChecked(True)
        window._toggle_chat_panel(True)
        window.chat_panel.question_input.setFocus()
    elif action == "open_recent":
        doc_id = payload.get("docId") if isinstance(payload, dict) else None
        if isinstance(doc_id, int) and not isinstance(doc_id, bool) and doc_id > 0:
            window.show_document(doc_id)


class AppController(QObject):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.landing = None
        self.main_window = None
        self._main_class = None
        self._pending = None
        self._initializing = False
        self._animation = None
        self._snapshot = LandingSnapshotService(
            ROOT / "database" / "documents.db",
            base_dir=ROOT,
            theme_getter=lambda: QSettings("OCR VBHC", "OCR_VBHC").value(
                "theme", "light", type=str
            ),
        )

    def start(self):
        self.show_landing()

    @pyqtSlot()
    def show_landing(self):
        if self.main_window is not None:
            self.main_window.hide()
        if self.landing is None:
            landing = LandingWindow(self._snapshot.get_snapshot)
            landing.action_requested.connect(self._on_action)
            landing.destroyed.connect(self._landing_destroyed)
            self.landing = landing
        self.landing.showMaximized()
        self.landing.raise_()
        self.landing.activateWindow()

    def _landing_destroyed(self):
        self.landing = None

    def _on_action(self, action, payload):
        if self._initializing:
            return
        self._pending = (action, payload)
        if self.main_window is not None:
            self._present_main()
            return
        self._initializing = True
        self.landing.set_progress(12, "Đang chuẩn bị thư viện giao diện…")
        QTimer.singleShot(40, self._import_workspace)

    def _import_workspace(self):
        try:
            from main_window_1 import MainWindow
            self._main_class = MainWindow
            self.landing.set_progress(38, "Đã tải giao diện nghiệp vụ")
            QTimer.singleShot(40, self._construct_workspace)
        except Exception as exc:
            self._fail(exc)

    def _construct_workspace(self):
        try:
            self.landing.set_progress(65, "Đang khởi tạo OCR và kho tri thức…")
            QApplication.processEvents()
            window = self._main_class()
            self.main_window = window
            if hasattr(window, "home_requested"):
                window.home_requested.connect(self.show_landing)
            self.landing.set_progress(92, "Đang hoàn thiện danh sách văn bản…")
            QTimer.singleShot(90, self._present_main)
        except Exception as exc:
            self._fail(exc)

    def _present_main(self):
        self._initializing = False
        if self.landing is not None:
            self.landing.set_progress(100, "Sẵn sàng")
            self.landing.set_ready()
        window = self.main_window
        window.setWindowOpacity(0.0)
        window.showMaximized()
        window.raise_()
        window.activateWindow()

        animation = QPropertyAnimation(window, b"windowOpacity", self)
        animation.setDuration(260)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.finished.connect(self._finish_transition)
        self._animation = animation
        animation.start()

    def _finish_transition(self):
        if self.landing is not None:
            old = self.landing
            self.landing = None
            old.close()
            old.deleteLater()
        if self._pending:
            action, payload = self._pending
            self._pending = None
            QTimer.singleShot(
                80, lambda: dispatch_startup_intent(self.main_window, action, payload)
            )

    def _fail(self, exc):
        self._initializing = False
        logger.error("Workspace startup failed: %s\n%s", exc, traceback.format_exc())
        if self.main_window is not None:
            self.main_window.deleteLater()
            self.main_window = None
        if self.landing is not None:
            self.landing.set_failed("Không thể mở workspace: {}".format(exc))
