import unittest

from torch_compat import cuda_arch_supported


class _Cuda:
    def __init__(self, available=True, capability=(12, 0), arches=()):
        self.available = available
        self.capability = capability
        self.arches = list(arches)

    def is_available(self):
        return self.available

    def get_device_capability(self, _index=0):
        return self.capability

    def get_arch_list(self):
        return self.arches


class _Torch:
    def __init__(self, cuda):
        self.cuda = cuda


class TorchCompatibilityTests(unittest.TestCase):
    def test_rejects_blackwell_when_wheel_has_no_sm120_kernel(self):
        torch = _Torch(_Cuda(arches=["sm_90", "compute_90"]))
        self.assertFalse(cuda_arch_supported(torch))

    def test_accepts_matching_binary_or_ptx_architecture(self):
        self.assertTrue(cuda_arch_supported(_Torch(_Cuda(arches=["sm_120"]))))
        self.assertTrue(cuda_arch_supported(_Torch(_Cuda(arches=["compute_120"]))))

    def test_rejects_unavailable_cuda(self):
        self.assertFalse(cuda_arch_supported(_Torch(_Cuda(available=False))))


if __name__ == "__main__":
    unittest.main()
