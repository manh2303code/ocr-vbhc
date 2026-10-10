"""
GPU keep-alive ping for llama-server.

Problem observed 2026-04-18: RTX 4070 Laptop idles down to 210 MHz
(~10% of 2175 MHz boost) within ~1s of inference completion. Next
request pays a 0.5-1.5s clock-ramp penalty in first-token latency.
Measured impact: 42 tok/s cold vs 62 tok/s warm.

Solution: a low-cost /completion request every N seconds (default 6s)
that forces the GPU to stay in P0 power state. The request is tiny —
1 token prediction, no prompt cache impact — so cost is ≈30ms per ping.

Wire from MainWindow after OCR finishes or the server is confirmed
healthy. Stop when app closes or server goes down.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
from PyQt5.QtCore import QObject, QThread, pyqtSignal

logger = logging.getLogger("GPUKeepAlive")


class GPUKeepAliveWorker(QThread):
    """Background thread that pings llama-server every interval_s seconds.

    The ping is a 1-token /completion that touches the GPU briefly. We
    deliberately skip the /health endpoint — it doesn't wake the GPU.
    Uses httpx.Client with tight timeout so a stuck server doesn't
    compound into a pile of hanging requests.
    """

    error_occurred = pyqtSignal(str)

    def __init__(self, url: str, interval_s: float = 6.0,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._url = url
        self._interval_s = interval_s
        self._stop = False
        self._client: Optional[httpx.Client] = None

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        # Single client reused — avoids per-request TCP handshake cost.
        try:
            self._client = httpx.Client(base_url=self._url, timeout=3.0)
        except Exception as e:
            logger.error(f"Failed to create httpx client: {e}")
            self.error_occurred.emit(str(e))
            return

        consecutive_failures = 0
        logger.info(f"GPU keep-alive started (interval={self._interval_s}s)")

        while not self._stop:
            try:
                # Tiny single-token ping — 30ms on warm GPU, <150ms cold.
                r = self._client.post(
                    "/completion",
                    json={
                        "prompt": ".",
                        "n_predict": 1,
                        "stream": False,
                        # Force deterministic short output to avoid
                        # large outputs corrupting KV cache.
                        "temperature": 0,
                        "cache_prompt": False,
                    },
                )
                if r.status_code == 200:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    logger.debug(f"Keep-alive HTTP {r.status_code}")
            except httpx.ConnectError:
                # Server probably down — back off so we don't spam errors.
                consecutive_failures += 1
                if consecutive_failures == 3:
                    logger.warning("llama-server unreachable from keep-alive")
            except httpx.TimeoutException:
                consecutive_failures += 1
                logger.debug("Keep-alive timeout — server busy (expected)")
            except Exception as e:
                consecutive_failures += 1
                logger.debug(f"Keep-alive error: {e}")

            # Stop pinging if the server has been down for a minute.
            if consecutive_failures >= 10:
                logger.warning(
                    "Keep-alive suspending — server down for 10 consecutive pings"
                )
                time.sleep(30)  # long backoff before next attempt
                consecutive_failures = 0
                continue

            # Cooperative sleep so stop() can interrupt quickly.
            end = time.monotonic() + self._interval_s
            while not self._stop and time.monotonic() < end:
                time.sleep(0.2)

        try:
            if self._client is not None:
                self._client.close()
        except Exception:
            pass
        logger.info("GPU keep-alive stopped")
