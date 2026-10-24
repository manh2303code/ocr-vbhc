"""Screenshots of the secondary windows in both themes, without models.

    runtime\\python.exe -X utf8 devtools\\window_snapshots.py DB OUT_DIR

Renders offscreen, against a scratch copy of DB (the dialogs migrate and
index the database they open, so the file given is never touched):
the analytics dialog (table and charts), the AI semantic search with
results taken from the full-text index (no embedding model, no reranker),
one result expanded, the statistics dialog, the index rebuild progress
(its worker never starts) and every tab of the settings dialog. Each
window is built once and the theme is switched under it, so the second
theme also shows whether the window follows a live theme change.
Settings go to a scratch INI file, never to the registry the installed app
reads. Screenshots of real documents stay on this machine.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import PyQt5.QtCore as QtCore  # noqa: E402

_SCRATCH = tempfile.mkdtemp(prefix="window-snap-")


class _ScratchSettings(QtCore.QSettings):
    def __init__(self, *_args, **_kwargs):
        super().__init__(os.path.join(_SCRATCH, "settings.ini"), QtCore.QSettings.IniFormat)


QtCore.QSettings = _ScratchSettings  # before any app module imports it

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from font_loader import load_application_fonts  # noqa: E402

load_application_fonts(app)


def _load_document_fonts():
    """The offscreen platform sees no system fonts; Times New Roman (quoted
    document text) comes from Windows, so register it for the render."""
    from PyQt5.QtGui import QFontDatabase
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"):
        if (fonts / name).is_file():
            QFontDatabase.addApplicationFont(str(fonts / name))


_load_document_fonts()

from document_repository import DocumentDatabase  # noqa: E402
from theme_manager_v2 import ThemeManager  # noqa: E402

QUERY = "quỹ phát triển đất và quỹ bảo trì đường bộ"


class _Embedding:
    """Stands in for the embedding model: never loads, never encodes."""

    def is_available(self):
        return True

    def encode(self, _text):
        return None


class _VectorStore:
    """The parts of VectorStore the search dialog reads."""

    def __init__(self, db_path):
        self.db_path = db_path

    def get_embedding_count(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute(
                "SELECT COUNT(DISTINCT document_id) FROM document_chunks "
                "WHERE embedding IS NOT NULL").fetchone()[0]


def fake_results(db_path, query, limit=6):
    """(doc_id, score, chunk_text) per document, best chunk first, from the
    accent-insensitive full-text index: real passages, no model."""
    from search_store import fts_search_folded
    with closing(sqlite3.connect(db_path)) as conn:
        hits = fts_search_folded(conn, query, top_n=40)
        best = {}
        for chunk_id, doc_id, _score in hits:
            best.setdefault(doc_id, chunk_id)
        rows = []
        for rank, (doc_id, chunk_id) in enumerate(list(best.items())[:limit]):
            text = conn.execute(
                "SELECT chunk_text FROM document_chunks WHERE id = ?", (chunk_id,)).fetchone()[0]
            rows.append((doc_id, round(0.86 - 0.11 * rank, 2), text or ""))
    return rows


def settle(ms=300):
    end = QtCore.QTime.currentTime().addMSecs(ms)
    while QtCore.QTime.currentTime() < end:
        app.processEvents()


def save(widget, out_dir, name, theme):
    widget.grab().save(str(out_dir / "{}_{}.png".format(name, theme)))


def build_windows(db, db_path):
    from ai_search_dialog import AISearchDialog
    from analytics_dialog import AnalyticsDialog
    from reindex_dialog import ReindexDialog
    from settings_dialog import SettingsDialog
    from statistics_dialog import StatisticsDialog

    windows = {}
    analytics = AnalyticsDialog(db=db)
    analytics.resize(1360, 820)
    windows["analytics"] = analytics

    search = AISearchDialog(db, _Embedding(), _VectorStore(db_path))
    search.resize(900, 640)
    search.search_input.setText(QUERY)
    search.on_search_complete(fake_results(db_path, QUERY))
    windows["aisearch"] = search

    statistics = StatisticsDialog(db)
    statistics.resize(820, 620)
    windows["statistics"] = statistics

    reindex = ReindexDialog(db_path, _Embedding())
    reindex._started = True  # the worker would re-embed the copy; show the progress only
    windows["reindex"] = reindex

    settings = SettingsDialog(None)
    windows["settings"] = settings
    for window in windows.values():
        window.show()
    reindex._on_progress(12, 30, "01.01.52.441.03.pdf")
    settle(800)
    return windows


def grab_all(windows, out_dir, theme):
    analytics = windows["analytics"]
    analytics.result_tabs.setCurrentIndex(0)
    settle()
    save(analytics, out_dir, "analytics_table", theme)
    analytics.result_tabs.setCurrentIndex(1)
    settle(600)
    save(analytics, out_dir, "analytics_charts", theme)
    analytics.result_tabs.setCurrentIndex(0)

    search = windows["aisearch"]
    save(search, out_dir, "aisearch", theme)
    expand = getattr(search, "set_result_expanded", None)
    if expand is not None:  # the redesigned list can open one passage in full
        expand(0, True)
        settle()
        save(search, out_dir, "aisearch_expanded", theme)
        expand(0, False)

    save(windows["statistics"], out_dir, "statistics", theme)
    save(windows["reindex"], out_dir, "reindex", theme)

    settings = windows["settings"]
    tabs = settings._tabs
    for index in range(tabs.count()):
        tabs.setCurrentIndex(index)
        settle(150)
        save(settings, out_dir, "settings_{}".format(index + 1), theme)
    tabs.setCurrentIndex(0)


def main():
    source, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = os.path.join(_SCRATCH, "snapshot.db")
    shutil.copyfile(source, db_path)
    manager = ThemeManager.instance()
    manager.apply(app, "light")
    db = DocumentDatabase(db_path)
    windows = build_windows(db, db_path)
    for theme in ("light", "dark"):
        manager.apply(app, theme)
        settle(600)
        grab_all(windows, out_dir, theme)
    for window in windows.values():
        window.close()
    print("saved to", out_dir)


if __name__ == "__main__":
    main()
