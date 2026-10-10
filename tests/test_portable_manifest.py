import unittest
import tempfile
import inspect
from pathlib import Path
from unittest import mock

import build_portable
import release_bundle
from app_config import CHAT_MODELS


ROOT = Path(__file__).resolve().parents[1]


class PortableManifestTests(unittest.TestCase):
    def test_all_declared_source_and_static_files_exist(self):
        missing = [
            name for name in build_portable.CORE_PY_FILES + build_portable.STATIC_FILES
            if not (ROOT / name).is_file()
        ]
        self.assertEqual([], missing)
        self.assertIn("theme_dark.qss", build_portable.STATIC_FILES)
        self.assertIn("torch_compat.py", build_portable.CORE_PY_FILES)
        self.assertIn("startup_controller.py", build_portable.CORE_PY_FILES)
        self.assertIn("frontend", build_portable.RESOURCE_DIRS)

    def test_builder_uses_blackwell_capable_cuda_wheels(self):
        torch_version = tuple(map(int, build_portable.TORCH_VERSION.split(".")))
        self.assertGreaterEqual(torch_version, (2, 7, 0))
        if build_portable.PYTHON_VERSION.startswith("3.9."):
            self.assertEqual((2, 8, 0), torch_version)
        self.assertTrue(build_portable.TORCH_GPU_INDEX.endswith("/cu128"))

    def test_embedded_python_uses_version_specific_pip_bootstrap(self):
        major_minor = ".".join(build_portable.PYTHON_VERSION.split(".")[:2])
        self.assertIn(f"/pip/{major_minor}/get-pip.py", build_portable.GET_PIP_URL)

    def test_requirements_exclude_removed_runtime_stacks(self):
        requirements = (ROOT / "requirements_portable.txt").read_text(encoding="utf-8").lower()
        for removed in ("ultralytics", "albumentations", "qdarkstyle", "sqlalchemy"):
            self.assertNotIn(removed, requirements)

    def test_llama_backend_detection_does_not_depend_on_pytorch(self):
        source = (ROOT / "_detect_gpu.py").read_text(encoding="utf-8-sig")
        self.assertNotIn("import torch", source)
        self.assertIn("nvidia-smi", source)

    def test_builder_copies_tested_launchers_and_full_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            build_portable.step5_copy_app_files(output)
            build_portable.step7_create_launchers(output, include_gpu=False)
            build_portable.step8_create_version_and_verify(
                output,
                {"profile": "runtime", "included": [],
                 "excluded": list(build_portable.ASSET_DIRS),
                 "missing_required": []},
            )

            for name in build_portable.CORE_PY_FILES + build_portable.STATIC_FILES:
                self.assertTrue((output / name).is_file(), name)
            # The package must describe itself, or the target cannot tell a
            # runtime-profile build from an interrupted transfer.
            manifest = release_bundle.read_manifest(output)
            self.assertEqual("runtime", manifest["profile"])
            self.assertIn("Runtime layer:", (output / "VERSION.txt").read_text(encoding="utf-8"))
            self.assertEqual((ROOT / "start.bat").read_bytes(), (output / "start.bat").read_bytes())
            self.assertEqual(
                (ROOT / "KHOI_DONG_OCR_VBHC_CP4.bat").read_bytes(),
                (output / "KHOI_DONG_OCR_VBHC_CP4.bat").read_bytes(),
            )
            self.assertEqual((ROOT / "verify.bat").read_bytes(), (output / "verify.bat").read_bytes())

    def test_chat_model_manifest_matches_runtime_config(self):
        expected = tuple(profile["model_file"] for profile in CHAT_MODELS.values())
        self.assertEqual(expected, build_portable.CHAT_GGUF_FILES)

        verify = (ROOT / "verify.bat").read_text(encoding="utf-8-sig").replace("\\", "/")
        for relative_path in expected:
            self.assertIn(f'gguf/{relative_path}', verify)

    def test_builder_copies_all_chat_gguf_files_dynamically(self):
        with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as output_tmp:
            source = Path(source_tmp)
            output = Path(output_tmp)
            chat = source / "gguf" / "chat"
            chat.mkdir(parents=True)
            for name in ("model-a.gguf", "model-b.gguf"):
                (chat / name).write_bytes(name.encode("ascii"))
            # The full profile is the one that copies assets at all; it requires
            # every asset directory to exist, so stub the ones not under test.
            for name in build_portable.ASSET_DIRS:
                (source / name).mkdir(parents=True, exist_ok=True)

            with mock.patch.object(build_portable, "SRC_DIR", source), \
                    mock.patch.object(build_portable, "RESOURCE_DIRS", []):
                build_portable.step6_copy_models_and_resources(output, profile="full")

            self.assertEqual(
                ["model-a.gguf", "model-b.gguf"],
                sorted(path.name for path in (output / "gguf" / "chat").glob("*.gguf")),
            )

    def test_sqlite_vec_is_bundled_and_verified(self):
        requirements = (ROOT / "requirements_portable.txt").read_text(encoding="utf-8").lower()
        self.assertIn("sqlite-vec", requirements)
        verify = (ROOT / "verify.bat").read_text(encoding="utf-8-sig")
        self.assertIn("import sqlite_vec", verify)
        self.assertIn("import sqlite_vec", inspect.getsource(build_portable.step4_install_packages))

    def test_webengine_and_local_fonts_are_bundled_and_verified(self):
        requirements = (ROOT / "requirements_portable.txt").read_text(encoding="utf-8")
        verify = (ROOT / "verify.bat").read_text(encoding="utf-8-sig")
        build = (ROOT / "build_portable.py").read_text(encoding="utf-8-sig")
        self.assertIn("PyQtWebEngine==5.15.7", requirements)
        self.assertIn("PyQtWebEngine-Qt5==5.15.2", requirements)
        self.assertIn("PyQt5.QtWebEngineWidgets", verify)
        self.assertIn("frontend\\landing\\index.html", verify)
        self.assertIn("frontend\\landing\\landing.css", verify)
        self.assertIn("frontend\\landing\\landing.js", verify)
        self.assertIn("workspace_layout.py", build)
        self.assertIn("workspace_layout.py", verify)
        self.assertIn("QtWebEngineProcess.exe", verify)
        self.assertIn("qtwebengine_resources.pak", verify)
        self.assertIn("icudtl.dat", verify)
        self.assertTrue((ROOT / "assets" / "fonts" / "BeVietnamPro-Regular.ttf").is_file())

    def test_application_keeps_original_bear_icon(self):
        main_source = (ROOT / "main.py").read_text(encoding="utf-8-sig")
        verify = (ROOT / "verify.bat").read_text(encoding="utf-8-sig")
        self.assertIn('ASSETS_DIR / "app_icon.ico"', main_source)
        self.assertNotIn("_wordmark_icon", main_source)
        self.assertIn("assets\\app_icon.ico", verify)
        self.assertIn("assets\\app_icon_256.png", verify)

    @unittest.skipUnless(
        all((ROOT / "gguf" / profile["model_file"]).is_file() for profile in CHAT_MODELS.values()),
        "chat GGUF chưa tải",
    )
    def test_required_chat_models_are_present(self):
        for profile in CHAT_MODELS.values():
            path = ROOT / "gguf" / profile["model_file"]
            self.assertGreater(path.stat().st_size, 1_000_000_000, path.name)


if __name__ == "__main__":
    unittest.main()
