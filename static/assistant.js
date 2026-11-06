/* Pulse0 assistant: Claude-backed chat (SSE streaming, tool chips, markdown-lite) with transparent
   fallback to the rule-based /api/investigate. Overrides P.openAsk (see core.js). No build step. */
(function () {
  const P = window.P, { el, icon, api, qs, store, toast } = P;
  const S = P.S, enc = encodeURIComponent;

  const TOOLS = {
    list_services: "services", service_health: "service health", search_traces: "traces", get_trace: "trace",
    search_logs: "logs", metric_names: "metrics", metric_query: "metrics", list_insights: "insights",
    list_alerts: "alerts", list_slos: "SLOs",
  };
  const chipText = (name, inp) => {
    const base = TOOLS[name] || name;
    const hint = name === "get_trace" && inp.trace_id ? String(inp.trace_id).slice(0, 8) : (inp.service || inp.name || "");
    return `${base}${hint ? " · " + hint : ""}`;
  };

  // ------------------------------------------------------------ state (kept while the page lives)
  const chat = { history: [], busy: false, abort: null, root: null, body: null, input: null, send: null, stop: null, badge: null, foot: null, svc: [], lastQ: "" };
  P.aiStatus = null;
  const loadStatus = () => api("/assistant/status").then(s => (P.aiStatus = s)).catch(() => P.aiStatus);
  loadStatus();
  const isClaude = () => !!(P.aiStatus && P.aiStatus.enabled);

  // ------------------------------------------------------------ markdown-lite -> DOM (never innerHTML)
  const escRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const isId = s => /^[0-9a-f]{32}$/.test(s);
  function goto(href) { return e => { e.preventDefault(); P.closeDrawer(); location.hash = href; }; }
  function ref(text, cls) {
    let href = null;
    if (isId(text)) href = "#/traces?id=" + text;
    else if (chat.svc.includes(text)) href = "#/services/" + enc(text);
    return href ? el("a", { class: "ref " + (cls || ""), href, title: isId(text) ? "Open trace" : "Open service", on: { click: goto(href) } }, text) : null;
  }
  function inline(s) {
    const out = [];
    const svc = chat.svc.length ? "|(?<![\\w.-])(" + [...chat.svc].sort((a, b) => b.length - a.length).map(escRe).join("|") + ")(?![\\w.-])" : "";
    const re = new RegExp("(`[^`\\n]+`)|(\\*\\*[^*\\n]+\\*\\*)|(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])" + svc, "g");
    let last = 0, m;
    while ((m = re.exec(s))) {
      if (m.index > last) out.push(s.slice(last, m.index));
      if (m[1]) { const t = m[1].slice(1, -1); out.push(ref(t, "code") || el("code", {}, t)); }
      else if (m[2]) out.push(el("strong", {}, ...inline(m[2].slice(2, -2))));
      else out.push(ref(m[3] || m[4]) || m[0]);
      last = m.index + m[0].length;
    }
    if (last < s.length) out.push(s.slice(last));
    return out;
  }
  function md(text) {
    const root = el("div", { class: "md" }), lines = String(text).replace(/\r/g, "").split("\n");
    let para = [], list = null, i = 0;
    const flush = () => {
      if (para.length) { root.append(el("p", {}, ...inline(para.join(" ")))); para = []; }
      if (list) { root.append(list.el); list = null; }
    };
    while (i < lines.length) {
      const ln = lines[i];
      if (/^\s*```/.test(ln)) {
        flush(); const code = []; i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
        i++; root.append(el("pre", { class: "code" }, code.join("\n"))); continue;
      }
      if (/^\s*\|.*\|\s*$/.test(ln)) {
        flush(); const rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
        root.append(el("pre", { class: "code" }, rows.join("\n"))); continue;
      }
      let m;
      if ((m = ln.match(/^\s{0,3}(#{1,4})\s+(.*)$/))) { flush(); root.append(el("div", { class: "mh" }, ...inline(m[2]))); }
      else if ((m = ln.match(/^\s*([-*•]|\d+[.)])\s+(.*)$/))) {
        const ord = /\d/.test(m[1]);
        if (!list || list.ord !== ord) { flush(); list = { ord, el: el(ord ? "ol" : "ul", {}) }; }
        list.el.append(el("li", {}, ...inline(m[2])));
      } else if (!ln.trim()) flush();
      else { if (list) flush(); para.push(ln.trim()); }
      i++;
    }
    flush();
    return root;
  }

  // ------------------------------------------------------------ bubbles
  const scroll = () => { chat.body.scrollTop = chat.body.scrollHeight; };
  const badge = (mode, model) => el("span", { class: "aibadge " + mode, title: mode === "claude" ? "Answered by Claude with read-only queries on your data" : "Keyword rules over your own telemetry" },
    icon(mode === "claude" ? "sparkle" : "info", 11), mode === "claude" ? "Claude" + (model ? " · " + model : "") : "rule-based");
  const suggs = list => el("div", { class: "sugg" }, ...list.map(s => el("button", { class: "chipbtn", on: { click: () => ask(s) } }, s)));

  async function welcome() {
    chat.svc = (await P.services(true).catch(() => [])).map(s => s.service);
    const top = chat.svc[0];
    const list = isClaude()
      ? ["What is failing right now?", top ? `Why are errors elevated on ${top}?` : "Summarize the last hour", "Which traces are slowest and why?", "Any suspicious error log patterns?", "Give me a health overview"]
      : ["What is failing right now?", "Which services are slow?", "Show top error logs", "Any resource saturation?", "Give me an overview"];
    const intro = isClaude()
      ? "I'm Claude, connected to this Pulse0. I query your traces, logs, metrics, alerts and SLOs (read-only) to answer. Try:"
      : "I analyse the traces, logs, metrics, alerts and SLOs stored in Pulse0 using built-in rules — no data leaves your cluster. Try:";
    chat.body.replaceChildren(el("div", { class: "bubble" }, el("div", { class: "bs" }, intro), el("div", { class: "bs" }, suggs(list))));
  }

  function rulesBubble(r, note) {
    return el("div", { class: "bubble ai" },
      el("div", { class: "bh" }, el("span", {}, r.title), badge("rules")),
      note ? el("div", { class: "bs note" }, note) : null,
      el("div", { class: "bs" }, r.summary),
      ...r.findings.map(f => el("div", { class: "insight " + (f.severity || ""), on: { click: () => { P.closeDrawer(); location.hash = f.link; } } },
        el("div", { class: "ic" }, icon(f.severity === "info" ? "info" : "warn")), el("div", {}, el("div", { class: "t" }, f.label), el("div", { class: "d" }, f.value)))),
      r.suggestions?.length ? el("div", { class: "bs" }, suggs(r.suggestions)) : null);
  }

  // Turn raw SDK/HTTP error text into a short reason for the fallback notice.
  const friendly = m => {
    const c = /Error code: (\d{3})/.exec(String(m)); const code = c ? +c[1] : 0;
    if (code === 401 || code === 403) return "the API key was rejected";
    if (code === 429) return "rate limited";
    if (code === 529 || code >= 500) return "the API is temporarily unavailable";
    if (/timeout|timed out|connection/i.test(String(m))) return "the API could not be reached";
    if (/not set/i.test(String(m))) return "no API key is configured";
    return String(m).slice(0, 120);
  };
  async function runRules(text, note) {
    const r = await api("/investigate?" + qs({ q_: text, minutes: S.minutes }));
    chat.body.append(rulesBubble(r, note)); scroll();
  }

  // ------------------------------------------------------------ Claude streaming
  async function streamClaude(history, onEv, signal) {
    const h = { "Content-Type": "application/json" }, tok = store.get("token", "");
    if (tok) h.Authorization = "Bearer " + tok;
    const r = await fetch("/api/assistant", { method: "POST", headers: h, signal, body: JSON.stringify({ messages: history, stream: true }) });
    if (r.status === 401) throw new P.AuthError("This Pulse0 requires an API token (Settings).");
    if (!r.ok) {
      let j = {}; try { j = await r.json(); } catch { /* keep */ }
      const e = new Error(j.message || (j.details && j.details.map(d => d.msg).join("; ")) || r.statusText); e.fallback = r.status === 503 || r.status === 502; throw e;
    }
    const rd = r.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await rd.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let k;
      while ((k = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, k); buf = buf.slice(k + 2);
        for (const line of chunk.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          let ev; try { ev = JSON.parse(line.slice(6)); } catch { continue; }   // skip malformed frames only
          onEv(ev);                                                               // handler errors must propagate
        }
      }
    }
  }

  function setBusy(b) {
    chat.busy = b; chat.send.hidden = b; chat.stop.hidden = !b; chat.input.disabled = b;
    if (!b) chat.input.focus();
  }

  async function ask(text) {
    text = String(text ?? chat.input.value).trim();
    if (!text || chat.busy) return;
    chat.input.value = ""; chat.input.style.height = ""; chat.lastQ = text;
    chat.body.append(el("div", { class: "me" }, text));
    setBusy(true);
    try {
      if (!isClaude()) { const w = el("div", { class: "muted think" }, "Investigating…"); chat.body.append(w); scroll(); try { await runRules(text); } finally { w.remove(); } return; }
      chat.history.push({ role: "user", content: text.slice(0, 4000) });
      const chips = el("div", { class: "chips" }), mdBox = el("div", { class: "md-wrap" });
      const think = el("div", { class: "muted think" }, "Thinking", el("span", { class: "dots" }));
      const bub = el("div", { class: "bubble ai" }, el("div", { class: "bh" }, el("span", {}, "Answer"), badge("claude", P.aiStatus.model)), chips, mdBox, think);
      chat.body.append(bub); scroll();
      let acc = "", raf = 0, finished = false;
      const paint = () => { raf = 0; mdBox.replaceChildren(md(acc)); scroll(); };
      const pending = {};
      chat.abort = new AbortController();
      try {
        await streamClaude(chat.history.slice(-12), ev => {
          if (ev.type === "tool") { think.textContent = "Querying your data…"; const c = el("span", { class: "tchip run" }, icon("search", 11), "querying " + chipText(ev.name, ev.input || {})); (pending[ev.name] ||= []).push(c); chips.append(c); scroll(); }
          else if (ev.type === "tool_result") { const c = (pending[ev.name] || []).shift(); if (c) { c.classList.remove("run"); c.classList.add(ev.ok ? "ok" : "bad"); c.firstChild.replaceWith(icon(ev.ok ? "check" : "x", 11)); c.lastChild.textContent = c.lastChild.textContent.replace("querying", ev.ok ? "queried" : "failed"); } }
          else if (ev.type === "text") { think.remove(); acc += ev.text; if (!raf) raf = requestAnimationFrame(paint); }
          else if (ev.type === "done") finished = true;
          else if (ev.type === "error") { const e = new Error(ev.message); e.fallback = true; throw e; }
        }, chat.abort.signal);
        if (raf) cancelAnimationFrame(raf); paint(); think.remove();
        if (finished && acc) chat.history.push({ role: "assistant", content: acc.slice(0, 4000) });
        else if (!acc) { bub.append(el("div", { class: "bs note" }, "Claude returned an empty answer.")); chat.history.pop(); }
      } catch (e) {
        if (raf) cancelAnimationFrame(raf); think.remove();
        if (e.name === "AbortError") {
          paint(); bub.append(el("div", { class: "bs note" }, "Stopped."));
          if (acc) chat.history.push({ role: "assistant", content: acc.slice(0, 4000) }); else chat.history.pop();
        } else if (e instanceof P.AuthError) { chat.history.pop(); bub.remove(); chat.body.append(errBubble(e.message, text)); }
        else if (e.fallback) {
          chat.history.pop(); bub.remove();
          try { await runRules(text, `Claude is unavailable (${friendly(e.message)}). Showing the rule-based analysis instead.`); }
          catch (e2) { chat.body.append(errBubble(e2.message, text)); }
        } else { chat.history.pop(); bub.remove(); chat.body.append(errBubble(e.message, text)); }
      } finally { chat.abort = null; }
    } catch (e) { chat.body.append(errBubble(e.message || String(e), text)); }
    finally { setBusy(false); scroll(); }
  }

  const errBubble = (msg, text) => el("div", { class: "bubble err" }, el("div", { class: "bs" }, icon("warn", 14), " ", msg),
    el("div", { class: "bs" }, el("button", { class: "btn sm", on: { click: e => { e.currentTarget.closest(".bubble").remove(); ask(text); } } }, "Try again")));

  // ------------------------------------------------------------ drawer
  function build() {
    chat.input = el("textarea", { rows: 1, placeholder: "Ask about your services, traces, logs…", autocomplete: "off", "aria-label": "Ask Pulse0" });
    chat.send = el("button", { class: "btn primary", on: { click: () => ask() } }, "Ask");
    chat.stop = el("button", { class: "btn danger", hidden: true, on: { click: () => chat.abort?.abort() } }, el("span", { class: "sq" }), "Stop");
    chat.body = el("div", { class: "ask-body" });
    chat.badge = el("span", {});
    chat.foot = el("div", { class: "ask-foot" });
    chat.input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } });
    chat.input.addEventListener("input", () => { chat.input.style.height = "auto"; chat.input.style.height = Math.min(120, chat.input.scrollHeight) + "px"; });
    const clear = el("button", { class: "btn icon ghost", title: "Clear chat", "aria-label": "Clear chat", on: { click: () => { chat.abort?.abort(); chat.history = []; welcome(); } } }, icon("trash", 15));
    const dh = el("div", { class: "dh" }, icon("sparkle", 18), el("h2", {}, "Ask Pulse0"), chat.badge, el("span", { style: "flex:1" }), clear, el("button", { class: "btn icon ghost x", "aria-label": "Close", on: { click: P.closeDrawer } }, icon("x")));
    chat.root = el("div", { class: "db" }, dh, chat.body, el("div", { class: "ask-in" }, chat.input, chat.send, chat.stop), chat.foot);
    welcome();
  }

  function refreshHeader() {
    const claude = isClaude();
    chat.badge.replaceChildren(badge(claude ? "claude" : "rules", claude ? P.aiStatus.model : ""));
    chat.foot.replaceChildren(claude
      ? "Claude runs read-only queries on this Pulse0 instance. Verify important findings in the linked views."
      : el("span", {}, "Rule-based analysis. Set ", el("code", {}, "ANTHROPIC_API_KEY"), " to enable Claude (Settings → AI assistant)."));
  }

  async function open(question = "") {
    if (!chat.root) build();
    const was = isClaude(); await loadStatus();
    if (!chat.busy && was !== isClaude() && !chat.history.length) welcome();   // mode changed since last open
    refreshHeader();
    P.openDrawer(chat.root, "ask");
    setTimeout(() => { chat.input.focus(); if (question) ask(question); }, 220);
  }
  P.assistantOpen = open;
  P.explain = (what, text) => open(`Explain ${what} and suggest likely causes and next steps.\n\n${text}`.slice(0, 3900));
})();
