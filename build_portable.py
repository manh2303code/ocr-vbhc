"""
build_portable.py — Đóng gói OCR VBHC thành folder portable offline.

Chạy MỘT LẦN trên máy phát triển (có internet) để tạo package.
Kết quả: folder "OCR_VBHC_Portable/" sẵn sàng copy sang USB.

Usage:
    python build_portable.py                        # full, CPU-only (~23 GB)
    python build_portable.py --include-gpu          # full + GPU wheels
    python build_portable.py --profile runtime      # no models/binaries (CI)

Profiles decide which heavy assets the package must contain. "full" fails when
gguf/, bin/ or the model directories are absent, because those live outside Git
and a build without them looks complete but cannot run. "runtime" builds the
Python runtime and application only, for CI and for split distribution where
the asset bundle travels separately.

Trên máy đích (target):
    1. Copy folder OCR_VBHC_Portable/ từ USB
    2. Double-click "start.bat"
"""

import hashlib
import json
import os
import sys
import shutil
import subprocess
import urllib.request
import zipfile
import argparse
import time
from pathlib import Path

import release_bundle
from app_config import CHAT_MODELS

# ══════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════

PYTHON_VERSION = "3.9.13"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-embed-amd64.zip"
)
PYTHON_MAJOR_MINOR = ".".join(PYTHON_VERSION.split(".")[:2])
GET_PIP_URL = f"https://bootstrap.pypa.io/pip/{PYTHON_MAJOR_MINOR}/get-pip.py"

# Exact dependency set for the runtime layer. Its hash identifies that layer, so
# an application patch can declare which runtime it was built and tested against.
RUNTIME_LOCK_FILE = "requirements_portable.lock"

TORCH_VERSION = "2.8.0"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
TORCH_GPU_INDEX = "https://download.pytorch.org/whl/cu128"

# Source directory (where this script lives)
SRC_DIR = Path(__file__).parent.resolve()

# Core application files (desktop app only, no tests/benchmarks).
# 2026-04-17 refactor: removed vintern_ocr, model_manager, ocr_postprocess,
# login, auth_system, license_dialogs, download_reranker (legacy YOLO/Vintern
# pipeline deprecated — Chandra OCR 2 Q4 is sole engine).
CORE_PY_FILES = [
    # Entry points + core pipeline
    "main.py",
    "intro_page.py",
    "font_loader.py",
    "landing_data.py",
    "landing_page.py",
    "startup_controller.py",
    "main_window_1.py",
    "llama_ocr.py",
    "llama_server_manager.py",
    "app_config.py",
    "logging_config.py",
    "torch_compat.py",
    "chandra_field_parser.py",
    "html_to_docx.py",
    "display_html_renderer.py",
    "editor_widgets.py",        # document editor: growing fields, date popover
    "pdf_rendering.py",
    "pdf_load_worker.py",       # Phase L: async PDF loader (avoids UI freeze)
    "hardware_detect.py",
    "startup_preloader.py",
    "gpu_keepalive.py",         # keeps GPU from throttling between OCR jobs
    "background_workers.py",    # OCR/embedding/file-repair QThreads
    "ocr_pipeline.py",          # PDF/image -> Chandra -> NĐ30 fields
    "document_repository.py",   # SQLite schema, queries, versions, exports
    "file_lifecycle.py",        # safe temp-to-permanent source ownership
    "shutdown_lifecycle.py",    # ordered workers/client/server/DB shutdown
    "release_bundle.py",        # target-side join, checksum and manifest reader
    "app_updater.py",           # verified, rollback-safe application-layer patch
    "build_asset_bundle.py",    # its check/restore run on the target, not here
    "verify_plan.py",           # verify.bat calls this to read the build profile
    "_detect_gpu.py",           # start.bat calls this to pick cuda/cpu backend
    # UI widgets (2026-04-18/19 redesign)
    "doc_list_widget.py",       # minimalist document list + hover tooltips
    "floating_progress.py",     # bottom-right floating progress indicator
    "loading_spinner.py",       # Phase T: rotating SVG spinner widget
    "loading_overlay.py",       # Phase T: intro→main transition overlay
    "toast_widget.py",          # transient notification toasts
    "status_indicators.py",     # llama-server health + VRAM indicator pills
    "shortcut_overlay.py",      # "?" keyboard shortcut help overlay
    "settings_dialog.py",       # app settings dialog (gear icon / Ctrl+,)
    "ui_icons.py",              # Lucide SVG icon loader + make_tool_action
    "ui_commands.py",           # single command/shortcut registry
    "workspace_layout.py",      # deterministic responsive splitter rules
    # Theme system
    "theme_manager_v2.py",      # Phase 11: theme switching + signal cascade
    "theme_tokens.py",          # Phase L: design tokens + qss_content() loader
    # Utilities
    "math_render.py",           # LaTeX math → Unicode/plain text converter
    # Search + analytics dialogs
    "ai_search.py",
    "ai_search_dialog.py",
    "search_store.py",
    "vector_index.py",
    "retrieval.py",
    "legal_relations.py",
    "legal_graph.py",
    "chat_server_manager.py",
    "llm_client.py",
    "agent_tools.py",
    "doc_resolver.py",          # documents a chat question names or points at
    "metadata_query.py",        # chat questions answered from a table of the metadata
    "answer_check.py",          # chat answers checked against their sources
    "rag_agent.py",
    "chat_history.py",
    "chat_panel.py",
    "chat_widgets.py",          # native chat transcript, composer, live answer
    "reindex_dialog.py",
    "reranker.py",
    "analytics_dialog.py",
    "analytics_queries.py",
    "statistics_dialog.py",
    "export_methods_updated.py",
]

