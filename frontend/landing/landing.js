(() => {
  "use strict";
  // Runs in QtWebEngine 5.15, i.e. Chromium 83: tests/test_landing.py lists
  // the newer APIs that rules out.
  let bridge = null;
  let commands = {};
  // The page answers the catalogue's shortcuts (snapshot.commands); these
  // keep the three file shortcuts alive if a snapshot ever arrives without it.
  const DEFAULT_SHORTCUTS = {open_file: "Ctrl+O", open_files: "Ctrl+Shift+O", import_folder: "Ctrl+Alt+O"};
  const TIP_DELAY = 600;
  const TIP_TARGET = "[data-tip],[data-shortcut]";
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); };
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const collapse = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const groupDigits = (value) =>
    String(Math.max(0, Math.floor(Number(value) || 0))).replace(/\B(?=(\d{3})+(?!\d))/g, ".");

  function request(action, payload = {}) {
    hideTip();
    if (!bridge) return showNotice("Giao diện chưa kết nối với ứng dụng.");
    bridge.requestAction(action, JSON.stringify(payload));
  }
  let noticeTimer = 0;
  function showNotice(message) {
    $("#notice-text").textContent = message;
    $("#notice").hidden = false;
    window.clearTimeout(noticeTimer);
    noticeTimer = window.setTimeout(() => { $("#notice").hidden = true; }, 5000);
  }

  // ── Dates: landing_data sends local time as YYYY-MM-DDTHH:MM:SS ──
  function describeStamp(value) {
    const raw = String(value || "");
    const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(raw);
    if (!m) return {short: raw, full: raw};
    const day = `${m[3]}/${m[2]}/${m[1]}`;
    if (m[4] === undefined) return {short: day, full: day};
    const time = `${m[4]}:${m[5]}`;
    const then = new Date(+m[1], +m[2] - 1, +m[3]);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const days = Math.round((today - then) / 86400000);
    const short = days === 0 ? `Hôm nay ${time}` : days === 1 ? `Hôm qua ${time}` : day;
    return {short, full: `${day} ${time}`};
  }

  // ── Tooltips: hover (after a pause) or keyboard focus ──
  // Styled like the workspace's QToolTip; the explanation lives here, not on
  // the page. A clipped cell ([data-clip]) adds its full text to the tip.
  let tipOwner = null;
  let tipTimer = 0;
  let hovered = null;
  function showTip(node) {
    const shortcut = node.dataset.shortcut || "";
    if (!node.isConnected || !(node.dataset.tip || shortcut)) return;
    const box = $("#tip");
    clear(box);
    const clipped = [...node.querySelectorAll("[data-clip]")]
      .filter((cell) => cell.scrollWidth > cell.clientWidth)
      .map((cell) => cell.textContent);
    const text = clipped.concat(node.dataset.tip || []).join("\n");
    if (text) box.append(document.createTextNode(text));
    if (shortcut) box.append(make("span", text ? "tip-key" : "", shortcut));
    const host = node.closest("dialog[open]") || document.body;
    if (box.parentNode !== host) host.append(box);
    box.hidden = false;
    tipOwner = node;
    node.setAttribute("aria-describedby", "tip");
    const anchor = node.getBoundingClientRect();
    const size = box.getBoundingClientRect();
    const width = document.documentElement.clientWidth;
    const height = document.documentElement.clientHeight;
    let top = anchor.bottom + 6;
    if (top + size.height > height - 8) top = anchor.top - size.height - 6;
    box.style.left = `${Math.max(8, Math.min(anchor.left, width - size.width - 8))}px`;
    box.style.top = `${Math.max(8, top)}px`;
  }
  function hideTip() {
    window.clearTimeout(tipTimer);
    $("#tip").hidden = true;
    if (tipOwner) tipOwner.removeAttribute("aria-describedby");
    tipOwner = null;
  }
  const keyboardInput = () => document.documentElement.dataset.input !== "pointer";

  document.addEventListener("pointerover", (event) => {
    const node = event.target.closest ? event.target.closest(TIP_TARGET) : null;
    if (node === hovered) return;
    hovered = node;
    hideTip();
    if (node) tipTimer = window.setTimeout(() => showTip(node), TIP_DELAY);
  });
  document.documentElement.addEventListener("pointerleave", () => { hovered = null; hideTip(); });
  document.addEventListener("pointerdown", () => {
    document.documentElement.dataset.input = "pointer";
    hideTip();
  }, true);
  document.addEventListener("focusin", (event) => {
    const node = event.target.closest ? event.target.closest(TIP_TARGET) : null;
    if (node && keyboardInput()) showTip(node);
  });
  document.addEventListener("focusout", (event) => {
    if (tipOwner && tipOwner.contains(event.target)) hideTip();
  });
  window.addEventListener("scroll", hideTip, true);
  window.addEventListener("resize", hideTip);
  window.addEventListener("blur", hideTip);

  // ── Snapshot ──
  const MODEL_TEXT = {
    ocr: {
      available: ["OCR sẵn sàng", "Mô hình nhận dạng Chandra đã có trên máy"],
      missing: ["Thiếu mô hình OCR", "Chưa có mô hình nhận dạng trong thư mục gguf\\chandra,\nnên chưa quét được văn bản"],
    },
    chat: {
      available: ["Trợ lý sẵn sàng", "Mô hình trả lời của trợ lý pháp luật đã có trên máy"],
      missing: ["Thiếu mô hình trợ lý", "Chưa có mô hình trong thư mục gguf\\chat,\nnên trợ lý pháp luật chưa trả lời được"],
    },
  };
  function setModel(selector, kind, state) {
    const node = $(selector);
    const known = state === "available" ? "available" : "missing";
    const [label, tip] = MODEL_TEXT[kind][known];
    node.dataset.state = known === "available" ? "ok" : "missing";
    node.querySelector(".status-text").textContent = label;
    node.dataset.tip = tip;
  }
  function renderCommands(specs) {
    commands = {};
    specs.forEach((spec) => {
      commands[spec.action] = spec;
      const button = $$("[data-action]").find((node) => node.dataset.action === spec.action);
      if (!button) return;
      const label = button.querySelector(".label");
      if (label && spec.label) label.textContent = spec.label;
      if (spec.hint) button.dataset.tip = spec.hint;
      if (spec.shortcut) {
        button.dataset.shortcut = spec.shortcut;
        button.setAttribute("aria-keyshortcuts", spec.shortcut.replace(/Ctrl/g, "Control"));
      }
    });
  }
  function renderRecent(documents) {
    const list = $("#recent-list");
    clear(list);
    $("#recent-cols").hidden = !documents.length;
    $("#recent-empty").hidden = documents.length > 0;
    documents.forEach((doc) => {
      const stamp = describeStamp(doc.updatedAt);
      const summary = collapse(doc.summary);
      const title = collapse(doc.title) || "Văn bản không tên";
      const pages = Number(doc.pageCount) || 0;
      const row = make("button", "row");
      row.type = "button";
      const symbol = make("span", "symbol", collapse(doc.symbol));
      symbol.dataset.clip = "";
      const text = make("span", summary ? "summary" : "summary is-file", summary || title);
      if (summary) text.dataset.clip = "";
      row.append(symbol, text, make("span", "when", stamp.short), make("span", "pages", pages ? String(pages) : ""));
      row.dataset.tip = [summary ? title : "", stamp.full ? `Cập nhật ${stamp.full}` : ""].filter(Boolean).join("\n");
      row.setAttribute("aria-label", [
        collapse(doc.symbol), summary || title,
        stamp.full ? `cập nhật ${stamp.full}` : "", pages ? `${pages} trang` : "",
      ].filter(Boolean).join(", "));
      row.addEventListener("click", () => request("open_recent", {docId: doc.id}));
      const item = make("li");
      item.append(row);
      list.append(item);
    });
  }
  function render(snapshot) {
    document.documentElement.dataset.theme = snapshot.theme === "dark" ? "dark" : "light";
    renderCommands(snapshot.commands || []);
    setModel("#ocr-status", "ocr", snapshot.models && snapshot.models.ocr);
    setModel("#chat-status", "chat", snapshot.models && snapshot.models.chat);
    $("#document-count").textContent = `${groupDigits(snapshot.documentCount)} văn bản`;
    $("#chunk-count").textContent = `Chỉ mục ${groupDigits(snapshot.chunkCount)} đoạn`;
    renderRecent(snapshot.recentDocuments || []);
  }

  function setProgress(percent, message) {
    hideTip();
    $("#startup").hidden = false;
    $("#startup-message").textContent = message;
    $("#progress-value").textContent = `${percent}%`;
    $("#progress-bar").style.width = `${Math.max(0, Math.min(100, percent))}%`;
  }

  // ── About ──
  function aboutIsOpen() {
    return $("#about-dialog").open;
  }
  function openAbout(event) {
    if (event) event.preventDefault();
    const overlay = $("#about-dialog");
    if (overlay.open) return;
    hideTip();
    if (typeof overlay.showModal === "function") overlay.showModal();
    else overlay.setAttribute("open", "");
    overlay.setAttribute("aria-hidden", "false");
    $("#about-close").focus();
  }
  function closeAbout(event) {
    if (event) event.preventDefault();
    const overlay = $("#about-dialog");
    if (!overlay.open) return;
    hideTip();
    if (typeof overlay.close === "function") overlay.close();
    else overlay.removeAttribute("open");
    overlay.setAttribute("aria-hidden", "true");
    $("#info-button").focus();
  }

  // ── Keyboard ──
  function matches(event, shortcut) {
    const parts = String(shortcut || "").toLowerCase().split("+");
    const key = parts.pop();
    return Boolean(key) && !event.metaKey
      && event.ctrlKey === parts.includes("ctrl")
      && event.shiftKey === parts.includes("shift")
      && event.altKey === parts.includes("alt")
      && event.key.toLowerCase() === key;
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Tab" || event.key.indexOf("Arrow") === 0) {
      document.documentElement.dataset.input = "keyboard";
    }
    if (event.key === "Escape") {
      hideTip();
      if (aboutIsOpen()) closeAbout(event);
      return;
    }
    if (aboutIsOpen() || !$("#startup").hidden) return;
    const shortcuts = Object.assign({}, DEFAULT_SHORTCUTS);
    Object.keys(commands).forEach((action) => {
      if (commands[action].shortcut) shortcuts[action] = commands[action].shortcut;
    });
    const action = Object.keys(shortcuts).find((name) => matches(event, shortcuts[name]));
    if (action) {
      event.preventDefault();
      request(action);
    }
  }, true);
  $("#recent-list").addEventListener("keydown", (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const rows = $$("#recent-list .row");
    const index = rows.indexOf(document.activeElement);
    if (index < 0) return;
    event.preventDefault();
    const step = event.key === "ArrowDown" ? 1 : -1;
    rows[Math.max(0, Math.min(rows.length - 1, index + step))].focus();
  });

  $$("[data-action]").forEach((button) => {
    button.addEventListener("click", () => request(button.dataset.action));
  });
  $("#info-button").addEventListener("click", openAbout);
  $("#about-close").addEventListener("click", closeAbout);
  $("#about-dialog").addEventListener("click", (event) => { if (event.target.id === "about-dialog") closeAbout(); });
  $("#about-dialog").addEventListener("cancel", closeAbout);

  window.landingUI = Object.freeze({openAbout, closeAbout});

  if (typeof QWebChannel === "undefined" || typeof qt === "undefined") {
    $("#app").setAttribute("aria-busy", "false");
    return;
  }

  new QWebChannel(qt.webChannelTransport, (channel) => {
    bridge = channel.objects.landingBridge;
    bridge.startupProgress.connect(setProgress);
    // The workspace fades in over this window and then closes it, so a stuck
    // overlay is invisible on the happy path — until a slow machine, or a
    // future caller that keeps the landing alive, leaves it covering the page.
    bridge.startupReady.connect(() => { $("#startup").hidden = true; });
    bridge.startupFailed.connect((message) => { $("#startup").hidden = true; showNotice(message); });
    bridge.action_rejected?.connect(showNotice);
    bridge.getSnapshot((raw) => {
      try { render(JSON.parse(raw)); $("#app").setAttribute("aria-busy", "false"); }
      catch (error) { console.error(error); showNotice("Không đọc được trạng thái kho văn bản."); }
    });
  });
})();
