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

    def setup_ui(self):
        from ui_icons import icon

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(5)
        
        def tool_button(icon_name, tooltip, callback):
            button = QPushButton()
            button.setIcon(icon(icon_name, size=17))
            button.setToolTip(tooltip)
            button.setFixedSize(30, 30)
            button.clicked.connect(callback)
            return button

        self.first_btn = tool_button("chevrons-left", "Trang đầu tiên", self.goto_first_page)
        self.prev_btn = tool_button("chevron-left", "Trang trước", self.previous_page)

        # Page label
        self.page_label = QLabel("Trang: 0/0")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.page_label.setMinimumWidth(100)
        self.page_label.setStyleSheet("font-weight:600;padding:2px 8px;")

        # Page jump controls
        self.page_edit = QLineEdit()
        self.page_edit.setMaximumWidth(50)
        self.page_edit.setPlaceholderText("#")
        self.page_edit.returnPressed.connect(self.jump_to_page)

        self.jump_btn = QPushButton("Đến")
        self.jump_btn.setProperty("variant", "primary")
        self.jump_btn.setMaximumWidth(64)
        self.jump_btn.clicked.connect(self.jump_to_page)

        # Next/Last buttons
        self.next_btn = tool_button("chevron-right", "Trang sau", self.next_page)
        self.last_btn = tool_button("chevrons-right", "Trang cuối cùng", self.goto_last_page)

        # Zoom controls
        self.zoom_out_btn = tool_button("zoom-out", "Thu nhỏ", self.zoom_out)
        self.zoom_in_btn = tool_button("zoom-in", "Phóng to", self.zoom_in)

        # Rotate controls
        self.rotate_left_btn = tool_button("rotate-ccw", "Xoay trái", self.rotate_left)
        self.rotate_right_btn = tool_button("rotate-cw", "Xoay phải", self.rotate_right)

        # Zoom label
        self.zoom_label = QLabel("100%")
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label.setMinimumWidth(60)
        self.zoom_label.setStyleSheet("font-weight:600;padding:2px 8px;")
        # Add controls to toolbar
        toolbar.addWidget(self.first_btn)
        toolbar.addWidget(self.prev_btn)
        toolbar.addWidget(self.page_label)
        toolbar.addWidget(self.next_btn)
        toolbar.addWidget(self.last_btn)
        toolbar.addSpacing(10)
        toolbar.addWidget(self.page_edit)
        toolbar.addWidget(self.jump_btn)
        toolbar.addStretch()
        toolbar.addWidget(self.zoom_out_btn)
        toolbar.addWidget(self.zoom_label)
        toolbar.addWidget(self.zoom_in_btn)
        toolbar.addSpacing(10)
        toolbar.addWidget(self.rotate_left_btn)
        toolbar.addWidget(self.rotate_right_btn)
        
        # Image display in scrollable area
        self.scroll_area = QScrollArea()
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
        main_layout.addLayout(toolbar)
        main_layout.addWidget(self._content_stack)
        
        # Set initial state
        self.show_empty_state()
        self.update_controls()

    def _show_state(self, title: str, message: str, action: bool = False) -> None:
        self._state_title.setText(title)
        self._state_message.setText(message)
        self._state_action.setVisible(action)
        self._content_stack.setCurrentWidget(self._state_panel)

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
        
        # Update page display
        if has_doc:
            self.page_label.setText(f"Trang: {self.current_page + 1}/{len(self.pages)}")
        else:
            self.page_label.setText("Trang: 0/0")
            
        # Enable/disable controls
        self.first_btn.setEnabled(has_doc and self.current_page > 0)
        self.prev_btn.setEnabled(has_doc and self.current_page > 0)
        self.next_btn.setEnabled(has_doc and self.current_page < len(self.pages) - 1)
        self.last_btn.setEnabled(has_doc and self.current_page < len(self.pages) - 1)
        self.page_edit.setEnabled(has_doc)
        self.jump_btn.setEnabled(has_doc)
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
            self.page_edit.clear()
        except ValueError:
            # Invalid input, do nothing
            self.page_edit.clear()
            
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
class OCRResultEditor(QWidget):
    """Editor for OCR results with autosuggestions"""
    
    ocr_updated = pyqtSignal(int, dict)
    field_changed = pyqtSignal(str, str)  # field_name, new_value
    reocr_requested = pyqtSignal()  # user wants to re-run OCR on current document

    def __init__(self, db, parent=None):  # Thêm tham số db
        super().__init__(parent)
        self.setObjectName("ocrResultEditor")
        self.db = db  # Lưu database instance
        self.doc_id = None
        self.full_cqbh_text = ""
        self.current_doc_data = None 
        self.is_loading = False
        self._theme_name = AppThemeManager.instance().current
        self.setup_ui()

    def setup_ui(self):
        """Z-pattern Pro Editor layout (2026-04-18 v2 per user feedback).

          Header (fixed)     — filename + [Toggle metadata] button (F10)
          Main area          — Full Text editor, primary reading surface, 9pt
          Metadata dock      — Compact form fields, togglable. When hidden, Full Text
                               expands to fill all available vertical space.
          Action bar (46px)  — Lưu / Đặt lại / OCR lại / Sao chép / Xuất Word
                               always visible regardless of metadata state.
        """
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(4)
        main_layout.setContentsMargins(6, 6, 6, 6)

        # ── Header bar ───────────────────────────────────────────
        # Filename + toggle button. Matches "Z-pattern" top-bar from spec.
        self._header_bar = QFrame()
        self._header_bar.setObjectName("ocr_header_bar")
        self._header_bar.setFixedHeight(38)
        header_layout = QHBoxLayout(self._header_bar)
        header_layout.setContentsMargins(12, 4, 8, 4)
        header_layout.setSpacing(8)

        # Header font per user spec 2026-04-18: Be Vietnam Pro, Bold, 13pt, Navy.
        self._file_label = QLabel("— Chưa mở văn bản —")
        _header_font = QFont("Be Vietnam Pro", 11)
        _header_font.setBold(True)
        self._file_label.setFont(_header_font)
        self._file_label.setProperty("role", "documentTitle")
        self._file_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        header_layout.addWidget(self._file_label, 1)

        self._toggle_meta_btn = QToolButton()
        self._toggle_meta_btn.setText("Ẩn metadata")
        self._toggle_meta_btn.setIcon(icon("panel-right", size=16))
        self._toggle_meta_btn.setCheckable(True)
        self._toggle_meta_btn.setChecked(False)
        self._toggle_meta_btn.setToolTip("Ẩn/hiện các trường metadata trong trình biên tập")
        self._toggle_meta_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._toggle_meta_btn.toggled.connect(self._on_toggle_metadata)
        header_layout.addWidget(self._toggle_meta_btn)

        main_layout.addWidget(self._header_bar)

        # Metadata scroll area (built later — only contains NON-full-text fields).
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("ocrMetadataScroll")
        self.scroll_area.viewport().setObjectName("ocrMetadataViewport")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)

        # Form container for metadata (populated below; van_ban_group is
        # added directly to main_layout, NOT to this form_layout).
        form_widget = QWidget()
        form_widget.setObjectName("ocrMetadataForm")
        self.form_layout = QVBoxLayout(form_widget)
        self.form_layout.setSpacing(6)
        self.form_layout.setContentsMargins(4, 4, 4, 4)

        self.fields = {}

        # ── Compact metadata grid (2026-04-18 v3) ───────────────
        # Per user spec: all 12 fields in ONE QGridLayout at bottom.
        # Hard-locked 10pt font — does NOT inherit from theme cascade.
        # No QGroupBox nesting — each groupbox added 16px padding × 5
        # groups = 80px wasted vertical space.
        _LABEL_CSS = "font-size:8pt;font-weight:600;"
        _INPUT_CSS = "font-family:'Be Vietnam Pro';font-size:9pt;"
        _TEXT_CSS = "font-family:'Be Vietnam Pro';font-size:9pt;line-height:1.35;"

        def _mk_label(text: str) -> QLabel:
            lab = QLabel(text)
            lab.setStyleSheet(_LABEL_CSS)
            lab.setProperty("role", "metadataLabel")
            return lab

        # Create all field widgets first.
        self.fields['cqbh'] = QLineEdit()
        self.fields['cqbh'].setPlaceholderText("CQBH...")
        self.fields['cqbh'].setStyleSheet(_INPUT_CSS)
        completer = QCompleter([])
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.fields['cqbh'].setCompleter(completer)

        self.cqbh_selector = QComboBox()
        self.cqbh_selector.addItems(["Tất cả", "Trên", "Dưới"])
        self.cqbh_selector.setStyleSheet(_INPUT_CSS)
        self.cqbh_selector.currentTextChanged.connect(self.cqbh_selection_changed)
        self.cqbh_selector.setFixedWidth(90)

        self.fields['so_ki_hieu'] = QLineEdit()
        self.fields['so_ki_hieu'].setPlaceholderText("Số ký hiệu...")
        self.fields['so_ki_hieu'].setStyleSheet(_INPUT_CSS)
        completer = QCompleter([])
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.fields['so_ki_hieu'].setCompleter(completer)
        self.fields['so_ki_hieu'].textChanged.connect(
            lambda: self.field_changed.emit('so_ki_hieu', self.fields['so_ki_hieu'].text())
        )

        self.fields['ngay_bh'] = QLineEdit()
        self.fields['ngay_bh'].setPlaceholderText("dd/mm/yyyy")
        self.fields['ngay_bh'].setStyleSheet(_INPUT_CSS)

        calendar_btn = QPushButton(icon("calendar", size=16), "")
        self._calendar_btn = calendar_btn
        calendar_btn.setObjectName("metadataCalendarButton")
        calendar_btn.setFixedSize(24, 24)
        calendar_btn.setIconSize(QSize(14, 14))
        calendar_btn.setToolTip("Chọn ngày từ lịch")
        calendar_btn.clicked.connect(self.show_date_picker)

        ngay_bh_wrapper = QHBoxLayout()
        ngay_bh_wrapper.setContentsMargins(0, 0, 0, 0)
        ngay_bh_wrapper.setSpacing(3)
        ngay_bh_wrapper.addWidget(self.fields['ngay_bh'])
        ngay_bh_wrapper.addWidget(calendar_btn)

        self.fields['loai_vb'] = QLineEdit()
        self.fields['loai_vb'].setPlaceholderText("Công văn, Quyết định,...")
        self.fields['loai_vb'].setStyleSheet(_INPUT_CSS)
        completer = QCompleter([])
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.fields['loai_vb'].setCompleter(completer)

        self.fields['do_khan'] = QComboBox()
        self.fields['do_khan'].setStyleSheet(_INPUT_CSS)
        for text, rgb in [("Không", None), ("Hỏa Tốc", (255, 0, 0)),
                          ("Thượng Khẩn", (255, 140, 0)), ("Khẩn", (255, 215, 0))]:
            if rgb is None:
                self.fields['do_khan'].addItem(QIcon(), text)
            else:
                ic = QIcon(); px = QPixmap(12, 12); px.fill(QColor(*rgb))
                ic.addPixmap(px); self.fields['do_khan'].addItem(ic, text)
        self.fields['do_khan'].currentTextChanged.connect(
            lambda: self.field_changed.emit('do_khan', self.fields['do_khan'].currentText())
        )

        self.fields['do_mat'] = QComboBox()
        self.fields['do_mat'].setStyleSheet(_INPUT_CSS)
        for text, rgb in [("Không", None), ("Mật", (255, 182, 193)),
                          ("Tối Mật", (219, 112, 147)), ("Tuyệt Mật", (128, 0, 128))]:
            if rgb is None:
                self.fields['do_mat'].addItem(QIcon(), text)
            else:
                ic = QIcon(); px = QPixmap(12, 12); px.fill(QColor(*rgb))
                ic.addPixmap(px); self.fields['do_mat'].addItem(ic, text)
        self.fields['do_mat'].currentTextChanged.connect(
            lambda: self.field_changed.emit('do_mat', self.fields['do_mat'].currentText())
        )

        self.fields['chuc_vu'] = QLineEdit()
        self.fields['chuc_vu'].setPlaceholderText("Chức vụ người ký...")
        self.fields['chuc_vu'].setStyleSheet(_INPUT_CSS)
        completer = QCompleter([])
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.fields['chuc_vu'].setCompleter(completer)

        self.chuc_vu_selector = QComboBox()
        self.chuc_vu_selector.addItems(["Tất cả", "Chức danh"])
        self.chuc_vu_selector.setStyleSheet(_INPUT_CSS)
        self.chuc_vu_selector.currentTextChanged.connect(self.chuc_vu_selection_changed)
        self.chuc_vu_selector.setFixedWidth(90)

        self.fields['chu_ky'] = QLineEdit()
        self.fields['chu_ky'].setPlaceholderText("Tên người ký...")
        self.fields['chu_ky'].setStyleSheet(_INPUT_CSS)

        self.fields['nd_chinh'] = QTextEdit()
        self.fields['nd_chinh'].setPlaceholderText("Nội dung chính...")
        self.fields['nd_chinh'].setStyleSheet(_TEXT_CSS)
        self.fields['nd_chinh'].setMinimumHeight(34)
        self.fields['nd_chinh'].setMaximumHeight(68)

        self.fields['noi_nhan'] = QTextEdit()
        self.fields['noi_nhan'].setPlaceholderText("Danh sách nơi nhận...")
        self.fields['noi_nhan'].setStyleSheet(_TEXT_CSS)
        self.fields['noi_nhan'].setMinimumHeight(34)
        self.fields['noi_nhan'].setMaximumHeight(68)

        # ── Assemble GRID ───────────────────────────────────────
        # Label ABOVE input (2026-04-18 v4 per user feedback: labels
        # squeezing input width horizontally was the root cause of
        # "pixelated / quá to" text. Stacking label-on-top gives each
        # input full cell width, and lets the 10pt font render cleanly).
        form_grid = QGridLayout()
        form_grid.setContentsMargins(2, 2, 2, 2)
        form_grid.setHorizontalSpacing(8)
        form_grid.setVerticalSpacing(7)
        form_grid.setColumnStretch(0, 1)
        form_grid.setColumnStretch(1, 1)
        form_grid.setColumnStretch(2, 1)

        def _cell(label_text, widget):
            """Vertical stack: small label on top, input below."""
            box = QVBoxLayout()
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(3)
            lab = _mk_label(label_text)
            lab.setMinimumHeight(14)
            lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            box.addWidget(lab)
            if isinstance(widget, QHBoxLayout):
                box.addLayout(widget)
            else:
                box.addWidget(widget)
            return box

        r = 0
        # Row 1: Số hiệu | Ngày BH | Loại VB (each takes one col, full width)
        form_grid.addLayout(_cell("Số hiệu", self.fields['so_ki_hieu']), r, 0)
        form_grid.addLayout(_cell("Ngày BH", ngay_bh_wrapper), r, 1)
        form_grid.addLayout(_cell("Loại VB", self.fields['loai_vb']), r, 2)
        r += 1

        # Row 2: Độ khẩn | Độ mật | (Người ký moved here)
        form_grid.addLayout(_cell("Độ khẩn", self.fields['do_khan']), r, 0)
        form_grid.addLayout(_cell("Độ mật", self.fields['do_mat']), r, 1)
        form_grid.addLayout(_cell("Người ký", self.fields['chu_ky']), r, 2)
        r += 1

        # Row 3: CQBH (span all 3 cols) with selector
        cqbh_row = QHBoxLayout()
        cqbh_row.setContentsMargins(0, 0, 0, 0)
        cqbh_row.setSpacing(4)
        cqbh_row.addWidget(self.fields['cqbh'], 1)
        cqbh_row.addWidget(self.cqbh_selector)
        form_grid.addLayout(_cell("Cơ quan ban hành", cqbh_row), r, 0, 1, 3)
        r += 1

        # Row 4: Chức vụ (span all 3 cols) with selector
        chuc_vu_row = QHBoxLayout()
        chuc_vu_row.setContentsMargins(0, 0, 0, 0)
        chuc_vu_row.setSpacing(4)
        chuc_vu_row.addWidget(self.fields['chuc_vu'], 1)
        chuc_vu_row.addWidget(self.chuc_vu_selector)
        form_grid.addLayout(_cell("Chức vụ", chuc_vu_row), r, 0, 1, 3)
        r += 1

        # Row 5: ND chính (span all 3 cols)
        form_grid.addLayout(_cell("Nội dung chính", self.fields['nd_chinh']), r, 0, 1, 3)
        r += 1

        # Row 6: Nơi nhận (span all 3 cols)
        form_grid.addLayout(_cell("Nơi nhận", self.fields['noi_nhan']), r, 0, 1, 3)
        r += 1

        self.form_layout.addLayout(form_grid)
        
        # Van ban dang txt - Toàn bộ nội dung văn bản dạng text
        van_ban_group = QGroupBox("Nội dung văn bản dạng Text (OCR)")
        van_ban_group.setObjectName("ocrTextGroup")
        van_ban_layout = QVBoxLayout(van_ban_group)
        van_ban_layout.setContentsMargins(8, 16, 8, 8)
        van_ban_layout.setSpacing(4)

        # NOTE (2026-04-18): OCR lại / Sao chép / Xuất Word buttons moved
        # into the single fixed floating action bar at the bottom — see
        # "Fixed Floating Action Bar" section below. All 5 action buttons
        # (OCR lại, Sao chép, Xuất Word, Đặt lại, Lưu thay đổi) now live
        # together in one sticky toolbar per user request.

        self.fields['van_ban_dang_txt'] = QTextEdit()
        self.fields['van_ban_dang_txt'].setPlaceholderText(
            "Toàn bộ nội dung văn bản đã được OCR sẽ hiển thị ở đây..."
        )
        # Primary reading surface; it fills the panel when metadata is hidden.
        self.fields['van_ban_dang_txt'].setMinimumHeight(260)
        self.fields['van_ban_dang_txt'].setReadOnly(True)
        self.fields['van_ban_dang_txt'].setFont(QFont("Be Vietnam Pro", 9))
        # Same compact Vietnamese-optimized font as the extracted fields.
        self.fields['van_ban_dang_txt'].setProperty("role", "ocrText")
        # Cache raw HTML from Chandra for DOCX export
        self._van_ban_html_cache = ""

        van_ban_layout.addWidget(self.fields['van_ban_dang_txt'])
        # NOTE: van_ban_group is NOT added to form_layout — it goes directly
        # into main_layout as the top 70% section (see assembly below).

        # Finalize metadata form in scroll area.
        self.scroll_area.setWidget(form_widget)

        # Balance reading and editing: on a laptop the complete compact form
        # should be visible before the user needs to scroll. OCR still keeps
        # the larger share and expands to 100% when metadata is hidden.
        self.scroll_area.setMinimumHeight(230)
        self.scroll_area.setMaximumHeight(370)
        main_layout.addWidget(van_ban_group, 45)
        main_layout.addWidget(self.scroll_area, 55)

        # Field-change monitoring (dirty tracking for save button).
        for field_id, widget in self.fields.items():
            if field_id != 'cqbh':
                if isinstance(widget, QLineEdit):
                    widget.textChanged.connect(self.content_changed)
                elif isinstance(widget, QTextEdit):
                    widget.textChanged.connect(self.content_changed)
                elif isinstance(widget, QComboBox):
                    widget.currentTextChanged.connect(self.content_changed)

        # Live-refresh header bar when so_ki_hieu / loai_vb change.
        self.fields['so_ki_hieu'].textChanged.connect(self._refresh_header_live)
        self.fields['loai_vb'].textChanged.connect(self._refresh_header_live)

        # ── Fixed Floating Action Bar ──────────────────────────
        # Consolidated toolbar containing ALL 5 action buttons per user
        # feedback 2026-04-18: "Các nút 'Lưu', 'Xóa', 'OCR lại' nên nằm
        # gọn gàng trong một thanh công cụ (Mini-toolbar) ở dưới cùng,
        # cố định vị trí (Fixed position) để dù người dùng cuộn lên
        # xuống vẫn thấy nút Lưu."
        #
        # Layout (left → right):
        #   [dirty hint]  [OCR lại] [Sao chép] [Xuất Word]  ··· stretch ···  [Đặt lại] [Lưu]
        action_bar = QFrame()
        self._action_bar = action_bar
        action_bar.setObjectName("ocr_action_bar")
        action_bar.setFixedHeight(46)
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(8, 6, 8, 6)
        action_layout.setSpacing(5)

        # Dirty hint — amber text indicator, left side.
        self._dirty_hint = QLabel("")
        self._dirty_hint.setStyleSheet("color:#D97706;font-size:8pt;")
        action_layout.addWidget(self._dirty_hint)

        # ── Left cluster: document-level actions ─────────────────
        # OCR lại — accent coral (warning-style), re-OCR is destructive.
        self.reocr_btn = QPushButton("OCR lại")
        self.reocr_btn.setIcon(icon("refresh-cw", size=15))
        self.reocr_btn.setProperty("variant", "gold")
        self.reocr_btn.setToolTip("Nhận dạng lại văn bản này (khi kết quả bị lỗi hoặc thiếu)")
        self.reocr_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.reocr_btn.setFixedHeight(30)
        self.reocr_btn.clicked.connect(lambda: self.reocr_requested.emit())
        action_layout.addWidget(self.reocr_btn)

        # Sao chép — icon-only button, compact.
        _icon_btn_style = ""
        copy_btn = QPushButton()
        self._copy_btn = copy_btn
        copy_btn.setIcon(icon("copy", size=15))
        copy_btn.setIconSize(QSize(15, 15))
        copy_btn.setFixedSize(30, 30)
        copy_btn.setToolTip("Sao chép nội dung văn bản vào clipboard")
        copy_btn.setCursor(QCursor(Qt.PointingHandCursor))
        copy_btn.setStyleSheet(_icon_btn_style)
        copy_btn.clicked.connect(self._copy_ocr_text)
        action_layout.addWidget(copy_btn)

        # Xuất Word — icon-only button, compact.
        export_word_btn = QPushButton()
        self._export_word_btn = export_word_btn
        export_word_btn.setIcon(icon("file-text", size=15))
        export_word_btn.setIconSize(QSize(15, 15))
        export_word_btn.setFixedSize(30, 30)
        export_word_btn.setToolTip("Xuất nội dung văn bản ra file Word (.docx)")
        export_word_btn.setCursor(QCursor(Qt.PointingHandCursor))
        export_word_btn.setStyleSheet(_icon_btn_style)
        export_word_btn.clicked.connect(self._export_ocr_to_word)
        action_layout.addWidget(export_word_btn)

        action_layout.addStretch()

        # ── Right cluster: form-level actions ────────────────────
        # Đặt lại — ghost/secondary button.
        self.reset_btn = QPushButton("Đặt lại")
        self.reset_btn.setIcon(icon("undo-2", size=15))
        self.reset_btn.setProperty("variant", "ghost")
        self.reset_btn.setFixedHeight(30)
        self.reset_btn.clicked.connect(self.reset_fields)
        action_layout.addWidget(self.reset_btn)

        # Lưu — primary olive button, prominent.
        self.save_btn = QPushButton("Lưu thay đổi")
        self.save_btn.setIcon(icon("save", size=15))
        self.save_btn.setProperty("variant", "primary")
        self.save_btn.setFixedHeight(30)
        self.save_btn.setMinimumWidth(116)
        self.save_btn.clicked.connect(self.save_changes)
        self.save_btn.setEnabled(False)
        action_layout.addWidget(self.save_btn)

        main_layout.addWidget(action_bar)

        compact_font = QFont("Be Vietnam Pro", 9)
        for field_id, widget in self.fields.items():
            widget.setFont(compact_font)
            if isinstance(widget, QLineEdit):
                widget.setMinimumHeight(22)
                widget.setMaximumHeight(24)
            elif isinstance(widget, QComboBox):
                widget.setMinimumHeight(22)
                widget.setMaximumHeight(24)

        for key in ('nd_chinh', 'noi_nhan'):
            self.fields[key].textChanged.connect(self._fit_metadata_text_fields)
        QTimer.singleShot(0, self._fit_metadata_text_fields)

    def _fit_metadata_text_fields(self) -> None:
        """Grow multiline fields only as far as their rendered content needs."""
        for key in ('nd_chinh', 'noi_nhan'):
            editor = self.fields.get(key)
            if editor is None:
                continue
            width = max(160, editor.viewport().width())
            editor.document().setTextWidth(width)
            content_height = editor.document().documentLayout().documentSize().height()
            editor.setFixedHeight(max(34, min(68, int(content_height + 7))))

    def apply_theme(self, name: str) -> None:
        """Recolor existing QTextDocument content after a live theme swap."""
        self._theme_name = "dark" if name == "dark" else "light"
        text_color = QColor("#E8EBF5" if self._theme_name == "dark" else "#17231B")
        base_color = QColor("#1A2332" if self._theme_name == "dark" else "#FFFEFA")
        editor = self.fields['van_ban_dang_txt']

        palette = editor.palette()
        palette.setColor(QPalette.Text, text_color)
        palette.setColor(QPalette.Base, base_color)
        editor.setPalette(palette)

        document = editor.document()
        document.setDefaultStyleSheet(
            "body, div, p, span, table, td, th, li { color: %s; background: transparent; }"
            % text_color.name()
        )
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.Document)
        char_format = QTextCharFormat()
        char_format.setForeground(text_color)
        cursor.mergeCharFormat(char_format)
        editor.viewport().update()

        self.set_header_info(
            self.fields['loai_vb'].text(),
            self.fields['so_ki_hieu'].text(),
            getattr(self, '_header_tooltip_file', ''),
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_metadata_text_fields)
    
    def show_date_picker(self):
        """Show date picker for ngay_bh field"""
        date_dialog = QDialog(self)
        date_dialog.setWindowTitle("Chọn ngày")
        date_dialog.setMinimumSize(300, 300)
        
        layout = QVBoxLayout(date_dialog)
        
        calendar = QCalendarWidget()
        layout.addWidget(calendar)
        
        # Try to set initial date from current value
        current_date = self.fields['ngay_bh'].text().strip()
        if current_date:
            try:
                import re as _re
                # dd/MM/yyyy (primary format from OCR postprocess)
                for fmt in ['%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%d/%m/%y']:
                    try:
                        date = datetime.strptime(current_date, fmt)
                        calendar.setSelectedDate(QDate(date.year, date.month, date.day))
                        break
                    except:
                        continue
                else:
                    # "ngày DD tháng MM năm YYYY" (legacy format)
                    m = _re.search(r'(\d{1,2})\s*(?:/|tháng)\s*(\d{1,2})\s*(?:/|năm)\s*(\d{4})', current_date)
                    if m:
                        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                        calendar.setSelectedDate(QDate(y, mo, d))
            except:
                pass
        
        # Buttons
        buttons = QHBoxLayout()
        ok_btn = QPushButton("Chọn")
        ok_btn.clicked.connect(date_dialog.accept)
        cancel_btn = QPushButton("Hủy")
        cancel_btn.clicked.connect(date_dialog.reject)
        
        buttons.addStretch()
        buttons.addWidget(ok_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)
        
        if date_dialog.exec_() == QDialog.Accepted:
            selected_date = calendar.selectedDate()
            # Format date as dd/mm/yyyy
            formatted_date = f"{selected_date.day():02d}/{selected_date.month():02d}/{selected_date.year():04d}"
            self.fields['ngay_bh'].setText(formatted_date)
            self.content_changed()

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
            self.save_btn.setEnabled(True)

    def _refresh_header_live(self) -> None:
        """Update header bar from current field values as user types."""
        if getattr(self, 'is_loading', False):
            return
        try:
            self.set_header_info(
                self.fields['loai_vb'].text(),
                self.fields['so_ki_hieu'].text(),
                getattr(self, '_header_tooltip_file', ''),
            )
        except (KeyError, AttributeError):
            pass

    def _on_toggle_metadata(self, hide: bool) -> None:
        """Toggle metadata dock visibility. Full Text expands into freed space."""
        if hide:
            self.scroll_area.hide()
            self._toggle_meta_btn.setText("Hiện metadata")
        else:
            self.scroll_area.show()
            self._toggle_meta_btn.setText("Ẩn metadata")
            self.scroll_area.verticalScrollBar().setValue(0)

    def set_current_filename(self, filename: str) -> None:
        """Legacy filename-only setter — kept as tooltip source.

        Visible header content is set by set_header_info(loai_vb, so_ki_hieu).
        """
        self._header_tooltip_file = filename or ""
        self._file_label.setToolTip(filename or "")

    def set_header_info(self, loai_vb: str, so_ki_hieu: str,
                        filename: str = "") -> None:
        """Update header bar with document identity.

        Format (per user spec 2026-04-18):
            {Loại VB}   Số:  {SỐ HIỆU IN HOA size to}

        so_ki_hieu is emphasized: bold, uppercase, larger font, brand olive.
        Falls back to filename if both identity fields empty.
        """
        loai = (loai_vb or "").strip()
        so = (so_ki_hieu or "").strip()

        if not loai and not so:
            display = filename or "— Chưa mở văn bản —"
            self._file_label.setTextFormat(Qt.PlainText)
            self._file_label.setText(display)
            self._file_label.setToolTip(filename or "")
            return

        # Standardized header markup — ALL tokens use the same font family.
        # Previously each span inherited different font from cascade →
        # header looked different per document. Now every span explicitly
        # declares font-family + sizes from a fixed set (11pt/13pt).
        _ff = "font-family:'Be Vietnam Pro','Segoe UI',sans-serif"
        if self._theme_name == "dark":
            type_color, muted_color, number_color = "#7EA2E8", "#A8B2D1", "#E5C466"
        else:
            type_color, muted_color, number_color = "#3B5998", "#5F687C", "#0F2B5B"
        loai_html = (
            f"<span style=\"{_ff}; color:{type_color}; font-size:10pt;"
            f" font-weight:600;\">{self._esc(loai)}</span>"
            if loai else ""
        )
        separator = (
            f"<span style=\"{_ff}; color:{muted_color};\">&nbsp;·&nbsp;</span>"
            if loai and so else ""
        )
        if so:
            so_html = (
                f"<span style=\"{_ff}; color:{muted_color}; font-size:10pt;"
                f" font-weight:500;\">Số:&nbsp;</span>"
                f"<span style=\"{_ff}; color:{number_color}; font-size:12pt;"
                f" font-weight:700; letter-spacing:1px;\">"
                f"{self._esc(so.upper())}</span>"
            )
        else:
            so_html = ""

        self._file_label.setTextFormat(Qt.RichText)
        self._file_label.setText(loai_html + separator + so_html)
        self._file_label.setToolTip(filename or "")

    @staticmethod
    def _esc(text: str) -> str:
        """Escape HTML reserved chars for safe rich-text rendering."""
        return (
            text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
        )

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
        if raw_text:
            # Detect Chandra HTML output (contains <div> tags) — render as HTML
            if '<div' in raw_text and ('data-label' in raw_text or 'data-bbox' in raw_text or 'data=' in raw_text):
                self._van_ban_html_cache = raw_text
                self.fields['van_ban_dang_txt'].setHtml(
                    self._build_display_html(raw_text)
                )
            else:
                self._van_ban_html_cache = ""
                self.fields['van_ban_dang_txt'].setPlainText(raw_text)
        else:
            self._van_ban_html_cache = ""
            self.fields['van_ban_dang_txt'].clear()
        
        # Refresh Z-pattern header bar with loai_vb + so_ki_hieu.
        # Keeps the filename fallback available via tooltip.
        if hasattr(self, 'set_header_info'):
            self.set_header_info(
                doc_data[9] if len(doc_data) > 9 else "",
                doc_data[5] if len(doc_data) > 5 else "",
                getattr(self, '_header_tooltip_file', ''),
            )

        # Reset state
        self.is_loading = False
        self.save_btn.setEnabled(False)
        self.scroll_area.verticalScrollBar().setValue(0)
        QTimer.singleShot(0, self._finish_loading_layout)

    def _finish_loading_layout(self) -> None:
        self.scroll_area.verticalScrollBar().setValue(0)
        self._fit_metadata_text_fields()

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
                self.save_btn.setEnabled(False)
                return
                
            data = self.get_current_data()
            # Emit update signal
            self.ocr_updated.emit(self.doc_id, data)
            self.save_btn.setEnabled(False)
            
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
                if isinstance(editor, QLineEdit):
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

            for field_id, editor in self.fields.items():
                if isinstance(editor, QComboBox):
                    editor.setCurrentIndex(0)  # Reset to first item for all ComboBoxes
                elif isinstance(editor, QTextEdit):
                    editor.clear()
                else:
                    editor.setText("")  
            
            self.save_btn.setEnabled(False)
        finally:
            self.is_loading = False
            
    def highlight_keywords(self, keywords: list):
        """
        Highlight keywords in the van_ban_dang_txt field.
        
        Args:
            keywords: List of keywords to highlight
        """
        if 'van_ban_dang_txt' not in self.fields:
            return
            
        text_edit = self.fields['van_ban_dang_txt']
        if not isinstance(text_edit, QTextEdit):
            return
            
        # Get the current text
        text = text_edit.toPlainText()
        if not text or not keywords:
            return
        
        # Store cursor position
        cursor = text_edit.textCursor()
        
        # Clear existing formatting first
        text_edit.setPlainText(text)
        
        # Create highlight format
        from PyQt5.QtGui import QTextCharFormat
        highlight_format = QTextCharFormat()
        highlight_format.setBackground(QColor(255, 255, 0))  # Yellow highlight
        highlight_format.setForeground(QColor(0, 0, 0))  # Black text
        
        # Find and highlight each keyword
        for keyword in keywords:
            if not keyword:
                continue
                
            cursor = text_edit.textCursor()
            cursor.movePosition(cursor.Start)
            text_edit.setTextCursor(cursor)
            
            # Find all occurrences
            while True:
                cursor = text_edit.document().find(keyword, cursor)
                if cursor.isNull():
                    break
                cursor.mergeCharFormat(highlight_format)

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
    def _build_display_html(chandra_html: str) -> str:
        """Delegate to pure renderer in display_html_renderer module.

        Kept as staticmethod for backward-compat with call sites that use
        MainWindow._build_display_html(...). Actual rendering logic is in
        desktop/display_html_renderer.py for isolated unit-testability
        (no PyQt5/YOLO/torch init required in tests).
        """
        from display_html_renderer import build_display_html
        return build_display_html(chandra_html)

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
class SpinnerWidget(QWidget):
    """Widget vòng xoay gradient đỏ cam animated"""

    def __init__(self, size=60, parent=None):
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

    def stop(self):
        self._spinning = False
        self._completed = True
        self._timer.stop()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        s = self._size
        pen_width = 5

        if self._completed:
            # Vẽ checkmark xanh lá
            painter.setPen(QPen(QColor("#4CAF50"), pen_width, Qt.SolidLine, Qt.RoundCap))
            cx, cy = s // 2, s // 2
            r = s // 2 - pen_width
            painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)
            # Checkmark
            painter.setPen(QPen(QColor("#4CAF50"), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawLine(int(cx - r * 0.35), int(cy + r * 0.05),
                             int(cx - r * 0.05), int(cy + r * 0.35))
            painter.drawLine(int(cx - r * 0.05), int(cy + r * 0.35),
                             int(cx + r * 0.4), int(cy - r * 0.3))
        else:
            # Vẽ arc xoay với gradient đỏ cam
            rect = QRect(pen_width, pen_width, s - pen_width * 2, s - pen_width * 2)
            gradient = QConicalGradient(s / 2, s / 2, self._angle)
            gradient.setColorAt(0.0, QColor("#FF5722"))
            gradient.setColorAt(0.5, QColor("#FF9800"))
            gradient.setColorAt(1.0, QColor(255, 87, 34, 40))  # Fade out

            pen = QPen(QBrush(gradient), pen_width, Qt.SolidLine, Qt.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, self._angle * 16, 270 * 16)

            # Track xám nhạt phía sau
            painter.setPen(QPen(QColor(230, 230, 230), pen_width - 1, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(rect, (self._angle + 270) * 16, 90 * 16)

        painter.end()


class ProgressDialog(QDialog):
    """Dialog hiện đại hiển thị tiến trình xử lý với spinner animated"""
    canceled = pyqtSignal()

    def __init__(self, total: int, title: str = "Đang xử lý", parent=None):
        super().__init__(parent)
        self.total = max(1, total)
        self.was_canceled = False
        self.is_completed = False
        self.setup_ui(title)

    def setup_ui(self, title: str):
        self.setWindowTitle(title)
        self.setWindowModality(Qt.ApplicationModal)
        self.setFixedSize(420, 280)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        # Nền trắng bo góc với shadow
        self.setStyleSheet("""
            QDialog {
                background-color: #FFFEFA;
                border-radius: 12px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 20)
        layout.setSpacing(10)

        # Spinner widget (trung tâm)
        spinner_layout = QHBoxLayout()
        spinner_layout.addStretch()
        self.spinner = SpinnerWidget(60, self)
        spinner_layout.addWidget(self.spinner)
        spinner_layout.addStretch()
        layout.addLayout(spinner_layout)

        layout.addSpacing(5)

        # Task label
        self.task_label = QLabel(title)
        self.task_label.setAlignment(Qt.AlignCenter)
        self.task_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #333333;")
        layout.addWidget(self.task_label)

        # Message chờ
        self.time_label = QLabel("Tài liệu đang được xử lý, đồng chí vui lòng chờ giây lát.")
        self.time_label.setAlignment(Qt.AlignCenter)
        self.time_label.setWordWrap(True)
        self.time_label.setStyleSheet("font-size: 10pt; font-style: italic; color: #666666;")
        layout.addWidget(self.time_label)

        layout.addSpacing(5)

        # Progress bar mỏng gradient
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #E8E8E8;
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                border-radius: 3px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #FF5722, stop:1 #FF9800);
            }
        """)
        layout.addWidget(self.progress_bar)

        # Status label
        self.status_label = QLabel("Đang chuẩn bị...")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 9pt; color: #888888;")
        layout.addWidget(self.status_label)

        layout.addSpacing(5)

        # Button layout
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        # OK button (hidden initially)
        self.ok_btn = QPushButton("OK")
        self.ok_btn.clicked.connect(self.accept)
        self.ok_btn.setVisible(False)
        self.ok_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4CAF50, stop:1 #66BB6A);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 28px;
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #43A047, stop:1 #5CB860);
            }
        """)
        button_layout.addWidget(self.ok_btn)

        # Cancel button
        self.cancel_btn = QPushButton("Hủy")
        self.cancel_btn.setIcon(QIcon("icons/cancel.png"))
        self.cancel_btn.clicked.connect(self.cancel_operation)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #999999;
                border: 1px solid #DDDDDD;
                border-radius: 6px;
                padding: 6px 20px;
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: #FFF0F0;
                color: #FF5722;
                border-color: #FF5722;
            }
        """)
        button_layout.addWidget(self.cancel_btn)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        # Timer
        self.start_time = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer)

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
        self.spinner.stop()
        self.canceled.emit()
        self.reject()

    def _on_timer(self):
        """Cập nhật message chờ - không hiện countdown, chỉ thông báo đang xử lý"""
        if self.start_time is None or self.is_completed:
            return
        # Giữ message chờ tĩnh, thân thiện
        elapsed = int(time.time() - self.start_time)
        if elapsed < 10:
            self.time_label.setText("Tài liệu đang được xử lý, đồng chí vui lòng chờ giây lát.")
        elif elapsed < 30:
            self.time_label.setText("Đang phân tích nội dung văn bản, vui lòng chờ thêm...")
        elif elapsed < 60:
            self.time_label.setText("Văn bản phức tạp cần thêm thời gian, cảm ơn đồng chí đã kiên nhẫn.")
        else:
            mins = elapsed // 60
            self.time_label.setText(f"Đã xử lý được {mins} phút, hệ thống đang nỗ lực hoàn thành...")

    def update_progress(self, current: int, total: int = None, message: str = None):
        if self.is_completed:
            return
        if total is not None:
            self.total = max(1, total)
        target_pct = min(100.0, (current / self.total) * 100) if self.total > 0 else 0

        # Smooth animation: gradually fill toward target instead of jumping
        current_pct = self.progress_bar.value()
        if target_pct > current_pct:
            self._smooth_target = target_pct
            if not hasattr(self, '_smooth_timer') or self._smooth_timer is None:
                self._smooth_timer = QTimer(self)
                self._smooth_timer.timeout.connect(self._animate_progress)
                self._smooth_timer.start(50)  # 50ms ticks = 20fps smooth

        if message:
            self.status_label.setText(f"{message} ({target_pct:.0f}%)")
        else:
            self.status_label.setText(f"Đang xử lý... {target_pct:.0f}%")

    def _animate_progress(self):
        """Smoothly animate progress bar toward target."""
        if self.is_completed:
            if hasattr(self, '_smooth_timer') and self._smooth_timer:
                self._smooth_timer.stop()
            return
        current = self.progress_bar.value()
        target = getattr(self, '_smooth_target', current)
        if current < target:
            # Ease-out: move faster when far from target, slower when close
            step = max(1, int((target - current) * 0.15))
            self.progress_bar.setValue(min(current + step, int(target)))
        elif current >= target:
            # At target — keep ticking slowly to simulate "working" feel
            # Don't exceed the target though
            pass

    def mark_completed(self):
        """Đánh dấu hoàn thành với animation"""
        if self.is_completed:
            return
        self.is_completed = True
        self.timer.stop()
        self.progress_bar.setValue(100)
        self.spinner.stop()

        # Tính thời gian xử lý
        elapsed = time.time() - self.start_time if self.start_time else 0
        elapsed_str = f"{elapsed:.1f}"

        self.task_label.setText("Hoàn thành!")
        self.task_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #4CAF50;")
        self.status_label.setText(
            f"Văn bản đã được xử lý trong {elapsed_str} giây,\n"
            f"cảm ơn đồng chí đã kiên nhẫn chờ đợi."
        )
        self.status_label.setStyleSheet("font-size: 10pt; color: #333333;")
        self.time_label.setText("")
        self.cancel_btn.setVisible(False)
        self.ok_btn.setVisible(True)

        # Progress bar chuyển xanh
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #E8E8E8;
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                border-radius: 3px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4CAF50, stop:1 #66BB6A);
            }
        """)

    def closeEvent(self, event):
        """Handle dialog close"""
        self.timer.stop()
        self.spinner._timer.stop()
        super().closeEvent(event)

class PreviewDialog(QDialog):
    """Dialog for previewing PDF before processing"""
    
    def __init__(self, pdf_path: str, parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.setup_ui()
        
    def setup_ui(self):
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
        layout.setContentsMargins(0, 0, 0, 10)
        
        # File info
        info_layout = QHBoxLayout()
        
        file_info = os.path.basename(self.pdf_path)
        if os.path.exists(self.pdf_path):
            size_mb = os.path.getsize(self.pdf_path) / (1024 * 1024)
            file_info += f" ({size_mb:.1f} MB)"
            
        info_label = QLabel(f"File: {file_info}")
        info_label.setStyleSheet("font-weight: bold; padding: 5px;")
        info_layout.addWidget(info_label)
        info_layout.addStretch()
        
        # PDF viewer
        self.viewer = PDFViewer()
        if not self.viewer.load_pdf(self.pdf_path):
            # Error loading PDF - show message
            error_widget = QWidget()
            error_layout = QVBoxLayout(error_widget)
            error_icon = QLabel()
            error_icon.setPixmap(icon("circle-alert", size=48, color="#B3263E").pixmap(64, 64))
            error_icon.setAlignment(Qt.AlignCenter)
            error_message = QLabel(f"Lỗi khi tải tệp:\n{self.pdf_path}")
            error_message.setAlignment(Qt.AlignCenter)
            error_message.setStyleSheet("color: red; font-weight: bold;")
            error_layout.addStretch()
            error_layout.addWidget(error_icon)
            error_layout.addWidget(error_message)
            error_layout.addStretch()
            layout.addWidget(error_widget)
        else:
            layout.addLayout(info_layout)
            layout.addWidget(self.viewer)

        # Buttons
        buttons = QHBoxLayout()
        self.process_btn = QPushButton("Bắt đầu phân tích")
        self.process_btn.setIcon(QIcon("icons/process.png"))
        self.process_btn.clicked.connect(self.accept)
        self.process_btn.setEnabled(os.path.exists(self.pdf_path))
        
        cancel_btn = QPushButton("Hủy")
        cancel_btn.setIcon(QIcon("icons/cancel.png"))
        cancel_btn.clicked.connect(self.reject)
        
        buttons.addStretch()
        buttons.addWidget(self.process_btn)
        buttons.addWidget(cancel_btn)
        
        layout.addLayout(buttons)

#############################
#  Model Selection Dialog   #
#############################
class SearchDialog(QDialog):
    """Dialog tìm kiếm từ khóa nâng cao với bảng kết quả"""
    document_selected = pyqtSignal(int, list)  # doc_id, keywords

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Tìm kiếm văn bản")
        self.setWindowIcon(QIcon("icons/find.png"))
        self.resize(800, 500)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Hướng dẫn
        hint = QLabel("Nhập từ khóa cần tìm, cách nhau bởi dấu phẩy (,)")
        hint.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(hint)

        # Input row
        input_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Ví dụ: quyết định, bổ nhiệm, nhân sự")
        self.search_input.setMinimumHeight(32)
        self.search_input.returnPressed.connect(self.do_search)

        self.search_btn = QPushButton("Tìm kiếm")
        self.search_btn.setIcon(QIcon("icons/find.png"))
        self.search_btn.setMinimumHeight(32)
        self.search_btn.clicked.connect(self.do_search)

        input_layout.addWidget(self.search_input)
        input_layout.addWidget(self.search_btn)
        layout.addLayout(input_layout)

        # Bảng kết quả
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(["ID", "Tên văn bản", "Tần suất", "Xem PDF"])
        self.result_table.horizontalHeader().setFixedHeight(28)
        self.result_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.setSortingEnabled(True)

        # Cỡ cột
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, header.Fixed)
        header.setSectionResizeMode(1, header.Stretch)
        header.setSectionResizeMode(2, header.Fixed)
        header.setSectionResizeMode(3, header.Fixed)
        self.result_table.setColumnWidth(0, 50)
        self.result_table.setColumnWidth(2, 80)
        self.result_table.setColumnWidth(3, 90)

        layout.addWidget(self.result_table)

        # Status bar
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #333;")
        layout.addWidget(self.status_label)

        # Focus vào input
        self.search_input.setFocus()

    def do_search(self):
        """Thực hiện tìm kiếm"""
        query_text = self.search_input.text().strip()
        if not query_text:
            self.status_label.setText("Vui lòng nhập từ khóa.")
            return

        # Đổi separator từ , sang ; cho tương thích với db.search_documents
        keywords = [kw.strip() for kw in query_text.split(',') if kw.strip()]
        db_query = ';'.join(keywords)

        try:
            results = self.db.search_documents(db_query, "all")

            self.result_table.setSortingEnabled(False)
            self.result_table.setRowCount(len(results))

            for row, (doc, stats) in enumerate(results):
                # ID
                id_item = QTableWidgetItem(str(doc[0]))
                id_item.setData(Qt.UserRole, doc[0])
                id_item.setTextAlignment(Qt.AlignCenter)
                self.result_table.setItem(row, 0, id_item)

                # Tên văn bản
                name_item = QTableWidgetItem(doc[2])
                # Tooltip with snippets
                tooltip_parts = [f"Đường dẫn: {doc[1]}"]
                if stats['snippets']:
                    tooltip_parts.append("\n--- Đoạn văn bản tìm thấy ---")
                    for snippet in stats['snippets'][:5]:
                        tooltip_parts.append(f"• Dòng {snippet['line']}: {snippet['context']}")
                name_item.setToolTip('\n'.join(tooltip_parts))
                self.result_table.setItem(row, 1, name_item)

                # Tần suất
                freq = stats['total_frequency']
                freq_item = QTableWidgetItem()
                freq_item.setData(Qt.DisplayRole, freq)
                freq_item.setTextAlignment(Qt.AlignCenter)
                # Color code
                if freq > 10:
                    freq_item.setBackground(QColor(144, 238, 144))
                elif freq > 5:
                    freq_item.setBackground(QColor(255, 255, 200))
                elif freq > 0:
                    freq_item.setBackground(QColor(230, 230, 250))
                # Tooltip breakdown
                if stats['keyword_frequencies']:
                    kw_breakdown = [f"{kw}: {count}" for kw, count in stats['keyword_frequencies'].items()]
                    freq_item.setToolTip("Số lần xuất hiện:\n" + '\n'.join(kw_breakdown))
                self.result_table.setItem(row, 2, freq_item)

                # Nút Xem PDF
                preview_btn = QPushButton("Xem PDF")
                preview_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #2196F3;
                        color: white;
                        border: none;
                        border-radius: 3px;
                        padding: 4px 8px;
                    }
                    QPushButton:hover {
                        background-color: #1976D2;
                    }
                """)
                doc_id = doc[0]
                preview_btn.clicked.connect(lambda checked, did=doc_id: self.preview_document(did))
                self.result_table.setCellWidget(row, 3, preview_btn)

            self.result_table.setSortingEnabled(True)
            # Sắp xếp theo tần suất giảm dần
            self.result_table.sortItems(2, Qt.DescendingOrder)

            self.status_label.setText(
                f"Tìm thấy {len(results)} tài liệu với từ khóa: {', '.join(keywords)}"
            )

        except Exception as e:
            logger.error(f"Search error: {str(e)}")
            self.status_label.setText(f"Lỗi tìm kiếm: {str(e)}")

    def preview_document(self, doc_id):
        """Mở document và đóng dialog, truyền kèm keywords"""
        keywords = [kw.strip() for kw in self.search_input.text().split(',') if kw.strip()]
        self.document_selected.emit(doc_id, keywords)
        self.accept()


