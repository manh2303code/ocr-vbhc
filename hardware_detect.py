"""
Hardware detection + smart parallel OCR configuration.

Detects CPU/GPU/RAM at runtime and calculates optimal number
of parallel OCR workers for the current machine.
"""
import logging
import os
import subprocess
from dataclasses import dataclass

logger = logging.getLogger("HardwareDetect")


@dataclass(frozen=True)
class HardwareInfo:
    cpu_cores: int
    cpu_threads: int
    ram_total_mb: int
    ram_available_mb: int
    gpu_name: str
    gpu_vram_total_mb: int
    gpu_vram_free_mb: int
    gpu_available: bool

    def __str__(self):
        gpu = f"{self.gpu_name} ({self.gpu_vram_total_mb}MB, {self.gpu_vram_free_mb}MB free)" if self.gpu_available else "None"
        return (f"CPU: {self.cpu_cores}c/{self.cpu_threads}t | "
                f"RAM: {self.ram_available_mb}/{self.ram_total_mb}MB | "
                f"GPU: {gpu}")


def detect_hardware() -> HardwareInfo:
    """Detect CPU, RAM, GPU at runtime."""
    # CPU
    cpu_cores = os.cpu_count() or 4
    cpu_threads = cpu_cores  # logical cores

    # RAM
    ram_total_mb, ram_available_mb = _detect_ram()

    # GPU
    gpu_name, gpu_vram_total, gpu_vram_free = _detect_gpu()
    gpu_available = gpu_vram_total > 0

    info = HardwareInfo(
        cpu_cores=cpu_cores,
        cpu_threads=cpu_threads,
        ram_total_mb=ram_total_mb,
        ram_available_mb=ram_available_mb,
        gpu_name=gpu_name,
        gpu_vram_total_mb=gpu_vram_total,
        gpu_vram_free_mb=gpu_vram_free,
        gpu_available=gpu_available,
    )
    logger.info(f"Hardware: {info}")
    return info


def _detect_ram() -> tuple:
    """Detect total and available RAM in MB."""
    try:
        import psutil
        mem = psutil.virtual_memory()
        return int(mem.total / 1024 / 1024), int(mem.available / 1024 / 1024)
    except ImportError:
        pass

    # Fallback: Windows API
    try:
        import ctypes
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        stat = MEMORYSTATUSEX(dwLength=ctypes.sizeof(MEMORYSTATUSEX))
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return int(stat.ullTotalPhys / 1024 / 1024), int(stat.ullAvailPhys / 1024 / 1024)
    except Exception:
        return 8192, 4096  # default 8GB/4GB


def _detect_gpu() -> tuple:
    """Detect GPU name, total VRAM, free VRAM in MB."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(",")
            name = parts[0].strip()
            total = int(parts[1].strip())
            free = int(parts[2].strip())
            return name, total, free
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return "", 0, 0


def calculate_optimal_workers(hw: HardwareInfo, model_vram_mb: int = 3500) -> int:
    """Calculate optimal number of parallel OCR workers.

    The result is also the OCR server's slot count (-np), so each worker
    owns a 12288-cell context (~384 MiB KV, Chandra 2 keeps KV on 8/32
    layers only); see app_config.ocr_server_context_args.

    Factors:
    - GPU VRAM: model + compute buffers (3286 MiB) + N × slot (434 MiB: 384 KV +
      50 recurrent state; measured 3720 MiB for 1 slot, 4155 MiB for 2)
    - RAM: each worker needs ~200MB for image processing
    - CPU: need threads for HTTP streaming + image decoding
    - GPU compute: too many workers → per-worker slowdown

    Returns:
        int: recommended number of parallel workers (1-4)
    """
    if not hw.gpu_available:
        # CPU-only — 1 worker always. llama.cpp uses all threads internally;
        # parallel OCR calls on single CPU just thrash caches and slow down.
        # Benchmark 2026-04-17: Chandra Q4 CPU ~90s/page, zero benefit from
        # 2-way parallel (both slow down equally).
        logger.info("CPU mode: 1 worker (no GPU parallelism; Chandra ~90s/page)")
        return 1

    # KV per slot is small (see docstring); the 8192 MiB figure in old logs
    # was llama-server's RAM prompt cache, now disabled (--cache-ram 0).
    # Key constraint is GPU compute bandwidth: more slots = slower per-slot.

    # Benchmark finding (RTX 4070 8GB, 2026-04-17):
    #   Sequential 2 pages: 40s (63 tok/s per slot, 100% GPU)
    #   Parallel 2 slots:   46s (53 tok/s per slot, GPU shared)
    #   → Parallel SLOWER on 8GB cards! GPU compute is the bottleneck.
    #
    # Parallel only helps when GPU has enough bandwidth to sustain
    # multiple concurrent inferences without significant slowdown.
    # Heuristic: need ~8GB headroom per extra worker (beyond model).
    #
    #   ≤12GB: 1 worker (sequential faster — GPU compute saturated)
    #   16GB+: 2 workers (RTX 4080/A4000 — enough headroom)
    #   24GB+: 2 workers (3 concurrent VLM requests destabilized llama-server
    #   on RTX 5090 Laptop during the 2026-07-13 three-page benchmark; that
    #   was 3 pages overflowing the then-shared 8192-cell KV pool. With one
    #   slot per worker 3+ workers have not been re-measured yet.)
    #   48GB+: 4 workers (A6000, DGX, GB10)
    if hw.gpu_vram_total_mb >= 48000:
        max_by_gpu = 4
    elif hw.gpu_vram_total_mb >= 16000:
        max_by_gpu = 2
    else:
        max_by_gpu = 1  # ≤12GB: sequential is faster

    # RAM: each worker needs ~300MB for image processing (PDF render + base64)
    max_by_ram = max(1, hw.ram_available_mb // 500)

    # CPU: need threads for HTTP streaming per worker
    max_by_cpu = max(1, hw.cpu_threads // 4)

    optimal = min(max_by_gpu, max_by_ram, max_by_cpu, 4)
    optimal = max(1, optimal)

    logger.info(
        f"Optimal workers: {optimal} "
        f"(gpu={max_by_gpu} [{hw.gpu_vram_total_mb}MB], "
        f"ram={max_by_ram}, cpu={max_by_cpu})"
    )
    return optimal
