/* Pulse0 pages, part 1: Home, Services, Service map, Traces, Logs */
(function () {
  const P = window.P, { el, sv, icon, api, qs, compact, fmt, ms, pct, rate, clock, stamp, ago, sevName, pill, errPill, healthPill, copyText, chart, sparkline, dataTable, empty, panel, kpi, head, codeBlock, field, toast, setParams, nav, COLORS } = P;
  const M = () => P.minutes(), enc = encodeURIComponent;
  const sevIcon = s => s === "critical" || s === "warning" ? "warn" : "info";
  const errColor = p => p >= 5 ? "var(--err)" : p >= 1 ? "var(--warn)" : "var(--ok)";

  const insightRow = i => el("a", { class: "insight " + i.severity, href: i.link }, el("div", { class: "ic" }, icon(sevIcon(i.severity), 15)), el("div", { style: "min-width:0" }, el("div", { class: "t" }, i.title), el("div", { class: "d" }, i.detail)), i.service ? el("span", { class: "svc" }, pill(i.service, "")) : null,
    el("button", { class: "btn sm ghost icon explain", title: P.aiStatus?.enabled ? "Explain with Claude" : "Explain (rule-based)", "aria-label": "Explain this insight", on: { click: e => { e.preventDefault(); e.stopPropagation(); P.explain("this insight", `${i.title}. ${i.detail}${i.service ? ` (service: ${i.service})` : ""}`); } } }, icon("sparkle", 14)));
  const insightsPanel = (list, extra = {}) => panel("Live insights", list.length ? el("div", {}, ...list.map(insightRow)) : empty("All clear", "No anomalies detected in this window."), { sub: list.length ? `${list.length} active` : "", bodyCls: "pb", cls: extra.cls || "", style: "" });

  // ============================================================ HOME
  P.route("/home", {
    title: "Home",
    async render(page) {
      page.classList.add("scroll");
      const m = M();
      const [h, ins] = await Promise.all([api(`/home?minutes=${m}&buckets=60`), api("/insights?minutes=" + m)]);
      const t = h.totals, T = h.timeseries;
      if (!t.services && !t.logs) {
        page.append(head("Welcome to Pulse0", "Nothing has been received yet."), panel("Get started", el("div", { style: "display:flex;flex-direction:column;gap:12px;align-items:flex-start" }, el("div", { class: "muted" }, "Point any OpenTelemetry SDK or Collector at this server and data shows up within seconds."), el("a", { class: "btn primary", href: "#/setup" }, "Open setup guide"))));
        return;
      }
      const crit = ins.filter(i => i.severity === "critical").length;
      page.append(
        head("Overview", `${t.services} services · ${compact(t.requests)} requests · ${compact(t.logs)} logs in the selected range`, el("button", { class: "btn", on: { click: () => P.openAsk("Give me an overview") } }, icon("sparkle", 15), "Ask Pulse0")),
        el("div", { class: "kpis" },
          kpi("Requests", compact(t.requests), `${rate(t.rate)} req/s`, { spark: sparkline(T.map(x => x.rate), { w: 70, h: 24 }), href: "#/traces" }),
          kpi("Error rate", pct(t.err_pct), `${compact(t.errors)} failed`, { cls: t.err_pct >= 5 ? "err" : t.err_pct >= 1 ? "warn" : "ok", spark: sparkline(T.map(x => x.err_rate), { w: 70, h: 24, color: "var(--err)" }), href: "#/traces?errors=1" }),
          kpi("Services", t.services, "reporting traces", { href: "#/services" }),
          kpi("Avg p95", ms(t.p95_avg), "across services", { href: "#/services" }),
          kpi("Error logs", compact(t.error_logs), `of ${compact(t.logs)} logs`, { cls: t.error_logs ? "warn" : "", href: "#/logs?sev=17" }),
          kpi("Insights", ins.length, crit ? `${crit} critical` : ins.length ? "needs a look" : "all clear", { cls: crit ? "err" : ins.length ? "warn" : "ok" })),
        el("div", { class: "row", style: "flex:none;height:340px" },
          panel("Traffic & errors", chart({ t: T.map(x => x.t), type: "area", h: 228, f: compact, series: [{ name: "requests/s", values: T.map(x => x.rate), color: "var(--c1)" }, { name: "errors/s", values: T.map(x => x.err_rate), color: "var(--err)" }] }), { sub: "per second", cls: "fill", style: "flex:2" }),
          el("section", { class: "panel fill", style: "flex:1;min-width:300px" }, el("div", { class: "ph" }, "Live insights", el("small", {}, ins.length ? `${ins.length} active` : ""), el("div", { class: "r" }, el("a", { class: "btn sm ghost", href: "#/alerts" }, "Alerts"))), el("div", { class: "pb", style: "padding:0", "data-keep": "ins" }, ins.length ? ins.map(insightRow) : empty("All clear", "No anomalies detected in this window.")))),
        el("div", { class: "grid", style: "grid-template-columns:repeat(auto-fit,minmax(300px,1fr));flex:none;height:270px" },
          panel("Top services by errors", rankTable(h.top_errors, "err_pct"), { bodyCls: "pb", cls: "fill" }),
          panel("Slowest services (p95)", rankTable(h.top_latency, "p95"), { cls: "fill" }),
          panel("Alerts & SLOs", el("div", {}, ...h.events.slice(0, 4).map(e => el("div", { class: "insight", style: "cursor:default" }, el("div", { class: "ic", style: e.state === "firing" ? "background:var(--err-bg);color:var(--err)" : "background:var(--ok-bg);color:var(--ok)" }, icon(e.state === "firing" ? "alerts" : "check", 15)), el("div", {}, el("div", { class: "t" }, e.name || "rule"), el("div", { class: "d" }, `${e.msg} · ${ago(e.ts * 1000)}`)))), ...h.slos.slice(0, 3).map(s => el("a", { class: "insight", href: "#/slos" }, el("div", { class: "ic" }, icon("slo", 15)), el("div", { style: "flex:1;min-width:0" }, el("div", { class: "t" }, s.name), el("div", { class: "d" }, s.attainment_pct == null ? "no data" : `${s.attainment_pct}% attainment (objective ${s.objective_pct}%)`)))), !h.events.length && !h.slos.length ? empty("No alert events or SLOs yet", "Create one from a template.", el("a", { class: "btn sm", href: "#/alerts?tab=templates" }, "Browse templates")) : null), { cls: "fill", bodyCls: "pb" })));
    },
  });
  function rankTable(rows, key) {
    if (!rows.length) return empty("No traffic yet");
    const max = Math.max(1e-9, ...rows.map(r => r[key]));
    return el("div", { class: "tbl" }, el("table", {}, el("tbody", {}, ...rows.map(r => el("tr", { class: "click", on: { click: () => nav("/services/" + enc(r.service)) } }, el("td", {}, el("b", {}, r.service)), el("td", { style: "width:38%" }, el("span", { class: "bar-in", style: `width:${Math.max(3, 100 * r[key] / max)}%;background:${key === "err_pct" ? errColor(r.err_pct) : "var(--c1)"}` })), el("td", { class: "num" }, key === "err_pct" ? errPill(r.err_pct) : ms(r.p95)))))));
  }

  // ============================================================ SERVICES
  async function enrich(rows) {
    await Promise.all(rows.map(async r => {
      const [h, ts] = await Promise.all([api(`/services/${enc(r.service)}/health?minutes=${M()}`).catch(() => null), api(`/services/${enc(r.service)}/timeseries?minutes=${M()}&buckets=30`).catch(() => [])]);
      r.health = h; r.ts = ts;
    }));
    return rows;
  }
  P.route("/services", {
    title: "Services",
    async render(page, { q }) {
      const showAll = q.get("all") === "1", term = q.get("s") || "", hf = q.get("health") || "";
      const rows = await enrich(await P.services(showAll));
      const tbl = dataTable({
        rows: [], sortKey: "rate", sortDir: -1,
        cols: [
          { key: "service", label: "Service", sort: r => r.service.toLowerCase(), render: r => el("a", { href: "#/services/" + enc(r.service), style: "font-weight:600" }, r.service) },
          { key: "health", label: "Health", sort: r => r.health?.score ?? -1, render: r => healthPill(r.health) },
          { key: "n", label: "Requests", num: true, render: r => fmt(r.n) },
          { key: "rate", label: "Rate /s", num: true, render: r => rate(r.rate) },
          { key: "err_pct", label: "Errors", num: true, render: r => r.n ? errPill(r.err_pct) : "—" },
          { key: "p50", label: "p50", num: true, render: r => ms(r.p50) }, { key: "p95", label: "p95", num: true, render: r => ms(r.p95) }, { key: "p99", label: "p99", num: true, render: r => ms(r.p99) },
          { key: "ts", label: "Trend", sort: false, render: r => sparkline((r.ts || []).map(x => x.rate), { w: 110, h: 26, color: errColor(r.err_pct) }) },
        ],
        onRow: r => nav("/services/" + enc(r.service)),
        empty: (term || hf) ? empty("No services match", "Adjust the filters, or send traces with a service.name resource attribute.") : empty("No services in this time range", "Widen the time range, or point OpenTelemetry at Pulse0 if you have not sent data yet.", el("a", { class: "btn primary", href: "#/setup" }, "Setup & integrations")),
      });
      const apply = () => {
        const t = search.value.trim().toLowerCase(), hv = hsel.value;
        tbl.repaint(rows.filter(r => (!t || r.service.toLowerCase().includes(t)) && (!hv || (r.health?.state || "none") === hv)));
        setParams({ s: search.value.trim(), health: hv });
      };
      const search = el("input", { type: "search", placeholder: "Filter services…", value: term, style: "width:240px", on: { input: apply } });
      const hsel = el("select", { on: { change: apply } }, ...[["", "All health"], ["healthy", "Healthy"], ["degraded", "Degraded"], ["unhealthy", "Unhealthy"]].map(([v, l]) => el("option", { value: v, selected: v === hf }, l)));
      page.append(head("Services", `${rows.length} ${showAll ? "resources" : "services with traces"} · ranked by request rate`,
        search, hsel, el("label", { class: "switch" }, el("input", { type: "checkbox", checked: showAll, on: { change: e => { setParams({ all: e.target.checked }); P.render(); } } }), el("span"), el("em", {}, "Include log/metric-only"))),
        el("section", { class: "panel fill" }, tbl));
      apply();
    },
  });

  // ---------------------------------------------------------------- service detail
  const TABS = [["overview", "Overview"], ["operations", "Operations"], ["traces", "Traces"], ["logs", "Logs"], ["metrics", "Metrics"], ["attributes", "Resource attributes"]];
  P.route("/services/:name", {
    title: "Service", cleanup() { P.cleanupLogs?.(); P.cleanupLogs = null; },
    crumbs: ({ params }) => [["Services", "#/services"], [params.name]],
    async render(page, { params, q }) {
      const svc = params.name, tab = q.get("tab") || "overview", m = M();
      if (["overview", "metrics", "attributes"].includes(tab)) page.classList.add("scroll");
      const [all, h, attrs] = await Promise.all([P.services(true), api(`/services/${enc(svc)}/health?minutes=${m}`), api("/resources/" + enc(svc)).catch(() => ({}))]);
      const s = all.find(x => x.service === svc) || { n: 0, err: 0, err_pct: 0, p50: 0, p95: 0, p99: 0, rate: 0, avg_ms: 0 };
      page.append(
        head(el("span", {}, svc, " ", attrs["service.version"] ? el("span", { class: "chip", style: "vertical-align:middle" }, "v" + attrs["service.version"]) : null, " ", healthPill(h)), attrs["deployment.environment"] ? `Environment: ${attrs["deployment.environment"]}` : h.reason,
          el("a", { class: "btn", href: `#/traces?service=${enc(svc)}` }, icon("traces", 15), "Traces"), el("a", { class: "btn", href: `#/logs?service=${enc(svc)}` }, icon("logs", 15), "Logs")),
        el("div", { class: "tabs" }, ...TABS.map(([k, l]) => el("a", { href: `#/services/${enc(svc)}?tab=${k}`, class: tab === k ? "on" : "" }, l))));
      const body = el("div", { class: "col fill", style: "min-height:0" }); page.append(body);
      if (tab === "overview") {
        const [ts, ops, ins] = await Promise.all([api(`/services/${enc(svc)}/timeseries?minutes=${m}&buckets=60`), api(`/operations?service=${enc(svc)}&minutes=${m}`), api("/insights?minutes=" + m)]);
        const mine = ins.filter(i => i.service === svc);
        body.append(el("div", { class: "kpis" }, kpi("Requests", compact(s.n), `${rate(s.rate)} req/s`), kpi("Error rate", pct(s.err_pct), `${s.err} failed`, { cls: s.err_pct >= 5 ? "err" : s.err_pct >= 1 ? "warn" : "ok" }), kpi("p50", ms(s.p50)), kpi("p95", ms(s.p95), h.signals ? `${h.signals.p95_drift_pct >= 0 ? "+" : ""}${h.signals.p95_drift_pct}% vs prior` : ""), kpi("p99", ms(s.p99)), kpi("Error logs/min", h.signals ? h.signals.log_err_per_min : "—")),
          el("div", { class: "row", style: "flex:1;min-height:260px" },
            el("div", { class: "col", style: "flex:2" }, panel("Traffic & errors", chart({ t: ts.map(x => x.t), type: "area", h: 190, series: [{ name: "requests/s", values: ts.map(x => x.rate) }, { name: "errors/s", values: ts.map(x => x.err), color: "var(--err)" }] }), { cls: "" }), panel("Average latency", chart({ t: ts.map(x => x.t), type: "line", h: 150, f: ms, series: [{ name: "avg latency", values: ts.map(x => x.avg_ms), color: "var(--c4)" }] }))),
            el("div", { class: "col", style: "flex:1;min-width:300px" }, panel("Insights", mine.length ? el("div", {}, ...mine.map(insightRow)) : empty("No issues", h.reason), { bodyCls: "pb", style: "" }), panel("Top operations", opsTable(ops.slice(0, 6), svc, true), { bodyCls: "pb", style: "padding:0" }))));
      } else if (tab === "operations") {
        const ops = await api(`/operations?service=${enc(svc)}&minutes=${m}`);
        body.append(el("section", { class: "panel fill" }, opsTable(ops, svc)));
      } else if (tab === "traces") await tracesBlock(body, { service: svc, fixed: true, q });
      else if (tab === "logs") await logsBlock(body, { service: svc, fixed: true, q });
      else if (tab === "metrics") {
        const names = (await api("/metrics/names")).filter(n => (n.services || "").split(",").includes(svc));
        if (!names.length) body.append(el("section", { class: "panel fill" }, empty("No metrics for this service", "Send metrics with this service.name to see them here.")));
        else {
          const g = el("div", { class: "grid", style: "grid-template-columns:repeat(auto-fill,minmax(420px,1fr))" }); body.append(g);
          for (const n of names.slice(0, 12)) {
            const d = await api("/metrics/query?" + qs({ name: n.name, service: svc, minutes: m, buckets: 50, agg: n.mtype === "sum" ? "sum" : "avg" }));
            g.append(panel(el("span", { class: "mono" }, n.name), chart({ t: d.t, type: "area", h: 150, series: Object.entries(d.series).map(([name, values]) => ({ name, values })) }), { sub: n.unit || n.mtype, right: el("a", { class: "btn sm ghost", href: `#/metrics?name=${enc(n.name)}&service=${enc(svc)}` }, "Explore") }));
          }
        }
      } else {
        const keys = Object.keys(attrs).sort();
        body.append(el("section", { class: "panel fill" }, el("div", { class: "pb", style: "padding:0" }, keys.length ? el("div", { class: "kv" }, ...keys.flatMap(k => [el("div", { class: "mono" }, k), el("div", { class: "mono" }, typeof attrs[k] === "object" ? JSON.stringify(attrs[k]) : String(attrs[k]))])) : empty("No resource attributes"))));
      }
    },
  });
  function opsTable(ops, svc, mini = false) {
    return dataTable({
      keep: "ops", rows: ops, sortKey: "n", sortDir: -1,
      cols: [{ key: "name", label: "Operation", render: r => el("span", { class: "mono" }, r.name) }, { key: "n", label: "Count", num: true, render: r => fmt(r.n) },
        { key: "err", label: "Errors", num: true, render: r => r.err ? pill(`${fmt(r.err)} · ${pct(100 * r.err / r.n)}`, "err") : "—" },
        ...(mini ? [] : [{ key: "avg_ms", label: "Avg", num: true, render: r => ms(r.avg_ms) }]), { key: "max_ms", label: "Max", num: true, render: r => ms(r.max_ms) }],
      onRow: r => nav("/traces", { service: svc, q_: r.name }), empty: empty("No operations in range"),
    });
  }

  // ============================================================ SERVICE MAP

  // ============================================================ TRACES
  async function tracesBlock(host, { service = "", fixed = false, q }) {
    const svcs = fixed ? [] : await P.services();
    const state = { service, errors: q.get("errors") === "1", term: q.get("q_") || "", min: q.get("min") || "" };
    if (!fixed) state.service = q.get("service") || "";
    const box = el("div", { class: "fill", style: "display:flex;flex-direction:column;min-height:0" });
    const count = el("span", { class: "muted" });
    async function load() {
      const rows = await api("/traces?" + qs({ minutes: M(), service: state.service, errors: state.errors, min_ms: state.min, q_: state.term, limit: 200 }));
      count.textContent = `${rows.length} trace${rows.length === 1 ? "" : "s"}`;
      const maxd = Math.max(1, ...rows.map(r => r.dur_ms));
      const tbl = dataTable({
        keep: "traces", rows, sortKey: "start_ns", sortDir: -1, rowClass: r => (q.get("id") === r.trace_id ? "sel" : ""),
        cols: [{ key: "start_ns", label: "Time", render: r => el("span", { class: "muted" }, clock(r.start_ns / 1e6)) }, { key: "root", label: "Root span", render: r => el("span", { class: "mono" }, r.root || "—") },
          { key: "root_service", label: "Service", render: r => r.root_service ? pill(r.root_service, "") : "—" },
          { key: "dur_ms", label: "Duration", num: true, render: r => el("span", { style: "display:inline-flex;align-items:center;gap:8px;justify-content:flex-end" }, el("span", { class: "bar-in", style: `width:${Math.max(2, 70 * r.dur_ms / maxd)}px;background:${r.errors ? "var(--err)" : "var(--c1)"}` }), ms(r.dur_ms)) },
          { key: "spans", label: "Spans", num: true }, { key: "services", label: "Services", num: true },
          { key: "errors", label: "Status", num: true, render: r => r.errors ? pill(`${r.errors} error${r.errors > 1 ? "s" : ""}`, "err") : pill("OK", "ok") }, { key: "trace_id", label: "Trace ID", sort: false, render: r => el("span", { class: "chip" }, r.trace_id.slice(0, 12)) }],
        onRow: r => { setParams({ id: r.trace_id }); openTrace(r.trace_id); }, empty: (state.service || state.errors || Number(state.min) > 0 || state.term) ? empty("No traces match", "Widen the time range or relax the filters.") : empty("No traces in this time range", "Widen the time range, or point OpenTelemetry at Pulse0 if you have not sent data yet.", el("a", { class: "btn primary", href: "#/setup" }, "Setup & integrations")),
      });
      box.replaceChildren(el("section", { class: "panel fill" }, tbl));
    }
    const upd = () => { if (!fixed) setParams({ service: state.service, errors: state.errors, q_: state.term, min: state.min }); load(); };
    const tb = el("div", { class: "toolbar" },
      fixed ? null : el("select", { on: { change: e => { state.service = e.target.value; upd(); } } }, el("option", { value: "" }, "All services"), ...svcs.map(s => el("option", { value: s.service, selected: s.service === state.service }, s.service))),
      el("input", { type: "search", placeholder: "Search span name, attribute or trace id…", value: state.term, style: "width:min(380px,100%);flex:1;max-width:420px", on: { keydown: e => { if (e.key === "Enter") { state.term = e.target.value.trim(); upd(); } } } }),
      el("button", { class: "chipbtn" + (state.errors ? " on" : ""), on: { click: e => { state.errors = !state.errors; e.currentTarget.classList.toggle("on", state.errors); upd(); } } }, el("span", { class: "pill err", style: "height:8px;width:8px;padding:0" }), "Errors only"),
      el("input", { type: "number", placeholder: "min ms", value: state.min, style: "width:92px", on: { change: e => { state.min = e.target.value; upd(); } } }), el("span", { class: "sp" }), count);
    host.append(tb, box); await load();
  }
  P.route("/traces", {
    title: "Traces",
    async render(page, { q }) {
      page.append(head("Traces", "End-to-end distributed traces across services"));
      const host = el("div", { class: "col fill", style: "min-height:0" }); page.append(host);
      await tracesBlock(host, { q });
      const id = q.get("id"); if (id && !(P.drawerOpen === "drawer" && P.openTraceId === id)) openTrace(id);
    },
  });

  async function openTrace(id) {
    P.openTraceId = id;
    let t; try { t = await api("/traces/" + id); } catch (e) { toast(e.message, "err"); return; }
    const spans = t.spans; const ids = new Set(spans.map(s => s.span_id)), kids = {};
    spans.forEach(s => { const p = ids.has(s.parent_id) ? s.parent_id : ""; (kids[p] = kids[p] || []).push(s); });
    Object.values(kids).forEach(a => a.sort((x, y) => x.start_ns - y.start_ns));
    const order = []; (function walk(p, d) { for (const s of kids[p] || []) { order.push({ s, d }); walk(s.span_id, d + 1); } })("", 0);
    const t0 = Math.min(...spans.map(s => s.start_ns)), t1 = Math.max(...spans.map(s => s.end_ns)), total = Math.max(1, t1 - t0);
    const svcIdx = {}; spans.forEach(s => { if (!(s.service in svcIdx)) svcIdx[s.service] = Object.keys(svcIdx).length; });
    const errs = spans.filter(s => s.status === 2), root = order[0]?.s;
    const side = el("div", {}); const rows = {};
    const kindName = k => ({ 1: "internal", 2: "server", 3: "client", 4: "producer", 5: "consumer" }[k] || "unspecified");
    function select(s) {
      Object.entries(rows).forEach(([k, r]) => r.classList.toggle("sel", k === s.span_id));
      const attrs = Object.entries(s.attrs || {}), logs = t.logs.filter(l => l.span_id === s.span_id);
      side.replaceChildren(
        el("div", { class: "sect" }, "Span"), el("div", { class: "kv" }, el("div", {}, "Name"), el("div", { class: "mono" }, s.name), el("div", {}, "Service"), el("div", {}, s.service), el("div", {}, "Kind"), el("div", {}, kindName(s.kind)), el("div", {}, "Status"), el("div", {}, s.status === 2 ? pill("error", "err") : pill(s.status === 1 ? "ok" : "unset", s.status === 1 ? "ok" : "")), el("div", {}, "Duration"), el("div", {}, ms(s.dur_ms)), el("div", {}, "Offset"), el("div", {}, ms((s.start_ns - t0) / 1e6)), ...(s.status_msg ? [el("div", {}, "Message"), el("div", {}, s.status_msg)] : []), el("div", {}, "Span ID"), el("div", { class: "mono" }, s.span_id)),
        el("div", { class: "sect" }, `Attributes (${attrs.length})`), attrs.length ? el("div", { class: "kv" }, ...attrs.flatMap(([k, v]) => [el("div", { class: "mono" }, k), el("div", { class: "mono" }, typeof v === "object" ? JSON.stringify(v) : String(v))])) : el("div", { class: "muted", style: "padding:4px 14px 10px" }, "none"),
        el("div", { class: "sect" }, logs.length ? `Logs for this span (${logs.length})` : `Trace logs (${t.logs.length})`),
        (logs.length ? logs : t.logs).length ? el("div", {}, ...(logs.length ? logs : t.logs).map(l => el("div", { class: "log", style: "grid-template-columns:70px 54px minmax(0,1fr)", on: { click: () => { const sp = spans.find(x => x.span_id === l.span_id); if (sp) select(sp); } } }, el("span", { class: "t" }, clock(l.ts_ns / 1e6)), el("span", { class: "sevt " + sevName(l.severity) }, sevName(l.severity)), el("span", { class: "b" }, l.body)))) : el("div", { class: "muted", style: "padding:4px 14px 10px" }, "No correlated logs"));
    }
    const wf = el("div", { class: "wf" }, el("div", { class: "wf-head" }, el("div", { style: "padding:8px 10px" }, "Span"), el("div", { class: "ruler" }, ...[0, .25, .5, .75, 1].map(f => el("span", { style: `left:${f * 100}%` }, ms(total * f / 1e6))))));
    for (const { s, d } of order) {
      const left = (s.start_ns - t0) / total * 100, w = Math.max(.4, (s.end_ns - s.start_ns) / total * 100), lblLeft = left + w > 82;
      const r = el("div", { class: "wf-row", on: { click: () => select(s) } }, el("div", { class: "wf-n", style: `padding-left:${10 + d * 16}px` }, el("i", { style: `width:7px;height:7px;border-radius:50%;flex:none;background:${COLORS[svcIdx[s.service] % COLORS.length]}` }), el("span", { class: "nm", title: s.name }, s.name), el("span", { class: "sv" }, s.service)),
        el("div", { class: "wf-t" }, el("div", { class: "wf-bar" + (s.status === 2 ? " err" : ""), style: `left:${left}%;width:${w}%;${s.status === 2 ? "" : `background:${COLORS[svcIdx[s.service] % COLORS.length]}`}` }), el("div", { class: "wf-lbl", style: lblLeft ? `right:${100 - left}%;padding-right:6px` : `left:${left + w}%` }, ms(s.dur_ms))));
      rows[s.span_id] = r; wf.append(r);
    }
    const dh = el("div", { class: "dh" }, el("div", {}, el("h2", {}, root ? root.name : "Trace", " ", errs.length ? pill(`${errs.length} error${errs.length > 1 ? "s" : ""}`, "err") : pill("OK", "ok")), el("div", { class: "muted", style: "font-size:12px;margin-top:2px;display:flex;gap:12px;flex-wrap:wrap" }, el("span", { class: "chip" }, id), ...[`${spans.length} spans`, `${Object.keys(svcIdx).length} services`, ms(total / 1e6), stamp(t0 / 1e6)].map(x => el("span", {}, x)))),
      el("button", { class: "btn sm accent", on: { click: () => P.explain(`trace ${id}`, `Trace ${id}${root ? ` (root span ${root.name} in ${root.service})` : ""}: ${spans.length} spans, ${errs.length} with errors. Why is it slow or failing? Use get_trace to inspect it.`) } }, icon("sparkle", 13), P.aiStatus?.enabled ? "Explain with Claude" : "Explain"), el("button", { class: "btn sm", on: { click: () => copyText(id) } }, icon("copy", 13), "Copy ID"), el("button", { class: "btn icon ghost x", on: { click: P.closeDrawer } }, icon("x")));
    P.openDrawer(el("div", { class: "db" }, dh, el("div", { class: "split" }, el("div", { class: "l", "data-keep": "wf" }, wf), el("div", { class: "r" }, side))));
    select(errs[0] || root || spans[0]);
  }

  // ============================================================ LOGS
  async function logsBlock(host, { service = "", fixed = false, q }) {
    const svcs = fixed ? [] : await P.services(true);
    const st = { service: fixed ? service : q.get("service") || "", sev: +(q.get("sev") || 0), q: q.get("q") || "", live: false };
    const rowsBox = el("div", { class: "pb", style: "padding:0", "data-keep": "logs" }), facets = el("div", { class: "facet" }), count = el("small", {}), histHost = el("div");
    const seen = new Set(); let timer = null;
    const sevOpts = [[0, "All levels"], [9, "INFO and above"], [13, "WARN and above"], [17, "ERROR and above"]];
    function logRow(r, fresh) {
      const sn = r.sev_text || sevName(r.severity);
      const row = el("div", { class: "log" + (fresh ? " new" : ""), on: { click: () => toggle(row, r) } }, el("span", { class: "t" }, clock(r.ts_ns / 1e6)), el("span", { class: "sevt " + sn }, sn), el("span", { class: "sv", title: r.service }, r.service), el("span", { class: "b" }, r.body));
      return row;
    }
    function toggle(row, r) {
      if (row.nextSibling?.classList?.contains("log-detail")) { row.nextSibling.remove(); return; }
      const attrs = Object.entries(r.attrs || {});
      row.after(el("div", { class: "log-detail" }, attrs.length ? el("div", { class: "kv", style: "border:1px solid var(--line);border-radius:8px;overflow:hidden" }, ...attrs.flatMap(([k, v]) => [el("div", { class: "mono" }, k), el("div", { class: "mono" }, String(v))])) : el("div", { class: "muted" }, "No attributes"), el("div", { style: "margin-top:8px;display:flex;gap:8px" }, r.trace_id ? el("button", { class: "btn sm primary", on: { click: () => { setParams({ id: r.trace_id }); location.hash = "#/traces?id=" + r.trace_id; } } }, "Open trace") : null, el("button", { class: "btn sm", on: { click: () => copyText(r.body) } }, "Copy message"))));
    }
    const params = () => ({ minutes: M(), service: st.service, min_severity: st.sev, q_: st.q, limit: 300 });
    async function loadRows(live) {
      const rows = await api("/logs?" + qs(params()));
      count.textContent = `${rows.length}${rows.length >= 300 ? "+" : ""} entries`;
      if (live && rows.length) { const keep = rowsBox.scrollTop; const fresh = rows.filter(r => !seen.has(r.ts_ns + r.body + r.service)); rows.forEach(r => seen.add(r.ts_ns + r.body + r.service)); rowsBox.replaceChildren(...rows.map(r => logRow(r, fresh.includes(r)))); rowsBox.scrollTop = keep; return; }
      rows.forEach(r => seen.add(r.ts_ns + r.body + r.service));
      rowsBox.replaceChildren(...(rows.length ? rows.map(r => logRow(r)) : [(st.service || Number(st.sev) > 0 || st.q) ? empty("No logs match", "Try another filter or a wider time range.") : empty("No logs in this time range", "Widen the time range, or point OpenTelemetry at Pulse0 if you have not sent data yet.", el("a", { class: "btn primary", href: "#/setup" }, "Setup & integrations"))]));
    }
    async function loadAside() {
      const [h, f] = await Promise.all([api("/logs/histogram?" + qs({ minutes: M(), service: st.service, buckets: 60 })), api("/logs/facets?" + qs({ minutes: M(), q_: st.q }))]);
      histHost.replaceChildren(chart({ t: h.map(x => x.t), type: "stack", h: 92, series: [{ name: "Other", values: h.map(x => x.n - x.warn - x.err), color: "var(--c5)" }, { name: "Warn", values: h.map(x => x.warn), color: "var(--warn)" }, { name: "Error", values: h.map(x => x.err), color: "var(--err)" }] }));
      const fi = (label, n, on, cb) => el("div", { class: "fi" + (on ? " on" : ""), on: { click: cb } }, el("span", {}, label), el("span", { class: "n" }, compact(n)));
      facets.replaceChildren(el("div", { class: "sect", style: "padding-left:8px" }, "Severity"), ...f.severity.map(x => fi(x.value, x.n, st.sev && sevName(st.sev) === x.value, () => { st.sev = st.sev && sevName(st.sev) === x.value ? 0 : ({ FATAL: 21, ERROR: 17, WARN: 13, INFO: 9, DEBUG: 5, TRACE: 1 })[x.value]; update(); })),
        ...(fixed ? [] : [el("div", { class: "sect", style: "padding-left:8px" }, "Service"), ...f.service.map(x => fi(x.value, x.n, st.service === x.value, () => { st.service = st.service === x.value ? "" : x.value; update(); }))]));
    }
    const update = () => { if (!fixed) setParams({ service: st.service, sev: st.sev, q: st.q }); else setParams({ sev: st.sev, q: st.q }); sevSel.value = st.sev; renderChips(); loadRows(); loadAside(); };
    const chips = el("div", { style: "display:flex;gap:6px;flex-wrap:wrap" });
    function renderChips() { chips.replaceChildren(...st.q.split(/\s+/).filter(t => t.includes("=")).map(t => el("span", { class: "chipbtn on" }, t, el("span", { style: "cursor:pointer", on: { click: () => { st.q = st.q.split(/\s+/).filter(x => x !== t).join(" "); qin.value = st.q; update(); } } }, "×")))); }
    const qin = el("input", { type: "search", placeholder: "Search text, or filter with key=value (e.g. http.status_code=500)…", value: st.q, style: "flex:1;min-width:220px;max-width:560px", on: { keydown: e => { if (e.key === "Enter") { st.q = e.target.value.trim(); update(); } } } });
    const sevSel = el("select", { on: { change: e => { st.sev = +e.target.value; update(); } } }, ...sevOpts.map(([v, l]) => el("option", { value: v, selected: v === st.sev }, l)));
    const liveBtn = el("button", { class: "btn", on: { click: () => { st.live = !st.live; liveBtn.classList.toggle("primary", st.live); liveBtn.replaceChildren(icon(st.live ? "pause" : "play", 14), st.live ? "Live tail on" : "Live tail"); clearInterval(timer); if (st.live) timer = setInterval(() => loadRows(true), 2000); } } }, icon("play", 14), "Live tail");
    P.cleanupLogs = () => clearInterval(timer);
    const selSvc = fixed ? null : el("select", { on: { change: e => { st.service = e.target.value; update(); } } }, el("option", { value: "" }, "All services"), ...svcs.map(s => el("option", { value: s.service, selected: s.service === st.service }, s.service)));
    host.append(el("div", { class: "toolbar" }, selSvc, sevSel, qin, el("span", { class: "sp" }), liveBtn), chips,
      el("div", { class: "logs-layout fill" }, el("section", { class: "panel facetp" }, el("div", { class: "pb", style: "padding:4px 4px 8px" }, facets)),
        el("div", { class: "col" }, el("section", { class: "panel", style: "flex:none" }, el("div", { class: "pb", style: "padding:8px 12px" }, histHost)), el("section", { class: "panel fill" }, el("div", { class: "ph" }, "Log entries", count), rowsBox))));
    renderChips(); await Promise.all([loadRows(), loadAside()]);
  }
  P.route("/logs", {
    title: "Logs", live: false, cleanup() { P.cleanupLogs?.(); P.cleanupLogs = null; },
    async render(page, { q }) {
      page.append(head("Logs", "Full-text and attribute search with trace correlation"));
      const host = el("div", { class: "col fill", style: "min-height:0" }); page.append(host);
      await logsBlock(host, { q });
    },
  });
  P.tracesBlock = tracesBlock; P.logsBlock = logsBlock; P.insightRow = insightRow; P.openTrace = openTrace;
})();
