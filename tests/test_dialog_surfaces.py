"""The secondary surfaces after the 2026-10 redesign, and the loose ends the
area merges left in the main window.

The OCR progress window, the PDF preview, the keyword search and the About
dialog still had the old cream look written as hex in Python (a gradient
progress bar, a blue "Xem PDF" button per row, an "OV" text badge), and so
did the floating batch progress, the toasts and the startup overlay. They
now take every colour from theme_tokens, in both themes.
"""
import ast
import gc
import os
import re
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QLineEdit, QToolButton

from theme_manager_v2 import ThemeManager

ROOT = Path(__file__).resolve().parents[1]


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def settle(self):
        for _ in range(4):
            self.app.processEvents()


def _dispose(*widgets):
    """Close the widgets and delete them now.

    deleteLater alone waits for an event loop these tests never enter, so a
    widget would linger until the garbage collector deleted it at some
    arbitrary moment, possibly while Qt was still using it (access
    violations in a later test)."""
    from PyQt5.QtCore import QCoreApplication, QEvent

    for widget in widgets:
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class ThemeSwitchGarbageCollectorTests(unittest.TestCase):
    """setStyleSheet repolishes every widget. A garbage collection in the
    middle of it could delete a leftover widget Qt was still walking and
    crash the process (seen in the test suite)."""

    def _apply(self, app, name):
        previous = ThemeManager._instance
        manager = ThemeManager()
        try:
            with mock.patch.object(manager, "_apply_palette"), \
                    mock.patch.object(manager, "_apply_matplotlib"), \
                    mock.patch.object(manager, "save_persisted"):
                manager.apply(app, name)
        finally:
            ThemeManager._instance = previous

    def test_the_collector_waits_while_the_stylesheet_is_applied(self):
        seen = []

        class App:
            def setStyleSheet(self, _qss):
                seen.append(gc.isenabled())

        self.assertTrue(gc.isenabled())
        self._apply(App(), "dark")
        self.assertEqual([False], seen)
        self.assertTrue(gc.isenabled())

    def test_the_collector_comes_back_after_a_failure_and_only_if_it_was_on(self):
        class Failing:
            def setStyleSheet(self, _qss):
                raise RuntimeError("stylesheet failed")

        class App:
            def setStyleSheet(self, _qss):
                pass

        with self.assertRaises(RuntimeError):
            self._apply(Failing(), "light")
        self.assertTrue(gc.isenabled())
        gc.disable()
        try:
            self._apply(App(), "light")
            self.assertFalse(gc.isenabled(), "a caller that turned it off keeps it off")
        finally:
            gc.enable()


class LineEditActionButtonTests(_QtCase):
    def test_icons_inside_a_line_edit_stay_small_and_centred(self):
        """The generic 26px tool-button minimum hit the buttons Qt puts in a
        line edit (clear button, addAction icons) and pushed them off centre;
        the library filter had a rule of its own, every other field did not."""
        template = (ROOT / "devtools" / "theme_template.qss").read_text(encoding="utf-8")
        self.assertRegex(template, r"\nQLineEdit QToolButton \{[^}]*min-width: 0;[^}]*min-height: 0;")
        ThemeManager.instance().apply(self.app, "light")
        field = QLineEdit()
        field.addAction(QIcon(), QLineEdit.LeadingPosition)
        field.setClearButtonEnabled(True)
        field.setText("quyết định")
        field.resize(240, field.sizeHint().height())
        field.show()
        self.settle()
        buttons = field.findChildren(QToolButton)
        self.assertGreaterEqual(len(buttons), 2)
        for button in buttons:
            self.assertLess(button.minimumSizeHint().height(), 26)
            self.assertLessEqual(button.height(), field.height())
            centre = button.geometry().center().y()
            self.assertLessEqual(abs(centre - field.rect().center().y()), 2)
        _dispose(field)


