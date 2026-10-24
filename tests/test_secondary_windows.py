"""The secondary windows of the 2026-10 minimal redesign.

Analytics, the AI semantic search, statistics, the index rebuild and the
settings carried hand-written colours (about sixty hex literals, a
"Material pastel" chart palette, light-only inline stylesheets) and the
search showed its matched passage only in a tooltip, cut to 300 of about
2000 characters. Now their colours come from the theme tokens through the
generated stylesheet and theme_manager_v2.matplotlib_rc, they follow a live
theme switch, and text is never cut away for good.
"""
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication, QLabel

import theme_manager_v2
from theme_manager_v2 import CHART_SERIES_TOKENS, ThemeManager, matplotlib_rc
from theme_tokens import colors_for
from ui_icons import icon

ROOT = Path(__file__).resolve().parents[1]

# Switching themes here must not change the theme the installed app reads.
_NO_PERSIST = mock.patch.object(ThemeManager, "save_persisted", lambda self, name: None)


def setUpModule():
    _NO_PERSIST.start()


def tearDownModule():
    _NO_PERSIST.stop()


OWNED = ("ai_search_dialog.py", "analytics_dialog.py", "statistics_dialog.py",
         "reindex_dialog.py", "settings_dialog.py")
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b")


def _text_colour(widget):
    widget.ensurePolished()
    return widget.palette().color(QPalette.WindowText).name()


class NoColourOutsideTheTokensTests(unittest.TestCase):
    def test_the_windows_write_no_colour_of_their_own(self):
        for name in OWNED:
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertEqual([], HEX.findall(source), name)
            self.assertEqual([], re.findall(r"rgba?\(|QColor\(\s*\d", source), name)

    def test_no_meta_strings_joined_with_middle_dots(self):
        for name in OWNED:
            self.assertNotIn(" · ", (ROOT / name).read_text(encoding="utf-8"), name)

    def test_the_matplotlib_theme_writes_no_colour_of_its_own(self):
        import inspect
        source = inspect.getsource(theme_manager_v2.ThemeManager._apply_matplotlib)
        source += inspect.getsource(matplotlib_rc)
        self.assertEqual([], HEX.findall(source))


class MatplotlibThemeTests(unittest.TestCase):
    def test_every_chart_colour_is_a_token_of_its_theme(self):
        for theme in ("light", "dark"):
            tokens = colors_for(theme)
            rc = matplotlib_rc(theme)
            colours = {key: value for key, value in rc.items()
                       if isinstance(value, str) and value.startswith("#")}
            self.assertGreater(len(colours), 10)
            for key, value in colours.items():
                self.assertIn(value, tokens.values(), (theme, key))
            self.assertEqual(tokens["surface"], rc["figure.facecolor"])
            self.assertEqual(tokens["surface"], rc["axes.facecolor"])
            self.assertEqual(tokens["text"], rc["axes.titlecolor"])
            series = rc["axes.prop_cycle"].by_key()["color"]
            self.assertEqual([tokens[key] for key in CHART_SERIES_TOKENS], series)
            # Olive carries the data; gold is only the second colour.
            self.assertEqual(tokens["primary_muted"], series[0])
            self.assertEqual(tokens["accent_gold"], series[1])

    def test_charts_are_flat_and_quiet(self):
        rc = matplotlib_rc("light")
        self.assertFalse(rc["axes.grid"])
        self.assertEqual("-", rc["grid.linestyle"])  # a grid, when asked for, is solid
        self.assertFalse(rc["axes.spines.top"])
        self.assertFalse(rc["axes.spines.right"])
        self.assertEqual("left", rc["axes.titlelocation"])
        self.assertEqual(0, rc["xtick.major.size"])

    def test_apply_points_loaded_matplotlib_at_the_theme(self):
        import matplotlib
        from matplotlib.colors import to_hex
        manager = ThemeManager()
        for theme in ("dark", "light"):
            manager._apply_matplotlib(theme)
            tokens = colors_for(theme)
            self.assertEqual(tokens["surface"].lower(), to_hex(matplotlib.rcParams["axes.facecolor"]))
            self.assertEqual(tokens["border"].lower(), to_hex(matplotlib.rcParams["axes.edgecolor"]))

    def test_apply_does_not_load_matplotlib(self):
        """Importing pyplot on every theme apply cost the start ~300 ms."""
        with mock.patch.dict(sys.modules, {"matplotlib": None}):
            ThemeManager()._apply_matplotlib("dark")  # returns without importing


