// The whole system: digitise → store → ask, inside the machine boundary.
import { Canvas, edge, finish, node, rect, baseStyle, kindColors } from "./lib.mjs";

const S = {
  vi: {
    title: "Toàn bộ hệ thống chạy trên máy của bạn",
    sub: "Từ bản quét đến câu trả lời có trích dẫn: mọi mô hình chạy cục bộ, không có lệnh gọi mạng nào khi chạy.",
    machine: "Máy của bạn",
    offline: "Không có dữ liệu nào rời khỏi máy",
    band1: "Số hóa", band2: "Lưu trữ và chỉ mục", band2b: "trong một tệp SQLite trên máy", band3: "Hỏi đáp",
    input: "PDF hoặc ảnh", inputSub: "một tệp, nhiều tệp hoặc cả thư mục",
    render: "Dựng trang", renderSub: ["150 dpi, trang ngang 200 dpi"],
    chandra: "Chandra OCR 2", chandraSub: [{ text: "llama-server :9999", weight: 500, fill: "ink2" }, "HTML bố cục của từng trang"],
    gate: "Kiểm tra trang", gateSub: ["cắt cụt, lặp vòng, lẫn suy luận", { text: "thử lại một lần, sau đó đánh dấu", weight: 500, fill: "ink2" }],
    parser: "Tách 12 trường NĐ30", parserSub: ["số ký hiệu, ngày, loại, cơ quan, trích yếu, người ký…"],
    bus: "chia đoạn, nhúng bge-m3",
    cols: [
      ["Văn bản", "mỗi lần sửa là một phiên bản"],
      ["FTS5 toàn văn", "tìm theo từ, giữ dấu"],
      ["FTS5 không dấu", "gõ không dấu vẫn tìm ra"],
      ["sqlite-vec", "vector bge-m3, 1024 chiều"],
      ["Quan hệ pháp lý", "căn cứ, thay thế, sửa đổi, bãi bỏ"],
      ["Hội thoại", "ngữ cảnh của từng lượt"],
    ],
    question: "Câu hỏi", questionSub: ["có dấu hoặc không dấu"],
    retrieval: "Truy xuất lai", retrievalSub: ["toàn văn, vector, không dấu", "gộp bằng RRF"],
    reranker: "ViRanker", rerankerSub: ["chấm lại đoạn tốt nhất, bỏ đoạn lạc đề"],
    agent: "Trợ lý Qwen3", agentPort: "llama-server :9998",
    chips: ["trạng thái hội thoại", "phân giải văn bản", "bảng thống kê", "công cụ tra cứu"],
    checkChip: "kiểm tra câu trả lời",
    answer: "Câu trả lời", answerSub: "nguồn mở đúng trang PDF",
    tools: "công cụ, bảng thống kê", save: "lưu mỗi lượt",
    legendModel: "Mô hình chạy cục bộ", legendCode: "Mã của ứng dụng", legendCheck: "Bước kiểm tra",
    credit: "OCR VBHC 2.0, phát triển bởi Trần Hậu Mạnh",
  },
  en: {
    title: "The whole system runs on your machine",
    sub: "From a scan to a cited answer: every model runs locally, with no network call at runtime.",
    machine: "Your machine",
    offline: "No data leaves the machine",
    band1: "Digitise", band2: "Store and index", band2b: "in one SQLite file on disk", band3: "Ask",
    input: "PDF or image", inputSub: "one file, many files or a whole folder",
    render: "Render pages", renderSub: ["150 dpi, landscape pages at 200 dpi"],
    chandra: "Chandra OCR 2", chandraSub: [{ text: "llama-server :9999", weight: 500, fill: "ink2" }, "layout HTML for each page"],
    gate: "Page quality gate", gateSub: ["truncated, looping or reasoning prose", { text: "retried once, then flagged", weight: 500, fill: "ink2" }],
    parser: "12 NĐ30 fields", parserSub: ["number, date, type, issuer, summary, signer…"],
    bus: "chunk, embed with bge-m3",
    cols: [
      ["Documents", "every edit is a new version"],
      ["FTS5 full text", "word search, with diacritics"],
      ["Folded FTS5", "finds text typed without accents"],
      ["sqlite-vec", "bge-m3 vectors, 1024-d"],
      ["Legal relations", "basis, replaces, amends, repeals"],
      ["Conversations", "the context of each turn"],
    ],
    question: "Question", questionSub: ["with or without diacritics"],
    retrieval: "Hybrid retrieval", retrievalSub: ["full text, vector, folded", "fused with RRF"],
    reranker: "ViRanker", rerankerSub: ["rescores the best passage, drops the off-topic"],
    agent: "Qwen3 assistant", agentPort: "llama-server :9998",
    chips: ["conversation state", "document resolver", "metadata tables", "lookup tools"],
    checkChip: "answer check",
    answer: "Answer", answerSub: "sources open the PDF page",
    tools: "tools, metadata tables", save: "saved each turn",
    legendModel: "Local model", legendCode: "The app's own code", legendCheck: "Check step",
    credit: "OCR VBHC 2.0, developed by Trần Hậu Mạnh",
  },
};