class MainWindow(QMainWindow):
    """Main application window"""
    home_requested = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hệ thống OCR Văn Bản")

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
        self.chat_panel.visibilityChanged.connect(self.act_chat.setChecked)
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
        progress = ProgressDialog(len(missing_files), "Repairing File Paths", self)
        
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
        main_layout.setContentsMargins(2, 0, 2, 2)  # Minimal margins
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
        splitter.setHandleWidth(6)  # Easier to grab than 1px
        # Allow dragging + collapsing left/right panels (keep center pinned).
        # 2026-04-18 fix: setChildrenCollapsible(False) + thin 1px handle +
        # fixed-width children prevented users from dragging at all. Now
        # any panel can shrink to 0 by dragging handle to the edge.
        splitter.setChildrenCollapsible(True)
        splitter.setOpaqueResize(False)
        self._main_splitter = splitter  # keep ref for later config

        # LEFT PANEL - Document List and Versions
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(1)

        # Document Tabs
        self.doc_tabs = QTabWidget()
        self.doc_tabs.setDocumentMode(True)
        self.doc_tabs.setStyleSheet("""
            QTabBar::tab {
                height: 24px;
                padding: 2px 8px;
            }
        """)

        # Document List — minimalist redesign 2026-04-18.
        # Replaced 8-column QTableWidget with clean 2-field list (STT + filename).
        # Rich metadata (số hiệu, ND chính, pages, urgency, size) shows on hover
        # popover. Sorting/filtering moved to search dialog + status dot.
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

        # ── Unified toolbar above the list ──────────────────────
        # Single-row toolbar (36px) that aligns with the overall header
        # density: filter input on the left, sort dropdown on the right.
        # Below it a tiny legend strip (16px) explains status icons.
        self._doc_list_panel = QWidget()
        _dlp_layout = QVBoxLayout(self._doc_list_panel)
        _dlp_layout.setContentsMargins(8, 8, 8, 8)
        _dlp_layout.setSpacing(6)

        library_header = QHBoxLayout()
        library_title = QLabel("Kho văn bản")
        library_title.setProperty("role", "sectionTitle")
        library_header.addWidget(library_title)
        library_header.addStretch()
        self._library_count_label = QLabel("0 văn bản")
        self._library_count_label.setProperty("role", "muted")
        library_header.addWidget(self._library_count_label)
        _dlp_layout.addLayout(library_header)

        toolbar = QFrame()
        toolbar.setObjectName("doc_toolbar")
        toolbar.setFixedHeight(36)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(6, 3, 6, 3)
        tb_layout.setSpacing(6)

        # Inline filter — live-filters the list by filename/số hiệu.
        self._doc_filter_input = QLineEdit()
        self._doc_filter_input.setPlaceholderText("Tìm trong kho…")
        self._doc_filter_input.setClearButtonEnabled(True)
        self._doc_filter_input.setFixedHeight(28)
        self._doc_filter_input.textChanged.connect(self._on_doc_filter_changed)
        tb_layout.addWidget(self._doc_filter_input, 1)

        self._doc_sort_combo = QComboBox()
        self._doc_sort_combo.addItem("Mới nhất", "date_desc")
        self._doc_sort_combo.addItem("Cũ nhất", "date_asc")
        self._doc_sort_combo.addItem("Tên A→Z", "name_asc")
        self._doc_sort_combo.addItem("Tên Z→A", "name_desc")
        self._doc_sort_combo.addItem("ID ↓", "id_desc")
        self._doc_sort_combo.addItem("ID ↑", "id_asc")
        self._doc_sort_combo.setFixedHeight(28)
        self._doc_sort_combo.setFixedWidth(92)
        self._doc_sort_combo.currentIndexChanged.connect(self._on_doc_sort_changed)
        tb_layout.addWidget(self._doc_sort_combo)

        # Tiny legend under toolbar — explains status glyphs without
        # consuming primary toolbar space.
        legend = QLabel("● đã OCR    ◐ chưa đủ    ○ chờ")
        legend.setProperty("role", "muted")
        legend.setAlignment(Qt.AlignLeft)

        _dlp_layout.addWidget(toolbar)
        _dlp_layout.addWidget(legend)
        _dlp_layout.addWidget(self.doc_list, 1)

        # Add panel to tabs. DocumentListWidget stays accessible via self.doc_list.
        self.doc_tabs.addTab(self._doc_list_panel, "Danh sách")
        self.doc_tabs.tabBar().hide()  # single-tab layout

        # Version history panel removed 2026-04-18 — single-version pipeline
        # (re-OCR overwrites in place, no multi-version workflow).
        left_layout.addWidget(self.doc_tabs, 1)

        splitter.addWidget(left_panel)

    # CENTER PANEL - PDF Viewer and Metadata
        center_panel = QTabWidget()
        center_panel.setDocumentMode(True)
        center_panel.setStyleSheet("""
            QTabBar::tab {
                height: 24px;
                padding: 2px 8px;
            }
        """)

        # PDF Viewer Tab
        self.pdf_viewer = PDFViewer()
        self.pdf_viewer.addRequested.connect(self.add_file)
        self.pdf_viewer.pageChanged.connect(self.page_changed)
        center_panel.addTab(self.pdf_viewer, "Xem văn bản")

        # Metadata Tab
        metadata_scroll = QScrollArea()
        metadata_scroll.setWidgetResizable(True)
        metadata_scroll.setFrameShape(QFrame.NoFrame)
        
        metadata_content = QWidget()
        metadata_layout = QVBoxLayout(metadata_content)
        metadata_layout.setSpacing(8)
        metadata_layout.setContentsMargins(8, 8, 8, 8)

        # Create metadata form
        metadata_form = QWidget()
        form_layout = QFormLayout(metadata_form)
        form_layout.setVerticalSpacing(6)
        form_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Initialize metadata fields
        self.metadata_fields = {}
        metadata_field_labels = [
            ('id', 'ID'),
            ('filename', 'Tên file'),
            ('created', 'Ngày tạo'),
            ('modified', 'Sửa đổi'),
            ('pages', 'Số trang'),
            ('filesize', 'Kích thước'),
            ('versions', 'Phiên bản'),
            ('cqbh', 'Cơ quan BH'),
            ('so_kh', 'Số ký hiệu'),
            ('loai_vb', 'Loại văn bản'),
            ('do_khan', 'Độ khẩn'),
            ('do_mat', 'Độ mật')
        ]

        for field_id, label in metadata_field_labels:
            # Create label
            field_label = QLabel(f"<b>{label}:</b>")
            
            # Create value field
            field = QLabel()
            field.setFrameShape(QFrame.StyledPanel)
            field.setFrameShadow(QFrame.Sunken)
            field.setMinimumHeight(22)
            field.setTextInteractionFlags(Qt.TextSelectableByMouse)
            field.setProperty("role", "metadataValue")
            
            self.metadata_fields[field_id] = field
            form_layout.addRow(field_label, field)

        metadata_layout.addWidget(metadata_form)
        metadata_layout.addStretch()
        metadata_scroll.setWidget(metadata_content)
        center_panel.addTab(metadata_scroll, "Thông tin tệp")

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


    def _build_main_toolbar(self) -> None:
        """Unified top toolbar (Phase 12, 2026-04-19 redesign).

        Single source of navigation — replaces both the legacy QMenuBar and
        the previous 12-icon toolbar. Uses split buttons to consolidate
        semantically-paired actions (Thêm 1 file + Thêm nhiều → one dropdown)
        and an overflow (⋯) menu for secondary actions.

        Layout: Home/panels · Add · edit · search/chat · document context ·
        analytics/export · theme/overflow.

        Split buttons:
          • "Thêm ▾" — main click = Ctrl+O, dropdown = batch / drag tip
          • "Tìm ▾"  — main click = Ctrl+F, dropdown = AI semantic search

        Overflow menu:
          Xem trước PDF (Ctrl+P) · Refresh (F5) · Sao lưu DB · Kiểm tra
          file · Cài đặt (Ctrl+,) · Hướng dẫn (F1) · Về ứng dụng
        """
        from ui_icons import icon, make_tool_action, preload_icons

        # Pre-warm cache for all toolbar icons — ~6ms one-time.
        preload_icons([
            "house", "panel-left", "file-plus", "folder-open", "save", "undo-2", "trash-2",
            "search", "bar-chart-3", "archive", "moon", "more-horizontal",
            "chevron-down", "command",
        ], size=20)

        toolbar = self.addToolBar("Main")
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        toolbar.setContextMenuPolicy(Qt.PreventContextMenu)

        # ── Group 1: Home + view toggle ───────────────────
        self.act_home = make_tool_action(
            self, "house", "Bàn bắt đầu việc ({})".format(command("home").shortcut),
            callback=lambda _checked=False: self.home_requested.emit(),
            shortcut=command("home").shortcut,
        )
        toolbar.addAction(self.act_home)

        self.act_toggle_left_panel = make_tool_action(
            self, "panel-left",
            "Ẩn/hiện kho văn bản ({})".format(command("toggle_library").shortcut),
            callback=self._toggle_left_panel,
            shortcut=command("toggle_library").shortcut,
            checkable=True,
        )
        self.act_toggle_left_panel.setChecked(True)
        toolbar.addAction(self.act_toggle_left_panel)
        self.act_toggle_details = make_tool_action(
            self, "panel-right",
            "Ẩn/hiện vùng chi tiết ({})".format(command("toggle_metadata").shortcut),
            callback=self._toggle_details_panel,
            shortcut=command("toggle_metadata").shortcut,
            checkable=True,
        )
        self.act_toggle_details.setChecked(True)
        toolbar.addAction(self.act_toggle_details)
        toolbar.addSeparator()

        # ── Group 2: File (split button "Thêm ▾") ─────────
        self.act_add_file = make_tool_action(
            self, "file-plus",
            "Thêm văn bản ({})\nNhấn ▾ để mở tùy chọn nâng cao".format(command("add_file").shortcut),
            callback=self.add_file,
            shortcut=command("add_file").shortcut,
        )
        self.act_add_file.setText("Thêm")
        add_menu = QMenu(self)
        act_add_batch = add_menu.addAction(
            icon("files", size=16), "Thêm nhiều tệp...\t{}".format(command("add_files").shortcut)
        )
        act_add_batch.triggered.connect(self.add_files)
        act_add_batch.setShortcut(QKeySequence(command("add_files").shortcut))
        act_add_folder = add_menu.addAction(
            icon("folder-open", size=16), "Nhập thư mục văn bản...\t{}".format(command("add_folder").shortcut)
        )
        act_add_folder.triggered.connect(self.add_folder)
        act_add_folder.setShortcut(QKeySequence(command("add_folder").shortcut))

        add_btn = QToolButton(toolbar)
        add_btn.setDefaultAction(self.act_add_file)
        add_btn.setMenu(add_menu)
        add_btn.setPopupMode(QToolButton.MenuButtonPopup)
        add_btn.setProperty("popupMode", "1")
        add_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toolbar.addWidget(add_btn)
        self._act_add_batch = act_add_batch  # keep ref for shortcut registration
        self._act_add_folder = act_add_folder
        toolbar.addSeparator()

        # ── Group 3: Edit (single buttons) ────────────────
        self.act_save = make_tool_action(
            self, "save",
            "Lưu thay đổi ({})".format(command("save").shortcut),
            callback=self._toolbar_save_current,
            shortcut=command("save").shortcut,
        )
        toolbar.addAction(self.act_save)

        self.act_reset = make_tool_action(
            self, "undo-2",
            "Đặt lại biểu mẫu ({})".format(command("reset").shortcut),
            callback=self._toolbar_reset_current,
            shortcut=command("reset").shortcut,
        )
        toolbar.addAction(self.act_reset)

        self.act_delete = make_tool_action(
            self, "trash-2",
            "Xóa tài liệu ({})".format(command("delete").shortcut),
            callback=self._delete_current_document_safe,
            shortcut=command("delete").shortcut,
            icon_color="#B91C1C",
        )
        toolbar.addAction(self.act_delete)
        toolbar.addSeparator()

        # ── Group 4: Search (split button "Tìm ▾") ────────
        self.act_search = make_tool_action(
            self, "search",
            "Tìm kiếm văn bản ({})\nNhấn ▾ để dùng AI ngữ nghĩa".format(command("search").shortcut),
            callback=self.show_search_dialog,
            shortcut=command("search").shortcut,
        )
        self.act_search.setText("Tìm")
        search_menu = QMenu(self)
        act_ai_search = search_menu.addAction(
            icon("sparkles", size=16, color="#846314"),
            "Tìm AI ngữ nghĩa...\t{}".format(command("ai_search").shortcut),
        )
        act_ai_search.triggered.connect(self.show_ai_search_dialog)
        act_ai_search.setShortcut(QKeySequence(command("ai_search").shortcut))

        search_btn = QToolButton(toolbar)
        search_btn.setDefaultAction(self.act_search)
        search_btn.setMenu(search_menu)
        search_btn.setPopupMode(QToolButton.MenuButtonPopup)
        search_btn.setProperty("popupMode", "1")
        search_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toolbar.addWidget(search_btn)
        self._act_ai_search = act_ai_search

        self.act_chat = make_tool_action(
            self, "panel-right", "Trợ lý pháp luật ({})".format(command("chat").shortcut),
            callback=self._toggle_chat_panel, checkable=True,
            shortcut=command("chat").shortcut,
            icon_color="#846314",
        )
        self.act_chat.setText("Trợ lý")
        toolbar.addAction(self.act_chat)
        toolbar.widgetForAction(self.act_chat).setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toolbar.addSeparator()

        # ── Context label (filename · pages) ──────────────
        # Shows which document is currently open in the center/right panels.
        # Replaces the hidden doc_title label purged in FE-BE audit.
        self.toolbar_context_label = QLabel("Chưa chọn tài liệu")
        self.toolbar_context_label.setObjectName("toolbar_context")
        self.toolbar_context_label.setProperty("empty", "true")
        self.toolbar_context_label.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Preferred
        )
        self.toolbar_context_label.setAlignment(Qt.AlignCenter)
        self.toolbar_context_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse
        )
        toolbar.addWidget(self.toolbar_context_label)

        # ── Group 5: Analytics + Export ───────────────────
        self.act_analytics = make_tool_action(
            self, "bar-chart-3",
            "Phân tích thống kê (Ctrl+Shift+A)",
            callback=self.show_analytics_dashboard,
            shortcut=command("analytics").shortcut,
        )
        toolbar.addAction(self.act_analytics)

        self.act_export_excel = make_tool_action(
            self, "archive",
            "Xuất dữ liệu ra Excel (Ctrl+E)",
            callback=self.export_to_excel,
            shortcut=command("export_excel").shortcut,
            icon_color="#1D6F42",
        )
        toolbar.addAction(self.act_export_excel)
        toolbar.addSeparator()

        # ── Group 6: Dark mode + Overflow (⋯) ─────────────
        self.act_dark_mode = make_tool_action(
            self, "moon",
            "Chế độ tối (Ctrl+D)",
            callback=self._on_dark_mode_toggled,
            shortcut=command("toggle_theme").shortcut,
            checkable=True,
        )
        self.act_dark_mode.setChecked(self._theme_mgr_v2.current == "dark")
        toolbar.addAction(self.act_dark_mode)

        # Overflow menu for secondary actions — keeps the toolbar clean.
        overflow_menu = QMenu(self)
        ov_preview = overflow_menu.addAction(
            icon("eye", size=16), "Xem trước PDF\t{}".format(command("preview").shortcut)
        )
        ov_preview.setShortcut(QKeySequence(command("preview").shortcut))
        ov_preview.triggered.connect(self.toggle_preview)

        ov_refresh = overflow_menu.addAction(
            icon("refresh-cw", size=16), "Làm mới dữ liệu\t{}".format(command("refresh").shortcut)
        )
        ov_refresh.setShortcut(QKeySequence(command("refresh").shortcut))
        ov_refresh.triggered.connect(self.refresh_view)

        overflow_menu.addSeparator()

        ov_settings = overflow_menu.addAction(
            icon("settings", size=16), "Cài đặt...\t{}".format(command("settings").shortcut)
        )
        ov_settings.setShortcut(QKeySequence(command("settings").shortcut))
        ov_settings.triggered.connect(self._show_settings_placeholder)

        ov_backup = overflow_menu.addAction(
            icon("database", size=16), "Sao lưu cơ sở dữ liệu..."
        )
        ov_backup.triggered.connect(self.backup_database)

        ov_check = overflow_menu.addAction(
            icon("folder-search", size=16), "Kiểm tra đường dẫn file..."
        )
        ov_check.triggered.connect(self.check_file_paths)

        ov_reindex = overflow_menu.addAction(
            icon("refresh-cw", size=16), "Xây lại chỉ mục AI..."
        )
        ov_reindex.triggered.connect(self.show_reindex_dialog)

        overflow_menu.addSeparator()

        ov_help = overflow_menu.addAction(
            icon("help-circle", size=16), "Hướng dẫn sử dụng"
        )
        ov_help.triggered.connect(self.show_help)

        ov_about = overflow_menu.addAction(
            icon("info", size=16), "Về ứng dụng..."
        )
        ov_about.triggered.connect(self.show_about)

        overflow_btn = QToolButton(toolbar)
        overflow_btn.setIcon(icon("more-horizontal", size=20))
        overflow_btn.setToolTip("Công cụ khác")
        overflow_btn.setMenu(overflow_menu)
        overflow_btn.setPopupMode(QToolButton.InstantPopup)
        toolbar.addWidget(overflow_btn)
        # Keep refs for shortcut triggering across the window.
        self._overflow_menu = overflow_menu
        self._overflow_actions = {
            "preview": ov_preview, "refresh": ov_refresh,
            "settings": ov_settings, "backup": ov_backup,
            "check_files": ov_check, "reindex": ov_reindex,
            "help": ov_help, "about": ov_about,
        }

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

    def _update_toolbar_context(self, filename: str = "", page_count: int = 0) -> None:
        """Update the toolbar center label when a document is loaded/cleared."""
        if not hasattr(self, "toolbar_context_label"):
            return
        if not filename:
            self.toolbar_context_label.setText("Chưa chọn tài liệu")
            self.toolbar_context_label.setProperty("empty", "true")
        else:
            short = filename if len(filename) <= 60 else filename[:57] + "..."
            suffix = f" · {page_count} trang" if page_count else ""
            self.toolbar_context_label.setText(f"{short}{suffix}")
            self.toolbar_context_label.setProperty("empty", "false")
        # Force stylesheet re-evaluation after dynamic property change.
        self.toolbar_context_label.style().unpolish(self.toolbar_context_label)
        self.toolbar_context_label.style().polish(self.toolbar_context_label)

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
        """Recolor every persistent Lucide icon after a light/dark switch."""
        base = "#E8EBF5" if theme == "dark" else "#24432D"
        self.act_dark_mode.blockSignals(True)
        self.act_dark_mode.setChecked(theme == "dark")
        self.act_dark_mode.blockSignals(False)
        accents = {
            "delete": "#F87171" if theme == "dark" else "#B3263E",
            "chat": "#E5C466" if theme == "dark" else "#846314",
            "export_excel": "#6EE7B7" if theme == "dark" else "#1D6F42",
        }
        for command_id, action in getattr(self, "_command_actions", {}).items():
            try:
                spec = command(command_id)
            except KeyError:
                continue
            if spec.icon:
                action.setIcon(icon(spec.icon, size=20, color=accents.get(command_id, base)))

        extras = {
            getattr(self, "_act_add_batch", None): "files",
            getattr(self, "_act_add_folder", None): "folder-open",
            getattr(self, "_act_ai_search", None): "sparkles",
        }
        extras.update({
            action: name for key, name in {
                "backup": "database", "check_files": "folder-search",
                "reindex": "refresh-cw", "help": "help-circle", "about": "info",
            }.items()
            for action in (getattr(self, "_overflow_actions", {}).get(key),)
        })
        for action, name in extras.items():
            if action is not None:
                action.setIcon(icon(name, size=16, color=base))

        pdf_icons = {
            "first_btn": "chevrons-left", "prev_btn": "chevron-left",
            "next_btn": "chevron-right", "last_btn": "chevrons-right",
            "zoom_out_btn": "zoom-out", "zoom_in_btn": "zoom-in",
            "rotate_left_btn": "rotate-ccw", "rotate_right_btn": "rotate-cw",
        }
        for attr, name in pdf_icons.items():
            getattr(self.pdf_viewer, attr).setIcon(icon(name, size=17, color=base))

        editor_icons = {
            "_toggle_meta_btn": "panel-right", "_calendar_btn": "calendar",
            "reocr_btn": "refresh-cw", "_copy_btn": "copy",
            "_export_word_btn": "file-text", "reset_btn": "undo-2",
            "save_btn": "save",
        }
        for attr, name in editor_icons.items():
            widget = getattr(self.ocr_editor, attr, None)
            if widget is not None:
                widget.setIcon(icon(name, size=18, color=base))
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
            "toggle_metadata": self.ocr_editor._toggle_meta_btn.toggle,
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
        """Status bar with live indicators (Phase 12, 2026-04-18).

        Layout (left → right):
          [doc_info]  ··· stretch ···  [workers] [VRAM bar] [Llama dot] [doc count]

        All indicators driven by single StatusIndicatorCoordinator (10s poll,
        paused when window not active). Light-weight: QLabel + 2 tiny custom
        widgets, no custom paint loops.
        """
        statusbar = self.statusBar()

        # Left side — current document info (variable width)
        self.doc_info_label = QLabel("")
        self.doc_info_label.setProperty("role", "muted")
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

        # VRAM bar + text
        self.vram_indicator = VramIndicator()
        statusbar.addPermanentWidget(self.vram_indicator)
        self._status_coord.register(self.vram_indicator)

        # Llama-server status dot
        self.llama_indicator = LlamaStatusIndicator()
        statusbar.addPermanentWidget(self.llama_indicator)
        self._status_coord.register(self.llama_indicator)

        # Doc count (rightmost)
        self.doc_count_label = QLabel("0 văn bản")
        self.doc_count_label.setProperty("role", "statusStrong")
        statusbar.addPermanentWidget(self.doc_count_label)

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
        """Minimalist list context menu (2026-04-18 redesign).

        Order: Mở PDF · OCR lại · Xuất nhanh (Word) · — · Tags · Di chuyển · — · Xóa (red)
        """
        try:
            doc_id = int(doc_id)
            ctx = QMenu(self)
            ctx_color = "#E8EBF5" if self._theme_mgr_v2.is_dark else "#24432D"

            open_pdf = ctx.addAction(icon("eye", size=16, color=ctx_color), "Mở PDF")
            reocr = ctx.addAction(icon("refresh-cw", size=16, color=ctx_color), "OCR lại")
            quick_export = ctx.addAction(icon("file-text", size=16, color=ctx_color),
                                         "Xuất nhanh (Word)")
            ctx.addSeparator()

            # Tag submenu
            tags_menu = ctx.addMenu("Gắn Tag")
            tags_menu.setIcon(icon("tag", size=16, color=ctx_color))
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
            new_tag_action = tags_menu.addAction("+ Tag mới...")

            move_action = ctx.addAction(icon("folder-open", size=16, color=ctx_color), "Di chuyển...")
            move_action.setEnabled(False)  # placeholder until folder feature lands

            ctx.addSeparator()

            delete_action = ctx.addAction(icon("trash-2", size=16, color="#B3263E"), "Xóa")
            delete_action.setProperty("danger", True)
            # force stylesheet re-apply after dynamic property set
            delete_action.setIconText("Xóa")

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
            # update_document_table already populates the minimalist list.
            
            # Update document count
            doc_count = len(documents)
            self.doc_count_label.setText(f"{doc_count} văn bản")
            if hasattr(self, "_library_count_label"):
                self._library_count_label.setText(f"{doc_count} văn bản")
            
        except Exception as e:
            logger.error(f"Error loading documents: {str(e)}")
            QMessageBox.critical(self, "Error", f"Error loading documents: {str(e)}")

    def _on_doc_filter_changed(self, text: str) -> None:
        """Live-filter the document list by filename / so_ki_hieu substring.

        Case-insensitive. Empty text → show all. Runs on cached snapshot
        to avoid DB hits. Pairs with the sort combo — result is
        (snapshot ∩ filter) sorted by current sort key.
        """
        text = (text or "").strip().lower()
        if not hasattr(self, '_docs_snapshot') or self._docs_snapshot is None:
            return

        def _doc_of(entry):
            return entry[0] if isinstance(entry, tuple) and len(entry) == 2 \
                and isinstance(entry[1], dict) else entry

        if not text:
            filtered = self._docs_snapshot
        else:
            filtered = []
            for entry in self._docs_snapshot:
                doc = _doc_of(entry)
                hay = (
                    (doc[2] or "").lower() if len(doc) > 2 else ""
                ) + " " + (
                    (doc[8] or "").lower() if len(doc) > 8 else ""
                )
                if text in hay:
                    filtered.append(entry)

        # Apply current sort order before rebuilding.
        sort_key = self._doc_sort_combo.currentData() if hasattr(self, '_doc_sort_combo') else 'id_desc'
        if sort_key == 'name_desc':
            filtered = sorted(filtered, key=lambda e: (_doc_of(e)[2] or "").lower(), reverse=True)
        elif sort_key == 'name_asc':
            filtered = sorted(filtered, key=lambda e: (_doc_of(e)[2] or "").lower())
        elif sort_key == 'date_desc':
            filtered = sorted(filtered, key=lambda e: str(_doc_of(e)[3] or ""), reverse=True)
        elif sort_key == 'date_asc':
            filtered = sorted(filtered, key=lambda e: str(_doc_of(e)[3] or ""))
        elif sort_key == 'id_asc':
            filtered = sorted(filtered, key=lambda e: int(_doc_of(e)[0] or 0))
        else:
            filtered = sorted(filtered, key=lambda e: -int(_doc_of(e)[0] or 0))

        self._rebuild_doc_list_only(filtered)

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

    def _on_doc_sort_changed(self, _idx: int) -> None:
        """Re-render the doc list with new sort order. Preserves selection."""
        if not hasattr(self, '_docs_snapshot') or self._docs_snapshot is None:
            return
        selected = self.doc_list.selected_doc_ids() if hasattr(self, 'doc_list') else []
        sort_key = self._doc_sort_combo.currentData()

        def _doc_of(entry):
            return entry[0] if isinstance(entry, tuple) and len(entry) == 2 \
                and isinstance(entry[1], dict) else entry

        def sort_fn(entry):
            d = _doc_of(entry)
            try:
                did = int(d[0])
            except Exception:
                did = 0
            name = (d[2] or "") if len(d) > 2 else ""
            date = str(d[3]) if len(d) > 3 else ""
            if sort_key == 'id_desc': return (-did,)
            if sort_key == 'id_asc':  return (did,)
            if sort_key == 'name_asc':  return (name.lower(), did)
            if sort_key == 'name_desc': return (tuple(-ord(c) for c in name.lower()[:1]) or (0,), -did)
            if sort_key == 'date_desc': return (date,) if date else ("",)
            if sort_key == 'date_asc':  return (date,) if date else ("",)
            return (-did,)

        reverse = sort_key in ('name_desc', 'date_desc')
        # name_desc we handle via reverse flag on lowercase name.
        if sort_key == 'name_desc':
            sorted_docs = sorted(self._docs_snapshot,
                                 key=lambda e: (_doc_of(e)[2] or "").lower(),
                                 reverse=True)
        elif sort_key == 'date_desc':
            sorted_docs = sorted(self._docs_snapshot,
                                 key=lambda e: str(_doc_of(e)[3] or ""),
                                 reverse=True)
        else:
            sorted_docs = sorted(self._docs_snapshot, key=sort_fn)

        self._rebuild_doc_list_only(sorted_docs)
        # Restore selection where possible.
        if selected:
            for doc_id in selected:
                self.doc_list.find_and_select(doc_id)

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
            try:
                version = self.db.get_latest_version(doc_id)
                if version:
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
            }

            # Status based on actual van_ban length (not heuristic).
            if van_ban_preview and len(van_ban_preview) >= 300:
                status = 'done'
            elif van_ban_preview or nd_chinh:
                status = 'partial'
            else:
                status = 'pending'
            self.doc_list.add_document(doc_id, filename, status, stats, meta)

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
                self.metadata_fields['filesize'].setText("File not found")
                    
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
            
            # Định dạng màu cho độ khẩn
            if do_khan == 'Hỏa Tốc':
                self.metadata_fields['do_khan'].setStyleSheet("color: red; font-weight: bold;")
            elif do_khan == 'Thượng Khẩn':
                self.metadata_fields['do_khan'].setStyleSheet("color: orangered; font-weight: bold;")
            elif do_khan == 'Khẩn':
                self.metadata_fields['do_khan'].setStyleSheet("color: orange; font-weight: bold;")
            else:
                self.metadata_fields['do_khan'].setStyleSheet("")
                
            # Độ mật with color coding
            do_mat = doc_version[8] or 'Không'  # Chỉ số 8 là do_mat trong bảng
            self.metadata_fields['do_mat'].setText(do_mat)
            
            # Định dạng màu cho độ mật
            if do_mat == 'Mật':
                self.metadata_fields['do_mat'].setStyleSheet("color: darkgreen; font-weight: bold;")
            elif do_mat == 'Tối Mật':
                self.metadata_fields['do_mat'].setStyleSheet("color: darkblue; font-weight: bold;")
            elif do_mat == 'Tuyệt Mật':
                self.metadata_fields['do_mat'].setStyleSheet("color: purple; font-weight: bold;")
            else:
                self.metadata_fields['do_mat'].setStyleSheet("")
                    
        except Exception as e:
            logger.error(f"Error updating metadata: {str(e)}")
            for field in self.metadata_fields.values():
                field.clear()

    def update_status_info(self, doc_id, doc_info):
        """Update status bar with document info"""
        if doc_info and os.path.exists(doc_info[1]):
            file_info = f"ID: {doc_id} | {os.path.basename(doc_info[1])}"
            self.doc_info_label.setText(file_info)
        else:
            self.doc_info_label.setText("")

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

                self._dispatch_embedding(doc_id)
                
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
        """Bôi vàng từ khóa tìm kiếm trong OCR output"""
        if not hasattr(self, 'ocr_editor'):
            return

        # Tìm QTextEdit chứa văn bản dạng txt trong ocr_editor
        text_edit = None
        if hasattr(self.ocr_editor, 'van_ban_txt_edit'):
            text_edit = self.ocr_editor.van_ban_txt_edit
        elif hasattr(self.ocr_editor, 'fields') and 'van_ban_dang_txt' in self.ocr_editor.fields:
            text_edit = self.ocr_editor.fields['van_ban_dang_txt']

        if text_edit is None:
            return

        # Clear old highlights
        cursor = text_edit.textCursor()
        cursor.select(QTextCursor.Document)
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(Qt.transparent))
        cursor.mergeCharFormat(fmt)

        # Apply yellow highlights cho từng keyword
        highlight_fmt = QTextCharFormat()
        highlight_fmt.setBackground(QColor("yellow"))

        for keyword in keywords:
            cursor = text_edit.textCursor()
            cursor.movePosition(QTextCursor.Start)

            while True:
                cursor = text_edit.document().find(keyword, cursor)
                if cursor.isNull():
                    break
                cursor.mergeCharFormat(highlight_fmt)

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
        """About dialog using the same replaceable wordmark as the landing."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Về ứng dụng")
        dialog.setFixedSize(520, 480)
        dialog.setStyleSheet("QDialog { background: #F5F1E8; }")

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)

        # ── Header: logo + title ──────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(16)

        logo_label = QLabel("OV")
        logo_label.setFixedSize(72, 72)
        logo_label.setAlignment(Qt.AlignCenter)
        logo_label.setStyleSheet(
            "color:#24432D;border:1px solid #24432D;border-radius:16px;"
            "font-size:20pt;font-weight:700;background:#FFFEFA;"
        )
        header.addWidget(logo_label)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        app_title = QLabel("OCR VBHC Desktop")
        app_title.setStyleSheet(
            "font-size: 18pt; font-weight: 700; color: #24432D;"
            " font-family: 'Be Vietnam Pro';"
        )
        title_col.addWidget(app_title)
        version_label = QLabel(f"Phiên bản {APP_VERSION} · Build 2026-04-17")
        version_label.setStyleSheet(
            "font-size: 10pt; color: #5C665F; font-family: 'Be Vietnam Pro';"
        )
        title_col.addWidget(version_label)
        title_col.addStretch()
        header.addLayout(title_col, 1)

        layout.addLayout(header)

        # ── Divider ───────────────────────────────────────────────
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("background: #D8D1C3; max-height: 1px; border: none;")
        layout.addWidget(divider)

        # ── Tagline ───────────────────────────────────────────────
        tagline = QLabel(
            "Rút ngắn thời gian phân loại và tìm kiếm văn bản dạng PDF theo "
            "<b>Nghị định 30/2020/NĐ-CP</b>."
        )
        tagline.setWordWrap(True)
        tagline.setStyleSheet(
            "font-size: 11pt; color: #17231B; font-family: 'Be Vietnam Pro';"
            " line-height: 1.5;"
        )
        layout.addWidget(tagline)

        # ── Features ──────────────────────────────────────────────
        features_label = QLabel("Tính năng chính")
        features_label.setStyleSheet(
            "font-size: 10pt; font-weight: 700; color: #24432D;"
            " font-family: 'Be Vietnam Pro'; margin-top: 4px;"
        )
        layout.addWidget(features_label)

        features_list = QLabel(
            "<ul style='margin: 4px 0 4px 12px; line-height: 1.7;'>"
            "<li>Chandra OCR 2 — trích xuất 12 trường NĐ 30/2020</li>"
            "<li>Tìm kiếm ngữ nghĩa (AI + rerank) trên toàn bộ kho văn bản</li>"
            "<li>Xuất Word / Excel / HTML / Markdown một nút bấm</li>"
            "<li>Hoạt động 100% offline — an toàn, bảo mật dữ liệu</li>"
            "</ul>"
        )
        features_list.setTextFormat(Qt.RichText)
        features_list.setStyleSheet(
            "font-size: 10pt; color: #17231B; font-family: 'Be Vietnam Pro';"
        )
        layout.addWidget(features_list)

        layout.addStretch()

        # ── Divider ───────────────────────────────────────────────
        divider2 = QFrame()
        divider2.setFrameShape(QFrame.HLine)
        divider2.setStyleSheet("background: #D8D1C3; max-height: 1px; border: none;")
        layout.addWidget(divider2)

        # ── Footer: credits + close button ────────────────────────
        footer = QHBoxLayout()
        credits = QLabel(
            "© 2026 <b>OCR VBHC</b><br/>Xử lý hoàn toàn cục bộ"
        )
        credits.setTextFormat(Qt.RichText)
        credits.setStyleSheet(
            "font-size: 9pt; color: #5C665F; font-family: 'Be Vietnam Pro';"
        )
        footer.addWidget(credits, 1)

        close_btn = QPushButton("Đóng")
        close_btn.setFixedWidth(100)
        close_btn.setStyleSheet(
            "QPushButton { background: #24432D; color: #F5F1E8;"
            " border: none; border-radius: 6px; padding: 8px 16px;"
            " font-weight: 600; font-family: 'Be Vietnam Pro'; }"
            "QPushButton:hover { background: #315A3E; }"
        )
        close_btn.clicked.connect(dialog.accept)
        footer.addWidget(close_btn)

        layout.addLayout(footer)

        dialog.exec_()

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
