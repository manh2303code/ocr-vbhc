import unittest

import app_config


class SelectChatModelTests(unittest.TestCase):
    def test_high_vram_gets_8b_cuda(self):
        self.assertEqual(
            app_config.select_chat_model(vram_mb=12000),
            ("qwen3-8b", "cuda"),
        )

    def test_mid_vram_gets_4b_cuda(self):
        self.assertEqual(
            app_config.select_chat_model(vram_mb=8000),
            ("qwen3-4b", "cuda"),
        )

    def test_no_gpu_gets_4b_cpu(self):
        self.assertEqual(
            app_config.select_chat_model(vram_mb=0),
            ("qwen3-4b", "cpu"),
        )

    def test_env_override(self):
        import os

        os.environ["CHAT_MODEL"] = "qwen3-8b"
        try:
            key, _ = app_config.select_chat_model(vram_mb=0)
            self.assertEqual(key, "qwen3-8b")
        finally:
            del os.environ["CHAT_MODEL"]


if __name__ == "__main__":
    unittest.main()
