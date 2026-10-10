import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication

from main_window_1 import BatchProcessWorker, OCRWorker


class _FailingOCR:
    def process_document_chandra(self, _path, progress_callback=None):
        raise RuntimeError("model request failed")


class _EmptyOCR:
    def process_document_chandra(self, _path, progress_callback=None):
        return {}, []


class _SuccessfulOCR:
    def process_document_chandra(self, _path, progress_callback=None):
        if progress_callback:
            progress_callback(1.0, 1, "done")
        return {"Van_ban_dang_txt": "nội dung thật"}, [[]]


class OCRWorkerOutcomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def _source_file(self, directory):
        path = os.path.join(directory, "source.pdf")
        with open(path, "wb") as stream:
            stream.write(b"test")
        return path

    def test_single_worker_emits_error_not_finished_on_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = OCRWorker(_FailingOCR(), self._source_file(tmp))
            errors, finished = [], []
            worker.error.connect(errors.append)
            worker.finished.connect(lambda *args: finished.append(args))

            worker.run()

            self.assertTrue(errors)
            self.assertEqual([], finished)

    def test_single_worker_emits_error_not_finished_when_source_is_missing(self):
        worker = OCRWorker(_EmptyOCR(), "Z:/path/that/does/not/exist.pdf")
        errors, finished = [], []
        worker.error.connect(errors.append)
        worker.finished.connect(lambda *args: finished.append(args))

        worker.run()

        self.assertTrue(errors)
        self.assertEqual([], finished)

    def test_single_worker_rejects_empty_ocr_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = OCRWorker(_EmptyOCR(), self._source_file(tmp))
            errors, finished = [], []
            worker.error.connect(errors.append)
            worker.finished.connect(lambda *args: finished.append(args))

            worker.run()

            self.assertTrue(errors)
            self.assertEqual([], finished)

    def test_batch_worker_does_not_return_empty_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = BatchProcessWorker(_EmptyOCR(), [self._source_file(tmp)])
            batches = []
            worker.finished.connect(batches.append)

            worker.run()

            self.assertEqual([[]], batches)

    def test_batch_can_emit_each_document_without_retaining_entire_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._source_file(tmp)
            worker = BatchProcessWorker(
                _SuccessfulOCR(), [path], collect_results=False
            )
            ready, batches = [], []
            worker.document_ready.connect(
                lambda source, result, pages: ready.append((source, result, pages))
            )
            worker.finished.connect(batches.append)

            worker.run()

            self.assertEqual(path, ready[0][0])
            self.assertEqual("nội dung thật", ready[0][1]["Van_ban_dang_txt"])
            self.assertEqual([[]], ready[0][2])
            self.assertEqual([[]], batches)


if __name__ == "__main__":
    unittest.main()
