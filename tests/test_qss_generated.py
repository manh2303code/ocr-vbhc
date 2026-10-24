"""theme.qss and theme_dark.qss are generated from one template.

Hand-written, the two had drifted: about thirty selectors existed in one
theme only, so a widget could be styled in light mode and raw in dark mode.
"""
import importlib.util
import unittest
from pathlib import Path

import theme_tokens

ROOT = Path(__file__).resolve().parents[1]


def _builder():
    spec = importlib.util.spec_from_file_location("build_qss", ROOT / "devtools" / "build_qss.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GeneratedStylesheetTests(unittest.TestCase):
    def test_stylesheets_and_their_icons_match_the_template(self):
        stale = [str(path.relative_to(ROOT)) for path, text in _builder().expected().items()
                 if not path.exists() or path.read_text(encoding="utf-8") != text]
        self.assertEqual([], stale, "run devtools/build_qss.py")

    def test_both_themes_style_the_same_selectors(self):
        def selectors(path):
            text = path.read_text(encoding="utf-8")
            return {line.split("{")[0].strip() for line in text.splitlines() if "{" in line}

        self.assertEqual(selectors(ROOT / "theme.qss"), selectors(ROOT / "theme_dark.qss"))

    def test_no_colour_is_written_outside_the_tokens(self):
        import re
        for theme, name in (("light", "theme.qss"), ("dark", "theme_dark.qss")):
            used = set(re.findall(r"#[0-9A-Fa-f]{6}\b", (ROOT / name).read_text(encoding="utf-8")))
            allowed = set(theme_tokens.colors_for(theme).values())
            self.assertEqual(set(), used - allowed, name)

    def test_the_cream_page_is_gone(self):
        for name in ("theme.qss", "theme_dark.qss"):
            self.assertNotIn("#F5F1E8", (ROOT / name).read_text(encoding="utf-8").upper())


if __name__ == "__main__":
    unittest.main()
