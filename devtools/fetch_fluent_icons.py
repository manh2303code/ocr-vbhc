"""Fetch the Fluent UI System Icons the app uses into icons/fluent/.

    runtime\\python.exe devtools\\fetch_fluent_icons.py

Source: the npm package @fluentui/svg-icons (Microsoft, MIT), pinned to
VERSION, served by cdn.jsdelivr.net. Each app icon name maps to one or more
Fluent names; the first that exists is saved as icons/fluent/<app name>.svg
and recorded in icons/fluent/MANIFEST.json. Run it again after changing
ICONS; the app itself never downloads anything.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "1.1.344"
BASE = "https://cdn.jsdelivr.net/npm/@fluentui/svg-icons@{}/icons/".format(VERSION)
OUT = Path(__file__).resolve().parents[1] / "icons" / "fluent"

# app name -> Fluent candidates (20 px grid, regular unless noted)
ICONS = {
    "archive": ["archive_20_regular"],
    "arrow-down": ["arrow_down_20_regular"],
    "arrow-up": ["arrow_up_20_regular"],
    "bar-chart-3": ["data_bar_vertical_20_regular"],
    "calendar": ["calendar_ltr_20_regular", "calendar_20_regular"],
    "check": ["checkmark_20_regular"],
    "chevron-down": ["chevron_down_20_regular"],
    "chevron-up": ["chevron_up_20_regular"],
    "chevron-left": ["chevron_left_20_regular"],
    "chevron-right": ["chevron_right_20_regular"],
    "chevrons-left": ["chevron_double_left_20_regular"],
    "chevrons-right": ["chevron_double_right_20_regular"],
    "circle-alert": ["error_circle_20_regular"],
    "command": ["keyboard_20_regular"],
    "copy": ["copy_20_regular"],
    "database": ["database_20_regular"],
    "eye": ["eye_20_regular"],
    "file-plus": ["document_add_20_regular"],
    "file-text": ["document_text_20_regular"],
    "files": ["document_multiple_20_regular"],
    "folder-open": ["folder_open_20_regular"],
    "folder-search": ["folder_search_20_regular", "folder_open_20_regular"],
    "help-circle": ["question_circle_20_regular"],
    "house": ["home_20_regular"],
    "info": ["info_20_regular"],
    "loader-2": ["spinner_ios_20_regular", "arrow_sync_20_regular"],
    "minus": ["subtract_20_regular"],
    "moon": ["weather_moon_20_regular"],
    "sun": ["weather_sunny_20_regular"],
    "more-horizontal": ["more_horizontal_20_regular"],
    "panel-left": ["panel_left_20_regular"],
    "panel-right": ["panel_right_20_regular"],
    "pause": ["pause_20_regular"],
    "plus": ["add_20_regular"],
    "refresh-cw": ["arrow_clockwise_20_regular"],
    "rotate-ccw": ["arrow_rotate_counterclockwise_20_regular"],
    "rotate-cw": ["arrow_rotate_clockwise_20_regular"],
    "save": ["save_20_regular"],
    "search": ["search_20_regular"],
    "settings": ["settings_20_regular"],
    "sparkles": ["sparkle_20_regular"],
    "tag": ["tag_20_regular"],
    "trash-2": ["delete_20_regular"],
    "undo-2": ["arrow_undo_20_regular"],
    "x": ["dismiss_20_regular"],
    "zoom-in": ["zoom_in_20_regular"],
    "zoom-out": ["zoom_out_20_regular"],
    # added for the minimal layout
    "chat": ["chat_20_regular"],
    "stop": ["record_stop_20_regular", "stop_20_regular"],
    "send": ["send_20_regular"],
    "expand": ["arrow_maximize_20_regular"],
    "collapse": ["arrow_minimize_20_regular"],
    "filter": ["filter_20_regular"],
    "sort": ["arrow_sort_20_regular"],
    "scan": ["scan_text_20_regular", "document_text_20_regular"],
    "open": ["open_20_regular"],
    "edit": ["edit_20_regular"],
    "document-search": ["document_search_20_regular"],
    "text-description": ["text_description_20_regular"],
    "status-ok": ["checkmark_circle_20_filled"],
    "status-partial": ["circle_half_fill_20_regular"],
    "status-waiting": ["circle_20_regular"],
    "app-mark": ["document_one_page_20_regular", "document_20_regular"],
    "word": ["document_text_extract_20_regular", "document_text_20_regular"],
    "excel": ["table_20_regular"],
    "dismiss-circle": ["dismiss_circle_20_regular"],
    # workspace chrome: the toolbar mark (solid, unlike the outline tools),
    # overflow menu (rebuild AI index, command search)
    "app-mark-solid": ["document_one_page_20_filled", "document_20_filled"],
    "sync": ["arrow_sync_20_regular"],
    "command-search": ["search_square_20_regular", "apps_list_20_regular"],
    # secondary surfaces: the kind of a toast (success, warning)
    "check-circle": ["checkmark_circle_20_regular"],
    "warning": ["warning_20_regular"],
}


def fetch(name):
    try:
        with urllib.request.urlopen(BASE + name + ".svg", timeout=20) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _write(path, text):
    # LF on every system, like the rest of the repository (write_text would
    # turn the line ends into CRLF on Windows and touch every icon).
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest, missing = {}, []
    for app_name, candidates in sorted(ICONS.items()):
        for candidate in candidates:
            svg = fetch(candidate)
            if svg and "<svg" in svg:
                # Fluent paths carry no colour; ui_icons.icon() recolours
                # by replacing currentColor, so the root fill names it.
                root_end = svg.index(">", svg.index("<svg"))
                if "fill=" not in svg[:root_end]:
                    svg = svg.replace("<svg ", '<svg fill="currentColor" ', 1)
                _write(OUT / (app_name + ".svg"), svg.strip() + "\n")
                manifest[app_name] = candidate
                break
        else:
            missing.append(app_name)
    _write(OUT / "MANIFEST.json", json.dumps(
        {"package": "@fluentui/svg-icons", "version": VERSION, "license": "MIT",
         "icons": manifest}, indent=1, sort_keys=True) + "\n")
    print("{} icons saved to {}".format(len(manifest), OUT))
    if missing:
        print("not found:", ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
