// Measured results as charts (dataviz rules: honest zero-based scales,
// direct labels, thin marks, one olive ramp validated for both themes).
import { Canvas, finish, rect, baseStyle, num } from "./lib.mjs";

const CAPTION = {
  vi: "Đo trên máy của nhà phát triển (RTX 5090 Laptop 24 GB, Qwen3-8B), bộ kiểm thử cục bộ.",
  en: "Measured on the developer's machine (RTX 5090 Laptop 24 GB, Qwen3-8B), local test sets.",
};

const W = 1000;

function frame(c, title, sub, lang) {
  const t = c.t;
  let b = rect(0, 0, c.W, c.H, { rx: 16, fill: t.bg });
  b += c.text(title, 40, 50, { size: 23, weight: 600 });
  b += c.para(sub, 40, 78, c.W - 80, { size: 14.5, fill: t.ink2 }).svg;
  b += c.text(CAPTION[lang], 40, c.H - 24, { size: 12.5, fill: t.muted });
  return b;
}

// Horizontal bar, square at the baseline, 4px rounded data end.
function hbar(x, y, w, h, fill, opacity) {
  if (w <= 0) return "";
  const r = Math.min(4, w);
  return `<path d="M${x} ${y}h${(w - r).toFixed(1)}a${r} ${r} 0 0 1 ${r} ${r}v${h - 2 * r}a${r} ${r} 0 0 1-${r} ${r}h-${(w - r).toFixed(1)}z" fill="${fill}"${opacity != null ? ` opacity="${opacity}"` : ""}/>`;
}

const pct = (a, b, lang) => num((100 * a) / b, lang, 0) + "%";

// ── (a) Multi-turn accuracy across stages ─────────────────────────────
export function multiturn(glyphs, theme, lang) {
  const vi = lang === "vi";
  const c = new Canvas(glyphs, theme, lang, W, 420);
  const t = theme;
  let b = frame(c,
    vi ? "Hội thoại nhiều lượt: từ 134 lên 187 trên 199 lượt đúng" : "Multi-turn conversations: from 134 to 187 of 199 turns right",
    vi ? "52 hội thoại, 199 lượt hỏi có đáp án biết trước. Mỗi bước giữ lại các bản sửa của bước trước."
      : "52 conversations, 199 turns with known answers. Each step keeps the fixes before it.", lang);

  const panel = (x0, x1, title, total, rows) => {
    let s = c.text(title, x0, 134, { size: 15, weight: 600 });
    rows.forEach(([label, value, step], i) => {
      const y = 166 + i * 64;
      s += c.text(label, x0, y, { size: 14, fill: t.ink2 });
      const valueText = num(value, lang) + (vi ? " trên " : " of ") + total;
      s += c.text(pct(value, total, lang), x1, y, { size: 14, weight: 600, anchor: "end" });
      s += c.text(valueText, x1 - c.width(pct(value, total, lang), 14, 600) - 10, y, { size: 13.5, fill: t.muted, anchor: "end" });
      s += rect(x0, y + 10, x1 - x0, 22, { rx: 4, fill: t.track });
      s += hbar(x0, y + 10, ((x1 - x0) * value) / total, 22, t.ramp[step]);
    });
    s += c.text("0", x0, 166 + rows.length * 64 - 6, { size: 12, fill: t.muted });
    s += c.text(String(total), x1, 166 + rows.length * 64 - 6, { size: 12, fill: t.muted, anchor: "end" });
    return s;
  };
  b += panel(40, 580, vi ? "Lượt trả lời đúng" : "Turns answered right", 199, [
    [vi ? "Ban đầu" : "Baseline", 134, 0],
    [vi ? "Sửa bộ nhớ hội thoại" : "Conversation memory fixed", 182, 1],
    [vi ? "Thêm kiểm tra câu trả lời" : "Answer check added", 187, 2],
  ]);
  b += panel(640, 960, vi ? "Hội thoại đúng trọn vẹn" : "Conversations fully right", 52, [
    [vi ? "Ban đầu" : "Baseline", 16, 0],
    [vi ? "Hiện tại" : "Now", 40, 2],
  ]);
  const desc = vi
    ? "Lượt trả lời đúng trên 199: ban đầu 134 (67%), sau khi sửa bộ nhớ hội thoại 182 (91%), thêm kiểm tra câu trả lời 187 (94%). Hội thoại đúng trọn vẹn trên 52: ban đầu 16, hiện tại 40."
    : "Turns answered right out of 199: baseline 134 (67%), conversation memory fixed 182 (91%), answer check added 187 (94%). Conversations fully right out of 52: baseline 16, now 40.";
  return { svg: finish(c, b, { title: vi ? "Hội thoại nhiều lượt" : "Multi-turn conversations", desc, style: baseStyle(t) }), canvas: c };
}

