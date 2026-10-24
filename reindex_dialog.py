"""Progress dialog for rebuilding all local AI knowledge indexes.

2026-10 minimal redesign: a short title, one muted line, a thin progress
bar, then the count and the document being read as two separate parts
(no "12/30 — name" string). Styled by the "analytics" area of the theme
stylesheet (objectNames reindex*); no colour is written here.
"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from background_workers import ReindexWorker
from editor_widgets import ElidedLabel


class ReindexDialog(QDialog):
    completed = pyqtSignal(int)
    failed = pyqtSignal(str)

    def __init__(
        self, db_path, embedding_model, doc_ids=None, parent=None, worker_tracker=None
    ):
        super().__init__(parent)
        self.db_path = db_path
        self.embedding_model = embedding_model
        self.doc_ids = doc_ids
        self.worker_tracker = worker_tracker
        self.worker = None
        self._started = False
        self._cancelled = False
        self._close_when_stopped = False
        self.setObjectName("reindexDialog")
        self.setWindowTitle("Xây lại chỉ mục AI")
        self.setMinimumWidth(460)
        self.setWindowModality(Qt.WindowModal)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(0)
        title = QLabel("Đang cập nhật tìm kiếm ngữ nghĩa và quan hệ pháp lý")
        title.setObjectName("reindexTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        layout.addSpacing(4)
        intro = QLabel("Dữ liệu chỉ được xử lý trên máy này.")
        intro.setObjectName("reindexNote")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addSpacing(16)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("reindexProgress")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        layout.addSpacing(8)

        # While it runs: how far, and the document being read (its name
        # ends in "…" when long, whole in the tooltip). Otherwise a sentence.
        detail = QHBoxLayout()
        detail.setSpacing(10)
        self.count_label = QLabel("")
        self.count_label.setObjectName("reindexCount")
        detail.addWidget(self.count_label)
        self.name_label = ElidedLabel("")
        self.name_label.setObjectName("reindexName")
        self.name_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        detail.addWidget(self.name_label, 1)
        layout.addLayout(detail)
        self.detail_label = QLabel("Đang chuẩn bị…")
        self.detail_label.setObjectName("reindexDetail")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.detail_label)
        self._show_progress_line(False)
        layout.addSpacing(16)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("Dừng")
        self.cancel_button.setObjectName("reindexCancel")
        self.cancel_button.setToolTip("Dừng sau văn bản đang xử lý; phần đã xong được giữ lại")
        self.cancel_button.clicked.connect(self.cancel)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    def _show_progress_line(self, running):
        self.count_label.setVisible(running)
        self.name_label.setVisible(running)
        self.detail_label.setVisible(not running)

    def _message(self, text):
        """A whole sentence replaces the count and the document name."""
        self._show_progress_line(False)
        self.detail_label.setText(text)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._started:
            self.start()

    def start(self):
        if self._started:
            return
        self._started = True
        self.worker = ReindexWorker(
            self.db_path, self.embedding_model, self.doc_ids
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_completed)
        self.worker.error.connect(self._on_error)
        self.worker.finished.connect(self._on_stopped)
        if self.worker_tracker:
            self.worker_tracker(self.worker)
        self.worker.start()

    def cancel(self):
        if self.worker is None or not self.worker.isRunning():
            self.accept()
            return
        self._cancelled = True
        self.cancel_button.setEnabled(False)
        self._message("Đang dừng sau văn bản hiện tại…")
        self.worker.cancel()

    def _on_progress(self, current, total, name):
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(current)
        self.count_label.setText("{}/{}".format(current, total))
        self.name_label.set_full_text(name)
        self.name_label.setToolTip(name)
        self._show_progress_line(True)

    def _on_completed(self, count):
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self._message(
            "Đã dừng. {} văn bản đã được cập nhật.".format(count)
            if self._cancelled
            else "Hoàn tất: {} văn bản đã được cập nhật.".format(count)
        )
        self.cancel_button.setText("Đóng")
        self.cancel_button.setEnabled(True)
        self.completed.emit(count)

    def _on_error(self, message):
        self._message("Không thể xây lại chỉ mục: {}".format(message))
        self.detail_label.setProperty("state", "error")
        self.detail_label.style().unpolish(self.detail_label)
        self.detail_label.style().polish(self.detail_label)
        self.cancel_button.setText("Đóng")
        self.cancel_button.setEnabled(True)
        self.failed.emit(message)

    def _on_stopped(self):
        if self._close_when_stopped:
            self.accept()
        else:
            self.cancel_button.setText("Đóng")
            self.cancel_button.setEnabled(True)

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self._close_when_stopped = True
            self.cancel()
            event.ignore()
            return
        super().closeEvent(event)
