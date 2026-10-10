"""
Dynamic path configuration for OCR VBHC Desktop.
Resolves paths for both dev environment and portable deployment.
"""
import os
import subprocess
import ctypes
import logging
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("AppConfig")

# Base directory = folder containing this file (desktop/)
BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent  # project root (D:\OCR_VBHC - Custom)


def _find_first(candidates: List[Path], name: str) -> Optional[Path]:
    """Return first existing path from candidates."""
    for p in candidates:
        if p.exists():
            return p
    logger.warning(f"{name} not found in: {[str(c) for c in candidates]}")
    return None


# ─── llama-server binary ──────────────────────────────────────────
def get_llama_bin_dir(use_cuda: bool = True) -> Optional[Path]:
    """Find llama.cpp binary directory (CUDA or CPU)."""
    variant = "cuda" if use_cuda else "cpu"
    return _find_first([
        BASE_DIR / "bin" / variant,                              # portable
        ROOT_DIR / "experiments" / "llama_cpp_bin" / variant,    # dev
    ], f"llama-server ({variant})")


def get_llama_server_exe(use_cuda: bool = True) -> Optional[Path]:
    """Find llama-server executable."""
    bin_dir = get_llama_bin_dir(use_cuda)
    if not bin_dir:
        return None
    exe_name = "llama-server.exe" if os.name == "nt" else "llama-server"
    exe = bin_dir / exe_name
    return exe if exe.exists() else None


# ─── GGUF models ──────────────────────────────────────────────────
def get_gguf_dir() -> Optional[Path]:
    """Find GGUF model directory."""
    return _find_first([
        BASE_DIR / "gguf",                                # portable
        ROOT_DIR / "experiments" / "model_gguf",           # dev
    ], "GGUF models")


# ─── OCR Model Profile ───────────────────────────────────────────
# The portable package ships exactly one quantized OCR model.
OCR_MODELS = {
    "chandra-q4": {
        "name": "Chandra OCR 2 (Q4, cân bằng)",
        "model_file": "chandra/chandra-ocr-2-q4_k_m.gguf",
        "mmproj_file": "chandra/chandra-ocr-2.mmproj-q8_0.gguf",
        # 2026-04-17 reverted 16384→8192: 8GB VRAM saturates at 16384 KV cache
        # → layer offload → 2× slowdown (27 tok/s vs 57 tok/s baseline).
        # Bump back to 16384 via OCR_CTX_SIZE env var when processing rare
        # multi-page dense tables that truncate at 8192.
        "ctx_size": int(os.environ.get("OCR_CTX_SIZE", 8192)),
        "vram_mb": 3500,
        "speed": "~20s/page GPU (ctx=8192)",
    },
}

# (ACTIVE_OCR_MODEL defined below with port config)


def get_model_path(model_key: Optional[str] = None) -> Optional[Path]:
    """Find GGUF model file for active OCR model."""
    key = model_key or ACTIVE_OCR_MODEL
    profile = OCR_MODELS.get(key)
    if not profile:
        return None
    gguf_dir = get_gguf_dir()
    if not gguf_dir:
        return None
    return _find_first([gguf_dir / profile["model_file"]], f"Model ({key})")


def get_mmproj_path(model_key: Optional[str] = None) -> Optional[Path]:
    """Find vision projector GGUF file for active OCR model."""
    key = model_key or ACTIVE_OCR_MODEL
    profile = OCR_MODELS.get(key)
    if not profile:
        return None
    gguf_dir = get_gguf_dir()
    if not gguf_dir:
        return None
    return _find_first([gguf_dir / profile["mmproj_file"]], f"mmproj ({key})")


# ─── Other paths ──────────────────────────────────────────────────
def get_poppler_path() -> Optional[Path]:
    """Find poppler binaries for PDF processing."""
    return _find_first([
        BASE_DIR / "tools" / "poppler" / "Library" / "bin",    # portable
        ROOT_DIR / "tools" / "poppler" / "Library" / "bin",    # dev
    ], "poppler")