// ── (b) Specific capabilities, before → after (dumbbells) ─────────────
export function capabilities(glyphs, theme, lang) {
  const vi = lang === "vi";
  const c = new Canvas(glyphs, theme, lang, W, 470);
  const t = theme;
  let b = frame(c,
    vi ? "Những chỗ từng hỏng, trước và sau khi sửa" : "Where it used to fail, before and after the fixes",
    vi ? "Mỗi hàng là một nhóm lượt hỏi trong bộ kiểm thử. Bên trái tính theo tỷ lệ đúng, bên phải đếm số lỗi."
      : "Each row is a group of test turns. Left: share answered right. Right: number of errors.", lang);
  // Legend (two states of one measure)
  let lx = 40;
  for (const [label, col] of [[vi ? "trước" : "before", t.ramp[0]], [vi ? "sau" : "after", t.ramp[2]]]) {
    b += `<circle cx="${lx + 6}" cy="${117}" r="6" fill="${col}"/>`;
    b += c.text(label, lx + 18, 122, { size: 13.5, fill: t.ink2 });
    lx += 18 + c.width(label, 13.5) + 22;
  }

  const dumbbell = (x0, x1, y, before, after, max, bl, al) => {
    const X = (v) => x0 + ((x1 - x0) * v) / max;
    const xb = X(before), xa = X(after);
    let s = `<path d="M${xb} ${y}H${xa}" stroke="${t.line}" stroke-width="2"/>`;
    s += `<circle cx="${xb}" cy="${y}" r="7" fill="${t.ramp[0]}" stroke="${t.bg}" stroke-width="2"/>`;
    s += `<circle cx="${xa}" cy="${y}" r="7" fill="${t.ramp[2]}" stroke="${t.bg}" stroke-width="2"/>`;
    const up = after >= before;
    s += c.text(bl, up ? xb - 13 : xb + 13, y + 4.8, { size: 13, fill: t.muted, anchor: up ? "end" : "start" });
    s += c.text(al, up ? xa + 13 : xa - 13, y + 4.8, { size: 13.5, weight: 600, anchor: up ? "start" : "end" });
    return s;
  };
  const grid = (x0, x1, y0, y1, ticks, max, fmt) => {
    let s = "";
    for (const v of ticks) {
      const x = x0 + ((x1 - x0) * v) / max;
      s += `<path d="M${x} ${y0}V${y1}" stroke="${t.grid}"/>`;
      s += c.text(fmt(v), x, y1 + 18, { size: 12, fill: t.muted, anchor: "middle" });
    }
    return s;
  };

  // Left: share answered right
  const L0 = 90, L1 = 530, top = 150;
  b += c.text(vi ? "Tỷ lệ đúng" : "Share answered right", 40, top, { size: 15, weight: 600 });
  const left = [
    [vi ? "So sánh văn bản" : "Comparing documents", 5, 18, 22],
    [vi ? "Nhớ văn bản của lượt 1, từ lượt 4 trở đi" : "Recalling turn 1's document at turn 4 or later", 3, 16, 16],
    [vi ? "Bộ dữ liệu thật: 24 hội thoại, 67 lượt" : "Real-data set: 24 conversations, 67 turns", 41, 65, 67],
    [vi ? "Phiên hỏi thật của nhà phát triển" : "The developer's own failing session", 0, 5, 5],
  ];
  b += grid(L0, L1, top + 16, top + 16 + left.length * 58, [0, 25, 50, 75, 100], 100, (v) => v + "%");
  left.forEach(([label, before, after, total], i) => {
    const y = top + 36 + i * 58;
    b += rect(40, y - 14, L1 + 70 - 40, 18, { fill: t.bg });
    b += c.text(label, 40, y, { size: 13.5, fill: t.ink2 });
    b += dumbbell(L0, L1, y + 22, (100 * before) / total, (100 * after) / total, 100, before + "/" + total, after + "/" + total);
  });

  // Right: errors, fewer is better
  const R0 = 640, R1 = 940;
  b += c.text(vi ? "Số lỗi, càng ít càng tốt" : "Errors, fewer is better", R0 - 20, top, { size: 15, weight: 600 });
  const right = [
    [vi ? "Câu trả lời có số, ngày, con số không có\ntrong nguồn, sai loại văn bản hoặc chép nhãn nguồn"
      : "Answers with an unsupported number, date or\nfigure, a wrong type or copied source labels", 17, 0],
    [vi ? "Báo “không tìm thấy” sai" : "False “not found”", 25, 8],
  ];
  b += grid(R0, R1, top + 16, top + 16 + 2 * 87, [0, 5, 10, 15, 20, 25], 25, (v) => String(v));
  right.forEach(([label, before, after], i) => {
    const y = top + 36 + i * 87;
    const p = c.para(label, R0 - 20, y, R1 + 40 - (R0 - 20), { size: 13.5, fill: t.ink2, lh: 1.3 });
    b += rect(R0 - 20, y - 14, R1 + 40 - (R0 - 20), p.lines.length * 17.6 + 2, { fill: t.bg });
    b += p.svg;
    b += dumbbell(R0, R1, y + (p.lines.length - 1) * 17.6 + 24, before, after, 25, String(before), String(after));
  });
  const desc = vi
    ? "Trước và sau: so sánh văn bản 5/22 lên 18/22; nhớ văn bản của lượt 1 từ lượt 4 trở đi 3/16 lên 16/16; bộ dữ liệu thật 41/67 lên 65/67; phiên hỏi thật của nhà phát triển 0/5 lên 5/5. Số lỗi: câu trả lời có nội dung không có trong nguồn 17 xuống 0; báo không tìm thấy sai 25 xuống 8."
    : "Before and after: comparing documents 5/22 to 18/22; recalling turn 1's document at turn 4 or later 3/16 to 16/16; real-data set 41/67 to 65/67; the developer's own failing session 0/5 to 5/5. Errors: answers with unsupported content 17 to 0; false not found 25 to 8.";
  return { svg: finish(c, b, { title: vi ? "Trước và sau khi sửa" : "Before and after the fixes", desc, style: baseStyle(t) }), canvas: c };
}

