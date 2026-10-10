import re
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from landing_data import LandingSnapshotService
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton

from landing_page import ALLOWED_ACTIONS, LandingBridge, LandingWindow


ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "frontend" / "landing"


class LandingSnapshotTests(unittest.TestCase):
    def test_snapshot_is_read_only_and_returns_five_newest_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "documents.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.executescript(
                    """
                    CREATE TABLE documents (
                        id INTEGER PRIMARY KEY,
                        file_name TEXT,
                        last_modified TEXT,
                        page_count INTEGER
                    );
                    CREATE TABLE document_versions (
                        document_id INTEGER,
                        version_number INTEGER,
                        so_ki_hieu TEXT
                    );
                    CREATE TABLE document_chunks (id INTEGER PRIMARY KEY);
                    """
                )
                for doc_id in range(1, 8):
                    conn.execute(
                        "INSERT INTO documents VALUES (?, ?, ?, ?)",
                        (doc_id, f"van-ban-{doc_id}.pdf", f"2026-07-{doc_id:02d}", doc_id),
                    )
                    conn.execute(
                        "INSERT INTO document_versions VALUES (?, 1, ?)",
                        (doc_id, f"Số {doc_id}"),
                    )
                conn.executemany(
                    "INSERT INTO document_chunks(id) VALUES (?)", [(1,), (2,), (3,)]
                )
                conn.commit()

            snapshot = LandingSnapshotService(
                db_path,
                base_dir=root,
                theme_getter=lambda: "dark",
            ).get_snapshot()

            self.assertEqual(7, snapshot["documentCount"])
            self.assertEqual(3, snapshot["chunkCount"])
            self.assertEqual([7, 6, 5, 4, 3], [item["id"] for item in snapshot["recentDocuments"]])
            self.assertEqual("dark", snapshot["theme"])
            self.assertTrue(snapshot["offline"])

            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(7, conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])

    def test_missing_database_returns_safe_empty_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = LandingSnapshotService(
                root / "missing.db", base_dir=root
            ).get_snapshot()
            self.assertEqual(0, snapshot["documentCount"])
            self.assertEqual([], snapshot["recentDocuments"])
            self.assertEqual("missing", snapshot["models"]["ocr"])


class LandingBridgeTests(unittest.TestCase):
    def test_valid_action_emits_once_and_invalid_action_is_rejected(self):
        bridge = LandingBridge(lambda: {"offline": True})
        received = []
        rejected = []
        bridge.action_requested.connect(lambda action, payload: received.append((action, payload)))
        bridge.action_rejected.connect(rejected.append)

        bridge.requestAction("open_recent", json.dumps({"docId": 7}))
        bridge.requestAction("browse_web", "{}")

        self.assertEqual([("open_recent", {"docId": 7})], received)
        self.assertEqual(1, len(rejected))
        self.assertEqual(
            {
                "open_file", "open_files", "import_folder", "open_library",
                "open_chat", "open_recent",
            },
            ALLOWED_ACTIONS,
        )

    def test_recent_document_requires_positive_integer_id(self):
        bridge = LandingBridge(lambda: {})
        received = []
        bridge.action_requested.connect(lambda *args: received.append(args))
        for payload in ("{}", '{"docId": 0}', '{"docId": "bad"}', "not json"):
            bridge.requestAction("open_recent", payload)
        self.assertEqual([], received)


class LandingAssetTests(unittest.TestCase):
    def test_landing_is_fully_local_and_has_required_assets(self):
        index = (LANDING / "index.html").read_text(encoding="utf-8")
        styles = (LANDING / "landing.css").read_text(encoding="utf-8")
        script = (LANDING / "landing.js").read_text(encoding="utf-8")
        combined = (index + script).lower()
        self.assertIn("content-security-policy", combined)
        self.assertIn("connect-src 'none'", combined)
        self.assertNotIn("https://", combined)
        self.assertNotIn("http://", combined)
        self.assertNotIn("mascot", combined)
        self.assertIn('../../assets/app_icon_256.png', index)
        self.assertNotIn('>OV<', index)
        self.assertTrue((ROOT / "assets" / "app_icon_256.png").is_file())
        for relative in ("landing.css", "landing.js", "vendor/gsap.min.js"):
            self.assertTrue((LANDING / relative).is_file(), relative)
        shell = (ROOT / "landing_page.py").read_text(encoding="utf-8")
        self.assertIn("downloadRequested.connect", shell)
        self.assertIn("LocalContentCanAccessRemoteUrls", shell)

    def test_actions_use_pointer_3d_highlight_without_a_fixed_primary_card(self):
        index = (LANDING / "index.html").read_text(encoding="utf-8")
        styles = (LANDING / "landing.css").read_text(encoding="utf-8")
        script = (LANDING / "landing.js").read_text(encoding="utf-8")
        self.assertNotIn("action-primary", index)
        self.assertNotIn(":focus-visible", styles)
        self.assertIn("perspective:", styles)
        self.assertIn("transform-style:preserve-3d", styles)
        self.assertIn(".action.is-hovered", styles)
        self.assertIn('addEventListener("pointermove"', script)
        self.assertIn('style.setProperty("--rotate-x"', script)

    def test_home_replaces_command_palette_with_product_information(self):
        index = (LANDING / "index.html").read_text(encoding="utf-8")
        script = (LANDING / "landing.js").read_text(encoding="utf-8")
        shell = (ROOT / "landing_page.py").read_text(encoding="utf-8")
        self.assertNotIn("Ctrl+K", index)
        self.assertNotIn("command-button", index)
        self.assertNotIn("togglePalette", script)
        self.assertNotIn('QKeySequence("Ctrl+K")', shell)
        self.assertNotIn("BÀN BẮT ĐẦU VIỆC", index)
        self.assertNotIn("Hoàn toàn ngoại tuyến", index)
        self.assertIn("offline-icon", index)
        self.assertNotIn("agency-mark", index)
        self.assertIn("id=\"info-button\"", index)
        self.assertIn("Trần Hậu Mạnh", index)
        self.assertIn("Chandra", index)
        self.assertIn("Qwen3", index)
        self.assertIn("openAbout", script)
        self.assertIn('<dialog class="about"', index)
        self.assertIn(".about::backdrop", (LANDING / "landing.css").read_text(encoding="utf-8"))
        self.assertIn("overlay.showModal()", script)
        self.assertIn("overlay.close()", script)


class LandingFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_native_fallback_keeps_startup_actions_and_progress(self):
        window = LandingWindow(lambda: {}, force_native=True)
        received = []
        window.action_requested.connect(lambda action, payload: received.append((action, payload)))
        self.assertFalse(window.uses_webengine)
        logo = window._fallback.findChild(QLabel, "brandLogo")
        self.assertIsNotNone(logo)
        self.assertFalse(logo.pixmap().isNull())
        window._fallback.findChildren(QPushButton)[0].click()
        window.set_progress(65, "Đang chuẩn bị")
        self.assertEqual([("open_file", {})], received)
        self.assertIn("65%", window._fallback._status.text())
        window.close()


if __name__ == "__main__":
    unittest.main()


class BridgeSignalCoverageTests(unittest.TestCase):
    """A signal the page never listens to is a promise the backend cannot keep.

    startupReady was emitted on every successful start and connected nowhere,
    so the progress overlay was only ever hidden on failure. The workspace fades
    in over the landing window and closes it, which hid the defect — until a
    slow machine, or any future caller that keeps the landing alive, leaves the
    overlay covering the page.
    """

    def test_every_bridge_signal_is_connected_by_the_page(self):
        import landing_page

        script = (LANDING / "landing.js").read_text(encoding="utf-8")
        signals = landing_page.LandingBridge.PAGE_SIGNALS
        self.assertTrue(signals, "no page-facing signals declared")

        unconnected = [
            name for name in signals
            if "{}.connect".format(name) not in script
            and "{}?.connect".format(name) not in script
        ]
        self.assertEqual([], unconnected)

    def test_page_signal_list_covers_every_outbound_signal(self):
        """Otherwise a new signal could be added and simply left off the list,
        which is the same silence this guard exists to prevent."""
        import landing_page

        declared = set(landing_page.LandingBridge.PAGE_SIGNALS)
        defined = {
            name for name, value in vars(landing_page.LandingBridge).items()
            if type(value).__name__ == "pyqtSignal"
        }
        # action_requested travels the other way: the page raises it, Python
        # consumes it, so the page has nothing to connect.
        self.assertEqual(defined - {"action_requested"}, declared)

    def test_the_colour_tokens_and_the_landing_agree_on_the_accent(self):
        """theme.qss and landing.css hold their own literals, so a palette
        change has to reach both or the two screens drift apart."""
        from theme_tokens import COLORS

        styles = (LANDING / "landing.css").read_text(encoding="utf-8")
        self.assertIn(
            "--gold:{}".format(COLORS["accent_gold"].lower()), styles.lower()
        )


class LandingDarkContrastTests(unittest.TestCase):
    """The landing page writes its brand colour as text, and in dark mode the
    olive fill was reused for that: .brand and the about headings rendered at
    2.39 against the dark surface. No single olive can serve both roles, so the
    dark theme needs a lighter one for text — the same split theme_tokens makes
    between primary and primary_muted.
    """

    @staticmethod
    def _contrast(a, b):
        def lum(value):
            ch = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            ch = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in ch]
            return .2126 * ch[0] + .7152 * ch[1] + .0722 * ch[2]
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + .05) / (lo + .05)

    def _vars(self, block):
        styles = (LANDING / "landing.css").read_text(encoding="utf-8")
        section = styles.split(block, 1)[1].split("}", 1)[0]
        return dict(re.findall(r"--([a-z-]+):(#[0-9a-f]{6})", section))

    def test_brand_text_is_readable_in_both_themes(self):
        for block, floor in ((":root{", 4.5), ('[data-theme="dark"]{', 4.5)):
            variables = self._vars(block)
            ink = variables.get("olive-ink")
            self.assertIsNotNone(ink, "missing --olive-ink in {}".format(block))
            for surface in ("paper", "surface", "soft"):
                if surface not in variables:
                    continue
                ratio = self._contrast(ink, variables[surface])
                self.assertGreaterEqual(
                    ratio, floor,
                    "--olive-ink on --{} in {}: {:.2f}".format(surface, block, ratio),
                )

    def test_the_fill_role_keeps_its_own_variable(self):
        """Splitting the roles must not quietly restyle the hover fill."""
        styles = (LANDING / "landing.css").read_text(encoding="utf-8")
        self.assertIn("background:var(--olive)", styles)
