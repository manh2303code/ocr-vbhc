"""
Analytics Dashboard Dialog
Cung cấp giao diện phân tích, thống kê và tìm kiếm văn bản
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter
from analytics_queries import fetch_latest_documents

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QPushButton, QLineEdit, QDateEdit, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QFormLayout, QScrollArea, QFrame, QSplitter, QMessageBox,
    QSizePolicy, QGridLayout, QTextEdit, QProgressBar
)
from PyQt5.QtCore import Qt, QDate, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QPainter, QPen, QBrush, QPalette

from theme_manager_v2 import ThemeManager
from theme_tokens import colors_for

# Thử import matplotlib
try:
    import matplotlib
    matplotlib.use('Qt5Agg')
    # Font config — Vietnamese diacritics need Unicode-capable font. DejaVu
    # Sans ships with matplotlib; Segoe UI is Windows-default. Priority list
    # preserves rendering on both Linux (DejaVu) and Windows (Segoe UI).
    matplotlib.rcParams['font.family'] = ['DejaVu Sans', 'Segoe UI', 'Arial', 'sans-serif']
    matplotlib.rcParams['axes.unicode_minus'] = False
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    HAS_MATPLOTLIB = True

    class MplCanvas(FigureCanvas):
        """Canvas matplotlib cho PyQt5.

        Phase A 2026-04-17 + responsive fix: Expanding size policy so
        charts fill available width/height when window resizes. Minimum
        height 280px prevents collapse when stacked in scroll area.
        """
        def __init__(self, parent=None, width=8, height=4.5, dpi=100):
            from PyQt5.QtWidgets import QSizePolicy
            self.fig = Figure(figsize=(width, height), dpi=dpi,
                              facecolor='#FFFFFF', edgecolor='none')
            self.axes = self.fig.add_subplot(111)
            super().__init__(self.fig)
            self.setParent(parent)
            # Responsive: expand to fill parent layout cell, maintain min height.
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setMinimumHeight(280)
            # Theme axes — olive/cream palette
            self.axes.set_facecolor('#FFFFFF')
            for spine in self.axes.spines.values():
                spine.set_color('#E5E4DE')
            self.axes.tick_params(colors='#4B5563')
except ImportError:
    HAS_MATPLOTLIB = False
    MplCanvas = None  # guard for `if HAS_MATPLOTLIB:` callers


class SimpleBarWidget(QWidget):
    """Fallback bar chart when matplotlib not available.

    Phase A 2026-04-17: reserved label area expanded 30→80px for rotated labels
    to render fully; colors aligned to theme olive/gold/red palette.
    """
    LABEL_AREA = 60   # vertical space reserved below bars for x-labels
    TOP_PADDING = 16  # space above tallest bar for value text

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = []
        self.dark = False
        self.setMinimumHeight(240)

    def set_data(self, data):
        self.data = data
        self.update()

    def set_theme(self, name):
        self.dark = name == "dark"
        self.update()

    def paintEvent(self, event):
        if not self.data:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        plot_height = self.height() - self.LABEL_AREA - self.TOP_PADDING
        max_val = max([v for _, v in self.data]) if self.data else 1
        n = len(self.data)
        if n == 0:
            return
        bar_width = max(20, (width - 40) / n - 6)

        # Bars cycle the identity colours. These were still the pre-rebalance
        # values — #1E3924 olive and #D4AF37 gold — so the charts kept the old
        # brand while the rest of the application had moved on.
        tokens = colors_for("dark" if self.dark else "light")
        palette = [
            QColor(tokens[name]) for name in (
                "primary", "accent_gold", "cta",
                "primary_hover", "accent_gold_hover", "primary_muted",
            )
        ]
        text_color = QColor(tokens["text"])
        painter.setPen(text_color)

        for i, (label, value) in enumerate(self.data):
            x = 20 + i * (bar_width + 6)
            bar_height = (value / max_val) * plot_height if max_val > 0 else 0
            y = self.TOP_PADDING + (plot_height - bar_height)

            painter.setBrush(palette[i % len(palette)])
            painter.setPen(Qt.NoPen)
            painter.drawRect(int(x), int(y), int(bar_width), int(bar_height))

            # Value label above bar
            painter.setPen(text_color)
            painter.drawText(int(x + bar_width / 4), int(y - 4), str(value))

            # X-axis label — truncated with ellipsis if too long
            painter.setPen(QColor(tokens["text_secondary"]))
            label_text = label[:10] + "…" if len(label) > 12 else label
            painter.drawText(int(x), int(self.height() - self.LABEL_AREA + 16),
                             int(bar_width + 6), 20,
                             Qt.AlignLeft | Qt.AlignTop, label_text)


class AnalyticsDialog(QDialog):
    """Dialog phân tích và thống kê văn bản - Tất cả trong một giao diện"""
    
    def __init__(self, db, parent=None, initial_tab=None):
        super().__init__(parent)
        self.db = db
        self.initial_tab = initial_tab
        self.search_results = []
        self.all_documents = []
        self.setup_ui()
        self._theme_manager = ThemeManager.instance()
        self._theme_manager.register_widget(self)
        self.apply_theme(self._theme_manager.current)
        self.load_data()
    
    def setup_ui(self):
        self.setWindowTitle("Phân tích & Tìm kiếm văn bản — Analytics")
        self.setObjectName("analyticsDialog")
        self.setFont(QFont("Be Vietnam Pro", 9))
        self.setMinimumSize(1100, 700)
        self.resize(1360, 820)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(8)

        # Compact header strip — slim 36px bar with title left + actions right.
        # User feedback 2026-04-18: previous big banner wasted ~80px vertical,
        # pushed charts down. Window title already shows app name — just use
        # a minimal "section title" inline with refresh/export icons.
        header_bar = QHBoxLayout()
        header_bar.setSpacing(8)

        section_title = QLabel("Bảng phân tích")
        section_title.setObjectName("analyticsSectionTitle")
        section_title.setFont(QFont("Be Vietnam Pro", 12, QFont.Bold))
        self._section_title = section_title
        header_bar.addWidget(section_title)

        header_bar.addStretch()

        # Inline action buttons (moved here from footer; saves ~60px).
        self._header_refresh = QPushButton("Làm mới")
        self._header_refresh.setFixedHeight(28)
        self._header_refresh.clicked.connect(self.load_data)
        header_bar.addWidget(self._header_refresh)

        self._header_export = QPushButton("Xuất Excel")
        self._header_export.setObjectName("analyticsExportButton")
        self._header_export.setFixedHeight(28)
        self._header_export.clicked.connect(self.export_report)
        header_bar.addWidget(self._header_export)

        main_layout.addLayout(header_bar)
        
        # Main content - Splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("analyticsSplitter")
        self._splitter = splitter
        splitter.setChildrenCollapsible(False)
        
        # Left panel - Tìm kiếm & Lọc
        left_panel = self.create_search_panel()
        splitter.addWidget(left_panel)
        
        # Right panel - Kết quả & Biểu đồ
        right_panel = self.create_results_panel()
        splitter.addWidget(right_panel)
        
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([310, 1050])
        main_layout.addWidget(splitter)
        
        # Compact footer — only status + close (refresh/export moved to header).
        footer = QHBoxLayout()
        footer.setSpacing(8)

        self.status_label = QLabel("Sẵn sàng")
        self.status_label.setObjectName("analyticsStatus")
        footer.addWidget(self.status_label)

        footer.addStretch()

        close_btn = QPushButton("Đóng")
        close_btn.setObjectName("analyticsCloseButton")
        close_btn.clicked.connect(self.close)
        footer.addWidget(close_btn)

        main_layout.addLayout(footer)

    def apply_theme(self, name):
        """Apply one coherent palette to every Analytics surface.

        Analytics used to pin light backgrounds in a dialog stylesheet while
        inheriting dark text roles from the application palette.  Keeping the
        colors in one local token map prevents that mixed-theme state and also
        lets an already-open dialog react to ThemeManager toggles.
        """
        dark = name == "dark"
        # Names local to this dialog, values from the shared tokens. The map
        # used to hold its own literals and had drifted: the light gold here was
        # still #D4AF37 from before the palette was rebalanced for contrast.
        tokens = colors_for("dark" if dark else "light")
        colors = {
            "bg": tokens["bg"],
            "surface": tokens["surface"],
            "soft": tokens["surface_alt"],
            "header": tokens["surface_hover"],
            "ink": tokens["text"],
            "muted": tokens["text_secondary"],
            "line": tokens["border"],
            "primary": tokens["primary"],
            "primary_hover": tokens["primary_hover"],
            "accent": tokens["accent_gold"],
            "gold": tokens["accent_gold_hover"],
            "hover": tokens["surface_hover"],
        }
        self._theme_name = "dark" if dark else "light"
        self._chart_colors = colors
        c = colors
        self.setStyleSheet(f"""
            QDialog#analyticsDialog {{ background: {c['bg']}; color: {c['ink']}; }}
            QDialog#analyticsDialog QWidget {{
                color: {c['ink']}; font-family: 'Be Vietnam Pro'; font-size: 9pt;
            }}
            QDialog#analyticsDialog QWidget#analyticsSearchPanel,
            QDialog#analyticsDialog QScrollArea,
            QDialog#analyticsDialog QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
            QDialog#analyticsDialog QGroupBox {{
                background: {c['surface']}; border: 1px solid {c['line']};
                border-radius: 8px; margin-top: 10px; padding: 8px 7px 7px;
                font-weight: 600;
            }}
            QDialog#analyticsDialog QGroupBox::title {{
                subcontrol-origin: margin; left: 9px; padding: 0 4px;
                color: {c['accent']}; background: {c['bg']}; font-size: 8.5pt;
            }}
            QDialog#analyticsDialog QLineEdit,
            QDialog#analyticsDialog QComboBox,
            QDialog#analyticsDialog QDateEdit {{
                color: {c['ink']}; background: {c['surface']};
                border: 1px solid {c['line']}; border-radius: 6px;
                padding: 3px 7px; min-height: 22px;
                selection-background-color: {c['primary']};
            }}
            QDialog#analyticsDialog QLineEdit:focus,
            QDialog#analyticsDialog QComboBox:focus,
            QDialog#analyticsDialog QDateEdit:focus {{ border-color: {c['accent']}; }}
            QDialog#analyticsDialog QComboBox QAbstractItemView {{
                color: {c['ink']}; background: {c['surface']};
                selection-background-color: {c['primary']};
            }}
            QDialog#analyticsDialog QPushButton {{
                color: {c['ink']}; background: {c['surface']};
                border: 1px solid {c['line']}; border-radius: 6px;
                padding: 3px 10px; min-height: 24px;
                font-size: 8.5pt; font-weight: 600;
            }}
            QDialog#analyticsDialog QPushButton:hover {{ background: {c['hover']}; }}
            QDialog#analyticsDialog QPushButton:disabled {{ color: {c['muted']}; }}
            QDialog#analyticsDialog QPushButton#analyticsExportButton,
            QDialog#analyticsDialog QPushButton#content_search_btn {{
                color: #FFFEFA; background: {c['primary']}; border: none;
            }}
            QDialog#analyticsDialog QPushButton#analyticsExportButton:hover,
            QDialog#analyticsDialog QPushButton#content_search_btn:hover {{
                background: {c['primary_hover']};
            }}
            QDialog#analyticsDialog QPushButton#show_all_btn {{
                color: #17231B; background: {c['gold']}; border: none;
            }}
            QDialog#analyticsDialog QLabel#analyticsSectionTitle,
            QDialog#analyticsDialog QLabel#analyticsResultsCount {{ color: {c['accent']}; }}
            QDialog#analyticsDialog QLabel#analyticsStatus {{ color: {c['muted']}; font-size: 8.5pt; }}
            QDialog#analyticsDialog QTabWidget::pane {{
                background: {c['surface']}; border: 1px solid {c['line']};
                border-radius: 7px;
            }}
            QDialog#analyticsDialog QTabBar::tab {{
                color: {c['muted']}; background: transparent; padding: 6px 12px;
                border-bottom: 2px solid transparent; font-size: 9pt; font-weight: 600;
            }}
            QDialog#analyticsDialog QTabBar::tab:selected {{
                color: {c['accent']}; border-bottom-color: {c['accent']};
            }}
            QDialog#analyticsDialog QTableWidget {{
                color: {c['ink']}; background: {c['surface']};
                alternate-background-color: {c['soft']};
                selection-background-color: {c['primary']};
                selection-color: #FFFEFA; border: 1px solid {c['line']};
                font-size: 8.5pt;
            }}
            QDialog#analyticsDialog QTableWidget::item {{ color: {c['ink']}; padding: 2px 5px; }}
            QDialog#analyticsDialog QHeaderView::section {{
                color: {c['ink']}; background: {c['header']}; border: none;
                border-bottom: 1px solid {c['line']}; padding: 5px 6px;
                font-size: 8.5pt; font-weight: 600;
            }}
            QDialog#analyticsDialog QScrollBar:vertical {{
                width: 7px; background: transparent; margin: 2px;
            }}
            QDialog#analyticsDialog QScrollBar::handle:vertical {{
                min-height: 28px; border-radius: 3px; background: {c['line']};
            }}
            QDialog#analyticsDialog QScrollBar::add-line:vertical,
            QDialog#analyticsDialog QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        # Explicit palette roles keep item views and native date controls
        # readable even when Windows/Qt style does not derive roles from QSS.
        palette = self.palette()
        role_colors = {
            QPalette.Window: c["bg"], QPalette.WindowText: c["ink"],
            QPalette.Base: c["surface"], QPalette.AlternateBase: c["soft"],
            QPalette.Text: c["ink"], QPalette.Button: c["surface"],
            QPalette.ButtonText: c["ink"], QPalette.Highlight: c["primary"],
            QPalette.HighlightedText: colors_for(self._theme_name)["text_on_primary"],
        }
        for role, value in role_colors.items():
            palette.setColor(role, QColor(value))
        self.setPalette(palette)
        for widget in self.findChildren(QWidget):
            widget.setPalette(palette)

        for chart in self.findChildren(SimpleBarWidget):
            chart.set_theme(self._theme_name)

        if self.all_documents:
            self.update_charts()
    
    def create_search_panel(self):
        """Left search panel wrapped in QScrollArea.

        Responsive fix 2026-04-17: at small window heights the content
        panel (4 group boxes + 2 buttons) overflows the viewport and hides
        the "Tìm kiếm nội dung" button. Wrapping in QScrollArea lets user
        scroll through all filters regardless of window size.
        """
        # Inner content widget — original layout unchanged
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(7)
        
        # === THỐNG KÊ NHANH ===
        # Brand palette: olive (primary), gold (accent), red (CTA), muted olive.
        stats_group = QGroupBox("Thống kê nhanh")
        stats_layout = QGridLayout(stats_group)
        stats_layout.setContentsMargins(6, 9, 6, 6)
        stats_layout.setSpacing(6)

        # Stat cards also carried pre-rebalance literals; take the identity
        # colours from the tokens so they follow the theme like everything else.
        card_tokens = colors_for(getattr(self, "_theme_name", "light"))
        self.total_docs_card = self.create_stat_card("Tổng VB",   "0", card_tokens["primary"])
        self.today_docs_card = self.create_stat_card("Hôm nay",   "0", card_tokens["accent_gold"])
        self.week_docs_card  = self.create_stat_card("Tuần này",  "0", card_tokens["cta"])
        self.month_docs_card = self.create_stat_card("Tháng này", "0", card_tokens["primary_muted"])

        stats_layout.addWidget(self.total_docs_card, 0, 0)
        stats_layout.addWidget(self.today_docs_card, 0, 1)
        stats_layout.addWidget(self.week_docs_card, 1, 0)
        stats_layout.addWidget(self.month_docs_card, 1, 1)

        layout.addWidget(stats_group)

        # === TÌM KIẾM THEO NGÀY ===
        date_group = QGroupBox("Tìm theo ngày")
        date_layout = QFormLayout(date_group)

        self.date_from = QDateEdit()
        self.date_from.setDate(QDate.currentDate().addMonths(-1))
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("dd/MM/yyyy")
        date_layout.addRow("Từ ngày:", self.date_from)

        self.date_to = QDateEdit()
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("dd/MM/yyyy")
        date_layout.addRow("Đến ngày:", self.date_to)

        date_search_btn = QPushButton("Tìm theo ngày")
        date_search_btn.clicked.connect(self.search_by_date)
        date_layout.addRow(date_search_btn)

        layout.addWidget(date_group)

        # === TÌM KIẾM THEO SỐ KÝ HIỆU ===
        number_group = QGroupBox("Tìm theo số ký hiệu")
        number_layout = QVBoxLayout(number_group)

        self.number_input = QLineEdit()
        self.number_input.setPlaceholderText("Nhập số ký hiệu…")
        self.number_input.returnPressed.connect(self.search_by_number)
        number_layout.addWidget(self.number_input)

        number_search_btn = QPushButton("Tìm kiếm")
        number_search_btn.clicked.connect(self.search_by_number)
        number_layout.addWidget(number_search_btn)

        layout.addWidget(number_group)

        # === TÌM KIẾM THEO NỘI DUNG ===
        content_group = QGroupBox("Tìm theo nội dung")
        content_layout = QVBoxLayout(content_group)

        # Single QLineEdit (agent QA fix 2026-04-17: earlier codepath had
        # duplicate `self.content_input = QLineEdit()` assignment that leaked
        # the first widget).
        self.content_input = QLineEdit()
        self.content_input.setPlaceholderText("Nhập từ khóa tìm kiếm…")
        self.content_input.returnPressed.connect(self.search_by_content)
        content_layout.addWidget(self.content_input)

        # Bộ lọc loại văn bản
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Loại VB:"))
        self.doc_type_filter = QComboBox()
        self.doc_type_filter.addItem("Tất cả", "")
        self.doc_type_filter.addItem("Báo cáo", "báo cáo")
        self.doc_type_filter.addItem("Công văn", "công văn")
        self.doc_type_filter.addItem("Quyết định", "quyết định")
        self.doc_type_filter.addItem("Thông báo", "thông báo")
        self.doc_type_filter.addItem("Tờ trình", "tờ trình")
        filter_row.addWidget(self.doc_type_filter)
        content_layout.addLayout(filter_row)

        # Bộ lọc cơ quan
        self.org_filter = QLineEdit()
        self.org_filter.setPlaceholderText("Lọc theo cơ quan ban hành…")
        content_layout.addWidget(self.org_filter)

        content_search_btn = QPushButton("Tìm kiếm nội dung")
        content_search_btn.setObjectName("content_search_btn")
        content_search_btn.clicked.connect(self.search_by_content)
        content_layout.addWidget(content_search_btn)

        layout.addWidget(content_group)

        # === NÚT XEM TẤT CẢ ===
        show_all_btn = QPushButton("Hiển thị tất cả văn bản")
        show_all_btn.setObjectName("show_all_btn")
        show_all_btn.clicked.connect(self.show_all_documents)
        layout.addWidget(show_all_btn)

        layout.addStretch()

        # Wrap in QScrollArea so all filters remain accessible on short windows.
        scroll = QScrollArea()
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

    def create_results_panel(self):
        """Tạo panel kết quả bên phải"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Tabs cho kết quả và biểu đồ
        self.result_tabs = QTabWidget()

        # Tab 1: Kết quả tìm kiếm
        results_tab = QWidget()
        results_layout = QVBoxLayout(results_tab)

        results_header = QHBoxLayout()
        self.results_count_label = QLabel("Kết quả: 0 văn bản")
        self.results_count_label.setObjectName("analyticsResultsCount")
        self.results_count_label.setFont(QFont("Be Vietnam Pro", 11, QFont.Bold))
        results_header.addWidget(self.results_count_label)
        results_header.addStretch()
        results_layout.addLayout(results_header)

        self.results_table = self.create_documents_table()
        results_layout.addWidget(self.results_table)

        self.result_tabs.addTab(results_tab, "Kết quả tìm kiếm")

        # Tab 2: Biểu đồ thống kê
        charts_tab = self.create_charts_tab()
        self.result_tabs.addTab(charts_tab, "Biểu đồ thống kê")
        
        layout.addWidget(self.result_tabs)
        
        return panel
    
    def create_charts_tab(self):
        """3 charts stacked in a scroll area with equal stretch factors.

        Responsive fix 2026-04-17: each chart group gets stretch factor 1
        so all three share available height equally. MplCanvas has
        Expanding size policy so they fill width. Outer scroll kicks in
        only when window is too short (<900px total for 3 × 280px minimum).
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(6, 6, 6, 6)
        scroll_layout.setSpacing(10)

        # Chart 1 — Pie (Phân loại văn bản)
        chart1_group = QGroupBox("Phân loại văn bản")
        chart1_layout = QVBoxLayout(chart1_group)
        chart1_layout.setContentsMargins(8, 20, 8, 8)
        if HAS_MATPLOTLIB:
            self.doc_type_canvas = MplCanvas(self, width=8, height=4.5)
            chart1_layout.addWidget(self.doc_type_canvas)
        else:
            self.doc_type_chart = SimpleBarWidget()
            chart1_layout.addWidget(self.doc_type_chart)
        scroll_layout.addWidget(chart1_group, 1)  # stretch = 1

        # Chart 2 — Monthly bar
        chart2_group = QGroupBox("Văn bản theo tháng")
        chart2_layout = QVBoxLayout(chart2_group)
        chart2_layout.setContentsMargins(8, 20, 8, 8)
        if HAS_MATPLOTLIB:
            self.monthly_canvas = MplCanvas(self, width=8, height=4.5)
            chart2_layout.addWidget(self.monthly_canvas)
        else:
            self.monthly_chart = SimpleBarWidget()
            chart2_layout.addWidget(self.monthly_chart)
        scroll_layout.addWidget(chart2_group, 1)

        # Chart 3 — Horizontal bar (Cơ quan ban hành)
        chart3_group = QGroupBox("Cơ quan ban hành")
        chart3_layout = QVBoxLayout(chart3_group)
        chart3_layout.setContentsMargins(8, 20, 8, 8)
        if HAS_MATPLOTLIB:
            self.org_canvas = MplCanvas(self, width=8, height=4.5)
            chart3_layout.addWidget(self.org_canvas)
        else:
            self.org_chart = SimpleBarWidget()
            chart3_layout.addWidget(self.org_chart)
        scroll_layout.addWidget(chart3_group, 1)

        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        return widget
    
    def create_stat_card(self, title, value, color):
        """Compact stat card — title above, value below.

        Phase A 2026-04-17 + agent QA fix loop:
        - Value font reduced 18pt → 14pt (fix "7X" truncation)
        - Text color applied via objectName + QSS specific selectors so the
          global `theme.qss` `QLabel { color: #1F2937 }` doesn't override.
          Root cause: Qt app-level stylesheet takes precedence over widget
          stylesheet's descendant QLabel rule. Targeted selectors via
          `QFrame#stat_card QLabel#stat_title` break the tie.
        - Gold card (#D4AF37) uses DARK text (WCAG AA fix: white on gold
          was 1.9:1, needs 4.5:1). Other dark-bg cards keep light text.
        """
        # WCAG: white on gold #D4AF37 = 1.9:1 (FAIL). Switch to dark text.
        is_light_bg = color.upper() == '#D4AF37'
        fg = '#1F2937' if is_light_bg else '#FFFFFF'
        fg_muted = 'rgba(31,41,55,0.75)' if is_light_bg else 'rgba(255,255,255,0.85)'

        card = QFrame()
        card.setObjectName("stat_card")
        card.setMinimumHeight(58)
        card.setStyleSheet(f"""
            QFrame#stat_card {{
                background-color: {color};
                border-radius: 10px;
                padding: 6px 10px;
            }}
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(1)

        title_label = QLabel(title)
        title_label.setObjectName("stat_title")
        title_label.setFont(QFont("Be Vietnam Pro", 8))
        title_label.setAlignment(Qt.AlignCenter)
        # Direct label setStyleSheet wins over app-level theme.qss QLabel rule.
        title_label.setStyleSheet(
            f"QLabel#stat_title {{ color: {fg_muted}; background: transparent; }}"
        )
        layout.addWidget(title_label)

        value_label = QLabel(value)
        value_label.setObjectName("stat_value")
        value_label.setFont(QFont("Be Vietnam Pro", 12, QFont.Bold))
        value_label.setAlignment(Qt.AlignCenter)
        value_label.setWordWrap(False)
        value_label.setMaximumWidth(160)
        value_label.setStyleSheet(
            f"QLabel#stat_value {{ color: {fg}; background: transparent; }}"
        )
        layout.addWidget(value_label)

        return card
    
    def create_documents_table(self):
        """Tạo bảng văn bản — Interactive columns with sensible defaults.

        Responsive fix 2026-04-17: switched from ResizeToContents (which
        pinned Số KH / Ngày BH narrow and wasted space on ID) to Interactive
        with explicit default widths + Stretch on Nội dung (the only column
        that should absorb window-width changes). User can drag column
        borders to customize; "Nội dung chính" auto-fills remaining space.
        """
        table = QTableWidget()
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels([
            "ID", "Số ký hiệu", "Ngày BH", "Loại VB",
            "Cơ quan BH", "Nội dung chính", "File"
        ])

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        header.setSectionResizeMode(4, QHeaderView.Interactive)
        header.setSectionResizeMode(5, QHeaderView.Stretch)        # fills spare
        header.setSectionResizeMode(6, QHeaderView.Interactive)
        # Sensible default widths (user can drag to adjust).
        table.setColumnWidth(0, 50)    # ID
        table.setColumnWidth(1, 130)   # Số ký hiệu
        table.setColumnWidth(2, 100)   # Ngày BH
        table.setColumnWidth(3, 110)   # Loại VB
        table.setColumnWidth(4, 180)   # Cơ quan
        table.setColumnWidth(6, 160)   # File

        header.setStretchLastSection(False)
        header.setMinimumSectionSize(40)
        # Taller rows so Vietnamese diacritics don't clip + better readability.
        table.verticalHeader().setDefaultSectionSize(25)
        table.verticalHeader().setVisible(False)

        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.ElideRight)
        table.setShowGrid(False)

        return table
    
    def load_data(self):
        """Tải dữ liệu từ database"""
        try:
            if not self.db:
                self.status_label.setText("Không có kết nối database")
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
            except:
                pass
        
        # ObjectName changed in Phase A from "value" to "stat_value" (to
        # disambiguate title vs value labels). Update findChild lookups.
        self.total_docs_card.findChild(QLabel, "stat_value").setText(str(total))
        self.today_docs_card.findChild(QLabel, "stat_value").setText(str(today_count))
        self.week_docs_card.findChild(QLabel, "stat_value").setText(str(week_count))
        self.month_docs_card.findChild(QLabel, "stat_value").setText(str(month_count))
    
    def update_charts(self):
        """Aggregate counters from loaded documents for 3 charts.

        2026-04-18: pie chart switched from "Loại VB" → "Độ khẩn" per user
        feedback — urgency distribution is more actionable for a document
        management dashboard (hot/cold workload insight) than doc-type breakdown.
        """
        urgency = Counter()         # do_khan field
        orgs = Counter()            # cqbh_tren
        monthly = Counter()         # by YYYY-MM

        for doc in self.all_documents:
            # Normalize urgency label — empty or None becomes "Thường"
            urg_raw = (doc[9] or "").strip()
            if not urg_raw:
                urg = "Thường"
            elif urg_raw.lower() in ("khẩn", "khan"):
                urg = "Khẩn"
            elif urg_raw.lower() in ("thượng khẩn", "thuong khan"):
                urg = "Thượng Khẩn"
            elif urg_raw.lower() in ("hỏa tốc", "hoa toc"):
                urg = "Hỏa Tốc"
            else:
                urg = urg_raw
            urgency[urg] += 1

            org = doc[7] or "Không xác định"
            orgs[org] += 1

            try:
                created = doc[3]
                if created:
                    if isinstance(created, str):
                        month_key = created[:7]
                    else:
                        month_key = created.strftime("%Y-%m")
                    monthly[month_key] += 1
            except Exception:
                pass

        if HAS_MATPLOTLIB:
            self.update_matplotlib_charts(urgency, orgs, monthly)
        else:
            self.update_simple_charts(urgency, orgs, monthly)

    def _style_chart_axes(self, canvas):
        """Synchronize an existing matplotlib canvas with the dialog theme."""
        c = self._chart_colors
        canvas.fig.set_facecolor(c["bg"])
        canvas.axes.set_facecolor(c["surface"])
        canvas.axes.tick_params(colors=c["muted"])
        canvas.axes.xaxis.label.set_color(c["ink"])
        canvas.axes.yaxis.label.set_color(c["ink"])
        for spine in canvas.axes.spines.values():
            spine.set_color(c["line"])

    def update_matplotlib_charts(self, urgency, orgs, monthly):
        """Render 3 charts with Material-pastel palette — modern, soft-eye look.

        2026-04-18 redesign per user feedback:
          - Chart 1: Độ khẩn pie (replaces Loại VB). Pastel red→orange→gold
            →sage sequence conveys urgency hierarchy at a glance.
          - Chart 2: LINE chart with gradient fill below (replaces bar).
            Smoother narrative for timeline data; gradient signals "area under".
          - Chart 3: horizontal bar with pastel olive. Kept top-8 filter +
            big-on-top ordering for quick scanning.

        Color policy: Material Design inspired, pastel-tinted so charts feel
        airy on the cream (#FAFAF5) background — not ceremonial-red like
        before. Brand identity lives in the chrome (title color, CTAs).
        """
        c = self._chart_colors
        line_color = colors_for(self._theme_name)["primary_muted"]

        # ── Chart 1: Độ khẩn pie ──
        self.doc_type_canvas.axes.clear()
        self._style_chart_axes(self.doc_type_canvas)
        if urgency:
            # Preferred order: Hỏa Tốc → Thượng Khẩn → Khẩn → Thường → other
            order = ["Hỏa Tốc", "Thượng Khẩn", "Khẩn", "Thường"]
            ordered_items = [(k, urgency[k]) for k in order if urgency.get(k)]
            for k, v in urgency.most_common():
                if k not in order:
                    ordered_items.append((k, v))
            labels = [k for k, _ in ordered_items]
            sizes = [v for _, v in ordered_items]

            # Pastel palette aligned with urgency semantics
            urgency_colors = {
                "Hỏa Tốc":      '#FF6B6B',   # pastel coral — most urgent
                "Thượng Khẩn":  '#FFA94D',   # pastel peach
                "Khẩn":         '#FFD93D',   # pastel gold
                "Thường":       '#A8C4A8',   # pastel sage
            }
            default_extra = ['#9BB5D6', '#C4A8D4', '#CED4DA', '#B8B8A8']
            colors = []
            extra_idx = 0
            for lab in labels:
                if lab in urgency_colors:
                    colors.append(urgency_colors[lab])
                else:
                    colors.append(default_extra[extra_idx % len(default_extra)])
                    extra_idx += 1

            wedges, texts, autotexts = self.doc_type_canvas.axes.pie(
                sizes, labels=labels, autopct='%1.1f%%',
                colors=colors, startangle=90,
                textprops={'fontsize': 10, 'color': c['ink']},
                pctdistance=0.72,
                wedgeprops={'edgecolor': c['surface'], 'linewidth': 2},
            )
            for autotext in autotexts:
                autotext.set_color(c['ink'])
                autotext.set_fontweight('bold')

            self.doc_type_canvas.axes.set_title(
                "Độ khẩn văn bản", fontsize=13, color=c['ink'],
                fontweight='bold', pad=14,
            )
        self.doc_type_canvas.fig.subplots_adjust(top=0.88, bottom=0.06, left=0.06, right=0.94)
        self.doc_type_canvas.draw()

        # ── Chart 2: Timeline LINE chart với gradient fill ──
        self.monthly_canvas.axes.clear()
        self._style_chart_axes(self.monthly_canvas)
        sorted_months = sorted(monthly.items())[-12:]
        if sorted_months:
            months = [m[-7:] for m, v in sorted_months]   # YYYY-MM
            values = [v for m, v in sorted_months]
            x = list(range(len(months)))

            # Gradient fill beneath the line — pastel olive
            self.monthly_canvas.axes.fill_between(
                x, values, color='#A8C4A8', alpha=0.35, zorder=1,
            )
            # Line with data points
            self.monthly_canvas.axes.plot(
                x, values,
                color=line_color, linewidth=2.2, marker='o',
                markerfacecolor=c['surface'], markeredgecolor=line_color,
                markersize=6, markeredgewidth=2, zorder=3,
            )
            # Annotate peak
            if values:
                peak_idx = values.index(max(values))
                self.monthly_canvas.axes.annotate(
                    f'{max(values)}',
                    xy=(peak_idx, max(values)),
                    xytext=(0, 8), textcoords='offset points',
                    ha='center', fontsize=10, fontweight='bold',
                    color=line_color,
                )

            self.monthly_canvas.axes.set_xticks(x)
            self.monthly_canvas.axes.set_xticklabels(months)
            self.monthly_canvas.axes.set_title(
                "Tần suất văn bản theo thời gian", fontsize=13, color=c['ink'],
                fontweight='bold', pad=12,
            )
            self.monthly_canvas.axes.tick_params(axis='x', rotation=40,
                                                 labelsize=9, colors=c['muted'])
            self.monthly_canvas.axes.tick_params(axis='y', labelsize=9, colors=c['muted'])
            self.monthly_canvas.axes.grid(axis='y', linestyle='--', alpha=0.25, zorder=0)
            # Clean look — remove top/right spines
            self.monthly_canvas.axes.spines['top'].set_visible(False)
            self.monthly_canvas.axes.spines['right'].set_visible(False)
        self.monthly_canvas.fig.subplots_adjust(top=0.88, bottom=0.25, left=0.10, right=0.96)
        self.monthly_canvas.draw()

        # ── Chart 3: Cơ quan ban hành horizontal bar (pastel olive) ──
        self.org_canvas.axes.clear()
        self._style_chart_axes(self.org_canvas)
        if orgs:
            top_orgs = orgs.most_common(8)
            labels = [(k[:32] + '…') if len(k) > 33 else k for k, v in top_orgs]
            sizes = [v for k, v in top_orgs]

            # Pastel olive with subtle shade variation (darker = more docs)
            max_v = max(sizes) if sizes else 1
            bar_colors = [
                (0.66 - 0.25 * (v / max_v), 0.77 - 0.15 * (v / max_v),
                 0.66 - 0.2 * (v / max_v), 0.92)
                for v in sizes
            ]
            self.org_canvas.axes.barh(
                labels, sizes, color=bar_colors,
                edgecolor=line_color, linewidth=0.8,
            )
            # Value labels at end of bars
            for i, v in enumerate(sizes):
                self.org_canvas.axes.text(
                    v, i, f' {v}',
                    va='center', fontsize=9, color=c['ink'], fontweight='bold',
                )

            self.org_canvas.axes.set_title(
                "Cơ quan ban hành — Top 8", fontsize=13, color=c['ink'],
                fontweight='bold', pad=12,
            )
            self.org_canvas.axes.tick_params(axis='y', labelsize=9, colors=c['ink'])
            self.org_canvas.axes.tick_params(axis='x', labelsize=9, colors=c['muted'])
            self.org_canvas.axes.grid(axis='x', linestyle='--', alpha=0.25)
            self.org_canvas.axes.invert_yaxis()  # biggest on top
            self.org_canvas.axes.spines['top'].set_visible(False)
            self.org_canvas.axes.spines['right'].set_visible(False)
        self.org_canvas.fig.subplots_adjust(top=0.88, bottom=0.12, left=0.30, right=0.94)
        self.org_canvas.draw()
    
    def update_simple_charts(self, urgency, orgs, monthly):
        """Fallback when matplotlib missing — pure Qt widgets."""
        # Chart 1 now shows urgency (not doc types).
        self.doc_type_chart.set_data(urgency.most_common(6))
        sorted_months = sorted(monthly.items())[-6:]
        self.monthly_chart.set_data(sorted_months)
        self.org_chart.set_data(orgs.most_common(6))
    
    def populate_table(self, documents):
        """Populate result table with numeric ID sort.

        2026-04-18 fix: ID column previously used QTableWidgetItem(str(id))
        causing lexicographic sort (1, 10, 11, 2, 3…). Switched to
        `setData(Qt.DisplayRole, int)` so Qt natively compares numerically.
        """
        self.results_table.setRowCount(0)

        for doc in documents:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)

            # Column 0 — ID as INT (numeric sort)
            id_item = QTableWidgetItem()
            try:
                id_item.setData(Qt.DisplayRole, int(doc[0]))
            except (ValueError, TypeError):
                id_item.setData(Qt.DisplayRole, str(doc[0]))
            self.results_table.setItem(row, 0, id_item)

            self.results_table.setItem(row, 1, QTableWidgetItem(doc[4] or ""))
            self.results_table.setItem(row, 2, QTableWidgetItem(doc[5] or ""))
            self.results_table.setItem(row, 3, QTableWidgetItem(doc[6] or ""))
            self.results_table.setItem(row, 4, QTableWidgetItem(doc[7] or ""))
            nd = doc[8] or ""
            self.results_table.setItem(row, 5,
                QTableWidgetItem(nd[:80] + "…" if len(nd) > 80 else nd))
            self.results_table.setItem(row, 6, QTableWidgetItem(doc[2] or ""))

        self.results_count_label.setText(f"Kết quả: {len(documents)} văn bản")
        # Enable user-click sorting — respects numeric sort for ID column.
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
            self.status_label.setText(f"Tìm thấy {len(results)} văn bản từ {date_from} đến {date_to}")
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
            self.status_label.setText(f"Tìm thấy {len(results)} văn bản với số ký hiệu '{keyword}'")
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
        """Xuất báo cáo"""
        try:
            from PyQt5.QtWidgets import QFileDialog
            filepath, _ = QFileDialog.getSaveFileName(
                self, "Xuất báo cáo", "", "CSV Files (*.csv);;All Files (*)"
            )
            if filepath:
                with open(filepath, 'w', encoding='utf-8-sig') as f:
                    f.write("ID,Số ký hiệu,Ngày BH,Loại VB,Cơ quan BH,Nội dung,File\n")
                    for row in range(self.results_table.rowCount()):
                        row_data = []
                        for col in range(self.results_table.columnCount()):
                            item = self.results_table.item(row, col)
                            text = item.text() if item else ""
                            text = text.replace('"', '""')
                            row_data.append(f'"{text}"')
                        f.write(",".join(row_data) + "\n")
                
                QMessageBox.information(self, "Thành công", f"Đã xuất báo cáo: {filepath}")
        except Exception as e:
            QMessageBox.warning(self, "Lỗi", f"Lỗi xuất báo cáo: {str(e)}")
