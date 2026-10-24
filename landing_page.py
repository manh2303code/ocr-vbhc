"""Hybrid landing window: local HTML with a native Qt fallback."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

from PyQt5.QtCore import QObject, Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QFont, QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
    QPushButton, QShortcut, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from theme_tokens import colors_for
from landing_data import landing_commands

try:  # icons are a nicety; the fallback must open without QtSvg
    from ui_icons import icon as _icon
except ImportError:
    _icon = None

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

    def snapshot(self):
        """The snapshot for Python callers (theme, fallback); never raises."""
        try:
            data = self._snapshot_provider()
        except Exception:  # The home screen must open even on a broken database.
            logger.exception("Landing snapshot failed")
            return {}
        return data if isinstance(data, dict) else {}

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
        # The canvas colour before the HTML paints: no white flash in dark mode.
        theme = self._bridge.snapshot().get("theme")
        page.setBackgroundColor(QColor(colors_for(theme)["bg"]))

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


def describe_stamp(value, today=None):
    """("Hôm nay 09:12" | "Hôm qua 16:40" | "14/10/2026", "14/10/2026 09:12").

    Mirrors describeStamp in landing.js; the snapshot sends local time.
    """
    text = str(value or "")
    try:
        moment = datetime.strptime(text[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        try:
            day = datetime.strptime(text[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return text, text
        return day, day
    day, time = moment.strftime("%d/%m/%Y"), moment.strftime("%H:%M")
    days = ((today or date.today()) - moment.date()).days
    short = {0: "Hôm nay " + time, 1: "Hôm qua " + time}.get(days, day)
    return short, "{} {}".format(day, time)


def _group_digits(value):
    try:
        number = max(0, int(value or 0))
    except (TypeError, ValueError):
        number = 0
    return "{:,}".format(number).replace(",", ".")


# Status line wording, as on the page: label and tooltip per state.
_MODEL_TEXT = {
    "ocr": {
        True: ("OCR sẵn sàng", "Mô hình nhận dạng Chandra đã có trên máy"),
        False: ("Thiếu mô hình OCR",
                "Chưa có mô hình nhận dạng trong thư mục gguf\\chandra,\nnên chưa quét được văn bản"),
        None: ("OCR", ""),
    },
    "chat": {
        True: ("Trợ lý sẵn sàng", "Mô hình trả lời của trợ lý pháp luật đã có trên máy"),
        False: ("Thiếu mô hình trợ lý",
                "Chưa có mô hình trong thư mục gguf\\chat,\nnên trợ lý pháp luật chưa trả lời được"),
        None: ("Trợ lý", ""),
    },
}
_ACTION_ICONS = {
    "open_file": "scan", "open_files": "files", "import_folder": "folder-open", "open_chat": "chat",
}


def _native_stylesheet(c, brand):
    return """
