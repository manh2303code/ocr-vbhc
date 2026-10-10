import unittest
from pathlib import Path

import settings_dialog
from ui_commands import COMMANDS, command, landing_action_specs, shortcut_rows


ROOT = Path(__file__).resolve().parents[1]


class UICommandTests(unittest.TestCase):
    def test_shortcuts_are_unique_and_core_bindings_are_locked(self):
        shortcuts = [spec.shortcut.casefold() for spec in COMMANDS if spec.shortcut]
        self.assertEqual(len(shortcuts), len(set(shortcuts)))
        self.assertEqual("F1", command("shortcut_help").shortcut)
        self.assertEqual("Ctrl+K", command("command_palette").shortcut)
        self.assertEqual("Ctrl+Alt+O", command("add_folder").shortcut)

    def test_settings_shortcut_table_comes_from_registry(self):
        self.assertEqual(shortcut_rows(), settings_dialog.SHORTCUT_LIST)

    def test_landing_actions_are_derived_from_the_same_registry(self):
        specs = {item["action"]: item for item in landing_action_specs()}
        self.assertEqual(
            {"open_file", "open_files", "import_folder", "open_library", "open_chat"},
            set(specs),
        )
        self.assertEqual(command("add_file").shortcut, specs["open_file"]["shortcut"])
        script = (ROOT / "frontend" / "landing" / "landing.js").read_text(encoding="utf-8")
        self.assertIn("snapshot.commands", script)

    def test_main_has_one_f1_binding_and_safe_delete_router(self):
        source = (ROOT / "main_window_1.py").read_text(encoding="utf-8-sig")
        self.assertNotIn('ov_help.setShortcut(QKeySequence("F1"))', source)
        self.assertIn("_delete_current_document_safe", source)
        self.assertIn("_show_command_palette", source)


if __name__ == "__main__":
    unittest.main()