def _documents():
    """Rows in the shape of analytics_queries.fetch_latest_documents."""
    long_text = "Về việc trích lập và sử dụng Quỹ phát triển đất\n" + "nội dung dài " * 30
    rows = [
        (1, "a.pdf", "a.pdf", "2026-10-15 09:00:00", "1/KH-A", "01/02/2020", "Kế hoạch",
         "HĐND TỈNH HƯNG YÊN", long_text, ""),
        (2, "b.pdf", "b.pdf", "2026-09-01 09:00:00", "2/QĐ-B", "02/03/2021", "Quyết định",
         "ỦY BAN NHÂN DÂN", "Ngắn", "Hỏa tốc"),
        (3, "c.pdf", "c.pdf", "2026-10-02 09:00:00", "3/CV-C", "03/04/2022", "Công văn",
         "HĐND TỈNH HƯNG YÊN", "Ngắn", "Không"),
    ]
    return rows, long_text


class AnalyticsWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        ThemeManager.instance().apply(self.app, "light")

    def _dialog(self):
        from analytics_dialog import AnalyticsDialog
        dialog = AnalyticsDialog(db=None)
        dialog.all_documents, _ = _documents()
        dialog.update_charts()
        dialog.populate_table(dialog.all_documents)
        dialog.resize(1360, 820)
        dialog.show()
        self.app.processEvents()
        return dialog

    def test_charts_follow_a_live_theme_switch_in_token_colours(self):
        from analytics_dialog import HAS_MATPLOTLIB
        if not HAS_MATPLOTLIB:
            self.skipTest("matplotlib not installed")
        from matplotlib.colors import to_hex
        manager = ThemeManager.instance()
        manager.apply(self.app, "light")
        dialog = self._dialog()
        for theme in ("dark", "light"):
            manager.apply(self.app, theme)
            self.app.processEvents()
            tokens = colors_for(theme)
            for canvas in (dialog.monthly_canvas, dialog.doc_type_canvas, dialog.org_canvas):
                self.assertEqual(tokens["surface"].lower(), to_hex(canvas.fig.get_facecolor()))
                self.assertEqual(tokens["surface"].lower(), to_hex(canvas.axes.get_facecolor()))
                self.assertFalse(canvas.axes.spines["top"].get_visible())
                for text in canvas.axes.texts:  # values beside the bars: text tokens only
                    self.assertIn(to_hex(text.get_color()),
                                  {tokens[k].lower() for k in ("text", "text_secondary", "text_muted")})
            monthly = {to_hex(p.get_facecolor()) for p in dialog.monthly_canvas.axes.patches}
            self.assertEqual({tokens["primary_muted"].lower()}, monthly)
            urgency = dialog.doc_type_canvas.axes
            labels = [t.get_text() for t in urgency.get_yticklabels()]
            self.assertEqual(["Hỏa tốc", "Thượng khẩn", "Khẩn", "Thường"], labels)
            bars = urgency.patches
            self.assertEqual(tokens["urgency_hoa_toc"].lower(), to_hex(bars[0].get_facecolor()))
            self.assertEqual(tokens["primary_muted"].lower(), to_hex(bars[3].get_facecolor()))
            # Empty and "Không" are one level, Thường.
            self.assertEqual([1, 0, 0, 2], [int(b.get_width()) for b in bars])
        self.assertEqual("Thường: 2 văn bản", dialog.doc_type_canvas.hover_text(3))
        dialog.close()

    def test_window_and_icons_follow_a_live_theme_switch(self):
        manager = ThemeManager.instance()
        manager.apply(self.app, "light")
        dialog = self._dialog()
        for theme in ("dark", "light"):
            manager.apply(self.app, theme)
            self.app.processEvents()
            tokens = colors_for(theme)
            self.assertEqual(tokens["surface"].lower(),
                             dialog.palette().color(QPalette.Window).name())
            self.assertEqual(tokens["text_muted"].lower(), _text_colour(dialog.status_label))
            self.assertEqual(tokens["text"].lower(),
                             _text_colour(dialog.total_docs_card.findChild(QLabel, "stat_value")))
            self.assertEqual(icon("refresh-cw", size=18, color=tokens["text_secondary"]).cacheKey(),
                             dialog._header_refresh.icon().cacheKey())
        dialog.close()

    def test_table_keeps_the_whole_text(self):
        """The main content used to be cut to 80 characters for good."""
        dialog = self._dialog()
        _, long_text = _documents()
        item = dialog.results_table.item(0, 5)
        self.assertEqual(" ".join(long_text.split()), item.text())
        self.assertIn("nội dung dài " * 3, item.toolTip())
        self.assertTrue(item.toolTip().startswith("<p>"))  # rich text, so Qt wraps it
        self.assertEqual("3 văn bản", dialog.results_count_label.text())
        dialog.close()


