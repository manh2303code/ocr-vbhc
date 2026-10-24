// Renders the built SVGs to PNG with Edge headless, for review.
//
//   node docs/readme-src/render.mjs [out_dir] [name filter]
//
// Each SVG opens as its own document at its intrinsic size (the window is
// sized to the SVG), in a throwaway Edge profile under <out_dir>. The PNGs
// are for looking at only (default out_dir: the system temp folder); the
// README uses the SVGs.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");
const EDGE = process.env.EDGE || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const outDir = path.resolve(process.argv[2] || path.join(os.tmpdir(), "ocr-vbhc-readme-preview"));
const filter = process.argv[3] || "";
const scale = Number(process.env.SCALE || 1);
fs.mkdirSync(outDir, { recursive: true });
const profile = path.join(outDir, ".edge-profile");

for (const sub of ["diagrams", "charts"]) {
  const dir = path.join(ROOT, "docs", "media", sub);
  if (!fs.existsSync(dir)) continue;
  for (const name of fs.readdirSync(dir).filter((f) => f.endsWith(".svg") && f.includes(filter)).sort()) {
    const file = path.join(dir, name);
    const head = fs.readFileSync(file, "utf8").slice(0, 300);
    const w = Number(/width="(\d+)"/.exec(head)[1]);
    const h = Number(/height="(\d+)"/.exec(head)[1]);
    const png = path.join(outDir, name.replace(/\.svg$/, ".png"));
    execFileSync(EDGE, [
      "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
      "--user-data-dir=" + profile,
      "--force-device-scale-factor=" + scale,
      "--window-size=" + w + "," + h,
      "--screenshot=" + png,
      pathToFileURL(file).href,
    ], { stdio: "ignore", timeout: 60000 });
    console.log(path.relative(ROOT, png) + "  " + w + "x" + h);
  }
}
