// Which model runs when, where, and how the app picks them.
import { Canvas, finish, rect, baseStyle, num } from "./lib.mjs";

const S = {
  vi: {
    title: "Các mô hình chia nhau chiếc máy thế nào",
    sub: "Hai máy chủ llama.cpp và hai mô hình nhỏ, mỗi thứ chỉ làm việc khi cần. Ví dụ trên máy có GPU 24 GB.",
    ocrPanel: "Số hóa một văn bản 3 trang", chatPanel: "Trả lời một câu hỏi",
    lanes: [
      { dev: "GPU", name: "Chandra OCR 2", l1: "llama-server :9999", l2: "VRAM ≈3,6 GB với 1 luồng, 4,1 GB với 2" },
      { dev: "GPU", name: "Qwen3-8B", l1: "llama-server :9998, mở khi hỏi lần đầu", l2: "ngữ cảnh 16k, bộ nhớ KV dạng q8_0" },
      { dev: "GPU/CPU", name: "bge-m3 tiếng Việt", l1: "nhúng đoạn văn và câu hỏi, 1024 chiều", l2: "GPU khi PyTorch hỗ trợ card, nếu không CPU" },
      { dev: "CPU", name: "ViRanker", l1: "0,6B, chấm lại 10 đoạn tốt nhất", l2: "luôn chạy CPU để dành VRAM" },
      { dev: "CPU", name: "Mã của ứng dụng", l1: "kiểm tra trang, tách NĐ30, SQLite,", l2: "định tuyến, kiểm tra câu trả lời" },
    ],
    worker: "luồng", page: "trang", idle: "chưa dùng",
    gateTick: "kiểm tra", parse: "NĐ30", embed: "nhúng đoạn", store: "lưu",
    qEmbed: "nhúng", fts: "FTS5", rerank: "ViRanker", qwen: "Qwen3 trả lời", check: "kiểm tra ≈2 ms",
    ocrNote: "≈7,8 s/trang với 2 luồng khi GPU rảnh; ≈90 s/trang khi chỉ có CPU",
    chatNote: "≈5,6 s mỗi lượt trên GPU; hỏi về chính cuộc trò chuyện ≈0,8 s",
    time: "thời gian", legend: [["model", "mô hình"], ["code", "mã ứng dụng"], ["check", "kiểm tra"]],
    vramTitle: "VRAM của GPU 24 GB", vramSub: "khi cả hai máy chủ cùng chạy",
    vramChandra: "Chandra OCR 2, 2 luồng: 4,1 GB (đo)", vramQwen: "Qwen3-8B: tệp 4,7 GB + KV 16k 1,2 GB (tính)", vramRest: "còn lại cho hệ thống và bge-m3",
    vramFoot: "Phần Qwen3 tính từ kích thước tệp và cấu hình bộ nhớ KV, chưa gồm bộ đệm tính toán.",
    pickTitle: "Chọn mô hình trò chuyện theo VRAM",
    pick: [
      ["≥ 10 GB", "Qwen3-8B trên GPU", "ngữ cảnh 16k, tối đa 8 vòng công cụ"],
      ["5 đến 10 GB", "Qwen3-4B-Instruct-2507 trên GPU", "ngữ cảnh 16k, tệp 2,3 GB"],
      ["dưới 5 GB", "Qwen3-4B trên CPU", "ngữ cảnh 8k, tối đa 4 vòng công cụ"],
    ],
    workTitle: "Số luồng OCR song song",
    workSub: "mỗi luồng giữ một ngữ cảnh 12.288 token",
    work: [
      ["chỉ có CPU", 1, "≈90 s/trang"],
      ["GPU dưới 16 GB", 1, "tuần tự nhanh hơn"],
      ["GPU 16 đến 48 GB", 2, "≈7,8 s/trang"],
      ["GPU từ 48 GB", 4, ""],
    ],
    workFoot: "Còn bị giới hạn bởi RAM trống và số luồng CPU.",
  },
  en: {
    title: "How the models share the machine",
    sub: "Two llama.cpp servers and two small models, each working only when needed. Example: a machine with a 24 GB GPU.",
    ocrPanel: "Digitising a 3-page document", chatPanel: "Answering one question",
    lanes: [
      { dev: "GPU", name: "Chandra OCR 2", l1: "llama-server :9999", l2: "VRAM ≈3.6 GB with 1 worker, 4.1 GB with 2" },
      { dev: "GPU", name: "Qwen3-8B", l1: "llama-server :9998, starts on the first question", l2: "16k context, q8_0 KV cache" },
      { dev: "GPU/CPU", name: "Vietnamese bge-m3", l1: "embeds passages and questions, 1024-d", l2: "GPU if PyTorch supports the card, else CPU" },
      { dev: "CPU", name: "ViRanker", l1: "0.6B, rescores the 10 best passages", l2: "always on CPU to leave VRAM free" },
      { dev: "CPU", name: "The app's own code", l1: "page gate, NĐ30 fields, SQLite,", l2: "routing, answer check" },
    ],
    worker: "worker", page: "page", idle: "not used",
    gateTick: "gate", parse: "NĐ30", embed: "embed", store: "save",
    qEmbed: "embed", fts: "FTS5", rerank: "ViRanker", qwen: "Qwen3 answers", check: "check ≈2 ms",
    ocrNote: "≈7.8 s/page with 2 workers on an idle GPU; ≈90 s/page on CPU only",
    chatNote: "≈5.6 s per turn on GPU; questions about the conversation itself ≈0.8 s",
    time: "time", legend: [["model", "model"], ["code", "app code"], ["check", "check"]],
    vramTitle: "VRAM of a 24 GB GPU", vramSub: "with both servers running",
    vramChandra: "Chandra OCR 2, 2 workers: 4.1 GB (measured)", vramQwen: "Qwen3-8B: 4.7 GB file + 16k KV 1.2 GB (computed)", vramRest: "left for the system and bge-m3",
    vramFoot: "The Qwen3 part is computed from the file size and the KV cache settings, before compute buffers.",
    pickTitle: "Chat model picked by VRAM",
    pick: [
      ["≥ 10 GB", "Qwen3-8B on GPU", "16k context, up to 8 tool rounds"],
      ["5 to 10 GB", "Qwen3-4B-Instruct-2507 on GPU", "16k context, 2.3 GB file"],
      ["under 5 GB", "Qwen3-4B on CPU", "8k context, up to 4 tool rounds"],
    ],
    workTitle: "Parallel OCR workers",
    workSub: "each worker holds its own 12,288-token context",
    work: [
      ["CPU only", 1, "≈90 s/page"],
      ["GPU under 16 GB", 1, "sequential is faster"],
      ["GPU 16 to 48 GB", 2, "≈7.8 s/page"],
      ["GPU from 48 GB", 4, ""],
    ],
    workFoot: "Also capped by free RAM and CPU threads.",
  },
};

