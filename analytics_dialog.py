"""
Analytics dialog: the store at a glance and a filterable table of documents.

2026-10 minimal redesign (docs/design/minimal-ui-2026-10.md). One sheet like
the workspace: a header row with the title and two icon actions, the filters
on the left, the table and the charts in tabs on the right, a status line at
the bottom, all separated by hairlines. The four counts are figures, not
coloured cards; the charts are flat, olive on the surface, values in the
text colours, exact numbers one hover away.

Colours: the dialog itself names none. Its widgets are styled by the
"analytics" area of devtools/theme_template.qss (objectNames analytics*);
the charts take theme_manager_v2.matplotlib_rc(theme) and the theme tokens.
The stylesheet is now generated from the tokens for both themes, so the old
workarounds (a dialog stylesheet re-declaring every widget, explicit
palettes on every child to beat the global QLabel colour) are gone; the
dialog only redraws its charts and recolours its icons on a theme change.
"""
import html
import textwrap
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from analytics_queries import URGENCY_ORDER, fetch_latest_documents, urgency_label

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QPushButton, QLineEdit, QDateEdit, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QFormLayout, QScrollArea, QFrame, QSplitter, QMessageBox,
    QSizePolicy, QGridLayout, QToolButton, QToolTip,
)
from PyQt5.QtCore import Qt, QDate, QSize
from PyQt5.QtGui import QColor, QCursor, QFont, QPainter

from theme_manager_v2 import ThemeManager, matplotlib_rc
from theme_tokens import colors_for
from ui_icons import icon

# The bundled UI typeface, so the charts read like the rest of the window.
_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
_CHART_FONTS = ["Be Vietnam Pro", "Segoe UI", "DejaVu Sans"]

try:
    import matplotlib
    matplotlib.use('Qt5Agg')
    from matplotlib import font_manager
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    from matplotlib.ticker import MaxNLocator
    HAS_MATPLOTLIB = True

    for _name in ("BeVietnamPro-Regular.ttf", "BeVietnamPro-Medium.ttf",
                  "BeVietnamPro-SemiBold.ttf"):
        try:
            font_manager.fontManager.addfont(str(_FONT_DIR / _name))
        except Exception:
            _CHART_FONTS = [f for f in _CHART_FONTS if f != "Be Vietnam Pro"]

    class MplCanvas(FigureCanvas):
        """A matplotlib figure in the dialog: fills its cell, lays its labels
        out by itself (constrained layout), and shows the exact value of the
        slot under the pointer as a tooltip. The hit area of a slot is its
        whole band, not only the bar."""

        def __init__(self, parent=None, width=8, height=3, dpi=100):
            self.fig = Figure(figsize=(width, height), dpi=dpi, layout="constrained")
            self.axes = self.fig.add_subplot(111)
            super().__init__(self.fig)
            self.setParent(parent)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setMinimumHeight(200)
            self._hover_axis = None
            self._hover = {}
            self.mpl_connect("motion_notify_event", self._on_motion)
            self.mpl_connect("figure_leave_event", lambda _event: QToolTip.hideText())

        def set_hover(self, axis, texts):
            """texts: slot index -> tooltip, along the x or y axis."""
            self._hover_axis, self._hover = axis, dict(texts)

        def hover_text(self, slot):
            return self._hover.get(slot, "")

        def _on_motion(self, event):
            if event.inaxes is not self.axes or self._hover_axis is None:
                QToolTip.hideText()
                return
            value = event.xdata if self._hover_axis == "x" else event.ydata
            text = self._hover.get(int(round(value))) if value is not None else None
            if text:
                QToolTip.showText(QCursor.pos(), text, self)
            else:
                QToolTip.hideText()
except ImportError:
    HAS_MATPLOTLIB = False
    MplCanvas = None  # guard for `if HAS_MATPLOTLIB:` callers


def chart_rc(theme):
    """matplotlib_rc for the theme, set in the UI typeface."""
    rc = matplotlib_rc(theme)
    rc["font.family"] = list(_CHART_FONTS)
    return rc


def _tip(text):
    """A tooltip that wraps: Qt wraps rich text, never a plain long line."""
    text = (text or "").strip()
    if not text:
        return ""
    return "<p>{}</p>".format(html.escape(text, quote=False).replace("\n", "<br>"))


