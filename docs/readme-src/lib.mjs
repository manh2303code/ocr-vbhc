// Shared drawing kit for the README visuals: theme tokens, a text engine that
// sets Be Vietnam Pro as outlines (so GitHub needs no font), icons, edges.
import fs from "node:fs";
import path from "node:path";

// ── Theme tokens (the app's own, theme_tokens.py / readme brief) ─────────
export const THEMES = {
  light: {
    name: "light",
    bg: "#EEF0EE", surface: "#FFFFFF", alt: "#F6F7F6",
    ink: "#18211B", ink2: "#48524B", muted: "#5D6760",
    border: "#D5DAD5", line: "#8C978F", grid: "#D5DAD5",
    olive: "#2B4A33", oliveText: "#2B4A33", oliveSoft: "#E3EBE5", oliveEdge: "#A7BDAD",
    gold: "#7A5D0F", goldSoft: "#F4EBCF", goldEdge: "#D6C182",
    red: "#B3263E", onOlive: "#FFFFFF",
    // Ordinal olive ramp, validated (dataviz validate_palette.js --ordinal,
    // surface #EEF0EE): light end 2.09:1, monotone, one hue.
    ramp: ["#93AE9B", "#5E8569", "#2B4A33"],
    track: "#E0E4E0",
    // Two-entity categorical pair (validated: chroma, band, CVD 16.2, 3:1).
    cat: ["#367D4C", "#3F6FB0"],
    flow: "#2B4A33",
  },
  dark: {
    name: "dark",
    bg: "#121513", surface: "#191D1A", alt: "#1F2420",
    ink: "#E6E9E6", ink2: "#B2BAB3", muted: "#929B94",
    border: "#2B312C", line: "#56605A", grid: "#2B312C",
    olive: "#3F6A4B", oliveText: "#8CB497", oliveSoft: "#1F2B22", oliveEdge: "#36513D",
    gold: "#E2C46A", goldSoft: "#352E17", goldEdge: "#5E5024",
    red: "#F28B8B", onOlive: "#FFFFFF",
    // Same ramp stepped for the dark surface #121513 (validated, 3.24:1).
    ramp: ["#4F6E57", "#6E9479", "#8CB497"],
    track: "#222823",
    cat: ["#4FA067", "#6F95D6"],
    flow: "#8CB497",
  },
};

// ── Numbers in each language ────────────────────────────────────────────
export function num(value, lang, decimals = 0) {
  const fixed = Number(value).toFixed(decimals);
  const [whole, frac] = fixed.split(".");
  const sep = lang === "vi" ? "." : ",";
  const grouped = whole.length > 4 ? whole.replace(/\B(?=(\d{3})+(?!\d))/g, sep) : whole;
  return frac ? grouped + (lang === "vi" ? "," : ".") + frac : grouped;
}

const WEIGHT_PREFIX = { 400: "r", 500: "m", 600: "b" };
// Glyphs Be Vietnam Pro lacks, drawn in font units (y down, baseline 0).
const SYNTH = {
  "→": (w) => ({ adv: 720, d: w >= 600
    ? "M70-332H500V-440L668-290L500-140V-248H70Z"
    : "M70-322H514V-420L660-290L514-160V-258H70Z" }),
  "←": (w) => ({ adv: 720, d: w >= 600
    ? "M650-332H220V-440L52-290L220-140V-248H650Z"
    : "M650-322H206V-420L60-290L206-160V-258H650Z" }),
};

const r1 = (v) => Math.round(v * 10) / 10;

export class Canvas {
  constructor(glyphs, theme, lang, width, height) {
    this.G = glyphs;
    this.t = theme;
    this.lang = lang;
    this.W = width;
    this.H = height;
    this.ids = new Map();
    this.iconDefs = new Map();
    this.markers = new Map();
    this.problems = [];
    this.textLog = []; // [x0, y0, x1, y1, str] for overlap checks
  }

