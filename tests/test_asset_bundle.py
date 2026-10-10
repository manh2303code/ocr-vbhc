"""The runtime package and the asset bundle are cut on different machines.

CI can only build the runtime layer, because the 16 GB of weights and llama.cpp
binaries live outside Git. They are packed once by hand on a release machine and
carried across the air gap on USB. Nothing checked that the two halves fit
together: an operator who unpacked runtime + assets onto a target had no way to
tell a complete installation from one missing model_reranker/, because the
application starts either way and only degrades silently at query time. Nor was
there any way to move 16 GB at all — a single archive exceeds the 2 GiB release
asset ceiling that release_bundle.split_file exists to work around.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import build_asset_bundle
import build_portable
import release_bundle


class AssetBundleTestCase(unittest.TestCase):
    """A few KB standing in for 16 GB; every code path here is size-agnostic."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source = self.root / "src"
        self.bundle = self.root / "bundle"
        for name in build_portable.ASSET_DIRS:
            payload = self.source / name / "{}.bin".format(name)
            payload.parent.mkdir(parents=True)
            payload.write_bytes(name.encode("ascii") * 64)
        # bin/ is the one asset dir with a nested layout the archive must keep.
        nested = self.source / "bin" / "cuda" / "llama-server.exe"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"cuda-binary" * 32)

    def tearDown(self):
        self._tmp.cleanup()

    def pack(self, **overrides):
        options = dict(
            source_root=self.source,
            out_dir=self.bundle,
            built_at="2026-07-26T12:00:00",
            source_commit="abc1234",
            # Small enough that a few KB of fixture still exercises the split.
            part_limit=512,
        )
        options.update(overrides)
        return build_asset_bundle.pack_asset_bundle(**options)

    def runtime_manifest(self, included=()):
        """A real BUILD_MANIFEST.json, not a hand-written stand-in.

        Compatibility between the two manifests is the point of the feature, so
        the fixture has to come out of build_portable itself.
        """
        included = list(included)
        package = self.root / "package"
        package.mkdir(exist_ok=True)
        for name in included:
            (package / name).mkdir(parents=True, exist_ok=True)
            (package / name / "payload.bin").write_bytes(b"x" * 32)
        report = {
            "profile": "full" if len(included) == len(build_portable.ASSET_DIRS)
            else "runtime",
            "included": included,
            "excluded": [n for n in build_portable.ASSET_DIRS if n not in included],
            "missing_required": [],
        }
        return build_portable.build_manifest(
            package, report, "abc1234", "2026-07-26T12:00:00"
        )