# Non-Python resources (QSS stylesheet, etc.) copied alongside .py files.
STATIC_FILES = [
    "theme.qss",                # Phase L: main window light theme
    "theme_dark.qss",           # dark theme selected by persisted settings
    "PORTABLE_README.md",       # offline usage, corpus import and chat guide
    "THIRD_PARTY_NOTICES.md",   # font/icon licenses
    RUNTIME_LOCK_FILE,          # runtime layer identity, read by the updater
    "CAP_NHAT.bat",             # operator entry point for applying a patch
]

# Directories to copy (relative to SRC_DIR)
# model_ocr/models (YOLO) no longer needed post-refactor.
# Superseded by ASSET_DIRS/BUILD_PROFILES below; kept only as documentation of
# which directories hold the sentence-transformer weights.
MODEL_DIRS = ["model_embedding", "model_reranker"]
RESOURCE_DIRS = ["tools", "icons", "assets", "frontend", "config", "database"]

# llama.cpp binaries (copied separately)
LLAMA_BIN_DIRS = ["bin"]  # bin/cuda/ and bin/cpu/

# GGUF models (copied from experiments/model_gguf/)
GGUF_DIR = "gguf"  # desktop/gguf/ in portable

# The asset vocabulary lives in release_bundle, which ships. The target machine
# checks and unpacks asset bundles without the builder installed, so it needs
# these names too; re-exported here so build-side code reads naturally.
ASSET_DIRS = release_bundle.ASSET_DIRS
ASSET_NOTES = release_bundle.ASSET_NOTES
BUILD_PROFILES = release_bundle.BUILD_PROFILES


class IncompletePackageError(RuntimeError):
    """Raised when a profile demands assets the source tree does not have."""