  glyph(weight, ch) {
    const table = this.G[weight];
    if (!table) throw new Error("weight " + weight + " not loaded");
    const glyph = table.glyphs[ch] || (SYNTH[ch] && SYNTH[ch](weight));
    if (!glyph) throw new Error("missing glyph " + JSON.stringify(ch) + " (U+" + ch.codePointAt(0).toString(16) + ")");
    return glyph;
  }

  gid(weight, ch) {
    const key = weight + ch;
    let id = this.ids.get(key);
    if (!id) {
      id = WEIGHT_PREFIX[weight] + this.ids.size.toString(36);
      this.ids.set(key, id);
    }
    return id;
  }

  units(str, weight) {
    let total = 0;
    let prev = null;
    for (const ch of str.normalize("NFC")) {
      if (prev) total += this.G[weight].kern[prev + ch] || 0;
      total += this.glyph(weight, ch).adv;
      prev = ch;
    }
    return total;
  }

  width(str, size = 14, weight = 400) {
    return (this.units(str, weight) * size) / 1000;
  }

  // One line of text set as outlines; y is the baseline.
  text(str, x, y, o = {}) {
    const size = o.size || 14;
    const weight = o.weight || 400;
    const fill = o.fill || this.t.ink;
    str = String(str).normalize("NFC");
    const k = size / 1000;
    const w = this.units(str, weight) * k;
    const x0 = o.anchor === "middle" ? x - w / 2 : o.anchor === "end" ? x - w : x;
    let pos = 0;
    let prev = null;
    const uses = [];
    for (const ch of str) {
      if (prev) pos += this.G[weight].kern[prev + ch] || 0;
      const g = this.glyph(weight, ch);
      if (g.d) uses.push('<use href="#' + this.gid(weight, ch) + '"' + (pos ? ' x="' + pos + '"' : "") + "/>");
      pos += g.adv;
      prev = ch;
    }
    this.textLog.push([x0, y - size * 0.95, x0 + w, y + size * 0.25, str]);
    const attrs = (o.cls ? ' class="' + o.cls + '"' : "") + (o.opacity != null ? ' opacity="' + o.opacity + '"' : "");
    return '<g transform="translate(' + r1(x0) + " " + r1(y) + ") scale(" + +k.toFixed(5) + ')" fill="' + fill + '"' + attrs + ">" + uses.join("") + "</g>";
  }

  // Greedy word wrap; "\n" forces a break.
  wrap(str, maxW, size = 14, weight = 400) {
    const lines = [];
    for (const para of String(str).split("\n")) {
      let line = "";
      for (const word of para.split(" ")) {
        const trial = line ? line + " " + word : word;
        if (!line || this.width(trial, size, weight) <= maxW) line = trial;
        else {
          lines.push(line);
          line = word;
        }
      }
      lines.push(line);
    }
    return lines;
  }

  // A wrapped paragraph; returns {svg, height, lines, bottom}.
  para(str, x, y, maxW, o = {}) {
    const size = o.size || 14;
    const lh = (o.lh || 1.42) * size;
    const lines = this.wrap(str, maxW, size, o.weight || 400);
    let svg = "";
    lines.forEach((line, i) => {
      svg += this.text(line, x, y + i * lh, o);
      const w = this.width(line, size, o.weight || 400);
      if (w > maxW + 0.5) this.problems.push("too wide (" + Math.round(w) + " > " + maxW + "): " + line);
    });
    return { svg, lines, height: lines.length * lh, bottom: y + (lines.length - 1) * lh + size * 0.3 };
  }

  icon(name, x, y, size, fill) {
    if (!this.iconDefs.has(name)) {
      const file = path.join(ICON_DIR, name + ".svg");
      const raw = fs.readFileSync(file, "utf8");
      const inner = raw.replace(/^[\s\S]*?<svg[^>]*>/, "").replace(/<\/svg>\s*$/, "");
      this.iconDefs.set(name, '<symbol id="i-' + name + '" viewBox="0 0 20 20">' + inner + "</symbol>");
    }
    return '<use href="#i-' + name + '" x="' + r1(x) + '" y="' + r1(y) + '" width="' + size + '" height="' + size + '" fill="' + fill + '"/>';
  }