const W = 1200;
const H = 928;

export function models(glyphs, theme, lang) {
  const s = S[lang];
  const t = theme;
  const c = new Canvas(glyphs, theme, lang, W, H);
  let b = rect(0, 0, W, H, { rx: 16, fill: t.bg });
  b += c.text(s.title, 40, 54, { size: 27, weight: 600 });
  b += c.text(s.sub, 40, 84, { size: 15, fill: t.ink2 });

  // ── Swimlanes ──
  const LX = 40, TX = 352, TX2 = 1160;
  const P1 = [TX, 800], P2 = [822, TX2];
  const L0 = 150, LH = 66, N = s.lanes.length;
  const LB = L0 + LH * N;
  for (const [x0, x1] of [P1, P2]) b += rect(x0 - 8, L0 - 40, x1 - x0 + 16, LB - L0 + 40, { rx: 12, fill: t.surface, stroke: t.border });
  b += c.text(s.ocrPanel, P1[0] + 4, L0 - 15, { size: 14.5, weight: 600, fill: t.ink });
  b += c.text(s.chatPanel, P2[0] + 4, L0 - 15, { size: 14.5, weight: 600, fill: t.ink });
  for (let i = 0; i <= N; i++) {
    if (i > 0 && i < N) b += `<path d="M${LX} ${L0 + i * LH}H${TX2 + 8}" stroke="${t.border}"/>`;
  }
  s.lanes.forEach((lane, i) => {
    const y = L0 + i * LH;
    b += devicePill(c, lane.dev, LX, y + 10);
    const pw = pillWidth(c, lane.dev);
    b += c.text(lane.name, LX + pw + 10, y + 25, { size: 15, weight: 600 });
    b += c.text(lane.l1, LX, y + 44, { size: 13, fill: t.ink2 });
    b += c.text(lane.l2, LX, y + 60, { size: 13, fill: t.muted });
  });
  const laneY = (i) => L0 + i * LH;
  let gx = LX;
  for (const [kind, label] of s.legend) {
    b += rect(gx, L0 - 30, 14, 14, { rx: 3, fill: kind === "model" ? t.olive : kind === "check" ? t.gold : t.ink2 });
    b += c.text(label, gx + 20, L0 - 18.5, { size: 13, fill: t.ink2 });
    gx += 20 + c.width(label, 13) + 18;
  }

  // OCR job: two workers on Chandra, gate after each page, then fields,
  // embedding and saving.
  const pw = 112;
  const x0 = P1[0] + 6;
  b += block(c, x0, x0 + pw, laneY(0) + 10, 22, s.page + " 1", "model");
  b += block(c, x0, x0 + pw, laneY(0) + 36, 22, s.page + " 2", "model");
  b += block(c, x0 + pw + 6, x0 + 2 * pw + 6, laneY(0) + 10, 22, s.page + " 3", "model");
  b += c.text(s.worker + " 1", x0 + 2 * pw + 14, laneY(0) + 26, { size: 12.5, fill: t.muted });
  b += c.text(s.worker + " 2", x0 + pw + 14, laneY(0) + 52, { size: 12.5, fill: t.muted });
  const g1 = x0 + pw + 1, g2 = x0 + 2 * pw + 8;
  b += block(c, g1, g1 + 8, laneY(4) + 14, 38, "", "check");
  b += block(c, g2, g2 + 8, laneY(4) + 14, 38, "", "check");
  b += c.text(s.gateTick, g1 - 6, laneY(4) + 37, { size: 12.5, fill: t.muted, anchor: "end" });
  const p0 = g2 + 12;
  b += block(c, p0, p0 + 56, laneY(4) + 14, 38, s.parse, "code");
  b += block(c, p0 + 60, p0 + 152, laneY(2) + 14, 38, s.embed, "model");
  b += block(c, p0 + 156, P1[1], laneY(4) + 14, 38, s.store, "code");
  b += c.text(s.idle, P1[0] + 6, laneY(1) + 38, { size: 13, fill: t.muted });
  b += c.text(s.idle, P1[0] + 6, laneY(3) + 38, { size: 13, fill: t.muted });

  // One question: embed + FTS5, rerank, Qwen3, check.
  const q0 = P2[0] + 4;
  b += block(c, q0, q0 + 62, laneY(2) + 14, 38, s.qEmbed, "model");
  b += block(c, q0, q0 + 62, laneY(4) + 14, 38, s.fts, "code");
  b += block(c, q0 + 66, q0 + 142, laneY(3) + 14, 38, s.rerank, "model");
  b += block(c, q0 + 146, P2[1] - 22, laneY(1) + 14, 38, s.qwen, "model");
  b += block(c, P2[1] - 18, P2[1] - 10, laneY(4) + 14, 38, "", "check");
  b += c.text(s.check, P2[1] - 24, laneY(4) + 37, { size: 12.5, fill: t.muted, anchor: "end" });

  // Time arrows and notes under each panel
  for (const [xa, xb, note] of [[P1[0], P1[1], s.ocrNote], [P2[0], P2[1], s.chatNote]]) {
    b += `<path d="M${xa} ${LB + 14}H${xb}" stroke="${t.line}" stroke-width="1.3" marker-end="url(#${c.marker(t.line)})"/>`;
    b += c.text(s.time, xb - 12, LB + 32, { size: 12.5, fill: t.muted, anchor: "end" });
    b += c.para(note, xa, LB + 52, xb - xa, { size: 13.5, weight: 500, fill: t.ink2, lh: 1.35 }).svg;
  }

  // ── VRAM bar on the timeline's width ──
  const VY = LB + 118;
  b += c.text(s.vramTitle, LX, VY + 4, { size: 15, weight: 600 });
  b += c.text(s.vramSub, LX, VY + 23, { size: 13, fill: t.muted });
  const bx = TX, bw = TX2 - TX, gb = bw / 24;
  const segs = [[4.1, t.cat[0], s.vramChandra], [5.9, t.cat[1], s.vramQwen]];
  b += rect(bx, VY - 12, bw, 24, { rx: 4, fill: t.track });
  let sx = bx;
  let lx = bx;
  segs.forEach(([v, col, label], i) => {
    const w = v * gb;
    b += rect(sx, VY - 12, w - 2, 24, { rx: i === 0 ? 4 : 0, fill: col });
    sx += w;
    b += rect(lx, VY + 26, 14, 14, { rx: 3, fill: col });
    b += c.text(label, lx + 21, VY + 38, { size: 13.5, weight: 500, fill: t.ink2 });
    lx += 21 + c.width(label, 13.5, 500) + 26;
  });
  b += c.text(s.vramRest, TX2 - 10, VY + 4.5, { size: 13, fill: t.muted, anchor: 'end' });
  b += c.text('0', bx, VY - 20, { size: 12, fill: t.muted, anchor: 'middle' });
  b += c.text('24 GB', TX2, VY - 20, { size: 12, fill: t.muted, anchor: 'end' });
  b += c.para(s.vramFoot, TX, VY + 62, TX2 - TX, { size: 12.5, fill: t.muted }).svg;

  // ── Selection rules ──
  const BY = VY + 92;
  const half = (TX2 - LX - 24) / 2;
  b += rect(LX, BY, half, 214, { rx: 12, fill: t.surface, stroke: t.border });
  b += c.text(s.pickTitle, LX + 18, BY + 30, { size: 15, weight: 600 });
  s.pick.forEach(([cond, model, detail], i) => {
    const y = BY + 52 + i * 48;
    b += condPill(c, cond, LX + 18, y, 118, i);
    b += c.text(model, LX + 150, y + 15, { size: 14, weight: 600 });
    b += c.text(detail, LX + 150, y + 33, { size: 13, fill: t.muted });
  });
  const RX = LX + half + 24;
  b += rect(RX, BY, half, 214, { rx: 12, fill: t.surface, stroke: t.border });
  b += c.text(s.workTitle, RX + 18, BY + 30, { size: 15, weight: 600 });
  b += c.text(s.workSub, RX + 18 + c.width(s.workTitle, 15, 600) + 10, BY + 30, { size: 13, fill: t.muted });
  s.work.forEach(([cond, n, note], i) => {
    const y = BY + 50 + i * 34;
    b += c.text(cond, RX + 18, y + 17, { size: 14, fill: t.ink });
    for (let k = 0; k < 4; k++) {
      b += rect(RX + 190 + k * 22, y + 4, 16, 16, { rx: 4, fill: k < n ? t.olive : "none", stroke: k < n ? "none" : t.border });
    }
    b += c.text(String(n), RX + 290, y + 17, { size: 14, weight: 600 });
    if (note) b += c.text(note, RX + 310, y + 17, { size: 13, fill: t.muted });
  });
  b += c.text(s.workFoot, RX + 18, BY + 196, { size: 12.5, fill: t.muted });

  const desc = lang === "vi"
    ? "Làn thời gian: Chandra OCR 2 trên GPU (llama-server 9999) đọc trang theo 1 hoặc 2 luồng; mã ứng dụng kiểm tra trang, tách trường NĐ30, lưu SQLite; bge-m3 nhúng đoạn văn. Khi hỏi: bge-m3 nhúng câu hỏi, FTS5 tìm từ, ViRanker trên CPU chấm lại, Qwen3-8B trên GPU (llama-server 9998) viết câu trả lời, mã ứng dụng kiểm tra trong khoảng 2 ms. Quy tắc chọn: VRAM từ 10 GB dùng Qwen3-8B, từ 5 GB dùng Qwen3-4B trên GPU, thấp hơn dùng Qwen3-4B trên CPU với ngữ cảnh 8k. Luồng OCR: CPU 1, GPU dưới 16 GB 1, từ 16 GB 2, từ 48 GB 4."
    : "Timeline: Chandra OCR 2 on the GPU (llama-server 9999) reads pages with 1 or 2 workers; the app's code gates pages, extracts NĐ30 fields and saves to SQLite; bge-m3 embeds passages. For a question: bge-m3 embeds it, FTS5 matches words, ViRanker on CPU rescores, Qwen3-8B on the GPU (llama-server 9998) writes the answer, and the app checks it in about 2 ms. Rules: 10 GB of VRAM or more runs Qwen3-8B, 5 GB or more Qwen3-4B on GPU, less runs Qwen3-4B on CPU with an 8k context. OCR workers: CPU 1, GPU under 16 GB 1, from 16 GB 2, from 48 GB 4.";
  return { svg: finish(c, b, { title: s.title, desc, style: baseStyle(t) }), canvas: c };
}

