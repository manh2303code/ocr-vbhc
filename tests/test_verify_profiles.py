"""verify.bat must not report a runtime-profile package as damaged.

The script hard-failed on gguf/, the two model directories and poppler. A
package built with --profile runtime omits those 16.5 GB on purpose, so the
operator saw "Package co the khong chay dung" and a non-zero exit code for a
package that was exactly what the build produced — and had no way to tell that
apart from an interrupted USB copy.
"""
import tempfile
import unittest
from pathlib import Path

import build_portable
import verify_plan


ROOT = Path(__file__).resolve().parents[1]

ALL_ASSETS = ("gguf", "bin", "model_embedding", "model_reranker")


def _manifest(profile, present=()):
    """A manifest shaped like the one build_portable writes into the package."""
    return {
        "schema": 1,
        "profile": profile,
        "components": {
            name: {
                "present": name in present,
                "bytes": 1024 if name in present else 0,
                "note": "" if name in present else "Supply {} from the asset bundle.".format(name),
            }
            for name in ALL_ASSETS
        },
    }


def _balanced(text, start):
    """Return the text up to the parenthesis that closes the one already open."""
    depth = 1
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[start:index], index + 1
    raise AssertionError("unbalanced parentheses in verify.bat at offset {}".format(start))


def _gate_bodies(text, gate):
    """Split `if "%GATE%"=="1" ( ... ) else ( ... )` into its two branches."""
    marker = 'if "%{}%"=="1" ('.format(gate)
    if marker not in text:
        raise AssertionError("verify.bat does not gate {} on the manifest".format(gate))
    checked, after = _balanced(text, text.index(marker) + len(marker))
    tail = text[after:]
    stripped = tail.lstrip()
    if not stripped.startswith("else ("):
        raise AssertionError("{} has no skip branch in verify.bat".format(gate))
    else_start = after + (len(tail) - len(stripped)) + len("else (")
    skipped, _ = _balanced(text, else_start)
    return checked, skipped


