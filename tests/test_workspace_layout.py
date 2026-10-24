import unittest
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkspaceLayoutTests(unittest.TestCase):
    def test_details_collapse_only_when_space_is_competing(self):
        from workspace_layout import should_collapse_details

        self.assertTrue(should_collapse_details(1024, False))
        self.assertTrue(should_collapse_details(1366, True))
        self.assertFalse(should_collapse_details(1366, False))
        self.assertFalse(should_collapse_details(1920, True))

    def test_splitter_sizes_keep_center_usable(self):
        from workspace_layout import splitter_sizes

        self.assertEqual([250, 774, 0], splitter_sizes(1024, True, False))
        wide = splitter_sizes(1920, True, True)
        self.assertEqual(280, wide[0])
        self.assertEqual(380, wide[2])
        self.assertEqual(1920, sum(wide))

    def test_workspace_source_has_real_states_and_no_emoji_document_marker(self):
        source = (ROOT / "main_window_1.py").read_text(encoding="utf-8-sig")
        self.assertIn('"Kho văn bản"', source)
        self.assertIn("show_empty_state", source)
        self.assertIn("show_loading_state", source)
        self.assertIn("show_error_state", source)
        self.assertNotIn('f"📄 {short}', source)

    def test_workspace_uses_only_bundled_fluent_icons(self):
        sources = "\n".join(
            (ROOT / name).read_text(encoding="utf-8-sig")
            for name in ("main_window_1.py", "chat_panel.py")
        )
        self.assertNotIn("QIcon.fromTheme", sources)
        names = set(re.findall(r'\bicon\(\s*["\']([^"\']+)', sources))
        missing = [name for name in names if not (ROOT / "icons" / "fluent" / f"{name}.svg").is_file()]
        self.assertEqual([], missing)

    def test_document_list_elides_long_names_without_horizontal_scroll(self):
        source = (ROOT / "doc_list_widget.py").read_text(encoding="utf-8-sig")
        self.assertIn("QFontMetrics", source)
        self.assertIn("Qt.ScrollBarAlwaysOff", source)


if __name__ == "__main__":
    unittest.main()
