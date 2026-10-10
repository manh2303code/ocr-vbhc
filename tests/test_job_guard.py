import unittest

from main_window_1 import _has_running_worker


class _Worker:
    def __init__(self, running):
        self.running = running

    def isRunning(self):
        return self.running


class JobGuardTests(unittest.TestCase):
    def test_detects_active_single_or_batch_worker(self):
        self.assertTrue(_has_running_worker(None, _Worker(True)))
        self.assertTrue(_has_running_worker(_Worker(True), _Worker(False)))
        self.assertFalse(_has_running_worker(None, _Worker(False)))


if __name__ == "__main__":
    unittest.main()
