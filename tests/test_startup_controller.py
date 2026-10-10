import unittest
from pathlib import Path
from unittest import mock


from startup_controller import AppController, dispatch_startup_intent


class _Toggle:
    def __init__(self):
        self.checked = False

    def setChecked(self, value):
        self.checked = value


class _Input:
    def __init__(self):
        self.focused = False

    def setFocus(self):
        self.focused = True


class _Chat:
    def __init__(self):
        self.question_input = _Input()


class _Window:
    def __init__(self):
        self.calls = []
        self.act_chat = _Toggle()
        self.act_toggle_left_panel = _Toggle()
        self.chat_panel = _Chat()

    def add_file(self): self.calls.append("open_file")
    def add_files(self): self.calls.append("open_files")
    def add_folder(self): self.calls.append("import_folder")
    def show_document(self, doc_id): self.calls.append(("open_recent", doc_id))
    def _toggle_chat_panel(self, visible): self.calls.append(("open_chat", visible))
    def _toggle_left_panel(self, visible): self.calls.append(("open_library", visible))


class StartupIntentTests(unittest.TestCase):
    def test_all_actions_route_to_existing_window_methods(self):
        window = _Window()
        for action in ("open_file", "open_files", "import_folder", "open_library", "open_chat"):
            dispatch_startup_intent(window, action, {})
        dispatch_startup_intent(window, "open_recent", {"docId": 17})

        self.assertEqual(
            [
                "open_file", "open_files", "import_folder",
                ("open_library", True), ("open_chat", True), ("open_recent", 17),
            ],
            window.calls,
        )
        self.assertTrue(window.act_chat.checked)
        self.assertTrue(window.act_toggle_left_panel.checked)
        self.assertTrue(window.chat_panel.question_input.focused)

    def test_the_names_this_double_stands_in_for_exist_on_the_real_window(self):
        """The double would happily accept a method MainWindow does not have.

        That is how a route can pass its own test and still fail on launch: the
        first attempt at open_library reached for act_library and
        _toggle_library_panel, neither of which exists on MainWindow.
        """
        source = (
            Path(__file__).resolve().parents[1] / "main_window_1.py"
        ).read_text(encoding="utf-8-sig")

        missing = [
            name for name in vars(_Window())
            if name.startswith("act_") and "self.{} =".format(name) not in source
        ]
        missing += [
            name for name, value in vars(_Window).items()
            if not name.startswith("__") and callable(value)
            and "def {}(".format(name) not in source
        ]
        self.assertEqual([], sorted(missing))

    def test_invalid_recent_payload_is_ignored(self):
        window = _Window()
        dispatch_startup_intent(window, "open_recent", {"docId": "bad"})
        self.assertEqual([], window.calls)

    def test_home_reuses_the_existing_workspace_backend(self):
        class Signal:
            def connect(self, _callback):
                pass

        class Landing:
            def __init__(self, _snapshot):
                self.action_requested = Signal()
                self.destroyed = Signal()
                self.shown = False

            def showMaximized(self): self.shown = True
            def raise_(self): pass
            def activateWindow(self): pass

        class Main:
            def __init__(self): self.hidden = False
            def hide(self): self.hidden = True

        controller = AppController(object())
        main = Main()
        controller.main_window = main
        with mock.patch("startup_controller.LandingWindow", Landing):
            controller.show_landing()
        self.assertTrue(main.hidden)
        self.assertIs(main, controller.main_window)
        self.assertTrue(controller.landing.shown)


class LandingActionCoverageTests(unittest.TestCase):
    """Every action the landing screen may emit has to land somewhere.

    open_library sat in ALLOWED_ACTIONS and had its own button, but
    dispatch_startup_intent had no route for it, so pressing "Mở kho văn bản"
    opened the workspace and then silently did nothing else — the one action
    whose whole purpose is to show the library never showed it.
    """

    def test_every_allowed_action_is_dispatched(self):
        import landing_page

        unhandled = []
        for action in sorted(landing_page.ALLOWED_ACTIONS):
            window = _Window()
            payload = {"docId": 1} if action == "open_recent" else {}
            dispatch_startup_intent(window, action, payload)
            if not window.calls:
                unhandled.append(action)
        self.assertEqual([], unhandled)


if __name__ == "__main__":
    unittest.main()