const W = 1200;
const H = 816;

export function architecture(glyphs, theme, lang) {
  const s = S[lang];
  const c = new Canvas(glyphs, theme, lang, W, H);
  const t = theme;
  let b = rect(0, 0, W, H, { rx: 16, fill: t.bg });

  // Header
  b += c.text(s.title, 40, 54, { size: 27, weight: 600, fill: t.ink });
  b += c.text(s.sub, 40, 84, { size: 15, fill: t.ink2 });

  // Machine boundary
  const BX = 24, BY = 118, BW = W - 48, BH = 664;
  b += rect(BX, BY, BW, BH, { rx: 18, fill: "none", stroke: t.oliveText, sw: 1.6 });
  const mw = c.width(s.machine, 15, 600);
  b += rect(BX + 22, BY - 11, mw + 50, 22, { fill: t.bg });
  b += c.icon("house", BX + 30, BY - 9, 18, t.oliveText);
  b += c.text(s.machine, BX + 54, BY + 5, { size: 15, weight: 600, fill: t.oliveText });

  // The network stays outside: a stub that stops at the boundary.
  const NX = W - 96;
  b += cloud(NX, 72, t.muted);
  b += `<path d="M${NX} 84V${BY - 12}" stroke="${t.muted}" stroke-width="1.5"/>`;
  b += `<path d="M${NX - 9} ${BY - 12}H${NX + 9}" stroke="${t.red}" stroke-width="3" stroke-linecap="round"/>`;
  b += `<path d="M${NX - 17} 58L${NX + 17} 92" stroke="${t.red}" stroke-width="2.2" stroke-linecap="round"/>`;
  b += c.text(s.offline, NX - 34, 78, { size: 13.5, weight: 500, fill: t.ink2, anchor: "end" });

  const pulses = [];
  const pulse = (x, y, w, h, rx = 12) => pulses.push([x - 4, y - 4, w + 8, h + 8, rx]);

  // ── Row 1: digitise ──
  const R1 = 186, R1H = 140;
  b += bandLabel(c, "1", s.band1, 48, R1 - 16);
  // Input: a page, drawn, with its NĐ30 field zones tinted.
  b += page(c, 52, R1 + 4, 92, 124);
  const inText = c.para(s.input, 158, R1 + 30, 126, { size: 15, weight: 600, fill: t.ink, lh: 1.3 });
  b += inText.svg;
  b += c.para(s.inputSub, 158, inText.bottom + 22, 126, { size: 13.5, fill: t.muted, lh: 1.38 }).svg;
  pulse(48, R1, 240, R1H);

  const n1 = [312, 529, 746, 963];
  const nw = 189;
  const r1nodes = [
    { kind: "code", icon: "scan", title: s.render, lines: s.renderSub },
    { kind: "model", icon: "app-mark", title: s.chandra, lines: s.chandraSub },
    { kind: "check", icon: "check-circle", title: s.gate, lines: s.gateSub },
    { kind: "code", icon: "text-description", title: s.parser, lines: s.parserSub },
  ];
  r1nodes.forEach((n, i) => {
    b += node(c, { ...n, x: n1[i], y: R1, w: nw, h: R1H });
    pulse(n1[i], R1, nw, R1H);
  });
  const ry = R1 + R1H / 2;
  b += edge(c, [[288, ry], [n1[0], ry]]);
  for (let i = 0; i < 3; i++) b += edge(c, [[n1[i] + nw, ry], [n1[i + 1], ry]], { delay: 0.1 * i });

  // ── Row 2: storage, fed by a bus from the parser ──
  const R2 = 398, R2H = 112;
  const colW = (W - 96) / 6;
  const busY = R1 + R1H + 30;
  const px = n1[3] + nw / 2;
  b += `<path d="M${px} ${R1 + R1H}V${busY - 10}Q${px} ${busY} ${px - 10} ${busY}H${48 + colW / 2 + 10}Q${48 + colW / 2} ${busY} ${48 + colW / 2} ${busY + 10}" fill="none" stroke="${t.line}" stroke-width="1.5"/>`;
  b += `<path class="flow" d="M${px} ${R1 + R1H}V${busY}H${48 + colW / 2}"/>`;
  for (let i = 0; i < 5; i++) {
    const cx = 48 + colW * i + colW / 2;
    b += edge(c, [[cx, i === 0 ? busY + 9 : busY], [cx, R2]], { flow: false });
  }
  const busLabelW = c.width(s.bus, 13, 500) + 22;
  const blx = (48 + colW * 4 + colW / 2 + px) / 2 - busLabelW / 2;
  b += rect(blx, busY - 12, busLabelW, 24, { rx: 12, fill: t.oliveSoft, stroke: t.oliveEdge });
  b += c.text(s.bus, blx + busLabelW / 2, busY + 4.5, { size: 13, weight: 500, fill: t.oliveText, anchor: "middle" });

  b += rect(48, R2, W - 96, R2H, { rx: 12, fill: t.surface, stroke: t.border });
  pulse(48, R2, W - 96, R2H);
  b += c.icon("database", 62, R2 + 13, 18, t.ink2);
  b += c.text("2", 88, R2 + 27, { size: 15, weight: 600, fill: t.oliveText });
  b += c.text(s.band2, 104, R2 + 27, { size: 15, weight: 600, fill: t.ink });
  b += c.text(s.band2b, 104 + c.width(s.band2, 15, 600) + 10, R2 + 27, { size: 13.5, fill: t.muted });
  b += `<path d="M48 ${R2 + 42}H${W - 48}" stroke="${t.border}"/>`;
  s.cols.forEach(([title, sub], i) => {
    const x = 48 + colW * i;
    if (i) b += `<path d="M${x} ${R2 + 42}V${R2 + R2H}" stroke="${t.border}"/>`;
    b += c.text(title, x + 14, R2 + 66, { size: 14, weight: 600, fill: t.ink });
    b += c.para(sub, x + 14, R2 + 87, colW - 26, { size: 13, fill: t.muted, lh: 1.35 }).svg;
  });

  // ── Row 3: ask ──
  const R3 = 590, R3H = 168;
  b += bandLabel(c, "3", s.band3, 48, R3 - 16);
  const q = { x: 48, w: 132 }, rt = { x: 208, w: 180 }, rr = { x: 416, w: 160 }, ag = { x: 604, w: 328 }, an = { x: 960, w: 192 };
  b += bubble(c, q.x, R3, q.w, R3H, s.question, s.questionSub);
  pulse(q.x, R3, q.w, R3H);
  b += node(c, { kind: "code", icon: "search", title: s.retrieval, lines: s.retrievalSub, x: rt.x, y: R3, w: rt.w, h: R3H });
  pulse(rt.x, R3, rt.w, R3H);
  b += node(c, { kind: "model", icon: "sort", title: s.reranker, lines: s.rerankerSub, x: rr.x, y: R3, w: rr.w, h: R3H });
  pulse(rr.x, R3, rr.w, R3H);
  // Assistant: model node with its parts as chips.
  b += node(c, { kind: "model", icon: "sparkles", title: s.agent, lines: [{ text: s.agentPort, weight: 500, fill: "ink2" }], x: ag.x, y: R3, w: ag.w, h: R3H });
  pulse(ag.x, R3, ag.w, R3H);
  b += chips(c, ag.x + 14, R3 + 74, ag.w - 28, s.chips, s.checkChip);
  // Answer with citation chips.
  b += answerNode(c, an.x, R3, an.w, R3H, s);
  pulse(an.x, R3, an.w, R3H);

  const y3 = R3 + R3H / 2;
  b += edge(c, [[q.x + q.w, y3], [rt.x, y3]]);
  b += edge(c, [[rt.x + rt.w, y3], [rr.x, y3]], { delay: 0.2 });
  b += edge(c, [[rr.x + rr.w, y3], [ag.x, y3]], { delay: 0.4 });
  b += edge(c, [[ag.x + ag.w, y3], [an.x, y3]], { delay: 0.6 });
  // Reads from and writes to storage
  const rb = R2 + R2H;
  b += edge(c, [[rt.x + rt.w / 2, rb], [rt.x + rt.w / 2, R3]]);
  b += edge(c, [[ag.x + ag.w / 2, rb], [ag.x + ag.w / 2, R3]], { delay: 0.3 });
  b += c.text(s.tools, ag.x + ag.w / 2 + 12, rb + 45, { size: 13, weight: 500, fill: t.ink2 });
  b += edge(c, [[an.x + an.w / 2, R3], [an.x + an.w / 2, rb]], { delay: 0.5 });
  b += c.text(s.save, an.x + an.w / 2 + 12, rb + 45, { size: 13, weight: 500, fill: t.ink2 });

  // Pulses travel along the stages in reading order.
  const step = 12 / pulses.length;
  b += pulses.map(([x, y, w, h, rx], i) => rect(x, y, w, h, { rx, cls: "pulse", style: "animation-delay:" + (i * step).toFixed(2) + "s" })).join("");

  // Legend + credit
  const LY = H - 18;
  let lx = 40;
  for (const [kind, label] of [["model", s.legendModel], ["code", s.legendCode], ["check", s.legendCheck]]) {
    const col = kindColors(t, kind);
    b += rect(lx, LY - 12, 16, 14, { rx: 4, fill: col.fill, stroke: col.stroke });
    b += c.text(label, lx + 24, LY, { size: 13.5, fill: t.ink2 });
    lx += 24 + c.width(label, 13.5) + 28;
  }
  b += c.text(s.credit, W - 40, LY, { size: 13.5, fill: t.muted, anchor: "end" });

  const desc = lang === "vi"
    ? "Sơ đồ kiến trúc OCR VBHC. Số hóa: PDF hoặc ảnh, dựng trang, Chandra OCR 2 trên llama-server cổng 9999, kiểm tra chất lượng trang, tách 12 trường theo Nghị định 30. Lưu trữ trong SQLite: văn bản và phiên bản, FTS5 toàn văn, FTS5 không dấu, sqlite-vec với vector bge-m3, quan hệ pháp lý, hội thoại. Hỏi đáp: câu hỏi, truy xuất lai gộp RRF, ViRanker, trợ lý Qwen3 trên llama-server cổng 9998 (trạng thái hội thoại, phân giải văn bản, bảng thống kê, công cụ, kiểm tra câu trả lời), câu trả lời có trích dẫn. Tất cả nằm trong máy của người dùng; không có dữ liệu nào rời khỏi máy."
    : "OCR VBHC architecture. Digitise: PDF or image, render pages, Chandra OCR 2 on llama-server port 9999, page quality gate, 12 fields of Decree 30. Store in SQLite: documents and versions, FTS5 full text, folded FTS5, sqlite-vec with bge-m3 vectors, legal relations, conversations. Ask: question, hybrid retrieval fused with RRF, ViRanker, Qwen3 assistant on llama-server port 9998 (conversation state, document resolver, metadata tables, tools, answer check), answer with citations. Everything stays inside the user's machine; no data leaves it.";
  const svg = finish(c, b, { title: s.title, desc, style: baseStyle(t) });
  return { svg, canvas: c };
}

