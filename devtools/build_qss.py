"""Generate theme.qss and theme_dark.qss from one template and the tokens.

    runtime\\python.exe devtools\\build_qss.py          write the files
    runtime\\python.exe devtools\\build_qss.py --check  exit 1 if they are stale

The two stylesheets used to be written by hand and had drifted (about thirty
selectors existed in one theme only). Now both come from
devtools/theme_template.qss, with {{token}} replaced by theme_tokens values
and {{theme}} by "light" or "dark". Icons that QSS draws itself (combo and
calendar arrows, the check mark) are written to icons/qss/, coloured per
theme from the Fluent SVGs in icons/fluent/.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import theme_tokens  # noqa: E402

TEMPLATE = ROOT / "devtools" / "theme_template.qss"
OUTPUTS = {"light": ROOT / "theme.qss", "dark": ROOT / "theme_dark.qss"}
QSS_ICONS = ROOT / "icons" / "qss"
# icon -> token that colours it, per theme
ARROWS = ("chevron-down", "chevron-up", "chevron-left", "chevron-right")


def render(theme):
    tokens = theme_tokens.colors_for(theme)
    text = TEMPLATE.read_text(encoding="utf-8")

    def value(match):
        name = match.group(1)
        if name == "theme":
            return theme
        if name not in tokens:
            raise KeyError("unknown token {{{{{}}}}} in the template".format(name))
        return tokens[name]

    return re.sub(r"\{\{(\w+)\}\}", value, text)


def icons():
    """(path, svg text) for every icon the stylesheets reference."""
    files = {}
    for theme in ("light", "dark"):
        colour = theme_tokens.colors_for(theme)["text_secondary"]
        for name in ARROWS:
            svg = (ROOT / "icons" / "fluent" / (name + ".svg")).read_text(encoding="utf-8")
            files[QSS_ICONS / "{}-{}.svg".format(name, theme)] = svg.replace("currentColor", colour)
    check = (ROOT / "icons" / "fluent" / "check.svg").read_text(encoding="utf-8")
    files[QSS_ICONS / "check-on-primary.svg"] = check.replace(
        "currentColor", theme_tokens.COLORS["text_on_primary"])
    return files


def expected():
    files = {path: render(theme) for theme, path in OUTPUTS.items()}
    files.update(icons())
    return files


def main(argv):
    stale = []
    for path, text in expected().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != text:
            stale.append(path)
            if "--check" not in argv:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
    for path in stale:
        print(("stale: " if "--check" in argv else "wrote: ") + str(path.relative_to(ROOT)))
    return 1 if stale and "--check" in argv else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
