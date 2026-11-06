/* Pulse0 service map page. Graph + layout logic lives in mapkit.js (pure, unit-tested in tests/test_mapkit.js).
   This file only builds the DOM: toolbar, SVG canvas, minimap, side panel. */
(function () {
  const P = window.P, K = window.MapKit;
  const { el, sv, icon, api, compact, ms, pct, rate, pill, errPill, sparkline, empty, head, store, AuthError } = P;
  const enc = encodeURIComponent, M = () => P.minutes();
  const NW = 208, NH = 78, MAX_NODES = 400, AUTO_GROUP_AT = 120, ANIM_MAX_EDGES = 80;
  const reduceMotion = () => !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
  const cfg = { get: (k, d) => { const v = store.get("map." + k, null); return v == null ? d : v; }, set: (k, v) => store.set("map." + k, v) };
  const trunc = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
  let cleanups = [];

  P.route("/map", {
    title: "Service map", live: false,
    cleanup() { cleanups.splice(0).forEach(f => { try { f(); } catch { /* ignore */ } }); },
    async render(page, ctx) {
      const st = { dir: cfg.get("dir", "LR"), group: cfg.get("group", "none"), onlyErr: cfg.get("onlyErr", "0") === "1", hideExt: cfg.get("hideExt", "0") === "1", minCalls: +cfg.get("minCalls", 0) || 0, sel: ctx.q.get("sel") || "" };
      const collapsed = new Set(), initDone = new Set();
      let pendingFit = false, layoutAspect = 2, data = null, base = null, g = null, L = null, notice = "", view = { k: 1, tx: 0, ty: 0 }, svg, vp, nodeEls = new Map(), edgeEls = new Map(), reachCache = new Map(), hover = null, W = 0, H = 0, raf = 0, mmRect = null, mmScale = null;

      // ---------------------------------------------------------------- shell
      const sub = el("div", { class: "sub" }, "Loading…");
      const searchIn = el("input", { type: "search", placeholder: "Find a service…", "aria-label": "Find a service", autocomplete: "off" });
      const results = el("div", { class: "mp-res", hidden: true, role: "listbox" });
      const segBtn = (v, label, ic) => el("button", { type: "button", "data-v": v, title: label, "aria-label": label, on: { click: () => { st.dir = v; cfg.set("dir", v); rebuild(true); syncBar(); } } }, icon(ic, 14), el("span", {}, label));
      const dirSeg = el("div", { class: "seg", role: "group", "aria-label": "Layout direction" }, segBtn("LR", "Left → right", "chev"), segBtn("TB", "Top → down", "chev"));
      const groupSel = el("select", { "aria-label": "Group services", on: { change: e => { st.group = e.target.value; cfg.set("group", st.group); collapsed.clear(); rebuild(true); syncBar(); } } },
        ...[["none", "No grouping"], ["namespace", "Group by namespace"], ["prefix", "Group by name prefix"]].map(([v, l]) => el("option", { value: v }, l)));
      const collapseAll = el("button", { class: "btn sm", type: "button", on: { click: () => { for (const n of base.nodes) if (n.group) collapsed.add(n.group); rebuild(true); } } }, "Collapse all");
      const expandAll = el("button", { class: "btn sm", type: "button", on: { click: () => { collapsed.clear(); rebuild(true); } } }, "Expand all");
      const chip = (key, label, title) => el("button", { class: "chipbtn", type: "button", "aria-pressed": "false", title, "data-k": key, on: { click: () => { st[key] = !st[key]; cfg.set(key, st[key] ? "1" : "0"); rebuild(true); syncBar(); } } }, label);
      const minIn = el("input", { type: "number", min: 0, value: st.minCalls, title: "Hide dependencies with fewer calls than this", "aria-label": "Minimum calls", style: "width:70px", on: { change: e => { st.minCalls = Math.max(0, +e.target.value || 0); cfg.set("minCalls", st.minCalls); rebuild(true); } } });
      const refreshBtn = el("button", { class: "btn sm icon", type: "button", title: "Reload map data", "aria-label": "Reload map data", on: { click: () => load() } }, icon("refresh", 14));
      const bar = el("div", { class: "mp-bar" },
        el("div", { class: "mp-search" }, icon("search", 15), searchIn, results), dirSeg, groupSel, collapseAll, expandAll,
        chip("onlyErr", "Errors only", "Show only dependencies that have errors"), chip("hideExt", "Hide externals", "Hide databases, queues and other uninstrumented dependencies"),
        el("label", { class: "mp-min", title: "Hide dependencies with fewer calls than this" }, "Min calls", minIn), el("div", { class: "grow" }), refreshBtn);
      const canvas = el("div", { class: "mp-canvas" });
      page.append(head("Service map", sub), bar, canvas);
      function syncBar() {
        dirSeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.dir));
        groupSel.value = st.group;
        bar.querySelectorAll(".chipbtn").forEach(b => { b.classList.toggle("on", !!st[b.dataset.k]); b.setAttribute("aria-pressed", st[b.dataset.k] ? "true" : "false"); });
        const hasGroups = !!(base && base.nodes.some(n => n.group));
        collapseAll.hidden = expandAll.hidden = !hasGroups;
      }
      syncBar();

      // ---------------------------------------------------------------- data
      async function fetchData() {
        const m = M();
        const [edges, services, meta] = await Promise.all([api("/map?minutes=" + m), P.services(), api("/map/nodes?minutes=" + m).catch(() => [])]);
        data = { edges, services, meta: Object.fromEntries(meta.map(x => [x.service, x])) };
      }
      async function load() {
        try { await fetchData(); } catch (e) { if (e instanceof AuthError) throw e; showError(e); return; }
        rebuild(true);
      }
      function showError(e) { canvas.replaceChildren(el("div", { class: "mp-state" }, empty("Couldn't load the service map", String(e && e.message || e), el("button", { class: "btn primary sm", type: "button", style: "margin-top:10px", on: { click: () => load() } }, "Try again")))); sub.textContent = "Unavailable"; }

      function compute() {
        notice = "";
        base = K.buildGraph(data.edges, data.services, data.meta, { onlyErrors: st.onlyErr, hideExternal: st.hideExt, minCalls: st.minCalls });
        let mode = st.group;
        if (mode === "none" && base.nodes.length > AUTO_GROUP_AT) { mode = "prefix"; notice = `${base.nodes.length} services: grouped by name prefix so the map stays readable`; }
        K.assignGroups(base, mode);
        if (!initDone.has(mode) && base.nodes.length > AUTO_GROUP_AT) { initDone.add(mode); for (const n of base.nodes) if (n.group) collapsed.add(n.group); }
        let cg = K.collapse(base, collapsed);
        if (cg.nodes.length > MAX_NODES) { // hard cap: keep the busiest services, say so
          const keep = new Set(cg.nodes.slice().sort((a, b) => b.n - a.n).slice(0, MAX_NODES).map(n => n.id));
          const nodes = cg.nodes.filter(n => keep.has(n.id)), edges = cg.edges.filter(e => keep.has(e.src) && keep.has(e.dst));
          notice = `Showing the ${MAX_NODES} busiest of ${cg.nodes.length} nodes. Group or filter to see the rest.`;
          cg = { nodes, edges, byId: new Map(nodes.map(n => [n.id, n])) };
        }
        g = cg; reachCache = new Map();
      }

      // ---------------------------------------------------------------- drawing
      function rebuild(refit) {
        if (!data) return;
        compute(); syncBar();
        const hasData = (data.services || []).length || (data.edges || []).length;
        sub.textContent = g.nodes.length ? `${g.nodes.length} node${g.nodes.length === 1 ? "" : "s"} · ${g.edges.length} dependenc${g.edges.length === 1 ? "y" : "ies"} derived from traces` : "No dependencies";
        if (!g.nodes.length) {
          const filtered = st.onlyErr || st.hideExt || st.minCalls > 0;
          canvas.replaceChildren(el("div", { class: "mp-state" }, filtered && hasData
            ? empty("Nothing matches these filters", "Loosen the filters to see your services again.", el("button", { class: "btn sm", type: "button", style: "margin-top:10px", on: { click: () => { st.onlyErr = st.hideExt = false; st.minCalls = 0; minIn.value = 0; ["onlyErr", "hideExt", "minCalls"].forEach(k => cfg.set(k, "0")); rebuild(true); } } }, "Reset filters"))
            : empty("No dependencies yet", "Traces that cross services draw the map automatically. Point an app at Pulse0 and it will appear here.", el("a", { class: "btn primary sm", href: "#/setup", style: "margin-top:10px" }, "Set up instrumentation"))));
          return;
        }
        measure(); layoutAspect = W > 0 && H > 0 ? Math.max(1, Math.min(3, W / H)) : 2; L = K.layout(g, { dir: st.dir, NW, NH, aspect: layoutAspect });
        draw();
        measure();
        if (refit || !W) { pendingFit = true; if (W > 0 && H > 0) { pendingFit = false; fitView(false); } } else applyView();
        if (st.sel && g.byId.has(st.sel)) select(st.sel, { quiet: true }); else if (st.sel) { st.sel = ""; closePanel(); }
      }

      function stateOf(n) { return n.n ? K.errState(n.err_pct) : "idle"; }
      function nodeEl(n) {
        const p = L.pos.get(n.id), state = stateOf(n), b = n.collapsedGroup ? null : K.langBadge(n.language);
        const ringTxt = n.collapsedGroup ? "+" + n.members.length : b ? b[0] : n.name.slice(0, 2).toUpperCase();
        const subTxt = n.collapsedGroup ? `${n.members.length} services · collapsed` : n.external ? "external dependency" : [n.namespace, n.version && "v" + n.version].filter(Boolean).join(" · ") || "service";
        const e100 = n.err_pct;
        const aria = `${n.name}, ${n.external ? "external" : "service"}, ${n.n ? rate(n.rate) + " requests per second, " + pct(e100) + " errors, p95 " + ms(n.p95) : "no traffic data"}`;
        const g1 = sv("g", { class: `nd ${state}${n.external ? " ext" : ""}${n.collapsedGroup ? " grp" : ""}`, transform: `translate(${p.x - NW / 2},${p.y - NH / 2})`, tabindex: 0, role: "button", "aria-label": aria, "data-id": n.id },
          sv("title", {}, aria),
          sv("rect", { class: "card", width: NW, height: NH, rx: 12 }),
          sv("rect", { class: "acc", x: 0, y: 14, width: 4, height: NH - 28, rx: 2 }),
          sv("circle", { class: "ring", cx: 32, cy: 29, r: 15 }),
          sv("text", { class: "bd", x: 32, y: 33, style: b ? `fill:${b[1]}` : "" }, trunc(ringTxt, 4)),
          sv("text", { class: "nm", x: 56, y: 25 }, trunc(n.name, 19)),
          sv("text", { class: "sub", x: 56, y: 41 }, trunc(subTxt, 26)),
          sv("text", { class: "sv", x: 14, y: 66 }, n.n ? [sv("tspan", { class: "v" }, rate(n.rate)), " rps"] : "no traffic data"),
          n.n ? sv("text", { class: "sv", x: 82, y: 66 }, [sv("tspan", { class: "v " + state }, pct(e100)), " err"]) : null,
          n.n ? sv("text", { class: "sv", x: 148, y: 66 }, [sv("tspan", { class: "v" }, ms(n.p95)), " p95"]) : null);
        g1.addEventListener("pointerenter", () => { if (!dragging) { hover = n.id; scheduleFocus(); } });
        g1.addEventListener("pointerleave", () => { hover = null; scheduleFocus(); });
        g1.addEventListener("focus", () => { hover = n.id; scheduleFocus(); });
        g1.addEventListener("blur", () => { hover = null; scheduleFocus(); });
        g1.addEventListener("click", ev => { ev.stopPropagation(); select(n.id); });
        g1.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(n.id); } });
        return g1;
      }
      function edgeEl(e, animate) {
        const pth = L.paths.get(e.id), ep = e.calls ? (100 * e.err) / e.calls : 0, st1 = K.errState(ep);
        const w = (1.2 + Math.min(3.2, Math.log10(e.calls + 1) * 0.9)).toFixed(2);
        const txt = `${compact(e.calls)}${ep ? " · " + pct(ep) : ""}`, lw = txt.length * 6.1 + 16;
        const tip = `${e.src} → ${e.dst}\n${e.calls.toLocaleString()} calls · ${e.err.toLocaleString()} errors (${pct(ep)}) · avg ${ms(e.avg_ms)}`;
        return sv("g", { class: "eg", "data-id": e.id },
          sv("title", {}, tip),
          sv("path", { class: "edge " + st1, d: pth.d, "stroke-width": w, "marker-end": `url(#mp-arr-${st1})` }),
          animate ? sv("path", { class: "flowp " + st1, d: pth.d }) : null,
          sv("g", { class: "el " + st1, transform: `translate(${pth.lx},${pth.ly})` }, sv("rect", { x: -lw / 2, y: -9.5, width: lw, height: 19, rx: 9.5 }), sv("text", { y: 4 }, txt)));
      }
      function draw() {
        const animate = g.edges.length <= ANIM_MAX_EDGES && !reduceMotion();
        const marker = s => sv("marker", { id: "mp-arr-" + s, viewBox: "0 0 10 10", refX: 9, refY: 5, markerUnits: "userSpaceOnUse", markerWidth: 10, markerHeight: 10, orient: "auto" }, sv("path", { d: "M0,1L10,5L0,9z", class: "mk " + s }));
        const hulls = sv("g", { class: "hulls" }), edges = sv("g", { class: "edges" }), nodes = sv("g", { class: "nodes" });
        nodeEls = new Map(); edgeEls = new Map();
        for (const h of L.hulls) {
          const gr = sv("g", { class: "hull" }, sv("rect", { x: h.x, y: h.y, width: h.w, height: h.h, rx: 18 }));
          const lab = sv("text", { x: h.x + 14, y: h.y + 17, tabindex: 0, role: "button", "aria-label": `Collapse group ${h.name}` }, `▾ ${h.name} · ${h.count}`);
          lab.addEventListener("click", ev => { ev.stopPropagation(); collapsed.add(h.name); rebuild(true); });
          lab.addEventListener("keydown", ev => { if (ev.key === "Enter") { collapsed.add(h.name); rebuild(true); } });
          gr.append(lab); hulls.append(gr);
        }
        for (const e of g.edges) { const x = edgeEl(e, animate); edges.append(x); edgeEls.set(e.id, x); }
        for (const n of g.nodes) { const x = nodeEl(n); nodes.append(x); nodeEls.set(n.id, x); }
        vp = sv("g", { class: "vp" }, hulls, edges, nodes);
        svg = sv("svg", { class: "mp-svg" + (animate ? " anim" : ""), tabindex: 0, role: "application", "aria-label": "Service dependency map. Tab to move between services, Enter to open, arrow keys to pan, plus and minus to zoom." },
          sv("defs", {}, marker("ok"), marker("warn"), marker("err")), vp);
        bindInteractions();
        canvas.replaceChildren(...[svg, controls(), legend(), minimap(), panelBox, noticeBox()].filter(Boolean));
      }
      const noticeBox = () => (notice ? el("div", { class: "mp-note" }, icon("info", 14), el("span", {}, notice)) : null);
      const panelBox = el("aside", { class: "mp-panel", hidden: true, "aria-label": "Service details" });
      const controls = () => el("div", { class: "mp-ctl" },
        el("button", { class: "btn icon", type: "button", title: "Zoom in", "aria-label": "Zoom in", on: { click: () => zoomBy(1.25) } }, icon("plus")),
        el("button", { class: "btn icon", type: "button", title: "Zoom out", "aria-label": "Zoom out", on: { click: () => zoomBy(0.8) } }, el("span", { class: "minus" }, "−")),
        el("button", { class: "btn icon", type: "button", title: "Fit to screen", "aria-label": "Fit to screen", on: { click: () => fitView(true) } }, icon("fit")));
      const legend = () => el("div", { class: "mp-legend" },
        ...[["ok", "healthy <1%"], ["warn", "errors 1–5%"], ["err", "errors ≥5%"], ["idle", "external / no data"]].map(([c, l]) => el("span", {}, el("i", { class: "dot " + c }), l)),
        el("span", { class: "sep" }, "line width = traffic"));

      // ---------------------------------------------------------------- minimap
      function minimap() {
        const b = L.bounds, pad = 40, w = 176, h = 116, bw = b.x1 - b.x0 + 2 * pad, bh = b.y1 - b.y0 + 2 * pad;
        mmScale = { x0: b.x0 - pad, y0: b.y0 - pad, w: bw, h: bh };
        mmRect = sv("rect", { class: "vr" });
        const dots = g.nodes.map(n => { const p = L.pos.get(n.id); return sv("rect", { class: "dot " + stateOf(n), x: p.x - NW / 2, y: p.y - NH / 2, width: NW, height: NH, rx: 14 }); });
        const m = sv("svg", { viewBox: `${mmScale.x0} ${mmScale.y0} ${bw} ${bh}`, preserveAspectRatio: "xMidYMid meet", width: w, height: h, role: "img", "aria-label": "Minimap" }, ...dots, mmRect);
        const box = el("div", { class: "mp-mm" }, m);
        const go = ev => { const r = m.getBoundingClientRect(), s = Math.min(r.width / bw, r.height / bh); const wx = mmScale.x0 + (ev.clientX - r.left - (r.width - bw * s) / 2) / s, wy = mmScale.y0 + (ev.clientY - r.top - (r.height - bh * s) / 2) / s; view.tx = W / 2 - wx * view.k; view.ty = H / 2 - wy * view.k; applyView(); };
        let md = false;
        box.addEventListener("pointerdown", ev => { md = true; box.setPointerCapture(ev.pointerId); go(ev); });
        box.addEventListener("pointermove", ev => { if (md) go(ev); });
        box.addEventListener("pointerup", () => { md = false; });
        return box;
      }

      // ---------------------------------------------------------------- view (pan / zoom)
      let dragging = false;
      function measure() { const r = canvas.getBoundingClientRect(); W = r.width; H = r.height; }
      function applyView() {
        if (!vp) return;
        vp.setAttribute("transform", `translate(${view.tx.toFixed(1)},${view.ty.toFixed(1)}) scale(${view.k.toFixed(4)})`);
        svg.classList.toggle("lod1", view.k < 0.42); svg.classList.toggle("lod2", view.k < 0.2);
        if (mmRect && mmScale) { mmRect.setAttribute("x", -view.tx / view.k); mmRect.setAttribute("y", -view.ty / view.k); mmRect.setAttribute("width", W / view.k); mmRect.setAttribute("height", H / view.k); }
      }
      function fitView(animate) { measure(); if (!L) return; const f = K.fit(L.bounds, W, H, 34, 1.1); tweenTo(f, animate); }
      function tweenTo(t, animate) {
        cancelAnimationFrame(raf);
        if (!animate || reduceMotion()) { view = { k: t.k, tx: t.tx, ty: t.ty }; applyView(); return; }
        const s = { ...view }, t0 = performance.now(), D = 240;
        const step = now => { const u = Math.min(1, (now - t0) / D), e = 1 - Math.pow(1 - u, 3); view = { k: s.k + (t.k - s.k) * e, tx: s.tx + (t.tx - s.tx) * e, ty: s.ty + (t.ty - s.ty) * e }; applyView(); if (u < 1) raf = requestAnimationFrame(step); };
        raf = requestAnimationFrame(step);
      }
      function zoomAt(f, cx, cy) { const nk = Math.max(0.05, Math.min(3, view.k * f)); view.tx = cx - ((cx - view.tx) * nk) / view.k; view.ty = cy - ((cy - view.ty) * nk) / view.k; view.k = nk; applyView(); }
      function zoomBy(f) { measure(); zoomAt(f, W / 2, H / 2); }
      function focusNode(id) { const p = L && L.pos.get(id); if (!p) return; measure(); const k = Math.max(view.k, 0.9); tweenTo({ k, tx: W / 2 - p.x * k - (panelBox.hidden ? 0 : 150), ty: H / 2 - p.y * k }, true); }

      function bindInteractions() {
        let d = null;
        svg.addEventListener("wheel", ev => { ev.preventDefault(); const r = canvas.getBoundingClientRect(); zoomAt(ev.deltaY < 0 ? 1.12 : 0.89, ev.clientX - r.left, ev.clientY - r.top); }, { passive: false });
        svg.addEventListener("pointerdown", ev => { if (ev.button !== 0 || ev.target.closest(".nd, .hull text")) return; d = { x: ev.clientX, y: ev.clientY, tx: view.tx, ty: view.ty, moved: false }; svg.setPointerCapture(ev.pointerId); });
        svg.addEventListener("pointermove", ev => { if (!d) return; const dx = ev.clientX - d.x, dy = ev.clientY - d.y; if (!d.moved && Math.hypot(dx, dy) < 4) return; d.moved = true; dragging = true; svg.classList.add("drag"); view.tx = d.tx + dx; view.ty = d.ty + dy; applyView(); });
        svg.addEventListener("pointerup", () => { if (d && !d.moved) select(null); d = null; dragging = false; svg.classList.remove("drag"); });
        svg.addEventListener("keydown", ev => {
          if (ev.target !== svg) { if (ev.key === "Escape") select(null); return; }
          const step = 70, k = ev.key;
          if (k === "ArrowLeft") view.tx += step; else if (k === "ArrowRight") view.tx -= step; else if (k === "ArrowUp") view.ty += step; else if (k === "ArrowDown") view.ty -= step;
          else if (k === "+" || k === "=") return zoomBy(1.2); else if (k === "-" || k === "_") return zoomBy(0.83); else if (k === "0") return fitView(true); else if (k === "Escape") return select(null); else return;
          ev.preventDefault(); applyView();
        });
      }
      // the page is attached to the document only after render() returns, so the first fit waits for a real size
      const ro = new ResizeObserver(() => { const ow = W; measure(); if (!L || W <= 0 || H <= 0) return; if (pendingFit) { pendingFit = false; const want = Math.max(1, Math.min(3, W / H)); if (Math.abs(want - layoutAspect) / layoutAspect > 0.15) rebuild(true); else fitView(false); } else if (Math.abs(W - ow) > 2) applyView(); });
      ro.observe(canvas); cleanups.push(() => ro.disconnect());
      cleanups.push(() => cancelAnimationFrame(raf));

      // ---------------------------------------------------------------- focus + selection
      let focusQueued = false;
      function scheduleFocus() { if (focusQueued) return; focusQueued = true; requestAnimationFrame(() => { focusQueued = false; applyFocus(hover || st.sel || null); }); }
      function applyFocus(id) {
        if (!svg) return;
        if (!id || !g.byId.has(id)) { svg.classList.remove("focus"); nodeEls.forEach(e => e.classList.remove("on")); edgeEls.forEach(e => e.classList.remove("on")); return; }
        let r = reachCache.get(id); if (!r) { r = K.reach(g, id); reachCache.set(id, r); }
        svg.classList.add("focus");
        nodeEls.forEach((e, nid) => e.classList.toggle("on", r.nodes.has(nid)));
        edgeEls.forEach((e, eid) => e.classList.toggle("on", r.edges.has(eid)));
      }
      function select(id, opts = {}) {
        st.sel = id || "";
        nodeEls.forEach((e, nid) => e.classList.toggle("sel", nid === id));
        if (!opts.quiet) P.setParams({ sel: id || "" });
        scheduleFocus();
        if (!id) return closePanel();
        openPanel(g.byId.get(id));
        if (opts.focus) focusNode(id);
      }
      function closePanel() { panelBox.hidden = true; canvas.classList.remove("has-panel"); }
      function openPanel(n) {
        if (!n) return closePanel();
        canvas.classList.add("has-panel"); panelBox.hidden = false;
        const out = g.edges.filter(e => e.src === n.id), inn = g.edges.filter(e => e.dst === n.id);
        const row = (e, key) => { const o = g.byId.get(e[key]), ep = e.calls ? (100 * e.err) / e.calls : 0; return el("button", { class: "mp-row", type: "button", on: { click: () => select(o.id, { focus: true }) } }, el("span", { class: "dot " + stateOf(o) }), el("span", { class: "nm" }, o.name), el("span", { class: "mt" }, `${compact(e.calls)} calls${ep ? " · " + pct(ep) + " err" : ""} · ${ms(e.avg_ms)}`)); };
        const list = (arr, key, none) => arr.length ? el("div", { class: "mp-rows" }, ...arr.sort((a, b) => b.calls - a.calls).map(e => row(e, key))) : el("div", { class: "muted mp-none" }, none);
        const b = n.collapsedGroup ? null : K.langBadge(n.language);
        const kv = (k, v) => el("div", { class: "mp-kpi" }, el("div", { class: "k" }, k), el("div", { class: "v" }, v));
        const spark = el("div", { class: "mp-spark" });
        const body = el("div", { class: "mp-body" },
          el("div", { class: "mp-tags" }, n.collapsedGroup ? pill("group", "") : n.external ? pill("external", "") : pill(n.n ? (n.err_pct >= 5 ? "unhealthy" : n.err_pct >= 1 ? "degraded" : "healthy") : "no data", n.err_pct >= 5 ? "err" : n.err_pct >= 1 ? "warn" : n.n ? "ok" : ""), b ? pill(n.language, "") : null, n.namespace ? pill(n.namespace, "") : null, n.version ? pill("v" + n.version, "") : null),
          n.n ? el("div", { class: "mp-kpis" }, kv("Requests", compact(n.n)), kv("Rate", rate(n.rate) + "/s"), kv("Errors", pct(n.err_pct)), kv("p95", ms(n.p95))) : null,
          n.n && !n.collapsedGroup ? el("div", { class: "mp-sect" }, "Request rate", spark) : null,
          n.external && !n.collapsedGroup ? el("div", { class: "muted mp-none" }, "Uninstrumented dependency (database, queue or external API) inferred from client spans.") : null,
          n.collapsedGroup ? el("div", {}, el("div", { class: "mp-sect" }, "Members"), el("div", { class: "mp-chips" }, ...n.members.map(m => el("button", { class: "chipbtn", type: "button", on: { click: () => { collapsed.delete(n.group); st.sel = m; rebuild(false); focusNode(m); } } }, m))), el("button", { class: "btn primary sm", type: "button", style: "margin-top:10px", on: { click: () => { collapsed.delete(n.group); rebuild(true); } } }, "Expand group")) : null,
          el("div", { class: "mp-sect" }, `Calls to · ${out.length}`), list(out, "dst", "No outgoing calls"),
          el("div", { class: "mp-sect" }, `Called by · ${inn.length}`), list(inn, "src", "No callers"),
          !n.external && !n.collapsedGroup ? el("div", { class: "mp-actions" }, el("a", { class: "btn primary sm", href: "#/services/" + enc(n.id) }, "Open service"), el("a", { class: "btn sm", href: `#/traces?service=${enc(n.id)}` }, "Traces"), el("a", { class: "btn sm", href: `#/logs?service=${enc(n.id)}` }, "Logs")) : null);
        panelBox.replaceChildren(el("div", { class: "mp-ph" }, el("div", { class: "t" }, trunc(n.name, 30)), el("button", { class: "btn sm icon ghost", type: "button", "aria-label": "Close details", on: { click: () => select(null) } }, icon("x", 14))), body);
        if (n.n && !n.collapsedGroup) api(`/services/${enc(n.id)}/timeseries?minutes=${M()}&buckets=30`).then(ts => { if (st.sel === n.id) spark.replaceChildren(sparkline(ts.map(x => x.rate), { w: 296, h: 46, color: n.err_pct >= 5 ? "var(--err)" : "var(--c1)" })); }).catch(() => spark.replaceChildren(el("span", { class: "muted" }, "unavailable")));
      }

      // ---------------------------------------------------------------- search
      function runSearch() {
        const list = base ? K.search(base, searchIn.value).slice(0, 8) : [];
        results.hidden = !list.length && !searchIn.value.trim();
        results.replaceChildren(...(list.length ? list.map((n, i) => el("button", { type: "button", class: "it" + (i === 0 ? " sel" : ""), role: "option", on: { mousedown: ev => { ev.preventDefault(); pick(n); } } }, el("span", { class: "dot " + stateOf(n) }), el("span", { class: "nm" }, n.name), el("small", {}, n.external ? "external" : [n.language, n.namespace].filter(Boolean).join(" · ")))) : searchIn.value.trim() ? [el("div", { class: "it none" }, "No matching service")] : []));
        results._list = list;
      }
      function pick(n) {
        searchIn.value = ""; results.hidden = true; searchIn.blur();
        const hidden = !g.byId.has(n.id) && n.group && collapsed.has(n.group);
        if (hidden) { collapsed.delete(n.group); rebuild(false); }
        if (!g.byId.has(n.id)) return P.toast("That service is hidden by the current filters", "err");
        select(n.id, { focus: true });
      }
      searchIn.addEventListener("input", runSearch);
      searchIn.addEventListener("focus", runSearch);
      searchIn.addEventListener("blur", () => setTimeout(() => (results.hidden = true), 120));
      searchIn.addEventListener("keydown", ev => { if (ev.key === "Enter" && results._list && results._list[0]) { ev.preventDefault(); pick(results._list[0]); } else if (ev.key === "Escape") { searchIn.value = ""; results.hidden = true; searchIn.blur(); } });

      await load();   // data errors: auth -> router card; others -> in-page error state with retry
    },
  });
})();
