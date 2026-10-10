"""The runtime layer must be identified by an exact, hashable dependency set.

Two reasons, and the second is the one that matters for air-gap operations:

1. requirements_portable.txt pins only 7 of its 20 entries, so two builds a
   month apart resolve different wheels. An air-gapped site cannot hot-patch,
   so "whatever pip found that day" is not an acceptable definition of what is
   deployed.
2. The lock hash *is* the runtime layer version. An application patch declares
   the runtime it was built against; the target-side updater compares that hash
   against what is installed and refuses a patch that would land on a runtime
   it was never tested with.
"""
import re
import unittest
from pathlib import Path

import build_portable


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "requirements_portable.lock"
_PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[^\s;]+)")


def _lock_entries():
    entries = {}
    for line in LOCK_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN_RE.match(line)
        if match:
            entries[match.group("name").lower().replace("_", "-")] = match.group("version")
    return entries


class RuntimeLockTests(unittest.TestCase):
    def test_lock_file_exists(self):
        self.assertTrue(LOCK_PATH.is_file(), "requirements_portable.lock is missing")

    def test_every_entry_is_pinned_exactly(self):
        loose = []
        for line in LOCK_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if not _PIN_RE.match(line):
                loose.append(line)
        self.assertEqual([], loose, "lock entries must use == and nothing else")

    def test_lock_covers_every_declared_dependency(self):
        declared = set()
        for line in (ROOT / "requirements_portable.txt").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name = re.split(r"[=<>!~\[]", line)[0].strip().lower().replace("_", "-")
            if name:
                declared.add(name)

        missing = sorted(declared - set(_lock_entries()))
        self.assertEqual([], missing, "declared dependencies absent from the lock")

    def test_torch_is_pinned_because_the_builder_and_runtime_disagreed(self):
        # build_portable declared 2.8.0 while the shipped runtime carried
        # 2.1.2+cu118. Whichever wins, the lock must state it unambiguously.
        self.assertIn("torch", _lock_entries())

    def test_lock_hash_is_stable_and_content_addressed(self):
        first = build_portable.runtime_lock_hash()
        second = build_portable.runtime_lock_hash()
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_lock_hash_ignores_comments_and_ordering(self):
        """Reformatting the lock must not invent a new runtime layer version."""
        body = "b==2\na==1\n"
        noisy = "# generated\n\na==1\n\n# tail\nb==2\n"
        self.assertEqual(
            build_portable.hash_lock_content(body),
            build_portable.hash_lock_content(noisy),
        )

    def test_no_index_specific_local_versions(self):
        """A +cu118 style suffix pins to one index and fails against another.

        Generating the lock from pip freeze carried torchvision==0.16.2+cu118,
        which does not exist on the CPU index the builder downloads from, so
        the whole resolution failed.
        """
        local = {
            name: version
            for name, version in _lock_entries().items()
            if "+" in version
        }
        self.assertEqual({}, local)

    def test_deprecated_pipeline_packages_are_absent(self):
        """timm and ultralytics belong to the YOLO/Vintern pipeline retired in
        the 2026-04-17 refactor. Nothing imports them, and they are what pulled
        torchvision in."""
        entries = _lock_entries()
        for name in ("timm", "ultralytics", "torchvision", "torchaudio"):
            self.assertNotIn(name, entries)

    def test_lock_ships_with_the_package(self):
        """The target-side updater reads it to learn which runtime is installed."""
        self.assertIn(build_portable.RUNTIME_LOCK_FILE, build_portable.STATIC_FILES)

    def test_builder_torch_version_matches_the_lock(self):
        self.assertEqual(
            _lock_entries()["torch"].split("+")[0],
            build_portable.TORCH_VERSION,
            "TORCH_VERSION and the lock must not drift apart again",
        )


if __name__ == "__main__":
    unittest.main()
