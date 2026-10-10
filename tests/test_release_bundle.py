"""Splitting and checksums for artifacts that travel on removable media.

build_portable.py does not ship, so anything the target machine needs — joining
parts, verifying checksums, reading the manifest — lives in release_bundle.py,
which does.
"""
import unittest
from pathlib import Path
import tempfile

import release_bundle


GIB = 1024 ** 3


class SplitPlanTests(unittest.TestCase):
    """A pure planner, so the boundaries are testable without a 6 GB file."""

    def test_size_below_limit_is_a_single_part(self):
        plan = release_bundle.split_plan(10, limit=100)
        self.assertEqual(1, len(plan))
        self.assertEqual((0, 10), (plan[0].offset, plan[0].length))

    def test_size_exactly_at_the_limit_is_still_one_part(self):
        plan = release_bundle.split_plan(100, limit=100)
        self.assertEqual(1, len(plan))
        self.assertEqual(100, plan[0].length)

    def test_one_byte_over_the_limit_splits(self):
        plan = release_bundle.split_plan(101, limit=100)
        self.assertEqual(2, len(plan))
        self.assertEqual([100, 1], [part.length for part in plan])
        self.assertEqual([0, 100], [part.offset for part in plan])

    def test_empty_input_still_yields_one_part_so_join_round_trips(self):
        plan = release_bundle.split_plan(0, limit=100)
        self.assertEqual(1, len(plan))
        self.assertEqual(0, plan[0].length)

    def test_parts_cover_the_whole_input_without_gaps_or_overlap(self):
        plan = release_bundle.split_plan(6 * GIB, limit=2 * GIB - 1)
        self.assertEqual(6 * GIB, sum(part.length for part in plan))
        expected_offset = 0
        for part in plan:
            self.assertEqual(expected_offset, part.offset)
            expected_offset += part.length

    def test_part_names_sort_lexicographically_in_transfer_order(self):
        plan = release_bundle.split_plan(1500, limit=1)
        names = [part.name for part in plan]
        self.assertEqual(names, sorted(names))
        self.assertTrue(names[0].endswith(".part0001"), names[0])

    def test_non_positive_limit_is_rejected(self):
        for limit in (0, -1):
            with self.assertRaises(ValueError):
                release_bundle.split_plan(10, limit=limit)


class SplitJoinRoundTripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_split_then_join_reproduces_the_original_bytes(self):
        payload = bytes(range(256)) * 40
        source = self.root / "bundle.tar.gz"
        source.write_bytes(payload)

        parts = release_bundle.split_file(source, self.root / "out", limit=1000)
        self.assertGreater(len(parts), 1)

        rejoined = release_bundle.join_parts(parts, self.root / "rejoined.tar.gz")
        self.assertEqual(payload, rejoined.read_bytes())


class ChecksumTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.files = []
        for index in range(3):
            path = self.root / "bundle.tar.gz.part{:04d}".format(index + 1)
            path.write_bytes(b"payload-%d" % index)
            self.files.append(path)
        self.sums = release_bundle.write_checksums(self.files, self.root / "SHA256SUMS")

    def tearDown(self):
        self._tmp.cleanup()

    def test_checksum_file_lists_every_part(self):
        text = self.sums.read_text(encoding="utf-8")
        self.assertEqual(3, len(text.strip().splitlines()))
        for path in self.files:
            self.assertIn(path.name, text)

    def test_intact_parts_verify_clean(self):
        self.assertEqual([], release_bundle.verify_checksums(self.sums))

    def test_a_single_flipped_byte_is_detected(self):
        target = self.files[1]
        data = bytearray(target.read_bytes())
        data[0] ^= 0x01
        target.write_bytes(bytes(data))

        problems = release_bundle.verify_checksums(self.sums)
        self.assertEqual(1, len(problems))
        self.assertIn(target.name, problems[0])
        self.assertIn("mismatch", problems[0].lower())

    def test_a_missing_part_is_detected(self):
        self.files[2].unlink()

        problems = release_bundle.verify_checksums(self.sums)
        self.assertEqual(1, len(problems))
        self.assertIn("missing", problems[0].lower())

    def test_nested_files_are_recorded_by_relative_path(self):
        """Recording only the basename made every nested file verify as
        missing, which silently blocked patch bundles from ever being applied."""
        nested = self.root / "frontend" / "landing" / "index.html"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"<html>")
        sums = release_bundle.write_checksums(
            self.files + [nested], self.root / "NESTED_SUMS"
        )

        self.assertIn("frontend/landing/index.html", sums.read_text(encoding="utf-8"))
        self.assertEqual([], release_bundle.verify_checksums(sums))

    def test_checksum_format_is_the_standard_two_space_form(self):
        """So sha256sum -c on the target can read it without a custom tool."""
        first = self.sums.read_text(encoding="utf-8").splitlines()[0]
        digest, _, name = first.partition("  ")
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertTrue(name.startswith("bundle.tar.gz.part"))


if __name__ == "__main__":
    unittest.main()
