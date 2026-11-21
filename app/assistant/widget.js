/*
 * SoftuneBD storefront assistant: the chat bubble on a merchant's store.
 *
 * One plain script, served by the API at /public/assistant/widget.js, loaded
 * lazily by every theme. It is a single file on purpose: a fix here reaches
 * every store on every theme at once, and no theme needs rebuilding.
 *
 * It draws inside a shadow root, so a theme's CSS cannot reach in and the
 * widget's CSS cannot leak out. Everything from the API is written with
 * textContent, never innerHTML, so a merchant's or a shopper's text can never
 * run as markup.
 *
 * Loaded as:  <script src=".../widget.js" data-host="shop.example.com" data-api="https://api...">
 */
(function () {
  "use strict";
  if (window.__softuneAssistant) return;
  var tag = document.currentScript || document.querySelector('script[src*="assistant/widget.js"]');
  var host = tag && tag.getAttribute("data-host");
  var api = tag && (tag.getAttribute("data-api") || "").replace(/\/$/, "");
  if (!host || !api) return;
  window.__softuneAssistant = true;

  // z-index 45 on purpose: above page content, but BELOW every theme popup,
  // drawer and header (layer 50 and up). At the old maximum value the launcher
  // sat on top of the sale popup's close button and blocked it.
  // Brand logos live with the landing site; a missing one just hides itself.
  var ASSETS = "https://www.softunebd.com/icons/";
  var base = api + "/public/site/" + encodeURIComponent(host) + "/assistant";

  var lang = /^bn/i.test(document.documentElement.lang || navigator.language || "") ? "bn" : "en";
  var UI = {
    en: { ask: "Ask", talk: "Talk to us", input: "Type your question", send: "Send", close: "Close", open: "Open chat",
      hello: "Hi, I'm {n}! Ask me anything, or start here:", from: "From", viewAll: "View", soon: "Typing",
      whatsapp: "WhatsApp", messenger: "Messenger", instagram: "Instagram", phone: "Call us", email: "Email us",
      wa_note: "Fastest, usually within minutes", ms_note: "Message our Facebook page", ig_note: "DM us on Instagram",
      ph_note: "Speak to the team", em_note: "Write to us" },
    bn: { ask: "জিজ্ঞেস করুন", talk: "কথা বলুন", input: "আপনার প্রশ্ন লিখুন", send: "পাঠান", close: "বন্ধ", open: "চ্যাট খুলুন",
      hello: "হ্যালো, আমি {n}! যা জানতে চান জিজ্ঞেস করুন, অথবা এখান থেকে শুরু করুন:", from: "সূত্র", viewAll: "দেখুন", soon: "লিখছে",
      whatsapp: "WhatsApp", messenger: "Messenger", instagram: "Instagram", phone: "কল করুন", email: "ইমেইল করুন",
      wa_note: "সবচেয়ে দ্রুত, সাধারণত কয়েক মিনিটে", ms_note: "আমাদের ফেসবুক পেজে মেসেজ দিন", ig_note: "Instagram-এ DM করুন",
      ph_note: "টিমের সাথে কথা বলুন", em_note: "আমাদের লিখুন" },
  }[lang];

  function h(name, attrs, kids) {
    var el = document.createElement(name);
    for (var k in attrs || {}) {
      if (k === "class") el.className = attrs[k];
      else if (k === "text") el.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), attrs[k]);
      else el.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(function (c) { if (c) el.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return el;
  }
  function svg(markup, size) {
    var w = document.createElement("span");
    w.innerHTML = markup; // static markup written in this file, never data
    var s = w.firstChild;
    if (size) { s.setAttribute("width", size); s.setAttribute("height", size); }
    return s;
  }
  function taka(c) {
    var whole = Math.floor(c / 100), rest = c % 100;
    return "৳" + whole.toLocaleString("en-US") + (rest ? "." + (rest < 10 ? "0" : "") + rest : "");
  }

  // ---- avatars (same six as the dashboard gallery; colour-agnostic) --------
  var INK = "#1F1B2E";
  var FACES = {
    bot: '<line x1="20" y1="7.5" x2="20" y2="11" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/><circle cx="20" cy="6.5" r="1.9" fill="#fff" opacity=".9"/><rect x="9.5" y="11" width="21" height="17" rx="6.5" fill="#fff"/><rect x="7" y="17" width="2.5" height="5.5" rx="1.25" fill="#fff" opacity=".85"/><rect x="30.5" y="17" width="2.5" height="5.5" rx="1.25" fill="#fff" opacity=".85"/><rect x="12.5" y="14" width="15" height="10" rx="4.5" fill="' + INK + '"/><circle cx="16.8" cy="18.4" r="1.7" fill="#5EEAD4"/><circle cx="23.2" cy="18.4" r="1.7" fill="#5EEAD4"/><path d="M17.6 21.4Q20 23.1 22.4 21.4" stroke="#5EEAD4" stroke-width="1.3" fill="none" stroke-linecap="round"/><rect x="15" y="29.5" width="10" height="3.5" rx="1.75" fill="#fff" opacity=".9"/>',
    spark: '<path d="M20 7L23.2 16.8L33 20L23.2 23.2L20 33L16.8 23.2L7 20L16.8 16.8Z" fill="#fff"/><circle cx="29.5" cy="10.5" r="2.2" fill="#fff" opacity=".85"/><circle cx="10.5" cy="29.5" r="1.6" fill="#fff" opacity=".7"/>',
    bubble: '<path d="M9.5 12.5a4.5 4.5 0 0 1 4.5-4.5h12a4.5 4.5 0 0 1 4.5 4.5v8a4.5 4.5 0 0 1-4.5 4.5h-7.5l-6 5v-5a4.5 4.5 0 0 1-3-4.5z" fill="#fff"/><circle cx="15" cy="16.5" r="1.7" fill="var(--a)"/><circle cx="20" cy="16.5" r="1.7" fill="var(--a)"/><circle cx="25" cy="16.5" r="1.7" fill="var(--a)"/>',
    buddy: '<path d="M10.5 21a9.5 9.5 0 0 1 19 0" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/><rect x="8" y="19.5" width="4" height="7" rx="2" fill="#fff"/><rect x="28" y="19.5" width="4" height="7" rx="2" fill="#fff"/><circle cx="20" cy="22" r="8" fill="#fff"/><circle cx="16.8" cy="21" r="1.5" fill="' + INK + '"/><circle cx="23.2" cy="21" r="1.5" fill="' + INK + '"/><path d="M16.6 24.6Q20 27.4 23.4 24.6" stroke="' + INK + '" stroke-width="1.5" fill="none" stroke-linecap="round"/>',
    owl: '<path d="M11 12L15 15M29 12L25 15" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/><rect x="10" y="13" width="20" height="19" rx="9" fill="#fff"/><circle cx="16" cy="20" r="4.4" fill="var(--a)" opacity=".35"/><circle cx="24" cy="20" r="4.4" fill="var(--a)" opacity=".35"/><circle cx="16" cy="20" r="2" fill="' + INK + '"/><circle cx="24" cy="20" r="2" fill="' + INK + '"/><path d="M18.4 24L20 26.6L21.6 24Z" fill="var(--a)"/>',
    cat: '<path d="M9.5 20L10.5 9.5L18 14.5ZM30.5 20L29.5 9.5L22 14.5Z" fill="#fff"/><ellipse cx="20" cy="22" rx="11.5" ry="10" fill="#fff"/><ellipse cx="15.8" cy="21" rx="1.6" ry="2" fill="' + INK + '"/><ellipse cx="24.2" cy="21" rx="1.6" ry="2" fill="' + INK + '"/><path d="M18.6 24.6L21.4 24.6L20 26Z" fill="var(--a)"/><path d="M20 26Q18.6 28 17 27.2M20 26Q21.4 28 23 27.2" stroke="' + INK + '" stroke-width="1.1" fill="none" stroke-linecap="round"/>',
  };
  function avatar(id, size) {
    if (/^https:\/\//.test(id || "")) {
      var img = h("img", { src: id, alt: "", class: "av", width: size, height: size });
      img.style.width = img.style.height = size + "px";
      return img;
    }
    var face = FACES[id] || FACES.bot;
    var el = svg('<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="20" fill="var(--a)"/><circle cx="20" cy="20" r="20" fill="url(#g)"/>' + face + '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".22"/><stop offset="1" stop-color="#000" stop-opacity=".16"/></linearGradient></defs></svg>', size);
    el.setAttribute("class", "av");
    return el;
  }

  var CSS = [
    ":host{all:initial;font-family:inherit}",
    "*{box-sizing:border-box;font-family:inherit}",
    ".root{position:fixed;bottom:16px;z-index:45;display:flex;flex-direction:column;gap:12px;align-items:flex-end;--a:#E8431A;color:#111827;font-size:14px;line-height:1.45}",
    ".root.left{left:16px;align-items:flex-start}.root.right{right:16px}",
    ".av{flex:none;border-radius:50%;object-fit:cover;display:block}",
    ".launch{display:flex;align-items:center;gap:10px;height:56px;padding:0 20px 0 8px;border:0;border-radius:999px;background:var(--a);color:#fff;font:600 14px inherit;cursor:pointer;box-shadow:0 14px 32px -10px rgba(0,0,0,.45);transition:transform .15s}",
    ".launch:hover{transform:scale(1.03)}.launch:active{transform:scale(.96)}",
    ".launch .av{box-shadow:0 0 0 2px rgba(255,255,255,.7)}",
    ".dot{position:absolute;right:2px;bottom:2px;width:12px;height:12px;border-radius:50%;background:#22C55E;box-shadow:0 0 0 2px #fff}",
    ".avwrap{position:relative;display:flex}",
    ".panel{width:380px;max-width:calc(100vw - 24px);height:min(560px,calc(100dvh - 104px));display:flex;flex-direction:column;background:#fff;border-radius:22px;overflow:hidden;box-shadow:0 30px 80px -20px rgba(0,0,0,.45),0 0 0 1px rgba(0,0,0,.08);transform-origin:bottom right;animation:in .2s cubic-bezier(.22,1,.36,1)}",
    ".root.left .panel{transform-origin:bottom left}",
    "@keyframes in{from{opacity:0;transform:translateY(14px) scale(.98)}to{opacity:1;transform:none}}",
    ".tabs{display:grid;grid-template-columns:1fr 1fr;gap:4px;margin:10px;padding:4px;border-radius:16px;background:#F3F4F6}",
    ".tab{display:flex;align-items:center;justify-content:center;gap:6px;height:36px;border:0;border-radius:12px;background:transparent;color:#6B7280;font:600 13px inherit;cursor:pointer}",
    ".tab[aria-selected=true]{background:#111827;color:#fff}",
    ".body{flex:1;overflow-y:auto;padding:4px 14px 14px;display:flex;flex-direction:column;gap:14px}",
    ".row{display:flex;gap:8px;align-items:flex-start}.row>div{min-width:0;flex:1;display:flex;flex-direction:column;gap:8px;align-items:flex-start}",
    ".bub{max-width:100%;padding:9px 13px;border-radius:16px 16px 16px 5px;background:#F3F4F6;box-shadow:inset 0 0 0 1px #E5E7EB;white-space:pre-line;overflow-wrap:anywhere}",
    ".src{display:block;margin-top:7px;padding-top:5px;border-top:1px solid #E5E7EB;color:#6B7280;font-size:10.5px;font-weight:600;letter-spacing:.04em;text-transform:uppercase}",
    ".me{align-self:flex-end;max-width:80%;padding:8px 13px;border-radius:16px 16px 5px 16px;background:var(--a);color:#fff;overflow-wrap:anywhere}",
    ".list{width:100%;border-radius:14px;overflow:hidden;box-shadow:inset 0 0 0 1px #E5E7EB}",
    ".it{display:flex;align-items:center;gap:10px;width:100%;padding:9px 12px;border:0;border-top:1px solid #E5E7EB;background:#fff;text-align:left;font:13px inherit;color:#111827;cursor:pointer}",
    ".it:first-child{border-top:0}.it:hover{background:#F9FAFB}",
    ".ico{display:flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:50%;box-shadow:inset 0 0 0 1px #E5E7EB;color:#6B7280;flex:none}",
    ".chips{display:flex;flex-wrap:wrap;gap:6px}",
    ".chip{padding:4px 12px;border:0;border-radius:999px;background:#fff;box-shadow:inset 0 0 0 1px #E5E7EB;color:#6B7280;font:12px inherit;cursor:pointer}",
    ".chip:hover{color:var(--a);box-shadow:inset 0 0 0 1px var(--a)}",
    ".btns{display:flex;flex-wrap:wrap;gap:6px}",
    ".btn{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:999px;background:var(--a);color:#fff;font:600 12.5px inherit;text-decoration:none}",
    ".cards{display:flex;gap:8px;overflow-x:auto;width:100%;padding-bottom:2px}",
    ".card{flex:none;width:132px;border-radius:14px;overflow:hidden;text-decoration:none;color:inherit;box-shadow:inset 0 0 0 1px #E5E7EB;background:#fff}",
    ".card img{display:block;width:100%;aspect-ratio:1/1;object-fit:cover;background:#F3F4F6}",
    ".card b{display:block;padding:6px 8px 0;font-size:12px;line-height:1.3;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}",
    ".card span{display:block;padding:2px 8px 8px;font-size:12.5px;font-weight:700;color:var(--a)}",
    ".card s{color:#9CA3AF;font-weight:400;margin-left:4px;font-size:11px}",
    ".logos{display:flex;gap:8px}.logo{display:flex;align-items:center;justify-content:center;width:42px;height:42px;border-radius:14px;box-shadow:inset 0 0 0 1px #E5E7EB;transition:transform .15s}",
    ".logo:hover{transform:translateY(-2px)}.logo img{width:24px;height:24px;object-fit:contain}",
    ".typing{display:inline-flex;gap:4px;padding:11px 14px;border-radius:16px 16px 16px 5px;background:#F3F4F6;box-shadow:inset 0 0 0 1px #E5E7EB}",
    ".typing i{width:6px;height:6px;border-radius:50%;background:#9CA3AF;animation:p 1s infinite}.typing i:nth-child(2){animation-delay:.15s}.typing i:nth-child(3){animation-delay:.3s}",
    "@keyframes p{0%,100%{opacity:.3}50%{opacity:1}}",
    ".form{display:flex;gap:8px;padding:10px;border-top:1px solid #E5E7EB}",
    ".inp{flex:1;min-width:0;height:42px;padding:0 16px;border:0;border-radius:999px;background:#F3F4F6;box-shadow:inset 0 0 0 1px #E5E7EB;font:14px inherit;color:#111827;outline:none}",
    ".inp:focus{box-shadow:inset 0 0 0 2px var(--a);background:#fff}",
    ".send{flex:none;width:42px;height:42px;border:0;border-radius:50%;background:var(--a);color:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center}.send:disabled{opacity:.4;cursor:default}",
    ".chan{display:flex;align-items:center;gap:12px;padding:11px;border-radius:16px;background:#F9FAFB;box-shadow:inset 0 0 0 1px #E5E7EB;text-decoration:none;color:inherit}",
    ".chan:hover{box-shadow:inset 0 0 0 1px var(--a)}",
    ".chan .t{flex:1;min-width:0}.chan b{display:block;font-size:14.5px}.chan small{display:block;color:#6B7280;font-size:12.5px}",
    ".chan .tile{display:flex;align-items:center;justify-content:center;width:44px;height:44px;border-radius:12px;flex:none}.tile img{width:24px;height:24px;object-fit:contain}",
    "@media (max-width:520px){.root{bottom:12px}.root.right{right:12px}.root.left{left:12px}.panel{width:calc(100vw - 24px);height:min(78dvh,calc(100dvh - 96px))}.launch .lt{display:none}.launch{padding:0 8px;width:56px;justify-content:center}}",
    "@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}",
  ].join("\n");

  var ICON_SEND = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
  var ICON_ARROW = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17L17 7M8 7h9v9"/></svg>';
  var ICON_CHAT = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.4A8 8 0 1 1 21 12z"/></svg>';
  var ICON_PHONE = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#111827" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/></svg>';
  var ICON_MAIL = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#111827" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>';

  function channelDefs() {
    return {
      whatsapp: { name: UI.whatsapp, note: UI.wa_note, tint: "#25D366", img: ASSETS + "whatsapp.svg" },
      messenger: { name: UI.messenger, note: UI.ms_note, tint: "#0866FF", img: ASSETS + "facebook.webp" },
      instagram: { name: UI.instagram, note: UI.ig_note, tint: "#E1306C", img: ASSETS + "insta.webp" },
      phone: { name: UI.phone, note: UI.ph_note, tint: "#111827", icon: ICON_PHONE },
      email: { name: UI.email, note: UI.em_note, tint: "#111827", icon: ICON_MAIL },
    };
  }
  var ORDER = ["whatsapp", "messenger", "instagram", "phone", "email"];

  function logo(kind, url, big) {
    var d = channelDefs()[kind];
    var tile = h("span", { class: big ? "tile" : "logo" });
    tile.style.background = d.tint + "1A";
    if (d.img) {
      var img = h("img", { src: d.img, alt: "" });
      img.addEventListener("error", function () { img.style.display = "none"; });
      tile.appendChild(img);
    } else tile.appendChild(svg(d.icon, 22));
    return tile;
  }

  function boot(cfg) {
    var accent = cfg.accent || (getComputedStyle(document.documentElement).getPropertyValue("--brand") || "").trim() || "#E8431A";
    var name = cfg.name || "Assistant";
    var greeting = cfg.greeting || UI.hello.replace("{n}", name);
    var starters = (cfg.starters && (cfg.starters[lang] || cfg.starters.en)) || [];
    var channels = cfg.channels || {};

    var hostEl = h("div", { id: "softune-assistant" });
    var root = hostEl.attachShadow({ mode: "open" });
    root.appendChild(h("style", { text: CSS }));
    var wrap = h("div", { class: "root " + (cfg.position === "left" ? "left" : "right") });
    wrap.style.setProperty("--a", accent);
    root.appendChild(wrap);

    var open = false, busy = false, tab = "ask", started = false;
    var panel = null;

    var launchText = h("span", { class: "lt", text: cfg.launcher_text || "Let's Talk" });
    var launch = h("button", { class: "launch", type: "button", "aria-expanded": "false", "aria-label": UI.open, onclick: toggle }, [
      h("span", { class: "avwrap" }, [avatar(cfg.avatar, 40), h("span", { class: "dot" })]), launchText,
    ]);

    function toggle() {
      open = !open;
      launch.setAttribute("aria-expanded", String(open));
      render();
      if (open) setTimeout(function () { var i = root.querySelector(".inp"); if (i) i.focus(); }, 60);
    }

    function botRow(kids) {
      return h("div", { class: "row" }, [avatar(cfg.avatar, 28), h("div", {}, kids)]);
    }
    function bubble(text, src) {
      var b = h("div", { class: "bub", text: text });
      if (src) b.appendChild(h("span", { class: "src", text: UI.from + " " + src }));
      return b;
    }

    var body = null, input = null, sendBtn = null;

    function scrollDown() { if (body) body.scrollTop = body.scrollHeight; }

    function ask(q) {
      q = (q || "").trim();
      if (!q || busy) return;
      busy = true; if (sendBtn) sendBtn.disabled = true;
      if (input) input.value = "";
      body.appendChild(h("div", { class: "me", text: q }));
      var typing = botRow([h("div", { class: "typing", "aria-label": UI.soon }, [h("i"), h("i"), h("i")])]);
      body.appendChild(typing); scrollDown();
      var t0 = Date.now();
      fetch(base + "/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q }) })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (a) { return new Promise(function (res) { setTimeout(function () { res(a); }, Math.max(0, 500 - (Date.now() - t0))); }); })
        .then(function (a) { typing.remove(); body.appendChild(reply(a)); })
        .catch(function () {
          typing.remove();
          body.appendChild(reply({ answer: lang === "bn" ? "দুঃখিত, এখন উত্তর দিতে পারছি না। একটু পরে আবার চেষ্টা করুন।" : "Sorry, I couldn't answer just now. Please try again in a moment.", kind: "fallback", contact: channels, products: [], actions: [], followups: [] }));
        })
        .then(function () { busy = false; if (sendBtn) sendBtn.disabled = !(input && input.value.trim()); scrollDown(); });
    }

    function reply(a) {
      var kids = [bubble(a.answer, a.source)];
      if (a.products && a.products.length) {
        var cards = h("div", { class: "cards" });
        a.products.forEach(function (p) {
          var priceEl = h("span", { text: taka(p.price_cents) });
          if (p.compare_at_cents && p.compare_at_cents > p.price_cents) priceEl.appendChild(h("s", { text: taka(p.compare_at_cents) }));
          var c = h("a", { class: "card", href: "/shop/" + encodeURIComponent(p.slug) }, [
            p.image ? h("img", { src: p.image, alt: "", loading: "lazy" }) : h("div"), h("b", { text: p.name }), priceEl,
          ]);
          cards.appendChild(c);
        });
        kids.push(cards);
      }
      if (a.actions && a.actions.length) {
        var btns = h("div", { class: "btns" });
        a.actions.forEach(function (ac) {
          var link = h("a", { class: "btn", href: ac.path || ac.href || "#" }, [ac.label, svg(ICON_ARROW, 13)]);
          btns.appendChild(link);
        });
        kids.push(btns);
      }
      var cont = a.contact || {};
      var keys = ORDER.filter(function (k) { return cont[k]; });
      if (keys.length) {
        var logos = h("div", { class: "logos" });
        keys.forEach(function (k) {
          var l = h("a", { href: cont[k], target: k === "phone" || k === "email" ? "_self" : "_blank", rel: "noopener noreferrer", "aria-label": channelDefs()[k].name, title: channelDefs()[k].name }, [logo(k, cont[k], false)]);
          logos.appendChild(l);
        });
        kids.push(logos);
      }
      if (a.followups && a.followups.length) kids.push(chips(a.followups));
      return botRow(kids);
    }

    function chips(items) {
      var c = h("div", { class: "chips" });
      items.forEach(function (q) { c.appendChild(h("button", { class: "chip", type: "button", text: q, onclick: function () { ask(q); } })); });
      return c;
    }

    function renderAsk() {
      body = h("div", { class: "body" });
      var first = [bubble(greeting)];
      if (starters.length) {
        var list = h("div", { class: "list" });
        starters.forEach(function (q) {
          list.appendChild(h("button", { class: "it", type: "button", onclick: function () { ask(q); } }, [h("span", { class: "ico" }, [svg(ICON_CHAT, 14)]), h("span", { text: q })]));
        });
        first.push(list);
      }
      body.appendChild(botRow(first));
      input = h("input", { class: "inp", type: "text", maxlength: "300", placeholder: UI.input, "aria-label": UI.input, autocomplete: "off" });
      sendBtn = h("button", { class: "send", type: "submit", "aria-label": UI.send, disabled: "" }, [svg(ICON_SEND, 16)]);
      input.addEventListener("input", function () { sendBtn.disabled = busy || !input.value.trim(); });
      var form = h("form", { class: "form", onsubmit: function (e) { e.preventDefault(); ask(input.value); } }, [input, sendBtn]);
      return [body, form];
    }

    function renderTalk() {
      body = h("div", { class: "body" });
      var any = false;
      ORDER.forEach(function (k) {
        if (!channels[k]) return;
        any = true;
        var d = channelDefs()[k];
        body.appendChild(h("a", { class: "chan", href: channels[k], target: k === "phone" || k === "email" ? "_self" : "_blank", rel: "noopener noreferrer" }, [
          logo(k, channels[k], true), h("span", { class: "t" }, [h("b", { text: d.name }), h("small", { text: d.note })]), svg(ICON_ARROW, 14),
        ]));
      });
      if (!any) body.appendChild(h("p", { text: lang === "bn" ? "দোকানের যোগাযোগের তথ্য এখনো যোগ করা হয়নি।" : "This store hasn't added contact details yet." }));
      return [body];
    }

    function render() {
      if (panel) { panel.remove(); panel = null; }
      if (!open) return;
      var askTab = h("button", { class: "tab", role: "tab", type: "button", "aria-selected": String(tab === "ask"), onclick: function () { tab = "ask"; render(); } }, [avatar(cfg.avatar, 18), (UI.ask + " " + name).trim()]);
      var talkTab = h("button", { class: "tab", role: "tab", type: "button", "aria-selected": String(tab === "talk"), onclick: function () { tab = "talk"; render(); }, text: UI.talk });
      panel = h("div", { class: "panel", role: "dialog", "aria-label": name }, [
        h("div", { class: "tabs", role: "tablist" }, [askTab, talkTab]),
      ].concat(tab === "ask" ? renderAsk() : renderTalk()));
      wrap.insertBefore(panel, launch);
    }

    wrap.appendChild(launch);
    root.addEventListener("keydown", function (e) { if (e.key === "Escape" && open) toggle(); });
    document.body.appendChild(hostEl);
  }

  function start() {
    fetch(base, { method: "GET" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (cfg) { if (cfg && cfg.enabled) boot(cfg); })
      .catch(function () { /* the assistant is optional: fail silent, never break the store */ });
  }
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
})();
