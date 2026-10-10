"""BUILD_MANIFEST.json states what a package contains, and what it does not.

A runtime-profile package is missing 16.5 GB of models by design. Without a
manifest the target machine cannot tell that apart from a corrupt transfer, and
verify.bat would report a valid package as broken.
"""
import json
import tempfile
import unittest
from pathlib import Path

import build_portable
import release_bundle


class BuildManifestTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name)
        self.report = {
            "profile": "runtime",
            "included": [],
            "excluded": ["gguf", "bin", "model_embedding", "model_reranker"],
            "missing_required": [],
        }

    def tearDown(self):
        self._tmp.cleanup()

    def _manifest(self, **overrides):
        kwargs = dict(
            out_dir=self.out,
            asset_report=self.report,
            git_commit="abc1234",
            built_at="2026-07-26T12:00:00",
        )
        kwargs.update(overrides)
        return build_portable.build_manifest(**kwargs)

    def test_manifest_records_profile_provenance_and_schema(self):
        manifest = self._manifest()

        self.assertEqual(1, manifest["schema"])
        self.assertEqual("runtime", manifest["profile"])
        self.assertEqual("abc1234", manifest["git_commit"])
        self.assertEqual("2026-07-26T12:00:00", manifest["built_at"])

    def test_manifest_carries_the_runtime_layer_identity(self):
        """This hash is what a later application patch is matched against."""
        manifest = self._manifest()
        self.assertEqual(
            build_portable.runtime_lock_hash(), manifest["runtime_lock_sha256"]
        )

    def test_every_asset_directory_is_accounted_for(self):
        manifest = self._manifest()
        self.assertEqual(
            set(build_portable.ASSET_DIRS), set(manifest["components"])
        )

    def test_absent_components_say_how_to_obtain_them(self):
        manifest = self._manifest()

        for name in ("gguf", "bin", "model_embedding", "model_reranker"):
            component = manifest["components"][name]
            self.assertFalse(component["present"])
            self.assertTrue(
                component["note"].strip(),
                "an absent component must tell the operator what to do",
            )

    def test_present_components_record_their_measured_size(self):
        (self.out / "gguf").mkdir()
        (self.out / "gguf" / "weights.bin").write_bytes(b"x" * 2048)
        report = dict(self.report, included=["gguf"],
                      excluded=["bin", "model_embedding", "model_reranker"])

        manifest = self._manifest(asset_report=report)

        component = manifest["components"]["gguf"]
        self.assertTrue(component["present"])
        self.assertEqual(2048, component["bytes"])

    def test_writing_the_manifest_round_trips_through_the_shipped_reader(self):
        written = build_portable.write_manifest(self.out, self._manifest())

        self.assertEqual(release_bundle.MANIFEST_NAME, written.name)
        self.assertEqual(
            json.loads(written.read_text(encoding="utf-8")),
            release_bundle.read_manifest(self.out),
        )

    def test_reader_reports_a_missing_manifest_clearly(self):
        with self.assertRaises(FileNotFoundError):
            release_bundle.read_manifest(self.out)

    def test_manifest_is_declared_so_the_builder_ships_it(self):
        self.assertIn("release_bundle.py", build_portable.CORE_PY_FILES)


if __name__ == "__main__":
    unittest.main()