# ─── GPU detection (no torch needed) ─────────────────────────────
def detect_cuda_available() -> bool:
    """Check CUDA GPU availability without importing torch."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            logger.info(f"GPU detected: {result.stdout.strip()}")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: try loading CUDA DLL
    try:
        if os.name == "nt":
            ctypes.WinDLL("nvcuda.dll")
            return True
    except OSError:
        pass

    logger.info("No CUDA GPU detected, using CPU")
    return False


def select_backend() -> str:
    """Choose best backend: 'cuda' or 'cpu'."""
    if detect_cuda_available():
        server = get_llama_server_exe(use_cuda=True)
        if server:
            return "cuda"
        logger.warning("CUDA GPU found but no CUDA llama-server binary")
    return "cpu"


# ─── llama-server config ─────────────────────────────────────────
def _validated_port(env_val: str, default: int) -> int:
    try:
        port = int(env_val)
    except ValueError:
        logger.warning(f"Invalid LLAMA_SERVER_PORT={env_val!r}, using {default}")
        return default
    if not (1024 <= port <= 65535):
        logger.warning(f"LLAMA_SERVER_PORT={port} out of range, using {default}")
        return default
    return port

CHANDRA_PORT = _validated_port(os.environ.get("CHANDRA_PORT", "9999"), 9999)
LLAMA_SERVER_HOST = "127.0.0.1"

# Legacy alias — points to Chandra (primary)
LLAMA_SERVER_PORT = CHANDRA_PORT
LLAMA_SERVER_URL = f"http://{LLAMA_SERVER_HOST}:{CHANDRA_PORT}"

# Auto-detected at import time — modules can adapt behavior (e.g. increase
# timeouts, show progress more frequently, suppress parallel OCR workers).
CPU_ONLY_MODE = not detect_cuda_available()
if CPU_ONLY_MODE:
    logger.info("[CPU-only mode] Chandra ~90s/page, Embed ~2.2s/chunk. "
                "All background tasks stay on QThread.")

# Only the bundled Q4 model is accepted.
ACTIVE_OCR_MODEL = os.environ.get("OCR_MODEL", "chandra-q4")
if ACTIVE_OCR_MODEL not in OCR_MODELS:
    logger.warning(f"Unknown model {ACTIVE_OCR_MODEL!r}, falling back to chandra-q4")
    ACTIVE_OCR_MODEL = "chandra-q4"
LLAMA_FLASH_ATTN = True

# OCR defaults — minimal sampling parameters keep transcription deterministic.
# Sized to fit default ctx=8192 (reverted from 16384 for speed — see ctx_size
# comment above). ctx=8192 − ~2200 prompt = ~6000 output headroom.
# Override via OCR_MAX_TOKENS env when running with OCR_CTX_SIZE=16384 for
# dense multi-page tables (use OCR_MAX_TOKENS=13000 in that case).
OCR_MAX_TOKENS = int(os.environ.get("OCR_MAX_TOKENS", 6000))
# Per-field max tokens — small for precise fields, larger for content fields
OCR_FIELD_MAX_TOKENS = {
    0: 64,    # CQBH — 1-2 dòng tên cơ quan
    1: 32,    # Chu_Ky — chỉ họ tên
    2: 64,    # Chuc_Vu — 1-3 dòng chức danh
    3: 16,    # Do_Khan — 1 từ
    4: 16,    # Loai_VB — 1 từ
    5: 512,   # ND_Chinh — trích yếu có thể dài
    6: 32,    # Ngay_BH — 1 dòng ngày
    7: 512,   # Noi_Nhan — danh sách dài
    8: 32,    # So_Ki_Hieu — 1 dòng số
}
OCR_DEFAULT_FIELD_TOKENS = 64
OCR_TEMPERATURE = 0.0
OCR_REPEAT_PENALTY = 1.0

# Chandra OCR 2 — prompt comparison (2026-04-17):
#
#   Strict HTML (current):  ~25k chars, 121 divs, 4 tables, 121 <td>, 0 prose
#   Hybrid (reverted):      ~45k chars, 103 divs, 3 tables,  83 <td>, 436 prose markers
#   Default markdown:       prose-dominant, hits max_tokens on page with dense tables
#
# Strict HTML wins on complex tables: more HTML structure, zero thinking leak.
# Hybrid prompt allows thinking → model describes instead of transcribes → prose
# leaks through. For complex-table fidelity, Q8 model or second-pass OCR needed.
_STRICT_HTML_PROMPT = (
    "Convert this document to HTML. "
    "Output ONLY the final HTML with <div>, <p>, <table>, <tr>, <td>. "
    "Do NOT output analysis, thinking, or prose description. "
    "Preserve table structure exactly as <table><tr><td>...</td></tr></table>. "
    "Include every cell including empty ones as <td></td>, keep columns aligned."
)
OCR_PROMPTS = {"chandra-q4": _STRICT_HTML_PROMPT}
OCR_SYSTEM_PROMPTS = {"chandra-q4": ""}


def get_ocr_prompt(model_key: Optional[str] = None) -> str:
    return OCR_PROMPTS.get(model_key or ACTIVE_OCR_MODEL, "Convert this document to markdown.")

def get_ocr_system_prompt(model_key: Optional[str] = None) -> str:
    return OCR_SYSTEM_PROMPTS.get(model_key or ACTIVE_OCR_MODEL, "")


# ─── Chat model profiles ─────────────────────────────────────────
CHAT_PORT = _validated_port(os.environ.get("CHAT_PORT", "9998"), 9998)

CHAT_MODELS = {
    "qwen3-8b": {
        "name": "Qwen3-8B (chất lượng cao)",
        "model_file": "chat/Qwen3-8B-Q4_K_M.gguf",
        "ctx_gpu": 16384,
        "ctx_cpu": 4096,
        "min_vram_mb": 10000,
    },
    "qwen3-4b": {
        "name": "Qwen3-4B (nhanh, mặc định)",
        "model_file": "chat/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "ctx_gpu": 8192,
        "ctx_cpu": 4096,
        "min_vram_mb": 5000,
    },
}


def detect_vram_mb() -> int:
    """Return total VRAM of the first NVIDIA GPU, or zero when unavailable."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return int(result.stdout.splitlines()[0].strip())
    except (FileNotFoundError, IndexError, ValueError, subprocess.TimeoutExpired):
        pass
    return 0


def select_chat_model(vram_mb: Optional[int] = None) -> Tuple[str, str]:
    """Choose a chat model and llama-server backend for available VRAM."""
    available_mb = detect_vram_mb() if vram_mb is None else vram_mb
    if available_mb >= CHAT_MODELS["qwen3-8b"]["min_vram_mb"]:
        model_key, backend = "qwen3-8b", "cuda"
    elif available_mb >= CHAT_MODELS["qwen3-4b"]["min_vram_mb"]:
        model_key, backend = "qwen3-4b", "cuda"
    else:
        model_key, backend = "qwen3-4b", "cpu"

    override = os.environ.get("CHAT_MODEL")
    if override in CHAT_MODELS:
        model_key = override
    elif override:
        logger.warning("Unknown chat model %r; using automatic selection", override)
    return model_key, backend


def get_chat_model_path(model_key: str) -> Optional[Path]:
    """Find the GGUF file for a configured chat model."""
    profile = CHAT_MODELS.get(model_key)
    if not profile:
        return None
    gguf_dir = get_gguf_dir()
    if not gguf_dir:
        return None
    return _find_first(
        [gguf_dir / profile["model_file"]],
        "Chat model ({})".format(model_key),
    )
