"""Build profiles must make an incomplete package impossible to ship silently.

Before this contract existed, step6 logged a warning and carried on when the
model, llama.cpp and GGUF directories were absent — which is exactly the state
of a CI checkout, because none of them are tracked in Git. The build exited 0
and produced a package with no gguf/ directory at all.
"""
import tempfile
import unittest
from pathlib import Path

import build_portable


class BuildProfileTests(unittest.TestCase):
    def setUp(self):
        self._original_src = build_portable.SRC_DIR
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.src = root / "src"
        self.out = root / "out"
        for path in (self.src, self.out):
            path.mkdir(parents=True)
        # A CI checkout carries the resource dirs but none of the heavy assets.
        for name in build_portable.RESOURCE_DIRS:
            (self.src / name).mkdir(parents=True, exist_ok=True)
        build_portable.SRC_DIR = self.src

    def tearDown(self):
        build_portable.SRC_DIR = self._original_src
        self._tmp.cleanup()

    def test_profiles_declare_which_components_are_optional(self):
        profiles = build_portable.BUILD_PROFILES
        self.assertIn("runtime", profiles)
        self.assertIn("full", profiles)
        # The runtime profile is what CI can actually build.
        self.assertEqual(set(), set(profiles["runtime"]["required_assets"]))
        self.assertTrue(
            {"gguf", "bin", "model_embedding", "model_reranker"}
            <= set(profiles["full"]["required_assets"])
        )

    def test_runtime_profile_records_absent_assets_instead_of_warning(self):
        report = build_portable.copy_assets(self.out, profile="runtime")

        self.assertEqual("runtime", report["profile"])
        self.assertIn("gguf", report["excluded"])
        self.assertIn("model_embedding", report["excluded"])
        self.assertEqual([], report["missing_required"])

    def test_full_profile_refuses_to_build_without_the_assets(self):
        with self.assertRaises(build_portable.IncompletePackageError) as caught:
            build_portable.copy_assets(self.out, profile="full")

        message = str(caught.exception)
        for name in ("gguf", "model_embedding", "model_reranker", "bin"):
            self.assertIn(name, message)

    def test_runtime_profile_excludes_assets_even_when_they_are_available(self):
        """A profile states what the package contains, not merely what it needs.

        The release machine has all 16.5 GB locally and still has to be able to
        cut a runtime-only package, because the assets ship as their own bundle.
        """
        for name in build_portable.ASSET_DIRS:
            (self.src / name).mkdir(parents=True, exist_ok=True)
            (self.src / name / "weights.bin").write_bytes(b"x" * 16)

        report = build_portable.copy_assets(self.out, profile="runtime")

        self.assertEqual([], report["included"])
        self.assertEqual(sorted(build_portable.ASSET_DIRS), sorted(report["excluded"]))
        for name in build_portable.ASSET_DIRS:
            self.assertFalse((self.out / name / "weights.bin").exists(), name)

    def test_full_profile_includes_assets_when_present(self):
        for name in build_portable.ASSET_DIRS:
            (self.src / name).mkdir(parents=True, exist_ok=True)
            (self.src / name / "weights.bin").write_bytes(b"x" * 16)

        report = build_portable.copy_assets(self.out, profile="full")

        self.assertEqual(sorted(build_portable.ASSET_DIRS), sorted(report["included"]))
        self.assertEqual([], report["excluded"])

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            build_portable.copy_assets(self.out, profile="nope")

    def test_cli_exposes_the_profile_flag(self):
        parser = build_portable.build_arg_parser()
        self.assertEqual("runtime", parser.parse_args(["--profile", "runtime"]).profile)
        self.assertEqual("full", parser.parse_args([]).profile)


if __name__ == "__main__":
    unittest.main()
