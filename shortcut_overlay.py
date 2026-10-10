"""One searchable overlay for the command palette and F1 shortcut help."""
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ui_commands import COMMANDS


class ShortcutOverlay(QFrame):
    def __init__(self, parent=None, on_command_activated=None, **legacy):
        super().__init__(parent)
        self._parent = parent
        self._on_activated = on_command_activated or legacy.get("on_shortcut_activated")
        self._mode = "help"
        self._visible_commands = []
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setFixedSize(590, 450)
        self.setObjectName("commandOverlay")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)
        header = QHBoxLayout()
        self._title = QLabel("Phím tắt")
        self._title.setProperty("role", "sectionTitle")
        header.addWidget(self._title, 1)
        close = QPushButton("×")
        close.setFixedSize(28, 28)
        close.setProperty("variant", "ghost")
        close.clicked.connect(self.close)
        header.addWidget(close)
        root.addLayout(header)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Tìm tác vụ hoặc phím tắt…")
        self._search.textChanged.connect(self._apply_filter)
        self._search.returnPressed.connect(self._activate_current)
        root.addWidget(self._search)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Phím", "Tác vụ", "Nhóm"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        table_header = self._table.horizontalHeader()
        table_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table_header.setSectionResizeMode(1, QHeaderView.Stretch)
        table_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.itemDoubleClicked.connect(lambda _item: self._activate_current())
        root.addWidget(self._table, 1)

        self._hint = QLabel("Enter: thực hiện · Esc: đóng · ↑↓: chọn")
        self._hint.setProperty("role", "muted")
        root.addWidget(self._hint)
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)

    def show_help(self, filter_text=""):
        self._mode = "help"
        self._title.setText("Phím tắt")
        self._hint.setText("Esc: đóng · Ctrl+K: mở bảng tác vụ")
        self._show(filter_text)

    def show_commands(self, filter_text=""):
        self._mode = "commands"
        self._title.setText("Tác vụ nhanh")
        self._hint.setText("Enter: thực hiện · Esc: đóng · ↑↓: chọn")
        self._show(filter_text)

    def show_with_filter(self, filter_text=""):
        self.show_help(filter_text)

    def _show(self, filter_text):
        if self._parent:
            origin = self._parent.mapToGlobal(self._parent.rect().topLeft())
            self.move(
                origin.x() + (self._parent.width() - self.width()) // 2,
                origin.y() + max(36, (self._parent.height() - self.height()) // 3),
            )
        self._search.setText(filter_text)
        self._apply_filter(filter_text)
        self._search.setFocus()
        self._opacity.setOpacity(0)
        self.show()
        self.raise_()
        self.activateWindow()
        self._fade(0)

    def _apply_filter(self, text):
        needle = (text or "").strip().casefold()
        source = [item for item in COMMANDS if item.shortcut or self._mode == "commands"]
        self._visible_commands = [
            item for item in source
            if not needle or needle in " ".join(
                (item.shortcut, item.label, item.group)
            ).casefold()
        ]
        self._table.setRowCount(len(self._visible_commands))
        for row, spec in enumerate(self._visible_commands):
            key = QTableWidgetItem(spec.shortcut or "—")
            key.setData(Qt.UserRole, spec.id)
            self._table.setItem(row, 0, key)
            self._table.setItem(row, 1, QTableWidgetItem(spec.label))
            self._table.setItem(row, 2, QTableWidgetItem(spec.group))
        if self._visible_commands:
            self._table.selectRow(0)

    def _activate_current(self):
        row = self._table.currentRow()
        if row < 0 or not self._on_activated:
            return
        item = self._table.item(row, 0)
        self._on_activated(item.data(Qt.UserRole))
        self.close()

    def _fade(self, value):
        self._opacity.setOpacity(min(1, value))
        if value < 1:
            QTimer.singleShot(16, lambda: self._fade(value + .16))

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() == Qt.Key_Down:
            self._table.selectRow(min(self._table.rowCount() - 1, self._table.currentRow() + 1))
        elif event.key() == Qt.Key_Up:
            self._table.selectRow(max(0, self._table.currentRow() - 1))
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._activate_current()
        else:
            super().keyPressEvent(event)
