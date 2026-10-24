"""Screenshots of the workspace in both themes, without models or servers.

    runtime\\python.exe -X utf8 devtools\\ui_snapshots.py DB OUT_DIR [DOC_ID]

Builds the real MainWindow offscreen with the OCR engine, embedding model,
reranker and chat server replaced by inert stand-ins, opens DOC_ID from a
copy of a database, shows the chat dock with one fixed exchange, and saves
for each theme: the window, the editor panel (split, full text expanded,
metadata expanded) and the date popover drawn under its field. Settings
go to a scratch INI file, never to the registry the installed app reads.
Screenshots of real documents stay on this machine.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import PyQt5.QtCore as QtCore  # noqa: E402

_SCRATCH = tempfile.mkdtemp(prefix="ui-snap-")


class _ScratchSettings(QtCore.QSettings):
    def __init__(self, *_args, **_kwargs):
        super().__init__(os.path.join(_SCRATCH, "settings.ini"), QtCore.QSettings.IniFormat)


QtCore.QSettings = _ScratchSettings  # before any app module imports it

from PyQt5.QtCore import QObject, QTimer, pyqtSignal  # noqa: E402
from PyQt5.QtWidgets import QApplication, QDialog  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from font_loader import load_application_fonts  # noqa: E402

load_application_fonts(app)


def _load_document_fonts():
    """The offscreen platform sees no system fonts; Times New Roman (document
    text in the editor) comes from Windows, so register it for the render."""
    from PyQt5.QtGui import QFontDatabase
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"):
        if (fonts / name).is_file():
            QFontDatabase.addApplicationFont(str(fonts / name))


_load_document_fonts()

import main_window_1  # noqa: E402
import startup_preloader  # noqa: E402
import status_indicators  # noqa: E402
from chat_panel import ChatPanel  # noqa: E402
from document_repository import DocumentDatabase  # noqa: E402
from rag_agent import Citation  # noqa: E402
from theme_manager_v2 import ThemeManager  # noqa: E402


class _Inert:
    """Stands in for a model: loads nothing, answers nothing."""

    is_running = False
    _chandra_server = None
    url = "http://127.0.0.1:9"
    backend = "cpu"

    def __init__(self, *_args, **_kwargs):
        pass

    def __getattr__(self, _name):
        return lambda *_a, **_k: None

    def is_available(self):
        return False

    def load(self):
        return False


class _Preloader(QObject):
    ready = pyqtSignal(float)
    error = pyqtSignal(str)

    def __init__(self, *_args):
        super().__init__()

    def start(self, *_args):
        pass

    def isRunning(self):
        return False

    def wait(self, *_args):
        return True


class _History:
    def list_sessions(self, limit=20):
        return []

    def load_session(self, session_id):
        return session_id, []

    def load_last_session(self):
        return None, []

    def new_session(self, _title):
        return 1

    def __getattr__(self, _name):
        return lambda *_a, **_k: None


ANSWER = (
    "Kế hoạch 336/KH-KTNS ngày 02/8/2017 của Ban Kinh tế - Ngân sách HĐND tỉnh Hưng Yên "
    "khảo sát việc trích lập, quản lý và sử dụng Quỹ phát triển đất, Quỹ bảo vệ môi trường "
    "và Quỹ bảo trì đường bộ từ năm 2015 đến nay [1].\n\n"
    "- Mục đích: đánh giá tình hình trích lập, quản lý, sử dụng các quỹ có nguồn vốn từ ngân "
    "sách nhà nước [2].\n- Thời gian khảo sát: các ngày 29, 30 và 31/8/2017 [3].\n\n"
    "Nguồn: [1] [2] [3]\n\n⚠️ Thông tin tra cứu tự động, không thay thế tư vấn pháp lý."
)


def build(db_path):
    # The status bar probe would query the OCR server port of an app that may
    # be running on this machine; report a fixed, plausible state instead.
    status_indicators.collect_status = lambda *a, **k: {"online": True, "vram": (4400, 8188)}
    main_window_1.DocumentDatabase = lambda *a, **k: DocumentDatabase(db_path)
    main_window_1.DocumentOCR = _Inert
    main_window_1.EmbeddingModel = _Inert
    main_window_1.VectorStore = _Inert
    main_window_1.RerankerModel = _Inert
    startup_preloader.ModelPreloader = _Preloader
    main_window_1.ChatPanel = lambda parent, path, emb, rer, **kw: ChatPanel(
        parent, path, emb, rer, server_manager=_Inert(), history_store=_History(),
        executor_factory=lambda scope: None, client_factory=lambda url: None, **kw)
    window = main_window_1.MainWindow()
    window.check_file_paths = lambda *a, **k: None
    return window


def settle(ms=400):
    end = QtCore.QTime.currentTime().addMSecs(ms)
    while QtCore.QTime.currentTime() < end:
        app.processEvents()


def grab_chrome(window, out_dir, theme):
    """Close-ups of the workspace chrome: the toolbar, the library panel, the
    toolbar menus open, and the tooltip of the first library row. Every
    part is optional, so the tool keeps working while the window changes."""
    from PyQt5.QtCore import QPoint
    from PyQt5.QtWidgets import QToolTip

    def save(widget, name):
        if widget is not None:
            widget.grab().save(str(out_dir / "{}_{}.png".format(name, theme)))

    save(getattr(window, "_main_toolbar", None), "toolbar")
    splitter = getattr(window, "_main_splitter", None)
    if splitter is not None:
        save(splitter.widget(0), "library")
    for attr, name in (("_add_button", "menu_add"), ("_overflow_button", "menu_more"),
                       ("_doc_sort_btn", "menu_sort")):
        button = getattr(window, attr, None)
        menu = button.menu() if button is not None else None
        if menu is None:
            continue
        menu.popup(button.mapToGlobal(button.rect().bottomLeft()))
        settle(300)
        save(menu, name)
        menu.hide()
    doc_list = getattr(window, "doc_list", None)
    if doc_list is not None and doc_list.count():
        QToolTip.showText(QPoint(100, 100), doc_list.item(0).toolTip(), doc_list)
        settle(300)
        tips = [w for w in app.topLevelWidgets()
                if w.metaObject().className() == "QTipLabel" and w.isVisible()]
        if tips:
            save(tips[0], "tooltip")
        QToolTip.hideText()


def grab_modal(path, opener):
    def shoot():
        dialog = app.activeModalWidget()
        if isinstance(dialog, QDialog):
            dialog.grab().save(str(path))
            dialog.reject()
    QTimer.singleShot(500, shoot)
    opener()


def grab_popup(path, editor, anchor, opener):
    """A popover (not a modal dialog): the editor with the popover drawn
    under `anchor`, right edges aligned, as it opens on a real screen (the
    offscreen screen is smaller than the window, so its own position is
    clamped somewhere else)."""
    from PyQt5.QtCore import QPoint
    from PyQt5.QtGui import QPainter
    opener()
    settle(400)
    popup = app.activePopupWidget()
    if popup is None:
        return
    shot = editor.grab()
    painter = QPainter(shot)
    corner = anchor.mapTo(editor, QPoint(anchor.width() - popup.width(), anchor.height() + 4))
    painter.drawPixmap(corner, popup.grab())
    painter.end()
    shot.save(str(path))
    popup.close()
    settle(200)


def grab_editor_states(out_dir, theme, editor):
    """The editor panel split, with the full text expanded, and with the
    metadata expanded."""
    editor.grab().save(str(out_dir / "editorpanel_{}.png".format(theme)))
    for section in (editor.TEXT, editor.META):
        editor.set_expanded(section)
        settle(400)
        editor.grab().save(str(out_dir / "editor_{}_expanded_{}.png".format(section, theme)))
    editor.set_expanded(None)
    settle(300)


def main():
    db_path, out_dir = sys.argv[1], Path(sys.argv[2])
    doc_id = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    out_dir.mkdir(parents=True, exist_ok=True)
    window = build(db_path)
    window.resize(1680, 1000)
    window.show()
    settle(800)
    window.show_document(doc_id)
    window.doc_list.find_and_select(doc_id)  # as after a click in the library
    settle(1500)
    panel = window.chat_panel
    panel.set_scope_doc_ids([doc_id])
    panel._messages = [
        ("user", "Kế hoạch khảo sát số 336/KH-KTNS nói về gì?", []),
        ("assistant", ANSWER, [Citation(1, doc_id, None, "336/KH-KTNS", 1),
                               Citation(2, doc_id, None, "336/KH-KTNS", 1),
                               Citation(3, doc_id, None, "336/KH-KTNS", 2)]),
    ]
    window.act_chat.setChecked(True)
    window._toggle_chat_panel(True)
    panel._render_messages()
    settle(800)
    manager = ThemeManager.instance()
    for theme in ("light", "dark"):
        manager.apply(app, theme)
        settle(1200)
        window.grab().save(str(out_dir / "workspace_{}.png".format(theme)))
        panel.grab().save(str(out_dir / "chat_{}.png".format(theme)))
        window._toggle_chat_panel(False)
        settle(600)
        window.grab().save(str(out_dir / "editor_{}.png".format(theme)))
        grab_chrome(window, out_dir, theme)
        editor = window.ocr_editor
        grab_editor_states(out_dir, theme, editor)
        grab_popup(out_dir / "datepicker_{}.png".format(theme), editor,
                   editor.fields["ngay_bh"], editor.show_date_picker)
        window._toggle_chat_panel(True)
        settle(600)
    print("saved to", out_dir)
    window.close()


if __name__ == "__main__":
    main()
