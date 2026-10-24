"""Screenshots of the secondary surfaces in both themes, with inert data.

    runtime\\python.exe -X utf8 devtools\\dialog_snapshots.py OUT_DIR

Renders offscreen, without a database, models or servers: the OCR progress
window (running and done), the PDF preview (a page drawn here, and a missing
file), the keyword search with fixed results, the About dialog, and the
floating surfaces over a stand-in workspace (the batch progress expanded,
collapsed and done; the four kinds of toast; the startup overlay). Settings
go to a scratch INI file. Nothing here reads a real document, so these
pictures may be shared; still, keep screenshots out of the repository.
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

_SCRATCH = tempfile.mkdtemp(prefix="dialog-snap-")


class _ScratchSettings(QtCore.QSettings):
    def __init__(self, *_args, **_kwargs):
        super().__init__(os.path.join(_SCRATCH, "settings.ini"), QtCore.QSettings.IniFormat)


QtCore.QSettings = _ScratchSettings  # before any app module imports it

from PyQt5.QtCore import QRect, Qt, QTimer  # noqa: E402
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QFrame, QHBoxLayout, QWidget,
)

app = QApplication.instance() or QApplication(sys.argv)

from font_loader import load_application_fonts  # noqa: E402

load_application_fonts(app)
_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
for _name in ("times.ttf", "timesbd.ttf", "timesi.ttf"):
    if (_fonts / _name).is_file():
        QFontDatabase.addApplicationFont(str(_fonts / _name))

import main_window_1  # noqa: E402
import theme_tokens  # noqa: E402
from theme_manager_v2 import ThemeManager  # noqa: E402


def settle(ms=400):
    end = QtCore.QTime.currentTime().addMSecs(ms)
    while QtCore.QTime.currentTime() < end:
        app.processEvents()


def save(widget, out_dir, name, theme):
    widget.grab().save(str(out_dir / "{}_{}.png".format(name, theme)))


# ── Inert data ──────────────────────────────────────────────────────
def sample_page(path):
    """A plain administrative page (A4, 600 px wide) in Times New Roman, so
    the preview shows the canvas around it."""
    image = QImage(827, 1169, QImage.Format_RGB32)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setPen(QColor(20, 20, 20))

    def line(y, text, size=13, bold=False, align=Qt.AlignLeft, left=90, width=650):
        font = QFont("Times New Roman", size)
        font.setBold(bold)
        painter.setFont(font)
        painter.drawText(QRect(left, y, width, 30), align | Qt.AlignVCenter, text)

    line(80, "ỦY BAN NHÂN DÂN", 12, True, Qt.AlignHCenter, 60, 300)
    line(102, "XÃ AN BÌNH", 12, True, Qt.AlignHCenter, 60, 300)
    line(80, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", 12, True, Qt.AlignHCenter, 360, 420)
    line(102, "Độc lập - Tự do - Hạnh phúc", 12, True, Qt.AlignHCenter, 360, 420)
    line(150, "Số: 12/KH-UBND", 13, False, Qt.AlignHCenter, 60, 300)
    line(150, "An Bình, ngày 05 tháng 3 năm 2026", 13, False, Qt.AlignHCenter, 360, 420)
    line(230, "KẾ HOẠCH", 15, True, Qt.AlignHCenter)
    line(258, "Tổ chức hội nghị tổng kết công tác năm 2025", 14, True, Qt.AlignHCenter)
    body = (
        "Căn cứ Luật Tổ chức chính quyền địa phương năm 2015;",
        "Thực hiện chương trình công tác năm 2026 của Ủy ban nhân dân xã,",
        "Ủy ban nhân dân xã An Bình xây dựng kế hoạch như sau:",
        "I. MỤC ĐÍCH, YÊU CẦU",
        "1. Đánh giá kết quả thực hiện nhiệm vụ năm 2025.",
        "2. Đề ra phương hướng, nhiệm vụ trọng tâm năm 2026.",
        "II. THỜI GIAN, ĐỊA ĐIỂM",
        "Thời gian: 08 giờ 00, ngày 20 tháng 3 năm 2026.",
        "Địa điểm: Hội trường Ủy ban nhân dân xã.",
    )
    for index, text in enumerate(body):
        line(330 + index * 34, text, 13, text.startswith(("I.", "II.")))
    painter.end()
    image.scaledToWidth(600, Qt.SmoothTransformation).save(str(path))
    return path


class SearchDatabase:
    """Answers the keyword search with three fixed documents."""

    ROWS = (
        (7, r"C:\Kho\12-KH-UBND.pdf", "12-KH-UBND.pdf", "12/KH-UBND", 14,
         {"hội nghị": 9, "tổng kết": 5}),
        (3, r"C:\Kho\245-QD-UBND.pdf", "245-QD-UBND.pdf", "245/QĐ-UBND", 6,
         {"hội nghị": 4, "tổng kết": 2}),
        (11, r"C:\Kho\scan-03.pdf", "scan-03.pdf", "", 1, {"hội nghị": 1, "tổng kết": 0}),
    )

    def search_documents(self, _query, _scope):
        results = []
        for doc_id, path, name, symbol, total, per_keyword in self.ROWS:
            doc = (doc_id, path, name, "2026-03-05", 3, 1, "UBND", "XÃ AN BÌNH",
                   symbol, "Kế hoạch", "Không", "OCR", "2026-03-05", "chandra")
            stats = {
                "total_frequency": total,
                "keyword_frequencies": per_keyword,
                "snippets": [{"keyword": "hội nghị", "line": 8,
                              "context": "tổ chức hội nghị tổng kết công tác năm 2025"}],
            }
            results.append((doc, stats))
        return results


# ── Stand-in workspace for the floating surfaces ─────────────────────
def workspace(theme, width=920, height=560):
    """A canvas with a page on the left and a surface panel on the right:
    the two backgrounds a floating surface can land on."""
    tokens = theme_tokens.colors_for(theme)
    host = QWidget()
    host.setObjectName("snapHost")
    host.setAttribute(Qt.WA_StyledBackground, True)
    host.setStyleSheet("QWidget#snapHost {{ background: {}; }}".format(tokens["bg"]))
    host.resize(width, height)
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    canvas = QFrame()
    canvas.setObjectName("snapCanvas")
    canvas.setStyleSheet("QFrame#snapCanvas {{ background: {}; }}".format(tokens["bg"]))
    page = QFrame(canvas)
    page.setObjectName("snapPage")
    page.setStyleSheet("QFrame#snapPage { background: white; }")
    page.setGeometry(60, 30, 380, 540)
    panel = QFrame()
    panel.setObjectName("snapPanel")
    panel.setStyleSheet("QFrame#snapPanel {{ background: {}; border-left: 1px solid {}; }}"
                        .format(tokens["surface"], tokens["border"]))
    row.addWidget(canvas, 1)
    row.addWidget(panel, 1)
    host.show()
    settle(100)
    return host


# ── Surfaces ────────────────────────────────────────────────────────
def grab_progress(out_dir, theme):
    dialog = main_window_1.ProgressDialog(100, "Đang xử lý tệp")
    dialog.show()
    dialog.update_progress(40, 100, "Trang 2/4: 512 tokens (2 workers)...")
    settle(900)
    save(dialog, out_dir, "progress_running", theme)
    dialog.mark_completed()
    settle(300)
    save(dialog, out_dir, "progress_done", theme)
    dialog.close()


def grab_preview(out_dir, theme, page_path):
    for name, path in (("preview", page_path), ("preview_missing", Path(_SCRATCH) / "missing.pdf")):
        dialog = main_window_1.PreviewDialog(str(path))
        dialog.resize(760, 860)
        dialog.show()
        settle(600)
        save(dialog, out_dir, name, theme)
        dialog.close()


def grab_search(out_dir, theme):
    dialog = main_window_1.SearchDialog(SearchDatabase())
    dialog.show()
    dialog.search_input.setText("hội nghị, tổng kết")
    dialog.do_search()
    settle(400)
    save(dialog, out_dir, "search", theme)
    dialog.close()


def grab_about(out_dir, theme):
    about = getattr(main_window_1, "AboutDialog", None)
    if about is not None:
        dialog = about()
        dialog.show()
        settle(300)
        save(dialog, out_dir, "about", theme)
        dialog.close()
        return
    # Before the redesign the About dialog was built inside show_about.
    host = QWidget()

    def shoot():
        dialog = app.activeModalWidget()
        if isinstance(dialog, QDialog):
            save(dialog, out_dir, "about", theme)
            dialog.reject()
    QTimer.singleShot(400, shoot)
    main_window_1.MainWindow.show_about(host)


def grab_floating(out_dir, theme):
    from floating_progress import FloatingProgress
    from toast_widget import toast_error, toast_info, toast_success, toast_warning

    host = workspace(theme)
    progress = FloatingProgress(parent=host, title="Đang xử lý 12 văn bản")
    progress.set_progress(0.42, "Tệp 5/12: 245-QD-UBND.pdf — Chandra OCR trang 2/3...")
    progress.show_above()
    toast_success(host, "Đã lưu thay đổi", 60000)
    toast_info(host, "Đã xây lại chỉ mục AI cho 1.186 văn bản", 60000)
    toast_warning(host, "245-QD-UBND.pdf: trang 3 chưa nhận dạng được. "
                        "Kiểm tra lại và chạy OCR lại cho trang này.", 60000)
    toast_error(host, "Không thể kết nối máy chủ OCR", 60000)
    settle(700)
    save(host, out_dir, "floating", theme)

    progress._toggle_collapse()
    settle(300)
    save(host, out_dir, "floating_collapsed", theme)
    progress._toggle_collapse()
    progress.mark_completed("Hoàn tất 12/12 văn bản")
    settle(300)
    save(host, out_dir, "floating_done", theme)
    host.close()


def grab_loading(out_dir, theme):
    from loading_overlay import LoadingOverlay

    host = workspace(theme)
    overlay = LoadingOverlay(parent=host)
    overlay.center_in(host)
    overlay.set_progress(65, "Đang khởi tạo OCR và kho tri thức...")
    overlay.fade_in(duration=50)
    settle(600)
    save(host, out_dir, "loading", theme)
    host.close()


def main():
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    page = sample_page(Path(_SCRATCH) / "sample_page.png")
    manager = ThemeManager.instance()
    for theme in ("light", "dark"):
        manager.apply(app, theme)
        settle(200)
        for step in (grab_progress, grab_search, grab_about, grab_floating, grab_loading):
            step(out_dir, theme)
        grab_preview(out_dir, theme, page)
    print("saved to", out_dir)


if __name__ == "__main__":
    main()