function pillWidth(c, label) {
  return c.width(label, 12, 600) + 16;
}

function devicePill(c, label, x, y) {
  const t = c.t;
  const w = pillWidth(c, label);
  const gpu = label === "GPU";
  const both = label.includes("/");
  const fill = gpu ? t.olive : both ? t.oliveSoft : t.surface;
  const stroke = gpu ? "none" : both ? t.oliveEdge : t.line;
  return rect(x, y, w, 20, { rx: 10, fill, stroke }) +
    c.text(label, x + w / 2, y + 14.2, { size: 12, weight: 600, fill: gpu ? t.onOlive : both ? t.oliveText : t.ink2, anchor: "middle" });
}

function condPill(c, label, x, y, w, i) {
  const t = c.t;
  return rect(x, y, w, 40, { rx: 8, fill: t.oliveSoft, stroke: t.oliveEdge }) +
    c.text(label, x + w / 2, y + 25, { size: 14, weight: 600, fill: t.oliveText, anchor: "middle" });
}

// An activity block on a lane; the label goes inside when it fits.
function block(c, x0, x1, y, h, label, kind) {
  const t = c.t;
  const fill = kind === "model" ? t.olive : kind === "check" ? t.gold : t.ink2;
  const ink = kind === "check" ? t.bg : kind === "model" ? t.onOlive : t.bg;
  let s = rect(x0, y, x1 - x0, h, { rx: 4, fill });
  if (label) {
    const w = c.width(label, 13, 500);
    if (w + 12 <= x1 - x0) s += c.text(label, (x0 + x1) / 2, y + h / 2 + 4.6, { size: 13, weight: 500, fill: ink, anchor: "middle" });
    else c.problems.push("block label does not fit: " + label);
  }
  return s;
}
