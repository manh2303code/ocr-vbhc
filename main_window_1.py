"""
Hệ thống OCR AI văn bản hành chính
Phần mềm quản lý và trích xuất thông tin từ văn bản hành chính
"""
from pdf2image import convert_from_path
from typing import List, Dict, Any, Optional, Union, Tuple
import os
import sys
from pathlib import Path
import fitz
from PIL import Image
import time
import logging
from datetime import datetime
import traceback
from statistics_dialog import StatisticsDialog
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                           QPushButton, QLabel, QFileDialog, QTableWidget, QTableWidgetItem,
                           QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QScrollArea, QFrame, QSplitter, QMessageBox,
                           QComboBox, QMenu, QAction, QProgressBar, QDialog, QShortcut,
                           QCompleter, QRadioButton, QButtonGroup, QGroupBox, QTabWidget,
                           QStackedWidget,
                           QHeaderView, QSpacerItem, QSizePolicy, QStatusBar, QToolBar, 
                           QToolButton, QStyle, QStyleFactory, QCalendarWidget, QDateEdit, 
                           QCheckBox, QStyledItemDelegate, QGraphicsDropShadowEffect, QGridLayout, QFormLayout, QInputDialog,
                           QDialogButtonBox)
from PyQt5.QtCore import (Qt, QThread, pyqtSignal, QSize, QRect, QPoint, QTimer, QStringListModel,
                         QDate, QDateTime, QEvent, QPropertyAnimation, QEasingCurve, QSettings,
                         QModelIndex, QSortFilterProxyModel, QAbstractTableModel, QRegExp, QUrl)
from PyQt5.QtGui import (QImage, QPixmap, QPainter, QPen, QKeySequence, QFont, QIcon, QColor,
                       QBrush, QLinearGradient, QConicalGradient, QPalette, QFontDatabase, QCursor, QRegExpValidator,
                       QDesktopServices, QPainterPath, QStandardItemModel, QStandardItem,
                       QTextCursor, QTextCharFormat)
from ai_search import EmbeddingModel, VectorStore
from ai_search_dialog import AISearchDialog
from reranker import RerankerModel
from reindex_dialog import ReindexDialog
from chat_panel import ChatPanel
from pdf_rendering import render_pdf_adaptive  # moved from hot function
from doc_list_widget import (
    DocumentListWidget, compute_file_size_mb, classify_status,
)
from file_lifecycle import materialize_document_source, remove_temporary_source
from shutdown_lifecycle import shutdown_runtime
from background_workers import (
    BatchProcessWorker, EmbeddingWorker, FileRepairWorker, OCRWorker,
    RelationWorker,
)
from ocr_pipeline import DocumentOCR
from document_repository import DocumentDatabase
from theme_manager_v2 import ThemeManager as AppThemeManager
from settings_dialog import apply_ui_settings, get_setting
from ui_commands import command
from ui_icons import icon
from workspace_layout import should_collapse_details, splitter_sizes

# Cấu hình cơ bản
APP_VERSION = "2.0.0"
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / 'output'
DATABASE_DIR = BASE_DIR / 'database'
LOGS_DIR = BASE_DIR / 'logs'
IMAGES_DIR = OUTPUT_DIR / 'images'
RESULTS_DIR = OUTPUT_DIR / 'results'
BACKUP_DIR = BASE_DIR / 'backup'
CONFIG_DIR = BASE_DIR / 'config'
TEMP_DIR = BASE_DIR / 'temp'
ICON_DIR = BASE_DIR / 'icons'
# Poppler: handle both layouts
#   • Portable: main_window_1.py and tools/ at the SAME level → tools/poppler/...
#   • Dev:      desktop/main_window_1.py and tools/ at PARENT level → ../tools/poppler/...
# Bug 2026-04-19: hardcoded ".." broke Portable (pdf2image raised
# "Unable to get page count" / "unable to find poppler in PATH").
_HERE = os.path.dirname(os.path.abspath(__file__))
POPPLER_PATH = ""
for _cand in (
    os.path.join(_HERE, "tools", "poppler", "Library", "bin"),      # portable
    os.path.join(_HERE, "..", "tools", "poppler", "Library", "bin"), # dev
):
    if os.path.isdir(_cand):
        POPPLER_PATH = _cand
        break
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.tiff', '.bmp'}


def _has_running_worker(*workers) -> bool:
    return any(worker is not None and worker.isRunning() for worker in workers)

# Tạo thư mục
for dir_path in [OUTPUT_DIR, DATABASE_DIR, LOGS_DIR, IMAGES_DIR, 
                RESULTS_DIR, BACKUP_DIR, CONFIG_DIR, TEMP_DIR, ICON_DIR]:
    dir_path.mkdir(exist_ok=True, parents=True)

logger = logging.getLogger("OCRApp")

AUTOSAVE_INTERVAL = 60000  # ms (1 minute)

#############################
# Database Connection Pool  #
#############################
    

#############################
# Theme Manager & Styling   #
#############################
    

#############################
#   Chrome icons            #
#############################
from PyQt5.QtGui import QFontMetrics, QIconEngine  # noqa: E402  (chrome only)


class _StateIconEngine(QIconEngine):
    """One recoloured icon per state: quiet at rest, darker under the
    pointer (QIcon.Active), olive when a toggle is on (QIcon.On)."""

    def __init__(self, normal, active=None, on=None):
        super().__init__()
        self._normal, self._active, self._on = normal, active, on

    def _pick(self, mode, state):
        if state == QIcon.On and self._on is not None:
            return self._on
        if mode in (QIcon.Active, QIcon.Selected) and self._active is not None:
            return self._active
        return self._normal

    def paint(self, painter, rect, mode, state):
        sub = QIcon.Disabled if mode == QIcon.Disabled else QIcon.Normal
        self._pick(mode, state).paint(painter, rect, Qt.AlignCenter, sub, QIcon.Off)

    def pixmap(self, size, mode, state):
        sub = QIcon.Disabled if mode == QIcon.Disabled else QIcon.Normal
        return self._pick(mode, state).pixmap(size, sub, QIcon.Off)

    def actualSize(self, size, mode, state):
        return size

    def clone(self):
        return _StateIconEngine(self._normal, self._active, self._on)


def chrome_icon(name, size=18, theme=None, role="tool", on_name=None):
    """A toolbar/menu icon coloured from the theme tokens.

    Roles: "tool" (secondary text, full text under the pointer, olive when
    toggled on), "danger" (turns red only under the pointer), "mark" (the
    olive app mark). on_name swaps the glyph when a toggle is on (moon/sun).
    """
    from theme_tokens import colors_for
    if theme is None:
        theme = AppThemeManager.instance().current
    tokens = colors_for(theme)
    rest = tokens["text_secondary"]
    hover = tokens["danger"] if role == "danger" else tokens["text"]
    on = tokens["primary_muted"]
    if role == "mark":
        rest = hover = tokens["primary"] if theme == "light" else tokens["primary_muted"]
    if on_name:
        on_icon = icon(on_name, size=size, color=rest)
    elif role == "tool":
        on_icon = icon(name, size=size, color=on)
    else:
        on_icon = None
    return QIcon(_StateIconEngine(
        icon(name, size=size, color=rest), icon(name, size=size, color=hover), on_icon,
    ))


def chrome_tooltip(title, shortcut="", hint=""):
    """'Action', an optional one-line explanation, then the shortcut."""
    return "\n".join(part for part in (title, hint, shortcut) if part)


#############################
#   PDF Viewer Component    #
#############################
class PDFViewer(QWidget):
    """PDF viewing widget with page navigation and detection visualization"""
    
    pageChanged = pyqtSignal(int)
    zoomChanged = pyqtSignal(float)
    addRequested = pyqtSignal()
    
    # Zoom levels in percentage
    ZOOM_LEVELS = [25, 50, 75, 100, 125, 150, 175, 200, 250, 300]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_page = 0
        self.pages = []
        self.detection_boxes = []
        self.pdf_path = None
        self.is_updating = False
        self.zoom_level = 100  # percentage
        self.zoom_idx = 3      # index in ZOOM_LEVELS (100%)
        self.rotation = 0      # degrees (0, 90, 180, 270)
        self.highlight_text = ""
        self.highlight_keywords = []  # List of keywords for multi-keyword highlighting
        self.dpi = 150         # Base DPI for rendering
        self.current_pixmap = None
        
        # Thuộc tính cho việc vẽ box
        self.start_point = QPoint()
        self.end_point = QPoint()
        self.selection_rect = None
        self.is_drawing = False
        
        # Chế độ hiện tại (vẽ hoặc di chuyển)
        self.mode = "draw"  # "draw" hoặc "pan"
        
        # Biến cho việc pan (di chuyển) hình ảnh
        self.is_panning = False
        self.pan_start_point = QPoint()
        self.scroll_start_h = 0
        self.scroll_start_v = 0
        
        # Màu sắc cho các box
        self.selection_color = QColor(255, 0, 0)  # Màu đỏ cho selection box
        
        # Cho phép theo dõi chuột và sự kiện
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        
        self.setup_ui()

    # Navigation buttons: attribute, icon, title, command id for the shortcut.
    NAV_BUTTONS = (
        ("first_btn", "chevrons-left", "Trang đầu", None),
        ("prev_btn", "chevron-left", "Trang trước", "previous_page"),
        ("next_btn", "chevron-right", "Trang sau", "next_page"),
        ("last_btn", "chevrons-right", "Trang cuối", None),
        ("zoom_out_btn", "zoom-out", "Thu nhỏ", "zoom_out"),
        ("zoom_in_btn", "zoom-in", "Phóng to", "zoom_in"),
        ("rotate_left_btn", "rotate-ccw", "Xoay trái", "rotate_left"),
        ("rotate_right_btn", "rotate-cw", "Xoay phải", "rotate_right"),
    )

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Navigation strip: page arrows around an editable page number, zoom,
        # rotation. Small ghost icon buttons; what each does is in its tooltip.
        # The main window lifts this strip into the tab bar (take_nav_bar);
        # the preview dialog keeps it on top of the page.
        self.nav_bar = QWidget()
        self.nav_bar.setObjectName("pdf_nav")
        toolbar = QHBoxLayout(self.nav_bar)
        toolbar.setContentsMargins(6, 2, 6, 2)
        toolbar.setSpacing(2)

        callbacks = {
            "first_btn": self.goto_first_page, "prev_btn": self.previous_page,
            "next_btn": self.next_page, "last_btn": self.goto_last_page,
            "zoom_out_btn": self.zoom_out, "zoom_in_btn": self.zoom_in,
            "rotate_left_btn": self.rotate_left, "rotate_right_btn": self.rotate_right,
        }
        for attr, _icon_name, title, command_id in self.NAV_BUTTONS:
            button = QToolButton()
            button.setAutoRaise(True)
            button.setFocusPolicy(Qt.NoFocus)
            button.setToolTip(chrome_tooltip(
                title, command(command_id).shortcut if command_id else ""))
            button.clicked.connect(callbacks[attr])
            setattr(self, attr, button)

        # The current page is an editable field: type a number, press Enter.
        self.page_edit = QLineEdit()
        self.page_edit.setObjectName("pdf_page_field")
        self.page_edit.setAlignment(Qt.AlignCenter)
        self.page_edit.setFixedWidth(38)
        self.page_edit.setToolTip(chrome_tooltip("Trang đang xem", hint="Gõ số trang rồi nhấn Enter"))
        self.page_edit.returnPressed.connect(self.jump_to_page)
        self.page_edit.editingFinished.connect(self._show_current_page_number)

        # "/ 4" — the page count beside the field.
        self.page_label = QLabel("")
        self.page_label.setObjectName("pdf_page_total")

        # Zoom level; clicking it returns to 100% and the original orientation.
        self.zoom_label = QToolButton()
        self.zoom_label.setObjectName("pdf_zoom_reset")
        self.zoom_label.setAutoRaise(True)
        self.zoom_label.setFocusPolicy(Qt.NoFocus)
        self.zoom_label.setText("100%")
        self.zoom_label.setToolTip(chrome_tooltip(
            "Đặt lại thu phóng", command("zoom_reset").shortcut, "Về 100% và hướng gốc"))
        self.zoom_label.clicked.connect(self.reset_view)

        toolbar.addWidget(self.first_btn)
        toolbar.addWidget(self.prev_btn)
        toolbar.addSpacing(4)
        toolbar.addWidget(self.page_edit)
        toolbar.addSpacing(4)
        toolbar.addWidget(self.page_label)
        toolbar.addSpacing(4)
        toolbar.addWidget(self.next_btn)
        toolbar.addWidget(self.last_btn)
        toolbar.addStretch()
        toolbar.addSpacing(16)
        toolbar.addWidget(self.zoom_out_btn)
        toolbar.addWidget(self.zoom_label)
        toolbar.addWidget(self.zoom_in_btn)
        toolbar.addSpacing(10)
        toolbar.addWidget(self.rotate_left_btn)
        toolbar.addWidget(self.rotate_right_btn)
        self.apply_theme()

        # Image display in scrollable area
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("pdf_canvas")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        
        # Sử dụng custom label để xử lý sự kiện vẽ và chuột
        self.image_label = CustomImageLabel(self)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setBackgroundRole(QPalette.Base)
        
        # Quan trọng: Sử dụng đúng policy để không bị méo hình
        self.image_label.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.MinimumExpanding)
        self.image_label.setScaledContents(False)  # Tắt scaled contents
        
        self.scroll_area.setWidget(self.image_label)
        
        # Explicit empty/loading/error states instead of a blank white panel.
        self._state_panel = QWidget()
        state_layout = QVBoxLayout(self._state_panel)
        state_layout.setContentsMargins(40, 40, 40, 40)
        state_layout.addStretch()
        self._state_title = QLabel()
        self._state_title.setAlignment(Qt.AlignCenter)
        self._state_title.setProperty("role", "emptyTitle")
        state_layout.addWidget(self._state_title)
        self._state_message = QLabel()
        self._state_message.setAlignment(Qt.AlignCenter)
        self._state_message.setWordWrap(True)
        self._state_message.setProperty("role", "muted")
        state_layout.addWidget(self._state_message)
        self._state_action = QPushButton("Thêm văn bản")
        self._state_action.setFixedWidth(170)
        self._state_action.setProperty("variant", "primary")
        self._state_action.clicked.connect(self.addRequested)
        state_layout.addWidget(self._state_action, 0, Qt.AlignHCenter)
        state_layout.addStretch()

        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(self._state_panel)
        self._content_stack.addWidget(self.scroll_area)

        # Add to main layout
        main_layout.addWidget(self.nav_bar)
        main_layout.addWidget(self._content_stack)

        # Set initial state
        self.show_empty_state()
        self.update_controls()

    def take_nav_bar(self) -> QWidget:
        """Hand the navigation strip to a host (the workspace tab bar)."""
        self.layout().removeWidget(self.nav_bar)
        return self.nav_bar

    def set_nav_allowed(self, allowed: bool) -> None:
        """The host hides the strip while another tab is in front."""
        self._nav_allowed = bool(allowed)
        self._sync_nav()

    def _sync_nav(self) -> None:
        """No strip while nothing is open; in a narrow viewer only the page
        arrows, the page number and zoom stay (first/last and rotation have
        shortcuts or are rare)."""
        compact = 0 < self.width() < 640
        for attr in ("first_btn", "last_btn", "rotate_left_btn", "rotate_right_btn"):
            getattr(self, attr).setVisible(not compact)
        self.nav_bar.setVisible(getattr(self, "_nav_allowed", True)
                                and not getattr(self, "_empty", True))

    def apply_theme(self, theme=None) -> None:
        """Recolour the navigation icons from the theme tokens."""
        for attr, icon_name, _title, _command_id in self.NAV_BUTTONS:
            getattr(self, attr).setIcon(chrome_icon(icon_name, size=16, theme=theme))

    def _show_current_page_number(self) -> None:
        """Leaving the page field without Enter shows the current page again."""
        self.page_edit.setText(str(self.current_page + 1) if self.pages else "")

    def _show_state(self, title: str, message: str, action: bool = False) -> None:
        self._state_title.setText(title)
        self._state_message.setText(message)
        self._state_action.setVisible(action)
        self._content_stack.setCurrentWidget(self._state_panel)
        self._empty = action  # only the empty state offers "Thêm văn bản"
        self._sync_nav()

    def show_empty_state(self) -> None:
        self._show_state(
            "Chưa có văn bản đang mở",
            "Chọn một văn bản trong Kho văn bản hoặc thêm tệp mới để bắt đầu.",
            action=True,
        )

    def show_loading_state(self) -> None:
        self._show_state("Đang chuẩn bị bản xem", "Đang đọc và kết xuất các trang PDF…")

    def show_error_state(self, message: str) -> None:
        self._show_state("Không thể mở bản xem", message or "Tệp có thể đã bị di chuyển hoặc hỏng.")

    def show_document_state(self) -> None:
        self._content_stack.setCurrentWidget(self.scroll_area)
        self._empty = False
        self._sync_nav()

    def load_pdf(self, pdf_path):
        """Load a PDF or image file for preview (sync — kept for compat).

        NEW 2026-04-18: prefer load_pdf_async() to avoid UI freeze on
        large PDFs. This sync version is still used by batch OCR paths.
        """
        try:
            from pdf2image import convert_from_path
            from PIL import ImageOps

            self.pdf_path = pdf_path
            self.show_loading_state()
            if not os.path.exists(pdf_path):
                logger.error(f"File not found: {pdf_path}")
                self.pages = []
                self.current_page = 0
                self.update_controls()
                self.show_error_state("Không tìm thấy tệp tại đường dẫn đã lưu.")
                return False

            ext = os.path.splitext(pdf_path)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                img = Image.open(pdf_path).convert('RGB')
                img = ImageOps.exif_transpose(img)
                self.pages = [img]
            else:
                self.pages = convert_from_path(pdf_path, dpi=self.dpi, poppler_path=POPPLER_PATH)

            self.current_page = 0
            self.detection_boxes = []
            self.show_document_state()
            self.update_page_display()
            self.update_controls()
            return True

        except Exception as e:
            logger.error(f"Error loading file: {str(e)}")
            self.pages = []
            self.current_page = 0
            self.update_controls()
            self.show_error_state(str(e))
            return False

    def load_pdf_async(self, pdf_path, on_done=None):
        """Non-blocking PDF load via PDFLoadWorker.

        Shows a "loading" placeholder immediately, dispatches a QThread
        that runs convert_from_path off the GUI thread, then swaps in
        the pages when ready. Cancels any in-flight worker first.

        on_done: optional callable(success: bool) invoked after load.
        """
        from pdf_load_worker import PDFLoadWorker

        self.pdf_path = pdf_path

        # Cancel previous worker — fire-and-forget.
        # 2026-04-18 fix: removed wait(200) which blocked the main thread
        # for up to 200ms per rapid click, aggravating the lag.
        # The stale worker will finish in background; its emitted result
        # is ignored by _on_ready via `path != self.pdf_path` guard.
        prev_worker = getattr(self, '_pdf_worker', None)
        if prev_worker is not None:
            try:
                prev_worker.cancel()
                # Disconnect signals so any late emit from prev worker
                # can't touch the viewer. Using try/except since Qt
                # raises if signal wasn't connected.
                try:
                    prev_worker.pages_ready.disconnect()
                except Exception:
                    pass
                try:
                    prev_worker.load_failed.disconnect()
                except Exception:
                    pass
            except Exception:
                pass

        if not os.path.exists(pdf_path):
            self.pages = []
            self.current_page = 0
            self.show_error_state("Không tìm thấy tệp tại đường dẫn đã lưu.")
            self.update_controls()
            if callable(on_done):
                on_done(False)
            return

        # Show loading placeholder (don't block).
        self.pages = []
        self.current_page = 0
        self.show_loading_state()
        self.update_controls()

        worker = PDFLoadWorker(pdf_path, dpi=self.dpi, poppler_path=POPPLER_PATH,
                               parent=self)
        self._pdf_worker = worker
        self._pdf_on_done = on_done

        def _on_ready(path, pages):
            if path != self.pdf_path:
                return  # a newer load started — ignore stale result
            self.pages = pages
            self.current_page = 0
            self.detection_boxes = []
            self.show_document_state()
            self.update_page_display()
            self.update_controls()
            if callable(self._pdf_on_done):
                self._pdf_on_done(True)

        def _on_failed(path, err):
            if path != self.pdf_path:
                return
            logger.error(f"PDF load failed: {err}")
            self.pages = []
            self.current_page = 0
            self.show_error_state(str(err))
            self.update_controls()
            if callable(self._pdf_on_done):
                self._pdf_on_done(False)

        worker.pages_ready.connect(_on_ready)
        worker.load_failed.connect(_on_failed)
        worker.start(QThread.LowPriority)

    def set_detection_boxes(self, boxes):
        """Set detection boxes to visualize"""
        self.detection_boxes = boxes
        if not self.is_updating:  # Chỉ gọi update khi không đang update
            self.update_page_display()

    def set_highlight_text(self, text):
        """
        Set text to highlight in the document.
        
        Args:
            text: Can be a single string or a list of keywords to highlight
        """
        if isinstance(text, list):
            self.highlight_keywords = text
            self.highlight_text = '; '.join(text) if text else ""
        else:
            self.highlight_text = text or ""
            self.highlight_keywords = [kw.strip() for kw in text.split(';') if kw.strip()] if text else []
        # Note: Text highlighting may require different implementation with pdf2image
        self.update_page_display()

    def update_controls(self):
        """Update navigation controls based on document state"""
        has_doc = len(self.pages) > 0

        # Update page display: the field holds the page, the label the count.
        self.page_label.setText(f"/ {len(self.pages)}" if has_doc else "")
        self._show_current_page_number()

        # Enable/disable controls
        self.first_btn.setEnabled(has_doc and self.current_page > 0)
        self.prev_btn.setEnabled(has_doc and self.current_page > 0)
        self.next_btn.setEnabled(has_doc and self.current_page < len(self.pages) - 1)
        self.last_btn.setEnabled(has_doc and self.current_page < len(self.pages) - 1)
        self.page_edit.setEnabled(has_doc)
        self.zoom_label.setEnabled(has_doc)
        self.zoom_in_btn.setEnabled(has_doc and self.zoom_idx < len(self.ZOOM_LEVELS) - 1)
        self.zoom_out_btn.setEnabled(has_doc and self.zoom_idx > 0)
        self.rotate_left_btn.setEnabled(has_doc)
        self.rotate_right_btn.setEnabled(has_doc)
        
        # Update zoom display
        self.zoom_label.setText(f"{self.zoom_level}%")

    def update_page_display(self):
        """Render current page"""
        # Ngăn đệ quy
        if self.is_updating:
            return
            
        self.is_updating = True
        
        try:
            if not self.pages or self.current_page >= len(self.pages):
                self.image_label.clear()
                self.is_updating = False
                return

            # Get the PIL Image for current page
            current_pil_image = self.pages[self.current_page]
            
            # Apply rotation if needed
            if self.rotation != 0:
                current_pil_image = current_pil_image.rotate(-self.rotation, expand=True)
            
            # Convert PIL Image to QImage for display
            img_data = current_pil_image.convert('RGB')
            width, height = img_data.size
            
            # Convert to QImage
            bytes_per_line = 3 * width
            q_image = QImage(img_data.tobytes('raw', 'RGB'), width, height, bytes_per_line, QImage.Format_RGB888)
            
            # Tạo QPixmap từ QImage
            pixmap = QPixmap.fromImage(q_image)
            self.current_pixmap = pixmap  # Lưu pixmap hiện tại để sử dụng trong paintEvent
            
            # Tính toán tỷ lệ zoom
            zoom_factor = self.zoom_level / 100.0
            
            # Tắt scaleContents để tránh méo hình và cho phép custom drawing
            self.image_label.setScaledContents(False)
            
            # Thay đổi kích thước của QLabel để vừa với hình ảnh đã zoom
            scaled_width = int(pixmap.width() * zoom_factor)
            scaled_height = int(pixmap.height() * zoom_factor)
            self.image_label.setMinimumSize(scaled_width, scaled_height)
            self.image_label.resize(scaled_width, scaled_height)
            
            # Thêm pixmap vào label (chỉ để giữ reference, việc vẽ sẽ do paintEvent)
            # Note: có thể không cần thiết vì chúng ta đã vẽ trong paintEvent
            # Nhưng gán pixmap giúp QLabel có thể hiểu kích thước
            self.image_label.setPixmap(pixmap)
            
            # Gọi hàm update để vẽ lại
            self.image_label.update()
            
            # Emit page changed signal
            self.pageChanged.emit(self.current_page)
                
        except Exception as e:
            print(f"Error updating page display: {str(e)}")
            # Create an empty white image as fallback
            fallback_img = QImage(600, 800, QImage.Format_RGB888)
            fallback_img.fill(Qt.white)
            self.current_pixmap = QPixmap.fromImage(fallback_img)
            self.image_label.setPixmap(QPixmap.fromImage(fallback_img))  # Thêm dòng này
            self.image_label.update()
            
        self.is_updating = False

    def previous_page(self):
        """Go to previous page"""
        if self.pages and self.current_page > 0:
            self.current_page -= 1
            self.update_page_display()
            self.update_controls()

    def next_page(self):
        """Go to next page"""
        if self.pages and self.current_page < len(self.pages) - 1:
            self.current_page += 1
            self.update_page_display()
            self.update_controls()
            
    def goto_first_page(self):
        """Go to first page"""
        if self.pages and self.current_page > 0:
            self.current_page = 0
            self.update_page_display()
            self.update_controls()
            
    def goto_last_page(self):
        """Go to last page"""
        if self.pages and self.current_page < len(self.pages) - 1:
            self.current_page = len(self.pages) - 1
            self.update_page_display()
            self.update_controls()
            
    def jump_to_page(self):
        """Jump to specific page number"""
        if not self.pages:
            return
            
        try:
            page_num = int(self.page_edit.text())
            if page_num < 1:
                page_num = 1
            elif page_num > len(self.pages):
                page_num = len(self.pages)
                
            self.current_page = page_num - 1
            self.update_page_display()
            self.update_controls()
        except ValueError:
            # Invalid input: show the current page again
            self._show_current_page_number()
            
    def zoom_in(self):
        """Increase zoom level"""
        if not self.pages:
            return
            
        if self.zoom_idx < len(self.ZOOM_LEVELS) - 1:
            self.zoom_idx += 1
            self.zoom_level = self.ZOOM_LEVELS[self.zoom_idx]
            self.update_page_display()
            self.update_controls()
            self.zoomChanged.emit(self.zoom_level / 100.0)
            
    def zoom_out(self):
        """Decrease zoom level"""
        if not self.pages:
            return
            
        if self.zoom_idx > 0:
            self.zoom_idx -= 1
            self.zoom_level = self.ZOOM_LEVELS[self.zoom_idx]
            self.update_page_display()
            self.update_controls()
            self.zoomChanged.emit(self.zoom_level / 100.0)
    
    def rotate_left(self):
        """Rotate view 90 degrees counterclockwise"""
        if not self.pages:
            return
            
        self.rotation = (self.rotation - 90) % 360
        self.update_page_display()
        
    def rotate_right(self):
        """Rotate view 90 degrees clockwise"""
        if not self.pages:
            return
            
        self.rotation = (self.rotation + 90) % 360
        self.update_page_display()
        
    def reset_view(self):
        """Reset zoom and rotation to defaults"""
        if not self.pages:
            return
            
        self.zoom_level = 100
        self.zoom_idx = self.ZOOM_LEVELS.index(self.zoom_level)
        self.rotation = 0
        self.update_page_display()
        self.update_controls()
        self.zoomChanged.emit(self.zoom_level / 100.0)

    def resizeEvent(self, event):
        """Handle resize events"""
        super().resizeEvent(event)
        self._sync_nav()
        if self.pages:
            self.update_page_display()

    def clear(self):
        """Clear current display"""
        self.pages = []
        self.current_page = 0
        self.detection_boxes = []
        self.pdf_path = None
        self.zoom_level = 100
        self.zoom_idx = self.ZOOM_LEVELS.index(self.zoom_level)
        self.rotation = 0
        self.highlight_text = ""
        self.current_pixmap = None
        self.image_label.clear()
        self.show_empty_state()
        self.update_controls()
        
    def clear_selection(self):
        """Clear the current selection box"""
        self.selection_rect = None
        self.image_label.update()
    