function bandLabel(c, n, label, x, y) {
  return c.text(n, x, y, { size: 15, weight: 600, fill: c.t.oliveText }) +
    c.text(label, x + 16, y, { size: 15, weight: 600, fill: c.t.ink });
}

function cloud(cx, cy, color) {
  const d = `M${cx - 22} ${cy + 10}h44a11 11 0 0 0 0-22a15 15 0 0 0-28-5a11 11 0 0 0-16 27z`;
  return `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round"/>`;
}

// A portrait administrative page; gold zones mark where NĐ30 fields sit.
function page(c, x, y, w, h) {
  const t = c.t;
  let s = rect(x + 7, y - 6, w, h, { rx: 3, fill: t.alt, stroke: t.border });
  s += `<path d="M${x} ${y + 3}a3 3 0 0 1 3-3H${x + w - 16}L${x + w} ${y + 16}V${y + h - 3}a3 3 0 0 1-3 3H${x + 3}a3 3 0 0 1-3-3Z" fill="${t.surface}" stroke="${t.line}" stroke-width="1"/>`;
  s += `<path d="M${x + w - 16} ${y}V${y + 16}H${x + w}" fill="none" stroke="${t.line}" stroke-width="1"/>`;
  const L = (x1, y1, len, wgt = 1.6, col = t.line) => `<path d="M${x1} ${y1}h${len}" stroke="${col}" stroke-width="${wgt}" stroke-linecap="round"/>`;
  // Field zones: issuer + number (top left), date (top right), summary, signer.
  s += rect(x + 6, y + 9, 34, 23, { rx: 2, fill: t.goldSoft });
  s += rect(x + 44, y + 23, 36, 10, { rx: 2, fill: t.goldSoft });
  s += rect(x + 18, y + 40, 56, 14, { rx: 2, fill: t.goldSoft });
  s += rect(x + 50, y + 96, 36, 20, { rx: 2, fill: t.goldSoft });
  s += L(x + 10, y + 14, 26, 1.8, t.ink2) + L(x + 14, y + 20, 18) + L(x + 12, y + 28, 22);
  s += L(x + 48, y + 14, 26, 1.8, t.ink2) + L(x + 52, y + 19, 18) + L(x + 48, y + 28, 28);
  s += L(x + 26, y + 45, 40, 1.8, t.ink2) + L(x + 22, y + 50, 48);
  for (let i = 0; i < 5; i++) s += L(x + 10, y + 62 + i * 6, i === 4 ? 44 : 72);
  s += L(x + 10, y + 102, 26) + L(x + 10, y + 107, 22);
  s += L(x + 56, y + 101, 24, 1.8, t.ink2) + L(x + 58, y + 111, 20);
  return s;
}

