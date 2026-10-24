"""README screenshots of the real app, on the fictional demo corpus.

    runtime\\python.exe -X utf8 devtools\\readme_shots.py DEMO_DB OUT_DIR [part ...]

DEMO_DB is the database devtools\\demo_corpus\\build_demo.py builds from
invented documents (never a real one); OUT_DIR is docs\\media\\screens.
Parts (all by default, each in its own process):

  workspace  the real MainWindow offscreen with inert models (ui_snapshots):
             the workspace with the plan 125/KH-UBND open (PDF at 75 %), the
             same with the chat dock on a fixed two-turn exchange (a saved
             conversation, not a model), the dock alone, and the editor with
             its calendar open
  dialogs    the AI search (full-text passages, no model) and the analytics
  home       the home page through the real QtWebEngine page (needs a desktop
             session: the window opens off screen for a moment)

Every part runs on a scratch copy of DEMO_DB, in both themes, at 1600x1000
(the home page at 1600x720), and writes optimised PNGs: home_, workspace_, workspace_chat_, chat_,
editor_calendar_, aisearch_, analytics_ + light/dark.
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVTOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DEVTOOLS))

PARTS = ("workspace", "dialogs", "home")
SIZE = (1600, 1000)
# The home page centres its content vertically; at the full height the
# register floats in empty space, so it is shot in a shorter window.
HOME_SIZE = (1600, 720)
THEMES = ("light", "dark")
OPEN_SO = "125/KH-UBND"
COMPARE_SO = "47/TTr-STTTT"
SEARCH_QUERY = "số hóa tài liệu lưu trữ"
MAX_PNG = 600 * 1024

# The exchange shown in the chat dock. Fixed text, as a model would answer
# from the demo corpus; every figure is in the cited page of the PDF.
QUESTION_1 = "Mục tiêu năm 2026 và kinh phí của kế hoạch 125?"
ANSWER_1 = (
    "Kế hoạch số 125/KH-UBND ngày 14/3/2026 của UBND tỉnh Mẫu đặt mục tiêu năm 2026 số hóa "
    "**60%** tài liệu có thời hạn bảo quản vĩnh viễn của các cơ quan cấp tỉnh, hoàn thành "
    "100% trước ngày 31/12/2027 [1].\n\n"
    "- **Kinh phí:** 18,6 tỷ đồng từ ngân sách tỉnh; năm 2026 là 11,2 tỷ đồng, năm 2027 là "
    "7,4 tỷ đồng [2].\n"
    "- **Đầu mối:** Sở Thông tin và Truyền thông vận hành nền tảng, báo cáo kết quả hằng "
    "quý [2]."
)
QUESTION_2 = "So sánh với tờ trình 47"
ANSWER_2 = (
    "So với đề xuất tại Tờ trình số 47/TTr-STTTT ngày 02/02/2026 [1], Kế hoạch 125/KH-UBND "
    "rút ngắn thời hạn và nâng mục tiêu năm đầu [2]:\n\n"
    "| | Tờ trình 47 | Kế hoạch 125 |\n"
    "|---|---|---|\n"
    "| Hoàn thành | 31/12/2028 | 31/12/2027 |\n"
    "| Mục tiêu 2026 | 40% | 60% |\n"
    "| Kinh phí | 21,4 tỷ đồng | 18,6 tỷ đồng |\n\n"
    "Kinh phí thấp hơn đề xuất vì dùng hạ tầng Trung tâm dữ liệu tỉnh hiện có, không thuê "
    "trung tâm dữ liệu dự phòng [1][3]."
)
TIMES = (("5,2 giây", "Trả lời trong 5,2 giây\nTốc độ: 61 token/giây"),
         ("6,4 giây", "Trả lời trong 6,4 giây\nTốc độ: 58 token/giây"))
# (days ago, local time) of the home page's eight most recent documents.
REGISTER_TIMES = ((0, "10:14"), (0, "08:52"), (1, "16:40"), (3, "15:05"), (5, "09:35"),
                  (7, "14:10"), (9, "11:20"), (12, "16:25"))


# ─── shared ──────────────────────────────────────────────────────────
def scratch_copy(db_path):
    folder = Path(tempfile.mkdtemp(prefix="readme-shots-"))
    target = folder / "demo.db"
    shutil.copyfile(db_path, target)
    return target


def doc_id_of(db_path, so):
    with closing(sqlite3.connect(str(db_path))) as conn:
        row = conn.execute(
            "SELECT document_id FROM document_versions WHERE so_ki_hieu = ? "
            "ORDER BY version_number DESC LIMIT 1", (so,)).fetchone()
    if row is None:
        raise SystemExit("{} is not in {}: build the demo database first".format(so, db_path))
    return int(row[0])


def chunk_of(db_path, doc_id, page):
    with closing(sqlite3.connect(str(db_path))) as conn:
        row = conn.execute(
            "SELECT id FROM document_chunks WHERE document_id = ? AND page_number = ? "
            "ORDER BY id LIMIT 1", (doc_id, page)).fetchone()
    return int(row[0]) if row else None


def save_png(image, path):
    """Save a QImage/QPixmap, then shrink it with Pillow: lossless first,
    a 256-colour palette only when that is still too big."""
    from PIL import Image

    path = Path(path)
    image.save(str(path))
    with Image.open(path) as source:
        rgb = source.convert("RGB")
    rgb.save(path, optimize=True)
    if path.stat().st_size > MAX_PNG:
        rgb.quantize(colors=256, method=Image.MEDIANCUT, dither=Image.NONE).save(
            path, optimize=True)
    print("{:<28} {:>4}x{:<4} {:>4} KB".format(path.name, rgb.width, rgb.height,
                                                path.stat().st_size // 1024))


# ─── workspace: main window, chat dock, editor ───────────────────────
def run_workspace(db_path, out):
    import ui_snapshots as snap  # offscreen, scratch settings, fonts, inert models
    from chat_panel import _Message
    from rag_agent import Citation
    from theme_manager_v2 import ThemeManager

    app = snap.app
    open_id = doc_id_of(db_path, OPEN_SO)
    compare_id = doc_id_of(db_path, COMPARE_SO)

    def cite(n, doc_id, so, page):
        return Citation(n, doc_id, page, so, chunk_of(db_path, doc_id, page))

    turn_1 = [cite(1, open_id, OPEN_SO, 1), cite(2, open_id, OPEN_SO, 2)]
    turn_2 = [cite(1, compare_id, COMPARE_SO, 1), cite(2, open_id, OPEN_SO, 1),
              cite(3, open_id, OPEN_SO, 2)]
    messages = [
        _Message("user", QUESTION_1, [], {"scope": "open", "open_doc": open_id}),
        _Message("assistant", ANSWER_1, turn_1, {}),
        _Message("user", QUESTION_2, [], {"scope": "all", "open_doc": open_id}),
        _Message("assistant", ANSWER_2, turn_2, {}),
    ]

    class History(snap._History):
        """One saved conversation: the exchange above."""

        def list_sessions(self, limit=20):
            return [{"id": 7, "title": "Kế hoạch số hóa 125/KH-UBND"}]

        def load_last_session(self):
            return 7, [{"role": m[0], "content": m[1], "citations": m[2], "meta": m.meta}
                       for m in messages]

        def load_session(self, session_id):
            return self.load_last_session()

    snap._History = History
    window = snap.build(str(db_path))
    # The status bar as on the measured machine (24 GB GPU, OCR server up),
    # from the first frame on; nothing is probed.
    import status_indicators
    status = {"online": True, "vram": (7066, 24463)}
    status_indicators.collect_status = lambda *a, **k: dict(status)
    window._status_coord._apply_result(dict(status))
    window.resize(*SIZE)
    window.show()
    snap.settle(800)
    window.show_document(open_id)
    window.doc_list.find_and_select(open_id)  # as after a click in the library
    snap.settle(1800)
    # 75 %: the page fills the viewer's width at 96 dpi (100 % is 150 dpi).
    window.pdf_viewer.zoom_out()
    snap.settle(300)
    panel = window.chat_panel
    panel.set_scope_doc_ids([open_id])
    splitter = window._main_splitter
    initial_sizes = splitter.sizes()

    def keep_library_width(width=280):
        """With the dock open the splitter shrinks every pane alike and
        the library's dates are cut; give it back its own width."""
        sizes = splitter.sizes()
        if sizes and sizes[0] < width:
            sizes[1] -= width - sizes[0]
            sizes[0] = width
            splitter.setSizes(sizes)

    def grab_calendar(path, editor):
        """The editor with its calendar drawn under the date field, right
        edges aligned, as it opens on a real screen."""
        from PyQt5.QtCore import QPoint
        from PyQt5.QtGui import QPainter
        anchor = editor.fields["ngay_bh"]
        popup = editor.show_date_picker()
        snap.settle(400)
        shot = editor.grab()
        painter = QPainter(shot)
        corner = anchor.mapTo(editor, QPoint(anchor.width() - popup.width(), anchor.height() + 4))
        painter.drawPixmap(corner, popup.grab())
        painter.end()
        popup.close()
        snap.settle(200)
        save_png(shot, path)

    def present_times():
        for index, view in panel._message_views:
            role, content, citations = panel._messages[index]
            if role == "assistant":
                panel._present_answer(view, index, content, citations, TIMES[index // 2])
        panel.steps_label.setText("")
        panel.transcript.scroll_to_bottom()

    manager = ThemeManager.instance()
    for theme in THEMES:
        manager.apply(app, theme)
        snap.settle(1200)
        # The workspace: library | PDF | editor (full text and fields), with
        # the panes as they first opened (the dock of the previous theme
        # collapsed the editor and squeezed the library).
        window._toggle_chat_panel(False)
        snap.settle(400)
        window._details_user_choice = None
        window._layout_bucket = None
        window._set_details_visible(True)
        splitter.setSizes(initial_sizes)
        snap.settle(700)
        save_png(window.grab(), out / "workspace_{}.png".format(theme))
        grab_calendar(out / "editor_calendar_{}.png".format(theme), window.ocr_editor)
        # The same with the chat dock open on the saved conversation.
        window.act_chat.setChecked(True)
        window._toggle_chat_panel(True)
        snap.settle(600)
        keep_library_width()
        panel._render_messages()
        present_times()
        snap.settle(900)
        # The narrower viewer cuts the page's right edge: scroll it fully
        # right, so the text block sits whole between the page margins.
        bar = window.pdf_viewer.scroll_area.horizontalScrollBar()
        bar.setValue(bar.maximum())
        snap.settle(200)
        overflow = panel.transcript.verticalScrollBar().maximum()
        if overflow:  # the first question would be cut at the top
            print("note: the exchange is {} px taller than the dock".format(overflow))
        save_png(window.grab(), out / "workspace_chat_{}.png".format(theme))
        save_png(panel.grab(), out / "chat_{}.png".format(theme))  # the dock alone
    window.close()


# ─── dialogs: AI search, analytics ───────────────────────────────────
def run_dialogs(db_path, out):
    import window_snapshots as wsnap  # offscreen, scratch settings, fonts
    from ai_search_dialog import AISearchDialog
    from analytics_dialog import AnalyticsDialog
    from document_repository import DocumentDatabase
    from theme_manager_v2 import ThemeManager

    app = wsnap.app
    manager = ThemeManager.instance()
    manager.apply(app, "light")
    db = DocumentDatabase(str(db_path))
    analytics = AnalyticsDialog(db=db)
    analytics.resize(1360, 820)
    search = AISearchDialog(db, wsnap._Embedding(), wsnap._VectorStore(str(db_path)))
    search.resize(960, 680)
    search.search_input.setText(SEARCH_QUERY)
    search.on_search_complete(wsnap.fake_results(str(db_path), SEARCH_QUERY))
    for window in (analytics, search):
        window.show()
    wsnap.settle(800)
    for theme in THEMES:
        manager.apply(app, theme)
        wsnap.settle(700)
        analytics.result_tabs.setCurrentIndex(0)
        wsnap.settle(300)
        save_png(analytics.grab(), out / "analytics_{}.png".format(theme))
        analytics.result_tabs.setCurrentIndex(1)
        wsnap.settle(700)
        save_png(analytics.grab(), out / "analytics_charts_{}.png".format(theme))
        analytics.result_tabs.setCurrentIndex(0)
        save_png(search.grab(), out / "aisearch_{}.png".format(theme))
    for window in (analytics, search):
        window.close()


# ─── home page (QtWebEngine: a desktop session) ──────────────────────
def run_home(db_path, out):
    os.environ["QT_FONT_DPI"] = "96"
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer

    QCoreApplication.setOrganizationName("OCR_VBHC_dev")
    QCoreApplication.setApplicationName("readme-shots")
    from PyQt5.QtWidgets import QApplication

    import landing_page  # imports QtWebEngine before the application exists
    from font_loader import load_application_fonts
    from landing_data import LandingSnapshotService

    app = QApplication.instance() or QApplication(sys.argv[:1])
    load_application_fonts(app)

    def wait(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec_()

    for theme in THEMES:
        snapshot = LandingSnapshotService(db_path, base_dir=ROOT,
                                          theme_getter=lambda t=theme: t).get_snapshot()
        snapshot["models"] = {"ocr": "available", "chat": "available"}
        # The register's "Cập nhật" column in office hours whenever the shot
        # is taken (the database keeps the build's own clock): newest first.
        today = datetime.now().replace(second=0, microsecond=0)
        if today.hour < 11:  # never a time still to come
            today -= timedelta(days=1)
        for item, (days, clock) in zip(snapshot["recentDocuments"], REGISTER_TIMES):
            hour, minute = (int(x) for x in clock.split(":"))
            moment = (today - timedelta(days=days)).replace(hour=hour, minute=minute)
            item["updatedAt"] = moment.strftime("%Y-%m-%dT%H:%M:%S")
        window = landing_page.LandingWindow(lambda s=snapshot: s)
        window.setMinimumSize(*HOME_SIZE)
        window.resize(*HOME_SIZE)
        window.move(-HOME_SIZE[0] - 400, 0)
        window.show()
        wait(2600)
        # The public README shows the app's own mark, never the agency
        # emblem the installed app carries in its header.
        if window._web_view is not None:
            window._web_view.page().runJavaScript(
                "document.querySelector('img.emblem').src='../../assets/app_icon_256.png'")
            wait(500)
        save_png(window.grab(), out / "home_{}.png".format(theme))
        window.close()
        window.deleteLater()
        wait(200)


RUNNERS = {"workspace": run_workspace, "dialogs": run_dialogs, "home": run_home}


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    source, out = Path(argv[1]).resolve(), Path(argv[2]).resolve()
    parts = argv[3:] or list(PARTS)
    unknown = [part for part in parts if part not in PARTS]
    if unknown:
        print("unknown part(s): {}".format(", ".join(unknown)))
        return 2
    out.mkdir(parents=True, exist_ok=True)
    if len(parts) > 1:  # each part in a process of its own (Qt platform, settings)
        failed = 0
        for part in parts:
            failed |= subprocess.call([sys.executable, "-X", "utf8", __file__, str(source),
                                       str(out), part])
        return failed
    db_path = scratch_copy(source)
    RUNNERS[parts[0]](db_path, out)
    # Leave without tearing the Qt objects down one by one: the main
    # window's shutdown hooks expect the real app and end with an error code.
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
