import tempfile
import unittest
from pathlib import Path

from file_lifecycle import materialize_document_source, remove_temporary_source


class FileLifecycleTests(unittest.TestCase):
    def test_cleanup_removes_original_temp_but_keeps_permanent_copy(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            temp_dir = root / "temp"
            output_dir = root / "output"
            temp_dir.mkdir()
            source = temp_dir / "scan.png"
            source.write_bytes(b"document")

            stored_path, cleanup_path = materialize_document_source(
                source, temp_dir, output_dir, unique_token="checkpoint"
            )
            remove_temporary_source(cleanup_path)

            self.assertFalse(source.exists())
            self.assertTrue(stored_path.exists())
            self.assertEqual(b"document", stored_path.read_bytes())

    def test_external_source_is_not_copied_or_removed(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            temp_dir = root / "temp"
            source = root / "external.pdf"
            temp_dir.mkdir()
            source.write_bytes(b"document")

            stored_path, cleanup_path = materialize_document_source(
                source, temp_dir, root / "output"
            )
            remove_temporary_source(cleanup_path)

            self.assertEqual(source, stored_path)
            self.assertIsNone(cleanup_path)
            self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