#nativeLanding {{ background: {bg}; }}
#nativeLanding QLabel {{ color: {text}; background: transparent; }}
#nativeLanding QLabel#appName {{ color: {brand}; font-size: 10.5pt; font-weight: 600; }}
#nativeLanding QLabel#registerTitle {{ font-size: 11pt; font-weight: 600; }}
#nativeLanding QLabel#registerEmpty {{ color: {text_secondary}; padding: 8px 16px 20px 16px; }}
#nativeLanding #statusLine QLabel {{ color: {text_secondary}; font-size: 8.5pt; }}
#nativeLanding #statusLine QLabel[state="missing"] {{ color: {danger}; }}
#nativeLanding QPushButton {{
    background: {surface}; color: {text}; border: 1px solid {border_strong};
    border-radius: 4px; padding: 0 14px 0 10px; min-height: 32px; font-weight: 500;
}}
#nativeLanding QPushButton:hover {{ background: {surface_hover}; }}
#nativeLanding QPushButton:pressed {{ background: {surface_pressed}; }}
#nativeLanding QPushButton:focus {{ border-color: {border_focus}; }}
#nativeLanding QPushButton[kind="primary"] {{
    background: {primary}; border-color: {primary}; color: {text_on_primary};
}}
#nativeLanding QPushButton[kind="primary"]:hover {{ background: {primary_hover}; border-color: {primary_hover}; }}
#nativeLanding QPushButton[kind="primary"]:focus {{ border-color: {border_focus}; }}
#nativeLanding QPushButton[kind="ghost"] {{ background: transparent; border-color: transparent; color: {brand}; }}
#nativeLanding QPushButton[kind="ghost"]:hover {{ background: {surface_hover}; }}
#nativeLanding QPushButton[kind="ghost"]:focus {{ border-color: {border_focus}; }}
#register {{ background: {surface}; border: 1px solid {border}; border-radius: 4px; }}
#registerRows {{
    background: {surface}; color: {text}; border: none; outline: 0;
    selection-background-color: {primary_soft}; selection-color: {text};
}}
#registerRows::item {{ padding: 9px 8px 9px 16px; border-bottom: 1px solid {border}; }}
#registerRows::item:hover {{ background: {surface_hover}; }}
#registerRows::item:selected {{ background: {primary_soft}; }}
#registerRows QHeaderView::section {{
    background: {surface}; color: {text_muted}; border: none; border-bottom: 1px solid {border};
    padding: 0 8px 6px 16px; font-size: 8.5pt; font-weight: 500;
}}
QToolTip {{
    background: {surface}; color: {text}; border: 1px solid {border_strong};
    border-radius: 4px; padding: 5px 8px; font-size: 9pt;
}}
""".format(brand=brand, **c)


class _NativeLanding(QWidget):
    """The home page in plain Qt, for a build whose WebEngine is missing or
    whose render process died: the same order as the HTML (brand, one primary
    action, the register of recent documents, the status line), the same
    tokens, both themes. Explanations and shortcuts are tooltips here too."""

    ACTIONS = ("open_file", "open_files", "import_folder", "open_chat")

    def __init__(self, bridge, message="", parent=None):
        super().__init__(parent)
        self._bridge = bridge
        snapshot = bridge.snapshot()
        theme = snapshot.get("theme")
        self._colors = colors = colors_for(theme)
        self._brand_color = colors["primary_muted"] if theme == "dark" else colors["primary"]
        specs = {
            spec.get("action"): spec
            for spec in (snapshot.get("commands") or landing_commands())
        }
        self.setObjectName("nativeLanding")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_native_stylesheet(colors, self._brand_color))

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 0, 32, 8)
        root.setSpacing(0)
        root.addWidget(self._brand())
        root.addStretch(4)
        # Created first so the primary action leads the tab order.
        buttons = [
            self._button(specs.get(action) or {"action": action},
                         "primary" if action == "open_file" else "secondary")
            for action in self.ACTIONS
        ]
        actions = QHBoxLayout()
        actions.setSpacing(8)
        for button in buttons[:3]:
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(buttons[3])
        root.addLayout(actions)
        root.addSpacing(16)
        root.addWidget(self._register(snapshot, specs))
        root.addStretch(5)
        root.addWidget(self._status_line(snapshot, message))

    # ── pieces ──
    def _brand(self):
        bar = QWidget(self)
        bar.setFixedHeight(56)
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        logo = QLabel(bar)
        logo.setObjectName("brandLogo")
        logo.setFixedSize(24, 24)
        pixmap = QPixmap(str(ROOT / "assets" / "app_icon_256.png"))
        if not pixmap.isNull():
            pixmap = pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pixmap.setDevicePixelRatio(2.0)
        logo.setPixmap(pixmap)
        name = QLabel("OCR VBHC", bar)
        name.setObjectName("appName")
        row.addWidget(logo)
        row.addWidget(name)
        row.addStretch(1)
        return bar

    def _button(self, spec, kind):
        action = spec.get("action")
        button = QPushButton(spec.get("label") or action, self)
        button.setProperty("kind", kind)
        button.setCursor(Qt.PointingHandCursor)
        name = _ACTION_ICONS.get(action)
        if name and _icon is not None:
            colour = self._colors["text_on_primary"] if kind == "primary" else self._brand_color
            button.setIcon(_icon(name, 20, colour))
        shortcut = spec.get("shortcut") or ""
        tip = spec.get("hint") or ""
        button.setToolTip("\n".join(part for part in (tip, shortcut) if part))
        if shortcut:
            QShortcut(QKeySequence(shortcut), self, activated=lambda current=action: self._request(current))
        button.clicked.connect(lambda _checked=False, current=action: self._request(current))
        return button

    def _register(self, snapshot, specs):
        sheet = QFrame(self)
        sheet.setObjectName("register")
        layout = QVBoxLayout(sheet)
        layout.setContentsMargins(1, 6, 1, 1)
        layout.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(15, 0, 7, 0)
        title = QLabel("Văn bản gần đây", sheet)
        title.setObjectName("registerTitle")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self._button(specs.get("open_library") or {"action": "open_library"}, "ghost"))
        layout.addLayout(head)
        documents = snapshot.get("recentDocuments") or []
        if not documents:
            empty = QLabel("Kho chưa có văn bản. Văn bản bạn quét sẽ hiện ở đây.", sheet)
            empty.setObjectName("registerEmpty")
            layout.addWidget(empty)
            return sheet
        layout.addWidget(self._rows(sheet, documents))
        return sheet

    def _rows(self, parent, documents):
        c = self._colors
        tree = QTreeWidget(parent)
        tree.setObjectName("registerRows")
        tree.setColumnCount(4)
        tree.setHeaderLabels(["Số, ký hiệu", "Trích yếu", "Cập nhật", "Trang"])
        tree.headerItem().setTextAlignment(3, Qt.AlignRight | Qt.AlignVCenter)
        tree.setRootIsDecorated(False)
        tree.setIndentation(0)
        tree.setUniformRowHeights(True)
        tree.setAllColumnsShowFocus(True)
        tree.setMouseTracking(True)
        tree.setFrameShape(QFrame.NoFrame)
        tree.setSelectionMode(QAbstractItemView.SingleSelection)
        tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tree.setTextElideMode(Qt.ElideRight)
        tree.viewport().setCursor(Qt.PointingHandCursor)
        header = tree.header()
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)
        header.setHighlightSections(False)
        for column, width in ((0, 184), (2, 136), (3, 64)):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            tree.setColumnWidth(column, width)
        header.setSectionResizeMode(1, QHeaderView.Stretch)

        serif = QFont("Times New Roman")
        serif.setPointSizeF(11)
        small = QFont(self.font())
        small.setPointSizeF(9)
        gold, quiet = QColor(c["accent_gold"]), QColor(c["text_secondary"])
        for doc in documents:
            summary = " ".join(str(doc.get("summary") or "").split())
            title = doc.get("title") or "Văn bản không tên"
            short, full = describe_stamp(doc.get("updatedAt"))
            pages = int(doc.get("pageCount") or 0)
            item = QTreeWidgetItem([
                doc.get("symbol") or "", summary or title, short, str(pages) if pages else "",
            ])
            item.setData(0, Qt.UserRole, doc.get("id"))
            item.setFont(0, serif)
            item.setForeground(0, gold)
            item.setFont(1, serif if summary else small)
            if not summary:
                item.setForeground(1, quiet)
            for column in (2, 3):
                item.setFont(column, small)
                item.setForeground(column, quiet)
            item.setTextAlignment(3, Qt.AlignRight | Qt.AlignVCenter)
            tip = "\n".join(part for part in (
                summary, title if summary else "", "Cập nhật " + full if full else "",
            ) if part)
            for column in range(4):
                item.setToolTip(column, tip)
            tree.addTopLevelItem(item)
        tree.itemClicked.connect(lambda item, _column: self._open(item))
        for key in (Qt.Key_Return, Qt.Key_Enter):
            QShortcut(QKeySequence(key), tree, context=Qt.WidgetShortcut,
                      activated=lambda: self._open(tree.currentItem()))
        tree.ensurePolished()
        rows = sum(tree.sizeHintForRow(index) for index in range(tree.topLevelItemCount()))
        tree.setFixedHeight(header.sizeHint().height() + rows + 2)
        return tree

    def _status_line(self, snapshot, message):
        line = QWidget(self)
        line.setObjectName("statusLine")
        row = QHBoxLayout(line)
        row.setContentsMargins(0, 12, 0, 2)
        row.setSpacing(16)
        models = snapshot.get("models") or {}
        for kind in ("ocr", "chat"):
            state = models.get(kind)
            ready = None if state is None else state == "available"
            label, tip = _MODEL_TEXT[kind][ready]
            dot = {True: "success", False: "danger", None: "text_muted"}[ready]
            widget = QLabel(line)
            widget.setTextFormat(Qt.RichText)
            widget.setText('<span style="color:{}">●</span>&nbsp;{}'.format(self._colors[dot], label))
            widget.setProperty("state", {True: "ok", False: "missing", None: "unknown"}[ready])
            widget.setToolTip(tip)
            row.addWidget(widget)
        for text, tip in (
            ("{} văn bản".format(_group_digits(snapshot.get("documentCount"))),
             "Văn bản đã lưu trong kho trên máy này"),
            ("Chỉ mục {} đoạn".format(_group_digits(snapshot.get("chunkCount"))),
             "Đoạn văn bản đã lập chỉ mục cho tìm kiếm và trợ lý pháp luật"),
            ("Ngoại tuyến",
             "Không cần Internet. Tệp, chỉ mục và mô hình đều xử lý trên thiết bị này."),
        ):
            widget = QLabel(text, line)
            widget.setToolTip(tip)
            row.addWidget(widget)
        row.addStretch(1)
        self._status = QLabel(message or "", line)
        self._status.setObjectName("nativeMessage")
        row.addWidget(self._status)
        return line

    # ── behaviour ──
    def _request(self, action):
        self._bridge.requestAction(action, "{}")

    def _open(self, item):
        doc_id = item.data(0, Qt.UserRole) if item is not None else None
        if isinstance(doc_id, int):
            self._bridge.requestAction("open_recent", json.dumps({"docId": doc_id}))

    def _set_message(self, text, state):
        self._status.setProperty("state", state)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)
        self._status.setText(text)

    def set_progress(self, percent, message):
        self._set_message("{} {}%".format(message, int(percent)), "")

    def set_error(self, message):
        self._set_message(str(message), "missing")