def hash_lock_content(content: str) -> str:
    """Content-address a dependency lock, ignoring comments and ordering.

    Reformatting the file must not look like a new runtime layer, otherwise
    every cosmetic edit would invalidate application patches already built
    against that runtime.
    """
    pins = sorted(
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    return hashlib.sha256("\n".join(pins).encode("utf-8")).hexdigest()


def runtime_lock_hash() -> str:
    """Identity of the runtime layer: the pinned dependency set it contains."""
    return hash_lock_content(
        (SRC_DIR / RUNTIME_LOCK_FILE).read_text(encoding="utf-8")
    )
CHAT_GGUF_FILES = tuple(profile["model_file"] for profile in CHAT_MODELS.values())

# Minimum free disk space required (GB)
MIN_DISK_SPACE_GB = 15


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════

def log(msg: str, level: str = "INFO") -> None:
    """Print timestamped log message."""
    ts = time.strftime("%H:%M:%S")
    prefix = {"INFO": "  ", "OK": "  [OK]", "WARN": "  [!]", "ERR": "  [X]"}
    print(f"  {ts} {prefix.get(level, '  ')} {msg}")


def download_file(url: str, dest: Path, desc: str = "") -> None:
    """Download a file with progress indicator."""
    label = desc or dest.name
    log(f"Downloading {label}...")

    def _progress(block_num, block_size, total_size):
        if total_size > 0:
            pct = min(100, int(block_num * block_size * 100 / total_size))
            mb = block_num * block_size / (1024 * 1024)
            total_mb = total_size / (1024 * 1024)
            print(f"\r    [{pct:3d}%] {mb:.0f}/{total_mb:.0f} MB", end="", flush=True)

    urllib.request.urlretrieve(url, str(dest), reporthook=_progress)
    print()  # newline after progress


def copy_dir_with_progress(src: Path, dst: Path, desc: str) -> None:
    """Copy directory with progress reporting for large dirs."""
    if not src.exists():
        log(f"SKIP {desc}: {src} not found", "WARN")
        return

    # Calculate total size
    total_bytes = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
    total_gb = total_bytes / (1024 ** 3)

    if total_gb > 0.1:
        log(f"Copying {desc} ({total_gb:.1f} GB)...")
    else:
        log(f"Copying {desc}...")

    copied_bytes = 0
    for item in src.rglob("*"):
        if item.is_file():
            rel = item.relative_to(src)
            dest_file = dst / rel
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_file)

            if total_gb > 0.1:
                copied_bytes += item.stat().st_size
                pct = int(copied_bytes * 100 / total_bytes)
                print(
                    f"\r    [{pct:3d}%] {copied_bytes / (1024**3):.1f}/{total_gb:.1f} GB",
                    end="",
                    flush=True,
                )

    if total_gb > 0.1:
        print()


def run_pip(runtime_dir: Path, args: list, desc: str = "") -> None:
    """Run pip command using the bundled Python. Shows live output."""
    python_exe = runtime_dir / "python.exe"
    cmd = [str(python_exe), "-m", "pip"] + args
    if desc:
        log(desc)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"pip command failed: {' '.join(args[:3])}...")


def check_disk_space(target: Path) -> None:
    """Check if enough disk space is available."""
    drive = target.anchor or target.parts[0]
    usage = shutil.disk_usage(drive)
    free_gb = usage.free / (1024 ** 3)
    if free_gb < MIN_DISK_SPACE_GB:
        raise RuntimeError(
            f"Not enough disk space on {drive}: {free_gb:.1f} GB free, "
            f"need at least {MIN_DISK_SPACE_GB} GB"
        )
    log(f"Disk space OK: {free_gb:.0f} GB free on {drive}")


# ══════════════════════════════════════════════════════════════
# Build Steps
# ══════════════════════════════════════════════════════════════

