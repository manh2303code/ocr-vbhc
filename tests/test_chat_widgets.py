"""The native chat widgets: answer rendering, keyboard, contrast, scrolling."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel

from chat_widgets import (
    AssistantTurnView,
    ChatInput,
    StatusRow,
    Transcript,
    WorkingIndicator,
    chat_contrast_pairs,
    markdown_to_html,
)
from rag_agent import Citation
from tests.test_theme_accessibility import contrast


class MarkdownTests(unittest.TestCase):
    """Qwen answers in Markdown; the old panel showed '- ' and '###' raw."""

    def test_lists_headings_and_emphasis_become_structure(self):
        rendered = markdown_to_html(
            "### Kết luận\n"
            "Văn bản **còn hiệu lực** và *đang áp dụng*.\n\n"
            "- Ý thứ nhất\n"
            "- Ý thứ hai\n\n"
            "1. Bước một\n"
            "2. Bước hai\n"
        )

        self.assertIn("<b>Kết luận</b>", rendered)
        self.assertIn("<b>còn hiệu lực</b>", rendered)
        self.assertIn("<i>đang áp dụng</i>", rendered)
        self.assertEqual(1, rendered.count("<ul"))
        bullets = rendered[rendered.index("<ul"):rendered.index("</ul>")]
        self.assertEqual(2, bullets.count("<li>"))
        self.assertIn("<ol", rendered)
        self.assertNotIn("###", rendered)
        self.assertNotIn("- Ý", rendered)

    def test_ordered_list_keeps_its_starting_number(self):
        rendered = markdown_to_html("3. Ba\n4. Bốn")

        self.assertIn('start="3"', rendered)

    def test_html_in_the_answer_is_escaped(self):
        rendered = markdown_to_html("Xem <script>alert(1)</script> & <b>x</b>")

        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&amp;", rendered)
        self.assertNotIn("<b>x</b>", rendered)

    def test_citations_go_through_the_callback_and_code_is_left_alone(self):
        rendered = markdown_to_html(
            "Theo [2] và `[3]`.",
            cite=lambda n: '<a href="cite:{0}">{0}</a>'.format(n),
        )

        self.assertIn('<a href="cite:2">2</a>', rendered)
        self.assertIn("<code>[3]</code>", rendered)

    def test_a_citation_stays_on_the_line_of_the_word_before_it(self):
        rendered = markdown_to_html(
            "từ ngân sách nhà nước [2].", cite=lambda n: "<a>{}</a>".format(n)
        )

        self.assertIn("nước&nbsp;<a>2</a>.", rendered)

    def test_half_streamed_markers_stay_literal(self):
        rendered = markdown_to_html("Đang viết **chưa đóng")

        self.assertIn("**chưa đóng", rendered)
        self.assertNotIn("<b>", rendered)

    def test_document_numbers_with_underscores_and_stars_are_not_mangled(self):
        rendered = markdown_to_html("Số 12_2020_QĐ và 3 * 4 = 12")

        self.assertIn("12_2020_QĐ", rendered)
        self.assertIn("3 * 4 = 12", rendered)

    def test_pipe_tables_render_as_tables(self):
        rendered = markdown_to_html(
            "| Số | Ngày |\n|---|---|\n| 01/QĐ | 02/01/2024 |\n"
        )

        self.assertIn("<table", rendered)
        self.assertIn("01/QĐ", rendered)
        self.assertNotIn("|---|", rendered)

    def test_unclosed_code_fence_while_streaming_still_renders(self):
        rendered = markdown_to_html("Ví dụ:\n```\nmột <dòng>")

        self.assertIn("<pre", rendered)
        self.assertIn("&lt;dòng&gt;", rendered)


class ChatContrastTests(unittest.TestCase):
    """Every colour pair the chat draws meets WCAG AA in both themes."""

    def test_chat_colour_pairs_meet_their_minimum(self):
        failures = []
        for theme in ("light", "dark"):
            for role, foreground, background, minimum in chat_contrast_pairs(theme):
                ratio = contrast(foreground, background)
                if ratio < minimum:
                    failures.append("{} {}: {:.2f} < {}".format(theme, role, ratio, minimum))
        self.assertEqual([], failures)

    def test_pairs_cover_the_states_the_user_reads(self):
        roles = {role for role, *_ in chat_contrast_pairs("light")}
        for role in ("answer", "question block", "status", "meta", "citation",
                     "source chip", "error title", "send button", "focus ring",
                     "header icon", "disclaimer", "scope"):
            self.assertIn(role, roles)


class ChatInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.input = ChatInput()
        self.sent = []
        self.input.submitted.connect(lambda: self.sent.append(self.input.toPlainText()))

    def tearDown(self):
        self.input.deleteLater()

    def test_enter_submits_and_shift_enter_adds_a_line(self):
        QTest.keyClicks(self.input, "dong mot")
        QTest.keyClick(self.input, Qt.Key_Return, Qt.ShiftModifier)
        QTest.keyClicks(self.input, "dong hai")

        self.assertEqual([], self.sent)
        self.assertEqual("dong mot\ndong hai", self.input.toPlainText())

        QTest.keyClick(self.input, Qt.Key_Return)
        self.assertEqual(["dong mot\ndong hai"], self.sent)

    def test_keypad_enter_also_submits(self):
        QTest.keyClicks(self.input, "hoi")
        QTest.keyClick(self.input, Qt.Key_Enter, Qt.KeypadModifier)

        self.assertEqual(["hoi"], self.sent)

    def test_escape_is_reported_only_while_capturing(self):
        stopped = []
        self.input.escape_pressed.connect(lambda: stopped.append(True))
        QTest.keyClick(self.input, Qt.Key_Escape)
        self.assertEqual([], stopped)

        self.input.capture_escape = True
        QTest.keyClick(self.input, Qt.Key_Escape)
        self.assertEqual([True], stopped)

    def test_up_in_an_empty_box_recalls_the_last_question(self):
        self.input.recall_text = "Câu hỏi trước"
        QTest.keyClick(self.input, Qt.Key_Up)

        self.assertEqual("Câu hỏi trước", self.input.toPlainText())

    def test_box_grows_with_lines_up_to_a_limit(self):
        self.input.resize(300, self.input.height())
        start = self.input.height()
        self.input.setPlainText("một\nhai\nba")
        grown = self.input.height()
        self.input.setPlainText("\n".join("dòng" for _ in range(30)))

        self.assertGreater(grown, start)
        self.assertLessEqual(self.input.height(), self.input.maximum_rows_height())


def _delete_now(widget):
    """Delete a test's widget at once rather than leaving it to a later event
    loop or garbage collection (see _PanelFixture.tearDown in test_chat_panel)."""
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class SourceChipTests(unittest.TestCase):
    """Chips lead with the same number pill as [n] in the text."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_chips_show_number_document_and_page_without_middle_dots(self):
        view = AssistantTurnView()
        view.set_sources(
            [Citation(3, 7, 2, "336/KH-KTNS", 1), Citation(4, 9, None, "", 2)],
            "Nguồn trích dẫn",
        )

        chips = view.source_chips()
        self.assertEqual([3, 4], [chip.property("citationNumber") for chip in chips])
        self.assertEqual(["336/KH-KTNS, tr. 2", "Văn bản #9"], [chip.text() for chip in chips])
        self.assertFalse(any(chip.icon().isNull() for chip in chips))
        # What the chips are is said on hover, not by a caption above them.
        self.assertEqual("Nguồn trích dẫn", view.sources_caption())
        self.assertIn("Nguồn trích dẫn", chips[0].toolTip())
        self.assertIn("trang 2", chips[0].toolTip())
        view.apply_theme("dark")  # repaints the number pills
        self.assertFalse(any(chip.icon().isNull() for chip in view.source_chips()))
        _delete_now(view)


class StatusRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_the_working_line_shows_seconds_and_keeps_the_speed_for_hover(self):
        row = StatusRow()
        row.begin("Đang soạn câu trả lời…")
        row.set_rate("65 token/giây")
        row._started -= 3
        row._on_frame(0.1)

        self.assertEqual("3 giây", row.elapsed.text())
        self.assertIn("65 token/giây", row.elapsed.toolTip())
        row.end()
        _delete_now(row)


class WorkingIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_animates_only_between_start_and_stop(self):
        indicator = WorkingIndicator()
        self.assertFalse(indicator.is_running())
        indicator.start()
        self.assertTrue(indicator.is_running())
        indicator.stop()
        self.assertFalse(indicator.is_running())
        indicator.deleteLater()


class TranscriptScrollTests(unittest.TestCase):
    """New text follows the bottom until the reader scrolls up."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.transcript = Transcript()
        self.transcript.resize(320, 240)
        self.transcript.show()
        self.label = QLabel("\n".join("dòng {}".format(i) for i in range(60)))
        self.transcript.add_view(self.label)
        self._settle()

    def tearDown(self):
        self.transcript.deleteLater()

    def _settle(self):
        for _ in range(5):
            self.app.processEvents()

    def test_growth_keeps_the_bottom_in_view_while_pinned(self):
        bar = self.transcript.verticalScrollBar()
        self.transcript.scroll_to_bottom()
        self._settle()
        self.label.setText(self.label.text() + "\n" + "\n".join("thêm" for _ in range(20)))
        self._settle()

        self.assertEqual(bar.maximum(), bar.value())
        self.assertFalse(self.transcript.jump_button.isVisibleTo(self.transcript))

    def test_scrolling_up_stops_following_and_offers_a_jump_back(self):
        bar = self.transcript.verticalScrollBar()
        self.transcript.scroll_to_bottom()
        self._settle()
        bar.setValue(0)
        self.label.setText(self.label.text() + "\n" + "\n".join("thêm" for _ in range(20)))
        self._settle()

        self.assertEqual(0, bar.value())
        self.assertTrue(self.transcript.jump_button.isVisibleTo(self.transcript))
        self.transcript.jump_button.click()
        self._settle()
        self.assertEqual(bar.maximum(), bar.value())
        self.assertFalse(self.transcript.jump_button.isVisibleTo(self.transcript))


class TranscriptOrderTests(unittest.TestCase):
    """Views keep the order they are added in, always above the footer."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.transcript = Transcript()
        self.footer = QLabel("trạng thái")
        self.transcript.set_footer(self.footer)

    def tearDown(self):
        self.transcript.deleteLater()

    def _order(self):
        layout = self.transcript._layout
        widgets = (layout.itemAt(index).widget() for index in range(layout.count()))
        return [widget for widget in widgets if widget is not None]

    def test_adding_a_view_already_shown_moves_it_last_above_the_footer(self):
        first, second = QLabel("một"), QLabel("hai")
        self.transcript.add_view(first)
        self.transcript.add_view(second)

        self.transcript.add_view(first)

        self.assertEqual([second, first, self.footer], self._order())
        self.assertEqual([second, first], self.transcript.views())

    def test_a_view_taken_out_and_added_back_is_shown_again(self):
        self.transcript.resize(320, 240)
        self.transcript.show()
        view = QLabel("gợi ý")
        self.transcript.add_view(view)
        self.app.processEvents()
        self.transcript.remove_view(view, delete=False)
        self.assertFalse(view.isVisibleTo(self.transcript))

        self.transcript.add_view(view)
        self.app.processEvents()

        self.assertTrue(view.isVisibleTo(self.transcript))
        self.assertEqual([view, self.footer], self._order())


if __name__ == "__main__":
    unittest.main()