  marker(color) {
    let id = this.markers.get(color);
    if (!id) {
      id = "ah" + this.markers.size;
      this.markers.set(color, id);
    }
    return id;
  }

  defs() {
    const glyphDefs = [];
    for (const [key, id] of this.ids) {
      const weight = key.slice(0, 3);
      const ch = key.slice(3);
      glyphDefs.push('<path id="' + id + '" d="' + this.glyph(Number(weight), ch).d + '"/>');
    }
    const markers = [];
    for (const [color, id] of this.markers) {
      markers.push('<marker id="' + id + '" viewBox="0 0 10 10" refX="8.6" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto">' +
        '<path d="M1.6 1.4L8.6 5L1.6 8.6" fill="none" stroke="' + color + '" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></marker>');
    }
    return "<defs>" + glyphDefs.join("") + [...this.iconDefs.values()].join("") + markers.join("") + "</defs>";
  }
}

let ICON_DIR = "";
export function setIconDir(dir) {
  ICON_DIR = dir;
}

// ── Geometry helpers ────────────────────────────────────────────────────
// Polyline with rounded elbows.
export function roundedPath(points, radius = 10) {
  let d = "M" + r1(points[0][0]) + " " + r1(points[0][1]);
  for (let i = 1; i < points.length; i++) {
    const [x, y] = points[i];
    if (i < points.length - 1) {
      const [px, py] = points[i - 1];
      const [nx, ny] = points[i + 1];
      const l1 = Math.hypot(x - px, y - py);
      const l2 = Math.hypot(nx - x, ny - y);
      const r = Math.min(radius, l1 / 2, l2 / 2);
      const ax = x - ((x - px) / l1) * r;
      const ay = y - ((y - py) / l1) * r;
      const bx = x + ((nx - x) / l2) * r;
      const by = y + ((ny - y) / l2) * r;
      d += "L" + r1(ax) + " " + r1(ay) + "Q" + r1(x) + " " + r1(y) + " " + r1(bx) + " " + r1(by);
    } else {
      d += "L" + r1(x) + " " + r1(y);
    }
  }
  return d;
}

// Shorten the last segment of a polyline by `by` px.
export function trimEnd(points, by) {
  const pts = points.map((p) => p.slice());
  const n = pts.length;
  const [px, py] = pts[n - 2];
  const [x, y] = pts[n - 1];
  const l = Math.hypot(x - px, y - py);
  pts[n - 1] = [x - ((x - px) / l) * by, y - ((y - py) / l) * by];
  return pts;
}

// An edge: hairline with an arrowhead, plus an animated dotted overlay.
export function edge(c, points, o = {}) {
  const color = o.color || c.t.line;
  const width = o.width || 1.5;
  const head = o.head !== false;
  const d = roundedPath(points, o.radius || 10);
  let svg = '<path d="' + d + '" fill="none" stroke="' + color + '" stroke-width="' + width + '" stroke-linecap="round" stroke-linejoin="round"' +
    (head ? ' marker-end="url(#' + c.marker(color) + ')"' : "") + "/>";
  if (o.flow !== false) {
    const fd = roundedPath(head ? trimEnd(points, 10) : points, o.radius || 10);
    svg += '<path class="flow" d="' + fd + '"' + (o.delay ? ' style="animation-delay:' + o.delay + 's"' : "") + "/>";
  }
  return svg;
}

export function rect(x, y, w, h, o = {}) {
  return '<rect x="' + r1(x) + '" y="' + r1(y) + '" width="' + r1(w) + '" height="' + r1(h) + '"' +
    (o.rx ? ' rx="' + o.rx + '"' : "") +
    ' fill="' + (o.fill || "none") + '"' +
    (o.stroke ? ' stroke="' + o.stroke + '" stroke-width="' + (o.sw || 1) + '"' : "") +
    (o.cls ? ' class="' + o.cls + '"' : "") +
    (o.style ? ' style="' + o.style + '"' : "") +
    (o.opacity != null ? ' opacity="' + o.opacity + '"' : "") + "/>";
}