// ── (c) OCR quality: four stat tiles with honest mini scales ─────────
export function ocrQuality(glyphs, theme, lang) {
  const vi = lang === "vi";
  const c = new Canvas(glyphs, theme, lang, W, 360);
  const t = theme;
  let b = frame(c,
    vi ? "Chất lượng OCR sau các bản sửa tháng 10/2026" : "OCR quality after the October 2026 fixes",
    vi ? "50 trang chuẩn với 165 trường NĐ30; lỗi ký tự đo trên 7 trang có bản gõ tham chiếu."
      : "50 benchmark pages with 165 NĐ30 fields; character errors measured on 7 pages with a typed reference.", lang);
  const TW = (W - 80 - 3 * 16) / 4;
  const TY = 112, TH = 196;
  const tiles = [
    { label: vi ? "Trường NĐ30 tách đúng" : "NĐ30 fields right", value: num(80.6, lang, 1) + "%",
      note: vi ? "133 trên 165 trường" : "133 of 165 fields", meter: 133 / 165 },
    { label: vi ? "Tỷ lệ lỗi ký tự (CER)" : "Character error rate", value: num(0.74, lang, 2) + "%",
      note: (vi ? "trước: " : "before: ") + num(1.65, lang, 2) + "%", pair: [1.65, 0.74], max: 2,
      ticks: ["0", num(2, lang) + "%"], lower: true },
    { label: vi ? "Trang bị cắt cụt" : "Truncated pages", value: "0",
      note: vi ? "trước: 1 trên 50 trang" : "before: 1 of 50 pages", pair: [1, 0], max: 1, ticks: ["0", "1"], lower: true },
    { label: vi ? "RAM của máy chủ OCR" : "OCR server RAM", value: num(6.2, lang, 1) + " GB",
      note: (vi ? "trước: " : "before: ") + num(15.5, lang, 1) + " GB", pair: [15.5, 6.2], max: 16,
      ticks: ["0", "16 GB"], lower: true },
  ];
  tiles.forEach((tile, i) => {
    const x = 40 + i * (TW + 16);
    b += rect(x, TY, TW, TH, { rx: 12, fill: t.surface, stroke: t.border });
    const lab = c.para(tile.label, x + 18, TY + 30, TW - 36, { size: 14, weight: 500, fill: t.ink2 });
    if (lab.lines.length > 1) c.problems.push("tile label wraps: " + tile.label);
    b += lab.svg;
    b += c.text(tile.value, x + 18, TY + 86, { size: 34, weight: 600 });
    b += c.text(tile.note, x + 18, TY + 110, { size: 13.5, fill: t.muted });
    const bx = x + 18, bw = TW - 36;
    if (tile.meter != null) {
      b += rect(bx, TY + 140, bw, 14, { rx: 4, fill: t.track });
      b += hbar(bx, TY + 140, bw * tile.meter, 14, t.ramp[2]);
      b += c.text("0", bx, TY + 174, { size: 12, fill: t.muted });
      b += c.text("165", bx + bw, TY + 174, { size: 12, fill: t.muted, anchor: "end" });
    } else {
      const [before, after] = tile.pair;
      const lab = vi ? ["trước", "sau"] : ["before", "after"];
      const lw = Math.max(c.width(lab[0], 12), c.width(lab[1], 12)) + 8;
      [[before, t.ramp[0], lab[0]], [after, t.ramp[2], lab[1]]].forEach(([v, col, l], k) => {
        const y = TY + 128 + k * 20;
        b += c.text(l, bx, y + 11, { size: 12, fill: t.muted });
        b += rect(bx + lw, y, bw - lw, 14, { rx: 4, fill: t.track });
        b += hbar(bx + lw, y, ((bw - lw) * v) / tile.max, 14, col);
      });
      b += c.text(tile.ticks[0], bx + lw, TY + 184, { size: 12, fill: t.muted });
      b += c.text(tile.ticks[1], bx + bw, TY + 184, { size: 12, fill: t.muted, anchor: "end" });
    }
  });
  const desc = vi
    ? "Trường NĐ30 tách đúng 133 trên 165 (80,6%). Tỷ lệ lỗi ký tự 0,74%, trước là 1,65%. Trang bị cắt cụt 0, trước là 1. RAM của máy chủ OCR 6,2 GB, trước là 15,5 GB."
    : "NĐ30 fields extracted right: 133 of 165 (80.6%). Character error rate 0.74%, before 1.65%. Truncated pages 0, before 1. OCR server RAM 6.2 GB, before 15.5 GB.";
  return { svg: finish(c, b, { title: vi ? "Chất lượng OCR" : "OCR quality", desc, style: baseStyle(t) }), canvas: c };
}

