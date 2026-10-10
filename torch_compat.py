"""Small compatibility checks for optional PyTorch CUDA acceleration."""


def cuda_arch_supported(torch_module) -> bool:
    """Return whether the installed wheel contains code for the active GPU."""
    try:
        if not torch_module.cuda.is_available():
            return False
        major, minor = torch_module.cuda.get_device_capability(0)
        target = f"{major}{minor}"
        arches = set(torch_module.cuda.get_arch_list())
        return f"sm_{target}" in arches or f"compute_{target}" in arches
    except Exception:
        return False


def preferred_torch_device() -> str:
    try:
        import torch
        return "cuda" if cuda_arch_supported(torch) else "cpu"
    except ImportError:
        return "cpu"