class _StubDb:
    def __init__(self, nd_chinh):
        self.nd_chinh = nd_chinh
        self.db_path = ":memory:"

    def get_document_info(self, doc_id):
        return (doc_id, "x.pdf", "01.01.52.441.03.pdf", "2026-10-15 09:00:00")

    def get_latest_version(self, doc_id):
        return (1, doc_id, 1, "HĐND TỈNH HƯNG YÊN", "", "336/KH-KTNS", "02/08/2017",
                "", "", "Kế hoạch", self.nd_chinh)


class _StubStore:
    def __init__(self, path):
        self.db_path = path

    def get_embedding_count(self):
        return 1


class AISearchWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE document_chunks (id INTEGER PRIMARY KEY, document_id INTEGER, "
                         "chunk_index INTEGER, chunk_text TEXT, embedding BLOB, page_number INTEGER)")

    def tearDown(self):
        ThemeManager.instance().apply(self.app, "light")
        try:
            os.remove(self.path)
        except OSError:
            pass

    def _dialog(self, nd_chinh="Việc trích lập, quản lý và sử dụng Quỹ phát triển đất " * 6):
        from ai_search_dialog import AISearchDialog
        embedding = mock.Mock()
        embedding.is_available.return_value = True
        dialog = AISearchDialog(_StubDb(nd_chinh), embedding, _StubStore(self.path))
        dialog.resize(900, 640)
        dialog.show()
        return dialog

    CHUNK = ("Mở đầu của văn bản. " * 40 + "Khảo sát việc trích lập Quỹ phát triển đất "
             "và Quỹ bảo trì đường bộ từ năm 2015. " + "Phần sau của văn bản. " * 40)

    def test_the_matched_passage_is_on_screen_and_whole_on_expand(self):
        dialog = self._dialog()
        dialog.search_input.setText("quỹ phát triển đất")
        dialog.on_search_complete([(7, 0.82, self.CHUNK)])
        self.app.processEvents()
        row = dialog.result_rows()[0]
        self.assertTrue(row.passage_label.isVisible())
        shown = row.passage_label.text()
        self.assertIn("<b>Quỹ phát triển đất</b>", shown)  # the phrase, in bold
        self.assertLess(len(row.passage_text()), len(self.CHUNK) / 2)
        self.assertTrue(row.passage_text().startswith("…"))
        self.assertFalse(row.is_expanded())

        dialog.set_result_expanded(0, True)
        self.app.processEvents()
        self.assertTrue(row.is_expanded())
        self.assertEqual(" ".join(self.CHUNK.split()), " ".join(row.passage_text().split()))
        item = dialog.result_list.item(0)
        self.assertGreaterEqual(item.sizeHint().height(), row.passage_label.height())
        dialog.close()

    def test_main_content_and_tooltip_are_whole(self):
        nd_chinh = "Việc trích lập, quản lý và sử dụng Quỹ phát triển đất " * 6
        dialog = self._dialog(nd_chinh)
        dialog.search_input.setText("quỹ")
        dialog.on_search_complete([(7, 0.5, self.CHUNK)])
        row = dialog.result_rows()[0]
        self.assertEqual(" ".join(nd_chinh.split()), row.summary_label.full_text())
        self.assertIn(nd_chinh.strip()[-40:], dialog.result_list.item(0).toolTip())
        self.assertEqual("336/KH-KTNS", row.number_label.text())
        dialog.close()

    def test_opening_a_result_selects_the_document(self):
        dialog = self._dialog()
        chosen = []
        dialog.document_selected.connect(lambda doc_id, words: chosen.append(doc_id))
        dialog.search_input.setText("quỹ")
        dialog.on_search_complete([(7, 0.5, self.CHUNK), (9, 0.4, "Quỹ")])
        dialog.result_rows()[1].open_button.click()
        self.assertEqual([9], chosen)
        dialog.close()

    def test_no_result_says_what_to_do_on_screen(self):
        dialog = self._dialog()
        dialog.search_input.setText("không có gì")
        dialog.on_search_complete([])
        self.assertTrue(dialog.response_label.isVisible())
        self.assertIn("Thử mô tả", dialog.response_label.text())
        self.assertEqual(0, dialog.result_list.count())
        dialog.close()

    def test_follows_a_live_theme_switch(self):
        manager = ThemeManager.instance()
        manager.apply(self.app, "light")
        dialog = self._dialog()
        dialog.search_input.setText("quỹ")
        dialog.on_search_complete([(7, 0.5, self.CHUNK)])
        row = dialog.result_rows()[0]
        for theme in ("dark", "light"):
            manager.apply(self.app, theme)
            self.app.processEvents()
            tokens = colors_for(theme)
            self.assertEqual(tokens["surface"].lower(), dialog.palette().color(QPalette.Window).name())
            self.assertEqual(tokens["text_muted"].lower(), _text_colour(dialog.count_label))
            self.assertEqual(tokens["text"].lower(), _text_colour(row.passage_label))
            self.assertEqual(icon("sync", size=16, color=tokens["text_secondary"]).cacheKey(),
                             dialog.reindex_btn.icon().cacheKey())
            self.assertEqual(icon("open", size=16, color=tokens["text_secondary"]).cacheKey(),
                             row.open_button.icon().cacheKey())
            self.assertIn(tokens["text_secondary"], dialog.result_list.item(0).toolTip())
        dialog._set_status("Lỗi: x", "error")
        self.assertEqual(colors_for("light")["danger"].lower(), _text_colour(dialog.status_label))
        dialog.close()

    def test_highlight_marks_phrases_not_lone_syllables(self):
        from ai_search_dialog import highlight_terms
        query = "quỹ phát triển đất và quỹ bảo trì đường bộ"
        marked = highlight_terms("Quỹ phát triển đất, cán bộ, chính trị, đường bộ", query)
        self.assertEqual("<b>Quỹ phát triển đất</b>, cán bộ, chính trị, <b>đường bộ</b>", marked)
        self.assertEqual("<b>quỹ</b> &amp; quy", highlight_terms("quỹ & quy", "quỹ"))
        self.assertEqual("<b>Quỹ</b> và <b>quy</b>", highlight_terms("Quỹ và quy", "quy"))


class OtherWindowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        ThemeManager.instance().apply(self.app, "light")

    def test_statistics_merges_unmarked_urgency_and_colours_levels_by_theme(self):
        from statistics_dialog import StatisticsDialog
        stats = {
            "total_documents": 33,
            "by_type": [("Kế hoạch", 20), (None, 13)],
            "by_urgency": [("", 29), ("Không", 1), ("Hỏa tốc", 3)],
            "by_month": [("2026-10", 33)],
            "recent_docs": [(1, "a.pdf", "2026-10-15 09:55:02", "1/KH")],
        }
        db = mock.Mock()
        db.get_statistics.return_value = stats
        manager = ThemeManager.instance()
        manager.apply(self.app, "light")
        dialog = StatisticsDialog(db)
        dialog.show()
        table = dialog.urgency_table
        rows = [(table.item(r, 0).text(), table.item(r, 1).text()) for r in range(table.rowCount())]
        self.assertEqual([("Hỏa tốc", "3"), ("Thường", "30")], rows)
        self.assertEqual("15/10/2026 09:55", dialog.recent_table.item(0, 2).text())
        self.assertEqual("Tháng 10/2026", dialog.time_table.item(0, 0).text())
        for theme in ("dark", "light"):
            manager.apply(self.app, theme)
            self.app.processEvents()
            tokens = colors_for(theme)
            self.assertEqual(tokens["urgency_hoa_toc"].lower(), table.item(0, 0).foreground().color().name())
            self.assertEqual(tokens["text"].lower(), _text_colour(dialog.total_label))
            self.assertEqual(tokens["surface"].lower(), dialog.palette().color(QPalette.Window).name())
        dialog.close()

    def test_reindex_shows_count_and_document_apart(self):
        from reindex_dialog import ReindexDialog
        dialog = ReindexDialog(":memory:", None)
        dialog._started = True  # no worker in a test
        dialog.show()
        dialog._on_progress(12, 30, "01.01.52.441.03.pdf")
        self.app.processEvents()
        self.assertEqual("12/30", dialog.count_label.text())
        self.assertEqual("01.01.52.441.03.pdf", dialog.name_label.full_text())
        self.assertFalse(dialog.detail_label.isVisible())
        for label in dialog.findChildren(QLabel):
            self.assertNotIn(" — ", label.text())
        dialog._on_error("hỏng")
        self.assertTrue(dialog.detail_label.isVisible())
        self.assertFalse(dialog.count_label.isVisible())
        self.assertEqual(colors_for("light")["danger"].lower(), _text_colour(dialog.detail_label))
        dialog.close()

    def test_settings_hints_are_tooltips_and_buttons_vietnamese(self):
        from PyQt5.QtWidgets import QDialogButtonBox
        from settings_dialog import SettingsDialog
        manager = ThemeManager.instance()
        manager.apply(self.app, "light")
        dialog = SettingsDialog(None)
        dialog.show()
        texts = " ".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertNotIn("Ping", texts)
        self.assertNotIn("cream", dialog._rb_light.text())
        self.assertIn("6 giây", dialog._chk_gpu_ka.toolTip())
        ok = dialog._btns.button(QDialogButtonBox.Ok)
        self.assertEqual("Lưu", ok.text())
        self.assertEqual("primary", ok.property("variant"))
        self.assertEqual("Hủy", dialog._btns.button(QDialogButtonBox.Cancel).text())
        self.assertEqual("Áp dụng", dialog._btns.button(QDialogButtonBox.Apply).text())
        self.assertEqual({"theme", "font_size", "ui_show_status_bar", "ocr_gpu_keepalive"},
                         set(dialog.snapshot()))
        label = next(l for l in dialog.findChildren(QLabel) if l.property("role") == "settingsLabel")
        for theme in ("dark", "light"):
            manager.apply(self.app, theme)
            self.app.processEvents()
            tokens = colors_for(theme)
            self.assertEqual(tokens["surface"].lower(), dialog.palette().color(QPalette.Window).name())
            self.assertEqual(tokens["text_secondary"].lower(), _text_colour(label))
        dialog.reject()


if __name__ == "__main__":
    unittest.main()
