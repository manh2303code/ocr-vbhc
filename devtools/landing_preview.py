"""Screenshots of the home page in both themes, with invented data.

    runtime\\python.exe -X utf8 devtools\\landing_preview.py OUT_DIR

Renders frontend/landing through the real LandingWindow — the QtWebEngine
the app ships (Chromium 83) and the real web channel — with a fixed
snapshot instead of the database, then the native fallback. Saves, for the
light and the dark theme: the page at 1440x900 and 1024x720, a keyboard
tooltip, the empty register, the startup progress, the about dialog and
the fallback.

QtWebEngine does not paint on the offscreen platform, so this needs a
desktop session: the window opens off screen for a moment. Its profile
lives under a scratch application name, never the installed app's.
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# 100 % display scaling whatever this machine uses, so the native fallback's
# point sizes and the page's CSS pixels are judged at the same scale.
os.environ["QT_FONT_DPI"] = "96"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

QCoreApplication.setOrganizationName("OCR_VBHC_dev")
QCoreApplication.setApplicationName("landing-preview")

from PyQt5.QtWidgets import QApplication  # noqa: E402

import landing_page  # noqa: E402  (imports QtWebEngine before the app exists)
from font_loader import load_application_fonts  # noqa: E402
from landing_data import landing_commands  # noqa: E402

SIZES = ((1440, 900), (1024, 720))


def _stamp(delta):
    return (datetime.now() - delta).strftime("%Y-%m-%dT%H:%M:%S")


def sample_snapshot(theme, empty=False):
    """Invented documents — never real ones; this output may be shared."""
    recent = [
        (41, "12/KH-UBND", "Kế hoạch tổ chức hội nghị tổng kết công tác năm 2026 và triển khai nhiệm vụ năm 2027",
         "ke-hoach-hoi-nghi-tong-ket.pdf", 4, timedelta(minutes=18)),
        (40, "245/QĐ-CAT", "Quyết định thành lập tổ công tác rà soát, số hóa hồ sơ lưu trữ giai đoạn 2026–2028",
         "qd-245-to-cong-tac.pdf", 2, timedelta(hours=2, minutes=5)),
        (39, "", "", "01.01.52.441.03.pdf", 3, timedelta(hours=5)),
        (38, "88/BC-PV01", "Báo cáo kết quả thực hiện công tác cải cách hành chính quý III năm 2026",
         "bao-cao-cchc-quy-3.pdf", 11, timedelta(days=1, hours=1)),
        (37, "1032/CV-VP", "V/v đăng ký nhu cầu đào tạo, bồi dưỡng chuyên môn nghiệp vụ năm 2027",
         "cv-1032-dao-tao.pdf", 1, timedelta(days=3)),
        (36, "07/TB-HĐND", "Thông báo kết luận của Thường trực Hội đồng nhân dân tỉnh tại phiên họp tháng 9",
         "tb-07-ket-luan.pdf", 2, timedelta(days=6)),
        (35, "19/2026/NQ-HĐND", "Nghị quyết quy định mức chi bảo đảm cho công tác cải cách hành chính trên địa bàn tỉnh",
         "nq-19-2026.pdf", 7, timedelta(days=12)),
        (34, "556/UBND-NC", "V/v tăng cường công tác bảo vệ bí mật nhà nước trong cơ quan, đơn vị",
         "cv-556-bao-mat.pdf", 3, timedelta(days=20)),
    ]
    return {
        "documentCount": 0 if empty else 1186,
        "chunkCount": 0 if empty else 24318,
        "recentDocuments": [] if empty else [
            {"id": doc_id, "symbol": symbol, "summary": summary, "title": title,
             "pageCount": pages, "updatedAt": _stamp(age)}
            for doc_id, symbol, summary, title, pages, age in recent
        ],
        "models": {"ocr": "available", "chat": "missing" if empty else "available"},
        "theme": theme,
        "offline": True,
        "commands": landing_commands(),
    }


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


def shoot(window, path):
    window.grab().save(str(path))
    print(path)


def render(out, theme, size, name, snapshot, prepare=None, native=False):
    window = landing_page.LandingWindow(lambda: snapshot, force_native=native)
    window.setMinimumSize(*size)
    window.resize(*size)
    window.move(-size[0] - 400, 0)
    window.show()
    wait(2200 if not native else 300)
    if prepare:
        prepare(window)
        wait(900)
    shoot(window, out / "{}_{}_{}x{}.png".format(name, theme, *size))
    window.close()
    window.deleteLater()
    wait(150)


def _focus_by_keyboard(selector):
    def prepare(window):
        view = window._web_view
        if view is None:
            return
        view.setFocus()
        view.page().runJavaScript(
            "document.documentElement.dataset.input='keyboard';"
            "document.querySelector({!r}).focus();".format(selector)
        )
    return prepare


def _progress(window):
    window.set_progress(65, "Đang khởi tạo OCR và kho tri thức…")


def _about(window):
    if window._web_view is not None:
        window._web_view.page().runJavaScript("window.landingUI.openAbout();")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(argv[:1])
    load_application_fonts(app)
    for theme in ("light", "dark"):
        for size in SIZES:
            render(out, theme, size, "home", sample_snapshot(theme))
        render(out, theme, SIZES[0], "tooltip", sample_snapshot(theme),
               _focus_by_keyboard('[data-action="open_files"]'))
        render(out, theme, SIZES[1], "empty", sample_snapshot(theme, empty=True))
        render(out, theme, SIZES[1], "startup", sample_snapshot(theme), _progress)
        render(out, theme, SIZES[1], "about", sample_snapshot(theme), _about)
        render(out, theme, SIZES[1], "native", sample_snapshot(theme), native=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
