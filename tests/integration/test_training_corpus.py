"""Real offline OCR -> index -> chat integration test.

This suite is intentionally opt-in because it starts both bundled llama-server
models and OCRs two real PDFs. Run from the portable root with::

    set RUN_INTEGRATION=1
    runtime\python.exe -m unittest tests.integration.test_training_corpus -v
"""

import os
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import List


TRAINING_ROOT = Path(os.environ.get("TRAINING_ROOT", "TRAINING"))


@dataclass
class PipelineResult:
    pdf_paths: List[Path]
    hybrid_doc_ids: List[int]
    expected_doc_id: int
    answer: str
    citations: list


def _select_two_types(root: Path) -> List[Path]:
    """Select the two smallest PDFs whose top-level document types differ."""
    candidates = []
    for category in sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda path: path.name.casefold(),
    ):
        files = sorted(
            category.rglob("*.pdf"),
            key=lambda path: (path.stat().st_size, str(path).casefold()),
        )
        if files:
            candidates.append(files[0])
    return sorted(
        candidates, key=lambda path: (path.stat().st_size, str(path).casefold())
    )[:2]


def _missing_requirements(root: Path) -> List[str]:
    if not root.is_dir():
        return ["TRAINING corpus not found: {}".format(root)]
    pdf_paths = _select_two_types(root)
    if len(pdf_paths) < 2:
        return ["TRAINING needs PDFs in at least two top-level document types"]

    from ai_search import EmbeddingModel
    from app_config import (
        get_chat_model_path,
        get_llama_server_exe,
        get_mmproj_path,
        get_model_path,
        select_backend,
        select_chat_model,
    )

    missing = []
    ocr_backend = select_backend()
    if get_llama_server_exe(ocr_backend == "cuda") is None:
        missing.append("{} OCR llama-server binary".format(ocr_backend))
    if get_model_path() is None:
        missing.append("Chandra OCR GGUF")
    if get_mmproj_path() is None:
        missing.append("Chandra mmproj GGUF")
    chat_key, chat_backend = select_chat_model()
    if get_llama_server_exe(chat_backend == "cuda") is None:
        missing.append("{} chat llama-server binary".format(chat_backend))
    if get_chat_model_path(chat_key) is None:
        missing.append("{} chat GGUF".format(chat_key))
    if not EmbeddingModel().is_available():
        missing.append("Vietnamese embedding model")
    return missing


def run_training_pipeline(root: Path = TRAINING_ROOT) -> PipelineResult:
    """Exercise the real offline product pipeline without touching app data."""
    from agent_tools import ToolExecutor
    from ai_search import EmbeddingModel, VectorStore, embed_document_chunks
    from app_config import select_backend, select_chat_model
    from background_workers import index_document_relations
    from chat_server_manager import ChatServerManager
    from document_repository import DocumentDatabase
    from llm_client import LlamaChatClient
    from rag_agent import RagAgent
    from retrieval import hybrid_search
    from vector_index import VectorIndex
    import ocr_pipeline

    pdf_paths = _select_two_types(root)
    with tempfile.TemporaryDirectory(prefix="ocr-vbhc-integration-") as temp_dir:
        temp_root = Path(temp_dir)
        original_output = ocr_pipeline.OUTPUT_DIR
        ocr_pipeline.OUTPUT_DIR = temp_root / "output"
        db = DocumentDatabase(temp_root / "documents.db")
        embedding = EmbeddingModel()
        ocr = None
        chat_server = None
        client = None
        try:
            ocr = ocr_pipeline.DocumentOCR()
            doc_ids = []
            results_by_id = {}
            for pdf_path in pdf_paths:
                result, page_detections = ocr.process_document_chandra(str(pdf_path))
                if not result or not str(result.get("Van_ban_dang_txt") or "").strip():
                    raise AssertionError("OCR returned no text for {}".format(pdf_path))
                doc_id = db.add_document(
                    str(pdf_path), result, page_count=len(page_detections or []) or 1
                )
                doc_ids.append(doc_id)
                results_by_id[doc_id] = result

            # OCR and chat models do not need to occupy memory concurrently.
            ocr.fullpage_ocr.close()
            ocr._chandra_server.stop()
            ocr = None

            if not embedding.load():
                raise AssertionError("Vietnamese embedding model failed to load")
            store = VectorStore(str(temp_root / "documents.db"))
            for doc_id in doc_ids:
                version = db.get_latest_version(doc_id)
                if embed_document_chunks(embedding, store, doc_id, version) <= 0:
                    raise AssertionError("No chunks indexed for document {}".format(doc_id))
                index_document_relations(temp_root / "documents.db", doc_id, version)

            conn = db.conn_pool.get_connection()
            vindex = VectorIndex(conn)
            vindex.sync_from_chunks(conn)
            conn.commit()

            expected_doc_id = doc_ids[0]
            expected = results_by_id[expected_doc_id]
            number = str(expected.get("So_Ki_Hieu") or "").strip()
            document_type = str(expected.get("Loai_VB") or pdf_paths[0].parent.name).strip()
            if not number:
                raise AssertionError("OCR did not extract So_Ki_Hieu from {}".format(pdf_paths[0]))
            query = "{} số {} quy định gì?".format(document_type, number)
            chunks = hybrid_search(conn, vindex, embedding, None, query, top_k=8)

            chat_key, chat_backend = select_chat_model()
            chat_server = ChatServerManager()
            chat_server.start_chat(
                timeout=180, model_key=chat_key, backend=chat_backend
            )
            client = LlamaChatClient(chat_server.url, timeout=300)
            executor = ToolExecutor(conn, vindex, embedding, None, scope_doc_ids=doc_ids)
            agent_result = RagAgent(client, executor).ask(query)
            return PipelineResult(
                pdf_paths=pdf_paths,
                hybrid_doc_ids=[chunk.doc_id for chunk in chunks],
                expected_doc_id=expected_doc_id,
                answer=agent_result.answer,
                citations=agent_result.citations,
            )
        finally:
            if client is not None:
                client.client.close()
            if chat_server is not None:
                chat_server.stop()
            if ocr is not None:
                if ocr.fullpage_ocr is not None:
                    ocr.fullpage_ocr.close()
                if ocr._chandra_server is not None:
                    ocr._chandra_server.stop()
            embedding.unload()
            db.close()
            ocr_pipeline.OUTPUT_DIR = original_output


@unittest.skipUnless(
    os.environ.get("RUN_INTEGRATION") == "1",
    "set RUN_INTEGRATION=1 to run the real TRAINING corpus pipeline",
)
class TrainingCorpusIntegrationTests(unittest.TestCase):
    def test_two_document_types_complete_ocr_index_and_cited_chat(self):
        missing = _missing_requirements(TRAINING_ROOT)
        if missing:
            self.skipTest("missing integration prerequisite(s): " + ", ".join(missing))

        result = run_training_pipeline()

        self.assertEqual(len(result.pdf_paths), 2)
        self.assertNotEqual(
            result.pdf_paths[0].parent.name, result.pdf_paths[1].parent.name
        )
        self.assertTrue(result.hybrid_doc_ids)
        self.assertIn(result.expected_doc_id, result.hybrid_doc_ids)
        self.assertTrue(result.answer.strip())
        self.assertTrue(result.citations)
        self.assertRegex(result.answer, r"\[\d+\]")


if __name__ == "__main__":
    unittest.main()
