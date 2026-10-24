"""
Lightweight status bar indicators — live system feedback.

Performance:
- Single QTimer (10s interval) drives all indicators — shared polling
- Window-focus gated: skip polling when app inactive (saves CPU)
- The probe (HTTP health + nvidia-smi) runs on a worker thread
- Widgets: small QLabels and an 8px painted dot

2026-10 minimal redesign: no "Chandra · Online" / "VRAM · –" strings on
screen. Each indicator is a few separate quiet parts styled by the theme
stylesheet (objectNames status_*), and says what it means in a tooltip.
Indicators with nothing to report hide.

Indicators exposed:
- LlamaStatusIndicator — dot + "Chandra" (+ "không phản hồi" when down)
- VramIndicator        — thin bar + "4.2/8 GB"; hidden without nvidia-smi
- WorkerCountIndicator — "2 tác vụ"; hidden when 0
"""
from __future__ import annotations

import logging
import subprocess
from typing import Optional

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QWidget, QLabel, QHBoxLayout, QProgressBar

import httpx

logger = logging.getLogger("StatusIndicators")


def collect_status(server_url, http_get=httpx.get, run_command=subprocess.run):
    """Collect system status without touching Qt widgets (worker-thread safe)."""
    try:
        online = http_get(f"{server_url}/health", timeout=1.0).status_code == 200
    except Exception:
        online = False
    try:
        result = run_command(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, creationflags=0x08000000,
        )
        values = tuple(int(value.strip()) for value in result.stdout.splitlines()[0].split(","))
        vram = values if result.returncode == 0 and len(values) == 2 else None
    except Exception:
        vram = None
    return {"online": online, "vram": vram}


class StatusProbeWorker(QThread):
    result_ready = pyqtSignal(object)

    def __init__(self, server_url, parent=None):
        super().__init__(parent)
        self.server_url = server_url

    def run(self):
        self.result_ready.emit(collect_status(self.server_url))


def _token(name: str) -> str:
    """A colour of the current theme (read at paint time, so a theme switch
    needs no callback: the stylesheet change repaints every widget)."""
    from theme_manager_v2 import ThemeManager
    from theme_tokens import colors_for
    return colors_for(ThemeManager.instance().current)[name]


def _repolish(widget: QWidget) -> None:
    """Re-read the stylesheet after a dynamic property changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# ── Dot widget (reusable) ──────────────────────────────────────────
class _StatusDot(QWidget):
    """A 6px dot in a theme token colour. Change it via set_token."""

    def __init__(self, token: str = "text_muted", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._token = token
        self.setFixedSize(8, 8)

    def set_token(self, token: str) -> None:
        self._token = token
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(_token(self._token)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(1, 1, 6, 6)
        p.end()


# ── OCR server indicator ───────────────────────────────────────────
class LlamaStatusIndicator(QWidget):
    """The Chandra OCR server: a dot and its name.

    The dot is green while the server answers, red when it does not, grey
    while the first check runs. The state is spelled out in the tooltip,
    and on screen only when something is wrong.
    """

    # online -> (dot token, words on screen, tooltip)
    STATES = {
        None: ("text_muted", "", "Máy chủ OCR Chandra\nĐang kiểm tra"),
        True: ("success", "", "Máy chủ OCR Chandra\nĐang chạy"),
        False: ("danger", "không phản hồi", "Máy chủ OCR Chandra\nKhông phản hồi"),
    }

    def __init__(self, server_url: str = "http://127.0.0.1:9999",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._url = server_url
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(5)
        self._dot = _StatusDot()
        self._label = QLabel("Chandra")
        self._label.setObjectName("status_llama")
        self._state = QLabel("")
        self._state.setObjectName("status_llama_state")
        layout.addWidget(self._dot)
        layout.addWidget(self._label)
        layout.addWidget(self._state)
        self.set_online(None)

    def set_online(self, online) -> None:
        token, words, tip = self.STATES[None if online is None else bool(online)]
        self._dot.set_token(token)
        self._state.setText(words)
        self._state.setVisible(bool(words))
        self.setToolTip(tip)


# ── VRAM indicator ─────────────────────────────────────────────────
class VramIndicator(QWidget):
    """GPU memory: a thin bar and "4.2/8 GB"; "VRAM" and the percentage are
    in the tooltip. Hidden until nvidia-smi answers."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(6)
        self._bar = QProgressBar()
        self._bar.setObjectName("status_vram_bar")
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFixedSize(40, 4)
        self._bar.setTextVisible(False)
        self._label = QLabel("")
        self._label.setObjectName("status_vram")
        layout.addWidget(self._bar, 0, Qt.AlignVCenter)
        layout.addWidget(self._label)
        self.setVisible(False)

    def set_usage(self, usage) -> None:
        if not usage:
            self.setVisible(False)
            return
        used, total = usage
        pct = int(100 * used / total) if total else 0
        self._bar.setValue(pct)
        level = "high" if pct >= 85 else "mid" if pct >= 60 else "low"
        if self._bar.property("level") != level:
            self._bar.setProperty("level", level)
            _repolish(self._bar)
        self._label.setText(f"{used / 1024:.1f}/{total / 1024:.0f} GB")
        self.setToolTip(
            f"Bộ nhớ GPU (VRAM) đang dùng\n{used / 1024:.1f} trên {total / 1024:.0f} GB, {pct}%"
        )
        self.setVisible(True)


# ── Worker count indicator ─────────────────────────────────────────
class WorkerCountIndicator(QWidget):
    """'N tác vụ' while background work runs; hidden when there is none."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(4)
        self._count = 0
        self._label = QLabel("")
        self._label.setObjectName("status_workers")
        layout.addWidget(self._label)
        self.setToolTip("Số tác vụ nền đang chạy, ví dụ OCR hoặc lập chỉ mục")
        self.setVisible(False)

    def set_count(self, n: int) -> None:
        self._count = n
        self._label.setText(f"{n} tác vụ" if n > 0 else "")
        self.setVisible(n > 0)


# ── Coordinator: single timer, refreshes all ───────────────────────
class StatusIndicatorCoordinator(QObject):
    """Owns a QTimer and refreshes all registered indicators.

    Attach to MainWindow. Call start() after llama-server ready.
    Pauses polling when window not focused (saves CPU when minimized).
    """

    POLL_INTERVAL_MS = 10_000  # 10s

    def __init__(self, parent_window: QWidget):
        super().__init__(parent_window)
        self._window = parent_window
        self._indicators: list = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._worker = None

    def register(self, indicator) -> None:
        """Register a widget with set_online() and/or set_usage()."""
        if hasattr(indicator, "set_online") or hasattr(indicator, "set_usage"):
            self._indicators.append(indicator)

    def start(self) -> None:
        # Immediate tick so values populate before first timer interval.
        self._tick()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(2500)

    def _tick(self) -> None:
        # Skip polling when window not active (saves CPU when minimized).
        if not self._window.isActiveWindow() and not self._window.isVisible():
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = StatusProbeWorker("http://127.0.0.1:9999", self)
        self._worker.result_ready.connect(self._apply_result)
        self._worker.finished.connect(
            lambda worker=self._worker: self._release_worker(worker)
        )
        self._worker.start()

    def _release_worker(self, worker) -> None:
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def _apply_result(self, result) -> None:
        for indicator in self._indicators:
            if hasattr(indicator, "set_online"):
                indicator.set_online(result["online"])
            if hasattr(indicator, "set_usage"):
                indicator.set_usage(result["vram"])
