import ast
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
