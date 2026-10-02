/*! AI-консультант для сайта · embed:
 *  <script src="https://YOUR-SERVER/widget.js" data-site="SITE-ID" defer></script>
 *  Works on Tilda (Site settings → Вставка кода → HTML-код для вставки внутрь head) and on any HTML site.
 *  Everything lives in a Shadow DOM, so the host site's styles can't break it and it can't break them.
 */
(function () {
  "use strict";
  if (window.__aiConsultantLoaded) return;
  window.__aiConsultantLoaded = true;

  var script = document.currentScript || document.querySelector('script[src*="widget.js"][data-site]');
  var SITE = script && script.getAttribute("data-site");
  if (!SITE) { console.warn("[ai-consultant] data-site attribute is missing"); return; }
  var API = (script.getAttribute("data-api") || new URL(script.src, location.href).origin).replace(/\/$/, "");
  var KEY = "aic:" + SITE + ":";
  var LANGS = ["ru", "en", "ar"];

  // ---------- small helpers ----------
  function load(k, fallback) {
    try { var v = sessionStorage.getItem(KEY + k); return v === null ? fallback : JSON.parse(v); }
    catch (e) { return fallback; }
  }
  function save(k, v) { try { sessionStorage.setItem(KEY + k, JSON.stringify(v)); } catch (e) { /* private mode */ } }
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    for (var k in attrs || {}) {
      if (k === "text") node.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== false && attrs[k] != null) node.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    }
    (children || []).forEach(function (c) { if (c) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return node;
  }
  function pick(block, lang) {
    if (!block) return "";
    if (typeof block === "string") return block;
    return block[lang] || block.ru || block.en || "";
  }
  function sessionId() {
    var id = load("session", null);
    if (!id) {
      id = (window.crypto && crypto.randomUUID) ? crypto.randomUUID().replace(/-/g, "")
        : (Date.now().toString(36) + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2));
      save("session", id);
    }
    return id;
  }
  function contrastOn(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return "#ffffff";
    var n = parseInt(m[1], 16), rgb = [n >> 16 & 255, n >> 8 & 255, n & 255].map(function (c) {
      c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    var lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
    return lum > 0.4 ? "#141414" : "#ffffff";
  }
  function linkify(text) {   // text → nodes; only http(s) links become <a>, everything else stays text
    var frag = document.createDocumentFragment(), re = /(https?:\/\/[^\s<]+[^\s<.,;:!?)»])/g, last = 0, m;
    while ((m = re.exec(text))) {
      frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      frag.appendChild(el("a", { href: m[1], target: "_blank", rel: "noopener noreferrer", text: m[1] }));
      last = m.index + m[1].length;
    }
    frag.appendChild(document.createTextNode(text.slice(last)));
    return frag;
  }
  function post(path, body) {
    return fetch(API + path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) { var err = new Error("HTTP " + r.status); err.status = r.status; err.data = data; throw err; }
        return data;
      });
    });
  }

  var ICON_CHAT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.2 3.6c-.5.4-1.3.1-1.3-.6V16A2.5 2.5 0 0 1 4 13.5z" fill="currentColor"/><circle cx="8.5" cy="9.5" r="1.2" fill="var(--aic-accent)"/><circle cx="12" cy="9.5" r="1.2" fill="var(--aic-accent)"/><circle cx="15.5" cy="9.5" r="1.2" fill="var(--aic-accent)"/></svg>';
  var ICON_CLOSE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
  var ICON_SEND = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12l15-7-5.5 15-2.5-6.5z" fill="currentColor"/></svg>';

  var CSS = [
    ":host{all:initial}",
    ":host{font-family:inherit}",
    "*{box-sizing:border-box}",
    ".root{position:fixed;z-index:2147483000;bottom:20px;font:15px/1.45 var(--aic-font,inherit);color:#1d1d1b;-webkit-font-smoothing:antialiased}",
    ".root.right{right:20px}.root.left{left:20px}",
    "button{font:inherit;color:inherit;cursor:pointer}",
    ".launcher{position:relative;width:60px;height:60px;border-radius:50%;border:0;background:var(--aic-accent);color:var(--aic-on);display:grid;place-items:center;box-shadow:0 10px 30px -8px rgba(0,0,0,.45);transition:transform .15s ease}",
    ".launcher:hover{transform:translateY(-2px)}",
    ".launcher svg{width:30px;height:30px}",
    ".launcher .dot{position:absolute;top:2px;right:2px;width:14px;height:14px;border-radius:50%;background:#e5484d;border:2px solid #fff}",
    ".teaser{position:absolute;bottom:72px;width:260px;background:#fff;border-radius:14px;padding:12px 34px 12px 14px;box-shadow:0 12px 32px -10px rgba(0,0,0,.35);font-size:14px;cursor:pointer;animation:pop .25s ease}",
    ".right .teaser{right:0}.left .teaser{left:0}",
    ".teaser .x{position:absolute;top:6px;right:6px;width:24px;height:24px;border:0;background:none;color:#8a8a85;padding:3px}",
    ".panel{position:absolute;bottom:76px;width:380px;height:min(620px,calc(100vh - 110px));background:#fff;border-radius:18px;box-shadow:0 24px 60px -16px rgba(0,0,0,.45);display:flex;flex-direction:column;overflow:hidden;animation:pop .2s ease}",
    ".right .panel{right:0}.left .panel{left:0}",
    "[hidden]{display:none!important}",
    "@keyframes pop{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}",
    ".head{display:flex;align-items:center;gap:12px;padding:14px 14px 14px 16px;background:var(--aic-accent);color:var(--aic-on)}",
    ".avatar{width:40px;height:40px;border-radius:50%;background:rgba(255,255,255,.18);display:grid;place-items:center;font-weight:700;font-size:14px;flex:none}",
    ".head .t{flex:1;min-width:0}.head b{display:block;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".head small{display:flex;align-items:center;gap:6px;opacity:.85;font-size:12.5px}",
    ".head small:before{content:'';width:7px;height:7px;border-radius:50%;background:#5ee08a}",
    ".icon{width:36px;height:36px;border:0;background:none;border-radius:10px;color:inherit;padding:7px;flex:none}",
    ".icon:hover{background:rgba(255,255,255,.15)}",
    ".log{flex:1;overflow-y:auto;padding:16px 14px 8px;display:flex;flex-direction:column;gap:10px;background:#f6f6f3;overscroll-behavior:contain}",
    ".msg{max-width:85%;padding:10px 13px;border-radius:16px;white-space:pre-wrap;word-wrap:break-word;unicode-bidi:plaintext}",
    ".bot{align-self:flex-start;background:#fff;border:1px solid #e8e7e2;border-bottom-left-radius:6px}",
    ".user{align-self:flex-end;background:var(--aic-accent);color:var(--aic-on);border-bottom-right-radius:6px}",
    "[dir=rtl] .bot{border-bottom-left-radius:16px;border-bottom-right-radius:6px}",
    "[dir=rtl] .user{border-bottom-right-radius:16px;border-bottom-left-radius:6px}",
    ".msg a{color:inherit;text-decoration:underline}",
    ".typing{display:flex;gap:4px;padding:14px}",
    ".typing i{width:7px;height:7px;border-radius:50%;background:#a3a29c;animation:blink 1.2s infinite}",
    ".typing i:nth-child(2){animation-delay:.2s}.typing i:nth-child(3){animation-delay:.4s}",
    "@keyframes blink{0%,80%,100%{opacity:.3}40%{opacity:1}}",
    ".chips{display:flex;flex-wrap:wrap;gap:8px;padding:2px 0 4px}",
    ".chip{border:1px solid var(--aic-accent);color:var(--aic-accent);background:#fff;border-radius:999px;padding:7px 12px;font-size:13.5px;line-height:1.2}",
    ".chip:hover{background:var(--aic-accent);color:var(--aic-on)}",
    ".card{align-self:stretch;background:#fff;border:1px solid #e8e7e2;border-radius:16px;padding:14px;display:grid;gap:10px}",
    ".card p{margin:0;font-weight:600;font-size:14.5px}",
    ".card label{display:grid;gap:4px;font-size:13px;color:#57564f}",
    ".card input[type=text],.card input[type=tel],.card textarea{font:inherit;font-size:15px;color:#1d1d1b;border:1.5px solid #dddcd5;border-radius:10px;padding:9px 11px;width:100%;background:#fbfbf9}",
    ".card textarea{min-height:56px;resize:vertical}",
    ".card input:focus,.card textarea:focus{outline:none;border-color:var(--aic-accent);background:#fff}",
    ".card input[type=tel]{direction:ltr}",
    "[dir=rtl] .card input[type=tel]{text-align:right}",
    ".card .consent{display:flex;gap:8px;align-items:flex-start;color:#57564f;font-size:12.5px}",
    ".card .consent input{margin-top:2px;width:16px;height:16px;accent-color:var(--aic-accent);flex:none}",
    ".card .consent a{color:inherit}",
    ".card .err{color:#c4320a;font-size:12.5px;min-height:1em}",
    ".primary{border:0;border-radius:12px;padding:11px 14px;background:var(--aic-accent);color:var(--aic-on);font-weight:600}",
    ".primary[disabled]{opacity:.6;cursor:progress}",
    ".done{align-self:stretch;background:#eaf6ee;color:#1b5e33;border-radius:14px;padding:12px 14px;font-weight:600}",
    ".foot{border-top:1px solid #ecebe6;background:#fff;padding:10px 12px 8px}",
    ".composer{display:flex;gap:8px;align-items:flex-end}",
    ".composer textarea{flex:1;font:inherit;font-size:15px;color:#1d1d1b;border:1.5px solid #dddcd5;border-radius:14px;padding:10px 12px;resize:none;max-height:120px;min-height:44px;line-height:1.35;background:#fbfbf9}",
    ".composer textarea:focus{outline:none;border-color:var(--aic-accent);background:#fff}",
    ".send{width:44px;height:44px;border-radius:50%;border:0;background:var(--aic-accent);color:var(--aic-on);padding:11px;flex:none}",
    ".send[disabled]{opacity:.5;cursor:default}",
    ".cta{display:block;margin:8px auto 0;border:0;background:none;color:var(--aic-accent);font-size:13px;font-weight:600;padding:2px 6px}",
    ".note{text-align:center;color:#9a998f;font-size:11px;margin-top:4px}",
    "svg{display:block;width:100%;height:100%}",
    ":focus-visible{outline:2px solid var(--aic-accent);outline-offset:2px}",
    "@media (max-width:520px){.root{bottom:14px}.root.right{right:14px}.root.left{left:14px}",
    ".panel{position:fixed;inset:0;width:auto;height:auto;border-radius:0;bottom:0}",
    ".teaser{width:min(260px,calc(100vw - 100px))}",
    ".root.open .launcher,.root.open .teaser{display:none}",
    ".composer textarea,.card input[type=text],.card input[type=tel],.card textarea{font-size:16px}}",
    "@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}"
  ].join("\n");

  // ---------- state ----------
  var state = {
    cfg: null,
    session: sessionId(),
    lang: load("lang", null),
    messages: load("messages", []),     // [{role:"bot"|"user", text}]
    formShown: load("formShown", false),
    leadSent: load("leadSent", false),
    teaserDismissed: load("teaserDismissed", false),
    busy: false
  };
  if (LANGS.indexOf(state.lang) < 0) {
    var guess = (document.documentElement.lang || navigator.language || "ru").slice(0, 2).toLowerCase();
    state.lang = LANGS.indexOf(guess) >= 0 ? guess : "ru";
  }
  function persist() {
    save("messages", state.messages.slice(-60)); save("lang", state.lang);
    save("formShown", state.formShown); save("leadSent", state.leadSent);
  }
  function t(key) { return pick(state.cfg[key], state.lang); }
  function ft(key) { var f = state.cfg.form || {}; return (f[state.lang] || f.ru || {})[key] || ""; }

  // ---------- UI ----------
  var host = el("div", { id: "ai-consultant", "data-site": SITE });
  var shadow = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
  var ui = {};

  function build(cfg) {
    state.cfg = cfg;
    var accent = /^#[0-9a-f]{6}$/i.test(cfg.accent || "") ? cfg.accent : "#2f6b4f";
    host.style.setProperty("--aic-accent", accent);
    host.style.setProperty("--aic-on", contrastOn(accent));
    if (cfg.font) host.style.setProperty("--aic-font", cfg.font);

    var style = el("style", { text: CSS });
    ui.root = el("div", { class: "root " + (cfg.position === "left" ? "left" : "right") });
    ui.launcher = el("button", { class: "launcher", type: "button", "aria-label": cfg.title || "Chat",
      "aria-expanded": "false", onclick: toggle });
    ui.launcher.innerHTML = ICON_CHAT;
    ui.dot = el("span", { class: "dot", hidden: state.messages.length > 0 });
    ui.launcher.appendChild(ui.dot);

    ui.teaser = el("div", { class: "teaser", hidden: true, role: "button", tabindex: "0", onclick: open,
      onkeydown: function (e) { if (e.key === "Enter") open(); } });
    ui.teaserText = el("span");
    var tx = el("button", { class: "x", type: "button", "aria-label": "×", onclick: function (e) {
      e.stopPropagation(); ui.teaser.hidden = true; state.teaserDismissed = true; save("teaserDismissed", true);
    } });
    tx.innerHTML = ICON_CLOSE;
    ui.teaser.appendChild(ui.teaserText); ui.teaser.appendChild(tx);

    ui.panel = el("div", { class: "panel", role: "dialog", "aria-label": cfg.title || "Chat", hidden: true });
    ui.title = el("b", { text: cfg.title || "" });
    ui.subtitle = el("small");
    var close = ui.close = el("button", { class: "icon", type: "button", onclick: closePanel });
    close.innerHTML = ICON_CLOSE;
    var head = el("div", { class: "head" }, [
      el("div", { class: "avatar", "aria-hidden": "true", text: cfg.avatar || (cfg.title || "AI").slice(0, 2) }),
      el("div", { class: "t" }, [ui.title, ui.subtitle]), close]);
    ui.log = el("div", { class: "log", role: "log", "aria-live": "polite" });
    ui.input = el("textarea", { rows: "1", maxlength: "1000", "aria-label": "Message", onkeydown: function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitText(); }
    }, oninput: autosize });
    ui.send = el("button", { class: "send", type: "submit", "aria-label": "Send" });
    ui.send.innerHTML = ICON_SEND;
    ui.composer = el("form", { class: "composer", onsubmit: function (e) { e.preventDefault(); submitText(); } },
      [ui.input, ui.send]);
    ui.cta = el("button", { class: "cta", type: "button", onclick: function () { showForm(true); } });
    ui.note = el("div", { class: "note" });
    var foot = el("div", { class: "foot" }, [ui.composer, ui.cta, ui.note]);
    ui.panel.appendChild(head); ui.panel.appendChild(ui.log); ui.panel.appendChild(foot);

    ui.root.appendChild(ui.panel); ui.root.appendChild(ui.teaser); ui.root.appendChild(ui.launcher);
    shadow.appendChild(style); shadow.appendChild(ui.root);
    document.body.appendChild(host);
    applyLang();
    render();

    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !ui.panel.hidden) closePanel(); });
    if (!state.teaserDismissed && !state.messages.length && pick(cfg.teaser, state.lang)) {
      setTimeout(function () { if (ui.panel.hidden) ui.teaser.hidden = false; }, 6000);
    }
  }

  function applyLang() {
    ui.panel.setAttribute("dir", state.lang === "ar" ? "rtl" : "ltr");
    ui.teaser.setAttribute("dir", state.lang === "ar" ? "rtl" : "ltr");
    ui.subtitle.textContent = t("subtitle");
    ui.close.setAttribute("aria-label", { ru: "Закрыть чат", en: "Close chat", ar: "إغلاق المحادثة" }[state.lang]);
    ui.teaserText.textContent = t("teaser");
    ui.input.setAttribute("placeholder", t("placeholder"));
    ui.cta.textContent = state.leadSent ? "" : t("lead_cta");
    ui.cta.hidden = state.leadSent;
    ui.note.textContent = { ru: "Отвечает нейросеть. Точные условия подтвердит менеджер.",
      en: "Answers are AI-generated. A manager confirms exact terms.",
      ar: "الإجابات من الذكاء الاصطناعي، ويؤكد المدير التفاصيل." }[state.lang];
  }

  function autosize() {
    ui.input.style.height = "auto";
    ui.input.style.height = Math.min(ui.input.scrollHeight, 120) + "px";
  }
  function scrollDown() { ui.log.scrollTop = ui.log.scrollHeight; }

  function bubble(role, text) {
    var b = el("div", { class: "msg " + role, dir: "auto" });
    b.appendChild(role === "bot" ? linkify(text) : document.createTextNode(text));
    return b;
  }

  function render() {
    ui.log.textContent = "";
    state.messages.forEach(function (m) { ui.log.appendChild(bubble(m.role, m.text)); });
    if (state.messages.length && !state.messages.some(function (m) { return m.role === "user"; })) renderChips();
    if (state.formShown && !state.leadSent) ui.log.appendChild(formCard());
    if (state.leadSent) ui.log.appendChild(el("div", { class: "done", dir: "auto", text: t("lead_done") }));
    scrollDown();
  }

  function renderChips() {
    var list = pick(state.cfg.quick_replies, state.lang);
    if (!list || !list.length) return;
    var box = el("div", { class: "chips" });
    list.forEach(function (q) {
      box.appendChild(el("button", { class: "chip", type: "button", text: q, onclick: function () { sendMessage(q); } }));
    });
    ui.log.appendChild(box);
  }

  function open() {
    ui.panel.hidden = false; ui.teaser.hidden = true; ui.dot.hidden = true; ui.root.classList.add("open");
    ui.launcher.setAttribute("aria-expanded", "true");
    state.teaserDismissed = true; save("teaserDismissed", true);
    if (!state.messages.length) {
      state.messages.push({ role: "bot", text: t("greeting") }); persist(); render();
    }
    scrollDown();
    if (wide()) setTimeout(function () { ui.input.focus(); }, 50);
  }
  function wide() { return window.matchMedia("(min-width: 521px)").matches; }
  function closePanel() {
    ui.panel.hidden = true; ui.root.classList.remove("open");
    ui.launcher.setAttribute("aria-expanded", "false"); ui.launcher.focus();
  }
  function toggle() { if (ui.panel.hidden) open(); else closePanel(); }

  function setBusy(busy) {
    state.busy = busy; ui.send.disabled = busy; ui.input.readOnly = busy;
  }

  function submitText() {
    var text = ui.input.value.trim();
    if (!text || state.busy) return;
    ui.input.value = ""; autosize();
    sendMessage(text);
  }

  function sendMessage(text) {
    if (state.busy) return;
    var chips = ui.log.querySelector(".chips"); if (chips) chips.remove();
    state.messages.push({ role: "user", text: text }); persist();
    var card = ui.log.querySelector(".card");
    ui.log.insertBefore(bubble("user", text), card || null);
    var typing = el("div", { class: "msg bot typing", "aria-label": "…" }, [el("i"), el("i"), el("i")]);
    ui.log.appendChild(typing); scrollDown(); setBusy(true);

    post("/api/chat", { site: SITE, session: state.session, message: text, lang: state.lang, page: location.href })
      .then(function (data) {
        if (data.lang && LANGS.indexOf(data.lang) >= 0 && data.lang !== state.lang) { state.lang = data.lang; applyLang(); }
        state.messages.push({ role: "bot", text: data.reply || "" });
        if (data.show_form && !state.leadSent) state.formShown = true;
      })
      .catch(function () {
        state.messages.push({ role: "bot", text: ft("error_send") || "…" });
        if (!state.leadSent) state.formShown = true;
      })
      .then(function () {
        typing.remove(); setBusy(false); persist(); render();
        if (!ui.panel.hidden && wide()) ui.input.focus();
      });
  }

  function showForm(focus) {
    if (state.leadSent) return;
    state.formShown = true; persist(); render();
    if (focus) { var name = ui.log.querySelector(".card input"); if (name) name.focus(); }
  }

  function formCard() {
    var err = el("div", { class: "err", role: "alert" });
    var name = el("input", { type: "text", name: "name", autocomplete: "name", maxlength: "80" });
    var phone = el("input", { type: "tel", name: "phone", autocomplete: "tel", inputmode: "tel", maxlength: "40",
      placeholder: state.lang === "ru" ? "+7 900 000-00-00" : "+" });
    var consent = el("input", { type: "checkbox", name: "consent" });
    var privacy = state.cfg.privacy_url
      ? el("a", { href: state.cfg.privacy_url, target: "_blank", rel: "noopener", text: ft("privacy") }) : null;
    var submit = el("button", { class: "primary", type: "submit", text: ft("submit") });
    var form = el("form", { class: "card", novalidate: true, onsubmit: function (e) {
      e.preventDefault();
      var digits = phone.value.replace(/\D/g, "");
      if (name.value.trim().length < 2) { err.textContent = ft("error_name"); name.focus(); return; }
      if (digits.length < 9 || digits.length > 15) { err.textContent = ft("error_phone"); phone.focus(); return; }
      if (!consent.checked) { err.textContent = ft("error_consent"); consent.focus(); return; }
      err.textContent = ""; submit.disabled = true;
      post("/api/lead", { site: SITE, session: state.session, name: name.value.trim(), phone: phone.value.trim(),
        consent: true, lang: state.lang, page: location.href })
        .then(function (data) {
          state.leadSent = true; state.formShown = false;
          if (data.message) state.cfg.lead_done = data.message;
          persist(); applyLang(); render();
        })
        .catch(function () { err.textContent = ft("error_send"); submit.disabled = false; });
    } }, [
      el("p", { text: ft("title") }),
      el("label", {}, [ft("name"), name]),
      el("label", {}, [ft("phone"), phone]),
      el("label", { class: "consent" }, [consent, el("span", {}, [ft("consent") + (privacy ? " · " : ""), privacy])]),
      err, submit
    ]);
    return form;
  }

  function start() {
    fetch(API + "/api/sites/" + encodeURIComponent(SITE) + "/config")
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(build)
      .catch(function (e) { console.warn("[ai-consultant] widget disabled:", e.message); });
  }
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);

  window.AIConsultant = { open: function () { if (ui.panel) open(); }, close: function () { if (ui.panel) closePanel(); } };
})();
