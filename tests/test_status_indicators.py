import subprocess
import unittest
from pathlib import Path

import status_indicators


ROOT = Path(__file__).resolve().parents[1]


class _Response:
    status_code = 200


class _CommandResult:
    returncode = 0
    stdout = "2048, 8192\n"


class StatusIndicatorTests(unittest.TestCase):
    def test_collect_status_returns_plain_background_safe_data(self):
        result = status_indicators.collect_status(
            "http://localhost:9999",
            http_get=lambda *_args, **_kwargs: _Response(),
            run_command=lambda *_args, **_kwargs: _CommandResult(),
        )

        self.assertEqual({"online": True, "vram": (2048, 8192)}, result)

    def test_collect_status_tolerates_unavailable_services(self):
        def fail(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("probe", 1)

        self.assertEqual(
            {"online": False, "vram": None},
            status_indicators.collect_status("http://localhost:9999", fail, fail),
        )

    def test_main_tracks_ocr_batch_repair_and_embedding_workers(self):
        source = (ROOT / "main_window_1.py").read_text(encoding="utf-8-sig")
        for worker in ("ocr_worker", "batch_worker", "repair_worker"):
            self.assertIn(f"self._track_worker(self.{worker})", source)
        self.assertIn("self._track_worker(worker)", source)


if __name__ == "__main__":
    unittest.main()