class VerifyPlanDecisionTests(unittest.TestCase):
    def test_full_package_still_checks_every_component(self):
        """Nothing may become optional for a package that claims to be complete."""
        checks = verify_plan.required_checks(_manifest("full", present=ALL_ASSETS))

        for name in ALL_ASSETS:
            self.assertIn(name, checks)
        self.assertIn("poppler", checks)
        self.assertEqual([], verify_plan.skipped_components(_manifest("full", present=ALL_ASSETS)))

    def test_runtime_package_checks_nothing_it_declares_absent(self):
        manifest = _manifest("runtime")

        checks = verify_plan.required_checks(manifest)

        for name in ALL_ASSETS:
            self.assertNotIn(name, checks)

    def test_package_without_a_manifest_is_verified_as_before(self):
        """Packages already in the field predate manifests; do not break them."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(verify_plan.load_manifest(Path(tmp)))

        checks = verify_plan.required_checks(None)
        for name in ALL_ASSETS + ("poppler",):
            self.assertIn(name, checks)
        self.assertEqual([], verify_plan.skipped_components(None))

    def test_unreadable_manifest_falls_back_to_checking_everything(self):
        """A manifest that cannot be parsed must never excuse a missing model."""
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / "BUILD_MANIFEST.json").write_text("{not json", encoding="utf-8")

            manifest = verify_plan.load_manifest(package)

        self.assertIsNone(manifest)
        self.assertIn("gguf", verify_plan.required_checks(manifest))

    def test_component_missing_from_the_manifest_is_still_checked(self):
        """An unknown component must fail loudly, not be silently excused."""
        manifest = {"schema": 1, "profile": "full", "components": {}}

        self.assertIn("gguf", verify_plan.required_checks(manifest))

    def test_skipped_components_carry_the_note_from_the_manifest(self):
        manifest = _manifest("runtime")

        skipped = dict(verify_plan.skipped_components(manifest))

        for name in ALL_ASSETS:
            self.assertIn(name, skipped)
            self.assertIn("asset bundle", skipped[name])

    def test_poppler_follows_the_profile_because_no_component_covers_it(self):
        """tools/poppler lives outside Git and outside the manifest components.

        A reduced profile ships "runtime and application only", so demanding the
        PDF renderer from it reproduces the very failure this change removes.
        """
        self.assertIn("poppler", verify_plan.required_checks(_manifest("full", present=ALL_ASSETS)))
        self.assertNotIn("poppler", verify_plan.required_checks(_manifest("runtime")))
        self.assertTrue(dict(verify_plan.skipped_components(_manifest("runtime")))["poppler"].strip())

    def test_classify_result_separates_a_declared_gap_from_damage(self):
        runtime = _manifest("runtime")
        full = _manifest("full", present=ALL_ASSETS)

        self.assertEqual(verify_plan.SKIPPED, verify_plan.classify_result(runtime, "gguf", False))
        self.assertEqual(verify_plan.ERROR, verify_plan.classify_result(full, "gguf", False))
        self.assertEqual(verify_plan.OK, verify_plan.classify_result(runtime, "gguf", True))
        self.assertEqual(verify_plan.OK, verify_plan.classify_result(full, "gguf", True))

    def test_a_declared_component_that_is_missing_is_damage(self):
        """present=true plus an empty directory means the copy was truncated."""
        manifest = _manifest("full", present=("gguf",))

        self.assertEqual(verify_plan.ERROR, verify_plan.classify_result(manifest, "gguf", False))

    def test_exit_codes_separate_a_reduced_package_from_a_broken_one(self):
        self.assertEqual(verify_plan.EXIT_OK, verify_plan.classify_exit_code(0, 0))
        self.assertEqual(verify_plan.EXIT_REDUCED, verify_plan.classify_exit_code(0, 4))
        self.assertEqual(verify_plan.EXIT_BROKEN, verify_plan.classify_exit_code(2, 0))
        # A package that is both reduced and damaged is damaged.
        self.assertEqual(verify_plan.EXIT_BROKEN, verify_plan.classify_exit_code(2, 4))
        self.assertEqual(
            3,
            len({verify_plan.EXIT_OK, verify_plan.EXIT_REDUCED, verify_plan.EXIT_BROKEN}),
        )


class VerifyPlanEmissionTests(unittest.TestCase):
    def test_notes_cannot_break_the_batch_parser(self):
        """cmd.exe parses an echoed ')' as the end of the if-block it sits in."""
        cleaned = verify_plan.batch_safe('binaries (cpu/ & cuda/) 100% "x" |y| <z> ^ !n!\r\n')

        for char in '|&<>^%()"!\r\n':
            self.assertNotIn(char, cleaned)

    def test_plan_lines_stay_three_pipe_separated_fields(self):
        manifest = _manifest("runtime")
        manifest["components"]["gguf"]["note"] = "weights (gguf/) | supply later"

        lines = verify_plan.plan_lines(manifest=manifest)

        self.assertIn("PROFILE|runtime", lines)
        skips = [line for line in lines if line.startswith("SKIP|")]
        self.assertTrue(skips)
        for line in skips:
            self.assertEqual(3, len(line.split("|")))

    def test_plan_reads_the_manifest_the_builder_actually_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            report = {
                "profile": "runtime",
                "included": [],
                "excluded": list(build_portable.ASSET_DIRS),
                "missing_required": [],
            }
            build_portable.write_manifest(
                package,
                build_portable.build_manifest(package, report, "abc1234", "2026-07-26T12:00:00"),
            )

            lines = verify_plan.plan_lines(package)

        self.assertIn("PROFILE|runtime", lines)
        skipped = {line.split("|")[1] for line in lines if line.startswith("SKIP|")}
        self.assertTrue(set(build_portable.ASSET_DIRS) <= skipped)
        self.assertNotIn("CHECK|gguf", lines)

    def test_full_package_plan_asks_for_every_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            for name in build_portable.ASSET_DIRS:
                (package / name).mkdir()
            report = {
                "profile": "full",
                "included": list(build_portable.ASSET_DIRS),
                "excluded": [],
                "missing_required": [],
            }
            build_portable.write_manifest(
                package,
                build_portable.build_manifest(package, report, "abc1234", "2026-07-26T12:00:00"),
            )

            lines = verify_plan.plan_lines(package)

        self.assertIn("PROFILE|full", lines)
        self.assertFalse([line for line in lines if line.startswith("SKIP|")])
        for name in build_portable.ASSET_DIRS + ("poppler",):
            self.assertIn("CHECK|" + name, lines)


class VerifyScriptTests(unittest.TestCase):
    def setUp(self):
        self.verify = (ROOT / "verify.bat").read_text(encoding="utf-8-sig")

    def test_script_asks_the_planner_before_it_judges_the_package(self):
        self.assertIn("verify_plan.py", self.verify)
        self.assertIn("BUILD_MANIFEST", self.verify)

    def test_gates_default_to_checking_everything(self):
        """No plan output — no python, no manifest — must mean the old behaviour."""
        for gate in ("CHECK_GGUF", "CHECK_MODEL_EMBEDDING", "CHECK_MODEL_RERANKER",
                     "CHECK_BIN", "CHECK_POPPLER"):
            self.assertIn('set "{}=1"'.format(gate), self.verify)

    def test_every_asset_check_sits_behind_its_gate(self):
        for gate, needles in (
            ("CHECK_GGUF", ("gguf\\chandra", "gguf\\chat")),
            ("CHECK_MODEL_EMBEDDING", ("model_embedding\\config.json",)),
            ("CHECK_MODEL_RERANKER", ("model_reranker\\config.json",)),
            ("CHECK_POPPLER", ("tools\\poppler",)),
        ):
            checked, skipped = _gate_bodies(self.verify, gate)
            for needle in needles:
                self.assertIn(needle, checked, gate)
                # No copy of the check may survive outside the gate, or the
                # runtime package still fails on an asset it never carried.
                self.assertEqual(self.verify.count(needle), checked.count(needle), needle)
            self.assertIn("ERRORS+=1", checked, gate)
            self.assertIn("[BO QUA]", skipped, gate)
            self.assertNotIn("ERRORS+=1", skipped, gate)
            self.assertIn("SKIPPED+=1", self.verify)

    def test_skipped_components_report_the_note_from_the_manifest(self):
        for note in ("NOTE_GGUF", "NOTE_MODEL_EMBEDDING", "NOTE_MODEL_RERANKER",
                     "NOTE_BIN", "NOTE_POPPLER"):
            self.assertIn("%{}%".format(note), self.verify)

    def test_core_application_checks_are_never_gated(self):
        """Only heavy assets are optional; a missing main.py is always damage."""
        for needle in ("main.py", "runtime\\python.exe", "QtWebEngineProcess.exe",
                       "assets\\app_icon.ico"):
            self.assertIn(needle, self.verify)
        for gate in ("CHECK_GGUF", "CHECK_MODEL_EMBEDDING", "CHECK_MODEL_RERANKER",
                     "CHECK_POPPLER"):
            checked, _ = _gate_bodies(self.verify, gate)
            self.assertNotIn("main.py", checked)
            self.assertNotIn("QtWebEngineProcess.exe", checked)

    def test_exit_codes_match_the_module_that_defines_them(self):
        for code in (verify_plan.EXIT_OK, verify_plan.EXIT_REDUCED, verify_plan.EXIT_BROKEN):
            self.assertIn('set "RC={}"'.format(code), self.verify)
        self.assertIn("exit /b %RC%", self.verify)
        self.assertNotIn("exit /b %ERRORS%", self.verify)


class VerifyPlanShippingTests(unittest.TestCase):
    def test_planner_is_shipped_to_the_target_machine(self):
        """verify.bat runs on the target, so the module has to travel with it."""
        self.assertIn("verify_plan.py", build_portable.CORE_PY_FILES)
        self.assertTrue((ROOT / "verify_plan.py").is_file())


if __name__ == "__main__":
    unittest.main()
