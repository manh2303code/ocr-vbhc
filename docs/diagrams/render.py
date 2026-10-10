"""Render the hand-drawn diagrams in this folder to PNG.

GitHub shows images in a README but not HTML, so each diagram page is
screenshotted with a headless Chromium (Edge ships with Windows; Chrome works
too). The page declares its own size in <meta name="sketch-size" content="WxH">
and the window is set to exactly that, so nothing is cropped or padded.

    python docs/diagrams/render.py                 # every *.html here
    python docs/diagrams/render.py 01-architecture.html

The pages load rough.js and the handwriting fonts from public CDNs, so
rendering needs network access. The application itself does not.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCALE = "1.5"
CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
_SIZE = re.compile(r'<meta\s+name="sketch-size"\s+content="(\d+)x(\d+)"')


def find_browser():
    override = os.environ.get("SKETCH_BROWSER")
    if override:
        return override
    for path in CANDIDATES:
        if Path(path).is_file():
            return path
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("No Chromium-based browser found; set SKETCH_BROWSER.")


def render(page, browser):
    match = _SIZE.search(page.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("{}: missing <meta name=\"sketch-size\">".format(page.name))
    width, height = match.groups()
    out = page.with_suffix(".png")
    if out.exists():
        out.unlink()
    profile = tempfile.mkdtemp(prefix="sketch-render-")
    try:
        command = [
            browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--no-first-run", "--no-default-browser-check", "--mute-audio",
            "--user-data-dir=" + profile,
            "--force-device-scale-factor=" + SCALE,
            "--window-size={},{}".format(width, height),
            # Long enough for fonts and rough.js to arrive before the capture.
            "--virtual-time-budget=20000",
            "--screenshot=" + str(out),
            page.as_uri(),
        ]
        # On Windows msedge.exe hands off to a child process and returns at
        # once, so neither its exit nor its pipes say the capture is done.
        # Wait for the file itself, and for its size to stop changing.
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 180
        last = -1
        while time.time() < deadline:
            size = out.stat().st_size if out.is_file() else -1
            if size > 0 and size == last:
                break
            last = size
            time.sleep(1.0)
        else:
            raise SystemExit("{}: no screenshot after 180 s".format(page.name))
    finally:
        # The browser may still be releasing its profile; leftovers in the
        # temp directory are harmless and not worth failing the render over.
        time.sleep(1.0)
        shutil.rmtree(profile, ignore_errors=True)
    print("{:<36} -> {} ({} KB)".format(page.name, out.name, out.stat().st_size // 1024))


def main(argv):
    browser = find_browser()
    names = argv or sorted(p.name for p in HERE.glob("[0-9]*.html"))
    for name in names:
        render(HERE / name, browser)


if __name__ == "__main__":
    main(sys.argv[1:])
