"""
AI Search Dialog - Giao diện tìm kiếm ngữ nghĩa bằng AI
Cho phép người dùng nhập mô tả tự nhiên để tìm văn bản liên quan.
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QLabel, QHeaderView,
    QWidget, QProgressBar, QApplication, QMessageBox,
)
from PyQt5.QtCore import Qt, pyqtSignal, QThread
from PyQt5.QtGui import QIcon, QColor
import logging
import sqlite3
from contextlib import closing

from retrieval import hybrid_search
from reindex_dialog import ReindexDialog
from vector_index import VectorIndex

logger = logging.getLogger("AISearch")


def _hybrid_or_legacy_search(
    embedding_model, vector_store, vector_index, query_text, reranker=None
):
    """Return the dialog's legacy tuple shape, falling back on any hybrid error."""
    try:
        with closing(sqlite3.connect(vector_store.db_path)) as conn, conn:
            chunks = hybrid_search(
                conn, vector_index, embedding_model, reranker, query_text,
                doc_ids=None,
            )
        best = {}
        for chunk in chunks:
            if chunk.doc_id not in best or chunk.score > best[chunk.doc_id].score:
                best[chunk.doc_id] = chunk
        return [
            (chunk.doc_id, chunk.score, chunk.chunk_text)
            for chunk in sorted(best.values(), key=lambda item: item.score, reverse=True)
        ]
    except Exception as exc:
        logger.warning("Hybrid search failed; using legacy vector search: %s", exc)
        embedding = embedding_model.encode(query_text)
        if embedding is None:
            raise ValueError("Không thể mã hóa truy vấn tìm kiếm")
        return vector_store.search_chunks(
            embedding, reranker=reranker, query_text=query_text
        )


