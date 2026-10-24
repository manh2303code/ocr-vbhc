"""Statistics dialog: counts of the store by type, urgency and month.

2026-10 minimal redesign: one sheet with text tabs; the total is a figure,
not a bold sentence; the tables are hairline lists without a row-number
column; urgency levels are written in their NĐ30 token colour instead of
filled cells (the empty mark and "Không" are one level, "Thường"). Styled by
the "analytics" area of the theme stylesheet (objectNames statistics*); the
urgency colours come from theme_tokens and follow a live theme switch.
"""
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
                             QTableWidgetItem, QLabel, QPushButton,
                             QHeaderView, QTabWidget, QWidget, QFileDialog, QMessageBox,
                             QFrame)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
import pandas as pd
import logging

from analytics_queries import URGENCY_ORDER, urgency_label
from theme_manager_v2 import ThemeManager
from theme_tokens import colors_for

logger = logging.getLogger("OCRApp")

# NĐ30 urgency level -> the token that colours it (as in "Thông tin tệp").
_URGENCY_TOKENS = {
    "Hỏa tốc": "urgency_hoa_toc",
    "Thượng khẩn": "urgency_thuong_khan",
    "Khẩn": "urgency_khan",
}


def _table(headers, numeric_column=None):
    table = QTableWidget()
    table.setObjectName("statisticsTable")
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(26)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setFrameShape(QFrame.NoFrame)
    table.setWordWrap(False)
    header = table.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    header.setHighlightSections(False)
    if numeric_column is not None:
        table.horizontalHeaderItem(numeric_column).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return table


def _number(value):
    item = QTableWidgetItem(str(value))
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return item


