import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app_config
from llama_ocr import LlamaOCR
from document_repository import DocumentDatabase


def _bundled_weights_present():
    """get_model_path returns None when gguf/ is absent, so guard on that
    rather than calling .is_file() on the result."""
    paths = (
        app_config.get_model_path("chandra-q4"),
        app_config.get_mmproj_path("chandra-q4"),
    )
    return all(path is not None and path.is_file() for path in paths)


class ModelIdentityTests(unittest.TestCase):
    def test_invalid_model_environment_falls_back_to_bundled_q4(self):
        with patch.dict(os.environ, {"OCR_MODEL": "not-a-model"}):
            config = importlib.reload(app_config)
            self.assertEqual("chandra-q4", config.ACTIVE_OCR_MODEL)
        importlib.reload(app_config)

    def test_only_bundled_model_is_advertised(self):
        self.assertEqual({"chandra-q4"}, set(app_config.OCR_MODELS))

    @unittest.skipUnless(_bundled_weights_present(), "Chandra GGUF chưa tải")
    def test_advertised_model_files_are_present(self):
        """Split from the catalogue check so CI and worktrees, which carry
        source only, skip the weights instead of failing on them."""
        self.assertTrue(app_config.get_model_path("chandra-q4").is_file())
        self.assertTrue(app_config.get_mmproj_path("chandra-q4").is_file())

    def test_ui_and_builder_do_not_reference_removed_models(self):
        root = Path(__file__).resolve().parents[1]
        source = "\n".join(
            (root / name).read_text(encoding="utf-8-sig")
            for name in ("main_window_1.py", "build_portable.py")
        ).lower()
        for legacy in ("vintern_1b", "vintern-q8", "chandra-q2", "chandra-q8"):
            self.assertNotIn(legacy, source)

    def test_llama_client_reports_active_model_key(self):
        client = LlamaOCR()
        try:
            self.assertEqual("chandra-q4", client.current_model_key)
        finally:
            client.close()

    def test_manual_version_inherits_previous_ocr_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "source.png"
            source.write_bytes(b"image fixture")
            db = DocumentDatabase(tmp / "documents.db")
            try:
                doc_id = db.add_document(
                    str(source),
                    {"Van_ban_dang_txt": "OCR body", "So_Ki_Hieu": "01/CV"},
                    model_key="chandra-q4",
                )
                db.create_new_version(
                    doc_id,
                    {"van_ban_dang_txt": "OCR body", "so_ki_hieu": "02/CV"},
                    modified_by="tester",
                )

                self.assertEqual("chandra-q4", db.get_latest_version(doc_id)["model_used"])
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
