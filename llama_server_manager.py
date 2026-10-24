"""
Manage llama-server as a background process.
Handles start, stop, health check, and GPU/CPU auto-selection.
"""
import atexit
import os
import subprocess
import time
import logging
from pathlib import Path
from typing import Optional

import httpx

import app_config
from app_config import (
    get_llama_server_exe, get_llama_bin_dir, get_model_path, get_mmproj_path,
    select_backend, LLAMA_SERVER_PORT, LLAMA_SERVER_HOST, LLAMA_SERVER_URL,
    ACTIVE_OCR_MODEL,
)

logger = logging.getLogger("LlamaServer")


def build_server_command(server_exe: Path, model_path: Path, mmproj_path: Path,
                         port: int, backend: str, parallel_slots: int = 1,
                         model_key: Optional[str] = None) -> list:
    """llama-server argv for the Chandra OCR server.

    ``parallel_slots`` must equal the number of OCR workers that send pages
    concurrently: each worker then owns a slot with its own context instead
    of sharing one pool (app_config.ocr_server_context_args).
    """
    threads = str(min(8, os.cpu_count() or 4))
    cmd = [
        str(server_exe),
        "-m", str(model_path),
        "--mmproj", str(mmproj_path),
        "--port", str(port),
        "--host", LLAMA_SERVER_HOST,
    ]
    cmd.extend(app_config.ocr_server_context_args(parallel_slots, model_key))
    cmd.extend(["-t", threads])

    # Leaves any <think> block inside message.content (it does NOT remove
    # reasoning); llama_ocr strips it, and requests disable thinking.
    cmd.extend(["--reasoning-format", "none"])

    if backend == "cuda":
        cmd.extend(["-ngl", "99"])  # offload all layers to GPU
    else:
        # CPU-only mode: explicitly disable GPU offload.
        # Benchmark 2026-04-17: Chandra Q4 CPU ~90s/page at 8 threads.
        # Using more threads than physical cores hurts due to cache thrash.
        cmd.extend(["-ngl", "0"])

    if app_config.LLAMA_FLASH_ATTN:
        cmd.extend(["--flash-attn", "auto"])
    return cmd