def _month_slots(monthly, today):
    """The twelve months up to this month as (year, month), oldest first, or
    the twelve up to the newest month with documents when all are older.
    Months without documents stay in the row, as zero."""
    keys = []
    for key in monthly:
        try:
            keys.append((int(key[:4]), int(key[5:7])))
        except (TypeError, ValueError):
            continue
    end = (today.year, today.month)
    if keys:
        newest = max(keys)
        if newest > end or (end[0] * 12 + end[1]) - (newest[0] * 12 + newest[1]) >= 12:
            end = newest
    slots = []
    year, month = end
    for _ in range(12):
        slots.append((year, month))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return list(reversed(slots))


class SimpleBarWidget(QWidget):
    """Fallback bar chart when matplotlib is not available: one olive series
    on the surface, values and labels in the text colours."""
    LABEL_AREA = 40   # vertical space reserved below bars for x-labels
    TOP_PADDING = 18  # space above tallest bar for value text

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = []
        self.dark = False
        self.setMinimumHeight(200)

    def set_data(self, data):
        self.data = data
        self.update()

    def set_theme(self, name):
        self.dark = name == "dark"
        self.update()

    def paintEvent(self, event):
        if not self.data:
            return
        tokens = colors_for("dark" if self.dark else "light")
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width = self.width()
        plot_height = self.height() - self.LABEL_AREA - self.TOP_PADDING
        max_val = max(v for _, v in self.data) or 1
        slot = (width - 40) / len(self.data)
        bar_width = min(24, max(6, slot * 0.4))
        baseline = self.TOP_PADDING + plot_height
        painter.setPen(QColor(tokens["border"]))
        painter.drawLine(20, int(baseline), int(width - 20), int(baseline))
        for i, (label, value) in enumerate(self.data):
            x = 20 + i * slot + (slot - bar_width) / 2
            bar_height = (value / max_val) * plot_height
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(tokens["primary_muted"]))
            painter.drawRect(int(x), int(baseline - bar_height), int(bar_width), int(bar_height))
            painter.setPen(QColor(tokens["text_secondary"]))
            painter.drawText(int(x - slot / 2), int(baseline - bar_height - 16),
                             int(slot + bar_width), 14, Qt.AlignHCenter, str(value))
            painter.setPen(QColor(tokens["text_muted"]))
            text = painter.fontMetrics().elidedText(str(label), Qt.ElideRight, int(slot - 4))
            painter.drawText(int(20 + i * slot), int(baseline + 6), int(slot), 16,
                             Qt.AlignHCenter | Qt.AlignTop, text)
        painter.end()


