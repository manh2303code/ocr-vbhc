// One question's path through the assistant (rag_agent.ask).
import { Canvas, edge, finish, node, rect, baseStyle, kindColors } from "./lib.mjs";

const S = {
  vi: {
    title: "Một câu hỏi đi qua trợ lý như thế nào",
    sub: "Mô hình chỉ viết câu trả lời; phân loại, phân giải, thống kê và kiểm tra là mã của ứng dụng.",
    question: "Câu hỏi", example: "“So sánh kế hoạch 125/KH-UBND với văn bản trước”",
    state: "Trạng thái hội thoại", stateSub: "văn bản đang mở, văn bản và kết quả của từng lượt",
    route: "Phân loại câu hỏi", routeSub: ["bằng quy tắc, trước khi gọi mô hình"],
    small: "Chào hỏi, cảm ơn", smallSub: "trả lời ngắn, không gắn nguồn",
    meta: "Hỏi về chính cuộc trò chuyện", metaSub: "trả lời từ trạng thái hội thoại, ≈0,8 s",
    docs: "hỏi về văn bản",
    resolver: "Phân giải văn bản",
    resolverSub: ["hiểu “văn bản 125”, “văn bản trước”, “cả hai”, “tờ trình đó”, kể cả số hiệu bị OCR đọc sai"],
    cards: [
      ["Nêu đích danh văn bản", "nạp trước đúng các văn bản đó; khi so sánh, mỗi văn bản được chia phần như nhau"],
      ["Câu hỏi thống kê", "bảng do ứng dụng tính: người ký, chức vụ, số lượng theo loại, cơ quan, năm"],
      ["Các câu hỏi khác", "tìm lai rồi ViRanker chấm lại; đoạn dưới ngưỡng liên quan không thành nguồn"],
    ],
    qwen: "Qwen3 viết câu trả lời", qwenPort: "llama-server :9998",
    tools: ["tìm đoạn", "thông tin văn bản", "văn bản liên quan", "hiệu lực", "đọc một mục", "liệt kê"],
    qwenSub: "tối đa 8 vòng công cụ trên GPU, 4 trên CPU; câu hỏi thống kê trả lời thẳng từ bảng",
    check: "Kiểm tra câu trả lời", checkSub: "≈2 ms, bằng quy tắc, đối chiếu với nguồn đã đưa",
    checks: ["số ký hiệu", "ngày", "con số", "số lượng", "loại + số", "nhãn nguồn chép nguyên", "thiếu một bên khi so sánh"],
    retry: "Có chỗ không khớp", retrySub: "hỏi lại Qwen3 một lần, kèm chỗ cần sửa",
    hedge: "Vẫn không có trong nguồn", hedgeSub: "bỏ đi, hoặc ghi “(chưa xác minh)”",
    answer: "Câu trả lời", answerSub: "bấm [n] để mở đúng trang PDF",
    save: "Lưu ngữ cảnh lượt này", saveSub: "văn bản đang mở, văn bản đã phân giải và đã trích dẫn",
    next: "lượt sau",
  },
  en: {
    title: "How one question travels through the assistant",
    sub: "The model only writes the answer; routing, resolving, statistics and checking are the app's own code.",
    question: "Question", example: "“Compare plan 125/KH-UBND with the previous document”",
    state: "Conversation state", stateSub: "the open document, each turn's documents and outcome",
    route: "Route the question", routeSub: ["by rules, before any model call"],
    small: "Greetings, thanks", smallSub: "a short reply with no sources",
    meta: "About the conversation itself", metaSub: "answered from the conversation state, ≈0.8 s",
    docs: "about documents",
    resolver: "Resolve the documents",
    resolverSub: ["understands “document 125”, “the previous one”, “both”, “that proposal”, even a number OCR misread"],
    cards: [
      ["Documents named", "prefetch exactly those documents; in a comparison each one gets an equal share"],
      ["A statistics question", "a table the app computes: signers, positions, counts by type, issuer, year"],
      ["Anything else", "hybrid search, then ViRanker; passages below the relevance gate are not sources"],
    ],
    qwen: "Qwen3 writes the answer", qwenPort: "llama-server :9998",
    tools: ["search passages", "document info", "related documents", "validity", "read a section", "list"],
    qwenSub: "up to 8 tool rounds on GPU, 4 on CPU; statistics are answered straight from the table",
    check: "Answer check", checkSub: "≈2 ms, rules only, against the sources it was shown",
    checks: ["document numbers", "dates", "figures", "counts", "type + number", "copied source labels", "a missing side"],
    retry: "Something does not hold", retrySub: "ask Qwen3 once more with the correction",
    hedge: "Still not in the sources", hedgeSub: "removed, or marked “(unverified)”",
    answer: "Answer", answerSub: "click [n] to open the PDF page",
    save: "Save this turn's context", saveSub: "the open document, the documents resolved and cited",
    next: "next turn",
  },
};

const W = 1000;
const H = 1128;

