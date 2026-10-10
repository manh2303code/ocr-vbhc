"""Lazy lifecycle manager for the dedicated legal-chat llama-server."""
import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from app_config import (
    CHAT_MODELS,
    CHAT_PORT,
    LLAMA_SERVER_HOST,
    get_chat_model_path,
    get_llama_bin_dir,
    get_llama_server_exe,
    select_chat_model,
)
from llama_server_manager import LlamaServerManager


logger = logging.getLogger("ChatServer")


def build_chat_server_command(server_exe: Path, model_path: Path, port: int,
                              backend: str, ctx: int,
                              threads: int) -> List[str]:
    """Build the side-effect-free llama-server command for legal chat."""
    return [
        str(server_exe), "-m", str(model_path),
        "--port", str(port), "--host", LLAMA_SERVER_HOST,
        "-c", str(ctx), "-t", str(threads),
        "--jinja", "--reasoning-format", "none",
        "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
        "--flash-attn", "auto",
        "--chat-template-kwargs", '{"enable_thinking": false}',
        "-ngl", "99" if backend == "cuda" else "0",
    ]


class ChatServerManager(LlamaServerManager):
    """Start the second llama-server only when chat is first requested."""

    def __init__(self, port: int = CHAT_PORT):
        super().__init__(port=port)
        self._active_model_key: Optional[str] = None

    @property
    def active_model_key(self) -> Optional[str]:
        return self._active_model_key

    def start_chat(self, timeout: float = 120.0,
                   model_key: Optional[str] = None,
                   backend: Optional[str] = None) -> None:
        """Select a Qwen profile, launch it, and wait for its health endpoint."""
        if self.is_running:
            logger.info("Chat llama-server already running on port %d", self._port)
            return

        self._kill_zombies_on_port()
        if model_key is None or backend is None:
            selected_key, selected_backend = select_chat_model()
            model_key = model_key or selected_key
            backend = backend or selected_backend

        profile = CHAT_MODELS.get(model_key)
        if profile is None:
            raise ValueError("Unknown chat model: {}".format(model_key))
        if backend not in ("cuda", "cpu"):
            raise ValueError("Unknown chat backend: {}".format(backend))

        use_cuda = backend == "cuda"
        server_exe = get_llama_server_exe(use_cuda=use_cuda)
        if not server_exe:
            raise FileNotFoundError(
                "llama-server not found for chat backend '{}'".format(backend)
            )
        model_path = get_chat_model_path(model_key)
        if not model_path:
            raise FileNotFoundError("Chat model GGUF not found for {}".format(model_key))
        bin_dir = get_llama_bin_dir(use_cuda=use_cuda)
        if not bin_dir:
            raise FileNotFoundError(
                "llama-server binary directory not found for '{}'".format(backend)
            )

        ctx = profile["ctx_gpu" if use_cuda else "ctx_cpu"]
        threads = min(8, os.cpu_count() or 4)
        command = build_chat_server_command(
            server_exe, model_path, self._port, backend, ctx, threads
        )
        env = os.environ.copy()
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        log_dir = Path(__file__).resolve().parent / "logs"
        log_dir.mkdir(exist_ok=True)
        self._log_file = open(
            log_dir / "llama_server_{}.log".format(self._port),
            "w", encoding="utf-8",
        )
        self._backend = backend
        logger.info("Starting chat model %s on port %d (%s)",
                    model_key, self._port, backend)
        try:
            self._process = subprocess.Popen(
                command,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=self._log_file,
                creationflags=creation_flags,
            )
        except Exception:
            self._log_file.close()
            self._log_file = None
            self._backend = None
            raise

        if not self._wait_healthy(timeout):
            self.stop()
            raise TimeoutError(
                "Chat llama-server not healthy after {}s".format(timeout)
            )
        self._active_model_key = model_key
        logger.info("Chat llama-server ready at %s (PID: %s)",
                    self._url, self._process.pid)
