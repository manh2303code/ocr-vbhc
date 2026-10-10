"""Applying a patch on an air-gapped machine has to be safe to abandon.

There is no network, no technical staff on site, and the machine runs a live
document workflow. A half-written update is worse than a refused one, so the
updater verifies before it touches anything, keeps the previous application
layer, and restores it if the replacement fails partway through.
"""
import json
import tempfile
import unittest
from pathlib import Path

import app_updater
import release_bundle


RUNTIME_HASH = "c" * 64


def _write_manifest(directory, manifest):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / release_bundle.MANIFEST_NAME).write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _installed_manifest(version="2.0.0"):
    return {
        "schema": 1,
        "profile": "full",
        "layers": {
            "app": {"version": version},
            "runtime": {"lock_sha256": RUNTIME_HASH},
            "assets": {"present": ["gguf", "bin"]},
        },
    }


def _patch_manifest(version="2.1.0", requires_runtime=RUNTIME_HASH, requires_assets=()):
    return {
        "schema": 1,
        "layers": {
            "app": {
                "version": version,
                "requires_runtime": requires_runtime,
                "requires_assets": list(requires_assets),
            }
        },
    }


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.install = root / "install"
        self.patch = root / "patch"

        _write_manifest(self.install, _installed_manifest())
        (self.install / "main.py").write_text("old application\n", encoding="utf-8")
        (self.install / "database").mkdir()
        (self.install / "database" / "documents.db").write_bytes(b"user data")

        _write_manifest(self.patch, _patch_manifest())
        (self.patch / "main.py").write_text("new application\n", encoding="utf-8")
        self._sign_patch()

    def tearDown(self):
        self._tmp.cleanup()

    def _sign_patch(self):
        payload = [
            path for path in sorted(self.patch.rglob("*"))
            if path.is_file() and path.name != release_bundle.CHECKSUM_NAME
        ]
        release_bundle.write_checksums(payload, self.patch / release_bundle.CHECKSUM_NAME)

    def test_a_valid_patch_replaces_the_application_layer(self):
        result = app_updater.apply_patch(self.patch, self.install)

        self.assertTrue(result.applied, result.problems)
        self.assertEqual(
            "new application\n",
            (self.install / "main.py").read_text(encoding="utf-8"),
        )

    def test_user_data_is_never_touched(self):
        app_updater.apply_patch(self.patch, self.install)

        self.assertEqual(
            b"user data", (self.install / "database" / "documents.db").read_bytes()
        )

    def test_corrupted_transfer_is_rejected_before_anything_is_written(self):
        target = self.patch / "main.py"
        target.write_text("tampered\n", encoding="utf-8")

        result = app_updater.apply_patch(self.patch, self.install)

        self.assertFalse(result.applied)
        self.assertTrue(any("checksum" in p.lower() for p in result.problems))
        # The installed tree must be exactly as it was.
        self.assertEqual(
            "old application\n",
            (self.install / "main.py").read_text(encoding="utf-8"),
        )

    def test_incompatible_patch_is_refused_with_the_reason(self):
        _write_manifest(self.patch, _patch_manifest(requires_runtime="d" * 64))
        self._sign_patch()

        result = app_updater.apply_patch(self.patch, self.install)

        self.assertFalse(result.applied)
        self.assertTrue(any("runtime" in p.lower() for p in result.problems))
        self.assertEqual(
            "old application\n",
            (self.install / "main.py").read_text(encoding="utf-8"),
        )

    def test_previous_application_layer_is_kept(self):
        app_updater.apply_patch(self.patch, self.install)

        backups = list((self.install / "backup").glob("app-*"))
        self.assertEqual(1, len(backups), backups)
        self.assertEqual(
            "old application\n",
            (backups[0] / "main.py").read_text(encoding="utf-8"),
        )

    def test_a_failure_midway_restores_the_previous_layer(self):
        """The machine runs a live workflow; a half-written tree is worse than
        no update at all."""
        def explode(_source, _destination):
            raise OSError("simulated media failure")

        result = app_updater.apply_patch(
            self.patch, self.install, _copy=explode
        )

        self.assertFalse(result.applied)
        self.assertTrue(result.rolled_back)
        self.assertEqual(
            "old application\n",
            (self.install / "main.py").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            b"user data", (self.install / "database" / "documents.db").read_bytes()
        )

    def test_manifest_is_updated_only_on_success(self):
        app_updater.apply_patch(self.patch, self.install)

        installed = release_bundle.read_manifest(self.install)
        self.assertEqual("2.1.0", installed["layers"]["app"]["version"])
        # The runtime and asset layers were not part of this patch.
        self.assertEqual(
            RUNTIME_HASH, installed["layers"]["runtime"]["lock_sha256"]
        )
        self.assertEqual(["gguf", "bin"], installed["layers"]["assets"]["present"])

    def test_updater_and_its_launcher_ship_with_the_package(self):
        """The patch is applied on the target, so both have to be in the box."""
        import build_portable

        self.assertIn("app_updater.py", build_portable.CORE_PY_FILES)
        self.assertIn("CAP_NHAT.bat", build_portable.STATIC_FILES)

    def test_patch_cannot_write_into_operator_owned_directories(self):
        """A malformed bundle must not reach database/ or config/."""
        (self.patch / "database").mkdir()
        (self.patch / "database" / "documents.db").write_bytes(b"hostile")
        self._sign_patch()

        result = app_updater.apply_patch(self.patch, self.install)

        self.assertFalse(result.applied)
        self.assertTrue(any("database" in p for p in result.problems))
        self.assertEqual(
            b"user data", (self.install / "database" / "documents.db").read_bytes()
        )

    def test_missing_checksums_are_refused_rather_than_trusted(self):
        (self.patch / release_bundle.CHECKSUM_NAME).unlink()

        result = app_updater.apply_patch(self.patch, self.install)

        self.assertFalse(result.applied)
        self.assertTrue(any("sha256sums" in p.lower() for p in result.problems))


if __name__ == "__main__":
    unittest.main()