def _ink(qicon, size=40):
    """The colour of an icon's glyph: its most opaque pixel, to the nearest
    token (thin glyphs at 16 px have few fully opaque pixels, and the
    unpremultiplied colour of the others is off by a step or two)."""
    from theme_tokens import COLORS, DARK_COLORS

    image = qicon.pixmap(size, size).toImage()
    best = None
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if best is None or pixel.alpha() > best.alpha():
                best = pixel
    if best is None or best.alpha() < 160:
        return None
    from PyQt5.QtGui import QColor

    def distance(value):
        colour = QColor(value)
        return max(abs(colour.red() - best.red()), abs(colour.green() - best.green()),
                   abs(colour.blue() - best.blue()))

    candidates = set(COLORS.values()) | set(DARK_COLORS.values())
    nearest = min(candidates, key=distance)
    return nearest.lower() if distance(nearest) <= 4 else best.name()


def _editor_row(text):
    return (
        1, 9, 1, "HĐND TỈNH HƯNG YÊN", "BAN KINH TẾ NGÂN SÁCH", "336/KH-KTNS",
        "02/08/2017", "Không", "Không", "Kế hoạch", "Khảo sát các quỹ",
        "- Lưu VT", "TRƯỞNG BAN", "Trần Quốc Toàn", text, "OCR System",
        "2026-07-11 10:00:00", "chandra-q4",
    )


class MainWindowLooseEndTests(_QtCase):
    """What the area merges left in the main window: a theme refresh that
    still recoloured editor buttons (one of them removed), and a search
    highlight in plain yellow with no text colour, unreadable in dark."""

    def test_theme_refresh_leaves_the_editor_buttons_to_the_editor(self):
        from main_window_1 import MainWindow

        host = mock.MagicMock()
        host._chrome_icons = []
        MainWindow._refresh_theme_icons(host, "dark")
        self.assertEqual([], host.ocr_editor.mock_calls)
        host.pdf_viewer.apply_theme.assert_called_once_with("dark")
        source = (ROOT / "main_window_1.py").read_text(encoding="utf-8")
        self.assertNotIn("_toggle_meta_btn", source)

    def test_the_editor_recolours_its_own_buttons_on_a_theme_switch(self):
        from main_window_1 import OCRResultEditor
        from theme_tokens import colors_for

        manager = ThemeManager.instance()
        manager.apply(self.app, "light")
        editor = OCRResultEditor(db=None)
        manager.register_widget(editor)
        try:
            for theme in ("dark", "light"):
                manager.apply(self.app, theme)
                self.settle()
                tone = colors_for(theme)["text_secondary"].lower()
                for button in (editor.reocr_btn, editor._copy_btn, editor._export_word_btn):
                    self.assertEqual(tone, _ink(button.icon()), (theme, button.toolTip()))
        finally:
            _dispose(editor)

    def test_search_highlights_are_the_editors_theme_marks(self):
        from PyQt5.QtGui import QTextCursor
        from main_window_1 import MainWindow, OCRResultEditor
        from theme_tokens import colors_for

        host = mock.Mock()
        MainWindow.highlight_search_keywords(host, ["quỹ"])
        host.ocr_editor.highlight_keywords.assert_called_once_with(["quỹ"])

        ThemeManager.instance().apply(self.app, "dark")
        editor = OCRResultEditor(db=None)
        editor.show()
        editor.load_data(_editor_row(
            "Kế hoạch khảo sát việc trích lập và sử dụng Quỹ phát triển đất của tỉnh."))
        self.settle()
        host = mock.Mock(ocr_editor=editor)
        MainWindow.highlight_search_keywords(host, ["Quỹ phát triển"])
        tokens = colors_for("dark")
        document = editor.fields["van_ban_dang_txt"].document()
        found = document.find("Quỹ phát triển")
        self.assertFalse(found.isNull())
        inside = QTextCursor(document)
        inside.setPosition(found.selectionStart() + 1)
        mark = inside.charFormat()
        self.assertEqual(tokens["accent_gold_soft"].lower(), mark.background().color().name())
        self.assertEqual(tokens["text"].lower(), mark.foreground().color().name())
        _dispose(editor)
        ThemeManager.instance().apply(self.app, "light")


