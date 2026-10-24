import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

from chat_server_manager import ChatServerManager, build_chat_server_command


class ChatServerCommandTests(unittest.TestCase):
    def test_cuda_command_has_all_tool_calling_and_cache_flags(self):
        command = build_chat_server_command(
            Path("llama-server.exe"), Path("qwen.gguf"), 9998,
            "cuda", 8192, 6,
        )

        self.assertEqual(command, [
            "llama-server.exe", "-m", "qwen.gguf",
            "--port", "9998", "--host", "127.0.0.1",
            "-c", "8192", "-t", "6",
            "--jinja", "--reasoning-format", "none",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
            "--flash-attn", "auto",
            "--chat-template-kwargs", '{"enable_thinking": false}',
            "-np", "1", "--cache-ram", "1024",
            "-ngl", "99",
        ])

    def test_one_slot_owns_the_whole_context_and_ram_cache_is_capped(self):
        # One user: a single slot gets all n_ctx tokens instead of four
        # slots sharing them, and the prompt cache stops at 1 GiB of RAM
        # instead of the 8 GiB default (token_budget_audit.md §3.9).
        command = build_chat_server_command(
            Path("llama-server.exe"), Path("qwen.gguf"), 9998, "cpu", 8192, 8,
        )

        self.assertEqual(command[command.index("-np") + 1], "1")
        self.assertEqual(command[command.index("--cache-ram") + 1], "1024")

    def test_cpu_command_disables_gpu_and_never_adds_mmproj(self):
        command = build_chat_server_command(
            Path("llama-server.exe"), Path("qwen.gguf"), 9998,
            "cpu", 4096, 8,
        )

        self.assertEqual(command[-2:], ["-ngl", "0"])
        self.assertNotIn("--mmproj", command)
        self.assertIn("--jinja", command)
        self.assertIn("q8_0", command)
        self.assertIn('{"enable_thinking": false}', command)


class ChatServerManagerTests(unittest.TestCase):
    @patch("chat_server_manager.subprocess.Popen")
    def test_init_is_lazy(self, popen):
        manager = ChatServerManager()

        self.assertEqual(manager.url, "http://127.0.0.1:9998")
        self.assertIsNone(manager.active_model_key)
        popen.assert_not_called()

    @patch("chat_server_manager.open", new_callable=mock_open)
    @patch("chat_server_manager.get_llama_bin_dir", return_value=Path("bin/cuda"))
    @patch("chat_server_manager.get_chat_model_path", return_value=Path("qwen.gguf"))
    @patch("chat_server_manager.get_llama_server_exe", return_value=Path("llama-server.exe"))
    @patch("chat_server_manager.select_chat_model", return_value=("qwen3-4b", "cuda"))
    @patch("chat_server_manager.subprocess.Popen")
    def test_start_chat_selects_model_and_waits_for_health(
            self, popen, select_model, get_exe, get_model, get_bin, _open):
        process = MagicMock()
        process.pid = 42
        process.poll.return_value = None
        popen.return_value = process
        manager = ChatServerManager()

        with patch.object(manager, "_kill_zombies_on_port", return_value=0), \
                patch.object(manager, "_wait_healthy", return_value=True) as wait:
            manager.start_chat(timeout=7.5)

        select_model.assert_called_once_with()
        get_exe.assert_called_once_with(use_cuda=True)
        get_model.assert_called_once_with("qwen3-4b")
        get_bin.assert_called_once_with(use_cuda=True)
        self.assertEqual(manager.active_model_key, "qwen3-4b")
        self.assertEqual(manager.backend, "cuda")
        wait.assert_called_once_with(7.5)
        command = popen.call_args.args[0]
        self.assertIn("--jinja", command)
        self.assertEqual(command[command.index("-c") + 1], "16384")
        self.assertEqual(manager.active_context_size, 16384)
        self.assertEqual(command[-2:], ["-ngl", "99"])
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)

    @patch("chat_server_manager.get_llama_bin_dir", return_value=Path("bin/cpu"))
    @patch("chat_server_manager.get_chat_model_path", return_value=Path("qwen.gguf"))
    @patch("chat_server_manager.get_llama_server_exe", return_value=Path("llama-server.exe"))
    @patch("chat_server_manager.select_chat_model")
    @patch("chat_server_manager.subprocess.Popen")
    def test_explicit_model_and_backend_skip_auto_selection(
            self, popen, select_model, _get_exe, _get_model, _get_bin):
        process = MagicMock()
        process.pid = 43
        process.poll.return_value = None
        popen.return_value = process
        manager = ChatServerManager()

        with patch("chat_server_manager.open", mock_open()), \
                patch.object(manager, "_kill_zombies_on_port", return_value=0), \
                patch.object(manager, "_wait_healthy", return_value=True):
            manager.start_chat(model_key="qwen3-8b", backend="cpu")

        select_model.assert_not_called()
        self.assertEqual(manager.active_model_key, "qwen3-8b")
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("-c") + 1], "8192")
        self.assertEqual(manager.active_context_size, 8192)
        self.assertEqual(command[-2:], ["-ngl", "0"])

    @patch("app_config.detect_vram_mb", return_value=24463)
    @patch("chat_server_manager.get_llama_bin_dir", return_value=Path("bin/cuda"))
    @patch("chat_server_manager.get_chat_model_path", return_value=Path("qwen.gguf"))
    @patch("chat_server_manager.get_llama_server_exe", return_value=Path("llama-server.exe"))
    @patch("chat_server_manager.subprocess.Popen")
    def test_8b_context_is_16k_without_asking_the_gpu_again(self, popen, _exe, _model, _bin, vram):
        process = MagicMock()
        process.pid = 44
        process.poll.return_value = None
        popen.return_value = process
        manager = ChatServerManager()

        with patch("chat_server_manager.open", mock_open()), \
                patch.object(manager, "_kill_zombies_on_port", return_value=0), \
                patch.object(manager, "_wait_healthy", return_value=True):
            manager.start_chat(model_key="qwen3-8b", backend="cuda")

        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("-c") + 1], "16384")
        self.assertEqual(manager.active_context_size, 16384)
        vram.assert_not_called()

    def test_context_size_is_unknown_until_started(self):
        self.assertIsNone(ChatServerManager().active_context_size)


if __name__ == "__main__":
    unittest.main()