export function chat(glyphs, theme, lang) {
  const s = S[lang];
  const t = theme;
  const c = new Canvas(glyphs, theme, lang, W, H);
  let b = rect(0, 0, W, H, { rx: 16, fill: t.bg });
  b += c.text(s.title, 40, 54, { size: 27, weight: 600 });
  b += c.para(s.sub, 40, 84, W - 80, { size: 15, fill: t.ink2 }).svg;

  const L = 40, LW = 520, RX = 600, RW = 360, FULL = W - 80;
  const cx = L + LW / 2;
  const pulses = [];
  const pulse = (x, y, w, h) => pulses.push([x - 4, y - 4, w + 8, h + 8]);

  // Question and the conversation state it carries
  const QY = 122;
  b += rect(L, QY, LW, 78, { rx: 12, fill: t.surface, stroke: t.border });
  b += c.icon("chat", L + 14, QY + 13, 18, t.ink2);
  b += c.text(s.question, L + 40, QY + 27, { size: 15, weight: 600 });
  b += c.text(s.example, L + 14, QY + 56, { size: 14, fill: t.ink2 });
  pulse(L, QY, LW, 78);
  b += node(c, { kind: "code", icon: "archive", title: s.state, lines: [s.stateSub], x: RX, y: QY, w: RW, h: 78, gap: 6, subSize: 13 });
  b += edge(c, [[RX, QY + 39], [L + LW, QY + 39]]);

  // Route
  const RY = 240;
  b += edge(c, [[cx, QY + 78], [cx, RY]]);
  b += node(c, { kind: "code", icon: "filter", title: s.route, lines: s.routeSub, x: L, y: RY, w: LW, h: 120 });
  pulse(L, RY, LW, 120);
  b += sideCard(c, RX, RY, RW, s.small, s.smallSub);
  b += sideCard(c, RX, RY + 64, RW, s.meta, s.metaSub);
  b += edge(c, [[L + LW, RY + 28], [RX, RY + 28]], { delay: 0.2 });
  b += edge(c, [[L + LW, RY + 92], [RX, RY + 92]], { delay: 0.4 });

  // Resolver
  const VY = 404;
  b += edge(c, [[cx, RY + 120], [cx, VY]]);
  b += c.text(s.docs, cx + 12, RY + 120 + 26, { size: 13, weight: 500, fill: t.ink2 });
  b += node(c, { kind: "code", icon: "document-search", title: s.resolver, lines: s.resolverSub, x: L, y: VY, w: FULL, h: 84 });
  pulse(L, VY, FULL, 84);

  // Three ways in
  const CY = 528, CW = (FULL - 32) / 3, CH = 108;
  s.cards.forEach(([title, sub], i) => {
    const x = L + i * (CW + 16);
    b += edge(c, [[x + CW / 2, VY + 84], [x + CW / 2, CY]], { delay: 0.15 * i });
    b += node(c, { kind: "code", title, lines: [sub], x, y: CY, w: CW, h: CH, subSize: 13.5, gap: 8 });
    pulse(x, CY, CW, CH);
    b += edge(c, [[x + CW / 2, CY + CH], [x + CW / 2, 680]], { delay: 0.15 * i });
  });

  // Qwen3
  const QW = 680, QH = 128;
  b += node(c, { kind: "model", icon: "sparkles", title: s.qwen, lines: [{ text: s.qwenPort, weight: 500, fill: "ink2" }], x: L, y: QW, w: FULL, h: QH });
  pulse(L, QW, FULL, QH);
  b += chipRow(c, L + 14, QW + 70, FULL - 28, s.tools, "tool");
  b += c.text(s.qwenSub, L + 14, QW + QH - 14, { size: 13.5, fill: t.muted });

  // Answer check and its two outcomes
  const KY = 854, KH = 132;
  b += edge(c, [[cx, QW + QH], [cx, KY]]);
  b += node(c, { kind: "check", icon: "check-circle", title: s.check, lines: [{ text: s.checkSub, fill: "ink2" }], x: L, y: KY, w: LW, h: KH, gap: 6 });
  pulse(L, KY, LW, KH);
  b += chipRow(c, L + 14, KY + 66, LW - 28, s.checks, "check");
  b += sideCard(c, RX, KY - 2, RW, s.retry, s.retrySub, "check");
  b += sideCard(c, RX, KY + 78, RW, s.hedge, s.hedgeSub, "check");
  b += edge(c, [[L + LW, KY + 26], [RX, KY + 26]]);
  b += edge(c, [[L + LW, KY + 106], [RX, KY + 106]], { delay: 0.3 });
  // The retry goes back to Qwen3 once.
  b += edge(c, [[RX + RW / 2, KY - 2], [RX + RW / 2, QW + QH]], { delay: 0.5 });

  // Answer and saving the turn
  const AY = 1024, AH = 80;
  b += edge(c, [[cx, KY + KH], [cx, AY]]);
  b += answer(c, L, AY, LW, AH, s);
  pulse(L, AY, LW, AH);
  b += node(c, { kind: "code", icon: "save", title: s.save, lines: [s.saveSub], x: RX, y: AY, w: RW, h: AH, gap: 6, subSize: 13 });
  pulse(RX, AY, RW, AH);
  b += edge(c, [[L + LW, AY + 40], [RX, AY + 40]]);
  // Back to the conversation state for the next turn.
  const ex = W - 18;
  b += edge(c, [[RX + RW, AY + 40], [ex, AY + 40], [ex, QY + 39], [RX + RW, QY + 39]], { radius: 12, delay: 0.2 });
  const mid = (QY + AY) / 2 + 40;
  const nw = c.width(s.next, 13, 500) + 16;
  b += `<rect x="${ex - 9}" y="${mid - nw / 2}" width="18" height="${nw}" fill="${t.bg}"/>`;
  b += `<g transform="rotate(-90 ${ex} ${mid})">` + c.text(s.next, ex, mid + 4.5, { size: 13, weight: 500, fill: t.ink2, anchor: "middle" }) + "</g>";
  c.textLog.pop();

  const step = 12 / pulses.length;
  b += pulses.map(([x, y, w, h], i) => rect(x, y, w, h, { rx: 13, cls: "pulse", style: "animation-delay:" + (i * step).toFixed(2) + "s" })).join("");

  const desc = lang === "vi"
    ? "Đường đi của một câu hỏi: câu hỏi cùng trạng thái hội thoại; phân loại bằng quy tắc (chào hỏi thì trả lời ngắn không gắn nguồn, hỏi về cuộc trò chuyện thì trả lời từ trạng thái hội thoại); phân giải văn bản; ba lối vào (nêu đích danh văn bản thì nạp trước đúng văn bản đó, câu hỏi thống kê thì dùng bảng do ứng dụng tính, còn lại thì tìm lai và ViRanker); Qwen3 viết câu trả lời, có thể gọi công cụ; kiểm tra câu trả lời bằng quy tắc, sai thì hỏi lại một lần, vẫn không có trong nguồn thì bỏ đi hoặc ghi chưa xác minh; câu trả lời có trích dẫn mở đúng trang PDF; lưu ngữ cảnh lượt cho lượt sau."
    : "The path of one question: the question with the conversation state; rule-based routing (greetings get a short reply with no sources, questions about the conversation are answered from its state); document resolving; three ways in (named documents are prefetched, statistics questions use a table the app computes, anything else uses hybrid search and ViRanker); Qwen3 writes the answer and may call tools; a rule-based answer check asks once more on a mismatch and removes or marks what is still unsupported; the cited answer opens the PDF page; the turn's context is saved for the next turn.";
  return { svg: finish(c, b, { title: s.title, desc, style: baseStyle(t) }), canvas: c };
}