# ── No colour of their own ──────────────────────────────────────────
# A literal colour: hex, rgb()/rgba(), QColor(r, g, b), a named colour, or a
# gradient. Every one of these surfaces had some; none may come back.
COLOUR_LITERAL = re.compile(
    r"#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?(?:[0-9A-Fa-f]{2})?\b"
    r"|rgba?\(|QColor\(\s*\d|QColor\(\s*[\"'][a-z]|qlineargradient|qconicalgradient"
    r"|Qt\.(?:white|black|red|green|blue|yellow|gray|darkGray|lightGray)\b"
)
FLOATING_MODULES = ("floating_progress.py", "toast_widget.py", "loading_overlay.py")
DIALOG_CLASSES = ("SpinnerWidget", "ProgressDialog", "PreviewDialog", "SearchDialog", "AboutDialog")
MAIN_WINDOW_METHODS = ("show_about", "highlight_search_keywords")


def _dialog_sources():
    """(name, source) of every part of main_window_1.py this area owns."""
    source = (ROOT / "main_window_1.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    parts = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_mark_colour":
            parts.append((node.name, ast.get_source_segment(source, node)))
        if isinstance(node, ast.ClassDef) and node.name in DIALOG_CLASSES:
            parts.append((node.name, ast.get_source_segment(source, node)))
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name in MAIN_WINDOW_METHODS:
                    parts.append(("MainWindow." + item.name, ast.get_source_segment(source, item)))
    return parts


class NoColourLiteralTests(unittest.TestCase):
    def test_the_dialogs_take_every_colour_from_the_tokens(self):
        parts = _dialog_sources()
        self.assertEqual(len(DIALOG_CLASSES) + len(MAIN_WINDOW_METHODS) + 1, len(parts))
        for name, source in parts:
            self.assertEqual([], COLOUR_LITERAL.findall(source), name)
            self.assertNotIn("·", source, name)
            self.assertNotIn('QIcon("icons/', source, name)  # the old PNG set

    def test_the_floating_parts_take_every_colour_from_the_tokens(self):
        for name in FLOATING_MODULES:
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertEqual([], COLOUR_LITERAL.findall(source), name)
            self.assertNotIn("·", source, name)
            # Quiet: no shadow under a floating part.
            self.assertNotIn("QGraphicsDropShadowEffect", source, name)

    def test_the_area_rules_are_one_section_at_the_end_of_the_template(self):
        template = (ROOT / "devtools" / "theme_template.qss").read_text(encoding="utf-8")
        head = "/* ============ AREA: dialogs ============ */"
        self.assertEqual(1, template.count(head))
        section = template.split(head, 1)[1]
        for name in ("progressDialog", "previewDialog", "searchDialog", "aboutDialog",
                     "floatingProgress", "toast", "loadingOverlay"):
            self.assertIn("#" + name, section, name)
        # Floating parts are separated by a hairline, not a shadow or a fill.
        for name in ("floatingProgress", "toast", "loadingOverlay"):
            rule = next(line for line in section.splitlines() if "#{} {{".format(name) in line)
            self.assertIn("1px solid {{border_strong}}", rule, name)
            self.assertIn("{{surface}}", rule, name)


def _pixel(widget, x, y):
    """The token colour drawn at (x, y), allowing the step or two of
    antialiasing a rounded hairline gets."""
    from theme_tokens import COLORS, DARK_COLORS
    from PyQt5.QtGui import QColor

    seen = widget.grab().toImage().pixelColor(x, y)

    def distance(value):
        colour = QColor(value)
        return max(abs(colour.red() - seen.red()), abs(colour.green() - seen.green()),
                   abs(colour.blue() - seen.blue()))

    nearest = min(set(COLORS.values()) | set(DARK_COLORS.values()), key=distance)
    return nearest.lower() if distance(nearest) <= 2 else seen.name()


class SearchDatabase:
    def __init__(self):
        self.queries = []

    def search_documents(self, query, scope):
        self.queries.append((query, scope))
        rows = ((7, "C:/kho/12-KH-UBND.pdf", "12-KH-UBND.pdf", "12/KH-UBND", 14),
                (11, "C:/kho/scan-03.pdf", "scan-03.pdf", "", 1))
        return [
            ((doc_id, path, name, "2026-03-05", 3, 1, "", "", symbol, "", "", "", "", ""),
             {"total_frequency": total, "keyword_frequencies": {"hội nghị": total},
              "snippets": [{"keyword": "hội nghị", "line": 3, "context": "tổ chức hội nghị"}]})
            for doc_id, path, name, symbol, total in rows
        ]


class DialogThemeTests(_QtCase):
    """Each dialog is a sheet of the surface colour of the theme in use, its
    text in the text tokens, in the light and the dark theme."""

    def setUp(self):
        self.manager = ThemeManager.instance()

    def tearDown(self):
        self.manager.apply(self.app, "light")

    def themes(self):
        from theme_tokens import colors_for
        for theme in ("light", "dark"):
            self.manager.apply(self.app, theme)
            yield theme, colors_for(theme)

    def test_progress_dialog_follows_the_theme(self):
        from PyQt5.QtGui import QPalette
        from main_window_1 import ProgressDialog

        for theme, tokens in self.themes():
            dialog = ProgressDialog(100, "Đang xử lý tệp")
            dialog.show()
            self.settle()
            self.assertEqual(tokens["surface"].lower(), _pixel(dialog, 4, dialog.height() // 2), theme)
            title = dialog.task_label.palette().color(QPalette.WindowText).name()
            self.assertEqual(tokens["text"].lower(), title, theme)
            message = dialog.time_label.palette().color(QPalette.WindowText).name()
            self.assertEqual(tokens["text_muted"].lower(), message, theme)
            _dispose(dialog)

    def test_progress_shows_the_step_and_the_percent_apart(self):
        from main_window_1 import ProgressDialog

        dialog = ProgressDialog(100, "Đang xử lý tệp")
        canceled = []
        dialog.canceled.connect(lambda: canceled.append(True))
        dialog.show()
        dialog.update_progress(40, 100, "Trang 2/4")
        self.assertEqual("Trang 2/4", dialog.status_label.full_text())
        self.assertEqual("40%", dialog.pct_label.text())
        dialog.mark_completed()
        self.assertEqual("100%", dialog.pct_label.text())
        self.assertTrue(dialog.ok_btn.isVisibleTo(dialog))
        self.assertFalse(dialog.cancel_btn.isVisibleTo(dialog))
        self.assertEqual("primary", dialog.ok_btn.property("variant"))
        self.assertTrue(dialog.spinner._completed)
        _dispose(dialog)

        dialog = ProgressDialog(100, "Đang xử lý tệp")
        dialog.canceled.connect(lambda: canceled.append(True))
        dialog.show()
        self.assertFalse(dialog.cancel_btn.autoDefault(), "Enter must not stop the OCR")
        dialog.cancel_btn.click()
        self.assertEqual([True], canceled)
        self.assertTrue(dialog.was_canceled)
        self.assertFalse(dialog.spinner._completed, "a cancelled job shows no check")
        _dispose(dialog)

    def test_search_dialog_follows_the_theme_and_keeps_its_hint_in_a_tooltip(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QLabel
        from main_window_1 import SearchDialog

        for theme, tokens in self.themes():
            database = SearchDatabase()
            dialog = SearchDialog(database)
            selected = []
            dialog.document_selected.connect(lambda doc_id, words: selected.append((doc_id, words)))
            dialog.show()
            dialog.search_input.setText("hội nghị, tổng kết")
            dialog.do_search()
            self.settle()
            self.assertEqual([("hội nghị;tổng kết", "all")], database.queries)
            self.assertEqual(tokens["surface"].lower(), _pixel(dialog, 4, dialog.height() // 2), theme)
            on_screen = " ".join(label.text() for label in dialog.findChildren(QLabel))
            self.assertNotIn("dấu phẩy", on_screen)
            self.assertIn("dấu phẩy", dialog.search_input.toolTip())
            self.assertTrue(dialog.result_table.isColumnHidden(0))
            table = dialog.result_table
            # The number when the OCR found one; the file name, quieter, otherwise.
            self.assertEqual("12/KH-UBND", table.item(0, 1).text())
            self.assertEqual("scan-03.pdf", table.item(1, 1).text())
            self.assertEqual(tokens["text_secondary"].lower(),
                             table.item(1, 1).foreground().color().name(), theme)
            for row in range(table.rowCount()):
                for column in range(table.columnCount()):
                    item = table.item(row, column)
                    if item is not None:
                        self.assertEqual(Qt.NoBrush, item.background().style(), (row, column))
            self.assertEqual("Mở văn bản này", table.cellWidget(0, 3).findChild(QToolButton).toolTip())
            table.cellDoubleClicked.emit(1, 1)
            self.assertEqual([(11, ["hội nghị", "tổng kết"])], selected)
            _dispose(dialog)

    def test_preview_of_a_missing_file_offers_no_analysis(self):
        from main_window_1 import PreviewDialog

        for theme, tokens in self.themes():
            dialog = PreviewDialog(str(ROOT / "no-such-file.pdf"))
            dialog.show()
            self.settle()
            self.assertFalse(dialog.process_btn.isEnabled())
            self.assertFalse(dialog.viewer.nav_bar.isVisible())
            self.assertEqual("primary", dialog.process_btn.property("variant"))
            self.assertEqual(tokens["surface"].lower(), _pixel(dialog, 4, 4), theme)
            _dispose(dialog)

    def test_about_dialog_follows_the_theme_and_the_home_page(self):
        from PyQt5.QtGui import QPalette
        from main_window_1 import APP_VERSION, AboutDialog

        landing = (ROOT / "frontend" / "landing" / "index.html").read_text(encoding="utf-8")
        for theme, tokens in self.themes():
            dialog = AboutDialog()
            dialog.show()
            self.settle()
            mark = tokens["primary_muted"] if theme == "dark" else tokens["primary"]
            self.assertEqual(tokens["surface"].lower(), _pixel(dialog, 4, dialog.height() // 2), theme)
            self.assertEqual(mark.lower(), dialog._title.palette().color(QPalette.WindowText).name())
            self.assertFalse(dialog._mark.pixmap().isNull(), theme)
            _dispose(dialog)
        # The same words as the home page's about sheet, so the two cannot drift.
        self.assertIn(AboutDialog.SUMMARY, landing)
        self.assertIn(AboutDialog.NOTE, landing)
        self.assertIn("Phiên bản {}".format(APP_VERSION), landing)
        for term, value in AboutDialog.FACTS:
            self.assertIn("<dt>{}</dt><dd>{}</dd>".format(term, value), landing)


class FloatingPartTests(_QtCase):
    """Batch progress, toasts and the startup overlay: the surface with a
    border_strong hairline in both themes, icons recoloured on a switch."""

    def setUp(self):
        from PyQt5.QtWidgets import QWidget
        self.manager = ThemeManager.instance()
        self.host = QWidget()
        self.host.resize(800, 500)
        self.host.show()

    def tearDown(self):
        _dispose(self.host)
        self.manager.apply(self.app, "light")
        self.settle()

    def assertFloatingSurface(self, widget, tokens, label):
        # Skip the fade. The fade animation has to stop first: still running,
        # it set the opacity back below 1 on the next event loop turn, the
        # surface blended with the window behind it, and on a slower machine
        # (CI) the sampled pixel missed the token.
        from PyQt5.QtCore import QPropertyAnimation
        for animation in widget.findChildren(QPropertyAnimation):
            animation.stop()
        widget._opacity.setOpacity(1.0)
        self.settle()
        self.assertEqual(tokens["border_strong"].lower(), _pixel(widget, widget.width() // 2, 0), label)
        self.assertEqual(tokens["surface"].lower(), _pixel(widget, 4, widget.height() // 2), label)

    def test_batch_progress_follows_the_theme(self):
        from floating_progress import FloatingProgress
        from theme_tokens import colors_for

        progress = FloatingProgress(parent=self.host, title="Đang xử lý 12 văn bản")
        progress.show_above()
        long_step = "Tệp 5/12: " + "rất-dài-" * 20 + ".pdf"
        progress.set_progress(0.42, long_step)
        for theme in ("light", "dark"):
            self.manager.apply(self.app, theme)
            tokens = colors_for(theme)
            self.assertFloatingSurface(progress, tokens, theme)
            self.assertEqual(tokens["text_secondary"].lower(), _ink(progress._close_btn.icon(), 16), theme)
        self.assertEqual("42%", progress._pct_label.text())
        self.assertEqual(long_step, progress._detail_label.toolTip())
        self.assertTrue(progress._detail_label.text().endswith("…"))
        self.assertNotIn("%", progress._detail_label.text())
        progress.mark_completed("Hoàn tất 12/12 văn bản")
        self.assertTrue(progress._done_mark.isVisibleTo(progress))
        self.assertEqual("Hoàn tất 12/12 văn bản", progress._title.full_text())
        self.assertEqual("Đóng", progress._close_btn.toolTip())
        cancelled = []
        progress.cancelled.connect(lambda: cancelled.append(True))
        progress._close_btn.click()
        self.assertEqual([True], cancelled)

    def test_toasts_follow_the_theme_and_say_their_kind_with_an_icon(self):
        import toast_widget
        from theme_tokens import colors_for

        long_message = "245-QD-UBND.pdf: trang 3 chưa nhận dạng được. " * 4
        for theme in ("light", "dark"):
            self.manager.apply(self.app, theme)
            tokens = colors_for(theme)
            for kind, token in (("success", "success"), ("error", "danger"),
                                ("warning", "warning"), ("info", "text_secondary")):
                toast = toast_widget.ToastManager.instance().show(self.host, long_message, kind, 60000)
                self.assertFloatingSurface(toast, tokens, (theme, kind))
                self.assertEqual(tokens[token].lower(),
                                 _ink(QIcon(toast._icon_label.pixmap()), toast.ICON_SIZE), (theme, kind))
                self.assertEqual(long_message, toast.toolTip())
                shown = toast._msg_label.text()
                self.assertTrue(shown.endswith("…"), shown)
                self.assertLessEqual(toast._msg_label.heightForWidth(toast._msg_label.width()),
                                     2 * toast._msg_label.fontMetrics().lineSpacing() + 2)
                toast._dismiss_timer.stop()
                toast.hide()

        # A switch recolours a toast that is still up.
        self.manager.apply(self.app, "light")
        toast = toast_widget.toast_warning(self.host, "Dung lượng thấp", 60000)
        self.manager.apply(self.app, "dark")
        self.assertEqual(colors_for("dark")["warning"].lower(),
                         _ink(QIcon(toast._icon_label.pixmap()), toast.ICON_SIZE))
        self.assertEqual("Dung lượng thấp", toast._msg_label.text())
        self.assertEqual("", toast.toolTip(), "a message shown whole needs no tooltip")
        toast._dismiss_timer.stop()

    def test_startup_overlay_follows_the_theme(self):
        from loading_overlay import LoadingOverlay
        from theme_tokens import colors_for

        overlay = LoadingOverlay(parent=self.host)
        overlay.center_in(self.host)
        overlay.show()
        overlay.set_progress(65, "Đang khởi tạo OCR và kho tri thức...")
        for theme in ("light", "dark"):
            self.manager.apply(self.app, theme)
            self.assertFloatingSurface(overlay, colors_for(theme), theme)
        self.assertEqual("65%", overlay._pct.text())


if __name__ == "__main__":
    unittest.main()
