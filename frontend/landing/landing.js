(() => {
  "use strict";
  let bridge = null;
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); };
  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function request(action, payload = {}) {
    if (!bridge) return showNotice("Giao diện chưa kết nối với ứng dụng.");
    bridge.requestAction(action, JSON.stringify(payload));
  }
  function showNotice(message) {
    const notice = $("#notice"); notice.textContent = message; notice.hidden = false;
    window.setTimeout(() => { notice.hidden = true; }, 4200);
  }
  function render(snapshot) {
    document.documentElement.dataset.theme = snapshot.theme || "light";
    (snapshot.commands || []).forEach((spec) => {
      const label = document.querySelector(`[data-action="${spec.action}"] b`);
      if (label) label.textContent = spec.label;
    });
    $("#document-count").textContent = `${snapshot.documentCount || 0} văn bản`;
    $("#chunk-count").textContent = `${snapshot.chunkCount || 0} đoạn`;
    $("#ocr-status").textContent = snapshot.models?.ocr === "available" ? "Đã phát hiện" : "Thiếu model";
    $("#chat-status").textContent = snapshot.models?.chat === "available" ? "Đã phát hiện" : "Thiếu model";
    const list = $("#recent-list"); clear(list);
    if (!snapshot.recentDocuments?.length) {
      const empty = document.createElement("p"); empty.className = "empty";
      empty.textContent = "Kho chưa có văn bản. Hãy quét một tệp hoặc nhập thư mục.";
      return list.append(empty);
    }
    snapshot.recentDocuments.forEach((doc) => {
      const button = document.createElement("button"); button.className = "recent";
      const title = document.createElement("b"); title.textContent = doc.title;
      const meta = document.createElement("small");
      meta.textContent = [doc.symbol, doc.pageCount ? `${doc.pageCount} trang` : "", doc.updatedAt].filter(Boolean).join(" · ");
      button.append(title, meta); button.addEventListener("click", () => request("open_recent", {docId: doc.id}));
      list.append(button);
    });
  }
  function enter() {
    if (!window.gsap || reducedMotion()) return;
    gsap.timeline({defaults:{ease:"power3.out"}})
      .from(".topbar", {opacity:0,y:-8,duration:.25})
      .from(".intro>*", {opacity:0,y:14,duration:.36,stagger:.06}, "-=.1")
      .from(".action", {opacity:0,y:12,duration:.3,stagger:.04}, "-=.24")
      .from(".lower-grid", {opacity:0,y:10,duration:.28}, "-=.18")
      .from("footer", {opacity:0,duration:.18}, "-=.12");
  }
  function setProgress(percent, message) {
    $("#startup").hidden = false; $("#startup-message").textContent = message;
    $("#progress-value").textContent = `${percent}%`;
    if (window.gsap) gsap.to("#progress-bar", {width:`${percent}%`,duration:.24,ease:"power2.out"});
    else $("#progress-bar").style.width = `${percent}%`;
  }
  function aboutIsOpen() {
    return $("#about-dialog").open;
  }
  function openAbout(event) {
    event?.preventDefault();
    const overlay = $("#about-dialog"), card = overlay.querySelector(".about-card");
    if (overlay.open) return;
    if (typeof overlay.showModal === "function") overlay.showModal();
    else overlay.setAttribute("open", "");
    overlay.setAttribute("aria-hidden", "false");
    $("#about-close").focus();
    if (window.gsap && !reducedMotion()) {
      gsap.fromTo(card, {opacity:0,y:12,scale:.985}, {opacity:1,y:0,scale:1,duration:.22,ease:"power3.out",clearProps:"transform,opacity"});
    }
  }
  function closeAbout(event) {
    event?.preventDefault();
    const overlay = $("#about-dialog");
    if (!overlay.open) return;
    if (typeof overlay.close === "function") overlay.close();
    else overlay.removeAttribute("open");
    overlay.setAttribute("aria-hidden", "true");
    $("#info-button").focus();
  }
  function attachTilt(button) {
    if (reducedMotion()) return;
    button.addEventListener("pointerenter", () => {
      $$(".action.is-hovered").forEach((card) => card.classList.remove("is-hovered"));
      button.classList.add("is-hovered");
    });
    button.addEventListener("pointermove", (event) => {
      $$(".action.is-hovered").forEach((card) => { if (card !== button) card.classList.remove("is-hovered"); });
      button.classList.add("is-hovered");
      const box = button.getBoundingClientRect();
      const x = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
      const y = Math.max(0, Math.min(1, (event.clientY - box.top) / box.height));
      button.style.setProperty("--rotate-x", `${((.5 - y) * 7).toFixed(2)}deg`);
      button.style.setProperty("--rotate-y", `${((x - .5) * 9).toFixed(2)}deg`);
      button.style.setProperty("--spot-x", `${(x * 100).toFixed(1)}%`);
      button.style.setProperty("--spot-y", `${(y * 100).toFixed(1)}%`);
    });
    button.addEventListener("pointerleave", () => {
      button.classList.remove("is-hovered");
      button.style.setProperty("--rotate-x", "0deg");
      button.style.setProperty("--rotate-y", "0deg");
    });
  }

  $$("[data-action]").forEach((button) => {
    attachTilt(button);
    button.addEventListener("click", () => request(button.dataset.action));
  });
  $("#info-button").addEventListener("click", openAbout);
  $("#about-close").addEventListener("click", closeAbout);
  $("#about-dialog").addEventListener("click", (event) => { if (event.target.id === "about-dialog") closeAbout(); });
  $("#about-dialog").addEventListener("cancel", closeAbout);
  document.addEventListener("keydown", (event) => {
    const key = event.key.toLocaleLowerCase();
    if (event.ctrlKey && event.altKey && key === "o") { event.preventDefault(); return request("import_folder"); }
    if (event.ctrlKey && event.shiftKey && key === "o") { event.preventDefault(); return request("open_files"); }
    if (event.ctrlKey && !event.shiftKey && !event.altKey && key === "o") { event.preventDefault(); return request("open_file"); }
    if (event.key === "Escape" && aboutIsOpen()) closeAbout(event);
  }, true);

  window.landingUI = Object.freeze({openAbout, closeAbout});

  if (typeof QWebChannel === "undefined" || typeof qt === "undefined") {
    $("#app").setAttribute("aria-busy", "false"); enter(); return;
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
      try { render(JSON.parse(raw)); $("#app").setAttribute("aria-busy", "false"); enter(); }
      catch (error) { console.error(error); showNotice("Không đọc được trạng thái kho văn bản."); }
    });
  });
})();
