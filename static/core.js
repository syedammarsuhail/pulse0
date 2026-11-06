/* Pulse0 UI core: helpers, api, router, charts, tables, overlays, shell. No build step. */
(function () {
  const XHTML = "http://www.w3.org/1999/xhtml", SVGNS = "http://www.w3.org/2000/svg";
  const P = (window.P = window.Pulse0 = { views: [], S: {} });
  const $ = (s, r = document) => r.querySelector(s);
  const store = {
    get(k, d) { try { const v = localStorage.getItem("p0." + k); return v == null ? d : v; } catch { return d; } },
    set(k, v) { try { localStorage.setItem("p0." + k, v); } catch { /* private mode */ } },
  };

  // ---------------------------------------------------------------- DOM helpers
  function build(ns, tag, a = {}, kids) {
    const n = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(a || {})) {
      if (v == null || v === false) continue;
      if (k === "on") for (const [e, h] of Object.entries(v)) n.addEventListener(e, h);
      else if (k === "html") n.innerHTML = v;
      else if (k === "value" || k === "checked" || k === "disabled" || k === "selected") n[k] = v;
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const c of kids.flat(Infinity)) if (c != null && c !== false) n.append(c.nodeType ? c : document.createTextNode(String(c)));
    return n;
  }
  const el = (tag, a, ...kids) => build(XHTML, tag, a, kids);
  const sv = (tag, a, ...kids) => build(SVGNS, tag, a, kids);
  const ICONS = {
    home: "M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10",
    services: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
    map: "M6 6m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M18 6m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M12 18m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M8.3 7.5l2.4 8M15.7 7.5l-2.4 8",
    traces: "M3 5h9M7 12h12M11 19h10",
    logs: "M4 5h16M4 10h16M4 15h10M4 20h7",
    metrics: "M3 20V4M3 20h18M7 15l4-5 4 3 5-7",
    dash: "M3 3h8v10H3zM13 3h8v6h-8zM13 11h8v10h-8zM3 15h8v6H3z",
    alerts: "M12 3a6 6 0 0 0-6 6v4l-2 3h16l-2-3V9a6 6 0 0 0-6-6zM10 20a2 2 0 0 0 4 0",
    slo: "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 12m-4.5 0a4.5 4.5 0 1 0 9 0a4.5 4.5 0 1 0-9 0M12 12l5-5",
    synth: "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18",
    setup: "M8 9l-4 3 4 3M16 9l4 3-4 3M13.5 6l-3 12",
    settings: "M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
    warn: "M12 3l10 18H2zM12 10v5M12 18.5v.01",
    info: "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 11v5M12 8v.01",
    plus: "M12 5v14M5 12h14", x: "M6 6l12 12M18 6L6 18", trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
    copy: "M9 9h11v11H9zM5 15V5h10", edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4", chev: "M9 6l6 6-6 6", search: "M11 11m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0M20 20l-3.5-3.5",
    play: "M7 4l13 8-13 8z", pause: "M7 4h4v16H7zM13 4h4v16h-4z", sparkle: "M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z",
    ext: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6", refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7", check: "M5 12l5 5 9-10", fit: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  };
  const icon = (n, size = 16) => sv("svg", { viewBox: "0 0 24 24", width: size, height: size }, sv("path", { d: ICONS[n] || "" }));

  // ---------------------------------------------------------------- formatting
  const compact = n => { if (n == null || isNaN(n)) return "—"; const a = Math.abs(n); if (a >= 1e9) return +(n / 1e9).toFixed(2) + "B"; if (a >= 1e6) return +(n / 1e6).toFixed(2) + "M"; if (a >= 1e4) return +(n / 1e3).toFixed(1) + "k"; if (a >= 100) return Math.round(n).toLocaleString(); return +n.toFixed(a < 1 ? 3 : 2) + ""; };
  const fmt = n => n == null ? "—" : Math.abs(n) >= 1000 ? Math.round(n).toLocaleString() : +n.toFixed(2) + "";
  const ms = n => n == null ? "—" : n === 0 ? "0ms" : n < 1 ? n.toFixed(2) + "ms" : n < 1000 ? Math.round(n) + "ms" : n < 60000 ? (n / 1000).toFixed(2) + "s" : (n / 60000).toFixed(1) + "m";
  const pct = n => n == null ? "—" : (n >= 10 ? n.toFixed(1) : n.toFixed(2)) + "%";
  const rate = n => n == null ? "—" : n >= 10 ? n.toFixed(1) : n.toFixed(2);
  const clock = t => new Date(t).toLocaleTimeString([], { hour12: false });
  const stamp = t => new Date(t).toLocaleString([], { hour12: false });
  const ago = t => { const s = Math.max(0, (Date.now() - t) / 1000); return s < 60 ? `${s | 0}s ago` : s < 3600 ? `${(s / 60) | 0}m ago` : s < 86400 ? `${(s / 3600) | 0}h ago` : `${(s / 86400) | 0}d ago`; };
  const sevName = n => n >= 21 ? "FATAL" : n >= 17 ? "ERROR" : n >= 13 ? "WARN" : n >= 9 ? "INFO" : n >= 5 ? "DEBUG" : n >= 1 ? "TRACE" : "UNSET";
  const pill = (txt, kind = "", title) => el("span", { class: "pill " + kind, title }, txt);
  const errPill = p => pill(pct(p), p >= 5 ? "err" : p >= 1 ? "warn" : "ok");
  const healthPill = h => h && h.score != null ? pill([el("i", { class: "d" }), `${Math.round(h.score)} · ${h.state}`], h.state === "healthy" ? "ok" : h.state === "degraded" ? "warn" : "err", h.reason) : pill("no traffic", "");
  const copyText = async t => { try { await navigator.clipboard.writeText(t); } catch { const a = el("textarea", { style: "position:fixed;opacity:0" }); a.value = t; document.body.append(a); a.select(); try { document.execCommand("copy"); } catch { /* ignore */ } a.remove(); } toast("Copied to clipboard", "ok"); };
  const COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)", "var(--c7)", "var(--c8)"];

  // ---------------------------------------------------------------- state
  const S = P.S;
  S.minutes = +store.get("min", 60) || 60;
  S.auto = store.get("auto", "1") === "1";
  P.minutes = () => S.minutes;
  const rangeLabel = m => { const p = [[5, "5 minutes"], [15, "15 minutes"], [30, "30 minutes"], [60, "1 hour"], [180, "3 hours"], [360, "6 hours"], [720, "12 hours"], [1440, "24 hours"], [4320, "3 days"], [10080, "7 days"]].find(x => x[0] === m); if (p) return "Last " + p[1]; return m % 1440 === 0 ? `Last ${m / 1440} days` : m % 60 === 0 ? `Last ${m / 60} hours` : `Last ${m} min`; };

  // ---------------------------------------------------------------- api
  class AuthError extends Error { }
  async function api(path, o = {}) {
    const h = {}, tok = store.get("token", "");
    if (tok) h.Authorization = "Bearer " + tok;
    const proj = store.get("project", ""); if (proj) h["x-pulse0-project"] = proj; // hosted mode: org admin keys pick a project
    if (o.body) h["Content-Type"] = "application/json";
    const r = await fetch("/api" + path, { method: o.method || "GET", headers: h, body: o.body ? JSON.stringify(o.body) : undefined });
    if (r.status === 401) throw new AuthError("This Pulse0 requires an API token.");
    if (r.status === 400 && P.onProjectRequired) { const j = await r.clone().json().catch(() => ({})); if (j.error === "project_required") { P.onProjectRequired(j); throw new Error(j.message); } }
    if (!r.ok) {
      let m = r.statusText;
      try { const j = await r.json(); m = j.message || (j.details && j.details.map(d => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join("; ")) || j.error || m; } catch { /* keep */ }
      throw new Error(m);
    }
    return r.json();
  }
  const qs = o => { const u = new URLSearchParams(); for (const [k, v] of Object.entries(o)) if (v !== "" && v != null && v !== false) u.set(k, v === true ? "1" : v); return u.toString(); };
  let svcCache = null;
  P.services = async (all = false) => { if (!svcCache) svcCache = api("/services?minutes=" + S.minutes); const r = await svcCache; return all ? r : r.filter(s => s.n > 0); };
  Object.assign(P, { $, el, sv, icon, store, api, AuthError, qs, compact, fmt, ms, pct, rate, clock, stamp, ago, sevName, pill, errPill, healthPill, copyText, COLORS, rangeLabel });

  // ---------------------------------------------------------------- toast / modal / drawer
  function toast(msg, kind = "") {
    const t = el("div", { class: "toast " + kind }, msg); $("#toasts").append(t);
    setTimeout(() => t.remove(), kind === "err" ? 7000 : 3200);
  }
  function modal({ title, body, actions = [], width, onClose }) {
    const bg = el("div", { class: "modal-bg" });
    const close = () => { bg.remove(); document.removeEventListener("keydown", esc); P.modalOpen = $("#modal-root").children.length > 0; onClose?.(); };
    const esc = e => { if (e.key === "Escape") close(); };
    const foot = el("div", { class: "mf" }, ...actions.map(a => el("button", { class: "btn " + (a.primary ? "primary" : a.danger ? "danger" : ""), on: { click: async e => { const b = e.currentTarget; b.disabled = true; try { if ((await a.onClick?.(close)) === false) b.disabled = false; else if (!a.keep) close(); } catch (err) { toast(err.message, "err"); b.disabled = false; } } } }, a.label)));
    const m = el("div", { class: "modal", style: width ? `width:min(${width}px,100%)` : null }, el("div", { class: "mh" }, title), el("div", { class: "mb" }, body), actions.length ? foot : null);
    // Enter inside a single-line field submits the primary action (not in textareas / on buttons)
    m.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && e.target.tagName === "INPUT" && e.target.type !== "checkbox" && e.target.type !== "radio") { const pb = m.querySelector(".mf .btn.primary"); if (pb && !pb.disabled) { e.preventDefault(); pb.click(); } } });
    bg.append(m); bg.addEventListener("mousedown", e => { if (e.target === bg) close(); });
    $("#modal-root").append(bg); P.modalOpen = true; document.addEventListener("keydown", esc);
    setTimeout(() => m.querySelector("input,select,textarea")?.focus(), 30);
    return close;
  }
  const confirmBox = (msg, label = "Delete") => new Promise(res => { let v = false; modal({ title: "Are you sure?", body: el("div", {}, msg), onClose: () => res(v), actions: [{ label: "Cancel" }, { label, danger: true, onClick: () => { v = true; } }] }); });
  const field = (label, input, hint, cls = "") => el("div", { class: "field " + cls }, el("label", {}, label), input, hint ? el("div", { class: "hint" }, hint) : null);
  Object.assign(P, { toast, modal, confirmBox, field });

  const drawerEl = () => $("#drawer"), scrim = () => $("#scrim");
  function openDrawer(content, which = "drawer") {
    const d = $("#" + which); d.replaceChildren(content); d.classList.add("open"); d.setAttribute("aria-hidden", "false"); scrim().classList.add("on"); P.drawerOpen = which;
  }
  function closeDrawer() {
    for (const w of ["drawer", "ask"]) { const d = $("#" + w); d.classList.remove("open"); d.setAttribute("aria-hidden", "true"); }
    scrim().classList.remove("on"); P.drawerOpen = null;
    const r = parseHash(); if (r.q.get("id") && r.path === "/traces") setParams({ id: "" }, true);
  }
  Object.assign(P, { openDrawer, closeDrawer });

  // ---------------------------------------------------------------- charts
  function niceMax(v) { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))), f = v / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p; }
  function timeLabel(t, span) { const d = new Date(t); return span > 36 * 3600e3 ? d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }); }
  /** chart({t, series:[{name, values, color?}], type:'line'|'area'|'bar'|'stack', h, fmt, legend}) */
  function chart(o) {
    const { t = [], series = [], type = "area", h = 200, f = compact, legend = series.length > 1 } = o;
    const box = el("div", { class: "chart" }), tip = el("div", { class: "tip" });
    const colors = series.map((s, i) => s.color || COLORS[i % COLORS.length]);
    const n = t.length || (series[0]?.values.length || 0);
    function draw() {
      const w = Math.max(220, box.clientWidth || 600), pad = { l: 46, r: 12, t: 10, b: 22 }, iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
      const stack = type === "stack";
      const totals = Array.from({ length: n }, (_, i) => stack ? series.reduce((s, x) => s + (x.values[i] || 0), 0) : Math.max(0, ...series.map(x => x.values[i] || 0)));
      const mx = niceMax(Math.max(...totals, 0) * 1.05 || 1);
      const X = i => pad.l + (type === "bar" || stack ? (i + .5) / n * iw : n > 1 ? i / (n - 1) * iw : iw / 2), Y = v => pad.t + ih - (v / mx) * ih;
      const s = sv("svg", { viewBox: `0 0 ${w} ${h}`, width: w, height: h });
      for (let i = 0; i <= 4; i++) { const y = pad.t + ih * i / 4; s.append(sv("line", { class: "gl", x1: pad.l, x2: w - pad.r, y1: y, y2: y }), sv("text", { class: "ax", x: pad.l - 7, y: y + 3.5, "text-anchor": "end" }, f(mx * (1 - i / 4)))); }
      if (n) { const span = t[n - 1] - t[0]; for (let i = 0; i < 5; i++) { const k = Math.round((n - 1) * i / 4); s.append(sv("text", { class: "ax", x: Math.min(w - 20, Math.max(pad.l + 14, X(k))), y: h - 5, "text-anchor": "middle" }, t.length ? timeLabel(t[k], span) : k)); } }
      if (type === "bar" || stack) {
        const bw = Math.max(1, iw / n * .72), acc = new Array(n).fill(0);
        series.forEach((x, si) => x.values.forEach((v, i) => { v = v || 0; if (!v) return; const base = stack ? acc[i] : 0, y1 = Y(base + v), y0 = Y(base); s.append(sv("rect", { x: X(i) - (stack ? bw : bw / series.length) / (stack ? 2 : 1) + (stack ? 0 : si * bw / series.length), y: y1, width: stack ? bw : bw / series.length, height: Math.max(.5, y0 - y1), fill: colors[si], rx: 1.5, opacity: .92 })); if (stack) acc[i] += v; }));
      } else series.forEach((x, si) => {
        // null means "no data in this bucket": break the line there instead of plotting a zero.
        const runs = []; let cur = null;
        x.values.forEach((v, i) => { if (v == null) { cur = null; return; } if (!cur) runs.push(cur = []); cur.push([X(i), Y(v)]); });
        runs.forEach(pts => {
          if (pts.length === 1) { s.append(sv("circle", { cx: pts[0][0], cy: pts[0][1], r: 2.4, fill: colors[si] })); return; }
          const line = pts.map(p => p.join(",")).join(" ");
          if (type === "area") s.append(sv("polygon", { points: `${pts[0][0]},${pad.t + ih} ${line} ${pts[pts.length - 1][0]},${pad.t + ih}`, fill: colors[si], "fill-opacity": series.length > 1 ? .08 : .16 }));
          s.append(sv("polyline", { points: line, fill: "none", stroke: colors[si], "stroke-width": 1.9, "stroke-linejoin": "round" }));
        });
      });
      const cross = sv("line", { y1: pad.t, y2: pad.t + ih, stroke: "var(--text-3)", "stroke-dasharray": "3 3", style: "display:none" });
      const hit = sv("rect", { x: pad.l, y: pad.t, width: iw, height: ih, fill: "transparent" });
      hit.addEventListener("mousemove", e => {
        const r = s.getBoundingClientRect(), mx_ = e.clientX - r.left - pad.l;
        const i = Math.max(0, Math.min(n - 1, type === "bar" || stack ? Math.floor(mx_ / iw * n) : Math.round(mx_ / iw * (n - 1))));
        cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.style.display = "";
        tip.replaceChildren(el("div", { class: "tt" }, t.length ? stamp(t[i]) : "#" + i), ...series.map((x, si) => el("div", { class: "tr" }, el("span", { style: "display:flex;align-items:center;gap:7px" }, el("i", { style: `background:${colors[si]}` }), x.name), el("b", {}, f(x.values[i] || 0)))));
        tip.style.display = "block"; const tw = tip.offsetWidth; tip.style.left = Math.min(w - tw - 4, Math.max(4, X(i) + 12)) + "px"; tip.style.top = "6px";
      });
      hit.addEventListener("mouseleave", () => { cross.style.display = "none"; tip.style.display = "none"; });
      s.append(cross, hit);
      box.replaceChildren(s, tip);
      if (legend) box.append(el("div", { class: "legend" }, ...series.map((x, i) => el("span", {}, el("i", { style: `background:${colors[i]}` }), x.name))));
    }
    let last = 0;
    const ro = new ResizeObserver(() => { const w = box.clientWidth; if (w && Math.abs(w - last) > 2) { last = w; draw(); } });
    queueMicrotask(() => { if (box.isConnected) ro.observe(box); else { const mo = new MutationObserver(() => { if (box.isConnected) { mo.disconnect(); ro.observe(box); draw(); } }); mo.observe(document.body, { childList: true, subtree: true }); } });
    box.style.minHeight = h + "px";
    return box;
  }
  function sparkline(vals, { w = 110, h = 28, color = "var(--c1)", max } = {}) {
    const v = vals.map(x => x || 0), m = max ?? Math.max(1e-9, ...v), dx = v.length > 1 ? w / (v.length - 1) : 0;
    const pts = v.map((x, i) => `${(i * dx).toFixed(1)},${(h - 2 - (x / m) * (h - 4)).toFixed(1)}`).join(" ");
    return sv("svg", { viewBox: `0 0 ${w} ${h}`, width: w, height: h, style: "display:block;overflow:visible" }, sv("polygon", { points: `0,${h} ${pts} ${w},${h}`, fill: color, "fill-opacity": .14, stroke: "none" }), sv("polyline", { points: pts, fill: "none", stroke: color, "stroke-width": 1.6 }));
  }
  Object.assign(P, { chart, sparkline });

  // ---------------------------------------------------------------- table
  /** dataTable({cols:[{key,label,num,sort,render}], rows, onRow, empty, sortKey, sortDir, rowClass}) */
  function dataTable(o) {
    let { sortKey = null, sortDir = -1 } = o;
    const wrap = el("div", { class: "tbl", "data-keep": o.keep || "tbl" });
    function paint() {
      let rows = o.rows.slice();
      const col = o.cols.find(c => c.key === sortKey);
      if (col) { const g = col.sort || (r => r[col.key]); rows.sort((a, b) => { const x = g(a), y = g(b); return (x > y ? 1 : x < y ? -1 : 0) * sortDir; }); }
      const head = el("tr", {}, ...o.cols.map(c => el("th", { class: (c.num ? "num " : "") + (c.sort !== false ? "sortable" : ""), style: c.width ? `width:${c.width}` : null, on: c.sort === false ? {} : { click: () => { sortDir = sortKey === c.key ? -sortDir : (c.num ? -1 : 1); sortKey = c.key; paint(); } } }, c.label, sortKey === c.key ? el("span", { class: "arr" }, sortDir > 0 ? "▲" : "▼") : null)));
      const body = rows.map(r => el("tr", { class: (o.onRow ? "click " : "") + (o.rowClass ? o.rowClass(r) : ""), on: o.onRow ? { click: () => o.onRow(r) } : {} }, ...o.cols.map(c => el("td", { class: (c.num ? "num " : "") + (c.cls || "") }, c.render ? c.render(r) : r[c.key] ?? "—"))));
      const keep = wrap.scrollTop;
      wrap.replaceChildren(...[el("table", {}, el("thead", {}, head), el("tbody", {}, body)), rows.length ? null : o.empty || el("div", { class: "empty" }, el("b", {}, "Nothing here yet"))].filter(Boolean));
      wrap.scrollTop = keep;
    }
    paint(); wrap.repaint = (rows) => { o.rows = rows; paint(); };
    return wrap;
  }
  Object.assign(P, { dataTable });
  const empty = (title, sub, ...extra) => el("div", { class: "empty" }, el("b", {}, title), sub ? el("div", {}, sub) : null, ...extra);
  const panel = (title, body, { sub, right, cls = "", bodyCls = "pb", style } = {}) => el("section", { class: "panel " + cls, style }, el("div", { class: "ph" }, title, sub ? el("small", {}, sub) : null, right ? el("div", { class: "r" }, right) : null), el("div", { class: bodyCls }, body));
  const kpi = (k, v, s, { cls = "", href, spark } = {}) => el(href ? "a" : "div", { class: "kpi " + cls, href }, el("div", { class: "k" }, k), el("div", { class: "v" }, v), s ? el("div", { class: "s" }, s) : null, spark ? el("div", { class: "spark" }, spark) : null);
  const head = (title, sub, ...actions) => el("div", { class: "head" }, el("div", {}, el("h1", {}, title), sub ? el("div", { class: "sub" }, sub) : null), actions.length ? el("div", { class: "actions" }, ...actions) : null);
  const codeBlock = txt => el("div", { class: "codewrap" }, el("pre", { class: "code" }, txt), el("button", { class: "btn sm", on: { click: () => copyText(txt) } }, icon("copy", 13), "Copy"));
  Object.assign(P, { empty, panel, kpi, head, codeBlock });

  // ---------------------------------------------------------------- router
  const parseHash = () => { const h = location.hash.slice(1) || "/home"; const [p, q] = h.split("?"); return { path: p.replace(/\/+$/, "") || "/home", q: new URLSearchParams(q || "") }; };
  function setParams(patch, replace = true) {
    const r = parseHash(); for (const [k, v] of Object.entries(patch)) (v === "" || v == null || v === false) ? r.q.delete(k) : r.q.set(k, v === true ? "1" : v);
    const s = r.q.toString(), url = "#" + r.path + (s ? "?" + s : "");
    if (replace) history.replaceState(null, "", url); else location.hash = url;
  }
  const nav = (path, params) => { location.hash = path + (params && qs(params) ? "?" + qs(params) : ""); };
  P.parseHash = parseHash; P.setParams = setParams; P.nav = nav;
  P.route = (pattern, view) => { const keys = []; const re = new RegExp("^" + pattern.replace(/:(\w+)/g, (_, k) => { keys.push(k); return "([^/]+)"; }) + "$"); P.views.push({ re, keys, view }); };
  function match(path) { for (const v of P.views) { const m = v.re.exec(path); if (m) return { view: v.view, params: Object.fromEntries(v.keys.map((k, i) => [k, decodeURIComponent(m[i + 1])])) }; } return null; }
  let current = null, renderId = 0;
  async function render(opts = {}) {
    const id = ++renderId, r = parseHash(), m = match(r.path) || match("/home");
    if (!opts.refresh) closeDrawerSoft();
    svcCache = null;
    if (current?.view.cleanup && (!opts.refresh || current.view !== m.view)) current.view.cleanup();
    current = m;
    const page = el("div", { class: "page" + (m.view.scroll ? " scroll" : "") });
    const keeps = {}; document.querySelectorAll("#main [data-keep]").forEach((n, i) => keeps[(n.dataset.keep || "") + i] = n.scrollTop);
    const mainTop = $("#main").scrollTop;
    try {
      const ctx = { params: m.params, q: r.q, refresh: !!opts.refresh, page };
      const crumbs = m.view.crumbs ? await m.view.crumbs(ctx) : [[m.view.title]];
      if (id !== renderId) return;
      renderCrumbs(crumbs, m.view);
      await m.view.render(page, ctx);
    } catch (e) {
      page.replaceChildren(e instanceof AuthError ? authCard() : el("div", { class: "panel" }, empty("Something went wrong", e.message)));
    }
    if (id !== renderId) return;
    $("#main").replaceChildren(page);
    document.querySelectorAll("#main [data-keep]").forEach((n, i) => { const k = (n.dataset.keep || "") + i; if (opts.refresh && keeps[k] != null) n.scrollTop = keeps[k]; });
    if (opts.refresh) $("#main").scrollTop = mainTop; else $("#main").scrollTop = 0;
    highlightRail(r.path);
    ping();
  }
  function closeDrawerSoft() { if (P.drawerOpen === "drawer" && !parseHash().q.get("id")) closeDrawer(); }
  function authCard() {
    const inp = el("input", { type: "password", placeholder: "Bearer token", style: "width:320px" });
    return el("div", { class: "panel", style: "max-width:520px;margin:60px auto;" }, el("div", { class: "ph" }, "API token required"), el("div", { class: "pb", style: "display:flex;flex-direction:column;gap:12px" }, el("div", { class: "muted" }, P.mode === "hosted" ? "This is a Pulse-hosted workspace. Paste a read or admin API key for your project; it is stored only in this browser." : "This Pulse0 instance has PULSE0_TOKEN set. Enter it once; it is stored only in this browser."), inp, el("div", {}, el("button", { class: "btn primary", on: { click: () => { store.set("token", inp.value.trim()); render(); } } }, "Unlock"))));
  }
  function renderCrumbs(list, view) {
    const c = $("#crumbs"); c.replaceChildren();
    list.forEach(([label, href], i) => { if (i) c.append(el("span", { class: "sl" }, "/")); c.append(href && i < list.length - 1 ? el("a", { href }, label) : el("span", { class: "cur" }, label)); });
    if (view.range !== false) c.append(el("span", { class: "range-note" }, rangeLabel(S.minutes)));
  }
  P.render = render;

  // ---------------------------------------------------------------- rail
  const NAV = [
    ["Overview", [["home", "Home", "#/home"]]],
    ["Explore", [["services", "Services", "#/services"], ["map", "Service map", "#/map"], ["traces", "Traces", "#/traces"], ["logs", "Logs", "#/logs"], ["metrics", "Metrics", "#/metrics"]]],
    ["Observe", [["dash", "Dashboards", "#/dashboards"], ["alerts", "Alerts", "#/alerts"], ["slo", "SLOs", "#/slos"], ["synth", "Synthetics", "#/synthetics"]]],
    ["Configure", [["setup", "Setup & integrations", "#/setup"], ["settings", "Settings", "#/settings"]]],
  ];
  P.NAV = NAV;
  function buildRail() {
    const r = $("#rail");
    for (const [g, items] of NAV) {
      r.append(el("div", { class: "grp" }, g), el("div", { class: "sep" }));
      for (const [ic, label, href] of items) r.append(el("a", { href, "data-path": href.slice(1), title: label }, icon(ic, 18), el("span", {}, label), ic === "home" ? el("span", { class: "badge", id: "badge-home" }) : null));
    }
  }
  function highlightRail(path) { document.querySelectorAll("#rail a").forEach(a => a.classList.toggle("on", path === a.dataset.path || path.startsWith(a.dataset.path + "/"))); }

  // ---------------------------------------------------------------- status + badge
  async function ping() {
    try {
      const [, ins] = await Promise.all([api("/stats"), api("/insights?minutes=" + S.minutes)]);
      $("#live").classList.remove("off"); $("#status").textContent = "live";
      const b = $("#badge-home"); const c = ins.filter(i => i.severity !== "info").length; if (b) { b.textContent = c; b.classList.toggle("show", c > 0); }
    } catch (e) { if (e instanceof AuthError) { $("#status").textContent = "locked"; } else { $("#live").classList.add("off"); $("#status").textContent = "offline"; } }
  }

  // ---------------------------------------------------------------- range menu
  const PRESETS = [5, 15, 30, 60, 180, 360, 720, 1440, 4320, 10080];
  function setRange(m) { S.minutes = Math.max(1, Math.round(m)); store.set("min", S.minutes); $("#range-label").textContent = rangeLabel(S.minutes); render(); }
  function openRangeMenu() {
    const wrap = $("#range-wrap"); if (wrap.querySelector(".menu")) return closeMenu();
    const num = el("input", { type: "number", min: 1, value: 90 }), unit = el("select", {}, el("option", { value: 1 }, "min"), el("option", { value: 60 }, "hours"), el("option", { value: 1440 }, "days"));
    const menu = el("div", { class: "menu" }, ...PRESETS.map(m => el("div", { class: "it" + (m === S.minutes ? " on" : ""), on: { click: () => { closeMenu(); setRange(m); } } }, rangeLabel(m))), el("div", { class: "cust" }, num, unit, el("button", { class: "btn sm", on: { click: () => { const v = +num.value * +unit.value; if (v > 0) { closeMenu(); setRange(v); } } } }, "Apply")));
    wrap.append(menu); setTimeout(() => document.addEventListener("mousedown", outside), 0);
    function outside(e) { if (!wrap.contains(e.target)) closeMenu(); }
    function closeMenu() { menu.remove(); document.removeEventListener("mousedown", outside); }
  }

  // ---------------------------------------------------------------- command palette
  function openPalette(initial = "") {
    const root = $("#palette"); root.hidden = false; P.modalOpen = true;
    const inp = el("input", { placeholder: "Search services, traces, metrics, pages — or type a question…", value: initial, autocomplete: "off" });
    const res = el("div", { class: "res" });
    root.replaceChildren(el("div", { class: "pal" }, inp, res, el("div", { class: "foot" }, el("span", {}, "↑↓ navigate"), el("span", {}, "↵ open"), el("span", {}, "esc close"))));
    let items = [], sel = 0, tick = 0;
    const close = () => { root.hidden = true; root.replaceChildren(); P.modalOpen = $("#modal-root").children.length > 0; document.removeEventListener("keydown", key, true); };
    const run = it => { close(); it.go(); };
    function paint() {
      res.replaceChildren(); let g = "";
      items.forEach((it, i) => { if (it.group !== g) { g = it.group; res.append(el("div", { class: "g" }, g)); } res.append(el("div", { class: "it" + (i === sel ? " sel" : ""), on: { click: () => run(it), mousemove: () => { if (sel !== i) { sel = i; paint(); } } } }, icon(it.icon || "chev", 16), el("span", {}, it.label), it.hint ? el("small", {}, it.hint) : null)); });
      if (!items.length) res.append(el("div", { class: "empty" }, "No matches"));
      res.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
    }
    async function collect() {
      const v = inp.value.trim(), my = ++tick, low = v.toLowerCase();
      const pages = NAV.flatMap(([, it]) => it).filter(([, l]) => !low || l.toLowerCase().includes(low)).map(([ic, l, h]) => ({ group: "Pages", icon: ic, label: l, hint: "Go to", go: () => { location.hash = h; } }));
      const acts = [{ group: "Actions", icon: "sparkle", label: v ? `Ask Pulse0: “${v}”` : "Ask Pulse0…", hint: "Investigate", go: () => openAsk(v) }, { group: "Actions", icon: "settings", label: "Toggle light / dark theme", go: toggleTheme }, { group: "Actions", icon: "alerts", label: "Create alert from template", go: () => nav("/alerts", { tab: "templates" }) }].filter(a => !low || a.label.toLowerCase().includes(low) || low.length > 8);
      items = v ? [...pages, ...acts] : [...pages.slice(0, 6), ...acts]; sel = 0; paint();
      if (v.length >= 2) {
        try {
          const r = await api("/search?" + qs({ q_: v }));
          if (my !== tick) return;
          const dyn = [...r.services.map(s => ({ group: "Services", icon: "services", label: s.name, hint: "Service", go: () => nav("/services/" + encodeURIComponent(s.name)) })),
            ...r.traces.map(t => ({ group: "Traces", icon: "traces", label: `${t.name} · ${t.service}`, hint: ms(t.dur_ms), go: () => nav("/traces", { id: t.trace_id }) })),
            ...r.metrics.map(m => ({ group: "Metrics", icon: "metrics", label: m.name, hint: "Metric", go: () => nav("/metrics", { name: m.name }) }))];
          items = [...dyn, ...items]; sel = 0; paint();
        } catch { /* palette still works offline */ }
      }
    }
    function key(e) {
      if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); paint(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(0, sel - 1); paint(); }
      else if (e.key === "Enter") { e.preventDefault(); if (items[sel]) run(items[sel]); }
    }
    document.addEventListener("keydown", key, true);
    root.addEventListener("mousedown", e => { if (e.target === root) close(); }, { once: true });
    let deb; inp.addEventListener("input", () => { clearTimeout(deb); deb = setTimeout(collect, 140); });
    collect(); inp.focus(); inp.select();
  }

  // ---------------------------------------------------------------- ask pulse0
  // assistant.js replaces this with the Claude-backed chat; this stays as a standalone fallback.
  function openAsk(question = "") { return (P.assistantOpen || openAskRules)(question); }
  function openAskRules(question = "") {
    const body = el("div", { class: "ask-body" }), inp = el("input", { placeholder: "e.g. Which services are slow? What broke?", autocomplete: "off" });
    const go = el("button", { class: "btn primary" }, "Ask");
    const dh = el("div", { class: "dh" }, icon("sparkle", 18), el("h2", {}, "Ask Pulse0"), el("small", { class: "muted" }, "rule-based analysis of your own telemetry"), el("button", { class: "btn icon ghost x", on: { click: closeDrawer } }, icon("x")));
    openDrawer(el("div", { class: "db" }, dh, el("div", { class: "ask-in" }, inp, go), body), "ask");
    const sugg = list => el("div", { class: "sugg" }, ...list.map(s => el("button", { class: "chipbtn", on: { click: () => ask(s) } }, s)));
    body.append(el("div", { class: "bubble" }, el("div", { class: "bs" }, "I analyse the traces, logs, metrics, alerts and SLOs stored in Pulse0 — no data leaves your cluster. Try:"), el("div", { class: "bs" }, sugg(["What is failing right now?", "Which services are slow?", "Show top error logs", "Any resource saturation?", "Give me an overview"]))));
    async function ask(text) {
      text = (text || inp.value).trim(); if (!text) return; inp.value = "";
      body.append(el("div", { class: "me" }, text)); const wait = el("div", { class: "muted" }, "Investigating…"); body.append(wait); body.scrollTop = 1e9;
      try {
        const r = await api("/investigate?" + qs({ q_: text, minutes: S.minutes })); wait.remove();
        body.append(el("div", { class: "bubble" }, el("div", { class: "bh" }, r.title), el("div", { class: "bs" }, r.summary),
          ...r.findings.map(f => el("div", { class: "insight " + (f.severity || ""), on: { click: () => { closeDrawer(); location.hash = f.link; } } }, el("div", { class: "ic" }, icon(f.severity === "info" ? "info" : "warn")), el("div", {}, el("div", { class: "t" }, f.label), el("div", { class: "d" }, f.value)))),
          el("div", { class: "bs" }, sugg(r.suggestions))));
      } catch (e) { wait.textContent = e.message; }
      body.scrollTop = 1e9;
    }
    go.addEventListener("click", () => ask()); inp.addEventListener("keydown", e => { if (e.key === "Enter") ask(); });
    setTimeout(() => { inp.focus(); if (question) ask(question); }, 220);
  }
  P.openAsk = openAsk; P.openPalette = openPalette;

  // ---------------------------------------------------------------- theme + start
  function toggleTheme() { const t = document.documentElement.dataset.theme === "light" ? "dark" : "light"; document.documentElement.dataset.theme = t; store.set("theme", t); render({ refresh: true }); }
  let timer = null;
  function tick() { clearInterval(timer); if (S.auto) timer = setInterval(() => { const ae = document.activeElement; const typing = ae && /^(INPUT|SELECT|TEXTAREA)$/.test(ae.tagName) && $("#main").contains(ae); if (current?.view.live === false || P.modalOpen || P.drawerOpen || typing || document.hidden) return; render({ refresh: true }); }, 15000); }
  P.start = function () {
    buildRail(); $("#range-label").textContent = rangeLabel(S.minutes); $("#auto").checked = S.auto;
    $("#range-btn").addEventListener("click", openRangeMenu);
    $("#refresh").addEventListener("click", () => render({ refresh: true }));
    $("#auto").addEventListener("change", e => { S.auto = e.target.checked; store.set("auto", S.auto ? "1" : "0"); tick(); });
    $("#theme").addEventListener("click", toggleTheme);
    $("#search-btn").addEventListener("click", () => openPalette());
    $("#ask-btn").addEventListener("click", () => openAsk());
    $("#scrim").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", e => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("#palette").hidden ? openPalette() : null; }
      else if (e.key === "Escape" && P.drawerOpen && !P.modalOpen) closeDrawer();
      else if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) && $("#palette").hidden) { e.preventDefault(); openPalette(); }
    });
    window.addEventListener("hashchange", () => render());
    tick(); render();
  };
  P.toggleTheme = toggleTheme; P.tick = tick; P.setRange = setRange;
})();