function sideCard(c, x, y, w, title, sub, kind = "code") {
  const t = c.t;
  const col = kindColors(t, kind);
  let s = rect(x, y, w, 56, { rx: 10, fill: col.fill, stroke: col.stroke });
  s += c.text(title, x + 14, y + 23, { size: 14, weight: 600 });
  s += c.text(sub, x + 14, y + 43, { size: 13, fill: t.muted });
  if (c.width(sub, 13) > w - 28 || c.width(title, 14, 600) > w - 28) c.problems.push("side card too narrow: " + title);
  return s;
}

function chipRow(c, x, y, maxW, labels, kind) {
  const t = c.t;
  let s = "";
  let cx = x;
  let cy = y;
  for (const label of labels) {
    const w = c.width(label, 13, 500) + 20;
    if (cx + w > x + maxW) {
      cx = x;
      cy += 30;
    }
    const gold = kind === "check";
    s += rect(cx, cy, w, 24, { rx: 12, fill: t.surface, stroke: gold ? t.goldEdge : t.oliveEdge });
    s += c.text(label, cx + 10, cy + 16.5, { size: 13, weight: 500, fill: t.ink2 });
    cx += w + 7;
  }
  return s;
}

function answer(c, x, y, w, h, s) {
  const t = c.t;
  let out = rect(x, y, w, h, { rx: 10, fill: t.surface, stroke: t.border });
  out += c.icon("check-circle", x + 14, y + 13, 18, t.ink2);
  out += c.text(s.answer, x + 40, y + 27, { size: 15, weight: 600 });
  const L = (x1, y1, len) => `<path d="M${x1} ${y1}h${len}" stroke="${t.line}" stroke-width="2" stroke-linecap="round"/>`;
  out += L(x + 14, y + 52, 150) + L(x + 14, y + 64, 110);
  let cx = x + 172;
  for (const n of ["1", "2"]) {
    out += rect(cx, y + 47, 30, 22, { rx: 11, fill: t.olive });
    out += c.text("[" + n + "]", cx + 15, y + 63, { size: 12.5, weight: 600, fill: t.onOlive, anchor: "middle" });
    cx += 36;
  }
  out += c.text(s.answerSub, cx + 8, y + 63, { size: 13.5, fill: t.muted });
  return out;
}
