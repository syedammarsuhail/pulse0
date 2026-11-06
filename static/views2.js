/* Pulse0 pages, part 2: Metrics, Dashboards, Alerts, SLOs, Synthetics, Setup, Settings */
(function () {
  const P = window.P, { el, icon, api, qs, compact, fmt, ms, pct, ago, stamp, pill, copyText, chart, sparkline, dataTable, empty, panel, kpi, head, codeBlock, field, toast, modal, confirmBox, setParams, nav, store, S } = P;
  const M = () => P.minutes(), enc = encodeURIComponent, post = (p, b) => api(p, { method: "POST", body: b }), del = p => api(p, { method: "DELETE" });
  const opt = (v, l, sel) => el("option", { value: v, selected: !!sel }, l ?? v);

  // ============================================================ METRICS EXPLORER
  P.route("/metrics", {
    title: "Metrics", live: false,
    async render(page, { q }) {
      const [names, svcs] = await Promise.all([api("/metrics/names"), P.services(true)]);
      page.append(head("Metrics", `${names.length} metric${names.length === 1 ? "" : "s"} discovered automatically from OpenTelemetry data`));
      if (!names.length) { page.append(el("section", { class: "panel fill" }, empty("No metrics received yet", "Send OTLP metrics, or enable the bundled collector's host and kubelet receivers."))); return; }
      const st = { name: q.get("name") || names[0].name, service: q.get("service") || "", agg: q.get("agg") || "", group: q.get("group") || "" };
      if (!names.find(n => n.name === st.name)) st.name = names[0].name;
      const list = el("div", { class: "pb", style: "padding:4px 6px", "data-keep": "mlist" }), main = el("div", { class: "col fill", style: "min-width:0;overflow-y:auto" });
      const search = el("input", { type: "search", placeholder: "Filter metrics…", style: "width:100%", on: { input: () => paintList() } });
      function paintList() {
        const t = search.value.toLowerCase();
        list.replaceChildren(...names.filter(n => n.name.toLowerCase().includes(t)).map(n => el("div", { class: "fi facet", style: "padding:0" }, el("div", { class: "fi" + (n.name === st.name ? " on" : ""), style: "gap:6px", on: { click: () => { st.name = n.name; st.agg = ""; sync(); paintList(); load(); } } }, el("span", { class: "mono", style: "font-size:12px" }, n.name), el("span", { class: "n" }, n.mtype)))));
      }
      const sync = () => setParams({ name: st.name, service: st.service, agg: st.agg, group: st.group });
      async function load() {
        const n = names.find(x => x.name === st.name), agg = st.agg || (n.mtype === "sum" ? "sum" : "avg");
        const svcSel = el("select", { on: { change: e => { st.service = e.target.value; sync(); load(); } } }, opt("", "All services"), ...svcs.map(s => opt(s.service, s.service, s.service === st.service)));
        const aggSel = el("select", { on: { change: e => { st.agg = e.target.value; sync(); load(); } } }, ...["avg", "sum", "min", "max", "count"].map(a => opt(a, a, a === agg)));
        const grp = el("input", { type: "text", placeholder: "group by attribute (e.g. http.method)", value: st.group, style: "width:250px", on: { keydown: e => { if (e.key === "Enter") { st.group = e.target.value.trim(); sync(); load(); } } } });
        const d = await api("/metrics/query?" + qs({ name: st.name, service: st.service, minutes: M(), buckets: 80, agg, group_by: st.group }));
        const series = Object.entries(d.series).map(([name, values]) => ({ name, values })), vals = a => a.filter(v => v != null);
        const rows = series.map(s => { const v = vals(s.values); return { name: s.name, last: v[v.length - 1], min: Math.min(...v), max: Math.max(...v), avg: v.reduce((a, b) => a + b, 0) / (v.length || 1), spark: s.values }; });
        main.replaceChildren(
          el("div", { class: "toolbar" }, el("b", { class: "mono", style: "font-size:14px" }, st.name), pill(n.mtype, "acc"), n.unit ? pill(n.unit, "") : null, el("span", { class: "sp" }), svcSel, aggSel, grp, el("button", { class: "btn primary", on: { click: () => addToDashboard({ type: "area", title: st.name, source: "metric", metric: st.name, agg, group_by: st.group, service: st.service }) } }, icon("plus", 14), "Add to dashboard")),
          panel("Query result", series.length ? chart({ t: d.t, type: "line", h: 210, series }) : empty("No datapoints in range"), { sub: `${n.points.toLocaleString()} datapoints · ${n.services || "no service"}`, style: "flex:none" }),
          el("section", { class: "panel fill", style: "flex:1 0 220px" }, dataTable({ keep: "mrows", rows, sortKey: "last", sortDir: -1, cols: [{ key: "name", label: "Series" }, { key: "last", label: "Latest", num: true, render: r => compact(r.last) }, { key: "avg", label: "Avg", num: true, render: r => compact(r.avg) }, { key: "min", label: "Min", num: true, render: r => compact(r.min) }, { key: "max", label: "Max", num: true, render: r => compact(r.max) }, { key: "spark", label: "Trend", sort: false, render: r => sparkline(r.spark, { w: 120 }) }], empty: empty("No series") })));
      }
      page.append(el("div", { class: "row fill metrics-row" }, el("section", { class: "panel", style: "width:290px;flex:none" }, el("div", { class: "ph" }, search), list), main));
      paintList(); await load();
    },
  });

  // ============================================================ DASHBOARDS
  const stepsW = [3, 4, 6, 8, 12], stepsH = [2, 3, 4, 5, 6];
  async function loadDash() { return api("/dashboards"); }
  async function saveDash(d) { await post("/dashboards", { name: d.name, panels: d.panels }); }
  async function panelData(p) {
    if (p.source === "service") { const ts = await api(`/services/${enc(p.service)}/timeseries?minutes=${M()}&buckets=40`), k = p.signal || "rate"; return { t: ts.map(x => x.t), series: [{ name: `${p.service} · ${k}`, values: ts.map(x => x[k]) }] }; }
    const d = await api("/metrics/query?" + qs({ name: p.metric, service: p.service, minutes: M(), buckets: 40, agg: p.agg || "avg", group_by: p.group_by }));
    return { t: d.t, series: Object.entries(d.series).map(([name, values]) => ({ name, values })) };
  }
  async function panelForm(p0) {
    const [names, svcs] = await Promise.all([api("/metrics/names"), P.services(true)]);
    const p = { type: "area", title: "", source: "metric", metric: names[0]?.name || "", agg: "avg", group_by: "", service: "", signal: "rate", w: 6, h: 3, ...p0 };
    const title = el("input", { type: "text", value: p.title, placeholder: "Panel title" }), type = el("select", {}, ...[["area", "Area chart"], ["line", "Line chart"], ["bar", "Bar chart"], ["stat", "Single stat"]].map(([v, l]) => opt(v, l, v === p.type)));
    const source = el("select", { on: { change: sync } }, opt("metric", "OpenTelemetry metric", p.source === "metric"), opt("service", "Service traffic (traces)", p.source === "service"));
    const metric = el("select", {}, ...names.map(n => opt(n.name, n.name, n.name === p.metric))), agg = el("select", {}, ...["avg", "sum", "min", "max", "count"].map(a => opt(a, a, a === p.agg)));
    const grp = el("input", { type: "text", value: p.group_by, placeholder: "optional attribute" }), svc = el("select", {}, opt("", "All services", !p.service), ...svcs.map(s => opt(s.service, s.service, s.service === p.service)));
    const sig = el("select", {}, ...[["rate", "Request rate"], ["err", "Error rate"], ["avg_ms", "Avg latency"]].map(([v, l]) => opt(v, l, v === p.signal)));
    const w = el("select", {}, ...stepsW.map(x => opt(x, `${x}/12 width`, x === p.w))), h = el("select", {}, ...stepsH.map(x => opt(x, `${x} rows tall`, x === p.h)));
    const fm = el("div", { class: "form" }, field("Title", title, null, "wide"), field("Visualisation", type), field("Data source", source), field("Metric", metric, null, "pf-metric"), field("Aggregation", agg, null, "pf-metric"), field("Group by", grp, null, "pf-metric"), field("Signal", sig, null, "pf-signal"), field("Service", svc), field("Width", w), field("Height", h));
    function sync() { const m = source.value === "metric"; fm.querySelectorAll(".pf-metric").forEach(x => x.style.display = m ? "" : "none"); fm.querySelectorAll(".pf-signal").forEach(x => x.style.display = m ? "none" : ""); }
    sync();
    return { body: fm, read: () => ({ ...p, id: p.id || "p" + Math.random().toString(36).slice(2, 8), title: title.value.trim() || (source.value === "metric" ? metric.value : `${svc.value || "all"} ${sig.value}`), type: type.value, source: source.value, metric: metric.value, agg: agg.value, group_by: grp.value.trim(), service: svc.value, signal: sig.value, w: +w.value, h: +h.value }) };
  }
  async function addToDashboard(panel) {
    const dashes = await loadDash(), sel = el("select", {}, ...dashes.map(d => opt(d.name)), opt("__new", "＋ New dashboard…")), nm = el("input", { type: "text", placeholder: "New dashboard name", style: dashes.length ? "display:none" : "" });
    if (!dashes.length) sel.value = "__new"; sel.addEventListener("change", () => nm.style.display = sel.value === "__new" ? "" : "none");
    modal({ title: "Add panel to dashboard", body: el("div", { class: "col" }, field("Dashboard", sel), nm), actions: [{ label: "Cancel" }, { label: "Add panel", primary: true, onClick: async () => { const name = sel.value === "__new" ? nm.value.trim() : sel.value; if (!name) { toast("Enter a dashboard name", "err"); return false; } const d = dashes.find(x => x.name === name) || { name, panels: [] }; d.panels.push({ w: 6, h: 3, ...panel, id: "p" + Math.random().toString(36).slice(2, 8) }); await saveDash(d); toast(`Added to “${name}”`, "ok"); } }] });
  }
  P.route("/dashboards", {
    title: "Dashboards", live: false,
    async render(page) {
      const list = await loadDash();
      page.append(head("Dashboards", "Build your own views on top of metrics and service traffic", el("button", { class: "btn primary", on: { click: newDash } }, icon("plus", 14), "New dashboard")));
      if (!list.length) { page.append(el("section", { class: "panel fill" }, empty("No dashboards yet", "Create one, then add metric or service panels.", el("button", { class: "btn primary", on: { click: newDash } }, "Create dashboard")))); return; }
      page.append(el("div", { class: "cards" }, ...list.map(d => el("a", { class: "card", href: "#/dashboards/" + d.id }, el("div", { style: "display:flex;align-items:center;gap:8px" }, icon("dash", 16), el("h3", {}, d.name), el("button", { class: "btn sm icon ghost danger", style: "margin-left:auto", title: "Delete", on: { click: async e => { e.preventDefault(); e.stopPropagation(); if (await confirmBox(`Delete dashboard “${d.name}”?`)) { await del("/dashboards/" + d.id); P.render(); } } } }, icon("trash", 14))), el("p", {}, `${d.panels.length} panel${d.panels.length === 1 ? "" : "s"}`), el("p", { class: "muted" }, "Updated " + ago(d.updated * 1000))))));
      function newDash() { const n = el("input", { type: "text", placeholder: "e.g. Checkout health" }); modal({ title: "New dashboard", body: field("Name", n), actions: [{ label: "Cancel" }, { label: "Create", primary: true, onClick: async () => { if (!n.value.trim()) return false; await saveDash({ name: n.value.trim(), panels: [] }); const d = (await loadDash()).find(x => x.name === n.value.trim()); nav("/dashboards/" + d.id); } }] }); }
    },
  });
  P.route("/dashboards/:id", {
    title: "Dashboard", live: false,
    crumbs: async ({ params }) => { const d = (await loadDash()).find(x => String(x.id) === params.id); return [["Dashboards", "#/dashboards"], [d ? d.name : "Not found"]]; },
    async render(page, { params, q }) {
      const d = (await loadDash()).find(x => String(x.id) === params.id);
      if (!d) { page.append(el("section", { class: "panel fill" }, empty("Dashboard not found"))); return; }
      const editing = q.get("edit") === "1";
      const persist = async () => { await saveDash(d); P.render(); };
      const move = (i, dir) => { const j = i + dir; if (j < 0 || j >= d.panels.length) return; [d.panels[i], d.panels[j]] = [d.panels[j], d.panels[i]]; persist(); };
      const cyc = (arr, v) => arr[(arr.indexOf(v) + 1) % arr.length];
      async function editPanel(i) { const f = await panelForm(i == null ? {} : d.panels[i]); modal({ title: i == null ? "Add panel" : "Edit panel", body: f.body, width: 640, actions: [{ label: "Cancel" }, { label: i == null ? "Add panel" : "Save", primary: true, onClick: async () => { const v = f.read(); if (i == null) d.panels.push(v); else d.panels[i] = v; await saveDash(d); P.render(); } }] }); }
      page.append(head(d.name, `${d.panels.length} panel${d.panels.length === 1 ? "" : "s"} · ${P.rangeLabel(M()).toLowerCase()}`,
        el("button", { class: "btn" + (editing ? " primary" : ""), on: { click: () => { setParams({ edit: editing ? "" : "1" }); P.render(); } } }, icon(editing ? "check" : "edit", 14), editing ? "Done" : "Edit layout"), el("button", { class: "btn primary", on: { click: () => editPanel(null) } }, icon("plus", 14), "Add panel")));
      if (!d.panels.length) { page.append(el("section", { class: "panel fill" }, empty("This dashboard is empty", "Add a panel from a metric or a service's traffic.", el("button", { class: "btn primary", on: { click: () => editPanel(null) } }, "Add first panel")))); return; }
      const grid = el("div", { class: "dgrid", "data-keep": "dg" }); page.append(grid);
      await Promise.all(d.panels.map(async (p, i) => {
        const host = el("div", { class: "pb" }), ch = Math.max(70, (p.h || 3) * 104 - 12 - 44 - 22);
        const tools = el("div", { class: "r" }, ...(editing ? [["◀", "Move earlier", () => move(i, -1)], ["▶", "Move later", () => move(i, 1)], ["↔", "Change width", () => { p.w = cyc(stepsW, p.w || 6); persist(); }], ["↕", "Change height", () => { p.h = cyc(stepsH, p.h || 3); persist(); }]].map(([t, ti, fn]) => el("button", { class: "btn sm icon ghost", title: ti, on: { click: fn } }, t)) : []), el("button", { class: "btn sm icon ghost", title: "Edit", on: { click: () => editPanel(i) } }, icon("edit", 13)), el("button", { class: "btn sm icon ghost danger", title: "Remove", on: { click: async () => { if (await confirmBox(`Remove panel “${p.title}”?`, "Remove")) { d.panels.splice(i, 1); persist(); } } } }, icon("trash", 13)));
        const box = el("div", { class: "dpanel", style: `grid-column:span ${p.w || 6};grid-row:span ${p.h || 3}`, draggable: editing ? "true" : null, on: editing ? { dragstart: e => { e.dataTransfer.setData("text/plain", String(i)); e.dataTransfer.effectAllowed = "move"; }, dragover: e => e.preventDefault(), drop: e => { e.preventDefault(); const from = parseInt(e.dataTransfer.getData("text/plain"), 10); if (Number.isInteger(from) && from !== i && from >= 0 && from < d.panels.length) { const [moved] = d.panels.splice(from, 1); d.panels.splice(i, 0, moved); persist(); } } } : {} }, el("div", { class: "ph" }, el("span", { style: "overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, p.title), tools), host);
        grid.append(box);
        try {
          const data = await panelData(p), first = data.series[0]?.values.filter(v => v != null) || [];
          if (p.type === "stat") host.replaceChildren(el("div", { style: "display:flex;align-items:flex-end;justify-content:space-between;gap:10px" }, el("div", {}, el("div", { class: "stat-big" }, first.length ? compact(first[first.length - 1]) : "—"), el("div", { class: "muted" }, data.series[0]?.name || "no data")), sparkline(data.series[0]?.values || [], { w: 120, h: 44 })));
          else host.replaceChildren(data.series.length ? chart({ t: data.t, series: data.series, type: p.type, h: data.series.length > 1 ? ch - 26 : ch }) : el("div", { class: "muted", style: "text-align:center" }, "No data in range"));
        } catch (e) { host.replaceChildren(el("div", { class: "muted" }, e.message)); }
      }));
    },
  });

  // ============================================================ ALERTS
  const ruleDesc = r => `${r.signal}${r.expr ? `(${r.expr})` : ""} ${r.op} ${r.threshold}${r.service ? ` on ${r.service}` : ""} · ${r.window_s >= 60 ? r.window_s / 60 + "m" : r.window_s + "s"}`;
  async function ruleForm(p0 = {}) {
    const svcs = await P.services(true), v = { name: "", signal: "error_rate", expr: "", op: ">", threshold: 5, window_s: 300, service: "", webhook: "", ...p0 };
    const f = {
      name: el("input", { type: "text", value: v.name, placeholder: "High error rate" }),
      signal: el("select", {}, ...["error_rate", "p95_latency", "request_rate", "log_errors", "metric_avg", "metric_max", "synthetic_down"].map(s => opt(s, s, s === v.signal))),
      expr: el("input", { type: "text", value: v.expr, placeholder: "metric name / log text / synthetic id" }),
      op: el("select", {}, ...[">", ">=", "<", "<="].map(o => opt(o, o, o === v.op))), threshold: el("input", { type: "number", step: "any", value: v.threshold }),
      window_s: el("input", { type: "number", value: v.window_s }), service: el("select", {}, opt("", "All services", !v.service), ...svcs.map(s => opt(s.service, s.service, s.service === v.service))),
      webhook: el("input", { type: "text", value: v.webhook, placeholder: "https://hooks.slack.com/services/… (optional)" }),
    };
    const hints = { error_rate: "% of failed requests", p95_latency: "milliseconds", request_rate: "requests per second", log_errors: "count of ERROR logs; expression filters by text", metric_avg: "expression = metric name", metric_max: "expression = metric name", synthetic_down: "failed runs; expression = check id (optional)" };
    const hint = el("div", { class: "hint" }); const upd = () => hint.textContent = "Unit: " + hints[f.signal.value]; f.signal.addEventListener("change", upd); upd();
    return { body: el("div", { class: "form" }, field("Name", f.name, null, "wide"), field("Signal", f.signal), field("Expression", f.expr), field("Operator", f.op), field("Threshold", f.threshold), field("Window (seconds)", f.window_s), field("Service", f.service), el("div", { class: "wide" }, hint), field("Webhook (Slack-compatible)", f.webhook, null, "wide")), read: () => ({ name: f.name.value.trim(), signal: f.signal.value, expr: f.expr.value.trim(), op: f.op.value, threshold: +f.threshold.value, window_s: +f.window_s.value, service: f.service.value, webhook: f.webhook.value.trim() }) };
  }
  async function openRuleModal(p0) { const f = await ruleForm(p0); modal({ title: "Create alert rule", body: f.body, width: 680, actions: [{ label: "Cancel" }, { label: "Create rule", primary: true, onClick: async () => { await post("/alerts", f.read()); toast("Alert rule created", "ok"); nav("/alerts", { tab: "rules" }); P.render(); } }] }); }
  P.route("/alerts", {
    title: "Alerts",
    async render(page, { q }) {
      const tab = q.get("tab") || "rules", [rules, events, tpls] = await Promise.all([api("/alerts"), api("/alert-events"), api("/alerts/templates")]);
      const firing = rules.filter(r => r.state === "firing" && r.enabled).length;
      page.append(head("Alerts", `${rules.length} rules · ${firing ? firing + " firing" : "none firing"}`, el("button", { class: "btn", on: { click: () => nav("/alerts", { tab: "templates" }) } }, "Browse templates"), el("button", { class: "btn primary", on: { click: () => openRuleModal() } }, icon("plus", 14), "New rule")),
        el("div", { class: "tabs" }, ...[["rules", `Rules (${rules.length})`], ["events", `Events (${events.length})`], ["templates", `Templates (${tpls.length})`]].map(([k, l]) => el("a", { href: `#/alerts?tab=${k}`, class: tab === k ? "on" : "" }, l))));
      const body = el("div", { class: "col fill" }); page.append(body);
      if (tab === "rules") {
        body.append(el("section", { class: "panel fill" }, dataTable({
          keep: "rules", rows: rules, sortKey: "state", sortDir: -1,
          cols: [{ key: "state", label: "State", render: r => pill([el("i", { class: "d" }), r.enabled ? r.state : "paused"], !r.enabled ? "" : r.state === "firing" ? "err" : "ok") }, { key: "name", label: "Name", render: r => el("b", {}, r.name) }, { key: "signal", label: "Condition", sort: false, render: r => el("span", { class: "mono" }, ruleDesc(r)) },
            { key: "last_value", label: "Last value", num: true, render: r => r.last_value != null ? compact(r.last_value) : "—" }, { key: "since", label: "Since", render: r => r.since ? el("span", { class: "muted" }, ago(r.since * 1000)) : "—" },
            { key: "id", label: "", sort: false, num: true, render: r => el("span", { style: "display:inline-flex;gap:6px" }, el("button", { class: "btn sm", on: { click: async () => { await post(`/alerts/${r.id}/toggle`); P.render(); } } }, r.enabled ? "Pause" : "Resume"), el("button", { class: "btn sm danger", on: { click: async () => { if (await confirmBox(`Delete rule “${r.name}”?`)) { await del("/alerts/" + r.id); P.render(); } } } }, icon("trash", 13))) }],
          empty: empty("No alert rules yet", "Start from a template — most take one click.", el("button", { class: "btn primary", on: { click: () => nav("/alerts", { tab: "templates" }) } }, "Browse templates")),
        })));
      } else if (tab === "events") {
        body.append(el("section", { class: "panel fill" }, dataTable({ keep: "events", rows: events, sortKey: "ts", sortDir: -1, cols: [{ key: "ts", label: "Time", render: r => el("span", { class: "muted" }, stamp(r.ts * 1000)) }, { key: "name", label: "Rule", render: r => r.name || "—" }, { key: "state", label: "State", render: r => pill(r.state, r.state === "firing" ? "err" : "ok") }, { key: "value", label: "Value", num: true, render: r => compact(r.value) }, { key: "msg", label: "Message" }], empty: empty("No alert events yet", "Events appear when a rule starts or stops firing.") })));
      } else {
        const cats = ["All", ...new Set(tpls.map(t => t.category))], cur = q.get("cat") || "All", term = q.get("t") || "";
        const grid = el("div", { class: "cards" });
        const paint = () => { const t = search.value.toLowerCase(); grid.replaceChildren(...tpls.filter(x => (cur === "All" || x.category === cur) && (!t || (x.name + x.description).toLowerCase().includes(t))).map(x => el("div", { class: "card", style: "cursor:default" }, el("div", { style: "display:flex;gap:8px;align-items:center" }, el("h3", {}, x.name), pill(x.category, "acc")), el("p", {}, x.description), el("p", { class: "mono muted" }, ruleDesc(x)), el("div", { style: "margin-top:4px" }, el("button", { class: "btn sm primary", on: { click: () => openRuleModal(x) } }, "Use template"))))); if (!grid.children.length) grid.append(empty("No templates match")); };
        const search = el("input", { type: "search", placeholder: "Search templates…", value: term, style: "width:260px", on: { input: paint } });
        body.append(el("div", { class: "toolbar" }, search, ...cats.map(c => el("button", { class: "chipbtn" + (c === cur ? " on" : ""), on: { click: () => { setParams({ cat: c === "All" ? "" : c }); P.render(); } } }, c))), el("div", { class: "fill", style: "overflow:auto", "data-keep": "tpl" }, grid)); paint();
      }
    },
  });

  // ============================================================ SLOs
  P.route("/slos", {
    title: "SLOs",
    async render(page) {
      const [slos, svcs] = await Promise.all([api("/slos"), P.services()]);
      page.append(head("Service level objectives", "Availability + latency targets with error-budget tracking", el("button", { class: "btn primary", on: { click: newSlo } }, icon("plus", 14), "New SLO")));
      if (!slos.length) { page.append(el("section", { class: "panel fill" }, empty("No SLOs yet", "Define a target such as “99.5% of checkout requests succeed under 500ms”.", el("button", { class: "btn primary", on: { click: newSlo } }, "Create SLO")))); return; }
      page.append(el("div", { class: "cards", "data-keep": "slo" }, ...slos.map(s => {
        const used = s.error_budget_used_pct, ok = s.attainment_pct == null || s.attainment_pct >= s.objective_pct, kind = used == null ? "" : used >= 100 ? "err" : used >= 75 ? "warn" : "ok";
        return el("div", { class: "card", style: "cursor:default;gap:10px" }, el("div", { style: "display:flex;align-items:center;gap:8px" }, el("h3", {}, s.name), pill(ok ? "meeting target" : "breaching", ok ? "ok" : "err"), el("button", { class: "btn sm icon ghost danger", style: "margin-left:auto", on: { click: async () => { if (await confirmBox(`Delete SLO “${s.name}”?`)) { await del("/slos/" + s.id); P.render(); } } } }, icon("trash", 14))),
          el("div", { class: "muted" }, `${s.service} · ${s.objective_pct}% within ${s.latency_ms}ms · ${s.window_days}d window`),
          el("div", { style: "display:flex;align-items:baseline;gap:10px" }, el("span", { class: "stat-big", style: "font-size:30px" }, s.attainment_pct == null ? "—" : s.attainment_pct + "%"), el("span", { class: "muted" }, `${compact(s.events)} events`)),
          el("div", {}, el("div", { class: "muted", style: "font-size:12px;margin-bottom:4px" }, used == null ? "Error budget: no data" : `Error budget used: ${Math.min(999, used).toFixed(0)}%`), el("div", { class: "meter" }, el("i", { style: `width:${Math.min(100, Math.max(0, used || 0))}%;background:var(--${kind === "err" ? "err" : kind === "warn" ? "warn" : "ok"})` }))));
      })));
      function newSlo() {
        const f = { name: el("input", { type: "text", placeholder: "Checkout availability" }), service: el("select", {}, ...svcs.map(s => opt(s.service))), obj: el("input", { type: "number", step: "0.1", value: 99.5 }), lat: el("input", { type: "number", value: 500 }), win: el("input", { type: "number", value: 30 }) };
        modal({ title: "New SLO", body: el("div", { class: "form" }, field("Name", f.name, null, "wide"), field("Service", f.service), field("Objective (%)", f.obj), field("Latency threshold (ms)", f.lat, "Slower requests count against the objective"), field("Window (days)", f.win)), actions: [{ label: "Cancel" }, { label: "Create SLO", primary: true, onClick: async () => { await post("/slos", { name: f.name.value.trim(), service: f.service.value, objective_pct: +f.obj.value, latency_ms: +f.lat.value, window_days: +f.win.value }); P.render(); } }] });
      }
    },
  });

  // ============================================================ SYNTHETICS
  P.route("/synthetics", {
    title: "Synthetics",
    async render(page) {
      const rows = await api("/synthetics");
      page.append(head("Synthetic monitoring", "HTTP availability, latency and TLS-expiry checks", el("button", { class: "btn primary", on: { click: addCheck } }, icon("plus", 14), "New check")));
      page.append(el("section", { class: "panel fill" }, dataTable({
        keep: "synth", rows, sortKey: "name", sortDir: 1,
        cols: [{ key: "last", label: "Status", sort: r => r.last ? r.last.ok : -1, render: r => r.last ? pill([el("i", { class: "d" }), r.last.ok ? "Up" : "Down"], r.last.ok ? "ok" : "err", r.last.err) : pill("pending") }, { key: "name", label: "Name", render: r => el("b", {}, r.name) }, { key: "url", label: "URL", render: r => el("span", { class: "mono muted" }, r.url) },
          { key: "lat", label: "Latency", num: true, sort: r => r.last?.ms ?? 0, render: r => r.last ? ms(r.last.ms) : "—" }, { key: "uptime", label: "Uptime", num: true, render: r => r.uptime != null ? pct(r.uptime) : "—" }, { key: "ssl", label: "TLS expiry", num: true, sort: r => r.last?.ssl_days ?? 1e9, render: r => r.last?.ssl_days != null ? Math.round(r.last.ssl_days) + " days" : "—" },
          { key: "h", label: "History", sort: false, render: r => el("span", { style: "display:inline-flex;gap:2px;align-items:center" }, ...(r.history || []).slice(-30).map(x => el("i", { title: x.err || "ok", style: `width:4px;height:16px;border-radius:2px;background:var(--${x.ok ? "ok" : "err"})` }))) },
          { key: "id", label: "", sort: false, num: true, render: r => el("span", { style: "display:inline-flex;gap:6px" }, el("button", { class: "btn sm", on: { click: async () => { await post(`/synthetics/${r.id}/run`); toast("Check executed", "ok"); P.render(); } } }, icon("play", 12), "Run"), el("button", { class: "btn sm danger", on: { click: async () => { if (await confirmBox(`Delete check “${r.name}”?`)) { await del("/synthetics/" + r.id); P.render(); } } } }, icon("trash", 13))) }],
        empty: empty("No synthetic checks", "Monitor an HTTP endpoint from inside your cluster.", el("button", { class: "btn primary", on: { click: addCheck } }, "Create check")),
      })));
      function addCheck() {
        const f = { name: el("input", { type: "text", placeholder: "Homepage" }), url: el("input", { type: "text", placeholder: "https://example.com/health" }), iv: el("input", { type: "number", value: 60 }), st: el("input", { type: "number", value: 200 }), mx: el("input", { type: "number", value: 2000 }) };
        modal({ title: "New synthetic check", body: el("div", { class: "form" }, field("Name", f.name), field("URL", f.url, null, "wide"), field("Interval (s)", f.iv), field("Expected status", f.st), field("Max latency (ms)", f.mx)), actions: [{ label: "Cancel" }, { label: "Create check", primary: true, onClick: async () => { await post("/synthetics", { name: f.name.value.trim(), url: f.url.value.trim(), interval_s: +f.iv.value, expect_status: +f.st.value, max_ms: +f.mx.value }); P.render(); } }] });
      }
    },
  });

  // ============================================================ SETUP
  P.route("/setup", {
    title: "Setup & integrations", live: false,
    async render(page, { q }) {
      page.classList.add("scroll");
      const ep = location.origin, host = location.hostname, tok = store.get("token", ""), auth = tok ? "<YOUR_TOKEN>" : "";
      const hdr = tok ? `\nOTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer ${auth}"` : "";
      const SN = {
        python: ["Python", `pip install opentelemetry-distro opentelemetry-exporter-otlp-proto-http\nopentelemetry-bootstrap --action=install\n\nOTEL_SERVICE_NAME=my-service \\\nOTEL_EXPORTER_OTLP_ENDPOINT=${ep} \\\nOTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \\\nOTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true \\\nopentelemetry-instrument python app.py${hdr ? "\n# also set: " + hdr.trim() : ""}`],
        node: ["Node.js", `npm i @opentelemetry/api @opentelemetry/auto-instrumentations-node\n\nOTEL_SERVICE_NAME=my-service \\\nOTEL_EXPORTER_OTLP_ENDPOINT=${ep} \\\nOTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \\\nnode --require @opentelemetry/auto-instrumentations-node/register app.js`],
        java: ["Java", `curl -L -O https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/latest/download/opentelemetry-javaagent.jar\n\njava -javaagent:opentelemetry-javaagent.jar \\\n  -Dotel.service.name=my-service \\\n  -Dotel.exporter.otlp.endpoint=${ep} \\\n  -Dotel.exporter.otlp.protocol=http/protobuf \\\n  -jar app.jar`],
        dotnet: [".NET", `dotnet add package OpenTelemetry.AutoInstrumentation\n\nOTEL_SERVICE_NAME=my-service \\\nOTEL_EXPORTER_OTLP_ENDPOINT=${ep} \\\nOTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \\\n./otel-dotnet-auto-instrument.sh dotnet MyApp.dll`],
        go: ["Go", `go get go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp\n\n// exporter\nexp, _ := otlptracehttp.New(ctx,\n    otlptracehttp.WithEndpointURL("${ep}/v1/traces"))\n\n// or zero-code with the Operator: instrumentation.opentelemetry.io/inject-go: "true"`],
        browser: ["Browser (RUM)", `<script type="module">\n  import { WebTracerProvider } from "https://esm.sh/@opentelemetry/sdk-trace-web";\n  import { OTLPTraceExporter } from "https://esm.sh/@opentelemetry/exporter-trace-otlp-http";\n  import { BatchSpanProcessor } from "https://esm.sh/@opentelemetry/sdk-trace-base";\n  const p = new WebTracerProvider({ spanProcessors: [new BatchSpanProcessor(\n    new OTLPTraceExporter({ url: "${ep}/v1/traces" }))] });\n  p.register();\n</script>`],
        collector: ["Collector", `exporters:\n  otlphttp/pulse0:\n    endpoint: ${ep}${tok ? `\n    headers:\n      Authorization: "Bearer ${auth}"` : ""}\n\nservice:\n  pipelines:\n    traces:  { exporters: [otlphttp/pulse0] }\n    metrics: { exporters: [otlphttp/pulse0] }\n    logs:    { exporters: [otlphttp/pulse0] }`],
        k8s: ["Kubernetes", `# Install Pulse0 (bundles an OpenTelemetry Collector DaemonSet)\nhelm upgrade --install pulse0 ./helm/pulse0 -n observability --create-namespace\n\n# Point workloads at the in-cluster collector\nOTEL_EXPORTER_OTLP_ENDPOINT=http://pulse0-collector.observability.svc.cluster.local:4319\n\n# Zero-code auto-instrumentation (needs the OpenTelemetry Operator)\nkubectl annotate deployment my-app instrumentation.opentelemetry.io/inject-python="true"`],
      };
      const tab = q.get("lang") || "python", out = el("div");
      const show = k => { out.replaceChildren(codeBlock(SN[k][1])); document.querySelectorAll(".lang-tabs a").forEach(a => a.classList.toggle("on", a.dataset.k === k)); setParams({ lang: k }); };
      const test = el("button", { class: "btn", on: { click: async () => { const now = Date.now() * 1e6, hex = n => [...crypto.getRandomValues(new Uint8Array(n))].map(b => b.toString(16).padStart(2, "0")).join(""); const h = { "Content-Type": "application/json" }; if (tok) h.Authorization = "Bearer " + tok; const r = await fetch("/v1/traces", { method: "POST", headers: h, body: JSON.stringify({ resourceSpans: [{ resource: { attributes: [{ key: "service.name", value: { stringValue: "pulse0-setup-test" } }] }, scopeSpans: [{ spans: [{ traceId: hex(16), spanId: hex(8), name: "setup test span", kind: 2, startTimeUnixNano: String(now - 5e6), endTimeUnixNano: String(now), status: { code: 1 }, attributes: [] }] }] }] }) }); toast(r.ok ? "Test span sent — see it under Services → pulse0-setup-test" : "Failed: HTTP " + r.status, r.ok ? "ok" : "err"); } } }, icon("play", 13), "Send test span");
      page.append(head("Setup & integrations", "Point OpenTelemetry at Pulse0 — one endpoint for traces, logs and metrics", test),
        el("div", { class: "grid", style: "grid-template-columns:repeat(auto-fit,minmax(240px,1fr))" }, ...[["Traces", "/v1/traces"], ["Logs", "/v1/logs"], ["Metrics", "/v1/metrics"]].map(([l, p]) => el("div", { class: "kpi" }, el("div", { class: "k" }, `OTLP/HTTP · ${l}`), el("div", { class: "mono", style: "margin-top:6px;overflow-wrap:anywhere" }, ep + p), el("div", {}, el("button", { class: "btn sm", style: "margin-top:8px", on: { click: () => copyText(ep + p) } }, icon("copy", 12), "Copy"))))),
        panel("Instrument an application", el("div", {}, el("div", { class: "tabs lang-tabs", style: "margin:-12px -14px 12px" }, ...Object.entries(SN).map(([k, [l]]) => el("a", { "data-k": k, href: "javascript:void 0", class: k === tab ? "on" : "", on: { click: () => show(k) } }, l))), out), { style: "flex:none" }),
        el("div", { class: "grid", style: "grid-template-columns:repeat(auto-fit,minmax(280px,1fr))" }, panel("Protocol", el("div", { class: "muted" }, "OTLP over HTTP, JSON or protobuf, gzip supported. gRPC is not served — use http/protobuf in SDKs and the Collector's otlphttp exporter.")), panel("Authentication", el("div", { class: "muted" }, tok ? "A token is saved in this browser. Add it to exporters as an Authorization: Bearer header." : "No token configured in this browser. If the server sets PULSE0_TOKEN, add it in Settings and as a Bearer header on exporters.")), panel("Zero-code on Kubernetes", el("div", { class: "muted" }, "Install the OpenTelemetry Operator, then annotate any Deployment with instrumentation.opentelemetry.io/inject-<lang>=\"true\" — no source changes."))));
      show(tab);
    },
  });

  // ============================================================ SETTINGS
  P.route("/settings", {
    title: "Settings", live: false, range: false,
    async render(page) {
      const stats = await api("/stats").catch(() => null);
      const ai = await api("/assistant/status").catch(() => null); if (ai) P.aiStatus = ai;
      const tok = el("input", { type: "password", value: store.get("token", ""), placeholder: "Bearer token (only if PULSE0_TOKEN is set)", style: "width:min(420px,100%)" });
      const theme = el("select", { on: { change: e => { document.documentElement.dataset.theme = e.target.value; store.set("theme", e.target.value); } } }, opt("dark", "Dark", document.documentElement.dataset.theme !== "light"), opt("light", "Light", document.documentElement.dataset.theme === "light"));
      const rng = el("select", { on: { change: e => P.setRange(+e.target.value) } }, ...[5, 15, 30, 60, 180, 360, 720, 1440, 4320, 10080].map(m => opt(m, P.rangeLabel(m), m === M())));
      page.append(head("Settings", "Preferences are stored in this browser"),
        el("div", { class: "grid", style: "grid-template-columns:repeat(auto-fit,minmax(380px,1fr));align-items:start" },
          panel("Access", el("div", { class: "col" }, field("API token", tok, "Sent as Authorization: Bearer on every request. Leave empty when auth is disabled."), el("div", { style: "display:flex;gap:8px" }, el("button", { class: "btn primary", on: { click: () => { store.set("token", tok.value.trim()); toast("Token saved", "ok"); P.render(); } } }, "Save token"), el("button", { class: "btn", on: { click: () => { store.set("token", ""); tok.value = ""; toast("Token cleared"); } } }, "Clear")))),
          panel("AI assistant", el("div", { class: "col" },
            el("div", { style: "display:flex;gap:8px;align-items:center;flex-wrap:wrap" }, ai ? (ai.enabled ? pill("Claude enabled", "ok") : pill("Rule-based only", "")) : pill("status unavailable", "warn"), ai && ai.enabled ? el("span", { class: "mono muted" }, ai.model) : null),
            el("div", { class: "muted" }, ai && ai.enabled ? "Ask Pulse0 answers with Claude using read-only queries over your traces, logs, metrics, alerts and SLOs. If Claude is unreachable it falls back to the built-in rules automatically." : "Ask Pulse0 currently uses built-in keyword rules. Add an Anthropic API key to turn on the Claude-backed assistant. The key is stored only as a Kubernetes secret, never in this UI."),
            el("div", { class: "muted", style: "font-size:12px" }, "Kubernetes — create the secret once (replace the placeholder), enable it in Helm, and restart:"),
            codeBlock(["kubectl -n observability create secret generic pulse0-anthropic --from-literal=api-key=<YOUR_ANTHROPIC_API_KEY>", "helm upgrade pulse0 ./helm/pulse0 -n observability --reuse-values --set ai.enabled=true --set ai.existingSecret=pulse0-anthropic --set ai.secretKey=api-key", "kubectl -n observability rollout restart statefulset/pulse0"].join("\n")),
            el("div", { class: "muted", style: "font-size:12px" }, "Docker / local process:"),
            codeBlock("ANTHROPIC_API_KEY=<YOUR_ANTHROPIC_API_KEY> python server.py   # optional: PULSE0_AI_MODEL=claude-sonnet-5-5"))),
          panel("Display", el("div", { class: "col" }, field("Theme", theme), field("Default time range", rng))),
          panel("This Pulse0", stats ? el("div", { class: "kv", style: "margin:-12px -14px" }, ...[["Spans stored", stats.spans.toLocaleString()], ["Log records", stats.logs.toLocaleString()], ["Metric points", stats.metrics.toLocaleString()], ["Services", stats.services], ["Alert rules", stats.alert_rules], ["Synthetic checks", stats.synthetics], ["Retention", stats.retention_hours + " hours"], ["Database", stats.db_path]].flatMap(([k, v]) => [el("div", {}, k), el("div", { class: "mono" }, String(v))])) : empty("Stats unavailable"))));
    },
  });
})();
