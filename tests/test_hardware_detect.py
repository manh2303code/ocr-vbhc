import unittest

from hardware_detect import HardwareInfo, calculate_optimal_workers
from ocr_pipeline import has_complete_page_ocr


class HardwareWorkerTests(unittest.TestCase):
    def _hardware(self, vram_mb):
        return HardwareInfo(
            cpu_cores=16, cpu_threads=16,
            ram_total_mb=65536, ram_available_mb=50000,
            gpu_name="RTX", gpu_vram_total_mb=vram_mb,
            gpu_vram_free_mb=vram_mb - 4000, gpu_available=True,
        )

    def test_24gb_gpu_is_capped_at_two_stable_ocr_workers(self):
        self.assertEqual(2, calculate_optimal_workers(self._hardware(24463)))

    def test_partial_page_failure_never_counts_as_successful_ocr(self):
        self.assertTrue(has_complete_page_ocr(["<div>trang 1</div>", "trang 2"]))
        self.assertFalse(has_complete_page_ocr(["<div>trang 1</div>", ""]))
        self.assertFalse(has_complete_page_ocr([]))


if __name__ == "__main__":
    unittest.main()
