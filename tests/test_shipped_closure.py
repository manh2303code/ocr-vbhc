"""Whatever the target machine has to run must be reachable from the package.

The asset bundle is checked and unpacked on the air-gapped machine, but
build_asset_bundle imported build_portable, which is build-side tooling and is
not shipped. So the two commands the operator actually needs on site — check
and restore — could not run in the one place they matter.

The rule generalises: any module declared in CORE_PY_FILES must only import
local modules that are themselves declared, or the package ships a broken
import graph that nothing catches until it is on a USB stick.
"""
import ast
import unittest
from pathlib import Path

import build_portable
import release_bundle


ROOT = Path(__file__).resolve().parents[1]


def _local_module_names():
    """Modules importable from the package root, by file name."""
    return {path.stem for path in ROOT.glob("*.py")}


def _imported_local_modules(filename):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
    local = _local_module_names()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.split(".")[0]
                if head in local:
                    found.add(head)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            head = node.module.split(".")[0]
            if head in local:
                found.add(head)
    return found


class ShippedClosureTests(unittest.TestCase):
    def test_shipped_modules_only_import_shipped_modules(self):
        shipped = {
            name[:-3] for name in build_portable.CORE_PY_FILES if name.endswith(".py")
        }
        broken = {}
        for name in sorted(shipped):
            if not (ROOT / (name + ".py")).is_file():
                continue
            missing = sorted(_imported_local_modules(name + ".py") - shipped)
            if missing:
                broken[name] = missing
        self.assertEqual({}, broken)

    def test_asset_bundle_ships_so_the_target_can_check_and_restore(self):
        self.assertIn("build_asset_bundle.py", build_portable.CORE_PY_FILES)

    def test_asset_bundle_does_not_reach_for_build_side_tooling(self):
        self.assertNotIn(
            "build_portable", _imported_local_modules("build_asset_bundle.py")
        )


class AssetConstantsTests(unittest.TestCase):
    """The asset vocabulary belongs to the shipped layer, not the builder."""

    def test_release_bundle_owns_the_asset_vocabulary(self):
        self.assertTrue(release_bundle.ASSET_DIRS)
        self.assertTrue(release_bundle.ASSET_NOTES)
        self.assertIn("runtime", release_bundle.BUILD_PROFILES)
        self.assertIn("full", release_bundle.BUILD_PROFILES)

    def test_builder_reuses_them_rather_than_keeping_a_second_copy(self):
        self.assertIs(build_portable.ASSET_DIRS, release_bundle.ASSET_DIRS)
        self.assertIs(build_portable.ASSET_NOTES, release_bundle.ASSET_NOTES)
        self.assertIs(build_portable.BUILD_PROFILES, release_bundle.BUILD_PROFILES)

    def test_every_asset_directory_has_a_note(self):
        """An absent component with no note tells the operator nothing."""
        for name in release_bundle.ASSET_DIRS:
            self.assertTrue(release_bundle.ASSET_NOTES.get(name, "").strip(), name)


class ConsoleSafeOutputTests(unittest.TestCase):
    """Operator-facing text must survive the target's console codepage.

    build_asset_bundle joined its reason strings with an em-dash. cp437 is a
    common default on Windows and cannot encode it, so print() raised
    UnicodeEncodeError — the tool crashed at exactly the moment it was supposed
    to tell the operator which asset was missing.
    """

    def _assert_encodable(self, text, where):
        try:
            str(text).encode("cp437")
        except UnicodeEncodeError as exc:
            self.fail("{} is not printable on a cp437 console: {}".format(where, exc))

    def test_asset_notes_print_on_a_legacy_console(self):
        for name, note in release_bundle.ASSET_NOTES.items():
            self._assert_encodable(note, "ASSET_NOTES[{!r}]".format(name))

    def test_missing_asset_reasons_print_on_a_legacy_console(self):
        import build_asset_bundle

        package = {
            "schema": 1,
            "profile": "runtime",
            "components": {
                name: {"present": False, "bytes": 0, "note": ""}
                for name in release_bundle.ASSET_DIRS
            },
        }
        bundle = {
            "schema": 1,
            "kind": "asset-bundle",
            "components": {
                name: {"present": False, "bytes": 0, "sha256": "", "files": 0,
                       "note": release_bundle.ASSET_NOTES[name]}
                for name in release_bundle.ASSET_DIRS
            },
        }

        result = build_asset_bundle.check_combined_package(
            package, bundle, profile="full"
        )

        self.assertTrue(result.reasons)
        for reason in result.reasons:
            self._assert_encodable(reason, "check_combined_package reason")

    # Only these reach a console. The Qt modules are full of Vietnamese UI text
    # and that is correct: Qt renders Unicode itself and never passes through
    # the console codepage.
    CONSOLE_MODULES = (
        "build_asset_bundle.py",
        "app_updater.py",
        "verify_plan.py",
        "release_bundle.py",
    )

    def test_console_tools_pass_nothing_a_legacy_console_rejects_to_print(self):
        """Guards the class of bug, not just the one em-dash."""
        offenders = []
        for name in self.CONSOLE_MODULES:
            path = ROOT / name
            if not path.is_file():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            docstrings = {
                id(node.body[0].value)
                for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant):
                    continue
                if not isinstance(node.value, str) or id(node) in docstrings:
                    continue
                try:
                    node.value.encode("cp437")
                except UnicodeEncodeError:
                    offenders.append("{}:{}".format(name, node.lineno))
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