def step1_setup_python_embedded(out_dir: Path) -> Path:
    """Download and configure Python embeddable package."""
    print("\n[1/7] Setting up Python embedded runtime...")

    runtime_dir = out_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    # Download Python embeddable
    zip_path = runtime_dir / "python-embed.zip"
    if not (runtime_dir / "python.exe").exists():
        download_file(PYTHON_EMBED_URL, zip_path, f"Python {PYTHON_VERSION}")

        log("Extracting Python...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(runtime_dir)
        zip_path.unlink()
    else:
        log("Python runtime already exists, skipping download")

    # Enable site-packages by uncommenting 'import site' in ._pth
    pth_file = runtime_dir / f"python{PYTHON_VERSION.replace('.', '')[:2]}._pth"
    if not pth_file.exists():
        # Try alternate naming
        pth_candidates = list(runtime_dir.glob("python*._pth"))
        if pth_candidates:
            pth_file = pth_candidates[0]
        else:
            raise FileNotFoundError(f"Cannot find python*._pth in {runtime_dir}")

    content = pth_file.read_text(encoding="utf-8")
    modified = False
    if "#import site" in content:
        content = content.replace("#import site", "import site")
        modified = True
    # Add '..' so sys.path includes the app root (parent of runtime/)
    if ".." not in content.splitlines():
        content = content.replace(".\n", ".\n..\n", 1)
        modified = True
    if modified:
        pth_file.write_text(content, encoding="utf-8")
        log("Patched ._pth: enabled site + added '..' for app root")

    # Install pip
    pip_exe = runtime_dir / "Scripts" / "pip.exe"
    if not pip_exe.exists():
        get_pip_path = runtime_dir / "get-pip.py"
        download_file(GET_PIP_URL, get_pip_path, "get-pip.py")

        log("Installing pip...")
        subprocess.run(
            [str(runtime_dir / "python.exe"), str(get_pip_path), "--no-warn-script-location"],
            check=True,
        )
        get_pip_path.unlink()
    else:
        log("pip already installed, skipping")

    log(f"Python {PYTHON_VERSION} runtime ready", "OK")
    return runtime_dir


def step2_download_cpu_wheels(out_dir: Path, runtime_dir: Path) -> Path:
    """Download all dependency wheels for offline CPU install."""
    print("\n[2/7] Downloading CPU packages (offline wheels)...")

    pkg_cpu = out_dir / "packages" / "cpu"
    pkg_cpu.mkdir(parents=True, exist_ok=True)

    # Resolve from the lock when it exists, so two builds months apart produce
    # the same runtime. requirements_portable.txt pins only 7 of 20 entries and
    # is kept as the human-readable declaration of intent.
    lock_file = SRC_DIR / RUNTIME_LOCK_FILE
    req_file = SRC_DIR / "requirements_portable.txt"
    source = lock_file if lock_file.exists() else req_file
    if not source.exists():
        raise FileNotFoundError(f"Missing {source}")
    if source is req_file:
        log("No lock file; resolving loosely from requirements_portable.txt", "WARN")

    run_pip(
        runtime_dir,
        [
            "download",
            "--dest", str(pkg_cpu),
            "--only-binary=:all:",
            "--extra-index-url", TORCH_CPU_INDEX,
            "-r", str(source),
        ],
        f"Downloading pinned dependency set from {source.name}...",
    )

    wheel_count = len(list(pkg_cpu.glob("*.whl")))
    total_mb = sum(f.stat().st_size for f in pkg_cpu.iterdir()) / (1024 ** 2)
    log(f"{wheel_count} wheels downloaded ({total_mb:.0f} MB)", "OK")
    return pkg_cpu


def step3_download_gpu_wheels(out_dir: Path, runtime_dir: Path) -> Path:
    """Download GPU PyTorch wheels (optional)."""
    print("\n[3/7] Downloading GPU PyTorch wheels (CUDA 12.8)...")

    pkg_gpu = out_dir / "packages" / "gpu"
    pkg_gpu.mkdir(parents=True, exist_ok=True)

    run_pip(
        runtime_dir,
        [
            "download",
            "--dest", str(pkg_gpu),
            "--only-binary=:all:",
            "--extra-index-url", TORCH_GPU_INDEX,
            f"torch=={TORCH_VERSION}",
        ],
        f"Downloading PyTorch {TORCH_VERSION} CUDA 12.8...",
    )

    total_mb = sum(f.stat().st_size for f in pkg_gpu.iterdir()) / (1024 ** 2)
    log(f"GPU wheels downloaded ({total_mb:.0f} MB)", "OK")
    return pkg_gpu


def step4_install_packages(runtime_dir: Path, pkg_cpu: Path) -> None:
    """Install all packages from local wheels into the runtime."""
    print("\n[4/7] Installing packages into runtime (offline)...")

    # Install everything from local wheels (torch + all deps)
    req_file = SRC_DIR / "requirements_portable.txt"
    run_pip(
        runtime_dir,
        [
            "install",
            "--no-index",
            "--find-links", str(pkg_cpu),
            f"torch=={TORCH_VERSION}",
            "-r", str(req_file),
        ],
        "Installing all packages from offline wheels...",
    )

    # Verify critical imports
    log("Verifying installation...")
    python_exe = runtime_dir / "python.exe"
    verify_script = (
        "import torch; print(f'  torch {torch.__version__}');"
        "import transformers; print(f'  transformers {transformers.__version__}');"
        "import PyQt5; print(f'  PyQt5 OK');"
        "from PyQt5 import QtWebEngineWidgets; print(f'  Qt WebEngine OK');"
        "import numpy; print(f'  numpy {numpy.__version__}');"
        "import sentence_transformers; print(f'  sentence-transformers OK');"
        "import openpyxl; print(f'  openpyxl OK');"
        "import fitz; print(f'  PyMuPDF OK');"
        "import sqlite_vec; print(f'  sqlite-vec {sqlite_vec.__version__}');"
        "print('ALL OK')"
    )
    result = subprocess.run(
        [str(python_exe), "-c", verify_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if "ALL OK" in result.stdout:
        print(result.stdout)
        log("All packages verified", "OK")
    else:
        print(result.stdout)
        print(result.stderr[:500])
        log("Some packages may have issues — check output above", "WARN")


def step5_copy_app_files(out_dir: Path) -> None:
    """Copy Python source files."""
    print("\n[5/7] Copying application files...")

    copied = 0
    for fname in CORE_PY_FILES:
        src = SRC_DIR / fname
        if src.exists():
            shutil.copy2(src, out_dir / fname)
            copied += 1
        else:
            log(f"SKIP missing file: {fname}", "WARN")

    # Static non-Python resources (QSS stylesheets, etc.)
    static_copied = 0
    for fname in STATIC_FILES:
        src = SRC_DIR / fname
        if src.exists():
            shutil.copy2(src, out_dir / fname)
            static_copied += 1
        else:
            log(f"SKIP missing static file: {fname}", "WARN")

    # Copy requirements for reference
    req_src = SRC_DIR / "requirements_portable.txt"
    if req_src.exists():
        shutil.copy2(req_src, out_dir / "requirements_portable.txt")

    log(f"{copied}/{len(CORE_PY_FILES)} Python files + {static_copied}/{len(STATIC_FILES)} static files copied", "OK")


def _asset_source(name: str) -> Path:
    """Resolve an asset directory, honouring the legacy dev layout."""
    primary = SRC_DIR / name
    if primary.exists():
        return primary
    legacy = {
        "bin": SRC_DIR.parent / "experiments" / "llama_cpp_bin",
        "gguf": SRC_DIR.parent / "experiments" / "model_gguf",
    }.get(name)
    return legacy if legacy is not None else primary


# The repository also carries checkpoint/* tags that are not releases, so
# describe has to be told which ones count.
RELEASE_TAG_GLOB = "v*"


def app_version() -> str:
    """Version of the application layer, taken from the release tag.

    OCR_APP_VERSION wins when set. The release workflow already resolves the tag
    it is publishing under, and passing it in is the only reliable route: CI
    checks out shallow, git describe finds no tags there, and the rehearsal
    build duly recorded the commit hash "d94102c" as its version.

    Falls back to the short commit so an untagged development build still
    produces a manifest, but release_bundle.is_release_version marks that value
    as unorderable rather than letting the downgrade guard compare hex digits.
    """
    override = os.environ.get("OCR_APP_VERSION", "").strip()
    if override:
        return override

    for args in (
        ["describe", "--tags", "--abbrev=0", "--match", RELEASE_TAG_GLOB],
        ["rev-parse", "--short", "HEAD"],
    ):
        try:
            result = subprocess.run(
                ["git"] + args, capture_output=True, text=True, cwd=str(SRC_DIR)
            )
        except FileNotFoundError:
            break
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return "0.0.0-unknown"


def build_manifest(out_dir: Path, asset_report: dict, git_commit: str,
                   built_at: str) -> dict:
    """Describe what the package holds so the target can tell short from broken.

    A runtime-profile package omits 16.5 GB on purpose. Without this record the
    target machine cannot distinguish that from an interrupted copy.
    """
    components = {}
    for name in ASSET_DIRS:
        present = name in asset_report["included"]
        components[name] = {
            "present": present,
            "bytes": release_bundle.directory_size(out_dir / name) if present else 0,
            "note": "" if present else ASSET_NOTES.get(
                name, "Supply from the asset bundle."
            ),
        }

    lock_hash = runtime_lock_hash()
    included = list(asset_report["included"])

    return {
        "schema": 1,
        "profile": asset_report["profile"],
        "built_at": built_at,
        "git_commit": git_commit,
        "python_version": PYTHON_VERSION,
        # Identity of the runtime layer; a later application patch is matched
        # against this before it is allowed to install.
        "runtime_lock_sha256": lock_hash,
        # The three layers version independently because they change at wildly
        # different rates: application source moved 64 times in three months,
        # the dependency set four times in the project's history, the models
        # effectively never. Versioning them together would mean carrying 23 GB
        # across an air gap for every 5 MB fix.
        "layers": {
            "app": {
                "version": app_version(),
                "commit": git_commit,
                # What the updater checks before writing anything.
                "requires_runtime": lock_hash,
                "requires_assets": included,
            },
            "runtime": {
                "lock_sha256": lock_hash,
                "python_version": PYTHON_VERSION,
            },
            "assets": {
                "present": included,
                "absent": list(asset_report["excluded"]),
            },
        },
        "components": components,
    }


def write_manifest(out_dir: Path, manifest: dict) -> Path:
    path = Path(out_dir) / release_bundle.MANIFEST_NAME
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def _copy_gguf_selectively(gguf_src: Path, gguf_dst: Path) -> None:
    """Copy only the weights app_config advertises, not whatever sits in gguf/.

    A developer machine accumulates experimental quantisations; shipping them
    would add gigabytes the application never loads.
    """
    gguf_dst.mkdir(parents=True, exist_ok=True)
    essential_files = [
        ("chandra/chandra-ocr-2-q4_k_m.gguf", "chandra"),
        ("chandra/chandra-ocr-2.mmproj-q8_0.gguf", "chandra"),
    ]
    for rel_path, subdir in essential_files:
        src_f = gguf_src / rel_path
        if src_f.exists():
            dst_subdir = gguf_dst / subdir if subdir else gguf_dst
            dst_subdir.mkdir(exist_ok=True)
            shutil.copy2(src_f, dst_subdir / src_f.name)
            log(f"Copied GGUF: {rel_path} ({src_f.stat().st_size // 1024 // 1024}MB)", "OK")
        else:
            log(f"GGUF not found: {rel_path}", "WARN")

    # Chat model inventory is extensible: copy every local GGUF while the
    # required runtime profiles remain validated by CHAT_GGUF_FILES.
    chat_src = gguf_src / "chat"
    chat_dst = gguf_dst / "chat"
    for src_f in sorted(chat_src.glob("*.gguf")):
        chat_dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_f, chat_dst / src_f.name)
        log(f"Copied chat GGUF: {src_f.name} ({src_f.stat().st_size // 1024 // 1024}MB)", "OK")
    for rel_path in CHAT_GGUF_FILES:
        if not (gguf_src / rel_path).is_file():
            log(f"Required chat GGUF not found: {rel_path}", "WARN")


def copy_assets(out_dir: Path, profile: str = "full") -> dict:
    """Copy heavy assets according to a profile and report what was included.

    Returns a report the manifest and the release workflow can consume. Raises
    IncompletePackageError when the profile requires an asset that is absent,
    rather than logging a warning and producing a package that looks complete.
    """
    if profile not in BUILD_PROFILES:
        raise ValueError(
            "Unknown build profile {!r}; expected one of {}".format(
                profile, ", ".join(sorted(BUILD_PROFILES))
            )
        )
    wanted = set(BUILD_PROFILES[profile]["assets"])
    required = set(BUILD_PROFILES[profile]["required_assets"])

    present, excluded = [], []
    for name in ASSET_DIRS:
        included = name in wanted and _asset_source(name).exists()
        (present if included else excluded).append(name)

    missing_required = sorted(required.intersection(excluded))
    if missing_required:
        raise IncompletePackageError(
            "Profile {!r} requires assets that are absent from {}: {}. "
            "Build with --profile runtime, or supply the asset bundle.".format(
                profile, SRC_DIR, ", ".join(missing_required)
            )
        )

    for name in present:
        if name == "gguf":
            _copy_gguf_selectively(_asset_source(name), out_dir / "gguf")
        else:
            copy_dir_with_progress(_asset_source(name), out_dir / name, name)
    for name in excluded:
        # A placeholder makes the gap visible on the target machine instead of
        # surfacing as an obscure path error at first launch.
        (out_dir / name).mkdir(parents=True, exist_ok=True)
        log("{} excluded by profile {!r}".format(name, profile), "WARN")

    return {
        "profile": profile,
        "included": present,
        "excluded": excluded,
        "missing_required": missing_required,
    }


def step6_copy_models_and_resources(out_dir: Path, profile: str = "full") -> dict:
    """Copy resource files, then heavy assets according to the profile."""
    print("\n[6/7] Copying models and resources...")

    for d in RESOURCE_DIRS:
        copy_dir_with_progress(SRC_DIR / d, out_dir / d, d)

    report = copy_assets(out_dir, profile)
    log("All resources copied", "OK")
    return report


def step7_create_launchers(out_dir: Path, include_gpu: bool) -> None:
    """Copy launchers and create optional GPU upgrade scripts."""
    print("\n[7/7] Creating launcher scripts...")

    shutil.copy2(SRC_DIR / "start.bat", out_dir / "start.bat")
    log("Created start.bat", "OK")
    launcher = "KHOI_DONG_OCR_VBHC_CP4.bat"
    shutil.copy2(SRC_DIR / launcher, out_dir / launcher)
    log(f"Created {launcher}", "OK")

    # ── NANG_CAP_GPU.bat (optional) ──
    if include_gpu:
        gpu_bat = r'''@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo  ============================================
echo   Nang cap PyTorch GPU (CUDA 12.8)
echo  ============================================
echo.

set PYTHON="%~dp0runtime\python.exe"

:: Check GPU wheels exist
if not exist "packages\gpu" (
    echo [LOI] Khong tim thay packages\gpu\
    echo Ban can build_portable.py voi --include-gpu
    pause
    exit /b 1
)

:: Check NVIDIA GPU
nvidia-smi >nul 2>&1
if %errorlevel% neq 0 (
    echo [CANH BAO] Khong phat hien GPU NVIDIA tren may nay.
    echo Nang cap GPU chi huu ich khi may co GPU NVIDIA + CUDA drivers.
    echo.
    set /p CONTINUE="Ban co muon tiep tuc? (y/n): "
    if /i not "%CONTINUE%"=="y" exit /b 0
)

echo Dang go bo PyTorch CPU...
%PYTHON% -m pip uninstall torch -y >nul 2>&1

echo Dang cai dat PyTorch GPU tu offline wheels...
%PYTHON% -m pip install --no-index --find-links "packages\gpu" torch
if %errorlevel% neq 0 goto gpu_rollback

%PYTHON% -c "import torch; from torch_compat import cuda_arch_supported; assert cuda_arch_supported(torch)" 2>nul
if %errorlevel% neq 0 goto gpu_rollback

echo.
echo [OK] Da nang cap thanh cong!
%PYTHON% -c "import torch; print(f'  PyTorch {torch.__version__}'); print(f'  GPU: {torch.cuda.get_device_name(0)}')"
goto gpu_done

:gpu_rollback
echo.
echo [LOI] CUDA wheel khong tuong thich. Dang cai lai phien ban CPU...
%PYTHON% -m pip install --no-index --find-links "packages\cpu" torch

:gpu_done

echo.
pause
'''
        (out_dir / "NANG_CAP_GPU.bat").write_text(gpu_bat, encoding="utf-8")
        log("Created NANG_CAP_GPU.bat", "OK")

    # ── HA_CAP_CPU.bat (downgrade back to CPU if GPU fails) ──
    if include_gpu:
        cpu_bat = r'''@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo  Chuyen ve PyTorch CPU...
echo.

set PYTHON="%~dp0runtime\python.exe"

%PYTHON% -m pip uninstall torch -y >nul 2>&1
%PYTHON% -m pip install --no-index --find-links "packages\cpu" torch

echo.
echo [OK] Da chuyen ve che do CPU.
pause
'''
        (out_dir / "HA_CAP_CPU.bat").write_text(cpu_bat, encoding="utf-8")
        log("Created HA_CAP_CPU.bat", "OK")


def step8_create_version_and_verify(out_dir: Path, asset_report: dict) -> None:
    """Create build metadata and copy the tested target verifier."""
    print("\n[8/8] Creating VERSION.txt, manifest and verify.bat...")
    git_hash = "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=str(SRC_DIR),
        )
        if result.returncode == 0:
            git_hash = result.stdout.strip()
    except FileNotFoundError:
        pass

    built_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    profile = asset_report["profile"]
    version_content = (
        "OCR VBHC Portable Package\n"
        f"Build date:    {built_at}\n"
        f"Git commit:    {git_hash}\n"
        f"Profile:       {profile} — {BUILD_PROFILES[profile]['description']}\n"
        f"Python:        {PYTHON_VERSION}\n"
        f"PyTorch:       {TORCH_VERSION} (CPU; optional CUDA 12.8)\n"
        f"Runtime layer: {runtime_lock_hash()[:16]}\n"
        "Transformers:  4.42.4\n"
        "Chat engine:   Qwen3 4B/8B Q4_K_M via llama.cpp (port 9998)\n"
    )
    (out_dir / "VERSION.txt").write_text(version_content, encoding="utf-8")

    manifest = build_manifest(out_dir, asset_report, git_hash, built_at)
    write_manifest(out_dir, manifest)
    absent = [name for name, item in manifest["components"].items()
              if not item["present"]]
    if absent:
        log("Manifest records {} absent: {}".format(
            "components" if len(absent) > 1 else "component", ", ".join(absent)
        ), "WARN")

    shutil.copy2(SRC_DIR / "verify.bat", out_dir / "verify.bat")
    log("Created VERSION.txt, BUILD_MANIFEST.json and verify.bat", "OK")


# ══════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build portable offline OCR VBHC package"
    )
    parser.add_argument(
        "--profile",
        choices=sorted(BUILD_PROFILES),
        default="full",
        help=(
            "full (default) requires every asset locally; runtime builds without "
            "models or llama.cpp binaries, for CI and split distribution"
        ),
    )
    parser.add_argument(
        "--include-gpu",
        action="store_true",
        help="Also download CUDA 12.8 PyTorch wheels for GPU upgrade",
    )
    parser.add_argument(
        "--slim",
        action="store_true",
        help="Remove packages/ after install to save ~2GB (no GPU upgrade possible)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output directory (default: OCR_VBHC_Portable next to this script)",
    )
    return parser


