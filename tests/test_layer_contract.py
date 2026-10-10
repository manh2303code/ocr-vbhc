"""The three layers version independently, and a patch must declare its needs.

app is 5.2 MB and changed 64 times in three months; runtime is 6.2 GB and
changed four times in the project's history; assets are 16 GB and effectively
never change. Shipping all three for every application fix means carrying 23 GB
across an air gap by hand.

Independent layer versions make a 5 MB patch possible. The safety condition is
that the target refuses a patch built against a runtime it does not have, and
says which layer is missing rather than failing at first launch.
"""
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import build_portable
import release_bundle


INSTALLED_RUNTIME = "a" * 64
OTHER_RUNTIME = "b" * 64


def _installed(runtime=INSTALLED_RUNTIME, assets=("gguf", "bin"), version="2.0.0"):
    return {
        "schema": 1,
        "layers": {
            "app": {"version": version},
            "runtime": {"lock_sha256": runtime},
            "assets": {"present": list(assets)},
        },
    }


def _patch(requires_runtime=INSTALLED_RUNTIME, requires_assets=(), version="2.1.0"):
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


class ManifestLayerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name)
        self.report = {
            "profile": "runtime",
            "included": [],
            "excluded": list(build_portable.ASSET_DIRS),
            "missing_required": [],
        }

    def tearDown(self):
        self._tmp.cleanup()

    def _manifest(self):
        return build_portable.build_manifest(
            out_dir=self.out,
            asset_report=self.report,
            git_commit="abc1234",
            built_at="2026-07-26T12:00:00",
        )

    def test_manifest_carries_three_independently_versioned_layers(self):
        layers = self._manifest()["layers"]
        self.assertEqual({"app", "runtime", "assets"}, set(layers))

    def test_app_layer_declares_the_runtime_it_was_built_against(self):
        app = self._manifest()["layers"]["app"]
        self.assertEqual(build_portable.runtime_lock_hash(), app["requires_runtime"])
        self.assertTrue(app["version"])
        self.assertEqual("abc1234", app["commit"])

    def test_runtime_layer_is_identified_by_its_lock(self):
        runtime = self._manifest()["layers"]["runtime"]
        self.assertEqual(build_portable.runtime_lock_hash(), runtime["lock_sha256"])
        self.assertEqual(build_portable.PYTHON_VERSION, runtime["python_version"])

    def test_asset_layer_separates_present_from_absent(self):
        assets = self._manifest()["layers"]["assets"]
        self.assertEqual([], assets["present"])
        self.assertEqual(sorted(build_portable.ASSET_DIRS), sorted(assets["absent"]))


class CompatibilityTests(unittest.TestCase):
    """Pure decision logic, so the updater can be trusted before it writes."""

    def test_matching_runtime_and_assets_is_compatible(self):
        self.assertEqual(
            [],
            release_bundle.compatibility_problems(
                _patch(requires_assets=["gguf"]), _installed()
            ),
        )

    def test_patch_for_a_different_runtime_is_refused_by_name(self):
        problems = release_bundle.compatibility_problems(
            _patch(requires_runtime=OTHER_RUNTIME), _installed()
        )

        self.assertEqual(1, len(problems))
        self.assertIn("runtime", problems[0].lower())
        # The operator has to learn which layer to fetch, not just that it failed.
        self.assertIn(OTHER_RUNTIME[:12], problems[0])
        self.assertIn(INSTALLED_RUNTIME[:12], problems[0])

    def test_missing_required_asset_is_named(self):
        problems = release_bundle.compatibility_problems(
            _patch(requires_assets=["gguf", "model_reranker"]), _installed()
        )

        self.assertEqual(1, len(problems))
        self.assertIn("model_reranker", problems[0])
        self.assertNotIn("gguf", problems[0])

    def test_downgrade_is_refused(self):
        """An air-gapped site works from whatever USB is at hand; an older
        patch must not silently replace a newer install."""
        problems = release_bundle.compatibility_problems(
            _patch(version="1.9.0"), _installed(version="2.0.0")
        )

        self.assertEqual(1, len(problems))
        self.assertIn("older", problems[0].lower())

    def test_same_version_reinstall_is_allowed(self):
        self.assertEqual(
            [],
            release_bundle.compatibility_problems(
                _patch(version="2.0.0"), _installed(version="2.0.0")
            ),
        )

    def test_every_mismatch_is_reported_at_once(self):
        """One trip to the air-gapped room should surface every blocker."""
        problems = release_bundle.compatibility_problems(
            _patch(
                requires_runtime=OTHER_RUNTIME,
                requires_assets=["model_embedding"],
                version="1.0.0",
            ),
            _installed(),
        )
        self.assertEqual(3, len(problems))

    def test_unreadable_manifest_is_refused_rather_than_guessed(self):
        problems = release_bundle.compatibility_problems({}, _installed())
        self.assertTrue(problems)
        self.assertIn("manifest", problems[0].lower())


if __name__ == "__main__":
    unittest.main()


class VersionResolutionTests(unittest.TestCase):
    """The rehearsal build recorded "d94102c" as the application version.

    git describe fails in a shallow CI checkout, so app_version fell back to the
    commit hash. Two things then break: the version no longer names a release,
    and _version_tuple turns hashes into arbitrary numbers — "abc1234" parses as
    (1234,) and "d94102c" as (94102,), so which one counts as newer depends on
    the hexadecimal digits. The downgrade guard becomes a coin flip.
    """

    def test_explicit_version_overrides_git(self):
        with mock.patch.dict(os.environ, {"OCR_APP_VERSION": "v3.2.1"}):
            self.assertEqual("v3.2.1", build_portable.app_version())

    def test_blank_override_is_ignored(self):
        with mock.patch.dict(os.environ, {"OCR_APP_VERSION": "  "}):
            self.assertNotEqual("  ", build_portable.app_version())

    def test_only_release_tags_are_considered(self):
        """The repository carries checkpoint/* tags that are not releases."""
        self.assertIn("v*", build_portable.RELEASE_TAG_GLOB)

    def test_a_commit_hash_is_not_treated_as_a_release_version(self):
        self.assertFalse(release_bundle.is_release_version("d94102c"))
        self.assertFalse(release_bundle.is_release_version("abc1234"))
        self.assertTrue(release_bundle.is_release_version("v2.1.0"))
        self.assertTrue(release_bundle.is_release_version("2.1.0"))

    def test_downgrade_check_is_skipped_when_either_side_is_unversioned(self):
        """Refusing on an unorderable comparison would block valid patches;
        accepting silently would let a real downgrade through. Neither is safe,
        so the guard says it cannot decide."""
        problems = release_bundle.compatibility_problems(
            _patch(version="abc1234"), _installed(version="v2.0.0")
        )

        self.assertEqual(1, len(problems))
        self.assertIn("cannot be ordered", problems[0].lower())
        self.assertIn("abc1234", problems[0])

    def test_ordered_versions_still_refuse_a_downgrade(self):
        problems = release_bundle.compatibility_problems(
            _patch(version="v1.9.0"), _installed(version="v2.0.0")
        )
        self.assertEqual(1, len(problems))
        self.assertIn("older", problems[0].lower())
