import ast
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDockWidget, QLineEdit, QMainWindow


ROOT = Path(__file__).resolve().parents[1]


class ChatMainWindowWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "main_window_1.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_chat_panel_is_constructed_after_models_and_docked_hidden(self):
        reranker = self.source.index("self.reranker = RerankerModel()")
        panel = self.source.index("self.chat_panel = ChatPanel(")
        self.assertGreater(panel, reranker)
        self.assertIn(
            "self.addDockWidget(Qt.RightDockWidgetArea, self.chat_panel)",
            self.source,
        )
        self.assertIn("self.chat_panel.hide()", self.source)

    def test_toolbar_toggle_citation_scope_and_shutdown_are_wired(self):
        self.assertIn('"panel-right"', self.source)
        self.assertIn("self.chat_panel.setVisible", self.source)
        self.assertIn("self.chat_panel.open_document_requested.connect", self.source)
        self.assertIn("self.chat_panel.set_scope_doc_ids([int(doc_id)])", self.source)
        self.assertIn("self.chat_panel.shutdown()", self.source)
        self.assertIn("if not self.chat_panel.shutdown()", self.source)
        # The toggle follows the dock through one handler (see
        # ChatDockToggleTests); fed straight from visibilityChanged, it closed
        # the dock whenever the window was minimised.
        self.assertIn(
            "self.chat_panel.visibilityChanged.connect(self._on_chat_visibility_changed)",
            self.source,
        )
        self.assertNotIn("visibilityChanged.connect(self.act_chat.setChecked)", self.source)

    def test_citation_handler_reuses_in_app_document_api_with_page(self):
        main = next(
            node for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
        )
        method = next(
            node for node in main.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_open_chat_document"
        )
        calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]
        show_calls = [
            call for call in calls
            if isinstance(call.func, ast.Attribute)
            and call.func.attr == "show_document"
        ]
        self.assertEqual(1, len(show_calls))
        self.assertEqual(2, len(show_calls[0].args))

        from main_window_1 import MainWindow

        events = []
        host = type("Host", (), {})()
        host.doc_list = type(
            "DocList", (), {"find_and_select": lambda _self, doc: events.append(("select", doc))}
        )()
        host.chat_panel = type(
            "Panel", (), {"set_scope_doc_ids": lambda _self, ids: events.append(("scope", ids))}
        )()
        host.show_document = lambda doc, page: events.append(("show", doc, page))

        MainWindow._open_chat_document(host, 12, 3)

        self.assertEqual(
            [("select", 12), ("scope", [12]), ("show", 12, 3)], events
        )


class ChatDockToggleTests(unittest.TestCase):
    """The toolbar toggle follows the reader opening or closing the dock.

    Minimising the window, or hiding it for the home screen, hides the dock
    too; that is not the reader closing it.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from main_window_1 import MainWindow
        from ui_icons import make_tool_action

        class Host(QMainWindow):
            # The real handlers, on a window without the OCR workspace.
            _toggle_chat_panel = MainWindow._toggle_chat_panel
            _on_chat_visibility_changed = MainWindow._on_chat_visibility_changed

            def _apply_responsive_layout(self):
                self.layouts += 1

        host = Host()
        host.layouts = 0
        host._details_user_choice = None
        host._layout_bucket = None
        host._chat_open = False
        host.setCentralWidget(QLineEdit())
        host.chat_panel = QDockWidget("Trợ lý pháp luật", host)
        host.chat_panel.setWidget(QLineEdit())
        host.addDockWidget(Qt.RightDockWidgetArea, host.chat_panel)
        host.act_chat = make_tool_action(
            host, "panel-right", "Trợ lý", callback=host._toggle_chat_panel, checkable=True,
        )
        # As MainWindow.__init__ wires it (asserted in the source test above).
        host.chat_panel.visibilityChanged.connect(host._on_chat_visibility_changed)
        host.chat_panel.hide()
        host.resize(900, 600)
        host.show()
        QTest.qWaitForWindowExposed(host)
        self.host = host

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()

    def test_minimising_the_window_keeps_the_chat_dock_open(self):
        host = self.host
        host.act_chat.setChecked(True)
        QTest.qWait(20)
        self.assertTrue(host.chat_panel.isVisible())
        host._details_user_choice = False  # the reader then hid the details

        host.showMinimized()
        QTest.qWait(20)
        host.showNormal()
        QTest.qWait(20)

        self.assertTrue(host.chat_panel.isVisible())
        self.assertTrue(host.act_chat.isChecked())
        self.assertIs(False, host._details_user_choice)

    def test_hiding_the_window_for_the_home_screen_keeps_the_dock_open(self):
        host = self.host
        host.act_chat.setChecked(True)
        QTest.qWait(20)

        host.hide()
        QTest.qWait(20)
        host.show()
        QTest.qWait(20)

        self.assertTrue(host.chat_panel.isVisible())
        self.assertTrue(host.act_chat.isChecked())

    def test_opening_and_closing_the_dock_moves_the_toggle_and_the_layout(self):
        host = self.host
        host.act_chat.setChecked(True)
        QTest.qWait(20)
        self.assertTrue(host.chat_panel.isVisible())
        self.assertEqual(1, host.layouts)

        host._details_user_choice = False
        host.chat_panel.close()  # the dock's own close button
        QTest.qWait(20)

        self.assertFalse(host.act_chat.isChecked())
        self.assertFalse(host.chat_panel.isVisible())
        self.assertIsNone(host._details_user_choice)
        self.assertEqual(2, host.layouts)


if __name__ == "__main__":
    unittest.main()
