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


class ChatContextSizeTests(unittest.TestCase):
    """Context per model and VRAM (research/token_budget_audit.md §3.9)."""

    def test_both_models_get_16k_on_the_gpu(self):
        # 8B at 32768 was measured against 16384 on the two 10-turn
        # conversations: no failure, cut or trim either way, and 1224 MiB
        # more KV (token-limits_results.md §8.5). It keeps 16384.
        self.assertEqual(app_config.chat_context_size("qwen3-8b", "cuda"), 16384)
        self.assertEqual(app_config.chat_context_size("qwen3-4b", "cuda"), 16384)

    def test_cpu_gets_8k_whatever_the_model(self):
        self.assertEqual(app_config.chat_context_size("qwen3-4b", "cpu"), 8192)
        self.assertEqual(app_config.chat_context_size("qwen3-8b", "cpu"), 8192)


if __name__ == "__main__":
    unittest.main()