class StatisticsDialog(QDialog):
    """Dialog hiển thị thống kê"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._theme = ThemeManager.instance().current
        self.setup_ui()
        self.load_statistics()
        ThemeManager.instance().theme_changed.connect(self.apply_theme)

    def setup_ui(self):
        self.setObjectName("statisticsDialog")
        self.setWindowTitle("Thống kê kho văn bản")
        self.setMinimumWidth(800)
        self.setMinimumHeight(600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("statisticsTabs")

        # Overview: the total, then the two breakdowns side by side.
        overview_tab = QWidget()
        overview_tab.setObjectName("statisticsPage")
        overview_layout = QVBoxLayout(overview_tab)
        overview_layout.setContentsMargins(20, 16, 20, 16)
        overview_layout.setSpacing(16)

        total_box = QVBoxLayout()
        total_box.setSpacing(0)
        total_caption = QLabel("Tổng số văn bản")
        total_caption.setObjectName("stat_title")
        total_box.addWidget(total_caption)
        self.total_label = QLabel("0")
        self.total_label.setObjectName("stat_value")
        total_box.addWidget(self.total_label)
        overview_layout.addLayout(total_box)

        columns = QHBoxLayout()
        columns.setSpacing(24)
        self.type_table = _table(["Loại văn bản", "Số lượng"], numeric_column=1)
        self.type_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.type_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        columns.addLayout(self._section("Theo loại văn bản", self.type_table), 1)
        self.urgency_table = _table(["Độ khẩn", "Số lượng"], numeric_column=1)
        self.urgency_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.urgency_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.urgency_table.setToolTip("Văn bản không ghi độ khẩn được tính là Thường")
        columns.addLayout(self._section("Theo độ khẩn", self.urgency_table), 1)
        overview_layout.addLayout(columns, 1)
        self.tab_widget.addTab(overview_tab, "Tổng quan")

        self.recent_table = _table(["ID", "Tên tệp", "Thêm vào kho", "Số hiệu"])
        self.recent_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.recent_table.setColumnWidth(0, 56)
        self.recent_table.setColumnWidth(2, 140)
        self.recent_table.setColumnWidth(3, 150)
        self.tab_widget.addTab(self.recent_table, "Văn bản mới nhất")

        self.time_table = _table(["Tháng", "Số lượng"], numeric_column=1)
        self.time_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.time_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.time_table.setToolTip("Số văn bản thêm vào kho mỗi tháng, mười hai tháng gần nhất")
        self.tab_widget.addTab(self.time_table, "Theo tháng")

        layout.addWidget(self.tab_widget, 1)

        footer = QFrame()
        footer.setObjectName("statisticsFooter")
        button_box = QHBoxLayout(footer)
        button_box.setContentsMargins(16, 10, 16, 12)
        button_box.setSpacing(8)

        self.export_btn = QPushButton("Xuất Excel")
        self.export_btn.setProperty("variant", "primary")
        self.export_btn.setToolTip("Lưu các bảng thống kê ra một tệp Excel")
        self.export_btn.clicked.connect(self.export_statistics)

        self.close_btn = QPushButton("Đóng")
        self.close_btn.clicked.connect(self.close)

        button_box.addStretch()
        button_box.addWidget(self.close_btn)
        button_box.addWidget(self.export_btn)
        layout.addWidget(footer)

    @staticmethod
    def _section(title, table):
        box = QVBoxLayout()
        box.setSpacing(6)
        label = QLabel(title)
        label.setProperty("role", "statisticsSection")
        box.addWidget(label)
        box.addWidget(table, 1)
        return box

    def apply_theme(self, name):
        """Recolour the urgency levels; the rest follows the stylesheet."""
        self._theme = "dark" if name == "dark" else "light"
        self._colour_urgency()

    def _colour_urgency(self):
        tokens = colors_for(self._theme)
        for row in range(self.urgency_table.rowCount()):
            item = self.urgency_table.item(row, 0)
            token = _URGENCY_TOKENS.get(item.text()) if item else None
            if token:
                item.setForeground(QColor(tokens[token]))
                font = QFont(item.font())
                font.setWeight(QFont.DemiBold)
                item.setFont(font)

    def load_statistics(self):
        """Load thống kê từ database"""
        stats = self.db.get_statistics()
        if not stats:
            return

        self.total_label.setText(str(stats['total_documents']))

        self.type_table.setRowCount(len(stats['by_type']))
        for i, (type_name, count) in enumerate(stats['by_type']):
            self.type_table.setItem(i, 0, QTableWidgetItem(type_name or "Không xác định"))
            self.type_table.setItem(i, 1, _number(count))

        # One row per level, most urgent first; empty and "Không" are Thường.
        levels = {}
        for urgency, count in stats['by_urgency']:
            name = urgency_label(urgency)
            levels[name] = levels.get(name, 0) + count
        order = [name for name in URGENCY_ORDER if name in levels]
        order += [name for name in levels if name not in URGENCY_ORDER]
        self.urgency_table.setRowCount(len(order))
        for i, name in enumerate(order):
            self.urgency_table.setItem(i, 0, QTableWidgetItem(name))
            self.urgency_table.setItem(i, 1, _number(levels[name]))
        self._colour_urgency()

        self.recent_table.setRowCount(len(stats['recent_docs']))
        for i, (doc_id, filename, created_at, so_kh) in enumerate(stats['recent_docs']):
            self.recent_table.setItem(i, 0, QTableWidgetItem(str(doc_id)))
            name = QTableWidgetItem(filename)
            name.setToolTip(filename or "")
            self.recent_table.setItem(i, 1, name)
            self.recent_table.setItem(i, 2, QTableWidgetItem(self._added(created_at)))
            self.recent_table.setItem(i, 3, QTableWidgetItem(so_kh))

        self.time_table.setRowCount(len(stats['by_month']))
        for i, (month, count) in enumerate(stats['by_month']):
            self.time_table.setItem(i, 0, QTableWidgetItem(self._month(month)))
            self.time_table.setItem(i, 1, _number(count))

    @staticmethod
    def _added(value):
        """'2026-10-15 09:55:02' as 15/10/2026 09:55, like the rest of the app."""
        text = str(value or "")
        if len(text) >= 16 and text[4] == "-" and text[7] == "-":
            return "{}/{}/{} {}".format(text[8:10], text[5:7], text[:4], text[11:16])
        return text

    @staticmethod
    def _month(value):
        text = str(value or "")
        if len(text) == 7 and text[4] == "-":
            return "Tháng {}/{}".format(int(text[5:7]), text[:4])
        return text

    def export_statistics(self):
        """Xuất thống kê ra file Excel"""
        try:
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Xuất báo cáo", "", "Excel Files (*.xlsx)"
            )
            if not file_path:
                return

            if not file_path.endswith('.xlsx'):
                file_path += '.xlsx'

            stats = self.db.get_statistics()
            if not stats:
                return

            with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
                # Tổng quan
                overview_data = {
                    'Thông tin': ['Tổng số văn bản'],
                    'Số lượng': [stats['total_documents']]
                }
                pd.DataFrame(overview_data).to_excel(writer, sheet_name='Tổng quan', index=False)

                # Thống kê theo loại
                type_data = pd.DataFrame(stats['by_type'], columns=['Loại văn bản', 'Số lượng'])
                type_data.to_excel(writer, sheet_name='Theo loại', index=False)

                # Thống kê theo độ khẩn
                urgency_data = pd.DataFrame(stats['by_urgency'], columns=['Độ khẩn', 'Số lượng'])
                urgency_data.to_excel(writer, sheet_name='Theo độ khẩn', index=False)

                # Thống kê theo thời gian
                time_data = pd.DataFrame(stats['by_month'], columns=['Tháng', 'Số lượng'])
                time_data.to_excel(writer, sheet_name='Theo thời gian', index=False)

                # Văn bản mới nhất
                recent_data = pd.DataFrame(
                    stats['recent_docs'],
                    columns=['ID', 'Tên file', 'Ngày tạo', 'Số ký hiệu']
                )
                recent_data.to_excel(writer, sheet_name='Văn bản mới', index=False)

            QMessageBox.information(self, "Thành công", f"Đã xuất báo cáo thống kê tới {file_path}")

        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Lỗi khi xuất báo cáo: {str(e)}")
