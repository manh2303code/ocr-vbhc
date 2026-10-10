"""Select the llama.cpp backend for start.bat without importing PyTorch."""

import os
import subprocess
from pathlib import Path


base_dir = Path(__file__).resolve().parent
result_file = base_dir / "_gpu_result.txt"
SERVER_EXE = "llama-server.exe" if os.name == "nt" else "llama-server"

try:
    # creationflags is Windows-only; passing it on Linux raises ValueError.
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    probe = subprocess.run(
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
        capture_output=True, text=True, timeout=5,
        **extra
    )
    cuda_server = base_dir / "bin" / "cuda" / SERVER_EXE
    use_cuda = probe.returncode == 0 and bool(probe.stdout.strip()) and cuda_server.is_file()
    result_file.write_text("cuda" if use_cuda else "cpu", encoding="ascii")
    print(f"Llama backend: {'CUDA - ' + probe.stdout.strip() if use_cuda else 'CPU'}")
except Exception as exc:
    result_file.write_text("cpu", encoding="ascii")
    print(f"Khong the kiem tra GPU ({exc}), su dung CPU")
