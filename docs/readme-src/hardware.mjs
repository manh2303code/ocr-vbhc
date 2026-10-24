// Recommended configuration: three tiers and what each one gets.
import { Canvas, finish, rect, baseStyle } from "./lib.mjs";

const S = {
  vi: {
    title: "Cấu hình đề xuất",
    sub: "Ứng dụng tự chọn mô hình và số luồng OCR theo phần cứng; máy chỉ có CPU vẫn chạy được, chỉ chậm hơn.",
    tiers: [
      { name: "Tối thiểu", note: "chỉ CPU" },
      { name: "Đề xuất", note: "GPU NVIDIA tầm trung" },
      { name: "Tốt nhất", note: "GPU NVIDIA nhiều VRAM" },
    ],
    machine: "Máy", gets: "Bạn nhận được",
    rows: [
      ["Hệ điều hành", ["Windows 10/11 x64", "Windows 11 x64", "Windows 11 x64"]],
      ["GPU", ["không cần", "NVIDIA từ 8 GB", "NVIDIA 16–24 GB"]],
      ["RAM", ["16 GB", "32 GB", "32–64 GB"]],
      ["Ổ đĩa", ["≈25 GB trống", "SSD", "NVMe SSD"]],
    ],
    getRows: [
      ["Trợ lý", ["Qwen3-4B trên CPU", "Qwen3-4B trên GPU", "Qwen3-8B trên GPU"]],
      ["Ngữ cảnh", ["8k, 4 vòng công cụ", "16k, 8 vòng công cụ", "16k, 8 vòng công cụ"]],
      ["Luồng OCR", ["1", "1", "2"]],
    ],
    speed: "Tốc độ OCR", unit: "s/trang", speedNote: "ngắn hơn là nhanh hơn",
    foot: [
      "Mọi mức cần ≈25 GB trống cho mô hình và runtime. GPU 10–15 GB đã dùng Qwen3-8B, với 1 luồng OCR.",
      "Tốc độ: ≈7,8 s/trang đo trên RTX 5090 Laptop 24 GB (2 luồng, GPU rảnh); ≈20 s/trang đo trên RTX 4070 8 GB (1 luồng); ≈90 s/trang khi chỉ dùng CPU.",
    ],
  },
  en: {
    title: "Recommended configuration",
    sub: "The app picks its models and OCR workers from the hardware; a CPU-only machine still works, only slower.",
    tiers: [
      { name: "Minimum", note: "CPU only" },
      { name: "Recommended", note: "a mid-range NVIDIA GPU" },
      { name: "Best", note: "an NVIDIA GPU with more VRAM" },
    ],
    machine: "Machine", gets: "What you get",
    rows: [
      ["Operating system", ["Windows 10/11 x64", "Windows 11 x64", "Windows 11 x64"]],
      ["GPU", ["not needed", "NVIDIA, 8 GB or more", "NVIDIA 16–24 GB"]],
      ["RAM", ["16 GB", "32 GB", "32–64 GB"]],
      ["Disk", ["≈25 GB free", "SSD", "NVMe SSD"]],
    ],
    getRows: [
      ["Assistant", ["Qwen3-4B on CPU", "Qwen3-4B on GPU", "Qwen3-8B on GPU"]],
      ["Context", ["8k, 4 tool rounds", "16k, 8 tool rounds", "16k, 8 tool rounds"]],
      ["OCR workers", ["1", "1", "2"]],
    ],
    speed: "OCR speed", unit: "s/page", speedNote: "shorter is faster",
    foot: [
      "Every tier needs ≈25 GB free for models and runtime. A 10–15 GB GPU already runs Qwen3-8B, with 1 OCR worker.",
      "Speed: ≈7.8 s/page measured on an RTX 5090 Laptop 24 GB (2 workers, idle GPU); ≈20 s/page on an RTX 4070 8 GB (1 worker); ≈90 s/page on CPU only.",
    ],
  },
};

const W = 1180;
const H = 760;
const SPEEDS = [90, 20, 7.8];