class EmbeddingWorker(QThread):
    """Worker thread để encode query + search, tránh đóng băng UI."""

    finished = pyqtSignal(list)  # List[(doc_id, score, best_chunk_text)]
    error = pyqtSignal(str)

    def __init__(
        self, embedding_model, vector_store, vector_index, query_text, reranker=None
    ):
        super().__init__()
        self.embedding_model = embedding_model
        self.vector_store = vector_store
        self.vector_index = vector_index
        self.query_text = query_text
        self.reranker = reranker

    def run(self):
        import time as _t
        import logging as _log
        _logger = _log.getLogger("AISearchTiming")
        try:
            t0 = _t.time()
            results = _hybrid_or_legacy_search(
                self.embedding_model, self.vector_store, self.vector_index,
                self.query_text, reranker=self.reranker,
            )
            total = _t.time() - t0
            _logger.info(
                f"[SEARCH] query={self.query_text[:40]!r} | "
                f"total={total*1000:.0f}ms | results={len(results)}"
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class AISearchDialog(QDialog):
    """Dialog tìm kiếm ngữ nghĩa bằng AI embedding."""

    document_selected = pyqtSignal(int, list)  # doc_id, keywords (rỗng cho AI search)

    def __init__(self, db, embedding_model, vector_store, reranker=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.embedding_model = embedding_model
        self.vector_store = vector_store
        self.vector_index = VectorIndex(vector_store.db_path)
        if not self.vector_index.is_native():
            with closing(sqlite3.connect(vector_store.db_path)) as conn, conn:
                self.vector_index.sync_from_chunks(conn)
        self.reranker = reranker
        self.search_worker = None
        self.setWindowTitle("AI Tìm kiếm ngữ nghĩa")
        self.setWindowIcon(QIcon("icons/ai_search.png"))
        self.resize(900, 550)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # Header với icon
        header_layout = QHBoxLayout()
        header_icon = QLabel()
        header_icon.setPixmap(QIcon("icons/ai_search.png").pixmap(24, 24))
        header_title = QLabel("Tìm kiếm thông minh")
        header_title.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #2E7D32;"
        )
        header_layout.addWidget(header_icon)
        header_layout.addWidget(header_title)
        header_layout.addStretch()

        # Số embeddings đã index
        embed_count = self.vector_store.get_embedding_count()
        self.count_label = QLabel(f"Đã đánh chỉ mục: {embed_count} tài liệu")
        self.count_label.setStyleSheet("color: #888; font-size: 11px;")
        header_layout.addWidget(self.count_label)

        # Nút cập nhật chỉ mục
        self.reindex_btn = QPushButton("Cập nhật chỉ mục")
        self.reindex_btn.setCursor(Qt.PointingHandCursor)
        self.reindex_btn.setToolTip(
            "Tạo lại chỉ mục tìm kiếm cho toàn bộ tài liệu.\n"
            "Dùng khi kết quả tìm kiếm chưa chính xác."
        )
        self.reindex_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #F57C00; }
            QPushButton:disabled { background-color: #FFCC80; color: #FFF3E0; }
        """)
        self.reindex_btn.clicked.connect(self.do_reindex)
        header_layout.addWidget(self.reindex_btn)

        layout.addLayout(header_layout)

        # Hướng dẫn
        hint = QLabel(
            "Nhập mô tả nội dung văn bản cần tìm bằng ngôn ngữ tự nhiên.\n"
            'Ví dụ: "Công văn tỉnh Hưng Yên về sử dụng hộ chiếu ngoại giao"'
        )
        hint.setStyleSheet("color: #666; font-style: italic; padding: 4px 0;")
        layout.addWidget(hint)

        # Input row
        input_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Mô tả nội dung văn bản cần tìm..."
        )
        self.search_input.setMinimumHeight(38)
        self.search_input.setStyleSheet("""
            QLineEdit {
                border: 2px solid #4CAF50;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 14px;
            }
            QLineEdit:focus {
                border-color: #2E7D32;
            }
        """)
        self.search_input.returnPressed.connect(self.do_search)

        self.search_btn = QPushButton("  Tìm kiếm AI")
        self.search_btn.setIcon(QIcon("icons/ai_search.png"))
        self.search_btn.setMinimumHeight(38)
        self.search_btn.setMinimumWidth(140)
        self.search_btn.setCursor(Qt.PointingHandCursor)
        self.search_btn.clicked.connect(self.do_search)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 18px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #388E3C;
            }
            QPushButton:pressed {
                background-color: #1B5E20;
            }
            QPushButton:disabled {
                background-color: #A5D6A7;
                color: #E8F5E9;
            }
        """)

        input_layout.addWidget(self.search_input)
        input_layout.addWidget(self.search_btn)
        layout.addLayout(input_layout)

        # Bảng kết quả
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(5)
        self.result_table.setHorizontalHeaderLabels([
            "#", "Tên văn bản", "Số ký hiệu", "Độ liên quan", "Xem PDF",
        ])
        self.result_table.horizontalHeader().setFixedHeight(30)
        self.result_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.setAlternatingRowColors(True)
        self.result_table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
                gridline-color: #eee;
            }
            QTableWidget::item {
                padding: 4px 8px;
            }
            QTableWidget::item:selected {
                background-color: #E8F5E9;
                color: #333;
            }
            QHeaderView::section {
                background-color: #f5f5f5;
                border: none;
                border-bottom: 2px solid #4CAF50;
                padding: 6px;
                font-weight: bold;
            }
        """)

        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        self.result_table.setColumnWidth(0, 40)
        self.result_table.setColumnWidth(2, 130)
        self.result_table.setColumnWidth(3, 140)
        self.result_table.setColumnWidth(4, 90)

        # AI Response - phản hồi tự nhiên từ AI
        self.response_label = QLabel("")
        self.response_label.setWordWrap(True)
        self.response_label.setStyleSheet(
            "color: #333; padding: 8px 12px; font-size: 13px; "
            "background-color: #F1F8E9; border-radius: 6px; "
            "border-left: 4px solid #4CAF50;"
        )
        self.response_label.setVisible(False)
        layout.addWidget(self.response_label)

        layout.addWidget(self.result_table)

        # Status bar (cho loading/error)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #555; padding: 4px 0;")
        layout.addWidget(self.status_label)

        self.search_input.setFocus()

    def do_search(self):
        """Thực hiện tìm kiếm ngữ nghĩa."""
        query = self.search_input.text().strip()
        if not query:
            self.status_label.setText("Vui lòng nhập nội dung cần tìm.")
            self.status_label.setStyleSheet("color: #F44336; padding: 4px 0;")
            return

        if not self.embedding_model.is_available():
            self.status_label.setText(
                "Mô hình embedding chưa được cài đặt. "
                "Vui lòng tải AITeamVN/Vietnamese_Embedding vào thư mục model_embedding/"
            )
            self.status_label.setStyleSheet("color: #F44336; padding: 4px 0;")
            return

        self.search_btn.setEnabled(False)
        self.search_btn.setText("  Đang tìm...")
        self.status_label.setText("Đang phân tích truy vấn và tìm kiếm...")
        self.status_label.setStyleSheet("color: #1976D2; padding: 4px 0;")
        QApplication.processEvents()

        self.search_worker = EmbeddingWorker(
            self.embedding_model,
            self.vector_store,
            self.vector_index,
            query,
            reranker=self.reranker,
        )
        self.search_worker.finished.connect(self.on_search_complete)
        self.search_worker.error.connect(self.on_search_error)
        self.search_worker.start()

    def _build_ai_response(self, query: str, results: list) -> str:
        """Tạo phản hồi tự nhiên từ AI dựa trên kết quả tìm kiếm."""
        total_docs = self.vector_store.get_embedding_count()

        if not results:
            return (
                f'Tôi đã tìm kiếm trong toàn bộ {total_docs} tài liệu '
                f'nhưng không tìm thấy kết quả nào phù hợp với "{query}". '
                f'Bạn thử mô tả lại bằng cách khác hoặc dùng từ khóa cụ thể hơn nhé.'
            )

        top_score = int(results[0][1] * 100)
        count = len(results)

        if count == 1:
            return (
                f'Tôi tìm thấy 1 tài liệu có liên quan tới "{query}" '
                f'với độ phù hợp {top_score}%. '
                f'Nếu chưa đúng ý, bạn thử mô tả chi tiết hơn nhé.'
            )

        if count <= 3:
            return (
                f'Có {count} tài liệu có vẻ liên quan tới "{query}". '
                f'Kết quả phù hợp nhất đạt {top_score}%. '
                f'Nếu chưa tìm được văn bản cần thiết, thử diễn đạt lại truy vấn.'
            )

        if top_score >= 70:
            confidence = "Kết quả đầu tiên rất có khả năng đúng với nội dung bạn cần."
        elif top_score >= 50:
            confidence = "Tài liệu đầu tiên có khả năng chứa nội dung bạn cần tìm."
        else:
            confidence = "Các kết quả mang tính tham khảo, bạn nên xem qua từng tài liệu."

        return (
            f'Đây là {count} kết quả có liên quan nhất tới "{query}", '
            f'được sắp xếp theo mức độ phù hợp (cao nhất {top_score}%). '
            f'{confidence}'
        )

    def on_search_complete(self, results):
        """Xử lý kết quả tìm kiếm."""
        self.search_btn.setEnabled(True)
        self.search_btn.setText("  Tìm kiếm AI")
        self.status_label.setText("")
        self.result_table.setRowCount(0)

        query = self.search_input.text().strip()

        # Hiển thị phản hồi AI
        ai_response = self._build_ai_response(query, results)
        self.response_label.setText(ai_response)
        self.response_label.setVisible(True)

        if not results:
            self.response_label.setStyleSheet(
                "color: #E65100; padding: 8px 12px; font-size: 13px; "
                "background-color: #FFF3E0; border-radius: 6px; "
                "border-left: 4px solid #FF9800;"
            )
            return

        self.response_label.setStyleSheet(
            "color: #333; padding: 8px 12px; font-size: 13px; "
            "background-color: #F1F8E9; border-radius: 6px; "
            "border-left: 4px solid #4CAF50;"
        )

        self.result_table.setSortingEnabled(False)
        self.result_table.setRowCount(len(results))

        for row, (doc_id, score, best_chunk_text) in enumerate(results):
            # Cột #
            rank_item = QTableWidgetItem(str(row + 1))
            rank_item.setTextAlignment(Qt.AlignCenter)
            rank_item.setData(Qt.UserRole, doc_id)
            self.result_table.setItem(row, 0, rank_item)

            # Lấy thông tin tài liệu
            doc_info = self.db.get_document_info(doc_id)
            version = self.db.get_latest_version(doc_id)

            # Cột Tên văn bản + tooltip preview nội dung
            file_name = doc_info[2] if doc_info else f"Tài liệu {doc_id}"
            name_item = QTableWidgetItem(file_name)
            name_item.setData(Qt.UserRole, doc_id)
            tooltip = self._build_tooltip(doc_info, version, best_chunk_text)
            name_item.setToolTip(tooltip)
            self.result_table.setItem(row, 1, name_item)

            # Cột Số ký hiệu
            so_ky_hieu = version[5] if version else ""
            ski_item = QTableWidgetItem(so_ky_hieu or "")
            ski_item.setTextAlignment(Qt.AlignCenter)
            self.result_table.setItem(row, 2, ski_item)

            # Cột Độ liên quan (progress bar + %)
            pct = int(score * 100)
            sim_widget = self._create_similarity_widget(pct)
            self.result_table.setCellWidget(row, 3, sim_widget)

            # Cột Xem PDF
            preview_btn = QPushButton("Xem PDF")
            preview_btn.setCursor(Qt.PointingHandCursor)
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
            preview_btn.clicked.connect(
                lambda checked, did=doc_id: self.preview_document(did)
            )
            self.result_table.setCellWidget(row, 4, preview_btn)

            # Row height
            self.result_table.setRowHeight(row, 36)

        self.status_label.setText(
            f"Hiển thị {len(results)} / {self.vector_store.get_embedding_count()} tài liệu"
        )
        self.status_label.setStyleSheet("color: #888; font-size: 11px; padding: 4px 0;")

    def _build_tooltip(self, doc_info, version, best_chunk_text: str = "") -> str:
        """Tạo tooltip preview nội dung tài liệu khi hover."""
        parts = []

        if doc_info:
            parts.append(f"Tệp: {doc_info[2]}")

        if version:
            # [9]=loai_vb, [10]=nd_chinh, [3]=cqbh_tren, [4]=cqbh_duoi, [5]=so_ki_hieu
            loai_vb = version[9] or ""
            nd_chinh = version[10] or ""
            cqbh_tren = version[3] or ""
            cqbh_duoi = version[4] or ""

            if loai_vb:
                parts.append(f"Loại: {loai_vb}")
            if cqbh_tren:
                parts.append(f"CQ ban hành: {cqbh_tren}")
            if cqbh_duoi:
                parts.append(f"CQ dưới: {cqbh_duoi}")
            if nd_chinh:
                preview = nd_chinh[:200] + "..." if len(nd_chinh) > 200 else nd_chinh
                parts.append(f"Nội dung: {preview}")

        # Hiển thị đoạn text match tốt nhất từ chunked search
        if best_chunk_text:
            chunk_preview = best_chunk_text[:300]
            if len(best_chunk_text) > 300:
                chunk_preview += "..."
            parts.append(f"\n--- Đoạn phù hợp nhất ---\n{chunk_preview}")

        if not parts:
            return "Không có thông tin chi tiết"

        return "\n".join(parts)

    def _create_similarity_widget(self, pct: int) -> QWidget:
        """Tạo widget hiển thị độ liên quan với progress bar + phần trăm."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(pct)
        bar.setTextVisible(False)
        bar.setFixedHeight(16)
        bar.setFixedWidth(70)

        if pct >= 80:
            color = "#4CAF50"   # Green - high relevance
        elif pct >= 50:
            color = "#FFC107"   # Yellow - moderate relevance
        else:
            color = "#9E9E9E"   # Grey - low relevance

        bar.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid #ddd;
                border-radius: 4px;
                background: #f0f0f0;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 3px;
            }}
        """)

        label = QLabel(f"{pct}%")
        label.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 12px;")
        label.setFixedWidth(38)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(bar)
        layout.addWidget(label)
        return widget

    def on_search_error(self, error_msg):
        """Xử lý lỗi tìm kiếm."""
        self.search_btn.setEnabled(True)
        self.search_btn.setText("  Tìm kiếm AI")
        self.status_label.setText(f"Lỗi: {error_msg}")
        self.status_label.setStyleSheet("color: #F44336; padding: 4px 0;")
        logger.error(f"AI Search error: {error_msg}")

    def do_reindex(self):
        """Tạo lại chỉ mục embedding cho toàn bộ tài liệu."""
        reply = QMessageBox.question(
            self,
            "Cập nhật chỉ mục",
            "Tạo lại chỉ mục tìm kiếm cho toàn bộ tài liệu?\n"
            "Quá trình này có thể mất vài phút tùy số lượng văn bản.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.reindex_btn.setEnabled(False)
        self.search_btn.setEnabled(False)
        self.reindex_btn.setText("Đang xử lý...")
        self.status_label.setText("Đang cập nhật chỉ mục...")
        self.status_label.setStyleSheet("color: #1976D2; padding: 4px 0;")

        dialog = ReindexDialog(
            self.db.db_path, self.embedding_model, parent=self
        )
        dialog.completed.connect(self.on_reindex_complete)
        dialog.failed.connect(self.on_reindex_error)
        dialog.exec_()

    def on_reindex_complete(self, success_count):
        """Hoàn tất reindex."""
        self.reindex_btn.setEnabled(True)
        self.search_btn.setEnabled(True)
        self.reindex_btn.setText("Cập nhật chỉ mục")

        if not self.vector_index.is_native():
            with closing(sqlite3.connect(self.vector_store.db_path)) as conn, conn:
                self.vector_index.sync_from_chunks(conn)

        embed_count = self.vector_store.get_embedding_count()
        self.count_label.setText(f"Đã đánh chỉ mục: {embed_count} tài liệu")

        self.status_label.setText(
            f"Đã cập nhật chỉ mục thành công cho {success_count} tài liệu."
        )
        self.status_label.setStyleSheet("color: #2E7D32; padding: 4px 0;")

    def on_reindex_error(self, error_msg):
        """Xử lý lỗi reindex."""
        self.reindex_btn.setEnabled(True)
        self.search_btn.setEnabled(True)
        self.reindex_btn.setText("Cập nhật chỉ mục")
        self.status_label.setText(f"Lỗi cập nhật chỉ mục: {error_msg}")
        self.status_label.setStyleSheet("color: #F44336; padding: 4px 0;")

    def preview_document(self, doc_id):
        """Chọn tài liệu và đóng dialog."""
        self.document_selected.emit(doc_id, [])
        self.accept()
