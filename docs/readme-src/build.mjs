// Builds the README diagrams and charts as standalone SVGs.
//
//   node docs/readme-src/build.mjs            (all visuals)
//   node docs/readme-src/build.mjs chat       (names containing "chat")
//
// Text is set in Be Vietnam Pro as outlines (glyphs.py extracts them from
// assets/fonts with fontTools; PYTHON defaults to the portable runtime), so
// the SVGs need no font on the viewer's machine and load nothing external.
// Output: docs/media/diagrams/*.svg and docs/media/charts/*.svg, one file per
// visual x language (vi, en) x theme (light, dark).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { THEMES, setIconDir, textOverlaps } from "./lib.mjs";
import { architecture } from "./architecture.mjs";
import { models } from "./models.mjs";
import { chat } from "./chat.mjs";
import { hardware } from "./hardware.mjs";
import { multiturn, capabilities, ocrQuality, ragSingle } from "./charts.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");
const OUT = {
  diagrams: path.join(ROOT, "docs", "media", "diagrams"),
  charts: path.join(ROOT, "docs", "media", "charts"),
};
setIconDir(path.join(ROOT, "icons", "fluent"));

function loadGlyphs() {
  const cache = path.join(os.tmpdir(), "ocr-vbhc-readme-glyphs.json");
  const script = path.join(HERE, "glyphs.py");
  const stale = !fs.existsSync(cache) || fs.statSync(cache).mtimeMs < fs.statSync(script).mtimeMs;
  if (stale) {
    const python = process.env.PYTHON || path.join(ROOT, "runtime", "python.exe");
    execFileSync(python, ["-I", "-B", script, path.join(ROOT, "assets", "fonts"), cache], { stdio: "inherit" });
  }
  return JSON.parse(fs.readFileSync(cache, "utf8"));
}

const VISUALS = [
  ["diagrams", "architecture", architecture],
  ["diagrams", "models", models],
  ["diagrams", "chat", chat],
  ["diagrams", "hardware", hardware],
  ["charts", "multiturn", multiturn],
  ["charts", "capabilities", capabilities],
  ["charts", "ocr_quality", ocrQuality],
  ["charts", "rag_single_turn", ragSingle],
];

const filter = process.argv[2] || "";
const glyphs = loadGlyphs();
let failed = false;
for (const [kind, name, make] of VISUALS) {
  if (filter && !name.includes(filter)) continue;
  for (const lang of ["vi", "en"]) {
    for (const themeName of ["light", "dark"]) {
      const { svg, canvas } = make(glyphs, THEMES[themeName], lang);
      const file = path.join(OUT[kind], `${name}_${lang}_${themeName}.svg`);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, svg, "utf8");
      const kb = Buffer.byteLength(svg) / 1024;
      const overlaps = textOverlaps(canvas);
      const issues = canvas.problems.concat(overlaps.map((o) => "overlap: " + o));
      console.log(`${kb.toFixed(1).padStart(6)} KB  ${path.relative(ROOT, file)}${issues.length ? "  (" + issues.length + " issues)" : ""}`);
      if (themeName === "light") for (const issue of [...new Set(issues)]) console.log("         ! " + issue);
      if (kb > 200) {
        console.log("         ! over 200 KB");
        failed = true;
      }
    }
  }
}
process.exitCode = failed ? 1 : 0;
