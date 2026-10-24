"""Glyph outlines, advances and pair kerning of Be Vietnam Pro, as JSON.

build.mjs draws every label as outlines (<path> glyphs reused with <use>),
so the README visuals look the same on GitHub whatever fonts the viewer
has and need no web font (GitHub serves repository SVGs under a strict
Content-Security-Policy). This script reads the bundled TTFs once and writes
what the layout needs:

    {"400": {"upm": 1000, "glyphs": {"A": {"adv": 640, "d": "m..."}},
             "kern": {"AV": -40}}, "600": {...}}

Usage (any Python 3.8+ with fontTools, e.g. the portable runtime):
    python glyphs.py <fonts_dir> <out.json>
"""

import json
import sys
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

WEIGHTS = {
    "400": "BeVietnamPro-Regular.ttf",
    "500": "BeVietnamPro-Medium.ttf",
    "600": "BeVietnamPro-SemiBold.ttf",
}

VIETNAMESE = (
    "ÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝàáâãèéêìíòóôõùúýĂăĐđĨĩŨũƠơƯư"
    + "".join(chr(cp) for cp in range(0x1EA0, 0x1EFA))
)
EXTRA = "–—…→←↑↓≥≤≈×‘’“”•°±·✓"
REPERTOIRE = "".join(chr(cp) for cp in range(0x20, 0x7F)) + VIETNAMESE + EXTRA


def _num(values):
    out = ""
    for value in values:
        text = str(value)
        if out and not text.startswith("-"):
            out += " "
        out += text
    return out


class RelativePen(BasePen):
    """SVG path data in font units, y flipped, relative integer commands."""

    def __init__(self, glyph_set):
        super().__init__(glyph_set)
        self.parts = []
        self.cur = (0, 0)
        self.start = (0, 0)

    @staticmethod
    def _pt(point):
        return int(round(point[0])), int(round(-point[1]))

    def _rel(self, point):
        x, y = self._pt(point)
        return x - self.cur[0], y - self.cur[1], (x, y)

    def _moveTo(self, pt):
        dx, dy, absolute = self._rel(pt)
        self.parts.append("m" + _num((dx, dy)))
        self.cur = self.start = absolute

    def _lineTo(self, pt):
        dx, dy, absolute = self._rel(pt)
        if dx == 0 and dy == 0:
            return
        if dy == 0:
            self.parts.append("h" + _num((dx,)))
        elif dx == 0:
            self.parts.append("v" + _num((dy,)))
        else:
            self.parts.append("l" + _num((dx, dy)))
        self.cur = absolute

    def _qCurveToOne(self, pt1, pt2):
        x1, y1 = self._pt(pt1)
        x2, y2 = self._pt(pt2)
        cx, cy = self.cur
        self.parts.append("q" + _num((x1 - cx, y1 - cy, x2 - cx, y2 - cy)))
        self.cur = (x2, y2)

    def _curveToOne(self, pt1, pt2, pt3):
        x1, y1 = self._pt(pt1)
        x2, y2 = self._pt(pt2)
        x3, y3 = self._pt(pt3)
        cx, cy = self.cur
        self.parts.append("c" + _num((x1 - cx, y1 - cy, x2 - cx, y2 - cy, x3 - cx, y3 - cy)))
        self.cur = (x3, y3)

    def _closePath(self):
        self.parts.append("z")
        self.cur = self.start

    def _endPath(self):
        self.cur = self.start

    def data(self):
        return "".join(self.parts)


def kern_pairs(font, names):
    """{(first, second): x advance adjustment} from the GPOS kern feature."""
    if "GPOS" not in font:
        return {}
    gpos = font["GPOS"].table
    lookups = set()
    for record in gpos.FeatureList.FeatureRecord:
        if record.FeatureTag == "kern":
            lookups.update(record.Feature.LookupListIndex)
    wanted = set(names)
    pairs = {}
    for index in sorted(lookups):
        lookup = gpos.LookupList.Lookup[index]
        found = {}
        for sub in lookup.SubTable:
            if lookup.LookupType == 9:
                sub = sub.ExtSubTable
            if getattr(sub, "LookupType", 2) != 2:
                continue
            coverage = sub.Coverage.glyphs
            if sub.Format == 1:
                for i, first in enumerate(coverage):
                    if first not in wanted:
                        continue
                    for record in sub.PairSet[i].PairValueRecord:
                        second = record.SecondGlyph
                        if second in wanted and (first, second) not in found:
                            value = getattr(record.Value1, "XAdvance", 0) if record.Value1 else 0
                            found[(first, second)] = value or 0
            elif sub.Format == 2:
                class1 = sub.ClassDef1.classDefs
                class2 = sub.ClassDef2.classDefs
                for first in coverage:
                    if first not in wanted:
                        continue
                    row = sub.Class1Record[class1.get(first, 0)]
                    for second in wanted:
                        if (first, second) in found:
                            continue
                        value1 = row.Class2Record[class2.get(second, 0)].Value1
                        value = getattr(value1, "XAdvance", 0) if value1 else 0
                        if value:
                            found[(first, second)] = value
        for key, value in found.items():
            if value:
                pairs[key] = pairs.get(key, 0) + value
    return pairs


def extract(path):
    font = TTFont(str(path))
    cmap = font.getBestCmap()
    glyph_set = font.getGlyphSet()
    hmtx = font["hmtx"]
    glyphs, names, missing = {}, {}, []
    for char in REPERTOIRE:
        name = cmap.get(ord(char))
        if name is None:
            missing.append(char)
            continue
        pen = RelativePen(glyph_set)
        glyph_set[name].draw(pen)
        glyphs[char] = {"adv": hmtx[name][0], "d": pen.data()}
        names[name] = char
    kern = {}
    for (first, second), value in kern_pairs(font, list(names)).items():
        kern[names[first] + names[second]] = value
    return {
        "upm": font["head"].unitsPerEm,
        "ascender": font["hhea"].ascent,
        "descender": font["hhea"].descent,
        "glyphs": glyphs,
        "kern": kern,
        "missing": "".join(missing),
    }


def main():
    fonts_dir, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    data = {weight: extract(fonts_dir / file) for weight, file in WEIGHTS.items()}
    Path(out_path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for weight, entry in data.items():
        print(weight, len(entry["glyphs"]), "glyphs,", len(entry["kern"]), "kern pairs, missing:",
              ascii(entry["missing"]))


if __name__ == "__main__":
    main()
