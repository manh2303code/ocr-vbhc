"""Progress dialog for rebuilding all local AI knowledge indexes."""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from background_workers import ReindexWorker


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
        self.setWindowTitle("Xây lại chỉ mục AI")
        self.setMinimumWidth(460)
        self.setWindowModality(Qt.WindowModal)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        intro = QLabel(
            "Đang cập nhật tìm kiếm ngữ nghĩa và quan hệ pháp lý. "
            "Dữ liệu chỉ được xử lý trên máy này."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.detail_label = QLabel("Đang chuẩn bị…")
        self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.detail_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        layout.addWidget(self.progress_bar)

        self.buttons = QDialogButtonBox()
        self.cancel_button = QPushButton("Dừng")
        self.buttons.addButton(self.cancel_button, QDialogButtonBox.RejectRole)
        self.cancel_button.clicked.connect(self.cancel)
        layout.addWidget(self.buttons)

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
        self.detail_label.setText("Đang dừng sau văn bản hiện tại…")
        self.worker.cancel()

    def _on_progress(self, current, total, name):
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(current)
        self.detail_label.setText(
            "{}/{} — {}".format(current, total, name)
        )

    def _on_completed(self, count):
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.detail_label.setText(
            "Đã dừng. {} văn bản đã được cập nhật.".format(count)
            if self._cancelled
            else "Hoàn tất: {} văn bản đã được cập nhật.".format(count)
        )
        self.cancel_button.setText("Đóng")
        self.cancel_button.setEnabled(True)
        self.completed.emit(count)

    def _on_error(self, message):
        self.detail_label.setText("Không thể xây lại chỉ mục: {}".format(message))
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