class AnalyticsDialog(QDialog):
    """Phân tích kho văn bản: counts, charts and a filterable table."""

    def __init__(self, db, parent=None, initial_tab=None):
        super().__init__(parent)
        self.db = db
        self.initial_tab = initial_tab
        self.search_results = []
        self.all_documents = []
        self._charts_drawn = False
        self._theme_manager = ThemeManager.instance()
        self._theme_name = self._theme_manager.current
        self.setup_ui()
        self._theme_manager.register_widget(self)
        self.apply_theme(self._theme_manager.current)
        self.load_data()

    def setup_ui(self):
        self.setWindowTitle("Phân tích kho văn bản")
        self.setObjectName("analyticsDialog")
        self.setFont(QFont("Be Vietnam Pro", 9))
        self.setMinimumSize(1100, 700)
        self.resize(1360, 820)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header: the title, then refresh and export as icon buttons.
        header = QFrame()
        header.setObjectName("analyticsHeader")
        header_bar = QHBoxLayout(header)
        header_bar.setContentsMargins(16, 6, 10, 6)
        header_bar.setSpacing(2)
        section_title = QLabel("Phân tích kho văn bản")
        section_title.setObjectName("analyticsSectionTitle")
        self._section_title = section_title
        header_bar.addWidget(section_title)
        header_bar.addStretch()
        self._header_refresh = self._tool_button(
            "analyticsRefreshButton", "Làm mới\nĐọc lại số liệu từ kho văn bản")
        self._header_refresh.clicked.connect(self.load_data)
        header_bar.addWidget(self._header_refresh)
        self._header_export = self._tool_button(
            "analyticsExportButton", "Xuất bảng kết quả\nLưu các văn bản đang hiện ra tệp CSV, mở được bằng Excel")
        self._header_export.clicked.connect(self.export_report)
        header_bar.addWidget(self._header_export)
        main_layout.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("analyticsSplitter")
        splitter.setHandleWidth(1)
        self._splitter = splitter
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.create_search_panel())
        splitter.addWidget(self.create_results_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 1060])
        main_layout.addWidget(splitter, 1)

        # Footer: what the table shows, and the way out.
        footer = QFrame()
        footer.setObjectName("analyticsFooter")
        footer_bar = QHBoxLayout(footer)
        footer_bar.setContentsMargins(16, 8, 12, 8)
        footer_bar.setSpacing(8)
        self.status_label = QLabel("Sẵn sàng")
        self.status_label.setObjectName("analyticsStatus")
        footer_bar.addWidget(self.status_label)
        footer_bar.addStretch()
        close_btn = QPushButton("Đóng")
        close_btn.setObjectName("analyticsCloseButton")
        close_btn.clicked.connect(self.close)
        footer_bar.addWidget(close_btn)
        main_layout.addWidget(footer)

    def _tool_button(self, name, tooltip):
        button = QToolButton()
        button.setObjectName(name)
        button.setAutoRaise(True)
        button.setIconSize(QSize(18, 18))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip.split("\n", 1)[0])
        button.setCursor(Qt.PointingHandCursor)
        return button

    def apply_theme(self, name):
        """Follow a theme switch: the stylesheet already changed with the
        application; recolour the icons and redraw the charts."""
        self._theme_name = "dark" if name == "dark" else "light"
        tokens = colors_for(self._theme_name)
        for button, glyph in ((self._header_refresh, "refresh-cw"),
                              (self._header_export, "excel")):
            button.setIcon(icon(glyph, size=18, color=tokens["text_secondary"]))
        for chart in self.findChildren(SimpleBarWidget):
            chart.set_theme(self._theme_name)
        if self._charts_drawn:
            self.update_charts()

    def create_search_panel(self):
        """The left panel: four counts, then the three ways to filter.

        Wrapped in a QScrollArea so every filter stays reachable in a short
        window.
        """
        content = QWidget()
        content.setObjectName("analyticsSearchContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        # Counts by the day a document entered the store (documents.created_at).
        stats = QWidget()
        stats_layout = QGridLayout(stats)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setHorizontalSpacing(16)
        stats_layout.setVerticalSpacing(10)
        self.total_docs_card = self.create_stat_card(
            "Tổng số", "0", "Mọi văn bản trong kho")
        self.today_docs_card = self.create_stat_card(
            "Hôm nay", "0", "Văn bản thêm vào kho hôm nay")
        self.week_docs_card = self.create_stat_card(
            "7 ngày qua", "0", "Văn bản thêm vào kho trong 7 ngày qua")
        self.month_docs_card = self.create_stat_card(
            "30 ngày qua", "0", "Văn bản thêm vào kho trong 30 ngày qua")
        stats_layout.addWidget(self.total_docs_card, 0, 0)
        stats_layout.addWidget(self.today_docs_card, 0, 1)
        stats_layout.addWidget(self.week_docs_card, 1, 0)
        stats_layout.addWidget(self.month_docs_card, 1, 1)
        layout.addWidget(stats)
        layout.addSpacing(6)

        # By the day a document entered the store.
        date_group = QGroupBox("Ngày thêm vào kho")
        date_group.setToolTip("Lọc theo ngày văn bản được thêm vào kho, không phải ngày ban hành")
        date_layout = QFormLayout(date_group)
        self._compact_form(date_layout)
        self.date_from = QDateEdit()
        self.date_from.setDate(QDate.currentDate().addMonths(-1))
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("dd/MM/yyyy")
        date_layout.addRow(self._field_label("Từ"), self.date_from)
        self.date_to = QDateEdit()
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("dd/MM/yyyy")
        date_layout.addRow(self._field_label("Đến"), self.date_to)
        date_search_btn = QPushButton("Lọc theo ngày")
        date_search_btn.clicked.connect(self.search_by_date)
        date_layout.addRow(self._right(date_search_btn))
        layout.addWidget(date_group)

        number_group = QGroupBox("Số hiệu")
        number_layout = QHBoxLayout(number_group)
        number_layout.setContentsMargins(0, 2, 0, 0)
        number_layout.setSpacing(6)
        self.number_input = QLineEdit()
        self.number_input.setPlaceholderText("Ví dụ 336/KH")
        self.number_input.setToolTip("Một phần số hiệu là đủ\nEnter để tìm")
        self.number_input.setClearButtonEnabled(True)
        self.number_input.returnPressed.connect(self.search_by_number)
        number_layout.addWidget(self.number_input, 1)
        number_search_btn = QPushButton("Tìm")
        number_search_btn.clicked.connect(self.search_by_number)
        number_layout.addWidget(number_search_btn)
        layout.addWidget(number_group)

        content_group = QGroupBox("Nội dung")
        content_layout = QFormLayout(content_group)
        self._compact_form(content_layout)
        self.content_input = QLineEdit()
        self.content_input.setPlaceholderText("Từ khóa")
        self.content_input.setToolTip(
            "Tìm trong nội dung chính, số hiệu và cơ quan ban hành\nEnter để tìm")
        self.content_input.setClearButtonEnabled(True)
        self.content_input.returnPressed.connect(self.search_by_content)
        content_layout.addRow(self.content_input)
        self.doc_type_filter = QComboBox()
        self.doc_type_filter.addItem("Tất cả", "")
        self.doc_type_filter.addItem("Báo cáo", "báo cáo")
        self.doc_type_filter.addItem("Công văn", "công văn")
        self.doc_type_filter.addItem("Quyết định", "quyết định")
        self.doc_type_filter.addItem("Thông báo", "thông báo")
        self.doc_type_filter.addItem("Tờ trình", "tờ trình")
        content_layout.addRow(self._field_label("Loại"), self.doc_type_filter)
        self.org_filter = QLineEdit()
        self.org_filter.setPlaceholderText("Cơ quan ban hành")
        self.org_filter.setClearButtonEnabled(True)
        self.org_filter.returnPressed.connect(self.search_by_content)
        content_layout.addRow(self._field_label("Cơ quan"), self.org_filter)
        content_search_btn = QPushButton("Tìm")
        content_search_btn.setObjectName("content_search_btn")
        content_search_btn.clicked.connect(self.search_by_content)
        content_layout.addRow(self._right(content_search_btn))
        layout.addWidget(content_group)
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setObjectName("analyticsSearchScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)

        outer = QWidget()
        outer.setObjectName("analyticsSearchPanel")
        outer.setMinimumWidth(280)
        outer.setMaximumWidth(340)
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroll)
        return outer

    @staticmethod
    def _compact_form(form):
        form.setContentsMargins(0, 2, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

    @staticmethod
    def _field_label(text):
        label = QLabel(text)
        label.setProperty("role", "analyticsFieldLabel")
        label.setMinimumWidth(44)
        return label

    @staticmethod
    def _right(button):
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 2, 0, 0)
        box.addStretch()
        box.addWidget(button)
        return row

    def create_results_panel(self):
        """The right panel: the table and the charts, as tabs."""
        panel = QWidget()
        panel.setObjectName("analyticsResultsPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.result_tabs = QTabWidget()
        self.result_tabs.setObjectName("analyticsTabs")

        # How many documents the table shows, and the way back to all of them.
        corner = QWidget()
        corner.setObjectName("analyticsTabCorner")
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 0, 10, 0)
        corner_layout.setSpacing(6)
        self.results_count_label = QLabel("0 văn bản")
        self.results_count_label.setObjectName("analyticsResultsCount")
        corner_layout.addWidget(self.results_count_label)
        show_all_btn = QPushButton("Hiện tất cả")
        show_all_btn.setObjectName("show_all_btn")
        show_all_btn.setProperty("variant", "ghost")
        show_all_btn.setToolTip("Bỏ bộ lọc, hiện mọi văn bản trong kho")
        show_all_btn.setCursor(Qt.PointingHandCursor)
        show_all_btn.clicked.connect(self.show_all_documents)
        self._show_all_btn = show_all_btn
        corner_layout.addWidget(show_all_btn)
        self.result_tabs.setCornerWidget(corner, Qt.TopRightCorner)

        self.results_table = self.create_documents_table()
        self.result_tabs.addTab(self.results_table, "Văn bản")
        self.result_tabs.addTab(self.create_charts_tab(), "Biểu đồ")
        self.result_tabs.currentChanged.connect(
            lambda index: corner.setVisible(index == 0))
        layout.addWidget(self.result_tabs)
        return panel

    def create_charts_tab(self):
        """By month across the top; urgency and issuing bodies below."""
        widget = QWidget()
        widget.setObjectName("analyticsCharts")
        grid = QGridLayout(widget)
        grid.setContentsMargins(20, 14, 20, 14)
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(18)

        def block(title, tooltip, attr_canvas, attr_simple):
            box = QWidget()
            box.setObjectName("analyticsChartBlock")
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.setSpacing(4)
            label = QLabel(title)
            label.setProperty("role", "analyticsChartTitle")
            label.setToolTip(tooltip)
            box_layout.addWidget(label)
            if HAS_MATPLOTLIB:
                chart = MplCanvas(box, width=6, height=2.6)
                setattr(self, attr_canvas, chart)
            else:
                chart = SimpleBarWidget()
                setattr(self, attr_simple, chart)
            box_layout.addWidget(chart, 1)
            return box

        grid.addWidget(block(
            "Văn bản thêm vào kho theo tháng",
            "Mười hai tháng gần nhất, theo ngày văn bản được thêm vào kho",
            "monthly_canvas", "monthly_chart"), 0, 0, 1, 2)
        grid.addWidget(block(
            "Độ khẩn", "Độ khẩn theo Nghị định 30; văn bản không ghi độ khẩn là Thường",
            "doc_type_canvas", "doc_type_chart"), 1, 0)
        grid.addWidget(block(
            "Cơ quan ban hành nhiều văn bản nhất", "Tám cơ quan có nhiều văn bản nhất trong kho",
            "org_canvas", "org_chart"), 1, 1)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 3)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)

        scroll = QScrollArea()
        scroll.setObjectName("analyticsChartsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        widget.setMinimumHeight(480)
        scroll.setWidget(widget)
        return scroll

    def create_stat_card(self, title, value, tooltip=""):
        """A count: a small muted label over the figure. The figure label
        keeps the objectName stat_value (update_statistics finds it)."""
        card = QFrame()
        card.setObjectName("stat_card")
        card.setToolTip(tooltip)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        title_label = QLabel(title)
        title_label.setObjectName("stat_title")
        layout.addWidget(title_label)
        value_label = QLabel(value)
        value_label.setObjectName("stat_value")
        value_label.setWordWrap(False)
        layout.addWidget(value_label)
        return card

    def create_documents_table(self):
        """The documents, one row each. Columns can be dragged wider; the
        main content absorbs the rest of the width. A cell too narrow for
        its text ends in "…" and shows the whole text as its tooltip."""
        table = QTableWidget()
        table.setObjectName("analyticsTable")
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels([
            "ID", "Số hiệu", "Ngày ban hành", "Loại văn bản",
            "Cơ quan ban hành", "Nội dung chính", "Tệp"
        ])
        header = table.horizontalHeader()
        for column in range(7):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.setColumnWidth(0, 48)
        table.setColumnWidth(1, 140)
        table.setColumnWidth(2, 112)
        table.setColumnWidth(3, 104)
        table.setColumnWidth(4, 180)
        table.setColumnWidth(6, 150)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(40)
        header.setHighlightSections(False)
        table.verticalHeader().setDefaultSectionSize(25)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.ElideRight)
        table.setShowGrid(False)
        table.setFrameShape(QFrame.NoFrame)
        return table

    def load_data(self):
        """Tải dữ liệu từ database"""
        try:
            if not self.db:
                self.status_label.setText("Không có kết nối cơ sở dữ liệu")
                self.update_charts()  # empty, but in the theme
                return
            self.all_documents = self.get_all_documents()
            self.update_statistics()
            self.update_charts()
            self.show_all_documents()
            self.status_label.setText(f"Đã tải {len(self.all_documents)} văn bản")
        except Exception as e:
            self.status_label.setText(f"Lỗi: {str(e)}")
            print(f"Error loading data: {e}")

    def get_all_documents(self):
        """Lấy tất cả văn bản"""
        try:
            with self.db.conn_pool.get_connection() as conn:
                return fetch_latest_documents(conn)
        except Exception as e:
            print(f"Error: {e}")
            return []

    def update_statistics(self):
        """Cập nhật thống kê"""
        total = len(self.all_documents)
        today = datetime.now().date()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)
        today_count = week_count = month_count = 0
        for doc in self.all_documents:
            try:
                created = doc[3]
                if created:
                    if isinstance(created, str):
                        doc_date = datetime.strptime(created[:10], "%Y-%m-%d").date()
                    else:
                        doc_date = created.date() if hasattr(created, 'date') else created
                    if doc_date == today:
                        today_count += 1
                    if doc_date >= week_ago:
                        week_count += 1
                    if doc_date >= month_ago:
                        month_count += 1
            except Exception:
                pass
        self.total_docs_card.findChild(QLabel, "stat_value").setText(str(total))
        self.today_docs_card.findChild(QLabel, "stat_value").setText(str(today_count))
        self.week_docs_card.findChild(QLabel, "stat_value").setText(str(week_count))
        self.month_docs_card.findChild(QLabel, "stat_value").setText(str(month_count))

    def update_charts(self):
        """Count the loaded documents for the three charts: urgency (NĐ30
        level, an empty mark is Thường), issuing body, month added."""
        urgency = Counter()
        orgs = Counter()
        monthly = Counter()
        for doc in self.all_documents:
            urgency[urgency_label(doc[9])] += 1
            orgs[(doc[7] or "").strip() or "Không xác định"] += 1
            try:
                created = doc[3]
                if created:
                    month_key = created[:7] if isinstance(created, str) else created.strftime("%Y-%m")
                    monthly[month_key] += 1
            except Exception:
                pass
        if HAS_MATPLOTLIB:
            self.update_matplotlib_charts(urgency, orgs, monthly)
        else:
            self.update_simple_charts(urgency, orgs, monthly)
        self._charts_drawn = True

    # ── Charts ────────────────────────────────────────────────
    def _chart_tokens(self):
        return colors_for(self._theme_name)

    def _start_chart(self, canvas):
        """A fresh axes in the current theme. Called inside chart_rc: new
        axes take their face, spines and ticks from the rc, which
        axes.clear() on the old ones would not."""
        canvas.fig.clear()
        canvas.fig.set_facecolor(self._chart_tokens()["surface"])
        canvas.axes = canvas.fig.add_subplot(111)
        canvas.set_hover(None, {})

    def _empty_chart(self, canvas):
        t = self._chart_tokens()
        canvas.axes.set_axis_off()
        canvas.axes.text(0.5, 0.5, "Chưa có văn bản", ha="center", va="center",
                         color=t["text_muted"], fontsize=9, transform=canvas.axes.transAxes)

    def update_matplotlib_charts(self, urgency, orgs, monthly):
        """Draw the three charts in the theme: flat olive bars on the
        surface, hairline baseline, labels in the text colours, a value at
        the end of each bar or on the extreme columns only."""
        with matplotlib.rc_context(chart_rc(self._theme_name)):
            self._draw_monthly(monthly)
            self._draw_urgency(urgency)
            self._draw_orgs(orgs)

    def _draw_monthly(self, monthly):
        canvas, t = self.monthly_canvas, self._chart_tokens()
        self._start_chart(canvas)
        ax = canvas.axes
        slots = _month_slots(monthly, datetime.now().date())
        values = [monthly.get("{:04d}-{:02d}".format(y, m), 0) for y, m in slots]
        if not any(values):
            self._empty_chart(canvas)
            canvas.draw_idle()
            return
        x = list(range(len(slots)))
        ax.bar(x, values, width=0.3, color=t["primary_muted"], zorder=2)
        labels = []
        for index, (year, month) in enumerate(slots):
            first = index == 0 or month == 1
            labels.append("T{}\n{}".format(month, year) if first else "T{}".format(month))
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_xlim(-0.6, len(slots) - 0.4)
        top = max(values)
        ax.set_ylim(0, top * 1.2)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
        ax.grid(axis="y")
        ax.spines["left"].set_visible(False)
        # Values on the extremes only: the highest month and the latest one.
        peak = values.index(top)
        latest = max(i for i, v in enumerate(values) if v)
        for index in sorted({peak, latest}):
            ax.annotate(str(values[index]), (index, values[index]), xytext=(0, 3),
                        textcoords="offset points", ha="center", va="bottom",
                        color=t["text_secondary"], fontsize=8.5)
        canvas.set_hover("x", {
            index: "Tháng {}/{}: {} văn bản".format(month, year, values[index])
            for index, (year, month) in enumerate(slots)
        })
        canvas.draw_idle()

    def _draw_bars(self, canvas, names, values, colours, hover, slots=4, thickness=0.5):
        """Horizontal bars, the first name on top, the value at each tip.
        At least `slots` rows of room, so a short list keeps thin bars."""
        t = self._chart_tokens()
        ax = canvas.axes
        y = list(range(len(names)))
        ax.barh(y, values, height=thickness, color=colours, zorder=2)
        ax.set_yticks(y)
        ax.set_yticklabels(names)
        ax.set_ylim(max(len(names), slots) - 0.4, -0.6)
        top = max(values) if values else 0
        ax.set_xlim(0, (top or 1) * 1.18)
        ax.xaxis.set_visible(False)
        ax.spines["bottom"].set_visible(False)
        for index, value in enumerate(values):
            ax.annotate(str(value), (value, index), xytext=(4, 0), textcoords="offset points",
                        ha="left", va="center", color=t["text_secondary"], fontsize=8.5)
        canvas.set_hover("y", dict(enumerate(hover)))
        canvas.draw_idle()

    def _draw_urgency(self, urgency):
        canvas, t = self.doc_type_canvas, self._chart_tokens()
        self._start_chart(canvas)
        if not urgency:
            self._empty_chart(canvas)
            canvas.draw_idle()
            return
        # Every NĐ30 level is listed, so "no urgent documents" is visible.
        names = list(URGENCY_ORDER) + [k for k, _ in urgency.most_common() if k not in URGENCY_ORDER]
        values = [urgency.get(name, 0) for name in names]
        level = {"Hỏa tốc": "urgency_hoa_toc", "Thượng khẩn": "urgency_thuong_khan",
                 "Khẩn": "urgency_khan", "Thường": "primary_muted"}
        colours = [t[level.get(name, "text_muted")] for name in names]
        hover = ["{}: {} văn bản".format(name, value) for name, value in zip(names, values)]
        self._draw_bars(canvas, names, values, colours, hover, thickness=0.36)

    def _draw_orgs(self, orgs):
        canvas, t = self.org_canvas, self._chart_tokens()
        self._start_chart(canvas)
        if not orgs:
            self._empty_chart(canvas)
            canvas.draw_idle()
            return
        top_orgs = orgs.most_common(8)
        # Long names wrap to two lines; the whole name is in the tooltip.
        names = []
        for name, _ in top_orgs:
            lines = textwrap.wrap(name, 26) or [name]
            if len(lines) > 2:
                lines = [lines[0], textwrap.shorten(" ".join(lines[1:]), 26, placeholder="…")]
            names.append("\n".join(lines))
        values = [v for _, v in top_orgs]
        hover = ["{}: {} văn bản".format(name, value) for name, value in top_orgs]
        self._draw_bars(canvas, names, values, [t["primary_muted"]] * len(values), hover, slots=6)

    def update_simple_charts(self, urgency, orgs, monthly):
        """Fallback when matplotlib missing — pure Qt widgets."""
        self.doc_type_chart.set_data([(k, urgency.get(k, 0)) for k in URGENCY_ORDER])
        slots = _month_slots(monthly, datetime.now().date())[-6:]
        self.monthly_chart.set_data(
            [("T{}".format(m), monthly.get("{:04d}-{:02d}".format(y, m), 0)) for y, m in slots])
        self.org_chart.set_data(orgs.most_common(6))

    # ── Table ─────────────────────────────────────────────────
    def populate_table(self, documents):
        """Fill the table. The ID column holds ints so it sorts numerically;
        every text is whole (line breaks become spaces) and a long one is
        also the cell's tooltip, so nothing is cut away for good."""
        self.results_table.setSortingEnabled(False)
        self.results_table.setRowCount(0)
        for doc in documents:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            id_item = QTableWidgetItem()
            try:
                id_item.setData(Qt.DisplayRole, int(doc[0]))
            except (ValueError, TypeError):
                id_item.setData(Qt.DisplayRole, str(doc[0]))
            self.results_table.setItem(row, 0, id_item)
            for column, value in ((1, doc[4]), (2, doc[5]), (3, doc[6]),
                                  (4, doc[7]), (5, doc[8]), (6, doc[2])):
                text = value or ""
                item = QTableWidgetItem(" ".join(text.split()))
                if len(text) > 18:
                    item.setToolTip(_tip(text))
                self.results_table.setItem(row, column, item)
        self.results_count_label.setText(f"{len(documents)} văn bản")
        self.results_table.setSortingEnabled(True)

    def show_all_documents(self):
        """Hiển thị tất cả văn bản"""
        self.populate_table(self.all_documents)
        self.result_tabs.setCurrentIndex(0)

    def search_by_date(self):
        """Tìm theo ngày"""
        try:
            date_from = self.date_from.date().toString("yyyy-MM-dd")
            date_to = self.date_to.date().toString("yyyy-MM-dd")
            with self.db.conn_pool.get_connection() as conn:
                results = fetch_latest_documents(
                    conn, date_from=date_from, date_to=date_to
                )
            self.populate_table(results)
            self.status_label.setText(
                "{} văn bản thêm vào kho từ {} đến {}".format(
                    len(results), self.date_from.date().toString("dd/MM/yyyy"),
                    self.date_to.date().toString("dd/MM/yyyy")))
            self.result_tabs.setCurrentIndex(0)
        except Exception as e:
            QMessageBox.warning(self, "Lỗi", f"Lỗi tìm kiếm: {str(e)}")

    def search_by_number(self):
        """Tìm theo số ký hiệu"""
        keyword = self.number_input.text().strip()
        if not keyword:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng nhập số ký hiệu!")
            return
        try:
            with self.db.conn_pool.get_connection() as conn:
                results = fetch_latest_documents(conn, number=keyword)
            self.populate_table(results)
            self.status_label.setText(f"{len(results)} văn bản có số hiệu chứa “{keyword}”")
            self.result_tabs.setCurrentIndex(0)
        except Exception as e:
            QMessageBox.warning(self, "Lỗi", f"Lỗi tìm kiếm: {str(e)}")

    def search_by_content(self):
        """Tìm theo nội dung"""
        keyword = self.content_input.text().strip()
        doc_type = self.doc_type_filter.currentData()
        org = self.org_filter.text().strip()
        try:
            with self.db.conn_pool.get_connection() as conn:
                results = fetch_latest_documents(
                    conn, keyword=keyword, doc_type=doc_type, org=org
                )
            self.populate_table(results)
            self.status_label.setText(f"Tìm thấy {len(results)} văn bản")
            self.result_tabs.setCurrentIndex(0)
        except Exception as e:
            QMessageBox.warning(self, "Lỗi", f"Lỗi tìm kiếm: {str(e)}")

    def export_report(self):
        """Save the rows the table shows as CSV (UTF-8 with BOM, for Excel)."""
        try:
            from PyQt5.QtWidgets import QFileDialog
            filepath, _ = QFileDialog.getSaveFileName(
                self, "Xuất bảng kết quả", "", "CSV Files (*.csv);;All Files (*)"
            )
            if filepath:
                table = self.results_table
                headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
                with open(filepath, 'w', encoding='utf-8-sig') as f:
                    f.write(",".join(headers) + "\n")
                    for row in range(table.rowCount()):
                        row_data = []
                        for col in range(table.columnCount()):
                            item = table.item(row, col)
                            text = item.text() if item else ""
                            text = text.replace('"', '""')
                            row_data.append(f'"{text}"')
                        f.write(",".join(row_data) + "\n")
                QMessageBox.information(self, "Thành công", f"Đã xuất báo cáo: {filepath}")
        except Exception as e:
            QMessageBox.warning(self, "Lỗi", f"Lỗi xuất báo cáo: {str(e)}")