// Node fills encode what runs there: a local model, the app's own code, or
// a verification step.
export function kindColors(t, kind) {
  if (kind === "model") return { fill: t.oliveSoft, stroke: t.oliveEdge, icon: t.oliveText };
  if (kind === "check") return { fill: t.goldSoft, stroke: t.goldEdge, icon: t.gold };
  return { fill: t.surface, stroke: t.border, icon: t.ink2 };
}

// A stage: icon + title + wrapped detail lines, checked to fit its box.
export function node(c, o) {
  const t = c.t;
  const pad = o.pad || 14;
  const col = kindColors(t, o.kind);
  const titleSize = o.titleSize || 15;
  const subSize = o.subSize || 13.5;
  let svg = rect(o.x, o.y, o.w, o.h, { rx: o.rx || 10, fill: col.fill, stroke: col.stroke, sw: 1 });
  let tx = o.x + pad;
  const iconSize = 18;
  if (o.icon) {
    svg += c.icon(o.icon, o.x + pad - 1, o.y + pad - 2, iconSize, col.icon);
    tx += iconSize + 7;
  }
  const title = c.para(o.title, tx, o.y + pad + 12, o.x + o.w - pad - tx, { size: titleSize, weight: 600, fill: t.ink, lh: 1.3 });
  svg += title.svg;
  let y = title.bottom + (o.gap || 10) + subSize * 0.75;
  for (const raw of o.lines || []) {
    const line = typeof raw === "string" ? { text: raw } : raw;
    const p = c.para(line.text, o.x + pad, y, o.w - pad * 2, {
      size: line.size || subSize, weight: line.weight || 400, fill: (line.fill && t[line.fill]) || line.fill || t.muted, lh: 1.38,
    });
    svg += p.svg;
    y = p.bottom + (line.after || 5) + subSize * 0.72;
  }
  const lastBottom = y - (subSize * 0.72) - 5;
  if (lastBottom > o.y + o.h - 8) c.problems.push("node overflow: " + o.title + " (" + Math.round(lastBottom - (o.y + o.h - 8)) + "px)");
  o.contentBottom = lastBottom;
  return svg;
}

export function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// Animation and motion preferences shared by every visual.
export function baseStyle(t, extra = "") {
  return "<style>" +
    ".flow{fill:none;stroke:" + t.flow + ";stroke-width:3.2;stroke-linecap:round;stroke-dasharray:0 13;opacity:.75;animation:flow 1.3s linear infinite}" +
    "@keyframes flow{to{stroke-dashoffset:-13}}" +
    ".pulse{fill:none;stroke:" + t.flow + ";stroke-width:2;opacity:0;animation:pulse 12s ease-in-out infinite}" +
    "@keyframes pulse{0%{opacity:0}3%{opacity:.9}10%{opacity:0}100%{opacity:0}}" +
    extra +
    "@media (prefers-reduced-motion:reduce){.flow,.pulse,.anim{animation:none!important}}" +
    "</style>";
}

// Wrap a finished drawing into a standalone SVG document.
export function finish(c, body, meta) {
  const head = '<svg xmlns="http://www.w3.org/2000/svg" width="' + c.W + '" height="' + c.H + '" viewBox="0 0 ' + c.W + " " + c.H + '" role="img" aria-labelledby="title desc">' +
    '<title id="title">' + esc(meta.title) + "</title>" +
    '<desc id="desc">' + esc(meta.desc) + "</desc>";
  return head + (meta.style || "") + c.defs() + body + "</svg>\n";
}

// Overlap check between logged text boxes (labels that collide).
export function textOverlaps(c, slack = 1) {
  const boxes = c.textLog;
  const hits = [];
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i];
      const b = boxes[j];
      if (a[0] < b[2] - slack && b[0] < a[2] - slack && a[1] < b[3] - slack && b[1] < a[3] - slack) {
        hits.push(a[4] + "  <>  " + b[4]);
      }
    }
  }
  return hits;
}
