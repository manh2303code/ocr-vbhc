/*
 * Hand-drawn diagram kit for the OCR VBHC documentation.
 *
 * Every diagram in docs/diagrams is drawn through this file so they share one
 * palette, one pair of handwriting fonts and one vocabulary of shapes. It wraps
 * rough.js, which supplies the wobble; this file supplies the house style.
 *
 * Usage, inside a diagram page:
 *
 *   Sketch.draw({ width: 1600, height: 1000, title: "…", subtitle: "…" }, (s) => {
 *     const a = s.box(120, 200, 260, 110, { label: "Landing", sub: "QWebEngine", tone: "olive" });
 *     const b = s.box(520, 200, 260, 110, { label: "Workspace", tone: "gold" });
 *     s.arrow(a.right, b.left, { label: "chọn tác vụ" });
 *   });
 *
 * Seeds are deterministic, so re-rendering an unchanged page produces the same
 * strokes and diffs of the PNGs stay meaningful.
 */
(function (global) {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const INK = "#17231B";
  const MUTED = "#5C665F";
  const PAPER = "#F5F1E8";
  const HAND = "'Patrick Hand', 'Segoe Print', cursive";
  const TITLE = "'Pangolin', 'Patrick Hand', cursive";

  // Fill, stroke and the ink used for text written on that fill. Text inks are
  // darker than strokes so labels clear WCAG AA on their own fill.
  const TONES = {
    olive: { fill: "#E2EAE4", stroke: "#24432D", ink: "#1C3523" },
    gold:  { fill: "#F2E8C8", stroke: "#846314", ink: "#5E460C" },
    red:   { fill: "#F6DCE0", stroke: "#B3263E", ink: "#86192B" },
    blue:  { fill: "#DFE7FB", stroke: "#235FE1", ink: "#1A46A8" },
    paper: { fill: "#FFFEFA", stroke: "#17231B", ink: "#17231B" },
    muted: { fill: "#EAE4D7", stroke: "#5C665F", ink: "#3A433D" },
    ink:   { fill: "#FFFEFA", stroke: "#17231B", ink: "#17231B" },
  };

  function tone(name) {
    return TONES[name] || TONES.paper;
  }

  function make(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const key of Object.keys(attrs || {})) node.setAttribute(key, attrs[key]);
    if (parent) parent.appendChild(node);
    return node;
  }

  function anchors(x, y, w, h) {
    const cx = x + w / 2;
    const cy = y + h / 2;
    return {
      x, y, w, h, cx, cy,
      top: [cx, y], bottom: [cx, y + h], left: [x, cy], right: [x + w, cy],
      topLeft: [x, y], topRight: [x + w, y],
      bottomLeft: [x, y + h], bottomRight: [x + w, y + h],
      // Any point on the box as fractions of its width and height.
      at(fx, fy) { return [x + w * fx, y + h * fy]; },
    };
  }

  function roundedRect(x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    return [
      "M", x + r, y, "H", x + w - r, "Q", x + w, y, x + w, y + r,
      "V", y + h - r, "Q", x + w, y + h, x + w - r, y + h,
      "H", x + r, "Q", x, y + h, x, y + h - r,
      "V", y + r, "Q", x, y, x + r, y, "Z",
    ].join(" ");
  }

  function build(config, svg) {
    const W = config.width;
    const H = config.height;
    const rc = rough.svg(svg);
    let seed = config.seed || 7;
    const nextSeed = () => (seed = (seed * 9301 + 49297) % 233280) || 1;

    const layers = {};
    for (const name of ["background", "frames", "arrows", "shapes", "text"]) {
      layers[name] = make("g", { "data-layer": name }, svg);
    }

    // ── paper ──────────────────────────────────────────────────────────────
    const defs = make("defs", {}, svg);
    svg.insertBefore(defs, svg.firstChild);
    const pattern = make("pattern", {
      id: "dots", width: 26, height: 26, patternUnits: "userSpaceOnUse",
    }, defs);
    make("circle", { cx: 2, cy: 2, r: 1.1, fill: INK, opacity: 0.09 }, pattern);
    make("rect", { x: 0, y: 0, width: W, height: H, fill: PAPER }, layers.background);
    make("rect", { x: 0, y: 0, width: W, height: H, fill: "url(#dots)" }, layers.background);
    layers.background.appendChild(rc.rectangle(18, 18, W - 36, H - 36, {
      stroke: MUTED, strokeWidth: 1.2, roughness: 1.6, bowing: 2, seed: nextSeed(),
    }));
    layers.background.lastChild.setAttribute("opacity", "0.45");

    // ── text ───────────────────────────────────────────────────────────────
    function text(x, y, value, opts) {
      opts = opts || {};
      const size = opts.size || 18;
      const lines = String(value).split("\n");
      const lineHeight = opts.lineHeight || size * 1.18;
      const node = make("text", {
        x, y,
        fill: opts.color || INK,
        "font-family": opts.font === "title" ? TITLE : HAND,
        "font-size": size,
        "text-anchor": opts.anchor || "start",
        "dominant-baseline": opts.baseline || "alphabetic",
        opacity: opts.opacity == null ? 1 : opts.opacity,
      }, opts.parent || layers.text);
      if (opts.weight) node.setAttribute("font-weight", opts.weight);
      if (opts.rotate) node.setAttribute("transform", `rotate(${opts.rotate} ${x} ${y})`);
      // Centre a multi-line block on y when the caller asks for middle.
      const startY = opts.baseline === "middle" ? y - ((lines.length - 1) * lineHeight) / 2 : y;
      node.setAttribute("dominant-baseline", opts.baseline === "middle" ? "middle" : (opts.baseline || "alphabetic"));
      lines.forEach((line, i) => {
        make("tspan", { x, y: startY + i * lineHeight }, node).textContent = line;
      });
      return node;
    }

    function width(node) {
      try { return node.getComputedTextLength(); } catch (e) { return 0; }
    }

    // ── shapes ─────────────────────────────────────────────────────────────
    function box(x, y, w, h, opts) {
      opts = opts || {};
      const t = tone(opts.tone);
      const radius = opts.radius == null ? 14 : opts.radius;
      const outline = radius > 0 ? roundedRect(x, y, w, h, radius) : null;
      if (opts.shadow !== false) {
        const shadow = outline
          ? rc.path(roundedRect(x + 7, y + 8, w, h, radius), {
              fill: t.stroke, fillStyle: "hachure", hachureGap: 6, hachureAngle: -40,
              fillWeight: 0.9, stroke: "none", roughness: 1.1, seed: nextSeed(),
            })
          : rc.rectangle(x + 7, y + 8, w, h, {
              fill: t.stroke, fillStyle: "hachure", hachureGap: 6, hachureAngle: -40,
              fillWeight: 0.9, stroke: "none", roughness: 1.1, seed: nextSeed(),
            });
        shadow.setAttribute("opacity", "0.22");
        layers.shapes.appendChild(shadow);
      }
      const body = {
        fill: opts.fill || t.fill, fillStyle: opts.fillStyle || "solid",
        stroke: t.stroke, strokeWidth: opts.strokeWidth || 2,
        roughness: opts.roughness == null ? 1.3 : opts.roughness, bowing: 1.1, seed: nextSeed(),
      };
      if (opts.dashed) body.strokeLineDash = [9, 7];
      layers.shapes.appendChild(outline ? rc.path(outline, body) : rc.rectangle(x, y, w, h, body));

      const a = anchors(x, y, w, h);
      if (opts.label) {
        const labelSize = opts.labelSize || 23;
        const hasSub = Boolean(opts.sub);
        const subSize = opts.subSize || 17;
        const subLines = hasSub ? String(opts.sub).split("\n").length : 0;
        const labelLines = String(opts.label).split("\n").length;
        const block = labelLines * labelSize * 1.15 + (hasSub ? 8 + subLines * subSize * 1.2 : 0);
        let top = a.cy - block / 2 + labelSize * 0.86;
        if (opts.align === "top") top = y + labelSize + 12;
        text(opts.align === "left" ? x + 18 : a.cx, top, opts.label, {
          size: labelSize, font: "title", color: t.ink,
          anchor: opts.align === "left" ? "start" : "middle",
        });
        if (hasSub) {
          text(opts.align === "left" ? x + 18 : a.cx, top + labelLines * labelSize * 1.15 - labelSize * 0.15 + 8 + subSize * 0.7, opts.sub, {
            size: subSize, color: MUTED, anchor: opts.align === "left" ? "start" : "middle",
            lineHeight: subSize * 1.2,
          });
        }
      }
      if (opts.badge != null) badge(x + 4, y + 4, opts.badge, { tone: opts.tone });
      return a;
    }

    function cylinder(x, y, w, h, opts) {
      opts = opts || {};
      const t = tone(opts.tone || "blue");
      const ry = Math.max(10, h * 0.13);
      const cx = x + w / 2;
      const side = `M ${x} ${y + ry} L ${x} ${y + h - ry} A ${w / 2} ${ry} 0 0 0 ${x + w} ${y + h - ry} L ${x + w} ${y + ry}`;
      const shadow = rc.path(`M ${x + 7} ${y + ry + 8} L ${x + 7} ${y + h - ry + 8} A ${w / 2} ${ry} 0 0 0 ${x + w + 7} ${y + h - ry + 8} L ${x + w + 7} ${y + ry + 8} Z`, {
        fill: t.stroke, fillStyle: "hachure", hachureGap: 6, hachureAngle: -40, stroke: "none",
        roughness: 1.1, seed: nextSeed(),
      });
      shadow.setAttribute("opacity", "0.22");
      layers.shapes.appendChild(shadow);
      layers.shapes.appendChild(rc.path(side + " Z", {
        fill: t.fill, fillStyle: "solid", stroke: t.stroke, strokeWidth: 2, roughness: 1.2, seed: nextSeed(),
      }));
      layers.shapes.appendChild(rc.ellipse(cx, y + ry, w, ry * 2, {
        fill: "#FFFEFA", fillStyle: "solid", stroke: t.stroke, strokeWidth: 2, roughness: 1.1, seed: nextSeed(),
      }));
      const a = anchors(x, y, w, h);
      if (opts.label) {
        text(cx, y + h / 2 + ry * 0.55, opts.label, { size: opts.labelSize || 21, font: "title", color: t.ink, anchor: "middle" });
      }
      if (opts.sub) {
        text(cx, y + h / 2 + ry * 0.55 + 24, opts.sub, { size: opts.subSize || 16, color: MUTED, anchor: "middle", lineHeight: 19 });
      }
      return a;
    }

    function circle(cx, cy, d, opts) {
      opts = opts || {};
      const t = tone(opts.tone);
      layers.shapes.appendChild(rc.circle(cx, cy, d, {
        fill: t.fill, fillStyle: opts.fillStyle || "solid", stroke: t.stroke, strokeWidth: 2,
        roughness: 1.2, seed: nextSeed(),
      }));
      if (opts.label) {
        text(cx, cy, opts.label, { size: opts.labelSize || 20, font: "title", color: t.ink, anchor: "middle", baseline: "middle" });
      }
      return anchors(cx - d / 2, cy - d / 2, d, d);
    }

    function badge(x, y, value, opts) {
      opts = opts || {};
      const t = tone(opts.tone || "gold");
      const d = opts.size || 34;
      layers.text.appendChild(rc.circle(x, y, d, {
        fill: t.stroke, fillStyle: "solid", stroke: t.stroke, strokeWidth: 1.5, roughness: 1, seed: nextSeed(),
      }));
      text(x, y + 1, String(value), { size: d * 0.58, font: "title", color: "#FFFEFA", anchor: "middle", baseline: "middle" });
    }

    function arrow(from, to, opts) {
      opts = opts || {};
      const color = opts.tone ? tone(opts.tone).stroke : INK;
      const [x1, y1] = from;
      const [x2, y2] = to;
      const mx = (x1 + x2) / 2;
      const my = (y1 + y2) / 2;
      const dx = x2 - x1;
      const dy = y2 - y1;
      const len = Math.hypot(dx, dy) || 1;
      const bend = opts.bend || 0;
      const ctrl = [mx - (dy / len) * bend, my + (dx / len) * bend];
      const style = {
        stroke: color, strokeWidth: opts.width || 2, roughness: opts.roughness == null ? 1.1 : opts.roughness,
        bowing: 1.4, seed: nextSeed(),
      };
      if (opts.dashed) style.strokeLineDash = [9, 7];
      const line = bend ? rc.curve([from, ctrl, to], style) : rc.line(x1, y1, x2, y2, style);
      layers.arrows.appendChild(line);

      function head(tip, tail) {
        const angle = Math.atan2(tip[1] - tail[1], tip[0] - tail[0]);
        const size = opts.headSize || 15;
        for (const side of [-1, 1]) {
          const a = angle + Math.PI + side * 0.42;
          layers.arrows.appendChild(rc.line(tip[0], tip[1], tip[0] + Math.cos(a) * size, tip[1] + Math.sin(a) * size, {
            stroke: color, strokeWidth: opts.width || 2, roughness: 0.8, seed: nextSeed(),
          }));
        }
      }
      if (opts.head !== false) head(to, bend ? ctrl : from);
      if (opts.both) head(from, bend ? ctrl : to);

      if (opts.label) {
        const at = bend ? ctrl : [mx, my];
        const lx = at[0] + (opts.labelDx || 0);
        const ly = at[1] + (opts.labelDy || 0);
        const size = opts.labelSize || 17;
        const node = text(lx, ly, opts.label, {
          size, anchor: "middle", baseline: "middle",
          color: opts.tone ? tone(opts.tone).ink : INK,
        });
        // A paper patch behind the label keeps it legible over the stroke.
        const lines = String(opts.label).split("\n");
        const w = Math.max(...lines.map(() => width(node))) + 16;
        const h = lines.length * size * 1.18 + 6;
        const patch = make("rect", {
          x: lx - w / 2, y: ly - h / 2 - 1, width: w, height: h, rx: 6,
          fill: PAPER, opacity: 0.93,
        });
        layers.text.insertBefore(patch, node);
      }
      return { from, to, mid: bend ? ctrl : [mx, my] };
    }

    // An elbow connector: horizontal then vertical (or the reverse), for tidy
    // routing between rows without crossing boxes.
    function elbow(from, to, opts) {
      opts = opts || {};
      const corner = opts.verticalFirst ? [from[0], to[1]] : [to[0], from[1]];
      const color = opts.tone ? tone(opts.tone).stroke : INK;
      const style = { stroke: color, strokeWidth: opts.width || 2, roughness: 1, seed: nextSeed() };
      if (opts.dashed) style.strokeLineDash = [9, 7];
      layers.arrows.appendChild(rc.linearPath([from, corner], style));
      return arrow(corner, to, Object.assign({}, opts, { label: opts.label }));
    }

    function frame(x, y, w, h, opts) {
      opts = opts || {};
      const t = tone(opts.tone || "muted");
      if (opts.fill !== false) {
        const wash = make("rect", { x, y, width: w, height: h, rx: 18, fill: t.fill, opacity: opts.washOpacity || 0.35 }, layers.frames);
        wash.setAttribute("data-wash", "1");
      }
      layers.frames.appendChild(rc.path(roundedRect(x, y, w, h, 18), {
        stroke: t.stroke, strokeWidth: 1.8, roughness: 1.5, strokeLineDash: [12, 9], seed: nextSeed(),
      }));
      if (opts.label) {
        text(x + 22, y - 12, opts.label, { size: opts.labelSize || 22, font: "title", color: t.ink });
      }
      return anchors(x, y, w, h);
    }

    function note(x, y, value, opts) {
      opts = opts || {};
      const size = opts.size || 18;
      const lines = String(value).split("\n");
      const w = opts.width || 250;
      const h = opts.height || lines.length * size * 1.25 + 30;
      const angle = opts.rotate == null ? -2 : opts.rotate;
      const group = make("g", { transform: `rotate(${angle} ${x + w / 2} ${y + h / 2})` }, layers.text);
      group.appendChild(rc.rectangle(x, y, w, h, {
        fill: opts.fill || "#FBF1CF", fillStyle: "solid", stroke: "#846314", strokeWidth: 1.4,
        roughness: 1.4, seed: nextSeed(),
      }));
      const tape = make("rect", { x: x + w / 2 - 34, y: y - 10, width: 68, height: 20, fill: "#5C665F", opacity: 0.18 }, group);
      tape.setAttribute("transform", `rotate(${-angle * 2} ${x + w / 2} ${y})`);
      text(x + 16, y + 16 + size, value, { size, color: INK, parent: group, lineHeight: size * 1.25 });
      return anchors(x, y, w, h);
    }

    function actor(x, y, opts) {
      opts = opts || {};
      const t = tone(opts.tone || "ink");
      const s = { stroke: t.stroke, strokeWidth: 2.2, roughness: 1, seed: nextSeed() };
      layers.shapes.appendChild(rc.circle(x, y, 34, Object.assign({}, s, { fill: t.fill, fillStyle: "solid" })));
      layers.shapes.appendChild(rc.line(x, y + 17, x, y + 64, s));
      layers.shapes.appendChild(rc.line(x - 26, y + 34, x + 26, y + 34, s));
      layers.shapes.appendChild(rc.line(x, y + 64, x - 20, y + 98, s));
      layers.shapes.appendChild(rc.line(x, y + 64, x + 20, y + 98, s));
      if (opts.label) {
        text(x, y + 126, opts.label, { size: opts.labelSize || 20, font: "title", color: t.ink, anchor: "middle" });
      }
      if (opts.sub) {
        text(x, y + 150, opts.sub, { size: 16, color: MUTED, anchor: "middle", lineHeight: 19 });
      }
      return anchors(x - 30, y - 17, 60, 120);
    }

    function squiggle(x1, y, x2, opts) {
      opts = opts || {};
      const color = opts.tone ? tone(opts.tone).stroke : "#846314";
      const points = [];
      for (let x = x1; x <= x2; x += 18) points.push([x, y + ((x - x1) / 18 % 2 ? 3 : -3)]);
      layers.text.appendChild(rc.curve(points, { stroke: color, strokeWidth: opts.width || 3, roughness: 0.8, seed: nextSeed() }));
    }

    function line(points, opts) {
      opts = opts || {};
      const color = opts.tone ? tone(opts.tone).stroke : INK;
      const style = { stroke: color, strokeWidth: opts.width || 2, roughness: 1.1, seed: nextSeed() };
      if (opts.dashed) style.strokeLineDash = [9, 7];
      layers.arrows.appendChild(rc.linearPath(points, style));
    }

    const api = {
      W, H, tones: TONES, rc,
      text, box, cylinder, circle, badge, arrow, elbow, frame, note, actor, squiggle, line,
      layer: (name) => layers[name],
    };

    // ── title block ────────────────────────────────────────────────────────
    if (config.title) {
      const title = text(60, 82, config.title, { size: config.titleSize || 42, font: "title", color: INK });
      squiggle(60, 98, 60 + Math.max(160, width(title)), { tone: "gold" });
    }
    if (config.subtitle) {
      text(60, 132, config.subtitle, { size: 21, color: MUTED, lineHeight: 25 });
    }
    text(W - 40, H - 34, "✎ vẽ tay bởi Claude · OCR VBHC", {
      size: 15, color: MUTED, anchor: "end", opacity: 0.85,
    });

    return api;
  }

  function draw(config, fn) {
    const board = document.getElementById("board") || document.body.appendChild(document.createElement("div"));
    board.id = "board";
    board.style.width = config.width + "px";
    board.style.height = config.height + "px";
    const fonts = [
      document.fonts.load("42px Pangolin"),
      document.fonts.load("18px 'Patrick Hand'"),
      // Vietnamese diacritics live in a separate unicode-range subset, which
      // only downloads when a glyph from it is requested.
      document.fonts.load("18px 'Patrick Hand'", "Văn bản tiếng Việt ẩn hiện được"),
      document.fonts.load("42px Pangolin", "Văn bản tiếng Việt ẩn hiện được"),
    ];
    Promise.all(fonts).catch(() => null).then(() => document.fonts.ready).then(() => {
      const svg = make("svg", {
        xmlns: NS, width: config.width, height: config.height,
        viewBox: `0 0 ${config.width} ${config.height}`,
      }, board);
      const api = build(config, svg);
      fn(api);
      document.body.dataset.ready = "1";
    });
  }

  global.Sketch = { draw, tones: TONES };
})(window);