class AssetManifestTests(AssetBundleTestCase):
    def test_manifest_records_schema_kind_and_provenance(self):
        manifest = self.pack()

        self.assertEqual(build_asset_bundle.ASSET_SCHEMA, manifest["schema"])
        self.assertEqual(build_asset_bundle.ASSET_KIND, manifest["kind"])
        self.assertEqual("2026-07-26T12:00:00", manifest["built_at"])
        self.assertEqual("abc1234", manifest["source_commit"])

    def test_every_asset_directory_is_accounted_for_with_size_and_digest(self):
        manifest = self.pack()

        self.assertEqual(
            set(build_portable.ASSET_DIRS), set(manifest["components"])
        )
        for name in build_portable.ASSET_DIRS:
            component = manifest["components"][name]
            self.assertTrue(component["present"], name)
            self.assertGreater(component["bytes"], 0, name)
            self.assertRegex(component["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(component["files"], 0, name)

    def test_component_digest_matches_a_fresh_scan_of_the_source(self):
        manifest = self.pack()

        expected = build_asset_bundle.directory_digest(
            build_asset_bundle.scan_asset_dir(self.source / "bin")
        )
        self.assertEqual(expected, manifest["components"]["bin"]["sha256"])

    def test_an_asset_left_out_on_purpose_is_recorded_not_omitted(self):
        """A partial re-cut must still describe the whole asset set."""
        manifest = self.pack(names=["gguf", "bin"])

        absent = manifest["components"]["model_reranker"]
        self.assertFalse(absent["present"])
        self.assertEqual(0, absent["bytes"])
        self.assertTrue(absent["note"].strip(), "an absent asset must say what breaks")

    def test_manifest_lists_the_parts_that_were_actually_written(self):
        manifest = self.pack()

        names = [part["name"] for part in manifest["parts"]]
        self.assertGreater(len(names), 1, "a 512-byte limit must force a split")
        self.assertEqual(names, sorted(names), "operators copy these in name order")
        for part in manifest["parts"]:
            self.assertTrue((self.bundle / part["name"]).is_file(), part["name"])
            self.assertRegex(part["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            manifest["archive"]["bytes"],
            sum(part["bytes"] for part in manifest["parts"]),
        )

    def test_manifest_is_written_beside_the_parts_and_reads_back(self):
        written = self.pack()

        path = self.bundle / build_asset_bundle.ASSET_MANIFEST_NAME
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")),
            build_asset_bundle.read_asset_manifest(self.bundle),
        )
        self.assertEqual(written, build_asset_bundle.read_asset_manifest(self.bundle))

    def test_asset_manifest_does_not_collide_with_the_package_manifest(self):
        """Both land in the same directory once the bundle is unpacked in place."""
        self.assertEqual("ASSET_MANIFEST.json", build_asset_bundle.ASSET_MANIFEST_NAME)
        self.assertNotEqual(
            release_bundle.MANIFEST_NAME, build_asset_bundle.ASSET_MANIFEST_NAME
        )

    def test_checksums_use_the_shipped_writer_and_cover_every_part(self):
        manifest = self.pack()

        text = (self.bundle / release_bundle.CHECKSUM_NAME).read_text(encoding="utf-8")
        self.assertEqual(len(manifest["parts"]), len(text.strip().splitlines()))
        published = build_asset_bundle.parse_checksums(text)
        for part in manifest["parts"]:
            # A manifest that disagrees with SHA256SUMS would send the operator
            # chasing a corruption that is not there.
            self.assertEqual(part["sha256"], published[part["name"]], part["name"])

    def test_default_part_limit_stays_under_the_two_gigabyte_ceiling(self):
        self.assertLessEqual(build_asset_bundle.ASSET_PART_LIMIT, 2 * 1024 ** 3)
        self.assertEqual(
            release_bundle.DEFAULT_PART_LIMIT, build_asset_bundle.ASSET_PART_LIMIT
        )

    def test_missing_source_directory_is_refused_not_silently_skipped(self):
        shutil.rmtree(self.source / "bin")

        with self.assertRaises(build_asset_bundle.AssetSourceError) as caught:
            self.pack()
        self.assertIn("bin", str(caught.exception))

    def test_empty_source_directory_is_refused(self):
        """build_portable leaves an empty placeholder dir for every excluded asset.

        Packing one of those would produce a bundle that looks complete and
        installs nothing.
        """
        for item in (self.source / "model_reranker").iterdir():
            item.unlink()

        with self.assertRaises(build_asset_bundle.AssetSourceError) as caught:
            self.pack()
        self.assertIn("model_reranker", str(caught.exception))

    def test_packing_nothing_is_rejected(self):
        with self.assertRaises(ValueError):
            self.pack(names=[])

    def test_unknown_asset_name_is_rejected(self):
        with self.assertRaises(ValueError):
            self.pack(names=["gguf", "not_an_asset"])


class ArchiveFormatTests(AssetBundleTestCase):
    def test_uncompressed_tar_is_the_default(self):
        """GGUF and safetensors weights are already quantised; gzip buys ~nothing
        on 16 GB and costs hours of single-threaded CPU on the release machine."""
        manifest = self.pack()

        self.assertEqual("none", manifest["archive"]["compression"])
        self.assertTrue(manifest["archive"]["name"].endswith(".tar"),
                        manifest["archive"]["name"])

    def test_gzip_is_available_when_the_operator_asks_for_it(self):
        manifest = self.pack(compression="gz")

        self.assertEqual("gz", manifest["archive"]["compression"])
        self.assertTrue(manifest["archive"]["name"].endswith(".tar.gz"),
                        manifest["archive"]["name"])

    def test_unknown_compression_is_rejected(self):
        with self.assertRaises(ValueError):
            self.pack(compression="brotli")

    def test_the_intermediate_archive_is_not_left_behind(self):
        """Keeping it would need a second 16 GB of free space for no benefit."""
        manifest = self.pack()

        self.assertFalse((self.bundle / manifest["archive"]["name"]).exists())


class DirectoryDigestTests(unittest.TestCase):
    """Pure, so the 16 GB path is covered by a three-entry list."""

    def test_digest_ignores_the_order_the_tree_was_walked_in(self):
        entries = [("gguf/a.bin", 3, "aa"), ("gguf/b.bin", 4, "bb")]

        self.assertEqual(
            build_asset_bundle.directory_digest(entries),
            build_asset_bundle.directory_digest(list(reversed(entries))),
        )

    def test_digest_changes_when_a_file_is_renamed(self):
        original = [("gguf/a.bin", 3, "aa")]
        renamed = [("gguf/b.bin", 3, "aa")]

        self.assertNotEqual(
            build_asset_bundle.directory_digest(original),
            build_asset_bundle.directory_digest(renamed),
        )

    def test_digest_changes_when_content_changes(self):
        original = [("gguf/a.bin", 3, "aa")]
        edited = [("gguf/a.bin", 3, "ab")]

        self.assertNotEqual(
            build_asset_bundle.directory_digest(original),
            build_asset_bundle.directory_digest(edited),
        )

    def test_digest_is_independent_of_the_path_separator(self):
        """A bundle cut on Windows has to verify on the Linux CI runner."""
        windows = [("bin\\cuda\\llama-server.exe", 5, "cc")]
        posix = [("bin/cuda/llama-server.exe", 5, "cc")]

        self.assertEqual(
            build_asset_bundle.directory_digest(windows),
            build_asset_bundle.directory_digest(posix),
        )


class CombinedPackageCheckTests(AssetBundleTestCase):
    def test_runtime_package_plus_full_bundle_is_complete(self):
        check = build_asset_bundle.check_combined_package(
            self.runtime_manifest(), self.pack()
        )

        self.assertTrue(check.complete, check.reasons)
        self.assertEqual([], check.reasons)
        self.assertEqual([], check.missing)

    def test_the_check_says_which_half_each_asset_comes_from(self):
        """The operator has to know which half to re-copy when something is off."""
        check = build_asset_bundle.check_combined_package(
            self.runtime_manifest(included=["bin"]), self.pack()
        )

        self.assertEqual("package", check.sources["bin"])
        self.assertEqual("bundle", check.sources["gguf"])

    def test_a_bundle_missing_one_asset_names_it_and_the_consequence(self):
        partial = self.pack(
            names=[n for n in build_portable.ASSET_DIRS if n != "model_reranker"]
        )

        check = build_asset_bundle.check_combined_package(
            self.runtime_manifest(), partial
        )

        self.assertFalse(check.complete)
        self.assertEqual(["model_reranker"], check.missing)
        self.assertEqual(1, len(check.reasons), check.reasons)
        reason = check.reasons[0]
        self.assertIn("model_reranker", reason)
        # Naming the gap is not enough; the operator needs to know what breaks.
        self.assertIn("result ordering degrades", reason)

    def test_every_missing_asset_is_reported_not_just_the_first(self):
        partial = self.pack(names=["bin"])

        check = build_asset_bundle.check_combined_package(
            self.runtime_manifest(), partial
        )

        self.assertEqual(
            ["gguf", "model_embedding", "model_reranker"], check.missing
        )
        self.assertEqual(3, len(check.reasons), check.reasons)

    def test_a_gap_the_runtime_package_already_fills_is_not_reported(self):
        partial = self.pack(
            names=[n for n in build_portable.ASSET_DIRS if n != "gguf"]
        )

        check = build_asset_bundle.check_combined_package(
            self.runtime_manifest(included=["gguf"]), partial
        )

        self.assertTrue(check.complete, check.reasons)

    def test_a_component_recorded_present_but_empty_does_not_count(self):
        manifest = self.pack()
        manifest["components"]["bin"]["bytes"] = 0

        check = build_asset_bundle.check_combined_package(
            self.runtime_manifest(), manifest
        )

        self.assertFalse(check.complete)
        self.assertEqual(["bin"], check.missing)
        self.assertIn("zero bytes", check.reasons[0])
        self.assertIn("bin", check.reasons[0])

    def test_the_runtime_profile_needs_no_assets_at_all(self):
        check = build_asset_bundle.check_combined_package(
            self.runtime_manifest(), self.pack(names=["bin"]), profile="runtime"
        )

        self.assertTrue(check.complete, check.reasons)
        self.assertEqual("runtime", check.profile)

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            build_asset_bundle.check_combined_package(
                self.runtime_manifest(), self.pack(), profile="nope"
            )

    def test_manifests_passed_in_the_wrong_order_are_named_not_passed(self):
        check = build_asset_bundle.check_combined_package(
            self.pack(), self.runtime_manifest()
        )

        self.assertFalse(check.complete)
        self.assertTrue(
            any(build_asset_bundle.ASSET_KIND in reason for reason in check.reasons),
            check.reasons,
        )

    def test_a_newer_asset_schema_is_refused_rather_than_misread(self):
        manifest = self.pack()
        manifest["schema"] = build_asset_bundle.ASSET_SCHEMA + 1

        check = build_asset_bundle.check_combined_package(
            self.runtime_manifest(), manifest
        )

        self.assertFalse(check.complete)
        self.assertTrue(
            any("schema" in reason.lower() for reason in check.reasons), check.reasons
        )

    def test_a_package_manifest_without_components_is_named(self):
        """Packages built before BUILD_MANIFEST.json carried components at all."""
        check = build_asset_bundle.check_combined_package({"profile": "runtime"},
                                                          self.pack())

        self.assertFalse(check.complete)
        self.assertTrue(
            any("components" in reason.lower() for reason in check.reasons),
            check.reasons,
        )


class UnsafeMemberTests(unittest.TestCase):
    """The archive arrives on a USB stick, so treat its member names as input."""

    def test_ordinary_members_are_accepted(self):
        self.assertEqual(
            [],
            build_asset_bundle.unsafe_members(
                ["gguf", "gguf/chat/model.gguf", "bin/cuda/llama-server.exe",
                 "gguf/./chat/a.bin", "bin/cuda/../cpu/x.exe"]
            ),
        )

    def test_members_that_escape_the_destination_are_rejected(self):
        rejected = build_asset_bundle.unsafe_members(
            ["gguf/ok.bin", "../escape.bin", "/abs/path.bin", "bin/../../x",
             "C:/windows/system32/evil.dll"]
        )

        self.assertEqual(
            ["../escape.bin", "/abs/path.bin", "bin/../../x",
             "C:/windows/system32/evil.dll"],
            rejected,
        )


class BundleIntegrityTests(AssetBundleTestCase):
    def test_an_intact_bundle_verifies_clean(self):
        self.pack()

        self.assertEqual([], build_asset_bundle.verify_bundle(self.bundle))

    def test_a_flipped_byte_in_one_part_is_caught_before_installation(self):
        manifest = self.pack()
        target = self.bundle / manifest["parts"][1]["name"]
        data = bytearray(target.read_bytes())
        data[0] ^= 0x01
        target.write_bytes(bytes(data))

        problems = build_asset_bundle.verify_bundle(self.bundle)

        self.assertEqual(1, len(problems), problems)
        self.assertIn(target.name, problems[0])
        self.assertIn("mismatch", problems[0].lower())

    def test_a_part_left_behind_on_the_usb_stick_is_reported_missing(self):
        manifest = self.pack()
        (self.bundle / manifest["parts"][-1]["name"]).unlink()

        problems = build_asset_bundle.verify_bundle(self.bundle)

        self.assertTrue(any("missing" in p.lower() for p in problems), problems)

    def test_a_part_the_manifest_never_declared_is_reported(self):
        """A stale part from an earlier cut would corrupt the join silently."""
        manifest = self.pack()
        stray = self.bundle / (manifest["parts"][-1]["name"][:-1] + "9")
        stray.write_bytes(b"stale")

        problems = build_asset_bundle.verify_bundle(self.bundle)

        self.assertTrue(any(stray.name in p for p in problems), problems)

    def test_restoring_reproduces_the_asset_tree_byte_for_byte(self):
        for compression in ("none", "gz"):
            with self.subTest(compression=compression):
                shutil.rmtree(self.bundle, ignore_errors=True)
                self.pack(compression=compression)
                destination = self.root / "restored-{}".format(compression)

                build_asset_bundle.restore_assets(self.bundle, destination)

                for original in sorted(self.source.rglob("*")):
                    if not original.is_file():
                        continue
                    relative = original.relative_to(self.source)
                    self.assertEqual(
                        original.read_bytes(),
                        (destination / relative).read_bytes(),
                        relative,
                    )

    def test_restoring_refuses_a_corrupt_transfer(self):
        manifest = self.pack()
        target = self.bundle / manifest["parts"][0]["name"]
        target.write_bytes(b"\x00" * target.stat().st_size)

        with self.assertRaises(build_asset_bundle.BundleIntegrityError) as caught:
            build_asset_bundle.restore_assets(self.bundle, self.root / "restored")
        self.assertIn(target.name, str(caught.exception))

    def test_restoring_leaves_no_joined_archive_behind(self):
        self.pack()
        destination = self.root / "restored"

        build_asset_bundle.restore_assets(self.bundle, destination)

        leftovers = [
            path.name for path in destination.parent.iterdir()
            if path.name.startswith("assets.tar")
        ]
        self.assertEqual([], leftovers)


class CommandLineTests(unittest.TestCase):
    def test_cli_exposes_pack_verify_and_check(self):
        parser = build_asset_bundle.build_arg_parser()

        self.assertEqual("pack", parser.parse_args(["pack"]).command)
        self.assertEqual("verify", parser.parse_args(["verify", "bundle"]).command)
        checked = parser.parse_args(["check", "package", "bundle"])
        self.assertEqual("check", checked.command)
        self.assertEqual("full", checked.profile)

    def test_pack_defaults_to_an_uncompressed_tar_of_every_asset(self):
        args = build_asset_bundle.build_arg_parser().parse_args(["pack"])

        self.assertEqual("none", args.compress)
        self.assertEqual(list(build_portable.ASSET_DIRS), args.assets)


if __name__ == "__main__":
    unittest.main()