class LlamaServerManager:
    """Manage llama-server lifecycle as a background process."""

    def __init__(self, port: int = LLAMA_SERVER_PORT):
        self._process: Optional[subprocess.Popen] = None
        self._port = port
        self._backend: Optional[str] = None
        self._url = f"http://{LLAMA_SERVER_HOST}:{port}"
        atexit.register(self.stop)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stop()

    @property
    def url(self) -> str:
        return self._url

    @property
    def backend(self) -> Optional[str]:
        return self._backend

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _kill_zombies_on_port(self) -> int:
        """Find and kill any llama-server.exe holding our port.

        Reason: when the app crashes or is force-closed (Task Manager,
        Ctrl+C in terminal, unclean exit) the atexit hook doesn't run and
        the child process lingers, holding VRAM. On next start we pile up
        zombies until GPU saturates → token rate collapses.

        Returns count of processes killed.
        """
        killed = 0
        try:
            import psutil
        except ImportError:
            logger.debug("psutil unavailable — skipping zombie scan")
            return 0

        try:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    name = (proc.info.get('name') or '').lower()
                    if name in ('llama-server', 'llama-server.exe'):
                        # Check if this instance binds our port.
                        conns = proc.net_connections(kind='inet')
                        if any(c.laddr.port == self._port for c in conns if c.laddr):
                            logger.warning(
                                f"Killing zombie llama-server PID {proc.pid} on port {self._port}"
                            )
                            proc.kill()
                            try:
                                proc.wait(timeout=3)
                            except psutil.TimeoutExpired:
                                pass
                            killed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            logger.warning(f"Zombie scan failed: {e}")
        if killed:
            # Small delay so OS releases the port fully before we bind.
            time.sleep(0.5)
        return killed

    def start(self, timeout: float = 60.0, backend: Optional[str] = None,
              model_key: Optional[str] = None, parallel_slots: int = 1) -> None:
        """Start llama-server and wait until healthy.

        Args:
            timeout: Max seconds to wait for health check.
            backend: 'cuda' or 'cpu'. Auto-detected if None.
            model_key: OCR model profile from app_config.OCR_MODELS.
                       Defaults to ACTIVE_OCR_MODEL.
            parallel_slots: concurrent OCR workers the server must serve.
        """
        if self.is_running:
            logger.info("llama-server already running on port %d", self._port)
            return

        # Kill any pre-existing zombies from crashed sessions.
        # Observed 2026-04-18: up to 3 orphan llama-server.exe holding
        # port 9999 simultaneously, eating 3-7 GB VRAM each.
        killed = self._kill_zombies_on_port()
        if killed:
            logger.info(f"Cleaned up {killed} zombie llama-server(s) before start")

        key = model_key or ACTIVE_OCR_MODEL
        self._backend = backend or select_backend()
        logger.info(f"Starting {key} on port {self._port} ({self._backend})")

        server_exe = get_llama_server_exe(use_cuda=(self._backend == "cuda"))
        if not server_exe:
            raise FileNotFoundError(f"llama-server not found for backend '{self._backend}'")

        model_path = get_model_path(model_key=key)
        if not model_path:
            raise FileNotFoundError(f"Model GGUF not found for {key}")

        mmproj_path = get_mmproj_path(model_key=key)
        if not mmproj_path:
            raise FileNotFoundError(f"mmproj GGUF not found for {key}")

        bin_dir = get_llama_bin_dir(use_cuda=(self._backend == "cuda"))

        logger.info(f"Server: {server_exe}")
        logger.info(f"Model:  {model_path} ({model_path.stat().st_size / 1024**2:.0f}MB)")
        logger.info(f"MMProj: {mmproj_path}")

        cmd = build_server_command(
            server_exe, model_path, mmproj_path, self._port, self._backend,
            parallel_slots=parallel_slots, model_key=key,
        )
        logger.info("OCR server context: %s", " ".join(
            app_config.ocr_server_context_args(parallel_slots, key)
        ))

        # Environment with DLL paths
        env = os.environ.copy()
        env['PATH'] = str(bin_dir) + os.pathsep + env.get('PATH', '')

        # Launch — stderr to log file for diagnosability
        logger.info(f"Starting llama-server on port {self._port}...")
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW

        log_dir = Path(__file__).parent / "logs"
        log_dir.mkdir(exist_ok=True)
        log_name = f"llama_server_{self._port}.log"
        self._log_file = open(log_dir / log_name, "w", encoding="utf-8")

        self._process = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.DEVNULL,
            stderr=self._log_file,
            creationflags=creation_flags,
        )

        # Wait for health
        if not self._wait_healthy(timeout):
            self.stop()
            raise TimeoutError(f"llama-server not healthy after {timeout}s")

        logger.info(f"llama-server ready at {self._url} (PID: {self._process.pid})")

    def _wait_healthy(self, timeout: float) -> bool:
        """Poll /health endpoint until server is ready.

        2026-04-18: sleep between polls is split into 50ms chunks that yield
        to the Qt event loop via QCoreApplication.processEvents(). This lets
        the intro → main loading overlay (spinner, progress bar, fade
        animations) keep rendering at ~20 fps during the 2-5s server warmup,
        instead of freezing. Falls back to plain time.sleep when Qt isn't
        available (e.g. headless tests).
        """
        # Try to access Qt event loop for non-blocking wait — optional.
        try:
            from PyQt5.QtCore import QCoreApplication
            _qt_app = QCoreApplication.instance()
        except ImportError:
            _qt_app = None

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_running:
                logger.error("llama-server process died during startup")
                return False
            try:
                r = httpx.get(f"{self._url}/health", timeout=2)
                if r.status_code == 200:
                    data = r.json()
                    status = data.get("status", "")
                    if status == "ok":
                        return True
                    logger.debug(f"Health status: {status}")
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
                pass

            # Cooperative wait ≈ 1s, yielding to Qt every 50ms so UI animates.
            chunk_end = time.monotonic() + 1.0
            while time.monotonic() < chunk_end:
                if _qt_app is not None:
                    _qt_app.processEvents()
                time.sleep(0.05)
        return False

    def stop(self) -> None:
        """Graceful shutdown of llama-server."""
        if self._process is None:
            return

        logger.info("Stopping llama-server...")
        try:
            self._process.terminate()
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("Force killing llama-server")
            self._process.kill()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.error("llama-server did not die after kill — zombie process")
        except Exception as e:
            logger.error(f"Error stopping llama-server: {e}")
        finally:
            self._process = None
            if hasattr(self, '_log_file') and self._log_file:
                self._log_file.close()
                self._log_file = None
        logger.info("llama-server stopped")

    def check_health(self) -> bool:
        """Quick health check."""
        try:
            r = httpx.get(f"{self._url}/health", timeout=3)
            return r.status_code == 200 and r.json().get("status") == "ok"
        except Exception:
            return False

    def get_model_info(self) -> Optional[dict]:
        """Get loaded model info from server."""
        try:
            r = httpx.get(f"{self._url}/v1/models", timeout=5)
            if r.status_code == 200:
                models = r.json().get("data", [])
                return models[0] if models else None
        except Exception:
            return None
        return None