// ── (d) Single-turn RAG ───────────────────────────────────────────────
export function ragSingle(glyphs, theme, lang) {
  const vi = lang === "vi";
  const c = new Canvas(glyphs, theme, lang, W, 330);
  const t = theme;
  let b = frame(c,
    vi ? "Hỏi một lượt: từ 13 lên 36–38 trên 41 câu đúng" : "Single questions: from 13 to 36–38 of 41 right",
    vi ? "41 câu hỏi có đáp án biết trước, trước và sau các bản sửa truy xuất; kết quả dao động ±1 giữa các lần chạy."
      : "41 questions with known answers, before and after the retrieval fixes; results vary by ±1 between runs.", lang);
  const x0 = 40, x1 = 600, total = 41;
  const X = (v) => x0 + ((x1 - x0) * v) / total;
  const rows = [
    [vi ? "Trước các bản sửa" : "Before the fixes", 13, null, 0],
    [vi ? "Sau các bản sửa" : "After the fixes", 36, 38, 2],
  ];
  rows.forEach(([label, lo, hi, step], i) => {
    const y = 136 + i * 66;
    b += c.text(label, x0, y, { size: 14, fill: t.ink2 });
    const value = hi ? lo + "–" + hi : String(lo);
    b += c.text(value + (vi ? " trên " : " of ") + total, x1, y, { size: 14, weight: 600, anchor: "end" });
    b += rect(x0, y + 10, x1 - x0, 22, { rx: 4, fill: t.track });
    if (hi) {
      b += hbar(x0, y + 10, X(hi) - x0, 22, t.ramp[1]);
      b += `<rect x="${X(lo)}" y="${y + 10}" width="2" height="22" fill="${t.bg}"/>`;
      b += rect(x0, y + 10, X(lo) - x0, 22, { fill: t.ramp[step] });
    } else {
      b += hbar(x0, y + 10, X(lo) - x0, 22, t.ramp[step]);
    }
  });
  b += c.text("0", x0, 278, { size: 12, fill: t.muted });
  b += c.text("41", x1, 278, { size: 12, fill: t.muted, anchor: "end" });
  b += rect(X(30), 268, 14, 12, { rx: 3, fill: t.ramp[1] });
  b += c.text(vi ? "khoảng dao động" : "run-to-run range", X(30) + 20, 278, { size: 12.5, fill: t.muted });

  // Two side facts as small stat lines
  const sx = 660;
  const facts = vi
    ? [["5/5", "câu chào hỏi được trả lời mà không gắn nguồn"], ["0", "nguồn gắn vào câu hỏi kiến thức chung"]]
    : [["5/5", "greetings answered without attaching sources"], ["0", "sources attached to general-knowledge questions"]];
  facts.forEach(([value, label], i) => {
    const y = 140 + i * 66;
    b += c.text(value, sx, y + 12, { size: 28, weight: 600 });
    b += c.para(label, sx + 66, y - 2, 960 - sx - 66, { size: 13.5, fill: t.ink2, lh: 1.35 }).svg;
  });
  const desc = vi
    ? "Hỏi một lượt trên 41 câu có đáp án: trước các bản sửa 13 câu đúng, sau các bản sửa 36 đến 38 câu. Câu chào hỏi 5/5 được trả lời không gắn nguồn; câu hỏi kiến thức chung không bị gắn nguồn nào."
    : "Single questions out of 41 with known answers: 13 right before the fixes, 36 to 38 after. Greetings 5/5 answered without sources; general-knowledge questions get no sources.";
  return { svg: finish(c, b, { title: vi ? "Hỏi một lượt" : "Single questions", desc, style: baseStyle(t) }), canvas: c };
}