function bubble(c, x, y, w, h, title, lines) {
  const t = c.t;
  const r = 12;
  const d = `M${x + r} ${y}H${x + w - r}a${r} ${r} 0 0 1 ${r} ${r}V${y + h - r}a${r} ${r} 0 0 1-${r} ${r}H${x + 28}l-14 12v-12H${x + r}a${r} ${r} 0 0 1-${r}-${r}V${y + r}a${r} ${r} 0 0 1 ${r}-${r}z`;
  let s = `<path d="${d}" fill="${t.surface}" stroke="${t.border}"/>`;
  s += c.icon("chat", x + 13, y + 12, 18, t.ink2);
  s += c.text(title, x + 38, y + 26, { size: 15, weight: 600, fill: t.ink });
  const p = c.para(lines[0], x + 14, y + 52, w - 28, { size: 13.5, fill: t.muted, lh: 1.38 });
  return s + p.svg;
}

function chips(c, x, y, maxW, labels, checkLabel) {
  const t = c.t;
  let s = "";
  let cx = x;
  let cy = y;
  const all = labels.map((l) => [l, false]).concat([[checkLabel, true]]);
  for (const [label, gold] of all) {
    const w = c.width(label, 13, 500) + 20;
    if (cx + w > x + maxW) {
      cx = x;
      cy += 30;
    }
    s += rect(cx, cy, w, 24, { rx: 12, fill: gold ? t.goldSoft : t.surface, stroke: gold ? t.goldEdge : t.oliveEdge });
    s += c.text(label, cx + 10, cy + 16.5, { size: 13, weight: 500, fill: gold ? t.gold : t.ink2 });
    cx += w + 7;
  }
  return s;
}

function answerNode(c, x, y, w, h, s) {
  const t = c.t;
  let out = rect(x, y, w, h, { rx: 10, fill: t.surface, stroke: t.border });
  out += c.icon("check-circle", x + 13, y + 12, 18, t.ink2);
  out += c.text(s.answer, x + 39, y + 26, { size: 15, weight: 600, fill: t.ink });
  // A short answer line with citation chips, then what the chips do.
  const L = (x1, y1, len) => `<path d="M${x1} ${y1}h${len}" stroke="${t.line}" stroke-width="2" stroke-linecap="round"/>`;
  out += L(x + 14, y + 54, w - 40) + L(x + 14, y + 66, w - 70);
  let cx = x + 14;
  for (const n of ["1", "2"]) {
    out += rect(cx, y + 78, 30, 22, { rx: 11, fill: t.olive });
    out += c.text("[" + n + "]", cx + 15, y + 94, { size: 12.5, weight: 600, fill: t.onOlive, anchor: "middle" });
    cx += 36;
  }
  out += c.para(s.answerSub, x + 14, y + 124, w - 28, { size: 13.5, fill: t.muted }).svg;
  return out;
}
