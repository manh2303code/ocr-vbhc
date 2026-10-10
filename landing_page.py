"""Hybrid landing window: local HTML/GSAP with a native Qt fallback."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PyQt5.QtCore import QObject, QSettings, Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget,
)

try:
    from PyQt5.QtWebChannel import QWebChannel
    from PyQt5.QtWebEngineCore import QWebEngineUrlRequestInterceptor
    from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineSettings, QWebEngineView
    WEBENGINE_AVAILABLE = True
except ImportError:  # The native fallback keeps damaged portable builds usable.
    WEBENGINE_AVAILABLE = False
    QWebEnginePage = object


ROOT = Path(__file__).resolve().parent
LANDING_DIR = ROOT / "frontend" / "landing"
logger = logging.getLogger("OCRApp.Landing")
ALLOWED_ACTIONS = {
    "open_file", "open_files", "import_folder", "open_library",
    "open_chat", "open_recent",
}


class LandingBridge(QObject):
    # Inbound: raised when the page asks for something, consumed by Python.
    action_requested = pyqtSignal(str, object)
    # Outbound: emitted for the page to react to. Declared so a test can fail
    # when one is added and never listened for — startupReady sat unconnected,
    # leaving the progress overlay to be hidden only on failure.
    PAGE_SIGNALS = ("action_rejected", "startupProgress", "startupReady", "startupFailed")
    action_rejected = pyqtSignal(str)
    startupProgress = pyqtSignal(int, str)
    startupReady = pyqtSignal()
    startupFailed = pyqtSignal(str)

    def __init__(self, snapshot_provider, parent=None):
        super().__init__(parent)
        self._snapshot_provider = snapshot_provider

    @pyqtSlot(result=str)
    def getSnapshot(self):
        return json.dumps(self._snapshot_provider(), ensure_ascii=False)

    @pyqtSlot(str, str)
    def requestAction(self, action, payload="{}"):
        if action not in ALLOWED_ACTIONS:
            self.action_rejected.emit("Tác vụ không được hỗ trợ")
            return
        try:
            parsed = json.loads(payload or "{}")
        except (TypeError, ValueError):
            self.action_rejected.emit("Dữ liệu tác vụ không hợp lệ")
            return
        if not isinstance(parsed, dict):
            self.action_rejected.emit("Dữ liệu tác vụ không hợp lệ")
            return
        if action == "open_recent":
            doc_id = parsed.get("docId")
            if isinstance(doc_id, bool) or not isinstance(doc_id, int) or doc_id <= 0:
                self.action_rejected.emit("Văn bản không hợp lệ")
                return
        self.action_requested.emit(action, parsed)


if WEBENGINE_AVAILABLE:
    class _LocalOnlyInterceptor(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme().lower() not in ("file", "qrc", "data"):
                info.block(True)


    class _LocalPage(QWebEnginePage):
        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            return url.scheme().lower() in ("file", "qrc", "data", "about")

        def createWindow(self, _window_type):
            return None

        def javaScriptConsoleMessage(self, level, message, line, source):
            logger.debug("Landing JS [%s:%s] %s", source, line, message)


class LandingWindow(QMainWindow):
    action_requested = pyqtSignal(str, object)

    def __init__(self, snapshot_provider, parent=None, force_native=False):
        super().__init__(parent)
        self.setWindowTitle("OCR VBHC")
        self.setMinimumSize(1024, 720)
        self._bridge = LandingBridge(snapshot_provider, self)
        self._bridge.action_requested.connect(self.action_requested)
        self._bridge.action_rejected.connect(self._show_error)
        self._web_view = None
        self._fallback = None
        self._build_shell(force_native)

    @property
    def uses_webengine(self):
        return self._web_view is not None

    def _build_shell(self, force_native):
        if WEBENGINE_AVAILABLE and not force_native:
            try:
                self._build_web_view()
                return
            except Exception:
                pass
        self._show_fallback()

    def _build_web_view(self):
        view = QWebEngineView(self)
        page = _LocalPage(view)
        profile = page.profile()
        self._interceptor = _LocalOnlyInterceptor(profile)
        profile.setRequestInterceptor(self._interceptor)
        profile.setPersistentCookiesPolicy(profile.NoPersistentCookies)
        profile.setHttpCacheType(profile.MemoryHttpCache)
        profile.downloadRequested.connect(lambda item: item.cancel())
        view.setPage(page)

        for attribute in (
            QWebEngineSettings.PluginsEnabled,
            QWebEngineSettings.FullScreenSupportEnabled,
            QWebEngineSettings.ScreenCaptureEnabled,
            QWebEngineSettings.LocalContentCanAccessRemoteUrls,
        ):
            view.settings().setAttribute(attribute, False)
        view.settings().setAttribute(QWebEngineSettings.JavascriptEnabled, True)

        channel = QWebChannel(page)
        channel.registerObject("landingBridge", self._bridge)
        page.setWebChannel(channel)
        self._channel = channel
        view.loadFinished.connect(self._on_load_finished)
        view.renderProcessTerminated.connect(
            lambda *_args: self._show_fallback("Bộ dựng giao diện đã dừng; chuyển sang chế độ an toàn.")
        )
        self._web_view = view
        self.setCentralWidget(view)
        view.setUrl(QUrl.fromLocalFile(str(LANDING_DIR / "index.html")))

    def _on_load_finished(self, ok):
        if not ok:
            self._show_fallback("Không tải được giao diện mới; chuyển sang chế độ an toàn.")

    def _show_fallback(self, message=""):
        if self._web_view is not None:
            self._web_view.deleteLater()
            self._web_view = None
        fallback = _NativeLanding(self._bridge, message, self)
        self._fallback = fallback
        self.setCentralWidget(fallback)

    def set_progress(self, percent, message):
        self._bridge.startupProgress.emit(int(percent), str(message))
        if self._fallback:
            self._fallback.set_progress(percent, message)

    def set_ready(self):
        self._bridge.startupReady.emit()

    def set_failed(self, message):
        self._bridge.startupFailed.emit(str(message))
        self._show_error(message)

    def _show_error(self, message):
        if self._fallback:
            self._fallback.set_error(message)


class _NativeLanding(QWidget):
    def __init__(self, bridge, message="", parent=None):
        super().__init__(parent)
        self._bridge = bridge
        self.setObjectName("nativeLanding")
        self.setStyleSheet(
            "#nativeLanding{background:#F5F1E8;color:#17231B;}"
            "QLabel{color:#17231B;}"
            "QPushButton{background:#FFFEFA;color:#24432D;border:1px solid #C9C2B3;"
            "border-radius:10px;padding:14px 18px;text-align:left;min-height:36px;}"
            "QPushButton:hover{border-color:#846314;background:#F9F5E9;}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(72, 48, 72, 48)
        root.setSpacing(18)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(12)
        logo = QLabel()
        logo.setObjectName("brandLogo")
        logo.setFixedSize(48, 48)
        logo.setPixmap(QPixmap(str(ROOT / "assets" / "app_icon_256.png")).scaled(
            48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))
        brand = QLabel("OCR VBHC\nHỆ THỐNG SỐ HÓA VĂN BẢN")
        brand.setStyleSheet("font-size:12pt;font-weight:600;color:#24432D;")
        brand_row.addWidget(logo)
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        root.addLayout(brand_row)
        root.addStretch(1)
        title = QLabel("Bắt đầu với văn bản hành chính")
        title.setStyleSheet("font-size:26pt;font-weight:700;")
        root.addWidget(title)
        root.addWidget(QLabel("Chọn một tác vụ để số hóa, tra cứu hoặc hỏi đáp văn bản."))
        row = QHBoxLayout()
        actions = (
            ("Quét một văn bản", "open_file"),
            ("Thêm nhiều tệp", "open_files"),
            ("Nhập thư mục", "import_folder"),
            ("Mở kho văn bản", "open_library"),
            ("Hỏi trợ lý pháp luật", "open_chat"),
        )
        for label, action in actions:
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, current=action: bridge.requestAction(current, "{}")
            )
            row.addWidget(button)
        root.addLayout(row)
        self._status = QLabel(message or "Sẵn sàng")
        self._status.setStyleSheet("color:#5C665F;padding-top:16px;")
        root.addWidget(self._status)
        root.addStretch(2)

    def set_progress(self, percent, message):
        self._status.setText("{}%  ·  {}".format(int(percent), message))

    def set_error(self, message):
        self._status.setStyleSheet("color:#B3263E;padding-top:16px;")
        self._status.setText(str(message))
