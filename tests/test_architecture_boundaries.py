import ast
import unittest
from pathlib import Path

from build_portable import CORE_PY_FILES


ROOT = Path(__file__).resolve().parents[1]


class ArchitectureBoundaryTests(unittest.TestCase):
    def _top_level_classes(self, filename):
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
        return {node.name for node in tree.body if isinstance(node, ast.ClassDef)}

    def test_main_window_does_not_define_background_workers(self):
        worker_classes = {
            "EmbeddingWorker", "OCRWorker", "BatchProcessWorker", "FileRepairWorker"
        }

        self.assertTrue((ROOT / "background_workers.py").exists())
        self.assertTrue(worker_classes <= self._top_level_classes("background_workers.py"))
        self.assertTrue(worker_classes.isdisjoint(self._top_level_classes("main_window_1.py")))

    def test_portable_builder_includes_extracted_runtime_modules(self):
        required = {
            "analytics_queries.py", "background_workers.py", "file_lifecycle.py",
            "shutdown_lifecycle.py",
        }
        self.assertTrue(required <= set(CORE_PY_FILES))

    def test_main_window_does_not_define_ocr_pipeline(self):
        self.assertTrue((ROOT / "ocr_pipeline.py").exists())
        self.assertIn("DocumentOCR", self._top_level_classes("ocr_pipeline.py"))
        self.assertNotIn("DocumentOCR", self._top_level_classes("main_window_1.py"))
        self.assertIn("ocr_pipeline.py", CORE_PY_FILES)

    def test_main_window_does_not_define_document_repository(self):
        repository_classes = {"DBConnectionPool", "DocumentDatabase"}

        self.assertTrue((ROOT / "document_repository.py").exists())
        self.assertTrue(
            repository_classes <= self._top_level_classes("document_repository.py")
        )
        self.assertTrue(
            repository_classes.isdisjoint(self._top_level_classes("main_window_1.py"))
        )
        self.assertIn("document_repository.py", CORE_PY_FILES)

    def test_main_window_has_no_duplicate_methods(self):
        tree = ast.parse((ROOT / "main_window_1.py").read_text(encoding="utf-8-sig"))
        for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            methods = [
                node.name for node in class_node.body if isinstance(node, ast.FunctionDef)
            ]
            self.assertEqual(len(methods), len(set(methods)), class_node.name)

    def test_runtime_modules_never_leak_sqlite_connections(self):
        """sqlite3.Connection.__exit__ ends the transaction, it does not close.

        A bare `with sqlite3.connect(...)` therefore keeps the handle open until
        the garbage collector runs, which on Windows blocks deleting, renaming
        or backing up the database file.
        """
        offenders = []
        for name in CORE_PY_FILES:
            path = ROOT / name
            if not path.is_file():
                continue
            source = path.read_text(encoding="utf-8-sig")
            for number, line in enumerate(source.splitlines(), start=1):
                if "with sqlite3.connect(" in line and "closing(" not in line:
                    offenders.append("{}:{}".format(name, number))
        self.assertEqual([], offenders)

    def test_main_window_uses_only_v2_theme_manager(self):
        classes = self._top_level_classes("main_window_1.py")
        source = (ROOT / "main_window_1.py").read_text(encoding="utf-8-sig")

        self.assertNotIn("ThemeManager", classes)
        self.assertNotIn("self.theme_manager", source)


if __name__ == "__main__":
    unittest.main()
