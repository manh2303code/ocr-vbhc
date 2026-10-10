import unittest

from main_window_1 import MainWindow


class _DB:
    def create_new_version(self, _doc_id, _updates, modified_by=None):
        return 2


class _Editor:
    def update_suggestions(self, _db):
        pass


class _StatusBar:
    def showMessage(self, *_args):
        pass


class _Window:
    current_doc_id = 7
    current_user = {"username": "tester"}
    db = _DB()
    ocr_editor = _Editor()

    def __init__(self):
        self.reindexed = []

    def load_documents(self):
        pass

    def show_document(self, _doc_id):
        pass

    def _dispatch_embedding(self, doc_id):
        self.reindexed.append(doc_id)

    def statusBar(self):
        return _StatusBar()


class ManualEditReindexTests(unittest.TestCase):
    def test_successful_manual_version_dispatches_reindex(self):
        window = _Window()

        MainWindow.update_document(window, 7, {"so_ki_hieu": "02/CV"})

        self.assertEqual([7], window.reindexed)


if __name__ == "__main__":
    unittest.main()