export function hardware(glyphs, theme, lang) {
  const s = S[lang];
  const t = theme;
  const c = new Canvas(glyphs, theme, lang, W, H);
  let b = rect(0, 0, W, H, { rx: 16, fill: t.bg });
  b += c.text(s.title, 40, 54, { size: 27, weight: 600 });
  b += c.text(s.sub, 40, 84, { size: 15, fill: t.ink2 });

  const LX = 40, TX = 256, TW = 284, GAP = 16;
  const tierX = (i) => TX + i * (TW + GAP);
  const TOP = 120, HEAD = 92, RH = 44;
  const machineY = TOP + HEAD + 8;
  const getsY = machineY + 30 + s.rows.length * RH + 18;
  const speedY = getsY + 30 + s.getRows.length * RH;
  const bottom = speedY + 62;

  // Tier columns as cards; the recommended one carries the olive edge.
  s.tiers.forEach((tier, i) => {
    const x = tierX(i);
    const rec = i === 1;
    b += rect(x, TOP, TW, bottom - TOP, { rx: 14, fill: t.surface, stroke: rec ? t.oliveText : t.border, sw: rec ? 2 : 1 });
    for (let k = 0; k < 3; k++) b += rect(x + 20 + k * 14, TOP + 22, 10, 10, { rx: 2.5, fill: k <= i ? t.ramp[2] : "none", stroke: k <= i ? "none" : t.line });
    b += c.text(tier.name, x + 20, TOP + 60, { size: 19, weight: 600 });
    b += c.text(tier.note, x + 20, TOP + 80, { size: 13.5, fill: t.muted });
  });

  const section = (label, y) => c.text(label, LX, y, { size: 13.5, weight: 600, fill: t.oliveText });
  const rowsAt = (rows, y0) => {
    let out = "";
    rows.forEach(([label, values], r) => {
      const y = y0 + r * RH;
      out += `<path d="M${LX} ${y}H${tierX(2) + TW}" stroke="${t.border}"/>`;
      out += c.text(label, LX, y + 28, { size: 14, weight: 500, fill: t.ink2 });
      values.forEach((v, i) => {
        const big = /^\d$/.test(v);
        out += c.text(v, tierX(i) + 20, y + 28, { size: big ? 16 : 14.5, weight: big || r === 0 && rows === s.getRows ? 600 : 400, fill: t.ink });
        if (c.width(v, 14.5) > TW - 40) c.problems.push("cell too wide: " + v);
      });
    });
    return out;
  };
  b += section(s.machine, machineY + 18);
  b += rowsAt(s.rows, machineY + 30);
  b += section(s.gets, getsY + 18);
  b += rowsAt(s.getRows, getsY + 30);

  // OCR speed: one shared scale across tiers, so the bars compare honestly.
  b += `<path d="M${LX} ${speedY}H${tierX(2) + TW}" stroke="${t.border}"/>`;
  b += c.text(s.speed, LX, speedY + 30, { size: 14, weight: 500, fill: t.ink2 });
  b += c.text(s.speedNote, LX, speedY + 48, { size: 12.5, fill: t.muted });
  const maxBar = TW - 40 - c.width("≈90 " + s.unit, 13.5, 600) - 10;
  const pxPerS = maxBar / 90;
  SPEEDS.forEach((v, i) => {
    const x = tierX(i) + 20;
    const w = Math.max(3, v * pxPerS);
    b += `<path d="M${x} ${speedY + 16}h${w - 4}a4 4 0 0 1 4 4v12a4 4 0 0 1-4 4h-${w - 4}z" fill="${t.ramp[2]}"/>`;
    const label = "≈" + (lang === "vi" ? String(v).replace(".", ",") : String(v)) + " " + s.unit;
    b += c.text(label, x + w + 8, speedY + 31, { size: 13.5, weight: 600, fill: t.ink });
  });

  s.foot.forEach((line, i) => {
    b += c.para(line, LX, bottom + 32 + i * 22, W - 80, { size: 13, fill: t.muted }).svg;
  });

  const desc = lang === "vi"
    ? "Ba mức cấu hình. Tối thiểu: Windows 10/11 x64, không cần GPU, RAM 16 GB, khoảng 25 GB trống; trợ lý Qwen3-4B trên CPU ngữ cảnh 8k, 1 luồng OCR, khoảng 90 giây mỗi trang. Đề xuất: Windows 11, GPU NVIDIA từ 8 GB, RAM 32 GB, SSD; Qwen3-4B trên GPU ngữ cảnh 16k, 1 luồng OCR, khoảng 20 giây mỗi trang. Tốt nhất: Windows 11, GPU NVIDIA 16 đến 24 GB, RAM 32 đến 64 GB, NVMe SSD; Qwen3-8B trên GPU ngữ cảnh 16k, 2 luồng OCR, khoảng 7,8 giây mỗi trang."
    : "Three tiers. Minimum: Windows 10/11 x64, no GPU, 16 GB RAM, about 25 GB free; Qwen3-4B on CPU with an 8k context, 1 OCR worker, about 90 seconds per page. Recommended: Windows 11, an NVIDIA GPU with 8 GB or more, 32 GB RAM, SSD; Qwen3-4B on GPU with a 16k context, 1 OCR worker, about 20 seconds per page. Best: Windows 11, an NVIDIA GPU with 16 to 24 GB, 32 to 64 GB RAM, NVMe SSD; Qwen3-8B on GPU with a 16k context, 2 OCR workers, about 7.8 seconds per page.";
  return { svg: finish(c, b, { title: s.title, desc, style: baseStyle(t) }), canvas: c };
}