def main():
    args = build_arg_parser().parse_args()

    out_dir = Path(args.output) if args.output else SRC_DIR / "OCR_VBHC_Portable"

    print("=" * 56)
    print("  BUILD PORTABLE OCR VBHC PACKAGE")
    print("=" * 56)
    print(f"  Source:  {SRC_DIR}")
    print(f"  Output:  {out_dir}")
    print(f"  GPU:     {'Yes (CUDA 12.8)' if args.include_gpu else 'CPU only'}")
    print(f"  Profile: {args.profile} — {BUILD_PROFILES[args.profile]['description']}")
    print("=" * 56)

    start_time = time.time()

    # Pre-flight checks
    check_disk_space(out_dir.parent)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Verify source files exist
    missing = [f for f in CORE_PY_FILES if not (SRC_DIR / f).exists()]
    if missing:
        log(f"Missing source files: {missing}", "WARN")

    # Execute build steps
    runtime_dir = step1_setup_python_embedded(out_dir)
    pkg_cpu = step2_download_cpu_wheels(out_dir, runtime_dir)

    if args.include_gpu:
        step3_download_gpu_wheels(out_dir, runtime_dir)
    else:
        print("\n[3/7] Skipping GPU wheels (use --include-gpu to include)")

    step4_install_packages(runtime_dir, pkg_cpu)
    step5_copy_app_files(out_dir)
    asset_report = step6_copy_models_and_resources(out_dir, args.profile)
    step7_create_launchers(out_dir, args.include_gpu)
    step8_create_version_and_verify(out_dir, asset_report)

    # Slim mode: remove packages/ to save space
    if args.slim:
        pkg_dir = out_dir / "packages"
        if pkg_dir.exists():
            log("--slim: removing packages/ to save space...")
            shutil.rmtree(pkg_dir)
            log("Removed packages/ (~2 GB saved)", "OK")

    # Summary
    elapsed = time.time() - start_time
    total_size = sum(
        f.stat().st_size for f in out_dir.rglob("*") if f.is_file()
    )
    total_gb = total_size / (1024 ** 3)

    print()
    print("=" * 56)
    print(f"  BUILD COMPLETE!")
    print(f"  Output:    {out_dir}")
    print(f"  Size:      {total_gb:.1f} GB")
    print(f"  Time:      {elapsed / 60:.1f} minutes")
    print("=" * 56)
    print()
    print("  HUONG DAN SU DUNG:")
    print("  1. Copy folder OCR_VBHC_Portable/ vao USB")
    print("  2. Paste vao may dich (bat ky thu muc nao)")
    print("  3. Double-click 'start.bat' de chay")
    if args.include_gpu:
        print("  4. (Tuy chon) Double-click 'NANG_CAP_GPU.bat'")
        print("     neu may co GPU NVIDIA + CUDA drivers")
    print()


if __name__ == "__main__":
    main()
