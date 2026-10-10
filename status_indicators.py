"""
Lightweight status bar indicators — live system feedback.

Performance:
- Single QTimer (10s interval) drives all indicators — shared polling
- Window-focus gated: skip polling when app inactive (saves CPU)
- No nvidia-smi shell per indicator — one subprocess call, parsed once
- Widgets: QLabel only (no custom paint, no repolish per update)

Indicators exposed:
- LlamaStatusIndicator — dot + "Chandra · Online/Offline"
- VramIndicator       — compact bar + "4.2/8 GB" text
- WorkerCountIndicator — "2 active" badge (hide when 0)
- DocCountIndicator   — "57 văn bản"
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QProgressBar, QSizePolicy,
)

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


# ── Dot widget (reusable) ──────────────────────────────────────────
class _StatusDot(QWidget):
    """8px colored dot painted in paintEvent. Change color via set_color."""

    def __init__(self, color: str = "#5F687C", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(10, 10)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(self._color)
        p.setPen(Qt.NoPen)
        p.drawEllipse(1, 1, 8, 8)
        p.end()


# ── Llama-server indicator ─────────────────────────────────────────
class LlamaStatusIndicator(QWidget):
    """Green/red dot + 'Chandra · Online/Offline' text."""

    def __init__(self, server_url: str = "http://127.0.0.1:9999",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._url = server_url
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)
        self._dot = _StatusDot("#5F687C")
        self._label = QLabel("Chandra · Đang kiểm tra")
        self._label.setStyleSheet("color: #6B7280; font-size: 9pt;")
        layout.addWidget(self._dot)
        layout.addWidget(self._label)
        layout.addStretch()

    def refresh(self) -> None:
        """Quick health check. Non-blocking — uses short 1s timeout."""
        try:
            r = httpx.get(f"{self._url}/health", timeout=1.0)
            if r.status_code == 200:
                self._dot.set_color("#16A34A")
                self._label.setText("Chandra · Online")
                self._label.setStyleSheet("color: #16A34A; font-size: 9pt; font-weight: 500;")
                return
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
            pass
        except Exception as e:
            logger.debug(f"LlamaStatus refresh error: {e}")
        # Offline/unreachable
        self._dot.set_color("#DC2626")
        self._label.setText("Chandra · Offline")
        self._label.setStyleSheet("color: #DC2626; font-size: 9pt;")

    def set_online(self, online: bool) -> None:
        self._dot.set_color("#16A34A" if online else "#DC2626")
        self._label.setText(f"Chandra · {'Online' if online else 'Offline'}")
        self._label.setStyleSheet(
            f"color: {'#16A34A' if online else '#DC2626'}; font-size: 9pt;"
            + (" font-weight: 500;" if online else "")
        )


# ── VRAM indicator ─────────────────────────────────────────────────
class VramIndicator(QWidget):
    """Compact bar + text '4.2/8 GB VRAM'. Uses nvidia-smi."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFixedSize(50, 6)
        self._bar.setTextVisible(False)
        self._bar.setStyleSheet(
            "QProgressBar { background: #D8D1C3; border: none; border-radius: 2px; }"
            "QProgressBar::chunk { background: #24432D; border-radius: 2px; }"
        )
        self._label = QLabel("VRAM · –")
        self._label.setStyleSheet("color: #6B7280; font-size: 9pt;")
        layout.addWidget(self._bar)
        layout.addWidget(self._label)
        layout.addStretch()

    def refresh(self) -> None:
        """Query nvidia-smi. ~30ms subprocess. Skip if unavailable."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2, creationflags=0x08000000
            )
            if result.returncode != 0:
                self._label.setText("VRAM · n/a")
                return
            # Parse: "1234, 8188"
            line = result.stdout.strip().split("\n")[0]
            used, total = (int(x.strip()) for x in line.split(","))
            pct = int(100 * used / total) if total > 0 else 0
            self._bar.setValue(pct)
            gb_used = used / 1024
            gb_total = total / 1024
            self._label.setText(f"VRAM · {gb_used:.1f}/{gb_total:.0f} GB")
            # Color threshold
            if pct >= 85:
                color = "#DC2626"  # red
            elif pct >= 60:
                color = "#D97706"  # amber
            else:
                color = "#24432D"  # olive
            self._bar.setStyleSheet(
                "QProgressBar { background: #D8D1C3; border: none; border-radius: 2px; }"
                f"QProgressBar::chunk {{ background: {color}; border-radius: 2px; }}"
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self._label.setText("VRAM · n/a")
        except Exception as e:
            logger.debug(f"VRAM refresh error: {e}")

    def set_usage(self, usage) -> None:
        if not usage:
            self._bar.setValue(0)
            self._label.setText("VRAM · n/a")
            return
        used, total = usage
        pct = int(100 * used / total) if total else 0
        self._bar.setValue(pct)
        self._label.setText(f"VRAM · {used / 1024:.1f}/{total / 1024:.0f} GB")
        color = "#DC2626" if pct >= 85 else "#D97706" if pct >= 60 else "#24432D"
        self._bar.setStyleSheet(
            "QProgressBar { background: #D8D1C3; border: none; border-radius: 2px; }"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 2px; }}"
        )


# ── Worker count indicator ─────────────────────────────────────────
class WorkerCountIndicator(QWidget):
    """'N active' badge. Callable set_count(n) from worker lifecycle hooks."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)
        self._count = 0
        self._label = QLabel("")
        self._label.setStyleSheet("color: #6B7280; font-size: 9pt;")
        layout.addWidget(self._label)
        layout.addStretch()

    def set_count(self, n: int) -> None:
        self._count = n
        if n <= 0:
            self._label.setText("")
        elif n == 1:
            self._label.setText("1 tác vụ")
            self._label.setStyleSheet("color: #D97706; font-size: 9pt; font-weight: 500;")
        else:
            self._label.setText(f"{n} tác vụ")
            self._label.setStyleSheet("color: #D97706; font-size: 9pt; font-weight: 500;")


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
        """Register any widget with a refresh() method."""
        if hasattr(indicator, "refresh"):
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