# Add CustomImageLabel for handling drawing and mouse events
class CustomImageLabel(QLabel):
    """Custom QLabel for handling box drawing and mouse events"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_viewer = parent  # Reference to the PDFViewer
        self.setMouseTracking(True)  # Enable mouse tracking
        self.setCursor(Qt.CrossCursor)  # Set default cursor
    
    def paintEvent(self, event):
        """Custom paint event to draw image and selection box"""
        # Vẽ pixmap trước
        if hasattr(self.parent_viewer, 'current_pixmap') and self.parent_viewer.current_pixmap is not None:
            pixmap = self.parent_viewer.current_pixmap
            zoom_factor = self.parent_viewer.zoom_level / 100.0
            
            # Tính toán kích thước và vị trí để vẽ pixmap
            scaled_width = pixmap.width() * zoom_factor
            scaled_height = pixmap.height() * zoom_factor
            
            # Tính toán vị trí để căn giữa
            x = max(0, (self.width() - scaled_width) / 2)
            y = max(0, (self.height() - scaled_height) / 2)

            # Tạo một painter để vẽ lên widget
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            
            # Vẽ pixmap với kích thước đã được thu phóng theo zoom
            target_rect = QRect(int(x), int(y), int(scaled_width), int(scaled_height))
            source_rect = QRect(0, 0, pixmap.width(), pixmap.height())
            painter.drawPixmap(target_rect, pixmap, source_rect)
            
            # Vẽ các detection boxes nếu có
            try:
                for box in self.parent_viewer.detection_boxes:
                    # Kiểm tra kiểu dữ liệu của box và trích xuất tọa độ an toàn
                    if isinstance(box, dict) and 'box' in box:
                        box_coords = box['box']
                        # Đảm bảo box_coords là list hoặc tuple có 4 phần tử
                        if isinstance(box_coords, (list, tuple)) and len(box_coords) == 4:
                            x1, y1, x2, y2 = box_coords
                            cls = box.get('class', 0)
                            cls_name = box.get('class_name', '')
                            
                            # Chuyển đổi tọa độ về không gian hiển thị
                            x1_disp = x + x1 * zoom_factor
                            y1_disp = y + y1 * zoom_factor
                            x2_disp = x + x2 * zoom_factor
                            y2_disp = y + y2 * zoom_factor
                            
                            # Đặt màu cho box dựa theo lớp
                            colors = [
                                QColor(0, 255, 0),     # Xanh lá
                                QColor(255, 0, 0),    # Đỏ
                                QColor(0, 0, 255),    # Xanh dương
                                QColor(255, 255, 0),  # Vàng
                                QColor(255, 0, 255),  # Tím
                                QColor(0, 255, 255),  # Xanh lam
                                QColor(255, 165, 0),  # Cam
                                QColor(128, 0, 128),  # Tím đậm
                                QColor(0, 128, 128)   # Xanh lục lam
                            ]
                            color = colors[cls % len(colors)]
                            painter.setPen(QPen(color, 2, Qt.SolidLine))
                            
                            # Vẽ box
                            box_width = int(x2_disp - x1_disp)
                            box_height = int(y2_disp - y1_disp)
                            painter.drawRect(int(x1_disp), int(y1_disp), box_width, box_height)
                            
                            # Vẽ label cho box nếu có class_name
                            if cls_name:
                                # Chuẩn bị vẽ text
                                text_rect = QRect(int(x1_disp), int(y1_disp) - 20, box_width, 20)
                                # Vẽ nền cho text
                                painter.fillRect(text_rect, QBrush(QColor(0, 0, 0, 180)))
                                # Vẽ text
                                painter.setPen(QPen(Qt.white))
                                painter.drawText(text_rect, Qt.AlignCenter, cls_name)
            except Exception as e:
                print(f"Lỗi khi vẽ detection boxes: {e}")
            
            # Vẽ box selection nếu có
            if self.parent_viewer.selection_rect:
                # Sử dụng màu đỏ đậm cho box selection để dễ nhìn
                painter.setPen(QPen(QColor(255, 0, 0), 2, Qt.SolidLine))
                painter.setBrush(QBrush(QColor(255, 0, 0, 50)))  # Đỏ với độ trong suốt
                painter.drawRect(self.parent_viewer.selection_rect)
            
            # Đảm bảo kết thúc painter đúng cách
            painter.end()
        else:
            # Vẽ fallback hoặc gọi hàm vẽ mặc định
            super().paintEvent(event)
    
    def mousePressEvent(self, event):
        """Handle mouse press events"""
        if not hasattr(self.parent_viewer, 'current_pixmap') or self.parent_viewer.current_pixmap is None:
            return
        
        # Middle button hoặc Ctrl+Left button để pan
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and event.modifiers() == Qt.ControlModifier):
            self.parent_viewer.is_panning = True
            self.parent_viewer.pan_start_point = event.pos()
            self.parent_viewer.scroll_start_h = self.parent_viewer.scroll_area.horizontalScrollBar().value()
            self.parent_viewer.scroll_start_v = self.parent_viewer.scroll_area.verticalScrollBar().value()
            self.setCursor(Qt.ClosedHandCursor)
            self.update()
            return
            
        if event.button() == Qt.LeftButton:
            # Bắt đầu vẽ box
            if self.parent_viewer.mode == "draw":
                self.parent_viewer.is_drawing = True
                self.parent_viewer.start_point = event.pos()
                self.parent_viewer.end_point = event.pos()
                self.parent_viewer.selection_rect = QRect(
                    self.parent_viewer.start_point, 
                    self.parent_viewer.end_point
                )
            # Bắt đầu di chuyển (pan)
            else:
                self.parent_viewer.is_panning = True
                self.parent_viewer.pan_start_point = event.pos()
                self.parent_viewer.scroll_start_h = self.parent_viewer.scroll_area.horizontalScrollBar().value()
                self.parent_viewer.scroll_start_v = self.parent_viewer.scroll_area.verticalScrollBar().value()
                self.setCursor(Qt.ClosedHandCursor)
                
            self.update()
    
    def mouseMoveEvent(self, event):
        """Handle mouse move events"""
        if not hasattr(self.parent_viewer, 'current_pixmap') or self.parent_viewer.current_pixmap is None:
            return
        
        # Xử lý panning (kéo để di chuyển)
        if self.parent_viewer.is_panning:
            delta = event.pos() - self.parent_viewer.pan_start_point
            # Di chuyển scroll bars
            h_bar = self.parent_viewer.scroll_area.horizontalScrollBar()
            v_bar = self.parent_viewer.scroll_area.verticalScrollBar()
            h_bar.setValue(self.parent_viewer.scroll_start_h - delta.x())
            v_bar.setValue(self.parent_viewer.scroll_start_v - delta.y())
            return
            
        if self.parent_viewer.is_drawing and self.parent_viewer.mode == "draw":
            # Tiếp tục vẽ box
            self.parent_viewer.end_point = event.pos()
            self.parent_viewer.selection_rect = QRect(
                self.parent_viewer.start_point, 
                self.parent_viewer.end_point
            ).normalized()
            self.update()
    
    def mouseReleaseEvent(self, event):
        """Handle mouse release events"""
        # Xử lý kết thúc panning cho middle button hoặc Ctrl+left
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and self.parent_viewer.is_panning):
            if self.parent_viewer.is_panning:
                self.parent_viewer.is_panning = False
                self.setCursor(Qt.CrossCursor)
                self.update()
                return
        
        if event.button() == Qt.LeftButton:
            if self.parent_viewer.is_drawing and self.parent_viewer.mode == "draw":
                # Kết thúc vẽ box
                self.parent_viewer.is_drawing = False
                self.parent_viewer.end_point = event.pos()
                self.parent_viewer.selection_rect = QRect(
                    self.parent_viewer.start_point, 
                    self.parent_viewer.end_point
                ).normalized()
                
                # Selection validated (buttons removed — GGUF pipeline doesn't use manual region OCR)
                pass
            
            elif self.parent_viewer.is_panning:
                # Kết thúc di chuyển
                self.parent_viewer.is_panning = False
                self.setCursor(Qt.CrossCursor)
            
            self.update()
    
    def wheelEvent(self, event):
        """Handle mouse wheel events for Ctrl+scroll zoom"""
        if not hasattr(self.parent_viewer, 'current_pixmap') or self.parent_viewer.current_pixmap is None:
            return
            
        # Kiểm tra nếu Ctrl được nhấn
        modifiers = QApplication.keyboardModifiers()
        if modifiers == Qt.ControlModifier:
            # Zoom in/out với Ctrl+scroll
            delta = event.angleDelta().y()
            if delta > 0:
                # Scroll up - zoom in
                self.parent_viewer.zoom_in()
            elif delta < 0:
                # Scroll down - zoom out
                self.parent_viewer.zoom_out()
            event.accept()
        else:
            # Không có Ctrl - scroll bình thường
            super().wheelEvent(event)

#############################
#  Document Database Class  #
#############################

# #############################
# #
# #############################
# class StatisticsDialog(QDialog):
#     """Dialog hiển thị thống kê"""

#     def __init__(self, db, parent=None):
#         super().__init__(parent)
#         self.db = db
#         self.setup_ui()
#         self.load_statistics()

#     def setup_ui(self):
#         self.setWindowTitle("Thống kê hệ thống")
#         self.setMinimumWidth(800)
#         self.setMinimumHeight(600)

#         layout = QVBoxLayout(self)

#         # Tạo tab widget
#         tab_widget = QTabWidget()
        
#         # Tab tổng quan
#         overview_tab = QWidget()
#         overview_layout = QVBoxLayout(overview_tab)
        
#         # Box thông tin tổng quan
#         overview_group = QGroupBox("Thông tin tổng quan")
#         overview_group_layout = QVBoxLayout()
#         self.total_label = QLabel()
#         self.total_label.setStyleSheet("font-size: 14px; font-weight: bold;")
#         overview_group_layout.addWidget(self.total_label)
#         overview_group.setLayout(overview_group_layout)
#         overview_layout.addWidget(overview_group)
        
#         # Thống kê theo loại
#         type_group = QGroupBox("Thống kê theo loại văn bản")
#         type_layout = QVBoxLayout()
#         self.type_table = QTableWidget()
#         self.type_table.setColumnCount(2)
#         self.type_table.setHorizontalHeaderLabels(["Loại văn bản", "Số lượng"])
#         self.type_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
#         self.type_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
#         type_layout.addWidget(self.type_table)
#         type_group.setLayout(type_layout)
#         overview_layout.addWidget(type_group)
        
#         # Thống kê độ khẩn
#         urgency_group = QGroupBox("Thống kê theo độ khẩn")
#         urgency_layout = QVBoxLayout()
#         self.urgency_table = QTableWidget()
#         self.urgency_table.setColumnCount(2)
#         self.urgency_table.setHorizontalHeaderLabels(["Độ khẩn", "Số lượng"])
#         self.urgency_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
#         self.urgency_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
#         urgency_layout.addWidget(self.urgency_table)
#         urgency_group.setLayout(urgency_layout)
#         overview_layout.addWidget(urgency_group)
        
#         tab_widget.addTab(overview_tab, "Tổng quan")
        
#         # Tab văn bản mới nhất
#         recent_tab = QWidget()
#         recent_layout = QVBoxLayout(recent_tab)
#         self.recent_table = QTableWidget()
#         self.recent_table.setColumnCount(4)
#         self.recent_table.setHorizontalHeaderLabels(["ID", "Tên file", "Ngày tạo", "Số ký hiệu"])
#         self.recent_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
#         recent_layout.addWidget(self.recent_table)
#         tab_widget.addTab(recent_tab, "Văn bản mới nhất")
        
#         # Tab thống kê theo thời gian
#         time_tab = QWidget()
#         time_layout = QVBoxLayout(time_tab)
#         self.time_table = QTableWidget()
#         self.time_table.setColumnCount(2)
#         self.time_table.setHorizontalHeaderLabels(["Tháng", "Số lượng"])
#         self.time_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
#         time_layout.addWidget(self.time_table)
#         tab_widget.addTab(time_tab, "Thống kê theo thời gian")
        
#         layout.addWidget(tab_widget)
        
#         # Buttons
#         button_box = QHBoxLayout()
#         close_btn = QPushButton("Đóng")
#         close_btn.clicked.connect(self.close)
#         export_btn = QPushButton("Xuất báo cáo")
#         export_btn.clicked.connect(self.export_statistics)
#         button_box.addStretch()
#         button_box.addWidget(export_btn)
#         button_box.addWidget(close_btn)
#         layout.addLayout(button_box)

#     def load_statistics(self):
#         """Load thống kê từ database"""
#         stats = self.db.get_statistics()
#         if not stats:
#             return

#         # Cập nhật tổng quan
#         self.total_label.setText(f"Tổng số văn bản: {stats['total_documents']}")

#         # Cập nhật bảng loại văn bản
#         self.type_table.setRowCount(len(stats['by_type']))
#         for i, (type_name, count) in enumerate(stats['by_type']):
#             self.type_table.setItem(i, 0, QTableWidgetItem(type_name or "Không xác định"))
#             self.type_table.setItem(i, 1, QTableWidgetItem(str(count)))

#         # Cập nhật bảng độ khẩn
#         self.urgency_table.setRowCount(len(stats['by_urgency']))
#         for i, (urgency, count) in enumerate(stats['by_urgency']):
#             item = QTableWidgetItem(urgency or "Không")
#             if urgency == "Độ Mật":
#                 item.setBackground(QColor(255, 200, 200))
#             elif urgency == "Hỏa Tốc":
#                 item.setBackground(QColor(255, 140, 0))
#                 item.setForeground(QColor(255, 255, 255))
#             self.urgency_table.setItem(i, 0, item)
#             self.urgency_table.setItem(i, 1, QTableWidgetItem(str(count)))

#         # Cập nhật văn bản mới nhất
#         self.recent_table.setRowCount(len(stats['recent_docs']))
#         for i, (doc_id, filename, created_at, so_kh) in enumerate(stats['recent_docs']):
#             self.recent_table.setItem(i, 0, QTableWidgetItem(str(doc_id)))
#             self.recent_table.setItem(i, 1, QTableWidgetItem(filename))
#             self.recent_table.setItem(i, 2, QTableWidgetItem(str(created_at)))
#             self.recent_table.setItem(i, 3, QTableWidgetItem(so_kh))

#         # Cập nhật thống kê theo thời gian
#         self.time_table.setRowCount(len(stats['by_month']))
#         for i, (month, count) in enumerate(stats['by_month']):
#             self.time_table.setItem(i, 0, QTableWidgetItem(month))
#             self.time_table.setItem(i, 1, QTableWidgetItem(str(count)))

#     def export_statistics(self):
#         """Xuất thống kê ra file Excel"""
#         try:
#             file_path, _ = QFileDialog.getSaveFileName(
#                 self, "Xuất báo cáo", "", "Excel Files (*.xlsx)"
#             )
#             if not file_path:
#                 return

#             if not file_path.endswith('.xlsx'):
#                 file_path += '.xlsx'

#             stats = self.db.get_statistics()
#             if not stats:
#                 return

#             with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
#                 # Tổng quan
#                 overview_data = {
#                     'Thông tin': ['Tổng số văn bản'],
#                     'Số lượng': [stats['total_documents']]
#                 }
#                 pd.DataFrame(overview_data).to_excel(writer, sheet_name='Tổng quan', index=False)

#                 # Thống kê theo loại
#                 type_data = pd.DataFrame(stats['by_type'], columns=['Loại văn bản', 'Số lượng'])
#                 type_data.to_excel(writer, sheet_name='Theo loại', index=False)

#                 # Thống kê theo độ khẩn
#                 urgency_data = pd.DataFrame(stats['by_urgency'], columns=['Độ khẩn', 'Số lượng'])
#                 urgency_data.to_excel(writer, sheet_name='Theo độ khẩn', index=False)

#                 # Thống kê theo thời gian
#                 time_data = pd.DataFrame(stats['by_month'], columns=['Tháng', 'Số lượng'])
#                 time_data.to_excel(writer, sheet_name='Theo thời gian', index=False)

#                 # Văn bản mới nhất
#                 recent_data = pd.DataFrame(
#                     stats['recent_docs'],
#                     columns=['ID', 'Tên file', 'Ngày tạo', 'Số ký hiệu']
#                 )
#                 recent_data.to_excel(writer, sheet_name='Văn bản mới', index=False)

#             QMessageBox.information(self, "Thành công", f"Đã xuất báo cáo thống kê tới {file_path}")

#         except Exception as e:
#             QMessageBox.critical(self, "Lỗi", f"Lỗi khi xuất báo cáo: {str(e)}")

#############################
#    Document OCR Class     #
#############################

    # def extract_text(self, image):
    #     """Extract text from a single image using Tesseract - tối ưu cho tiếng Việt"""
    #     try:
    #         # Đảm bảo đường dẫn Tesseract được thiết lập
    #         pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    #         os.environ['TESSDATA_PREFIX'] = r"C:\Program Files\Tesseract-OCR\tessdata"
            
    #         # Lưu ảnh gốc
    #         timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    #         if not isinstance(image, Image.Image):
    #             image = Image.fromarray(image)
            
    #         # Lưu ảnh gốc
    #         image_path = self.image_save_dir / 'original' / f"single_{timestamp}.png"
    #         image.save(image_path)
            
    #         # Tiền xử lý ảnh tối ưu cho tiếng Việt
    #         processed_img = self.preprocess_image_for_document(image)
    #         processed_img = Image.fromarray(processed_img)
            
    #         # Lưu ảnh đã xử lý
    #         processed_path = self.image_save_dir / 'processed' / f"single_{timestamp}.png"
    #         processed_img.save(processed_path)
            
    #         # Thực hiện OCR với thông số tối ưu
    #         custom_config = f'--oem 1 --psm {self.psm} --dpi 300 -c preserve_interword_spaces=1'
    #         text = pytesseract.image_to_string(
    #             processed_img,
    #             lang=self.lang,
    #             config=custom_config
    #         )
            
    #         # Xử lý hậu kỳ để sửa lỗi thường gặp trong tiếng Việt
    #         text = text.replace('ă', 'ă').replace('ơ', 'ơ').replace('ư', 'ư')
    #         text = text.replace('Ă', 'Ă').replace('Ơ', 'Ơ').replace('Ư', 'Ư')
            
    #         # Để duy trì khả năng tương thích với code hiện tại
    #         result = []
    #         if text.strip():
    #             # Giả lập một hộp giới hạn bao quanh toàn bộ ảnh
    #             width, height = image.size
    #             box = [
    #                 [0, 0],
    #                 [width, 0],
    #                 [width, height],
    #                 [0, height]
    #             ]
    #             # Độ tin cậy mặc định
    #             confidence = 0.95  # Tăng độ tin cậy mặc định
    #             result.append([box, text, confidence])
            
    #         return result
                
    #     except Exception as e:
    #         logger.error(f"Error extracting text: {str(e)}")
    #         traceback.print_exc()
    #         return []

#############################
#    OCR Result Editor      #
#############################
# Editor-only widgets and the tokens they are coloured with; imported here,
# next to their one user.
import theme_tokens  # noqa: E402
from editor_widgets import (  # noqa: E402
    DOCUMENT_FONT, DatePopup, ElidedLabel, GrowingTextEdit, InlineSelector,
    SectionHeader, format_document_date, install_wheel_guard, parse_document_date,
)

_QWIDGETSIZE_MAX = 16777215


def _document_font(point_size):
    font = QFont(DOCUMENT_FONT)
    font.setPointSizeF(point_size)
    return font


class OCRResultEditor(QWidget):
    """The document editor: full text above, metadata below, actions at the foot.

    2026-10 redesign (docs/design/minimal-ui-2026-10.md):
      header     the loại văn bản, and the số hiệu as the small gold mark
      Toàn văn   the OCR text, read-only, in Times New Roman like the paper
      Thông tin  label/value rows; values in Times New Roman never hide text:
                 multi-line values wrap and grow, single-line ones show their
                 start and carry the full value as tooltip when cut
      action bar icon tools left; Đặt lại and the one primary button right

    Each section title has an expand button. Expanding one section folds the
    other to its title row, so either the whole text or every metadata value
    fills the panel; the same button folds it back. This replaces the old
    show/hide-metadata toggle in the header.
    """

    ocr_updated = pyqtSignal(int, dict)
    field_changed = pyqtSignal(str, str)  # field_name, new_value
    reocr_requested = pyqtSignal()  # user wants to re-run OCR on current document

    TEXT = "text"
    META = "meta"
    # Lines a multi-line value shows in the split layout before it scrolls
    # on its own. Generous on purpose: the metadata panel already scrolls, and
    # a scroll inside a scroll hides text; only an unusually long value (a
    # long list of nơi nhận) stops here. Expanded, every line shows.
    SPLIT_LINES = {'cqbh': 4, 'nd_chinh': 8, 'noi_nhan': 8, 'chuc_vu': 4}
    # One value each, usually short, but wrapping (never cut) when long.
    SINGLE_VALUE_FIELDS = ('so_ki_hieu', 'loai_vb', 'chu_ky')
    # Share of the panel the metadata may take in the split layout.
    SPLIT_META_SHARE = 0.5
    # The one QLineEdit: the date, with its calendar. A legacy worded date can
    # still be long; it shows its start and the whole value as tooltip.
    LINE_FIELDS = ('ngay_bh',)
    URGENCY_LEVELS = (("Không", None), ("Hỏa Tốc", "urgency_hoa_toc"),
                      ("Thượng Khẩn", "urgency_thuong_khan"), ("Khẩn", "urgency_khan"))
    SECRECY_LEVELS = (("Không", None), ("Mật", "classif_mat"),
                      ("Tối Mật", "classif_toi_mat"), ("Tuyệt Mật", "classif_tuyet_mat"))

    def __init__(self, db, parent=None):  # Thêm tham số db
        super().__init__(parent)
        self.setObjectName("ocrResultEditor")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.db = db  # Lưu database instance
        self.doc_id = None
        self.full_cqbh_text = ""
        self.current_doc_data = None
        self.is_loading = False
        self._theme_name = "dark" if AppThemeManager.instance().current == "dark" else "light"
        self._expanded = None
        self._date_popup = None
        self._highlight_terms = []
        self._split_pending = False
        self._header_tooltip_file = ""
        self._van_ban_html_cache = ""
        self._van_ban_plain = ""
        self.setup_ui()
        self.apply_theme(self._theme_name)

    # ── Layout ──────────────────────────────────────────────────
    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout = main_layout
        self.fields = {}

        main_layout.addWidget(self._build_header())

        self._text_header = SectionHeader(
            "Toàn văn",
            "Mở rộng toàn văn\nThu gọn phần thông tin để đọc trọn văn bản",
            "Thu gọn toàn văn\nHiện lại phần thông tin",
        )
        self._text_header.toggled.connect(lambda: self.toggle_section(self.TEXT))
        main_layout.addWidget(self._text_header)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setFrameShape(QFrame.NoFrame)
        text.setProperty("role", "ocrText")
        text.setFont(_document_font(11))
        text.setPlaceholderText("Toàn văn sẽ hiện ở đây sau khi OCR.")
        text.setMinimumHeight(120)
        self.fields['van_ban_dang_txt'] = text
        main_layout.addWidget(text, 1)

        self._meta_header = SectionHeader(
            "Thông tin",
            "Mở rộng thông tin\nHiện đầy đủ mọi trường, thu gọn toàn văn",
            "Thu gọn thông tin\nHiện lại toàn văn",
        )
        self._meta_header.toggled.connect(lambda: self.toggle_section(self.META))
        main_layout.addWidget(self._meta_header)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("ocrMetadataScroll")
        self.scroll_area.viewport().setObjectName("ocrMetadataViewport")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setWidget(self._build_metadata_form())
        main_layout.addWidget(self.scroll_area)

        main_layout.addWidget(self._build_action_bar())

        # Dirty tracking for the save button. The full text is read-only and
        # re-rendered on theme changes and search highlights, so it is not a
        # user edit and stays out.
        for field_id, widget in self.fields.items():
            if field_id == 'van_ban_dang_txt':
                continue
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self.content_changed)
            elif isinstance(widget, QTextEdit):
                widget.textChanged.connect(self.content_changed)
            elif isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(self.content_changed)

        # Live-refresh header bar when so_ki_hieu / loai_vb change.
        self.fields['so_ki_hieu'].textChanged.connect(self._refresh_header_live)
        self.fields['loai_vb'].textChanged.connect(self._refresh_header_live)
        for key in self.LINE_FIELDS:
            self.fields[key].textChanged.connect(
                lambda *_args, k=key: self._sync_line_tooltip(self.fields[k])
            )
        for key in (*self.SPLIT_LINES, *self.SINGLE_VALUE_FIELDS):
            self.fields[key].height_changed.connect(self._schedule_split)

        self.set_expanded(None)

    def _build_header(self):
        """Loại văn bản, then the số hiệu as the gold mark; nothing else."""
        self._header_bar = QFrame()
        self._header_bar.setObjectName("ocr_header_bar")
        self._header_bar.setFixedHeight(36)
        layout = QHBoxLayout(self._header_bar)
        layout.setContentsMargins(12, 6, 12, 2)
        layout.setSpacing(8)
        self._doc_type_label = ElidedLabel("Chưa mở văn bản")
        self._doc_type_label.setProperty("role", "documentType")
        # Both elide rather than widen the panel; the number carries its full
        # text in the tooltip.
        self._doc_number_label = ElidedLabel("")
        self._doc_number_label.setProperty("role", "documentNumber")
        self._doc_number_label.hide()
        layout.addWidget(self._doc_type_label)
        layout.addWidget(self._doc_number_label)
        layout.addStretch(1)
        return self._header_bar

    def _build_metadata_form(self):
        """Label/value rows in the order the fields appear on the paper."""
        form_widget = QWidget()
        form_widget.setObjectName("ocrMetadataForm")
        self._form_widget = form_widget
        self.form_layout = QVBoxLayout(form_widget)
        self.form_layout.setContentsMargins(12, 2, 12, 12)
        self.form_layout.setSpacing(0)

        def line(key, placeholder=""):
            edit = QLineEdit()
            edit.setProperty("role", "metadataValue")
            edit.setFont(_document_font(10.5))
            if placeholder:
                edit.setPlaceholderText(placeholder)
            self.fields[key] = edit
            return edit

        def growing(key):
            single = key in self.SINGLE_VALUE_FIELDS
            edit = GrowingTextEdit(min_lines=1, single_line=single,
                                   max_lines=None if single else self.SPLIT_LINES[key])
            edit.setProperty("role", "metadataValue")
            edit.setFont(_document_font(10.5))
            self.fields[key] = edit
            return edit

        def level(key, levels):
            combo = QComboBox()
            combo.setProperty("role", "metadataValue")
            combo.setFont(_document_font(10.5))
            combo.setIconSize(QSize(8, 8))
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(6)
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            for text, _token in levels:
                combo.addItem(QIcon(), text)
            combo.currentTextChanged.connect(
                lambda value, k=key: self.field_changed.emit(k, value)
            )
            self.fields[key] = combo
            return combo

        def selector(items, tip):
            combo = InlineSelector(items)
            combo.setProperty("role", "metadataSelector")
            combo.setToolTip(tip)
            combo.setCursor(QCursor(Qt.PointingHandCursor))
            return combo

        for key in self.SINGLE_VALUE_FIELDS:
            growing(key)
        for key in ('so_ki_hieu', 'loai_vb'):
            completer = QCompleter([])
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            self.fields[key].setCompleter(completer)
        self.fields['so_ki_hieu'].textChanged.connect(
            lambda: self.field_changed.emit('so_ki_hieu', self.fields['so_ki_hieu'].text())
        )

        date_edit = line('ngay_bh', "dd/mm/yyyy")
        self._date_action = date_edit.addAction(QIcon(), QLineEdit.TrailingPosition)
        self._date_action.setToolTip("Chọn ngày từ lịch\nAlt+↓")
        self._date_action.triggered.connect(self.show_date_picker)
        date_edit.editingFinished.connect(self._normalize_typed_date)
        date_edit.installEventFilter(self)

        for key in ('cqbh', 'nd_chinh', 'noi_nhan', 'chuc_vu'):
            growing(key)
        level('do_khan', self.URGENCY_LEVELS)
        level('do_mat', self.SECRECY_LEVELS)

        self.cqbh_selector = selector(
            ["Tất cả", "Trên", "Dưới"],
            "Dòng đang sửa\nTất cả: cả hai dòng; Trên: cơ quan chủ quản;\n"
            "Dưới: cơ quan ban hành",
        )
        self.cqbh_selector.currentTextChanged.connect(self.cqbh_selection_changed)
        self.chuc_vu_selector = selector(
            ["Tất cả", "Chức danh"],
            "Tất cả: giữ nguyên các dòng (KT., TM., ...)\nChức danh: chỉ dòng cuối",
        )
        self.chuc_vu_selector.currentTextChanged.connect(self.chuc_vu_selection_changed)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        self._metadata_grid = grid
        self._metadata_labels = {}

        def label(text, tip=""):
            lab = QLabel(text)
            lab.setProperty("role", "metadataLabel")
            if tip:
                lab.setToolTip(tip)
            self._metadata_labels[text] = lab
            return lab

        def row(r, text, value, tip="", extra=None):
            cell = QVBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(0)
            lab = label(text, tip)
            cell.addWidget(lab, 0, Qt.AlignLeft | Qt.AlignTop)
            if extra is not None:
                cell.addWidget(extra, 0, Qt.AlignLeft | Qt.AlignTop)
            cell.addStretch(1)
            grid.addLayout(cell, r, 0)
            grid.addWidget(value, r, 1, Qt.AlignTop)

        row(0, "Cơ quan ban hành", self.fields['cqbh'],
            "Hai dòng: cơ quan chủ quản ở trên, cơ quan ban hành ở dưới",
            extra=self.cqbh_selector)
        row(1, "Số hiệu", self.fields['so_ki_hieu'], "Số, ký hiệu của văn bản")
        row(2, "Ngày ban hành", self.fields['ngay_bh'],
            "Gõ ngày dạng dd/mm/yyyy hoặc chọn từ lịch\nAlt+↓")
        row(3, "Độ khẩn", self.fields['do_khan'])
        row(4, "Độ mật", self.fields['do_mat'])
        row(5, "Loại văn bản", self.fields['loai_vb'])
        row(6, "Nội dung chính", self.fields['nd_chinh'], "Trích yếu nội dung")
        row(7, "Nơi nhận", self.fields['noi_nhan'])
        row(8, "Chức vụ", self.fields['chuc_vu'], "Chức vụ người ký",
            extra=self.chuc_vu_selector)
        row(9, "Người ký", self.fields['chu_ky'])

        self.form_layout.addLayout(grid)
        self.form_layout.addStretch(1)

        # Tab follows the rows, each value before its line selector.
        chain = [self.fields['cqbh'], self.cqbh_selector, self.fields['so_ki_hieu'],
                 self.fields['ngay_bh'], self.fields['do_khan'], self.fields['do_mat'],
                 self.fields['loai_vb'], self.fields['nd_chinh'], self.fields['noi_nhan'],
                 self.fields['chuc_vu'], self.chuc_vu_selector, self.fields['chu_ky']]
        for first, second in zip(chain, chain[1:]):
            QWidget.setTabOrder(first, second)
        return form_widget

    def _build_action_bar(self):
        """Icon tools on the left; Đặt lại and the one primary button right."""
        action_bar = QFrame()
        self._action_bar = action_bar
        action_bar.setObjectName("ocr_action_bar")
        action_bar.setFixedHeight(44)
        layout = QHBoxLayout(action_bar)
        layout.setContentsMargins(8, 6, 12, 6)
        layout.setSpacing(2)

        def tool(attr, tip, slot):
            button = QToolButton()
            button.setAutoRaise(True)
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(30, 30)
            button.setToolTip(tip)
            button.setAccessibleName(tip.split("\n", 1)[0])
            button.setCursor(QCursor(Qt.PointingHandCursor))
            button.clicked.connect(slot)
            setattr(self, attr, button)
            layout.addWidget(button)
            return button

        tool("reocr_btn", "OCR lại\nNhận dạng lại văn bản này khi kết quả lỗi hoặc thiếu",
             lambda: self.reocr_requested.emit())
        tool("_copy_btn", "Sao chép toàn văn", self._copy_ocr_text)
        tool("_export_word_btn", "Xuất toàn văn ra Word (.docx)", self._export_ocr_to_word)
        layout.addStretch(1)

        self.reset_btn = QPushButton("Đặt lại")
        self.reset_btn.setProperty("variant", "ghost")
        self.reset_btn.setToolTip(
            "Bỏ các sửa đổi chưa lưu\n{}".format(command("reset").shortcut))
        self.reset_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.reset_btn.clicked.connect(self.reset_fields)
        layout.addWidget(self.reset_btn)
        layout.addSpacing(6)

        self.save_btn = QPushButton("Lưu thay đổi")
        self.save_btn.setProperty("variant", "primary")
        self.save_btn.setToolTip("Lưu thay đổi\n{}".format(command("save").shortcut))
        self.save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        # Never narrower than its label: the layout may not squeeze it.
        self.save_btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.save_btn.clicked.connect(self.save_changes)
        layout.addWidget(self.save_btn)
        self._set_dirty(False)
        return action_bar

    # ── Expand / collapse ───────────────────────────────────────
    def expanded_section(self):
        """None (split), TEXT or META."""
        return self._expanded

    def toggle_section(self, section):
        self.set_expanded(None if self._expanded == section else section)

    def set_expanded(self, section):
        if section not in (None, self.TEXT, self.META):
            raise ValueError("unknown section: {!r}".format(section))
        self._expanded = section
        self.fields['van_ban_dang_txt'].setVisible(section != self.META)
        self.scroll_area.setVisible(section != self.TEXT)
        self._text_header.set_expanded(section == self.TEXT)
        self._meta_header.set_expanded(section == self.META)
        for key, lines in self.SPLIT_LINES.items():
            self.fields[key].set_max_lines(None if section == self.META else lines)
        self._main_layout.setStretchFactor(self.scroll_area, 1 if section == self.META else 0)
        self._update_split()

    def _schedule_split(self):
        if not self._split_pending:
            self._split_pending = True
            QTimer.singleShot(0, self._update_split)

    def _update_split(self):
        """Size the metadata: its natural height, at most half the panel."""
        self._split_pending = False
        if self._expanded == self.META:
            self.scroll_area.setMinimumHeight(0)
            self.scroll_area.setMaximumHeight(_QWIDGETSIZE_MAX)
            return
        if self._expanded == self.TEXT:
            return
        natural = self._form_widget.sizeHint().height()
        fixed = (self._header_bar.height() + self._text_header.height()
                 + self._meta_header.height() + self._action_bar.height())
        available = max(0, self.height() - fixed)
        cap = max(140, int(available * self.SPLIT_META_SHARE))
        # A ceiling, not a fixed height: a fixed one would become the panel's
        # minimum and stop the window from getting shorter.
        self.scroll_area.setMinimumHeight(min(natural, 96))
        self.scroll_area.setMaximumHeight(max(96, min(natural, cap)))

    # ── Theme ───────────────────────────────────────────────────
    def apply_theme(self, name: str) -> None:
        """Recolour what the stylesheet cannot reach after a theme swap."""
        self._theme_name = "dark" if name == "dark" else "light"
        tokens = theme_tokens.colors_for(self._theme_name)
        editor = self.fields['van_ban_dang_txt']
        palette = editor.palette()
        palette.setColor(QPalette.Text, QColor(tokens['text']))
        palette.setColor(QPalette.Base, QColor(tokens['surface']))
        editor.setPalette(palette)
        self._render_ocr_text()
        self._paint_level_icons()
        if self._date_popup is not None:
            self._date_popup.apply_theme(self._theme_name)
        self._refresh_icons()

    def _refresh_icons(self):
        tokens = theme_tokens.colors_for(self._theme_name)
        tone = tokens['text_secondary']
        for button, name in ((self.reocr_btn, "scan"), (self._copy_btn, "copy"),
                             (self._export_word_btn, "word")):
            button.setIcon(icon(name, size=18, color=tone))
        self.reset_btn.setIcon(QIcon())
        self.save_btn.setIcon(QIcon())
        self._date_action.setIcon(icon("calendar", size=16, color=tokens['text_muted']))
        for header in (self._text_header, self._meta_header):
            header.apply_theme(self._theme_name)

    def _paint_level_icons(self):
        """A small dot in the NĐ30 colour of each urgency/secrecy level.

        "Không" has none, so the common value lines up with the other values.
        """
        tokens = theme_tokens.colors_for(self._theme_name)
        for key, levels in (('do_khan', self.URGENCY_LEVELS), ('do_mat', self.SECRECY_LEVELS)):
            combo = self.fields[key]
            for index, (_text, token) in enumerate(levels):
                if not token:
                    combo.setItemIcon(index, QIcon())
                    continue
                pixmap = QPixmap(16, 16)
                pixmap.fill(Qt.transparent)
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.Antialiasing, True)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(tokens[token]))
                painter.drawEllipse(1, 1, 14, 14)
                painter.end()
                pixmap.setDevicePixelRatio(2.0)
                combo.setItemIcon(index, QIcon(pixmap))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_split()
        QTimer.singleShot(0, self._sync_line_tooltips)

    # ── Date ────────────────────────────────────────────────────
    def show_date_picker(self):
        """Open the calendar popover under the release-date field."""
        field = self.fields['ngay_bh']
        if self._date_popup is None:
            self._date_popup = DatePopup(self)
            self._date_popup.date_chosen.connect(self._on_date_chosen)
        self._date_popup.apply_theme(self._theme_name)
        parsed = parse_document_date(field.text())
        current = QDate(parsed.year, parsed.month, parsed.day) if parsed else None
        self._date_popup.open_for(field, current)
        return self._date_popup

    def _on_date_chosen(self, chosen):
        field = self.fields['ngay_bh']
        field.setText(format_document_date(chosen.toPyDate()))
        field.setCursorPosition(0)
        self.content_changed()

    def _normalize_typed_date(self):
        """A date typed as 2/8/2017 or 02-08-2017 is stored as 02/08/2017."""
        field = self.fields['ngay_bh']
        if not field.isModified():
            return
        field.setModified(False)
        parsed = parse_document_date(field.text())
        if parsed is not None:
            formatted = format_document_date(parsed)
            if formatted != field.text():
                field.setText(formatted)

    def eventFilter(self, watched, event):
        if watched is self.fields.get('ngay_bh') and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_F4 or (
                    event.key() == Qt.Key_Down and event.modifiers() & Qt.AltModifier):
                self.show_date_picker()
                return True
        return super().eventFilter(watched, event)

    # ── Selectors ───────────────────────────────────────────────
    def chuc_vu_selection_changed(self, selection):
        """Handle change in chuc_vu selection dropdown"""
        if self.is_loading or not self.current_doc_data:
            return

        chuc_vu_text = self.current_doc_data[12] or ''
        lines = chuc_vu_text.strip().split('\n')

        if selection == "Tất cả":
            self.fields['chuc_vu'].setText(chuc_vu_text.strip())
        elif selection == "Chức danh" and lines:
            self.fields['chuc_vu'].setText(lines[-1].strip())

        self.content_changed()

    def cqbh_selection_changed(self, selection):
        """Handle change in cqbh selection dropdown"""
        if self.is_loading:
            return

        if self.full_cqbh_text:
            lines = self.full_cqbh_text.strip().split('\n')
            if selection == "Tất cả":
                self.fields['cqbh'].setText(self.full_cqbh_text.strip())
            elif selection == "Trên" and lines:
                self.fields['cqbh'].setText(lines[0].strip())
            elif selection == "Dưới" and len(lines) > 1:
                self.fields['cqbh'].setText(lines[1].strip())
            self.content_changed()

    def content_changed(self):
        """Enable save button when content changes"""
        if not self.is_loading and self.doc_id is not None:
            self._set_dirty(True)

    def _set_dirty(self, dirty):
        """Lưu and Đặt lại only offer themselves when there is a change."""
        self.save_btn.setEnabled(dirty)
        self.reset_btn.setEnabled(dirty)

    # ── Header ──────────────────────────────────────────────────
    def _refresh_header_live(self) -> None:
        """Update header bar from current field values as user types."""
        if getattr(self, 'is_loading', False):
            return
        try:
            self.set_header_info(
                self.fields['loai_vb'].text(),
                self.fields['so_ki_hieu'].text(),
                self._header_tooltip_file,
            )
        except (KeyError, AttributeError):
            pass

    def set_current_filename(self, filename: str) -> None:
        """Legacy filename-only setter — kept as tooltip source.

        Visible header content is set by set_header_info(loai_vb, so_ki_hieu).
        """
        self._header_tooltip_file = filename or ""
        self._doc_type_label.setToolTip(self._header_tooltip_file)
        self._doc_number_label.setToolTip("\n".join(
            part for part in (self._doc_number_label.full_text(), self._header_tooltip_file) if part))

    def set_header_info(self, loai_vb: str, so_ki_hieu: str,
                        filename: str = "") -> None:
        """Show the document's identity: its type, then its number in gold.

        The two are separate labels (no "·" string between them). With
        neither known the header shows the file name, or that nothing is open.
        """
        if filename:
            self._header_tooltip_file = filename
        name = self._header_tooltip_file
        loai = (loai_vb or "").strip()
        so = (so_ki_hieu or "").strip()
        if not loai and not so:
            self._doc_type_label.set_full_text(name or "Chưa mở văn bản")
        else:
            self._doc_type_label.set_full_text(loai)
        self._doc_type_label.setVisible(bool(self._doc_type_label.full_text()))
        self._doc_number_label.set_full_text(so)
        self._doc_number_label.setVisible(bool(so))
        self._doc_type_label.setToolTip(name)
        self._doc_number_label.setToolTip("\n".join(part for part in (so, name) if part))

    # ── Data ────────────────────────────────────────────────────
    def load_data(self, doc_data):
        """Load document data into the editor fields"""
        self.is_loading = True

        if doc_data is None:
            self.clear()
            self.is_loading = False
            return

        self.current_doc_data = doc_data
        # Lưu document_id (index 1) thay vì sử dụng ID từ phiên bản (index 0)
        # Ở đây doc_data[1] là document_id, doc_data[0] là id của phiên bản
        if len(doc_data) > 1:
            self.doc_id = doc_data[1]  # Sử dụng document_id thay vì version id
        else:
            self.doc_id = doc_data[0]  # Sử dụng ID phiên bản nếu không có document_id

        # Build CQBH from parts
        cqbh_tren = doc_data[3] or ""
        cqbh_duoi = doc_data[4] or ""
        self.full_cqbh_text = "\n".join([part for part in [cqbh_tren, cqbh_duoi] if part])

        # Keep track of parts separately
        self.cqbh_selector.setCurrentText("Tất cả")
        self.chuc_vu_selector.setCurrentText("Tất cả")
        self.fields['cqbh'].setText(self.full_cqbh_text)

        # Set other fields - theo thứ tự mới: cơ quan ban hành, số ký hiệu, ngày ban hành, độ khẩn, độ mật, loại văn bản
        self.fields['so_ki_hieu'].setText(doc_data[5] or "")
        self.fields['ngay_bh'].setText(doc_data[6] or "")

        # Set độ khẩn with proper display
        do_khan = doc_data[7] or "Không"
        index = self.fields['do_khan'].findText(do_khan)
        if index >= 0:
            self.fields['do_khan'].setCurrentIndex(index)
        else:
            self.fields['do_khan'].setCurrentIndex(0)  # Default to "Không"

        # Set độ mật with proper display
        do_mat = doc_data[8] or "Không"
        index = self.fields['do_mat'].findText(do_mat)
        if index >= 0:
            self.fields['do_mat'].setCurrentIndex(index)
        else:
            self.fields['do_mat'].setCurrentIndex(0)  # Default to "Không"

        self.fields['loai_vb'].setText(doc_data[9] or "")
        self.fields['nd_chinh'].setText(doc_data[10] or "")
        self.fields['noi_nhan'].setText(doc_data[11] or "")
        self.fields['chuc_vu'].setText(doc_data[12] or "")
        self.fields['chu_ky'].setText(doc_data[13] or "")

        # Load van_ban_dang_txt - toàn bộ text OCR.
        # Schema migration quirk (2026-04-19): column index differs between
        # databases depending on CREATE TABLE vs ALTER TABLE order.
        #   • Fresh DB (CREATE order): [14]=van_ban_dang_txt [15]=modified_by [16]=created_at
        #   • Legacy DB (ALTER added van_ban_dang_txt last): [14]=modified_by [15]=created_at [16]=van_ban_dang_txt
        # Bug: hardcoded [16] showed timestamp in fresh DBs; hardcoded [14]
        # showed user-name in legacy DBs. Pick the index that looks like OCR
        # content (long text, may contain <div>, never a "YYYY-MM-DD HH:MM:SS"
        # timestamp or a 10-char username).
        raw_text = ""
        for _idx in (14, 15, 16):
            if len(doc_data) <= _idx:
                continue
            _cand = doc_data[_idx]
            if not _cand:
                continue
            _s = str(_cand)
            # Heuristic: OCR text is long (>40 chars) OR contains HTML markers.
            # Exclude obvious timestamps and short strings (user names).
            import re as _re
            if _re.match(r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}', _s):
                continue  # timestamp
            if len(_s) > 40 or '<div' in _s or '\n' in _s:
                raw_text = _s
                break
        # Chandra HTML output (contains <div> tags) renders as HTML.
        is_chandra_html = bool(raw_text) and '<div' in raw_text and (
            'data-label' in raw_text or 'data-bbox' in raw_text or 'data=' in raw_text)
        self._van_ban_html_cache = raw_text if is_chandra_html else ""
        self._van_ban_plain = "" if is_chandra_html else raw_text
        self._highlight_terms = []
        self._render_ocr_text()
        self.fields['van_ban_dang_txt'].verticalScrollBar().setValue(0)

        # Refresh the header with loai_vb + so_ki_hieu; the file name stays
        # available as its tooltip.
        self.set_header_info(
            doc_data[9] if len(doc_data) > 9 else "",
            doc_data[5] if len(doc_data) > 5 else "",
        )

        # Reset state
        self.is_loading = False
        self._set_dirty(False)
        self._show_line_starts()
        self.scroll_area.verticalScrollBar().setValue(0)
        QTimer.singleShot(0, self._finish_loading_layout)

    def _finish_loading_layout(self) -> None:
        self._update_split()
        self._show_line_starts()
        self.scroll_area.verticalScrollBar().setValue(0)

    def _show_line_starts(self):
        """Every value shows its start, not its end."""
        for key in self.LINE_FIELDS:
            edit = self.fields[key]
            edit.setCursorPosition(0)
            self._sync_line_tooltip(edit)
        for key in (*self.SPLIT_LINES, *self.SINGLE_VALUE_FIELDS):
            self.fields[key].setCursorPosition(0)
            self.fields[key].verticalScrollBar().setValue(0)

    def _sync_line_tooltips(self):
        for key in self.LINE_FIELDS:
            self._sync_line_tooltip(self.fields[key])

    @staticmethod
    def _sync_line_tooltip(edit):
        """The full value as tooltip, only when the field cuts it."""
        text = edit.text()
        # leave room for the text margins and any icon inside the field
        room = edit.contentsRect().width() - 6 - 24 * len(edit.actions())
        cut = bool(text) and edit.fontMetrics().horizontalAdvance(text) > room
        edit.setToolTip(text if cut else "")

    def get_current_data(self):
        """Get current data from all fields"""
        data = {}

        # Handle CQBH based on selection
        lines = self.full_cqbh_text.strip().split('\n')
        selection = self.cqbh_selector.currentText()

        if selection == "Trên":
            data['cqbh_tren'] = self.fields['cqbh'].text()
            data['cqbh_duoi'] = lines[1] if len(lines) > 1 else ''
        elif selection == "Dưới":
            data['cqbh_tren'] = lines[0] if lines else ''
            data['cqbh_duoi'] = self.fields['cqbh'].text()
        else:
            parts = self.fields['cqbh'].text().split('\n')
            data['cqbh_tren'] = parts[0] if parts else ''
            data['cqbh_duoi'] = parts[1] if len(parts) > 1 else ''

        # Get other fields
        for field_id, editor in self.fields.items():
            if field_id != 'cqbh':
                if field_id == 'do_khan' or field_id == 'do_mat':
                    # Get selected value from ComboBox
                    data[field_id] = editor.currentText()
                elif field_id == 'van_ban_dang_txt' and self._van_ban_html_cache:
                    data[field_id] = self._van_ban_html_cache
                elif isinstance(editor, GrowingTextEdit):
                    # text() keeps the value exactly (toPlainText would
                    # replace non-breaking spaces).
                    data[field_id] = editor.text().strip()
                elif isinstance(editor, QTextEdit):
                    data[field_id] = editor.toPlainText().strip()
                else:
                    data[field_id] = editor.text().strip()

        return data

    def save_changes(self):
        """Save changes to current document"""
        if self.doc_id is None:
            QMessageBox.warning(None, "Cảnh báo", "Không có tài liệu nào được chọn để lưu.")
            return

        try:
            # Kiểm tra document tồn tại
            doc_info = self.db.get_document_info(self.doc_id)
            if not doc_info:
                # Hiển thị cảnh báo hữu ích hơn
                QMessageBox.warning(None, "Cảnh báo",
                          f"Không tìm thấy tài liệu với ID {self.doc_id}.\nVui lòng làm mới danh sách tài liệu và thử lại.")
                self._set_dirty(False)
                return

            data = self.get_current_data()
            # Emit update signal
            self.ocr_updated.emit(self.doc_id, data)
            self._set_dirty(False)

        except Exception as e:
            logger.error(f"Error saving changes: {str(e)}")
            QMessageBox.critical(None, "Lỗi",
                            f"Lỗi khi lưu thay đổi: {str(e)}\nVui lòng thử lại.")

    def reset_fields(self):
        """Reset fields to original values"""
        if self.doc_id is not None:
            self.load_data(self.current_doc_data)

    def update_suggestions(self, db):
        """Update suggestions for all fields from database"""
        try:
            for field_id, editor in self.fields.items():
                if isinstance(editor, (QLineEdit, GrowingTextEdit)):
                    suggestions = db.get_suggestions(field_id)
                    completer = QCompleter(suggestions)
                    completer.setCaseSensitivity(Qt.CaseInsensitive)
                    completer.setFilterMode(Qt.MatchContains)
                    editor.setCompleter(completer)
        except Exception as e:
            logger.error(f"Error updating suggestions: {str(e)}")

    def clear(self):
        """Clear all fields"""
        self.is_loading = True
        try:
            self.doc_id = None
            self.full_cqbh_text = ""
            self.current_doc_data = None
            self._van_ban_html_cache = ""
            self._van_ban_plain = ""
            self._highlight_terms = []

            for field_id, editor in self.fields.items():
                if isinstance(editor, QComboBox):
                    editor.setCurrentIndex(0)  # Reset to first item for all ComboBoxes
                elif isinstance(editor, QTextEdit):
                    editor.clear()
                else:
                    editor.setText("")

            self._header_tooltip_file = ""
            self.set_header_info("", "", "")
            self._set_dirty(False)
        finally:
            self.is_loading = False

    # ── Full text ───────────────────────────────────────────────
    def _render_ocr_text(self):
        """Show the full text for the current theme, keeping the scroll place.

        Chandra HTML is rendered with the theme's tokens (table rules); the
        text colour itself is the widget's, so a theme swap recolours it.
        Search highlights are applied again on top.
        """
        editor = self.fields['van_ban_dang_txt']
        bar = editor.verticalScrollBar()
        position = bar.value()
        if self._van_ban_html_cache:
            editor.setHtml(self._build_display_html(
                self._van_ban_html_cache, theme_tokens.colors_for(self._theme_name)))
        elif self._van_ban_plain:
            editor.setPlainText(self._van_ban_plain)
        else:
            editor.clear()
        if self._highlight_terms:
            self._apply_highlights(self._highlight_terms)
        bar.setValue(position)

    def _apply_highlights(self, terms):
        tokens = theme_tokens.colors_for(self._theme_name)
        mark = QTextCharFormat()
        mark.setBackground(QColor(tokens['accent_gold_soft']))
        mark.setForeground(QColor(tokens['text']))
        document = self.fields['van_ban_dang_txt'].document()
        for term in terms:
            cursor = QTextCursor(document)
            while True:
                cursor = document.find(term, cursor)
                if cursor.isNull():
                    break
                cursor.mergeCharFormat(mark)

    def highlight_keywords(self, keywords: list):
        """Mark search keywords in the full text, in the gold of the theme.

        The text is rendered again first, so earlier marks go away and
        Chandra's formatting (headings, tables) stays.
        """
        if 'van_ban_dang_txt' not in self.fields:
            return
        terms = [keyword for keyword in (keywords or []) if keyword]
        if not terms or not self.fields['van_ban_dang_txt'].toPlainText():
            return
        self._highlight_terms = terms
        self._render_ocr_text()

    def _copy_ocr_text(self):
        """Copy OCR text content to clipboard"""
        text = self.fields['van_ban_dang_txt'].toPlainText()
        if text.strip():
            QApplication.clipboard().setText(text)
            # Show feedback via tooltip since OCRResultEditor has no statusBar
            from PyQt5.QtWidgets import QToolTip
            QToolTip.showText(QCursor.pos(), "Đã sao chép nội dung văn bản", self, self.rect(), 2000)
        else:
            from PyQt5.QtWidgets import QToolTip
            QToolTip.showText(QCursor.pos(), "Không có nội dung để sao chép", self, self.rect(), 2000)

    @staticmethod
    def _build_display_html(chandra_html: str, colors=None) -> str:
        """Delegate to pure renderer in display_html_renderer module.

        Kept as staticmethod for backward-compat with call sites that use
        MainWindow._build_display_html(...). Actual rendering logic is in
        desktop/display_html_renderer.py for isolated unit-testability
        (no PyQt5/YOLO/torch init required in tests). `colors` is a
        theme_tokens palette; the light one when omitted.
        """
        from display_html_renderer import build_display_html
        return build_display_html(chandra_html, colors)

    def _export_ocr_to_word(self):
        """Export OCR to Word — uses html_to_docx for Chandra HTML, plain text fallback."""
        html_content = getattr(self, '_van_ban_html_cache', '')
        plain_text = self.fields['van_ban_dang_txt'].toPlainText()

        if not plain_text.strip() and not html_content.strip():
            QMessageBox.information(self, "Thông báo", "Không có nội dung văn bản để xuất.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Xuất văn bản Word", "",
            "Word Document (*.docx)"
        )
        if not file_path:
            return
        if not file_path.endswith('.docx'):
            file_path += '.docx'

        try:
            if html_content and '<div' in html_content:
                # Chandra HTML path — formatted DOCX with structure
                from html_to_docx import convert_html_to_docx
                # Pass original image for stamp cropping if available
                original_img = None
                if hasattr(self, 'processed_images') and self.processed_images:
                    original_img = self.processed_images[0]
                convert_html_to_docx(html_content, file_path, original_image=original_img)
            else:
                # Plain text fallback
                from docx import Document
                from docx.shared import Pt
                doc = Document()
                style = doc.styles['Normal']
                style.font.name = 'Times New Roman'
                style.font.size = Pt(14)
                for line in plain_text.split('\n'):
                    doc.add_paragraph(line).style = doc.styles['Normal']
                doc.save(file_path)

            QMessageBox.information(self, "Thành công",
                f"Đã xuất văn bản ra:\n{file_path}")
        except ImportError as e:
            QMessageBox.critical(self, "Lỗi",
                f"Thiếu thư viện: {e}\nCài đặt: pip install python-docx beautifulsoup4")
        except Exception as e:
            QMessageBox.critical(self, "Lỗi",
                f"Không thể xuất văn bản: {str(e)}")

#############################
#       Dialog Classes      #
#############################
# The secondary surfaces of the 2026-10 redesign: sheets of the surface
# colour, styled by object name in the "AREA: dialogs" section of
# devtools/theme_template.qss. What a stylesheet cannot reach (the painted
# activity ring, icons) takes its colours from theme_tokens in code.
import html  # noqa: E402  (dialogs only)
from PyQt5.QtCore import QRectF  # noqa: E402


def _mark_colour(theme=None):
    """The olive of the app mark, as the toolbar paints it: the brand fill in
    the light theme, its text tone in the dark one (where the fill would sink
    into the surface)."""
    theme = theme or AppThemeManager.instance().current
    tokens = theme_tokens.colors_for(theme)
    return tokens["primary_muted"] if theme == "dark" else tokens["primary"]


class SpinnerWidget(QWidget):
    """The activity ring of the progress window.

    A quarter arc turns over a hairline track while the work runs; when it is
    done the ring becomes the Fluent check in the success colour. Colours are
    read from the tokens at paint time, so a theme switch needs nothing else.
    """

    def __init__(self, size=20, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._size = size
        self._spinning = True
        self._completed = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)
        self._timer.start(30)  # ~33 FPS

    def _rotate(self):
        if self._spinning:
            self._angle = (self._angle + 8) % 360
            self.update()

    def stop(self, done=True):
        """Stop turning; with done (the default) show the check."""
        self._spinning = False
        self._completed = done
        self._timer.stop()
        self.update()

    def paintEvent(self, event):
        theme = AppThemeManager.instance().current
        tokens = theme_tokens.colors_for(theme)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._completed:
            icon("status-ok", size=self._size, color=tokens["success"]).paint(painter, self.rect())
            painter.end()
            return
        pen_width = max(2.0, self._size / 10)
        inset = pen_width / 2 + 0.5
        ring = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        painter.setPen(QPen(QColor(tokens["border"]), pen_width))
        painter.drawEllipse(ring)
        painter.setPen(QPen(QColor(_mark_colour(theme)), pen_width, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(ring, -self._angle * 16, 90 * 16)
        painter.end()


class ProgressDialog(QDialog):
    """The OCR progress window: what runs, how far, and how to stop it.

    The activity ring beside a short title and a reassuring line, the thin
    progress line of the theme, then the current step and the percent as two
    separate parts. One button: Hủy while it runs, Đóng (the one primary
    button) once it is done.
    """
    canceled = pyqtSignal()

    # The line under the title changes as the wait gets longer (seconds).
    WAIT_MESSAGES = (
        (10, "Tài liệu đang được xử lý, đồng chí vui lòng chờ giây lát."),
        (30, "Đang phân tích nội dung văn bản, vui lòng chờ thêm..."),
        (60, "Văn bản phức tạp cần thêm thời gian, cảm ơn đồng chí đã kiên nhẫn."),
    )

    def __init__(self, total: int, title: str = "Đang xử lý", parent=None):
        super().__init__(parent)
        self.total = max(1, total)
        self.was_canceled = False
        self.is_completed = False
        self._smooth_timer = None
        self._smooth_target = 0
        self.setup_ui(title)

    def setup_ui(self, title: str):
        self.setObjectName("progressDialog")
        self.setWindowTitle(title)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setFixedSize(440, 182)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.spinner = SpinnerWidget(20, self)
        head.addWidget(self.spinner, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        self.task_label = QLabel(title)
        self.task_label.setObjectName("progressTitle")
        words.addWidget(self.task_label)
        self.time_label = QLabel(self.WAIT_MESSAGES[0][1])
        self.time_label.setObjectName("progressMessage")
        self.time_label.setWordWrap(True)
        words.addWidget(self.time_label)
        head.addLayout(words, 1)
        layout.addLayout(head)
        layout.addSpacing(16)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("progressBar")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        layout.addSpacing(6)

        status = QHBoxLayout()
        status.setSpacing(12)
        self.status_label = ElidedLabel()
        self.status_label.setObjectName("progressStatus")
        status.addWidget(self.status_label, 1)
        self.pct_label = QLabel("0%")
        self.pct_label.setObjectName("progressPercent")
        status.addWidget(self.pct_label)
        layout.addLayout(status)
        self._set_status("Đang chuẩn bị...")
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()
        self.cancel_btn = QPushButton("Hủy")
        # A stray Enter must not stop the OCR; a click or Space does.
        self.cancel_btn.setAutoDefault(False)
        self.cancel_btn.clicked.connect(self.cancel_operation)
        buttons.addWidget(self.cancel_btn)
        self.ok_btn = QPushButton("Đóng")
        self.ok_btn.setProperty("variant", "primary")
        self.ok_btn.clicked.connect(self.accept)
        self.ok_btn.setVisible(False)
        buttons.addWidget(self.ok_btn)
        layout.addLayout(buttons)

        self.start_time = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer)

    def _set_status(self, text):
        """The current step, on one line; the whole of it in the tooltip."""
        self.status_label.set_full_text(text)
        self.status_label.setToolTip(text)

    def showEvent(self, event):
        """Start timer khi dialog hiển thị (hoạt động với cả show() và exec_())"""
        if self.start_time is None:
            self.start_time = time.time()
            self.timer.start(1000)
        super().showEvent(event)

    def cancel_operation(self):
        """Handle cancel button click"""
        self.was_canceled = True
        self.task_label.setText("Đang hủy...")
        self.spinner.stop(done=False)
        self.canceled.emit()
        self.reject()

    def _on_timer(self):
        """A calmer line under the title as the wait gets longer (no countdown)."""
        if self.start_time is None or self.is_completed:
            return
        elapsed = int(time.time() - self.start_time)
        for limit, message in self.WAIT_MESSAGES:
            if elapsed < limit:
                self.time_label.setText(message)
                return
        self.time_label.setText(
            f"Đã xử lý được {elapsed // 60} phút, hệ thống đang nỗ lực hoàn thành...")

    def update_progress(self, current: int, total: int = None, message: str = None):
        if self.is_completed:
            return
        if total is not None:
            self.total = max(1, total)
        target_pct = min(100.0, (current / self.total) * 100) if self.total > 0 else 0

        # Smooth animation: gradually fill toward target instead of jumping
        if target_pct > self.progress_bar.value():
            self._smooth_target = target_pct
            if self._smooth_timer is None:
                self._smooth_timer = QTimer(self)
                self._smooth_timer.timeout.connect(self._animate_progress)
                self._smooth_timer.start(50)  # 50ms ticks = 20fps smooth

        self._set_status(message or "Đang xử lý...")
        self.pct_label.setText(f"{target_pct:.0f}%")

    def _animate_progress(self):
        """Smoothly animate progress bar toward target."""
        if self.is_completed:
            if self._smooth_timer is not None:
                self._smooth_timer.stop()
            return
        current = self.progress_bar.value()
        target = self._smooth_target
        if current < target:
            # Ease-out: move faster when far from target, slower when close
            step = max(1, int((target - current) * 0.15))
            self.progress_bar.setValue(min(current + step, int(target)))

    def mark_completed(self):
        """Done: the check, the time it took, and Đóng."""
        if self.is_completed:
            return
        self.is_completed = True
        self.timer.stop()
        if self._smooth_timer is not None:
            self._smooth_timer.stop()
        self.progress_bar.setValue(100)
        self.pct_label.setText("100%")
        self.spinner.stop()

        elapsed = time.time() - self.start_time if self.start_time else 0
        seconds = f"{elapsed:.1f}".replace(".", ",")
        self.task_label.setText("Hoàn thành")
        self.time_label.setText(f"Văn bản đã được xử lý trong {seconds} giây.")
        self._set_status("")
        self.cancel_btn.setVisible(False)
        self.ok_btn.setVisible(True)
        self.ok_btn.setDefault(True)
        self.ok_btn.setFocus()

    def closeEvent(self, event):
        """Handle dialog close"""
        self.timer.stop()
        self.spinner._timer.stop()
        super().closeEvent(event)


class PreviewDialog(QDialog):
    """A look at a file before it is analysed.

    One strip above the page, like the workspace tab bar: the file name and
    size at the left, the viewer's page navigation at the right. At the foot
    Hủy and the one primary action. A file that cannot be read shows the
    viewer's error state, the same as in the workspace, without navigation.
    """

    def __init__(self, pdf_path: str, parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.setup_ui()

    def setup_ui(self):
        self.setObjectName("previewDialog")
        self.setWindowTitle("Xem trước tệp")
        self.setWindowModality(Qt.ApplicationModal)

        # Lấy kích thước màn hình hoặc cửa sổ cha để tạo dialog lớn hơn
        if self.parent():
            parent_size = self.parent().size()
            # Dialog chiếm 90% chiều cao của cửa sổ cha
            new_height = int(parent_size.height() * 0.9)
            new_width = int(new_height * 0.75)  # Tỷ lệ 4:3
        else:
            # Fallback nếu không có parent
            screen = QApplication.primaryScreen().geometry()
            new_height = int(screen.height() * 0.85)
            new_width = int(new_height * 0.75)

        self.resize(max(800, new_width), max(600, new_height))
        self.setMinimumSize(800, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        exists = os.path.exists(self.pdf_path)
        header = QFrame()
        header.setObjectName("previewHeader")
        header.setMinimumHeight(38)  # the same with or without navigation
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 4, 4, 4)
        row.setSpacing(8)
        name = ElidedLabel(os.path.basename(self.pdf_path))
        name.setObjectName("previewFileName")
        name.setToolTip(self.pdf_path)
        row.addWidget(name)
        if exists:
            size_mb = os.path.getsize(self.pdf_path) / (1024 * 1024)
            size = QLabel(f"{size_mb:.1f} MB".replace(".", ","))
            size.setObjectName("previewFileSize")
            row.addWidget(size)
        row.addStretch(1)
        layout.addWidget(header)

        # The viewer shows the page, or its own error state; its navigation
        # strip moves into the header row.
        self.viewer = PDFViewer()
        loaded = self.viewer.load_pdf(self.pdf_path)
        row.addWidget(self.viewer.take_nav_bar())
        self.viewer.set_nav_allowed(bool(loaded))
        layout.addWidget(self.viewer, 1)

        footer = QFrame()
        footer.setObjectName("previewFooter")
        buttons = QHBoxLayout(footer)
        buttons.setContentsMargins(16, 10, 16, 10)
        buttons.setSpacing(8)
        buttons.addStretch()
        cancel_btn = QPushButton("Hủy")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        self.process_btn = QPushButton("Bắt đầu phân tích")
        self.process_btn.setProperty("variant", "primary")
        self.process_btn.setToolTip("Nhận dạng văn bản và trích các trường thông tin")
        self.process_btn.clicked.connect(self.accept)
        self.process_btn.setEnabled(exists)
        self.process_btn.setDefault(exists)
        buttons.addWidget(self.process_btn)
        layout.addWidget(footer)
        # The page has the keyboard first (not the page-number field).
        self.viewer.setFocus()


#############################
#  Keyword Search Dialog    #
#############################
class SearchDialog(QDialog):
    """Keyword search over the full text of the library.

    One field (how to write the keywords is its tooltip, not a line on
    screen), the primary Tìm kiếm button, the matches with how often the
    keywords occur, and a short count underneath. A match opens with its
    row button or a double click.
    """
    document_selected = pyqtSignal(int, list)  # doc_id, keywords

    # The id column stays for the code (UserRole) and is hidden.
    COLUMNS = ("ID", "Văn bản", "Số lần", "")

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setObjectName("searchDialog")
        self.setWindowTitle("Tìm kiếm văn bản")
        self.resize(720, 480)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)

        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchField")
        self.search_input.setPlaceholderText("Ví dụ: quyết định, bổ nhiệm, nhân sự")
        self.search_input.setToolTip(chrome_tooltip(
            "Từ khóa cần tìm, cách nhau bởi dấu phẩy", "Enter"))
        tokens = theme_tokens.colors_for(AppThemeManager.instance().current)
        self.search_input.addAction(
            icon("search", size=16, color=tokens["text_muted"]), QLineEdit.LeadingPosition)
        self.search_input.returnPressed.connect(self.do_search)
        input_layout.addWidget(self.search_input, 1)

        self.search_btn = QPushButton("Tìm kiếm")
        self.search_btn.setProperty("variant", "primary")
        self.search_btn.clicked.connect(self.do_search)
        input_layout.addWidget(self.search_btn)
        layout.addLayout(input_layout)

        table = QTableWidget()
        table.setObjectName("searchResults")
        table.setColumnCount(len(self.COLUMNS))
        table.setHorizontalHeaderLabels(self.COLUMNS)
        table.horizontalHeaderItem(2).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        table.setColumnHidden(0, True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(30)
        table.setSortingEnabled(True)
        header = table.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        table.setColumnWidth(2, 72)
        table.setColumnWidth(3, 40)
        table.cellDoubleClicked.connect(self._open_row)
        self.result_table = table
        layout.addWidget(table, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("searchStatus")
        layout.addWidget(self.status_label)

        self.search_input.setFocus()

    def _show_status(self, text, danger=False):
        self.status_label.setText(text)
        self.status_label.setProperty("tone", "danger" if danger else "")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def do_search(self):
        """Thực hiện tìm kiếm"""
        query_text = self.search_input.text().strip()
        if not query_text:
            self._show_status("Nhập từ khóa để tìm.")
            return

        # Đổi separator từ , sang ; cho tương thích với db.search_documents
        keywords = [kw.strip() for kw in query_text.split(',') if kw.strip()]
        db_query = ';'.join(keywords)

        try:
            results = self.db.search_documents(db_query, "all")
            tokens = theme_tokens.colors_for(AppThemeManager.instance().current)

            self.result_table.setSortingEnabled(False)
            self.result_table.setRowCount(len(results))
            for row, (doc, stats) in enumerate(results):
                self._fill_row(row, doc, stats, tokens)
            self.result_table.setSortingEnabled(True)
            # Sắp xếp theo tần suất giảm dần
            self.result_table.sortItems(2, Qt.DescendingOrder)

            found = (f"Tìm thấy {len(results)} văn bản" if results
                     else "Không tìm thấy văn bản nào")
            self._show_status(f"{found} với từ khóa: {', '.join(keywords)}")
        except Exception as e:
            logger.error(f"Search error: {str(e)}")
            self._show_status(f"Lỗi tìm kiếm: {str(e)}", danger=True)

    def _fill_row(self, row, doc, stats, tokens):
        doc_id = doc[0]
        id_item = QTableWidgetItem(str(doc_id))
        id_item.setData(Qt.UserRole, doc_id)
        self.result_table.setItem(row, 0, id_item)

        # The document number when the OCR found one, like the library; the
        # file name otherwise, in the quieter tone.
        symbol = doc[8] if len(doc) > 8 and doc[8] else ""
        name_item = QTableWidgetItem(symbol or doc[2])
        if not symbol:
            name_item.setForeground(QColor(tokens["text_secondary"]))
        tooltip_parts = [doc[2], doc[1]]
        if stats['snippets']:
            tooltip_parts.append("")
            for snippet in stats['snippets'][:5]:
                tooltip_parts.append(f"Dòng {snippet['line']}: {snippet['context']}")
        name_item.setToolTip('\n'.join(tooltip_parts))
        self.result_table.setItem(row, 1, name_item)

        freq_item = QTableWidgetItem()
        freq_item.setData(Qt.DisplayRole, stats['total_frequency'])
        freq_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if stats['keyword_frequencies']:
            breakdown = [f"{kw}: {count}" for kw, count in stats['keyword_frequencies'].items()]
            freq_item.setToolTip("Số lần xuất hiện\n" + '\n'.join(breakdown))
        self.result_table.setItem(row, 2, freq_item)

        open_btn = QToolButton()
        open_btn.setObjectName("searchOpenButton")
        open_btn.setAutoRaise(True)
        open_btn.setIcon(chrome_icon("open", 16))
        open_btn.setToolTip("Mở văn bản này")
        open_btn.setAccessibleName("Mở văn bản này")
        open_btn.setCursor(QCursor(Qt.PointingHandCursor))
        open_btn.clicked.connect(lambda checked, did=doc_id: self.preview_document(did))
        cell = QWidget()
        centred = QHBoxLayout(cell)
        centred.setContentsMargins(0, 0, 0, 0)
        centred.addWidget(open_btn, 0, Qt.AlignCenter)
        self.result_table.setCellWidget(row, 3, cell)

    def _open_row(self, row, _column):
        item = self.result_table.item(row, 0)
        if item is not None:
            self.preview_document(item.data(Qt.UserRole))

    def preview_document(self, doc_id):
        """Mở document và đóng dialog, truyền kèm keywords"""
        keywords = [kw.strip() for kw in self.search_input.text().split(',') if kw.strip()]
        self.document_selected.emit(doc_id, keywords)
        self.accept()


#############################
#       About Dialog        #
#############################
class AboutDialog(QDialog):
    """About OCR VBHC, in the words of the home page's about sheet
    (frontend/landing/index.html, <dialog class="about">): the name and
    version beside the app mark, one paragraph on what it does, a few facts,
    the licences of the bundled font and icons, and the note on the
    assistant.

    The mark is the app icon (assets/app_icon_256.png), as on the home
    page's about sheet and its top bar.
    """

    WIDTH = 540
    SUMMARY = ("Số hóa văn bản hành chính trên máy: nhận dạng PDF và ảnh, trích các "
               "trường thông tin theo Nghị định 30, lập chỉ mục và hỏi đáp có trích dẫn.")
    FACTS = (
        ("Người phát triển", "Trần Hậu Mạnh"),
        ("Quyền riêng tư", "Tệp, chỉ mục và hội thoại ở lại trên máy; không gửi lên "
                           "dịch vụ đám mây."),
        ("Trí tuệ nhân tạo", "Chandra nhận dạng văn bản; Qwen3 4B/8B trả lời và điều phối "
                             "công cụ cho trợ lý pháp luật."),
        ("Kho tri thức", "Tìm kiếm toàn văn FTS5 tiếng Việt, sqlite-vec, xếp hạng lại "
                         "ngữ nghĩa và đồ thị quan hệ pháp lý."),
    )
    # From THIRD_PARTY_NOTICES.md, shipped beside the app.
    LICENCES = ("Giấy phép",
                "Phông Be Vietnam Pro (SIL OFL 1.1), biểu tượng Fluent UI của Microsoft "
                "(MIT). Chi tiết trong THIRD_PARTY_NOTICES.md.")
    NOTE = ("Câu trả lời của trợ lý chỉ hỗ trợ tra cứu, không thay thế ý kiến chuyên môn "
            "hoặc tư vấn pháp lý.")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("aboutDialog")
        self.setWindowTitle("Về ứng dụng")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(0)

        heading = QHBoxLayout()
        heading.setSpacing(12)
        self._mark = QLabel()
        self._mark.setObjectName("aboutMark")
        self._mark.setFixedSize(36, 36)
        heading.addWidget(self._mark, 0, Qt.AlignVCenter)
        names = QVBoxLayout()
        names.setSpacing(0)
        self._title = QLabel("OCR VBHC")
        self._title.setObjectName("aboutTitle")
        names.addWidget(self._title)
        version = QLabel(f"Phiên bản {APP_VERSION}")
        version.setObjectName("aboutVersion")
        names.addWidget(version)
        heading.addLayout(names, 1)
        layout.addLayout(heading)
        layout.addSpacing(16)

        summary = self._paragraph(self.SUMMARY)
        summary.setObjectName("aboutSummary")
        layout.addWidget(summary)
        layout.addSpacing(16)

        facts = QFrame()
        facts.setObjectName("aboutFacts")
        rows = QVBoxLayout(facts)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(0)
        for term_text, value_text in self.FACTS + (self.LICENCES,):
            row = QFrame()
            row.setProperty("role", "aboutFact")
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 8, 0, 8)
            line.setSpacing(16)
            term = QLabel(term_text)
            term.setProperty("role", "aboutTerm")
            term.setFixedWidth(120)
            term.setWordWrap(True)
            line.addWidget(term, 0, Qt.AlignTop)
            value = self._paragraph(value_text)
            value.setProperty("role", "aboutValue")
            line.addWidget(value, 1)
            rows.addWidget(row)
        layout.addWidget(facts)
        layout.addSpacing(16)

        note = QLabel(self.NOTE)
        note.setObjectName("aboutNote")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addSpacing(16)

        buttons = QHBoxLayout()
        buttons.addStretch()
        close_btn = QPushButton("Đóng")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self.apply_theme()
        AppThemeManager.instance().theme_changed.connect(self.apply_theme)
        self.ensurePolished()
        self.setFixedSize(self.WIDTH, layout.totalHeightForWidth(self.WIDTH))

    @staticmethod
    def _paragraph(text):
        """A wrapped, selectable paragraph with the home page's looser
        leading (QSS has no line-height; Qt rich text does)."""
        label = QLabel("<p style='line-height:130%; margin:0'>{}</p>".format(html.escape(text)))
        label.setTextFormat(Qt.RichText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label

    def apply_theme(self, theme=None):
        """The app icon, as on the home page's about sheet, and the name in
        the olive of the theme."""
        colour = _mark_colour(theme)
        mark = QPixmap(str(Path(__file__).parent / "assets" / "app_icon_256.png"))
        ratio = self.devicePixelRatioF() or 1.0
        mark = mark.scaled(int(36 * ratio), int(36 * ratio), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        mark.setDevicePixelRatio(ratio)
        self._mark.setPixmap(mark)
        self._title.setStyleSheet(f"color: {colour};")


class MainWindow(QMainWindow):
    """Main application window"""
    home_requested = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hệ thống OCR Văn Bản")
        # Before any widget exists: scrolling over Độ khẩn / Độ mật (and every
        # other combo or spin box) must scroll, not change the value.
        install_wheel_guard(QApplication.instance())

        self.setWindowIcon(QApplication.instance().windowIcon())

        # Get display information
        screen = QApplication.primaryScreen().geometry()
        window_width = int(min(1280, screen.width() * 0.8))
        window_height = int(min(900, screen.height() * 0.8))
        self.setGeometry(
            int((screen.width() - window_width) // 2),
            int((screen.height() - window_height) // 2),
            window_width,
            window_height
        )
        
        # Set minimum size
        self.setMinimumSize(1024, 768)

        # Initialize settings
        self.settings = QSettings("OCRApp", "DocumentManagement")
        self.load_window_settings()

        self._theme_mgr_v2 = AppThemeManager.instance()

        self.db = DocumentDatabase()
        self._active_worker_count = 0

        # AI Search components
        self.embedding_model = EmbeddingModel()
        self.vector_store = VectorStore(str(DATABASE_DIR / 'documents.db'))
        self.reranker = RerankerModel()
        self.chat_panel = ChatPanel(
            self, self.db.db_path, self.embedding_model, self.reranker,
            worker_tracker=self._track_worker,
        )
        # Preload both in background — first semantic search goes from cold
        # 7-10s (cold load + predict) to warm 2-4s (predict only). Runs on
        # QThread so UI is never blocked; safe to overlap with OCR because
        # Chandra runs in a separate llama-server subprocess.
        try:
            from startup_preloader import ModelPreloader
            self._preloader = ModelPreloader(self.embedding_model, self.reranker)
            self._track_worker(self._preloader)
            self._preloader.ready.connect(
                lambda s: logger.info(f"[startup] AI models preloaded in {s:.1f}s")
            )
            self._preloader.error.connect(
                lambda msg: logger.warning(f"[startup] preload error: {msg}")
            )
            self._preloader.start()
        except Exception as e:
            logger.warning(f"[startup] preloader not dispatched: {e}")

        # Document tracking
        self.current_doc_id = None
        self._document_open = False
        self.detections_by_page = {}
        self.current_view_mode = "details"
        self.current_search_keywords = []  # Store active search keywords for highlighting
        self._details_user_choice = None
        self._layout_bucket = None
        self._chat_open = False
        
        # Thông tin người dùng đăng nhập
        self.current_user = {
            "username": "OCR System",  # Giá trị mặc định
            "full_name": "OCR System"
        }
        
        # Worker threads
        self.ocr_worker = None
        self.batch_worker = None
        self.repair_worker = None
        
        # Auto-save timer
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.auto_save)
        self.autosave_timer.setInterval(AUTOSAVE_INTERVAL)
        self.autosave_timer.start()
        
        # Khởi tạo OCR system (Chandra Q4 qua llama-server — model_path không còn cần)
        self.ocr_system = DocumentOCR()

        # GPU keep-alive — prevents RTX 4070 from clocking down to 210 MHz
        # between OCR requests (causing 42 vs 62 tok/s cold-start penalty).
        # Small /completion ping every 6s keeps GPU in P0 power state.
        self._gpu_keepalive: Optional['GPUKeepAliveWorker'] = None
        try:
            from gpu_keepalive import GPUKeepAliveWorker
            if (get_setting("ocr_gpu_keepalive")
                    and self.ocr_system._chandra_server is not None
                    and self.ocr_system._chandra_server.is_running):
                self._gpu_keepalive = GPUKeepAliveWorker(
                    url=self.ocr_system._chandra_server.url,
                    interval_s=6.0,
                    parent=self,
                )
                self._gpu_keepalive.start(QThread.LowPriority)
                logger.info("GPU keep-alive thread started (interval 6s)")
        except Exception as e:
            logger.warning(f"GPU keep-alive unavailable: {e}")

        # self.db = DocumentDatabase()
        # Setup UI. 2026-04-19: QMenuBar removed — single unified QToolBar
        # handles all navigation (File/Edit/Search/Analytics/Settings) with
        # overflow (⋯) menu for secondary actions (Refresh/Preview/About).
        self.setup_ui()
        self.addDockWidget(Qt.RightDockWidgetArea, self.chat_panel)
        self.chat_panel.open_document_requested.connect(self._open_chat_document)
        self.chat_panel.visibilityChanged.connect(self._on_chat_visibility_changed)
        self.chat_panel.hide()
        self.menuBar().setVisible(False)  # suppress legacy Qt menu strip
        self.setup_shortcuts()
        self.setup_statusbar()
        apply_ui_settings(QApplication.instance(), self.statusBar())

        # Register theme-aware widgets (Phase 14 2026-04-18).
        # These have inline stylesheets that need re-apply on theme change.
        # ThemeManager holds weakrefs → no memory leak.
        try:
            if hasattr(self, 'doc_list'):
                self._theme_mgr_v2.register_widget(self.doc_list)
            if hasattr(self, 'ocr_editor'):
                self._theme_mgr_v2.register_widget(self.ocr_editor)
        except Exception as e:
            logger.debug(f"Theme widget registration warning: {e}")

        self._theme_mgr_v2.theme_changed.connect(self._refresh_theme_icons)

        # Apply one persisted theme after all theme-aware widgets exist.
        try:
            self._theme_mgr_v2.apply(
                QApplication.instance(), self._theme_mgr_v2.load_persisted()
            )
        except Exception as e:
            logger.warning(f"Theme restore failed: {e}")
        
        # Verify file paths and repair broken links
        QTimer.singleShot(500, self.check_file_paths)
        
        # Load existing documents
        self.load_documents()
        QTimer.singleShot(0, self._apply_responsive_layout)

    def load_window_settings(self):
        """Load window position and size from settings"""
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
            
        state = self.settings.value("windowState")
        if state:
            self.restoreState(state)

    def save_window_settings(self):
        """Save window position and size to settings"""
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())

    def check_file_paths(self):
        """Check and potentially repair missing file paths"""
        missing_files = self.db.verify_file_paths()
        if missing_files:
            reply = QMessageBox.question(
                self,
                "Tệp tin bị thiếu",
                f"Tìm thấy {len(missing_files)} tài liệu có đường dẫn tệp bị thiếu. Bạn có muốn sửa chữa không?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            
            if reply == QMessageBox.Yes:
                self.repair_file_paths(missing_files)
    def repair_file_paths(self, missing_files):
        """Repair missing file paths"""
        if not missing_files:
            return
            
        # Create progress dialog
        progress = ProgressDialog(len(missing_files), "Đang sửa đường dẫn tệp", self)
        
        # Create worker thread
        self.repair_worker = FileRepairWorker(self.db, missing_files)
        self._track_worker(self.repair_worker)
        self.repair_worker.progress.connect(progress.update_progress)
        self.repair_worker.file_path_request.connect(self.request_file_path)
        self.repair_worker.finished.connect(self.repair_completed)
        self.repair_worker.error.connect(self.show_error)
        progress.canceled.connect(self.repair_worker.cancel)
        
        # Start worker
        self.repair_worker.start()
        progress.exec_()
        
    
    def request_file_path(self, doc_id, old_path):
        """Request file path from user"""
        filename = Path(old_path).name
        
        # Ask user for the file
        msg = f"Đang tìm tệp: {filename}\nĐường dẫn cũ: {old_path}"
        QMessageBox.information(self, "Find File", msg)
        
        new_path, _ = QFileDialog.getOpenFileName(
            self, f"Locate {filename}", "", "Tệp (*.pdf *.png *.jpg *.jpeg *.tiff *.bmp)"
        )
        
        # Send response back to worker thread
        self.repair_worker.set_file_path_response(new_path)
        
    def repair_completed(self, fixed_files, remaining_issues):
        """Handle completion of file path repair"""
        # Update UI
        if fixed_files:
            self.load_documents()
            
        # Show summary
        if remaining_issues:
            msg = f"Đã sửa chữa {len(fixed_files)} tệp, nhưng vẫn còn {len(remaining_issues)} vấn đề.\n\n"
            msg += "Các tệp sau vẫn bị thiếu:\n"
            for doc_id, path in remaining_issues[:10]:  # Show first 10
                msg += f"- ID {doc_id}: {os.path.basename(path)}\n"
                
            if len(remaining_issues) > 10:
                msg += f"...và {len(remaining_issues) - 10} tệp khác."
                
            QMessageBox.warning(self, "Repair Summary", msg)
        else:
            QMessageBox.information(
                self, 
                "Repair Complete", 
                f"Đã sửa chữa thành công {len(fixed_files)} tệp."
            )

    def setup_ui(self):
        """Set up the main user interface"""
        # Base setup
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)  # panels meet the window edge
        main_layout.setSpacing(0)  # No spacing between major sections

        # 1. Setup native QToolBar (Phase 10, 2026-04-18).
        # Native QToolBar is heavily Qt-optimized: QAction-based, auto icon
        # caching, built-in tooltip + shortcut handling. Replaces previous
        # QHBoxLayout-based header with full semantic grouping.
        self._build_main_toolbar()

        # 2. Main Content Area with Splitter
        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("workspace_splitter")  # drawn as a hairline
        splitter.setHandleWidth(6)  # Easier to grab than 1px
        # Allow dragging + collapsing left/right panels (keep center pinned).
        # 2026-04-18 fix: setChildrenCollapsible(False) + thin 1px handle +
        # fixed-width children prevented users from dragging at all. Now
        # any panel can shrink to 0 by dragging handle to the edge.
        splitter.setChildrenCollapsible(True)
        splitter.setOpaqueResize(False)
        self._main_splitter = splitter  # keep ref for later config

        # LEFT PANEL — Kho văn bản (2026-10 minimal redesign).
        # A title with the count, one filter field with a sort menu beside
        # it, then the list. Rows show the document number (or the file name
        # while there is none) and the issue date; everything else — status,
        # summary, pages — is in the row tooltip, so nothing is cut or printed
        # as a legend.
        left_panel = QWidget()
        left_panel.setObjectName("library_panel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self.doc_list = DocumentListWidget()
        self.doc_list.doc_clicked.connect(self.document_selected_by_id)
        self.doc_list.context_menu_requested.connect(self.show_document_context_menu_by_id)
        self.doc_list.hover_requested.connect(self._lazy_load_hover_meta)
        self._hover_meta_cache = set()  # track which doc_ids already loaded

        # Document Gallery — REMOVED 2026-04-18.
        # Previously a QTableWidget with PDF thumbnails via fitz.get_pixmap.
        # Feature deprecated: single minimalist list replaces it. All gallery
        # methods (update_document_gallery, load_thumbnail_for_item,
        # gallery_item_selected, show_gallery_context_menu) purged.
        # No-op stub to prevent AttributeError in any lingering callers.
        self.doc_gallery = None
        self._doc_list_panel = left_panel

        library_header = QHBoxLayout()
        library_header.setContentsMargins(12, 10, 10, 6)
        library_header.setSpacing(6)
        library_title = QLabel("Kho văn bản")
        library_title.setObjectName("library_title")
        library_header.addWidget(library_title)
        library_header.addStretch()
        self._library_count_label = QLabel("0")
        self._library_count_label.setObjectName("library_count")
        library_header.addWidget(self._library_count_label)
        left_layout.addLayout(library_header)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(8, 0, 8, 6)
        filter_row.setSpacing(4)

        # Live filter over the number and the file name.
        self._doc_filter_input = QLineEdit()
        self._doc_filter_input.setObjectName("library_filter")
        self._doc_filter_input.setPlaceholderText("Lọc theo số hiệu, tên tệp")
        self._doc_filter_input.setFixedHeight(26)
        self._doc_filter_input.setToolTip(chrome_tooltip(
            "Lọc kho văn bản",
            hint="Gõ một phần số hiệu hoặc tên tệp; tìm theo nội dung: Ctrl+F"))
        self._doc_filter_lead = self._doc_filter_input.addAction(
            QIcon(), QLineEdit.LeadingPosition)
        self._doc_filter_clear = self._doc_filter_input.addAction(
            QIcon(), QLineEdit.TrailingPosition)
        self._doc_filter_clear.setToolTip("Xóa bộ lọc")
        self._doc_filter_clear.setVisible(False)
        self._remember_icon(self._doc_filter_lead, "search", 16)
        self._remember_icon(self._doc_filter_clear, "x", 16)
        self._doc_filter_clear.triggered.connect(self._doc_filter_input.clear)
        self._doc_filter_input.textChanged.connect(
            lambda text: self._doc_filter_clear.setVisible(bool(text)))
        self._doc_filter_input.textChanged.connect(self._on_doc_filter_changed)
        filter_row.addWidget(self._doc_filter_input, 1)

        # Sort: a small icon button whose menu holds the choices.
        from PyQt5.QtWidgets import QActionGroup
        self._doc_sort_key = "date_desc"
        self._doc_sort_btn = QToolButton()
        self._doc_sort_btn.setObjectName("library_sort")
        self._doc_sort_btn.setAutoRaise(True)
        self._doc_sort_btn.setPopupMode(QToolButton.InstantPopup)
        sort_menu = QMenu(self._doc_sort_btn)
        sort_group = QActionGroup(sort_menu)
        sort_group.setExclusive(True)
        self._doc_sort_actions = {}
        for key, label in self.DOC_SORTS:
            if key is None:
                sort_menu.addSeparator()
                continue
            action = sort_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == self._doc_sort_key)
            action.setData(key)
            sort_group.addAction(action)
            self._doc_sort_actions[key] = action
        sort_group.triggered.connect(
            lambda action: self._on_doc_sort_changed(action.data()))
        self._doc_sort_btn.setMenu(sort_menu)
        self._doc_sort_btn.setIconSize(QSize(16, 16))
        self._remember_icon(self._doc_sort_btn, "sort", 16)
        filter_row.addWidget(self._doc_sort_btn)
        self._update_doc_sort_tooltip()
        left_layout.addLayout(filter_row)

        left_layout.addWidget(self.doc_list, 1)
        splitter.addWidget(left_panel)

    # CENTER PANEL - PDF Viewer and Metadata
        # A quiet underline tab strip; the page navigation sits at its right
        # end (one 32px row instead of two) and only while the page is shown.
        # Not documentMode: that draws a palette-coloured base line under the
        # corner widget (white in the dark theme); the pane's top hairline is
        # the only line here.
        center_panel = QTabWidget()
        center_panel.setObjectName("workspace_tabs")
        center_panel.setAttribute(Qt.WA_StyledBackground, True)  # the row is a surface
        center_panel.tabBar().setDrawBase(False)

        # PDF Viewer Tab
        self.pdf_viewer = PDFViewer()
        self.pdf_viewer.addRequested.connect(self.add_file)
        self.pdf_viewer.pageChanged.connect(self.page_changed)
        center_panel.addTab(self.pdf_viewer, "Xem văn bản")
        center_panel.setCornerWidget(self.pdf_viewer.take_nav_bar(), Qt.TopRightCorner)
        center_panel.currentChanged.connect(
            lambda index: self.pdf_viewer.set_nav_allowed(index == 0))
        # Narrow: tab labels shorten instead of hiding behind scroll arrows.
        center_panel.tabBar().setUsesScrollButtons(False)
        center_panel.tabBar().setElideMode(Qt.ElideRight)

        # Metadata Tab
        metadata_scroll = QScrollArea()
        metadata_scroll.setWidgetResizable(True)
        metadata_scroll.setFrameShape(QFrame.NoFrame)
        
        # A plain read-only sheet: muted labels, values as selectable text
        # that wraps — no sunken boxes (they turned black in the dark theme).
        metadata_content = QWidget()
        metadata_content.setObjectName("file_info")
        metadata_layout = QVBoxLayout(metadata_content)
        metadata_layout.setSpacing(8)
        metadata_layout.setContentsMargins(16, 14, 16, 16)

        # Create metadata form
        metadata_form = QWidget()
        form_layout = QFormLayout(metadata_form)
        form_layout.setVerticalSpacing(8)
        form_layout.setHorizontalSpacing(16)
        form_layout.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        form_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Initialize metadata fields
        self.metadata_fields = {}
        metadata_field_labels = [
            ('id', 'Mã'),
            ('filename', 'Tên tệp'),
            ('created', 'Ngày thêm'),
            ('modified', 'Sửa đổi'),
            ('pages', 'Số trang'),
            ('filesize', 'Kích thước'),
            ('versions', 'Phiên bản'),
            ('cqbh', 'Cơ quan ban hành'),
            ('so_kh', 'Số ký hiệu'),
            ('loai_vb', 'Loại văn bản'),
            ('do_khan', 'Độ khẩn'),
            ('do_mat', 'Độ mật')
        ]

        for field_id, label in metadata_field_labels:
            field_label = QLabel(label)
            field_label.setProperty("role", "fileInfoLabel")

            field = QLabel()
            field.setWordWrap(True)
            field.setTextInteractionFlags(Qt.TextSelectableByMouse)
            field.setProperty("role", "fileInfoValue")

            self.metadata_fields[field_id] = field
            form_layout.addRow(field_label, field)

        metadata_layout.addWidget(metadata_form)
        metadata_layout.addStretch()
        metadata_scroll.setWidget(metadata_content)
        center_panel.addTab(metadata_scroll, "Thông tin tệp")
        center_panel.setTabToolTip(0, "Trang văn bản gốc")
        center_panel.setTabToolTip(1, "Tệp, kích thước và các trường đã nhận dạng")

        splitter.addWidget(center_panel)

        # RIGHT PANEL - OCR Editor
        self.ocr_editor = OCRResultEditor(db=self.db)
        self.ocr_editor.ocr_updated.connect(self.update_document)
        self.ocr_editor.field_changed.connect(self.field_value_changed)
        self.ocr_editor.reocr_requested.connect(self._reocr_current_document)
        splitter.addWidget(self.ocr_editor)

        # Set initial splitter sizes (ratio: 25:45:30)
        splitter.setSizes([
            int(self.width() * 0.25),  # Left panel
            int(self.width() * 0.45),  # Center panel
            int(self.width() * 0.30)   # Right panel
        ])

        # Collapsibility + stretch (2026-04-18):
        #   Left   (0): can collapse — user may want max PDF view
        #   Center (1): NEVER collapse — PDF preview is always needed
        #   Right  (2): can collapse — already has F10 meta toggle but
        #               splitter drag is a discoverable alternative
        splitter.setCollapsible(0, True)
        splitter.setCollapsible(1, False)
        splitter.setCollapsible(2, True)
        splitter.setStretchFactor(0, 0)  # left keeps its size unless user drags
        splitter.setStretchFactor(1, 1)  # center absorbs extra window width
        splitter.setStretchFactor(2, 0)

        # Ensure children can actually shrink/grow. Prevent fixed-width
        # widgets inside from locking the splitter.
        for panel in (left_panel, ):  # center/right already flexible
            panel.setMinimumWidth(0)
            panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Add splitter to content layout
        content_layout.addWidget(splitter)
        main_layout.addWidget(content_widget, 1)  # Give content area stretch factor of 1

        # Initialize search timer
        self.current_search_keywords = []


    # Library sort choices (key, menu label); (None, None) is a separator.
    DOC_SORTS = (
        ("date_desc", "Mới thêm gần đây"),
        ("date_asc", "Thêm sớm nhất"),
        (None, None),
        ("issued_desc", "Ngày ban hành mới nhất"),
        ("issued_asc", "Ngày ban hành cũ nhất"),
        (None, None),
        ("name_asc", "Số hiệu A–Z"),
        ("name_desc", "Số hiệu Z–A"),
    )

    def _build_main_toolbar(self) -> None:
        """The top toolbar (2026-10 minimal redesign).

        One 34px row of 18px icon buttons in groups split by hairlines, no
        text beside the icons. Grouped actions open a menu on click (Thêm,
        Tìm, the overflow). Every button explains itself in a tooltip: what
        it does, a line of context, then the shortcut. The middle names the
        open document — its type, its number in gold, the page count — with
        the file name in the tooltip.

            mark | library details | add▾ delete | search▾ assistant
                            Kế hoạch 336/KH-KTNS 4 trang
                                analytics excel | theme more▾

        Save and reset act on the editor and live there; their shortcuts
        (Ctrl+S, Ctrl+Shift+Backspace) stay on the window.
        """
        self._chrome_icons = []  # (target, icon, size, role, on icon) for theme switches
        toolbar = self.addToolBar("Main")
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        toolbar.setContextMenuPolicy(Qt.PreventContextMenu)

        def action(command_id, callback, icon_name, title, hint="", role="tool",
                   checkable=False, on_name=None, size=18):
            spec = command(command_id)
            act = QAction(title, self)
            if spec.shortcut:
                act.setShortcut(QKeySequence(spec.shortcut))
            act.setToolTip(chrome_tooltip(title, spec.shortcut, hint))
            act.setCheckable(checkable)
            (act.toggled if checkable else act.triggered).connect(callback)
            self._remember_icon(act, icon_name, size, role, on_name)
            self.addAction(act)  # the shortcut works whatever is on screen
            return act

        def menu_button(icon_name, title, shortcut, hint, menu):
            button = QToolButton(toolbar)
            button.setAutoRaise(True)
            button.setIconSize(QSize(18, 18))
            button.setPopupMode(QToolButton.InstantPopup)
            button.setMenu(menu)
            button.setToolTip(chrome_tooltip(title, shortcut, hint))
            button.setAccessibleName(title)
            self._remember_icon(button, icon_name, 18)
            toolbar.addWidget(button)
            return button

        # ── The mark, which is also the way home ──────────
        # A solid olive page, a size up from the outline tools: the app's
        # sign without a picture logo; the bear stays the window icon.
        self.act_home = action(
            "home", lambda _checked=False: self.home_requested.emit(), "app-mark-solid",
            "Bàn bắt đầu việc", "Văn bản gần đây, quét và nhập tệp", role="mark", size=20,
        )
        toolbar.addAction(self.act_home)
        toolbar.widgetForAction(self.act_home).setIconSize(QSize(20, 20))
        toolbar.addSeparator()

        # ── Panels ─────────────────────────────────────────
        self.act_toggle_left_panel = action(
            "toggle_library", self._toggle_left_panel, "panel-left",
            "Kho văn bản", "Ẩn hoặc hiện danh sách văn bản bên trái", checkable=True,
        )
        self.act_toggle_left_panel.setChecked(True)
        toolbar.addAction(self.act_toggle_left_panel)
        self.act_toggle_details = action(
            "toggle_metadata", self._toggle_details_panel, "panel-right",
            "Vùng chi tiết", "Ẩn hoặc hiện văn bản OCR và các trường bên phải",
            checkable=True,
        )
        self.act_toggle_details.setChecked(True)
        toolbar.addAction(self.act_toggle_details)
        toolbar.addSeparator()

        # ── Documents in and out of the library ───────────
        add_menu = QMenu(self)
        self.act_add_file = self._menu_action(
            add_menu, "add_file", self.add_file, "file-plus", "Thêm một văn bản…")
        act_add_batch = self._menu_action(
            add_menu, "add_files", self.add_files, "files", "Thêm nhiều tệp…")
        act_add_folder = self._menu_action(
            add_menu, "add_folder", self.add_folder, "folder-open", "Nhập thư mục văn bản…")
        self._add_button = menu_button(
            "file-plus", "Thêm văn bản", command("add_file").shortcut,
            "Quét một tệp, nhiều tệp hoặc cả thư mục", add_menu,
        )
        self._act_add_batch = act_add_batch
        self._act_add_folder = act_add_folder

        self.act_delete = action(
            "delete", self._delete_current_document_safe, "trash-2",
            "Xóa văn bản", "Xóa các văn bản đang chọn khỏi kho", role="danger",
        )
        toolbar.addAction(self.act_delete)
        toolbar.addSeparator()

        # ── Find: keywords, meaning, the legal assistant ──
        search_menu = QMenu(self)
        self.act_search = self._menu_action(
            search_menu, "search", self.show_search_dialog, "search", "Tìm kiếm văn bản…")
        act_ai_search = self._menu_action(
            search_menu, "ai_search", self.show_ai_search_dialog, "sparkles",
            "Tìm kiếm AI ngữ nghĩa…")
        self._search_button = menu_button(
            "search", "Tìm kiếm", command("search").shortcut,
            "Theo từ khóa, hoặc theo ý nghĩa với AI", search_menu,
        )
        self._act_ai_search = act_ai_search

        self.act_chat = action(
            "chat", self._toggle_chat_panel, "chat", "Trợ lý pháp luật",
            "Hỏi về nội dung, căn cứ và hiệu lực của văn bản trong kho",
            checkable=True,
        )
        toolbar.addAction(self.act_chat)

        # ── The open document, in the middle ─────────────
        context = QWidget(toolbar)
        context.setObjectName("toolbar_context")
        context.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row = QHBoxLayout(context)
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(8)
        row.addStretch()
        self._toolbar_doc_type = QLabel()
        self._toolbar_doc_type.setObjectName("toolbar_doc_type")
        self._toolbar_doc_number = QLabel()
        self._toolbar_doc_number.setObjectName("toolbar_doc_number")
        self._toolbar_doc_number.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._toolbar_doc_pages = QLabel()
        self._toolbar_doc_pages.setObjectName("toolbar_doc_pages")
        for label in (self._toolbar_doc_type, self._toolbar_doc_number, self._toolbar_doc_pages):
            label.hide()
            row.addWidget(label)
        row.addStretch()
        self.toolbar_context_label = context
        self._toolbar_doc_file = ("", 0)
        toolbar.addWidget(context)

        # ── Reports ────────────────────────────────────────
        self.act_analytics = action(
            "analytics", self.show_analytics_dashboard, "bar-chart-3",
            "Phân tích thống kê", "Biểu đồ và số liệu về kho văn bản",
        )
        toolbar.addAction(self.act_analytics)
        self.act_export_excel = action(
            "export_excel", self.export_to_excel, "excel",
            "Xuất Excel", "Lưu toàn bộ kho văn bản ra một tệp Excel",
        )
        toolbar.addAction(self.act_export_excel)
        toolbar.addSeparator()

        # ── Theme and everything else ─────────────────────
        # Moon in the light theme, sun in the dark one: the icon shows where
        # a click leads. The tooltip follows the theme (_refresh_theme_icons).
        self.act_dark_mode = action(
            "toggle_theme", self._on_dark_mode_toggled, "moon", "Giao diện tối",
            checkable=True, on_name="sun",
        )
        self.act_dark_mode.setChecked(self._theme_mgr_v2.current == "dark")
        toolbar.addAction(self.act_dark_mode)
        toolbar.widgetForAction(self.act_dark_mode).setObjectName("theme_toggle")

        overflow_menu = QMenu(self)
        ov_preview = self._menu_action(
            overflow_menu, "preview", self.toggle_preview, "eye", "Xem trước PDF")
        ov_refresh = self._menu_action(
            overflow_menu, "refresh", self.refresh_view, "refresh-cw", "Làm mới dữ liệu")
        overflow_menu.addSeparator()
        # F1 and Ctrl+K are window shortcuts already (setup_shortcuts); the
        # menu only shows them, binding them twice would make both ambiguous.
        ov_palette = self._menu_action(
            overflow_menu, None, self._show_command_palette, "command-search",
            "Tìm và chạy tác vụ\t{}".format(command("command_palette").shortcut))
        ov_shortcuts = self._menu_action(
            overflow_menu, None, self._show_shortcut_overlay, "command",
            "Danh sách phím tắt\t{}".format(command("shortcut_help").shortcut))
        overflow_menu.addSeparator()
        ov_settings = self._menu_action(
            overflow_menu, "settings", self._show_settings_placeholder, "settings", "Cài đặt…")
        ov_backup = self._menu_action(
            overflow_menu, None, self.backup_database, "database", "Sao lưu cơ sở dữ liệu…")
        ov_check = self._menu_action(
            overflow_menu, None, self.check_file_paths, "folder-search", "Kiểm tra đường dẫn tệp…")
        ov_reindex = self._menu_action(
            overflow_menu, None, self.show_reindex_dialog, "sync", "Xây lại chỉ mục AI…")
        overflow_menu.addSeparator()
        ov_help = self._menu_action(
            overflow_menu, None, self.show_help, "help-circle", "Hướng dẫn sử dụng")
        ov_about = self._menu_action(
            overflow_menu, None, self.show_about, "info", "Về ứng dụng")

        overflow_btn = menu_button(
            "more-horizontal", "Công cụ khác", "",
            "Xem trước, làm mới, phím tắt, cài đặt, sao lưu, hướng dẫn", overflow_menu,
        )
        self._overflow_menu = overflow_menu
        self._overflow_button = overflow_btn
        self._overflow_actions = {
            "preview": ov_preview, "refresh": ov_refresh,
            "palette": ov_palette, "shortcuts": ov_shortcuts,
            "settings": ov_settings, "backup": ov_backup,
            "check_files": ov_check, "reindex": ov_reindex,
            "help": ov_help, "about": ov_about,
        }

        # Editor commands without a toolbar button: shortcut and palette only.
        self.act_save = self._window_action("save", self._toolbar_save_current)
        self.act_reset = self._window_action("reset", self._toolbar_reset_current)

        self._command_actions = {
            "home": self.act_home, "toggle_library": self.act_toggle_left_panel,
            "toggle_metadata": self.act_toggle_details,
            "add_file": self.act_add_file, "add_files": act_add_batch,
            "add_folder": act_add_folder, "save": self.act_save,
            "reset": self.act_reset, "delete": self.act_delete,
            "search": self.act_search, "ai_search": act_ai_search,
            "chat": self.act_chat, "analytics": self.act_analytics,
            "export_excel": self.act_export_excel, "toggle_theme": self.act_dark_mode,
            "preview": ov_preview, "refresh": ov_refresh, "settings": ov_settings,
        }

        self._main_toolbar = toolbar

    def _remember_icon(self, target, name, size=18, role="tool", on_name=None) -> None:
        """Give a QAction/button its token-coloured icon and recolour it on
        every theme switch (_refresh_theme_icons)."""
        self._chrome_icons.append((target, name, size, role, on_name))
        target.setIcon(chrome_icon(name, size, role=role, on_name=on_name))

    def _menu_action(self, menu, command_id, callback, icon_name, text) -> QAction:
        """A menu item with an icon; its shortcut (from the command catalogue)
        is shown in the menu and works on the whole window."""
        act = QAction(text, self)
        if command_id and command(command_id).shortcut:
            act.setShortcut(QKeySequence(command(command_id).shortcut))
            self.addAction(act)
        act.triggered.connect(lambda _checked=False: callback())
        self._remember_icon(act, icon_name, 16)
        menu.addAction(act)
        return act

    def _window_action(self, command_id, callback) -> QAction:
        """A shortcut-only command: no button, reachable from the palette."""
        spec = command(command_id)
        act = QAction(spec.label, self)
        act.setShortcut(QKeySequence(spec.shortcut))
        act.triggered.connect(lambda _checked=False: callback())
        self.addAction(act)
        return act

    def _current_doc_identity(self):
        """(số hiệu, loại VB) of the open document from the library snapshot."""
        for entry in getattr(self, "_docs_snapshot", None) or []:
            doc = entry[0] if isinstance(entry, tuple) and len(entry) == 2 \
                and isinstance(entry[1], dict) else entry
            try:
                if int(doc[0]) != int(self.current_doc_id or -1):
                    continue
            except (TypeError, ValueError):
                continue
            so_hieu = (doc[8] or "").strip() if len(doc) > 8 else ""
            loai_vb = (doc[9] or "").strip() if len(doc) > 9 else ""
            return so_hieu, loai_vb
        return "", ""

    def _update_toolbar_context(self, filename: str = "", page_count: int = 0) -> None:
        """Name the open document in the middle of the toolbar.

        The number (số hiệu) in gold is the title, its type before it and
        the page count after it are quieter; a document without a number
        shows its file name. Separate labels, no "a · b" string; the file
        name and the details are in the tooltip.
        """
        if not hasattr(self, "_toolbar_doc_number"):
            return
        self._toolbar_doc_file = (filename, page_count)
        kind, number, pages = self._toolbar_doc_type, self._toolbar_doc_number, self._toolbar_doc_pages
        if not filename:
            for label in (kind, number, pages):
                label.hide()
            self.toolbar_context_label.setToolTip("")
            return
        so_hieu, loai_vb = self._current_doc_identity()
        number.setProperty("kind", "number" if so_hieu else "file")
        number.style().unpolish(number)
        number.style().polish(number)
        title = so_hieu or filename
        number.setText(QFontMetrics(number.font()).elidedText(title, Qt.ElideMiddle, 420))
        kind.setText(loai_vb)
        kind.setVisible(bool(so_hieu and loai_vb))
        number.show()
        pages.setText(f"{page_count} trang" if page_count else "")
        pages.setVisible(bool(page_count))
        details = ", ".join(part for part in (
            loai_vb, f"{page_count} trang" if page_count else "") if part)
        self.toolbar_context_label.setToolTip(chrome_tooltip(filename, hint=details))

    # ── Toolbar action stubs / routers ──────────────────────
    def _toggle_left_panel(self, visible: bool) -> None:
        """Animated collapse of left panel (Phase 11).

        Uses QPropertyAnimation on splitter.setSizes via a QVariantAnimation
        wrapper. 200ms duration, OutCubic easing. Persists state via QSettings.

        Performance: single animation per toggle (not continuous), no polling.
        When animation completes → actual setVisible(False) to free layout.
        """
        if not hasattr(self, '_main_splitter'):
            return

        splitter = self._main_splitter
        left_widget = splitter.widget(0)
        if not left_widget:
            return

        # Stop any running animation to prevent conflict.
        if hasattr(self, '_left_panel_anim') and self._left_panel_anim:
            self._left_panel_anim.stop()

        from PyQt5.QtCore import QVariantAnimation, QEasingCurve, QSettings
        current_sizes = splitter.sizes()
        total = sum(current_sizes) or 1
        target_left = 280 if visible else 0

        # Remember last expanded width so re-open restores it.
        if not visible and current_sizes[0] > 0:
            QSettings("OCR VBHC", "OCR_VBHC").setValue("left_panel_width", current_sizes[0])
        elif visible:
            saved = QSettings("OCR VBHC", "OCR_VBHC").value("left_panel_width", 280, type=int)
            target_left = int(saved) if saved > 80 else 280

        # Ensure widget is visible DURING animation (can't animate hidden).
        left_widget.setVisible(True)

        anim = QVariantAnimation(self)
        anim.setDuration(200)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(current_sizes[0])
        anim.setEndValue(target_left)

        def _tick(width):
            # Redistribute: left=width, center+right get the rest proportionally.
            sizes = splitter.sizes()
            if len(sizes) < 2:
                return
            remaining = total - int(width)
            if len(sizes) == 3:
                # Split remaining between center (60%) + right (40%)
                sizes = [int(width), int(remaining * 0.6), remaining - int(remaining * 0.6)]
            else:
                sizes = [int(width)] + [remaining // (len(sizes) - 1)] * (len(sizes) - 1)
            splitter.setSizes(sizes)

        anim.valueChanged.connect(_tick)

        def _on_finished():
            if not visible:
                # After collapse → fully hide widget (frees layout calculations).
                left_widget.setVisible(False)

        anim.finished.connect(_on_finished)
        anim.start()
        self._left_panel_anim = anim

    def _toolbar_save_current(self) -> None:
        """Save button handler — delegates to OCR editor if doc open."""
        if hasattr(self, 'ocr_editor') and self.ocr_editor.save_btn.isEnabled():
            self.ocr_editor.save_changes()

    def _toggle_details_panel(self, visible: bool) -> None:
        """Let F10/toolbar temporarily open the editor like a compact drawer."""
        if not hasattr(self, "_main_splitter"):
            return
        if visible and not self._document_open:
            self.statusBar().showMessage("Hãy mở một văn bản trước khi xem chi tiết", 2500)
            self._set_details_visible(False)
            return
        self._details_user_choice = bool(visible)
        self._set_details_visible(bool(visible))

    def _set_details_visible(self, visible: bool) -> None:
        splitter = getattr(self, "_main_splitter", None)
        if splitter is None or splitter.count() < 3:
            return
        splitter.widget(2).setVisible(visible)
        if hasattr(self, "act_toggle_details"):
            self.act_toggle_details.blockSignals(True)
            self.act_toggle_details.setChecked(visible)
            self.act_toggle_details.blockSignals(False)
        width = splitter.width() or self.width()
        left_visible = splitter.widget(0).isVisible()
        splitter.setSizes(splitter_sizes(width, left_visible, visible))

    def _apply_responsive_layout(self) -> None:
        splitter = getattr(self, "_main_splitter", None)
        if splitter is None:
            return
        width = splitter.width() or self.width()
        chat_visible = self.chat_panel.isVisible()
        bucket = (width < 1200, chat_visible and width < 1600)
        if bucket != self._layout_bucket:
            self._layout_bucket = bucket
            self._details_user_choice = None
        auto_visible = self._document_open and not should_collapse_details(width, chat_visible)
        visible = auto_visible if self._details_user_choice is None else self._details_user_choice
        self._set_details_visible(visible)

    def _on_chat_visibility_changed(self, _visible: bool) -> None:
        # Minimising the window, or hiding it for the home screen, hides the
        # dock too; only the reader opening or closing it (isHidden) changes
        # the toggle and the layout. Fed straight from visibilityChanged, the
        # toggle's toggled signal closed the dock on every minimise.
        is_open = not self.chat_panel.isHidden()
        self.act_chat.setChecked(is_open)
        if is_open == self._chat_open:
            return
        self._chat_open = is_open
        self._details_user_choice = None
        self._layout_bucket = None
        QTimer.singleShot(0, self._apply_responsive_layout)

    def _toolbar_reset_current(self) -> None:
        """Reset button handler — delegates to OCR editor."""
        if hasattr(self, 'ocr_editor'):
            self.ocr_editor.reset_fields()

    def _delete_current_document_safe(self) -> None:
        """Ignore Del while the user is editing text or a form control."""
        focus = QApplication.focusWidget()
        if isinstance(focus, (
            QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QAbstractSpinBox,
        )):
            return
        self.delete_current_document()

    def _on_dark_mode_toggled(self, checked: bool) -> None:
        """Toolbar dark mode action handler — swap theme via ThemeManager v2."""
        self._theme_mgr_v2.apply(
            QApplication.instance(), "dark" if checked else "light"
        )

    def _refresh_theme_icons(self, theme: str) -> None:
        """Recolour every chrome icon from the theme tokens after a switch.

        Toolbar, menus (the overflow too), the library filter and sort, the
        PDF navigation: each icon was registered by _remember_icon with its
        role, so a new theme repaints all of them from colors_for(theme).
        The editor recolours its own buttons (OCRResultEditor.apply_theme,
        called by the theme manager), so nothing here reaches into it.
        """
        self.act_dark_mode.blockSignals(True)
        self.act_dark_mode.setChecked(theme == "dark")
        self.act_dark_mode.blockSignals(False)
        self.act_dark_mode.setToolTip(chrome_tooltip(
            "Giao diện sáng" if theme == "dark" else "Giao diện tối",
            command("toggle_theme").shortcut))
        for target, name, size, role, on_name in getattr(self, "_chrome_icons", []):
            target.setIcon(chrome_icon(name, size, theme, role, on_name))
        self.pdf_viewer.apply_theme(theme)
        self.chat_panel._render_messages()

    def _show_settings_placeholder(self) -> None:
        """Open Settings dialog (Phase 15, 2026-04-18)."""
        try:
            from settings_dialog import SettingsDialog
            dlg = SettingsDialog(self)
            dlg.exec_()
        except Exception as e:
            logger.error(f"Settings dialog error: {e}")
            QMessageBox.warning(self, "Cài đặt",
                                f"Không thể mở bảng cài đặt:\n{e}")

    def setup_shortcuts(self):
        """Bind non-QAction commands from the shared command catalogue."""
        self._direct_shortcuts = []
        bindings = {
            "previous_page": self.pdf_viewer.previous_page,
            "next_page": self.pdf_viewer.next_page,
            "zoom_in": self.pdf_viewer.zoom_in,
            "zoom_out": self.pdf_viewer.zoom_out,
            "zoom_reset": self.pdf_viewer.reset_view,
            "rotate_right": self.pdf_viewer.rotate_right,
            "rotate_left": self.pdf_viewer.rotate_left,
            "command_palette": self._show_command_palette,
            "shortcut_help": self._show_shortcut_overlay,
        }
        for command_id, callback in bindings.items():
            shortcut = QShortcut(QKeySequence(command(command_id).shortcut), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(callback)
            self._direct_shortcuts.append(shortcut)

        escape = QShortcut(QKeySequence("Escape"), self)
        escape.setContext(Qt.WindowShortcut)
        escape.activated.connect(self._handle_escape)
        self._direct_shortcuts.append(escape)

    def _ensure_command_overlay(self):
        if not hasattr(self, '_shortcut_overlay') or self._shortcut_overlay is None:
            from shortcut_overlay import ShortcutOverlay
            self._shortcut_overlay = ShortcutOverlay(
                self, on_command_activated=self._execute_command,
            )
        return self._shortcut_overlay

    def _show_shortcut_overlay(self) -> None:
        """Show F1 shortcut cheat sheet. Lazy-creates on first call."""
        try:
            self._ensure_command_overlay().show_help()
        except Exception as e:
            logger.error(f"Shortcut overlay error: {e}")

    def _show_command_palette(self) -> None:
        """Open Ctrl+K command search without stealing Ctrl+F."""
        try:
            self._ensure_command_overlay().show_commands()
        except Exception as e:
            logger.error(f"Command palette error: {e}")

    def _execute_command(self, command_id: str) -> None:
        action = getattr(self, "_command_actions", {}).get(command_id)
        if action is not None:
            action.trigger()
            return
        direct = {
            "previous_page": self.pdf_viewer.previous_page,
            "next_page": self.pdf_viewer.next_page,
            "zoom_in": self.pdf_viewer.zoom_in,
            "zoom_out": self.pdf_viewer.zoom_out,
            "zoom_reset": self.pdf_viewer.reset_view,
            "rotate_right": self.pdf_viewer.rotate_right,
            "rotate_left": self.pdf_viewer.rotate_left,
            "shortcut_help": self._show_shortcut_overlay,
            "command_palette": self._show_command_palette,
        }
        callback = direct.get(command_id)
        if callback:
            callback()

    def _handle_escape(self) -> None:
        overlay = getattr(self, "_shortcut_overlay", None)
        if overlay is not None and overlay.isVisible():
            overlay.close()
        elif self.chat_panel.stop_button.isEnabled():
            self.chat_panel.stop_generation()
        else:
            self.clear_selection()

    def setup_statusbar(self):
        """A quiet status bar (2026-10 minimal redesign).

          [open file name]  ··· stretch ···  [tasks] [VRAM bar  4.2/8 GB] [● Chandra]

        Separate small labels, no "a · b" strings; what each part means is
        in its tooltip. Tasks and VRAM hide while there is nothing to say.
        The document count moved next to "Kho văn bản". All indicators are
        driven by one StatusIndicatorCoordinator (10s poll, paused when the
        window is inactive).
        """
        statusbar = self.statusBar()
        statusbar.setSizeGripEnabled(False)

        # Left side — the open document's file name (variable width)
        self.doc_info_label = QLabel("")
        self.doc_info_label.setObjectName("status_doc")
        statusbar.addWidget(self.doc_info_label, 1)  # stretch=1

        # Right side — live indicators (permanent widgets stack right-to-left)
        from status_indicators import (
            LlamaStatusIndicator, VramIndicator, WorkerCountIndicator,
            StatusIndicatorCoordinator,
        )

        self._status_coord = StatusIndicatorCoordinator(self)

        # Worker count (hidden when 0)
        self.worker_indicator = WorkerCountIndicator()
        self.worker_indicator.set_count(self._active_worker_count)
        statusbar.addPermanentWidget(self.worker_indicator)

        # VRAM bar + text (hidden until nvidia-smi answers)
        self.vram_indicator = VramIndicator()
        statusbar.addPermanentWidget(self.vram_indicator)
        self._status_coord.register(self.vram_indicator)

        # OCR server status dot
        self.llama_indicator = LlamaStatusIndicator()
        statusbar.addPermanentWidget(self.llama_indicator)
        self._status_coord.register(self.llama_indicator)

        # Kick off periodic polling.
        self._status_coord.start()

    def _track_worker(self, worker) -> None:
        """Reflect any QThread lifecycle in the status-bar task badge."""
        timer = QTimer(self)
        timer.setInterval(100)

        def started():
            self._active_worker_count += 1
            self._refresh_worker_count()
            timer.start()

        def poll_until_stopped():
            if worker.isRunning():
                return
            timer.stop()
            self._active_worker_count = max(0, self._active_worker_count - 1)
            self._refresh_worker_count()
            timer.deleteLater()

        worker.started.connect(started)
        timer.timeout.connect(poll_until_stopped)

    def _refresh_worker_count(self) -> None:
        if hasattr(self, "worker_indicator"):
            self.worker_indicator.set_count(self._active_worker_count)
    def show_document_context_menu_by_id(self, doc_id, global_pos):
        """Library row context menu: open, OCR again, quick Word export;
        tags; delete (its icon turns red under the pointer). Icons are
        coloured from the theme tokens.
        """
        try:
            doc_id = int(doc_id)
            ctx = QMenu(self)

            open_pdf = ctx.addAction(chrome_icon("eye", 16), "Mở PDF")
            reocr = ctx.addAction(chrome_icon("refresh-cw", 16), "OCR lại")
            quick_export = ctx.addAction(chrome_icon("file-text", 16), "Xuất nhanh (Word)")
            ctx.addSeparator()

            # Tag submenu
            tags_menu = ctx.addMenu("Gắn tag")
            tags_menu.setIcon(chrome_icon("tag", 16))
            current_tags = set(self.db.get_document_tags(doc_id) or [])
            all_tags = list(self.db.get_all_tags() or [])
            tag_actions = {}
            for tag in all_tags:
                act = tags_menu.addAction(tag)
                act.setCheckable(True)
                act.setChecked(tag in current_tags)
                tag_actions[act] = tag
            if all_tags:
                tags_menu.addSeparator()
            new_tag_action = tags_menu.addAction("Tag mới…")

            ctx.addSeparator()

            delete_action = ctx.addAction(chrome_icon("trash-2", 16, role="danger"), "Xóa")

            action = ctx.exec_(global_pos)
            if action is None:
                return

            if action == open_pdf:
                self.open_document_pdf(doc_id)
            elif action == reocr:
                # Re-OCR the current doc — reuse editor signal if available
                self.current_doc_id = doc_id
                if hasattr(self, '_reocr_current_document'):
                    self._reocr_current_document()
                elif hasattr(self.ocr_editor, 'reocr_requested'):
                    self.show_document(doc_id)
                    self.ocr_editor.reocr_requested.emit()
            elif action == quick_export:
                # Quick export to Word — uses editor's exporter if available
                self.show_document(doc_id)
                if hasattr(self.ocr_editor, '_export_ocr_to_word'):
                    self.ocr_editor._export_ocr_to_word()
            elif action == new_tag_action:
                self.add_new_tag(doc_id)
            elif action in tag_actions:
                tag_name = tag_actions[action]
                if action.isChecked():
                    self.db.add_tag(doc_id, tag_name)
                else:
                    self.db.remove_tag(doc_id, tag_name)
            elif action == delete_action:
                self.delete_document(doc_id)
        except Exception as e:
            logger.error(f"Error showing context menu: {e}")

    # show_gallery_context_menu() — REMOVED 2026-04-18.
    # Gallery widget deleted; minimalist list uses its own context menu.

    def add_new_tag(self, doc_id):
        """Add a new tag to the document"""
        tag, ok = QInputDialog.getText(
            self, 
            "New Tag", 
            "Enter tag name:",
            QLineEdit.Normal
        )
        
        if ok and tag:
            self.db.add_tag(doc_id, tag)

    def open_document_pdf(self, doc_id):
        """Open the document's PDF in default application"""
        try:
            doc_info = self.db.get_document_info(doc_id)
            if doc_info and os.path.exists(doc_info[1]):
                QDesktopServices.openUrl(QUrl.fromLocalFile(doc_info[1]))
            else:
                QMessageBox.warning(self, "File not found", "PDF file could not be found.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error opening PDF: {str(e)}")

    def preview_document(self, doc_id):
        """Preview document in a dialog"""
        try:
            doc_info = self.db.get_document_info(doc_id)
            if doc_info and os.path.exists(doc_info[1]):
                preview = PreviewDialog(doc_info[1], self)
                preview.exec_()
            else:
                QMessageBox.warning(self, "File not found", "PDF file could not be found.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error previewing document: {str(e)}")

    def delete_document(self, doc_id):
        """Delete a document after confirmation"""
        try:
            reply = QMessageBox.question(
                self, 'Xác nhận xóa',
                'Bạn có chắc chắn muốn xóa tài liệu này không?\nThao tác này không thể hoàn tác.',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply != QMessageBox.Yes:
                return

            # Hỏi có muốn xóa file PDF không
            delete_file_reply = QMessageBox.question(
                self, 'Xóa file PDF?',
                'Bạn có muốn xóa file PDF khỏi ổ đĩa không?',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            keep_file = (delete_file_reply == QMessageBox.No)
            self.db.delete_document(doc_id, keep_file=keep_file)

            if self.current_doc_id == doc_id:
                self.clear_current_view()
                self.current_doc_id = None

            self.load_documents()
            QMessageBox.information(self, "Hoàn tất", "Đã xóa tài liệu thành công")

        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Lỗi khi xóa tài liệu: {str(e)}")

    def delete_current_document(self):
        """Delete currently selected documents (from doc_list)."""
        doc_ids = self.doc_list.selected_doc_ids()

        if not doc_ids:
            if self.current_doc_id:
                self.delete_document(self.current_doc_id)
            else:
                QMessageBox.information(
                    self, "Không có tài liệu",
                    "Vui lòng chọn ít nhất một tài liệu để xóa",
                )
            return

        # Hiển thị thông báo xác nhận xóa
        if len(doc_ids) == 1:
            msg = 'Bạn có chắc chắn muốn xóa tài liệu này không?'
        else:
            msg = f'Bạn có chắc chắn muốn xóa {len(doc_ids)} tài liệu đã chọn không?'

        reply = QMessageBox.question(
            self, 'Xác nhận xóa tài liệu',
            f'{msg}\nThao tác này không thể hoàn tác.',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        # Xác nhận xóa file hay chỉ xóa dữ liệu
        delete_files_reply = QMessageBox.question(
            self, 'Xóa các file?',
            'Bạn có muốn xóa các file PDF khỏi ổ đĩa không?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        keep_files = (delete_files_reply == QMessageBox.No)

        # Đếm số tài liệu đã xóa thành công
        success_count = 0
        failed_ids = []
        logger.info(f"Attempting to delete {len(doc_ids)} documents: {doc_ids}")

        try:
            for doc_id in doc_ids:
                try:
                    self.db.delete_document(doc_id, keep_file=keep_files)
                    logger.info(f"Successfully deleted document ID: {doc_id}")
                    success_count += 1
                except Exception as e:
                    logger.error(f"Failed to delete document ID {doc_id}: {str(e)}")
                    failed_ids.append(doc_id)

            # Xóa view nếu tài liệu đang xem nằm trong danh sách xóa
            if self.current_doc_id in doc_ids:
                self.clear_current_view()
                self.current_doc_id = None

            # Cập nhật bảng tài liệu
            self.load_documents()

            # Thông báo kết quả
            if failed_ids:
                QMessageBox.warning(
                    self,
                    "Hoàn tất",
                    f"Đã xóa {success_count}/{len(doc_ids)} tài liệu.\n"
                    f"Không xóa được ID: {', '.join(map(str, failed_ids))}"
                )
            else:
                QMessageBox.information(
                    self,
                    "Hoàn tất",
                    f"Đã xóa thành công {success_count} tài liệu."
                )
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Xảy ra lỗi khi xóa tài liệu: {str(e)}")
            logger.error(f"Error during batch deletion: {str(e)}")
            self.load_documents()


    def add_file(self):
        """Add a single document file (PDF or image)"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Chọn tệp", "",
            "Tất cả (*.pdf *.png *.jpg *.jpeg *.tiff *.bmp);;PDF (*.pdf);;Hình ảnh (*.png *.jpg *.jpeg *.tiff *.bmp)"
        )
        if file_path:
            pdf_path = file_path

            preview = PreviewDialog(pdf_path, self)
            if preview.exec_() == QDialog.Accepted:
                self.process_file(pdf_path)

    def add_files(self):
        """Add multiple document files (PDF or images)"""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "Chọn tệp", "",
            "Tất cả (*.pdf *.png *.jpg *.jpeg *.tiff *.bmp);;PDF (*.pdf);;Hình ảnh (*.png *.jpg *.jpeg *.tiff *.bmp)"
        )
        if file_paths:
            pdf_paths = file_paths

            if pdf_paths:
                self.process_files(pdf_paths)

    def add_folder(self):
        """Recursively import a corpus folder, skipping already stored paths."""
        folder = QFileDialog.getExistingDirectory(
            self, "Chọn thư mục văn bản", self.settings.value("last_directory", "")
        )
        if not folder:
            return
        extensions = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp"}
        candidates = sorted(
            str(path) for path in Path(folder).rglob("*")
            if path.is_file() and path.suffix.casefold() in extensions
        )
        conn = self.db.conn_pool.get_connection()
        existing = {
            os.path.normcase(os.path.abspath(row[0]))
            for row in conn.execute("SELECT file_path FROM documents") if row[0]
        }
        pending = [
            path for path in candidates
            if os.path.normcase(os.path.abspath(path)) not in existing
        ]
        if not pending:
            QMessageBox.information(
                self, "Không có tệp mới", "Mọi văn bản trong thư mục đã được nhập."
            )
            return
        if QMessageBox.question(
            self, "Nhập thư mục văn bản",
            "Tìm thấy {} tệp, trong đó {} tệp mới. Tiến hành OCR và lập chỉ mục?".format(
                len(candidates), len(pending)
            ),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        ) == QMessageBox.Yes:
            self.settings.setValue("last_directory", folder)
            self.process_files(pending)

    def is_converted_pdf(self, pdf_path):
        """Check if a PDF was converted from Word (exists in temp folder)"""
        # Thêm điều kiện kiểm tra kỹ hơn
        return str(TEMP_DIR) in str(pdf_path) and "temp" in str(pdf_path).lower()

    def _reocr_current_document(self):
        """Re-run OCR on the currently displayed document."""
        if not hasattr(self, 'current_doc_id') or not self.current_doc_id:
            QMessageBox.information(self, "Thông báo", "Không có văn bản nào đang hiển thị.")
            return

        doc_info = self.db.get_document_info(self.current_doc_id)
        if not doc_info:
            QMessageBox.warning(self, "Lỗi", "Không tìm thấy thông tin văn bản.")
            return

        pdf_path = doc_info[1]
        if not os.path.exists(pdf_path):
            QMessageBox.warning(self, "Lỗi", f"Tệp không tìm thấy: {pdf_path}")
            return

        reply = QMessageBox.question(
            self, "OCR lại",
            f"Bạn có muốn nhận dạng lại văn bản này?\n{os.path.basename(pdf_path)}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.process_file(pdf_path)

    def process_file(self, file_path):
        """Process a single file (PDF or image)"""
        if _has_running_worker(self.ocr_worker, self.batch_worker):
            QMessageBox.information(self, "OCR đang chạy", "Vui lòng chờ tác vụ OCR hiện tại hoàn tất hoặc hủy tác vụ.")
            return
        progress = ProgressDialog(100, "Đang xử lý tệp", self)
        progress.setWindowModality(Qt.ApplicationModal)

        self.ocr_worker = OCRWorker(self.ocr_system, file_path)
        self._track_worker(self.ocr_worker)
        self.ocr_worker.progress.connect(progress.update_progress)

        def on_finished(results, detections):
            # Update progress to "Đang lưu kết quả..." before heavy work
            progress.update_progress(95, 100, "Đang lưu kết quả...")
            QApplication.processEvents()  # Force UI repaint before blocking

            # Process results (DB save, form fill, HTML render)
            self.ocr_completed(file_path, results, detections)
            progress.mark_completed()
            self._warn_incomplete_ocr(file_path, results, modal=True)

        self.ocr_worker.finished.connect(on_finished)
        self.ocr_worker.error.connect(self.show_error)
        progress.canceled.connect(self.cancel_ocr)

        self.ocr_worker.start()
        progress.exec_()

    def process_files(self, file_paths):
        """Process multiple files (batch OCR).

        2026-04-18 Phase 9: uses non-blocking FloatingProgress widget
        instead of modal ProgressDialog. User can continue working
        (review other docs, search, analytics) while batch OCR runs.
        Click minimize to collapse to compact pill; click X to cancel.
        """
        if _has_running_worker(self.ocr_worker, self.batch_worker):
            QMessageBox.information(self, "OCR đang chạy", "Vui lòng chờ tác vụ OCR hiện tại hoàn tất hoặc hủy tác vụ.")
            return

        from floating_progress import FloatingProgress

        total = len(file_paths)
        fp = FloatingProgress(parent=self, title=f"Đang xử lý {total} văn bản")
        self._batch_floating_progress = fp  # keep ref alive

        self._batch_processed_count = 0
        self.batch_worker = BatchProcessWorker(
            self.ocr_system, file_paths, collect_results=False
        )
        self._track_worker(self.batch_worker)
        self.batch_worker.document_ready.connect(self._save_batch_result)

        # Worker emits (current:int, total:int, message:str)
        # FloatingProgress wants (fraction:float, detail:str)
        def _on_progress(current, batch_total, message):
            fraction = float(current) / max(1, batch_total * total / 100 or 1)
            # worker emits pct 0-100 per file; convert to overall 0-1 below.
            # Simpler: current is 0-100 (pct within this file) × file index
            # tracking. Use worker's own semantics: current=pct, total=100, message.
            fraction = min(1.0, max(0.0, current / max(1, batch_total)))
            fp.set_progress(fraction, message or "")

        self.batch_worker.progress.connect(_on_progress)

        def on_batch_finished(results):
            del results
            self.load_documents()
            processed = getattr(self, "_batch_processed_count", 0)
            fp.mark_completed(f"Hoàn tất {processed}/{total} văn bản")
            fp.auto_close_after(3000)

        def on_batch_error(err_msg):
            logger.error(f"Batch error: {err_msg}")
            fp.set_title("Lỗi xử lý")
            fp.set_progress(1.0, f"Lỗi: {err_msg[:40]}")
            fp.auto_close_after(5000)

        self.batch_worker.finished.connect(on_batch_finished)
        self.batch_worker.error.connect(on_batch_error)

        # Floating progress cancel → batch cancel (cooperative)
        fp.cancelled.connect(self.cancel_batch)

        fp.show_above()  # non-blocking — user keeps working
        self.batch_worker.start()

    def cancel_ocr(self):
        """Cancel OCR process — cooperative, does NOT terminate mid-request.

        Sets canceled=True flag and lets the worker exit naturally at the next
        progress checkpoint (between pages). DOES NOT call terminate(), which
        would kill the QThread mid-HTTP-request and corrupt the shared
        httpx.Client state in LlamaOCR — causing subsequent OCR requests on
        the same MainWindow instance to return empty HTML (bug reported
        2026-04-17). Worker finishes current page then exits.
        """
        if self.ocr_worker and self.ocr_worker.isRunning():
            self.ocr_worker.cancel()
            # Optional brief wait so user sees ending; no forced terminate.
            self.ocr_worker.wait(200)

    def cancel_batch(self):
        """Cancel batch — cooperative, no terminate (see cancel_ocr for why)."""
        if self.batch_worker and self.batch_worker.isRunning():
            self.batch_worker.cancel()
            self.batch_worker.wait(200)
            if False and self.batch_worker.isRunning():  # disabled: see cancel_ocr
                self.batch_worker.terminate()

    def _dispatch_embedding(self, doc_id):
        """Reindex the latest document version without blocking the UI."""
        try:
            version = self.db.get_latest_version(doc_id)
            if not version:
                return
            workers = getattr(self, '_embed_workers', None)
            if workers is None:
                workers = self._embed_workers = []
            worker = EmbeddingWorker(
                self.embedding_model, self.vector_store, doc_id, version
            )
            self._track_worker(worker)
            worker.done.connect(
                lambda did, count: logger.info(f"[embed] doc {did}: {count} chunks")
            )
            worker.error.connect(
                lambda did, message: logger.warning(f"[embed] doc {did} failed: {message}")
            )
            worker.finished.connect(
                lambda worker=worker: workers.remove(worker) if worker in workers else None
            )
            workers.append(worker)
            worker.start()
        except Exception as exc:
            logger.warning(f"AI embedding dispatch failed for doc {doc_id}: {exc}")

    def _dispatch_relations(self, doc_id):
        """Refresh the legal graph for the latest version in the background."""
        try:
            version = self.db.get_latest_version(doc_id)
            if not version:
                return
            workers = getattr(self, '_relation_workers', None)
            if workers is None:
                workers = self._relation_workers = []
            worker = RelationWorker(self.db.db_path, doc_id, version)
            self._track_worker(worker)
            worker.done.connect(
                lambda did, count: logger.info(f"[relations] doc {did}: {count} links")
            )
            worker.error.connect(
                lambda did, message: logger.warning(
                    f"[relations] doc {did} failed: {message}"
                )
            )
            worker.finished.connect(
                lambda worker=worker: workers.remove(worker) if worker in workers else None
            )
            workers.append(worker)
            worker.start()
        except Exception as exc:
            logger.warning(f"Legal relation dispatch failed for doc {doc_id}: {exc}")

    def _warn_incomplete_ocr(self, file_path, results, modal=False):
        """Tell the user which pages the OCR quality gate could not recover.

        The document is saved without those pages' faulty text; the user
        should check it and run "OCR lại". Returns True when a warning was
        shown.
        """
        warning = (results or {}).get("OCR_Warning") if isinstance(results, dict) else ""
        if not warning:
            return False
        name = os.path.basename(str(file_path))
        message = f"{name}: {warning}"
        logger.warning(message)
        try:
            self.statusBar().showMessage(message, 15000)
        except Exception:
            pass
        if modal:
            QMessageBox.warning(self, "OCR chưa trọn vẹn", message)
        else:
            try:
                from toast_widget import toast_warning

                toast_warning(self, message, 12000)
            except Exception as exc:
                logger.debug(f"Toast unavailable: {exc}")
        return True

    def ocr_completed(self, file_path, results, detections):
        """Handle completion of OCR process for a single file"""
        try:
            permanent_path, cleanup_path = materialize_document_source(
                file_path, TEMP_DIR, OUTPUT_DIR
            )
            permanent_file_path = str(permanent_path)
            
            # Add document to database với đường dẫn vĩnh viễn
            current_model = self.ocr_system.ocr_model.current_model_key
            doc_id = self.db.add_document(permanent_file_path, results, model_key=current_model)
            
            # Format và chuẩn hóa detections trước khi lưu vào DB
            try:
                for i, page_detection in enumerate(detections):
                    # Nếu page_detection là dictionary, chuyển sang JSON
                    if isinstance(page_detection, dict):
                        self.db.add_page_detections(doc_id, i+1, page_detection)
                    # Nếu page_detection là list, chuyển từng item sang JSON
                    elif isinstance(page_detection, list):
                        for item in page_detection:
                            if isinstance(item, dict):
                                self.db.add_page_detections(doc_id, i+1, item)
            except Exception as e:
                logger.error(f"Error formatting detections: {str(e)}")
            
            # Refresh document list and show the new document
            # processEvents() between heavy steps to keep UI responsive
            self.load_documents()
            QApplication.processEvents()
            self.show_document(doc_id)
            QApplication.processEvents()

            self._dispatch_embedding(doc_id)
            self._dispatch_relations(doc_id)

            # Add to recent files
            self.settings.setValue("last_directory", str(Path(permanent_file_path).parent))
            
            # Clean up temporary converted PDF if needed
            if cleanup_path is not None:
                try:
                    remove_temporary_source(cleanup_path)
                    logger.info(f"Removed temporary source: {cleanup_path}")
                except Exception as e:
                    logger.warning(f"Could not remove temporary source: {str(e)}")

            # Không cần hiện thêm thông báo vì ProgressDialog đã hiện "Xử lý hoàn tất 100%"

        except Exception as e:
            logger.error(f"Error in OCR completion: {str(e)}")
            QMessageBox.critical(self, "Error", f"Error saving results: {str(e)}")

    def batch_completed(self, results):
        """Handle completion of batch OCR process"""
        try:
            num_processed = 0
            for file_path, result, detections in results:
                try:
                    self._save_batch_result(file_path, result, detections)
                    num_processed += 1
                except Exception as e:
                    logger.error(f"Error processing {file_path}: {str(e)}")
            
            # Refresh document list
            self.load_documents()
            
            if results:
                # Remember last directory
                self.settings.setValue("last_directory", str(Path(results[0][0]).parent))

            # Không cần hiện thêm thông báo vì ProgressDialog đã hiện "Xử lý hoàn tất 100%"
            logger.info(f"Batch processing complete: {num_processed}/{len(results)} files")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error in batch processing: {str(e)}")

    def _save_batch_result(self, file_path, result, detections):
        """Persist and dispatch indexing immediately so long batches are resumable."""
        permanent_path, cleanup_path = materialize_document_source(
            file_path, TEMP_DIR, OUTPUT_DIR
        )
        current_model = self.ocr_system.ocr_model.current_model_key
        doc_id = self.db.add_document(
            str(permanent_path), result, model_key=current_model
        )
        for page_number, page_detection in enumerate(detections or [], 1):
            items = page_detection if isinstance(page_detection, list) else [page_detection]
            for item in items:
                if isinstance(item, dict):
                    self.db.add_page_detections(doc_id, page_number, item)
        self._dispatch_embedding(doc_id)
        self._dispatch_relations(doc_id)
        self.chat_panel.refresh_knowledge_status()
        self._warn_incomplete_ocr(file_path, result, modal=False)
        self._batch_processed_count = getattr(self, "_batch_processed_count", 0) + 1
        if cleanup_path is not None:
            remove_temporary_source(cleanup_path)
        logger.info("Batch document persisted and queued for indexing: %s", file_path)
        return doc_id

    def show_error(self, error_msg):
        """Display error message"""
        QMessageBox.critical(self, "Error", f"An error occurred: {error_msg}")

    def load_documents(self):
        """Load all documents from database.

        Performance fix 2026-04-18: skip update_document_gallery() — the
        gallery QTableWidget is never shown (tab removed). Previously
        wasted 10-30s on 300-doc refresh generating thumbnails via
        fitz.get_pixmap for a widget user never sees.
        """
        try:
            documents = self.db.get_all_documents()
            self.update_document_table(documents)
            # update_document_gallery(documents) — DEAD, gallery tab removed
            # update_document_table already populates the minimalist list
            # and the count beside "Kho văn bản".
            if self._document_open:
                # A saved edit may have changed the number or the type.
                self._update_toolbar_context(*self._toolbar_doc_file)

        except Exception as e:
            logger.error(f"Error loading documents: {str(e)}")
            QMessageBox.critical(self, "Error", f"Error loading documents: {str(e)}")

    @staticmethod
    def _doc_of(entry):
        """The document tuple of a snapshot entry (plain, or (doc, stats))."""
        return entry[0] if isinstance(entry, tuple) and len(entry) == 2 \
            and isinstance(entry[1], dict) else entry

    @staticmethod
    def _doc_title(doc) -> str:
        """What a library row shows: the number, else the file name."""
        so_hieu = (doc[8] or "").strip() if len(doc) > 8 else ""
        return so_hieu or ((doc[2] or "") if len(doc) > 2 else "")

    @staticmethod
    def _doc_matches(doc, text: str) -> bool:
        """Case-insensitive substring over the file name and the number."""
        if not text:
            return True
        hay = " ".join(str(doc[i] or "") for i in (2, 8) if len(doc) > i)
        return text in hay.lower()

    @staticmethod
    def _issued_key(value):
        """dd/mm/yyyy (or yyyy-mm-dd) -> (y, m, d); None when unreadable."""
        import re as _re
        match = _re.match(r"\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", value or "")
        if match:
            return int(match.group(3)), int(match.group(2)), int(match.group(1))
        match = _re.match(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})", value or "")
        if match:
            return int(match.group(1)), int(match.group(2)), int(match.group(3))
        return None

    def _sorted_docs(self, entries):
        """Order snapshot entries by the sort chosen in the library menu."""
        import re as _re
        key = getattr(self, "_doc_sort_key", "date_desc")
        doc_of = self._doc_of

        def doc_id(entry):
            try:
                return int(doc_of(entry)[0])
            except (TypeError, ValueError, IndexError):
                return 0

        if key in ("issued_desc", "issued_asc"):
            issued = getattr(self, "_doc_issued", {})
            dated = [e for e in entries if self._issued_key(issued.get(doc_id(e)))]
            undated = [e for e in entries if not self._issued_key(issued.get(doc_id(e)))]
            dated.sort(key=lambda e: (self._issued_key(issued.get(doc_id(e))), doc_id(e)),
                       reverse=key == "issued_desc")
            return dated + undated  # documents without a date stay at the end
        if key in ("name_asc", "name_desc"):
            def natural(entry):  # "23/KH" before "105/KH"
                parts = _re.split(r"(\d+)", self._doc_title(doc_of(entry)).casefold())
                return [int(p) if p.isdigit() else p for p in parts]
            return sorted(entries, key=natural, reverse=key == "name_desc")
        return sorted(entries, key=lambda e: (str(doc_of(e)[3] or ""), doc_id(e)),
                      reverse=key != "date_asc")

    def _update_library_count(self, shown: int) -> None:
        total = len(getattr(self, "_docs_snapshot", None) or [])
        label = self._library_count_label
        if shown == total:
            label.setText(str(total))
            label.setToolTip(f"{total} văn bản trong kho")
        else:
            label.setText(f"{shown} / {total}")
            label.setToolTip(f"Đang hiện {shown} trong {total} văn bản")

    def _on_doc_filter_changed(self, text: str = None) -> None:
        """Show the library through the filter text and the chosen order.

        Case-insensitive substring over the file name and the number, on the
        cached snapshot (no DB hit); the selection survives the rebuild.
        """
        if getattr(self, "_docs_snapshot", None) is None:
            return
        if text is None:
            text = self._doc_filter_input.text()
        text = (text or "").strip().lower()
        shown = self._sorted_docs([
            entry for entry in self._docs_snapshot
            if self._doc_matches(self._doc_of(entry), text)
        ])
        selected = self.doc_list.selected_doc_ids()
        self._rebuild_doc_list_only(shown)
        for doc_id in selected:
            self.doc_list.find_and_select(doc_id)
        self._update_library_count(len(shown))

    def _lazy_load_hover_meta(self, doc_id: int, _item) -> None:
        """Fetch loai_vb + nd_chinh + van_ban preview from DB on first hover.

        Caches per doc_id so repeat hovers don't re-query.

        2026-04-18 fix: many documents have empty nd_chinh (Chandra parser
        reports "ND_Chinh: (empty)" for ~40% of pages). Fall back to a
        preview of van_ban_dang_txt (full OCR text) so the tooltip
        always shows something useful — matches what user actually sees
        in the panel.
        """
        if doc_id in self._hover_meta_cache:
            return
        self._hover_meta_cache.add(doc_id)
        try:
            version = self.db.get_latest_version(doc_id)
            if not version:
                return

            patch = {}
            # [5]=so_ki_hieu, [9]=loai_vb, [10]=nd_chinh, [14]=van_ban_dang_txt
            if len(version) > 5 and version[5]:
                patch['so_hieu'] = str(version[5]).strip()
            if len(version) > 9 and version[9]:
                patch['loai_vb'] = str(version[9]).strip()

            nd_chinh = str(version[10] or '').strip() if len(version) > 10 else ''
            van_ban = str(version[14] or '').strip() if len(version) > 14 else ''

            # Strip any inline HTML tags from van_ban before using as preview
            # (van_ban may be stored as Chandra HTML markup).
            if van_ban:
                import re as _re
                van_ban_plain = _re.sub(r'<[^>]+>', ' ', van_ban)
                van_ban_plain = _re.sub(r'\s+', ' ', van_ban_plain).strip()
            else:
                van_ban_plain = ''

            # Prefer nd_chinh when non-empty; else fallback to van_ban preview.
            if nd_chinh:
                patch['nd_chinh'] = nd_chinh
            elif van_ban_plain:
                preview = van_ban_plain[:500]
                if len(van_ban_plain) > 500:
                    preview += '...'
                patch['nd_chinh'] = preview

            # Status hint based on full van_ban length.
            if van_ban_plain:
                if len(van_ban_plain) >= 300:
                    patch['_reclassify_status'] = 'done'
                else:
                    patch['_reclassify_status'] = 'partial'
            else:
                patch['_reclassify_status'] = 'pending'

            self.doc_list.update_item_meta(doc_id, patch)
        except Exception as e:
            logger.debug(f"Lazy hover meta for doc_id={doc_id} failed: {e}")

    def _on_doc_sort_changed(self, key) -> None:
        """A sort chosen in the library menu: re-render, keep the selection."""
        if key not in self._doc_sort_actions:
            return
        self._doc_sort_key = key
        self._doc_sort_actions[key].setChecked(True)
        self._update_doc_sort_tooltip()
        self._on_doc_filter_changed()

    def _update_doc_sort_tooltip(self) -> None:
        label = dict(item for item in self.DOC_SORTS if item[0])[self._doc_sort_key]
        self._doc_sort_btn.setToolTip(chrome_tooltip("Sắp xếp", hint=f"Đang theo: {label}"))
        # A non-default order shows as a checked (olive) button.
        self._doc_sort_btn.setCheckable(True)
        self._doc_sort_btn.setChecked(self._doc_sort_key != "date_desc")

    def _rebuild_doc_list_only(self, documents) -> None:
        """Rebuild visible list using BATCHED DB query for previews.

        Performance fix 2026-04-18: previous impl called get_latest_version
        per doc → N+1 query pattern (300 docs × 5ms = 1.5s UI freeze).
        Now: single query via get_latest_previews_batch(ids) → ~20ms total.
        """
        import re as _re

        # Extract doc_ids + build list entries (no DB yet).
        entries = []
        for item in documents:
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], dict):
                doc, stats = item[0], item[1]
            else:
                doc, stats = item, {'total_frequency': 0, 'keyword_frequencies': {}, 'snippets': []}
            try:
                doc_id = int(doc[0])
            except (ValueError, TypeError):
                continue
            entries.append((doc_id, doc, stats))

        # ONE batched DB query for all previews.
        previews = self.db.get_latest_previews_batch([e[0] for e in entries])

        # Strip HTML once per doc (vectorized regex) instead of in loop.
        tag_re = _re.compile(r'<[^>]+>')
        ws_re = _re.compile(r'\s+')

        self.doc_list.clear()
        for doc_id, doc, stats in entries:
            filename = doc[2] or "(không tên)"
            preview_data = previews.get(doc_id, {})
            loai_vb_val = preview_data.get("loai_vb", "")
            nd_chinh = preview_data.get("nd_chinh", "")
            vb_preview_raw = preview_data.get("van_ban_preview", "")

            van_ban_preview = ""
            if vb_preview_raw:
                vb_plain = tag_re.sub(' ', vb_preview_raw)
                van_ban_preview = ws_re.sub(' ', vb_plain).strip()

            if not nd_chinh and van_ban_preview:
                nd_chinh = van_ban_preview[:500] + ('...' if len(van_ban_preview) > 500 else '')

            meta = {
                'so_hieu': (doc[8] or '').strip() if len(doc) > 8 else '',
                'nd_chinh': nd_chinh,
                'loai_vb': loai_vb_val,
                'pages': doc[4] if len(doc) > 4 and doc[4] else 0,
                'urgency': (doc[10] or '').strip() if len(doc) > 10 else '',
                'size_mb': compute_file_size_mb(doc[1] if len(doc) > 1 else None),
                'filename': filename,
                'path': doc[1] if len(doc) > 1 else '',
                'issued': getattr(self, '_doc_issued', {}).get(doc_id, ''),
            }

            if van_ban_preview and len(van_ban_preview) >= 300:
                status = 'done'
            elif van_ban_preview or nd_chinh:
                status = 'partial'
            else:
                status = 'pending'
            self.doc_list.add_document(doc_id, filename, status, stats, meta)

    def update_document_table(self, documents):
        """
        Populate the minimalist document list + hidden data cache table.

        Supports two input shapes:
            - legacy: list of document tuples
            - new:    list of (document_tuple, search_stats_dict)
        """
        # Snapshot for sort re-renders.
        self._docs_snapshot = list(documents)
        self._doc_issued = {}  # doc_id -> ngày ban hành, for the rows and the sort
        filter_text = self._doc_filter_input.text().strip().lower() \
            if hasattr(self, "_doc_filter_input") else ""
        sort_key = getattr(self, "_doc_sort_key", "date_desc")
        if not sort_key.startswith("issued"):
            documents = self._sorted_docs(documents)

        # Visible minimalist list — rebuild from scratch.
        self.doc_list.clear()

        for row, item in enumerate(documents):
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], dict):
                doc = item[0]
                stats = item[1]
            else:
                doc = item
                stats = {'total_frequency': 0, 'keyword_frequencies': {}, 'snippets': []}

            try:
                doc_id = int(doc[0])
            except (ValueError, TypeError):
                continue

            filename = doc[2] or "(không tên)"

            # Tooltip metadata — populate EAGERLY from DB.
            # 2026-04-18 v5 fix: user reported ND chính missing in most
            # tooltips. Root cause: lazy-load via itemEntered signal was
            # unreliable on Windows (driver/composer swallowed it), plus
            # Qt tooltip caches text at first display so updates after
            # show were invisible. For typical lists (≤200 docs) the ~50-
            # 100ms upfront DB cost is invisible, and tooltips are now
            # correct on FIRST hover.
            nd_chinh = ''
            loai_vb_val = ''
            van_ban_preview = ''
            issued = ''
            try:
                version = self.db.get_latest_version(doc_id)
                if version:
                    if len(version) > 6 and version[6]:
                        issued = str(version[6]).strip()
                    if len(version) > 9 and version[9]:
                        loai_vb_val = str(version[9]).strip()
                    if len(version) > 10 and version[10]:
                        nd_chinh = str(version[10]).strip()
                    if len(version) > 14 and version[14]:
                        vb = str(version[14]).strip()
                        import re as _re
                        vb_plain = _re.sub(r'<[^>]+>', ' ', vb)
                        vb_plain = _re.sub(r'\s+', ' ', vb_plain).strip()
                        van_ban_preview = vb_plain
            except Exception as e:
                logger.debug(f"Tooltip data for doc {doc_id} failed: {e}")

            # If nd_chinh empty, fall back to van_ban preview.
            if not nd_chinh and van_ban_preview:
                nd_chinh = van_ban_preview[:500] + ('...' if len(van_ban_preview) > 500 else '')

            meta = {
                'so_hieu': (doc[8] or '').strip() if len(doc) > 8 else '',
                'nd_chinh': nd_chinh,
                'loai_vb': loai_vb_val,
                'pages': doc[4] if len(doc) > 4 and doc[4] else 0,
                'urgency': (doc[10] or '').strip() if len(doc) > 10 else '',
                'size_mb': compute_file_size_mb(doc[1] if len(doc) > 1 else None),
                'filename': filename,
                'path': doc[1] if len(doc) > 1 else '',
                'issued': issued,
            }
            self._doc_issued[doc_id] = issued

            # Status based on actual van_ban length (not heuristic).
            if van_ban_preview and len(van_ban_preview) >= 300:
                status = 'done'
            elif van_ban_preview or nd_chinh:
                status = 'partial'
            else:
                status = 'pending'
            if self._doc_matches(doc, filter_text):
                self.doc_list.add_document(doc_id, filename, status, stats, meta)

        if sort_key.startswith("issued"):
            self._on_doc_filter_changed()  # needs the dates gathered above
        else:
            self._update_library_count(self.doc_list.count())
        if self.current_doc_id is not None:
            self.doc_list.find_and_select(self.current_doc_id)

    # PURGED 2026-04-18: Gallery widget removed from UI. Dead code deleted:
    #   - Earlier show_document duplicate (Python used the later one anyway)
    #   - update_document_gallery() — generated thumbnails via fitz for a
    #     widget that was never displayed (10-30s waste on 300 docs)
    #   - load_thumbnail_for_item() — wrote thumb_*.jpg to disk
    #   - gallery_item_selected() — no longer reachable
    # Cached thumbnails in output/images/thumb_*.jpg can be deleted manually
    # (no longer written or read).

    def show_document(self, doc_id, page=None):
        """Display a document and its data"""
        try:
            # Get document data
            doc_version = self.db.get_latest_version(doc_id)
            doc_info = self.db.get_document_info(doc_id)
            
            if not doc_version or not doc_info:
                QMessageBox.warning(self, "Warning", "Document data not found")
                return
                        
            # Store current document ID
            self.current_doc_id = doc_id  # Đảm bảo lưu đúng ID hiện tại
            self._document_open = True
            self._details_user_choice = None
            QTimer.singleShot(0, self._apply_responsive_layout)
            
            # Load document content in editor FIRST - đặt ID trước rồi mới load data
            self.ocr_editor.doc_id = doc_id  # Đặt lại ID một cách rõ ràng
            self.ocr_editor.db = self.db    # Đảm bảo DB reference luôn được cập nhật
            self.ocr_editor.load_data(doc_version)
            self.ocr_editor.update_suggestions(self.db)

            # Update Z-pattern header — spec 2026-04-18: show
            # "{loại VB}  ·  Số: {SỐ HIỆU IN HOA}" with so_ki_hieu emphasized.
            try:
                filename = os.path.basename(doc_info[1]) if doc_info and doc_info[1] else ""
                loai_vb = doc_version[9] if doc_version and len(doc_version) > 9 else ""
                so_ki_hieu = doc_version[5] if doc_version and len(doc_version) > 5 else ""
                if hasattr(self.ocr_editor, 'set_header_info'):
                    self.ocr_editor.set_header_info(loai_vb, so_ki_hieu, filename)
                elif hasattr(self.ocr_editor, 'set_current_filename'):
                    self.ocr_editor.set_current_filename(filename)
                # Mirror into the toolbar context label (middle of top bar).
                page_count = int(doc_info[7]) if doc_info and len(doc_info) > 7 and doc_info[7] else 0
                self._update_toolbar_context(filename, page_count)
            except Exception:
                pass

            # Load document versions (needed for metadata page count display)
            versions = self.db.get_document_versions(doc_id)

            # Update metadata display
            self.update_metadata_display(doc_info, doc_version)
            
            # Load PDF asynchronously — 2026-04-18 fix for UI freeze on
            # large PDFs. Sync load_pdf() blocks for 1-5s via pdf2image.
            pdf_path = doc_info[1]
            if os.path.exists(pdf_path):
                def _after_pdf_load(success, _did=doc_id):
                    if success and self.current_doc_id == _did:
                        if page is not None and self.pdf_viewer.pages:
                            self.pdf_viewer.current_page = min(
                                max(int(page) - 1, 0),
                                len(self.pdf_viewer.pages) - 1,
                            )
                            self.pdf_viewer.update_page_display()
                            self.pdf_viewer.update_controls()
                        self.load_page_detections(_did, self.pdf_viewer.current_page)
                    elif not success:
                        self.detections_by_page = {}
                self.pdf_viewer.load_pdf_async(pdf_path, on_done=_after_pdf_load)
            else:
                QMessageBox.warning(self, "Warning", f"Tệp không tìm thấy: {pdf_path}")
                self.pdf_viewer.clear()
                self.detections_by_page = {}

            # Update status bar
            self.update_status_info(doc_id, doc_info)
                    
        except Exception as e:
            logger.error(f"Error showing document: {str(e)}")
            traceback.print_exc()
            QMessageBox.warning(self, "Error", f"Error loading document: {str(e)}")

    def load_page_detections(self, doc_id, page_num):
        """Load detections for a specific page"""
        if not doc_id or page_num is None or not hasattr(self.pdf_viewer, 'set_detection_boxes'):
            return
            
        try:
            detections = self.db.get_document_detections(doc_id, page_num)
            
            if isinstance(detections, list):  # Ensure detections is a list
                boxes = []
                for d in detections:
                    if isinstance(d, dict) and 'box' in d:
                        box = d.get('box', [])
                        if len(box) >= 4:
                            boxes.append(box)
                
                self.pdf_viewer.set_detection_boxes(boxes)
            else:
                self.pdf_viewer.set_detection_boxes([])
                
        except Exception as e:
            print(f"Error loading page detections: {str(e)}")  
            self.pdf_viewer.set_detection_boxes([])

    def update_metadata_display(self, doc_info, doc_version):
        """Update metadata information display"""
        try:
            if not doc_info or not doc_version:
                for field in self.metadata_fields.values():
                    field.clear()
                return
                    
            # Basic info
            self.metadata_fields['id'].setText(str(doc_info[0]))
            self.metadata_fields['filename'].setText(doc_info[2])
            self.metadata_fields['created'].setText(str(doc_info[3]))
            self.metadata_fields['modified'].setText(str(doc_info[4]))
            
            # Fix: Hiển thị số trang đúng
            # page_count ở vị trí index 7 trong bảng documents
            if len(doc_info) > 7 and doc_info[7] is not None:
                self.metadata_fields['pages'].setText(str(doc_info[7]))
            else:
                # Nếu không có thông tin số trang trong DB, thử đọc từ file PDF
                file_path = doc_info[1]
                if os.path.exists(file_path) and file_path.lower().endswith('.pdf'):
                    try:
                        with fitz.open(file_path) as pdf:
                            page_count = len(pdf)
                            self.metadata_fields['pages'].setText(str(page_count))
                            
                            # Cập nhật page_count vào DB
                            if self.current_doc_id:
                                try:
                                    conn = self.db.conn_pool.get_connection()
                                    cursor = conn.cursor()
                                    cursor.execute(
                                        'UPDATE documents SET page_count = ? WHERE id = ?',
                                        (page_count, self.current_doc_id)
                                    )
                                    conn.commit()
                                except Exception as e:
                                    logger.error(f"Error updating page count: {str(e)}")
                    except:
                        self.metadata_fields['pages'].setText("1")
                else:
                    self.metadata_fields['pages'].setText("N/A")
            
            # File size
            file_path = doc_info[1]
            if os.path.exists(file_path):
                size_bytes = os.path.getsize(file_path)
                if size_bytes < 1024:
                    self.metadata_fields['filesize'].setText(f"{size_bytes} B")
                elif size_bytes < 1024 * 1024:
                    self.metadata_fields['filesize'].setText(f"{size_bytes/1024:.1f} KB")
                else:
                    self.metadata_fields['filesize'].setText(f"{size_bytes/(1024*1024):.1f} MB")
            else:
                self.metadata_fields['filesize'].setText("Không tìm thấy tệp")
                    
            # Version count
            versions = self.db.get_document_versions(doc_info[0])
            self.metadata_fields['versions'].setText(str(len(versions)))
            
            # Document content fields
            self.metadata_fields['cqbh'].setText(f"{doc_version[3] or ''}\n{doc_version[4] or ''}")
            self.metadata_fields['so_kh'].setText(doc_version[5] or '')
            self.metadata_fields['loai_vb'].setText(doc_version[9] or '')  # Chỉ số 9 là loai_vb trong bảng
            
            # Độ khẩn with color coding
            do_khan = doc_version[7] or 'Không'  # Chỉ số 7 là do_khan trong bảng
            self.metadata_fields['do_khan'].setText(do_khan)
            
            # Độ mật
            do_mat = doc_version[8] or 'Không'  # Chỉ số 8 là do_mat trong bảng
            self.metadata_fields['do_mat'].setText(do_mat)

            # NĐ30 urgency and classification in their token colours (theme
            # stylesheet, [level=...]), so they read in both themes.
            levels = {
                'Hỏa Tốc': 'hoa_toc', 'Thượng Khẩn': 'thuong_khan', 'Khẩn': 'khan',
                'Mật': 'mat', 'Tối Mật': 'toi_mat', 'Tuyệt Mật': 'tuyet_mat',
            }
            for key, value in (('do_khan', do_khan), ('do_mat', do_mat)):
                field = self.metadata_fields[key]
                field.setProperty("level", levels.get(value, ""))
                field.style().unpolish(field)
                field.style().polish(field)
                    
        except Exception as e:
            logger.error(f"Error updating metadata: {str(e)}")
            for field in self.metadata_fields.values():
                field.clear()

    def update_status_info(self, doc_id, doc_info):
        """Update status bar with document info"""
        if doc_info and os.path.exists(doc_info[1]):
            self.doc_info_label.setText(os.path.basename(doc_info[1]))
            self.doc_info_label.setToolTip(f"Mã {doc_id}\n{doc_info[1]}")
        else:
            self.doc_info_label.setText("")
            self.doc_info_label.setToolTip("")

    def document_selected_by_id(self, doc_id):
        """Handle document selection from the new DocumentListWidget."""
        try:
            self.chat_panel.set_scope_doc_ids([int(doc_id)])
            self.show_document(int(doc_id))
            if self.current_search_keywords:
                if hasattr(self, 'pdf_viewer'):
                    self.pdf_viewer.set_highlight_text(self.current_search_keywords)
                if hasattr(self, 'ocr_editor'):
                    QTimer.singleShot(
                        100,
                        lambda: self.ocr_editor.highlight_keywords(self.current_search_keywords),
                    )
        except Exception as e:
            logger.error(f"Error selecting document: {e}")

    def _toggle_chat_panel(self, visible):
        self.chat_panel.setVisible(bool(visible))
        if visible:
            self.chat_panel.raise_()

    def _open_chat_document(self, doc_id, page):
        """Open a citation in the in-app viewer at its 1-based PDF page."""
        doc_id = int(doc_id)
        self.doc_list.find_and_select(doc_id)
        self.chat_panel.set_scope_doc_ids([doc_id])
        self.show_document(doc_id, int(page or 1))

    def update_document(self, doc_id, updates):
        """Update document with new data"""
        try:
            # Verify the document ID matches current selection
            if doc_id != self.current_doc_id:
                raise ValueError(f"Document ID mismatch: trying to update {doc_id} but current document is {self.current_doc_id}")
                
            # Lấy tên đăng nhập của người dùng để lưu vào phiên bản mới
            # Uu tiên lưu LUON là tên đăng nhập, không phải tên đầy đủ
            modified_by = self.current_user.get("username", "OCR System")
                
            # Create new version với thông tin người sửa
            new_version = self.db.create_new_version(doc_id, updates, modified_by=modified_by)
            
            if new_version:
                # Refresh document list
                self.load_documents()
                
                # Reload the same document
                self.show_document(doc_id)

                # A manual edit can change the number, signer or cited
                # documents, so the legal graph is refreshed with the chunks.
                self._dispatch_embedding(doc_id)
                self._dispatch_relations(doc_id)

                # Update suggestions
                self.ocr_editor.update_suggestions(self.db)
                
                self.statusBar().showMessage(f"Document {doc_id} updated successfully", 5000)
            else:
                raise Exception("Failed to create new version")
                
        except Exception as e:
            logger.error(f"Error updating document: {str(e)}")
            QMessageBox.critical(self, "Error", f"Error updating document: {str(e)}")

    def field_value_changed(self, field_name, new_value):
        """Handle field value change for real-time updates"""
        # This can be used for real-time validation or UI updates
        # without waiting for save
        pass

    def search_documents(self):
        """Search đã được chuyển sang SearchDialog. Method này giữ để backward compatible."""
        self.show_search_dialog()

    def show_search_dialog(self):
        """Mở dialog tìm kiếm từ khóa"""
        dialog = SearchDialog(self.db, parent=self)
        dialog.document_selected.connect(self.handle_search_result)
        dialog.exec_()

    def show_ai_search_dialog(self):
        """Mở dialog tìm kiếm AI ngữ nghĩa"""
        dialog = AISearchDialog(
            self.db, self.embedding_model, self.vector_store,
            reranker=self.reranker, parent=self
        )
        dialog.document_selected.connect(self.handle_search_result)
        dialog.exec_()

    def show_reindex_dialog(self):
        """Rebuild semantic, FTS and legal-graph indexes off the UI thread."""
        reply = QMessageBox.question(
            self,
            "Xây lại chỉ mục AI",
            "Xây lại chỉ mục cho toàn bộ kho văn bản?\n"
            "Quá trình có thể mất vài phút và có thể dừng an toàn.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        dialog = ReindexDialog(
            self.db.db_path,
            self.embedding_model,
            parent=self,
            worker_tracker=self._track_worker,
        )
        self._reindex_dialog = dialog
        dialog.completed.connect(self._on_full_reindex_completed)
        dialog.exec_()
        self._reindex_dialog = None

    def _on_full_reindex_completed(self, count):
        vector_index = getattr(self.chat_panel, "_vector_index", None)
        if vector_index is not None and not vector_index.is_native():
            vector_index.sync_from_chunks(self.chat_panel._db_conn)
        self.statusBar().showMessage(
            "Đã xây lại chỉ mục AI cho {} văn bản".format(count), 5000
        )

    def handle_search_result(self, doc_id, keywords):
        """Xử lý khi chọn tài liệu từ kết quả tìm kiếm"""
        # 1. Lưu keywords để highlight
        self.current_search_keywords = keywords

        # 2. Tìm và select item trong danh sách tài liệu minimalist
        try:
            self.doc_list.find_and_select(int(doc_id))
        except (ValueError, TypeError):
            pass

        # 3. Hiện dialog hỏi xem đầy đủ hay preview
        msg = QMessageBox(self)
        msg.setWindowTitle("Xem tài liệu")
        msg.setText("Bạn muốn xem đầy đủ hay preview?")
        full_btn = msg.addButton("Xem đầy đủ", QMessageBox.AcceptRole)
        preview_btn = msg.addButton("Preview", QMessageBox.ActionRole)
        msg.addButton("Hủy", QMessageBox.RejectRole)
        msg.exec_()

        clicked = msg.clickedButton()
        if clicked == full_btn:
            # Mở PDF bằng trình đọc bên ngoài
            self.open_document_pdf(doc_id)
        elif clicked == preview_btn:
            # Hiện trong app + highlight từ khóa
            self.show_document(doc_id)
            # Highlight từ khóa trong OCR output
            if keywords:
                QTimer.singleShot(200, lambda: self.highlight_search_keywords(keywords))

    def highlight_search_keywords(self, keywords):
        """Mark the search keywords in the full text of the open document.

        The editor owns the marks: it paints them in the theme's colours
        (gold wash, text colour on top, readable in dark too), clears the
        earlier ones and paints them again after a theme switch.
        """
        editor = getattr(self, 'ocr_editor', None)
        if editor is not None:
            editor.highlight_keywords(keywords)

    def export_to_excel(self):
        """Export all documents to Excel with optional filtering"""
        try:
            # Import custom export methods
            from export_methods_updated import export_to_excel as excel_exporter
            
            # Get export file path
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Xuất ra Excel", "", "Excel Files (*.xlsx)"
            )
            
            if file_path:
                if not file_path.endswith('.xlsx'):
                    file_path += '.xlsx'
                
                # Export tất cả documents
                filter_criteria = {}

                # Sử dụng hàm xuất Excel từ module riêng biệt
                excel_exporter(self.db.conn_pool, file_path, filter_criteria)
                QMessageBox.information(self, "Thành công", f"Dữ liệu đã được xuất ra {file_path}")
            
        except Exception as e:
            logger.error(f"Export error: {str(e)}")
            QMessageBox.critical(self, "Lỗi", f"Xuất Excel thất bại: {str(e)}")
    
    # export_to_json removed — not user-friendly for office users


    def backup_database(self):
        """Create backup of the database"""
        try:
            backup_path, _ = QFileDialog.getSaveFileName(
                self, 
                "Backup Database", 
                str(BACKUP_DIR / f"documents_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"),
                "Database Files (*.db)"
            )
            
            if backup_path:
                if not backup_path.endswith('.db'):
                    backup_path += '.db'
                self.db.backup_database(backup_path)
                
                QMessageBox.information(
                    self, 
                    "Backup Complete", 
                    f"Database backed up to:\n{backup_path}"
                )
                
        except Exception as e:
            logger.error(f"Database backup error: {str(e)}")
            QMessageBox.critical(self, "Backup Error", f"Database backup failed: {str(e)}")

    def toggle_preview(self):
        """Toggle PDF preview for currently selected document"""
        if not self.current_doc_id:
            QMessageBox.information(self, "Information", "Please select a document first")
            return
            
        # Get document info
        doc_info = self.db.get_document_info(self.current_doc_id)
        if not doc_info:
            QMessageBox.warning(self, "Warning", "Document information not found")
            return
            
        # Check if file exists
        pdf_path = doc_info[1]
        if not os.path.exists(pdf_path):
            QMessageBox.warning(self, "File not found", f"PDF file not found at: {pdf_path}")
            return
            
        # Show preview dialog
        preview = PreviewDialog(pdf_path, self)
        preview.exec_()

    def page_changed(self, page_num):
        """Handle PDF page change"""
        if self.current_doc_id:
            self.load_page_detections(self.current_doc_id, page_num)

    def show_analytics_dashboard(self, initial_tab=None):
        """Mở Analytics Dashboard"""
        try:
            from analytics_dialog import AnalyticsDialog
            dialog = AnalyticsDialog(db=self.db, parent=self, initial_tab=initial_tab)
            dialog.exec_()
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "Lỗi", f"Không thể mở Analytics Dashboard: {str(e)}")

    def show_about(self):
        """About the app: the home page's about sheet as a dialog."""
        dialog = AboutDialog(self)
        dialog.exec_()
        dialog.deleteLater()  # a child of the window; do not keep one per opening

    def show_help(self):
        """Show help documentation"""
        help_file = BASE_DIR / "help.html"
        if os.path.exists(help_file):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(help_file)))
        else:
            QMessageBox.information(
                self,
                "Help",
                "Hướng dẫn sử dụng:\n\n"
                "1. Thêm tài liệu: Sử dụng nút 'Thêm File' hoặc menu File > Thêm File\n"
                "2. Xử lý OCR: Hệ thống sẽ tự động trích xuất văn bản\n"
                "3. Chỉnh sửa kết quả: Sử dụng panel bên phải để chỉnh sửa\n"
                "4. Tìm kiếm (Ctrl+F): Nhập các từ khóa vào ô tìm kiếm\n"
                "5. Xuất dữ liệu: Sử dụng chức năng Xuất Excel\n"
            )

    def refresh_view(self):
        """Refresh current view and reload data"""
        self.load_documents()
        
        # Get currently selected document
        if self.current_doc_id:
            self.show_document(self.current_doc_id)
        else:
            self.clear_current_view()
            
        self.statusBar().showMessage("Data refreshed", 3000)

    def clear_selection(self):
        """Clear current selection"""
        self.doc_list.clearSelection() if hasattr(self.doc_list, 'clearSelection') else None
        self.current_doc_id = None
        self.clear_current_view()

    def clear_current_view(self):
        """Clear all views and editors"""
        self._document_open = False
        self._details_user_choice = None
        self.pdf_viewer.clear()
        self.ocr_editor.clear()

        # Clear metadata
        for field in self.metadata_fields.values():
            field.clear()

        self.detections_by_page = {}
        self.doc_info_label.setText("")
        self.doc_info_label.setToolTip("")
        self._update_toolbar_context("", 0)
        QTimer.singleShot(0, self._apply_responsive_layout)

    def auto_save(self):
        """Auto-save current document if changes detected"""
        if self.current_doc_id and self.ocr_editor.save_btn.isEnabled():
            self.ocr_editor.save_changes()
            self.statusBar().showMessage("Auto-saved", 2000)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_main_splitter"):
            QTimer.singleShot(0, self._apply_responsive_layout)

    def closeEvent(self, event):
        """Handle application close"""
        # Save window settings
        self.save_window_settings()
        
        # Check for unsaved changes
        if self.current_doc_id and self.ocr_editor.save_btn.isEnabled():
            reply = QMessageBox.question(
                self, 'Unsaved Changes',
                'You have unsaved changes. Do you want to save before exiting?',
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save
            )
            
            if reply == QMessageBox.Save:
                self.ocr_editor.save_changes()
                event.accept()
            elif reply == QMessageBox.Discard:
                event.accept()
            else:
                event.ignore()
                return
        
        self.autosave_timer.stop()
        if getattr(self, '_status_coord', None) is not None:
            self._status_coord.stop()
        if not self.chat_panel.shutdown():
            QMessageBox.warning(
                self, "Đang dừng trợ lý",
                "Trợ lý AI chưa dừng an toàn. Vui lòng chờ và đóng lại ứng dụng."
            )
            event.ignore()
            return

        workers = [
            self.ocr_worker, self.batch_worker, self.repair_worker,
            getattr(self, '_gpu_keepalive', None),
            getattr(getattr(self, 'pdf_viewer', None), '_pdf_worker', None),
            getattr(self, '_preloader', None),
            *getattr(self, '_embed_workers', []),
            *getattr(self, '_relation_workers', []),
            getattr(getattr(self, '_reindex_dialog', None), 'worker', None),
        ]
        ocr_client = getattr(getattr(self, 'ocr_system', None), 'fullpage_ocr', None)
        server = getattr(getattr(self, 'ocr_system', None), '_chandra_server', None)
        failures = shutdown_runtime(
            workers=workers,
            clients=[ocr_client],
            models=[self.embedding_model, self.reranker],
            servers=[server],
            database=self.db,
        )
        blocking = [failure for failure in failures if failure.startswith('worker:')]
        if blocking:
            logger.error("Shutdown postponed: %s", "; ".join(blocking))
            QMessageBox.warning(
                self, "Đang dừng tác vụ",
                "Một tác vụ nền chưa dừng an toàn. Vui lòng chờ và đóng lại ứng dụng."
            )
            event.ignore()
            return
        if failures:
            logger.warning("Shutdown cleanup warnings: %s", "; ".join(failures))
        event.accept()
def main():
    # Must set these attributes before creating QApplication
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    
    # Set application info
    app.setApplicationName("OCR Document Manager")
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("OCRApp")
    
    # Set application style
    app.setStyle(QStyleFactory.create('Fusion'))
    
    # Register custom fonts if available
    font_dir = BASE_DIR / 'fonts'
    if font_dir.exists():
        for font_file in font_dir.glob('*.ttf'):
            QFontDatabase.addApplicationFont(str(font_file))
    
    # Create and show main window
    window = MainWindow()
    window.show()
    
    # Start event loop
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
