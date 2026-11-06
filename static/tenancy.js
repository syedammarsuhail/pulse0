/* Pulse0 deployment mode UI: get-started chooser (self-hosted vs Pulse-hosted), organization page
   (hosted mode), Settings panel. Loaded after core.js/views*.js; wraps P.start to learn the mode first. */
(function () {
  const P = window.Pulse0;
  const { el, icon, panel, head, kpi, empty, codeBlock, field, modal, confirmBox, toast, pill, store, compact, fmt } = P;
  P.mode = "selfhosted"; P.features = {};

  // ---------------------------------------------------------------- styles (scoped, theme-aware)
  document.head.append(el("style", {}, `
.tn-choices{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.tn-card{position:relative;text-align:left;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 18px 16px;cursor:pointer;color:var(--text);display:flex;flex-direction:column;gap:10px;font:inherit}
.tn-card:hover{border-color:var(--line-2)}
.tn-card.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
.tn-card h3{margin:0;font-size:17px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tn-card .tag{color:var(--accent-2);font-weight:600;font-size:13px}
.tn-card ul{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:7px;color:var(--text-2);font-size:13px}
.tn-card li{display:flex;gap:8px}.tn-card li:before{content:"✓";color:var(--ok);font-weight:700}
.tn-card .here{position:absolute;top:14px;right:14px}
.tn-steps{display:flex;flex-direction:column;gap:18px}
.tn-step{display:grid;grid-template-columns:30px 1fr;gap:12px}
.tn-step>i{width:28px;height:28px;border-radius:50%;background:var(--accent-bg);color:var(--accent-2);display:flex;align-items:center;justify-content:center;font-style:normal;font-weight:700;font-size:13px}
.tn-step h4{margin:3px 0 6px;font-size:14px}.tn-step .muted,.tn-note{color:var(--text-3);font-size:12.5px}
.tn-step .col{gap:8px}
.tn-note{border:1px solid var(--line);border-left:3px solid var(--info);border-radius:8px;padding:10px 12px;background:var(--surface)}
.tn-note.warn{border-left-color:var(--warn)}
.tn-tabs{display:flex;gap:4px;flex-wrap:wrap}
.tn-meter{height:9px;border-radius:6px;background:var(--surface-3);overflow:hidden}
.tn-meter>i{display:block;height:100%;border-radius:6px;background:var(--ok);transition:width .3s}
.tn-meter.warn>i{background:var(--warn)}.tn-meter.err>i{background:var(--err)}
.tn-meter-row{display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:6px;color:var(--text-2)}
.tn-key{font-family:var(--mono);font-size:12px}
.tn-actions{display:flex;gap:6px;justify-content:flex-end}
`));

  // ---------------------------------------------------------------- helpers
  const MODES = { selfhosted: "Self-hosted", hosted: "Pulse-hosted" };
  const hosted = () => P.mode === "hosted";

  async function oapi(path, o = {}) {
    const h = {}, tok = store.get("token", "");
    if (tok) h.Authorization = "Bearer " + tok;
    if (o.body) h["Content-Type"] = "application/json";
    const r = await fetch("/org" + path, { method: o.method || "GET", headers: h, body: o.body ? JSON.stringify(o.body) : undefined });
    if (r.status === 401) throw new P.AuthError("An organization admin key is required.");
    if (!r.ok) {
      let m = r.statusText, code = null;
      try { const j = await r.json(); m = j.message || (j.details && j.details.map(d => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join("; ")) || j.error || m; code = j.error; } catch { /* keep */ }
      const e = new Error(m); e.status = r.status; e.code = code; throw e;
    }
    return r.json();
  }
  const bytes = n => n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB";
  const sumUse = u => (u.spans || 0) + (u.logs || 0) + (u.metrics || 0);
  const untilReset = () => { const d = new Date(), s = 86400 - (d.getUTCHours() * 3600 + d.getUTCMinutes() * 60 + d.getUTCSeconds()); return `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m`; };
  const meter = (used, cap) => {
    const pct = cap ? Math.min(100, 100 * used / cap) : 0;
    return el("div", {}, el("div", { class: "tn-meter-row" }, el("span", {}, `${fmt(used)} used today`), el("span", {}, cap ? `cap ${fmt(cap)} · ${pct.toFixed(0)}%` : "no cap")),
      el("div", { class: "tn-meter " + (pct >= 100 ? "err" : pct >= 80 ? "warn" : "") }, el("i", { style: `width:${cap ? pct : 0}%` })));
  };

  // ---------------------------------------------------------------- start: learn the mode before the rail is built
  const origStart = P.start;
  P.start = async function () {
    try { const m = await (await fetch("/api/mode")).json(); P.mode = m.mode; P.features = m.features || {}; } catch { /* old server: selfhosted */ }
    const cfg = P.NAV.find(g => g[0] === "Configure")[1];
    cfg.unshift(["play", "Get started", "#/onboarding"]);
    if (hosted()) cfg.unshift(["services", "Organization", "#/organization"]);
    origStart();
    firstRun();
  };

  async function firstRun() {
    if (store.get("onboarded", "")) return;
    const h = location.hash;
    if (h && h !== "#/" && h !== "#/home") return;
    if (hosted()) { location.hash = "/onboarding"; return; }
    try { const s = await P.api("/stats"); if (s.spans === 0) location.hash = "/onboarding"; } catch { /* locked or offline: stay put */ }
  }

  // hosted + org admin key + several projects: pick the first one instead of failing, and say so
  let pickedAt = 0;
  P.onProjectRequired = j => {
    if (Date.now() - pickedAt < 5000 || !j.projects?.length) return;
    pickedAt = Date.now();
    store.set("project", j.projects[0].id);
    toast(`Showing project "${j.projects[0].name}". Switch projects under Organization.`);
    setTimeout(() => P.render(), 0);
  };

  // ---------------------------------------------------------------- get started
  const ORIGIN = location.origin;
  function envBlock(key, mode) {
    const hdr = mode === "hosted" ? `x-pulse0-key=${key}` : `authorization=Bearer%20${key}`;
    return `export OTEL_SERVICE_NAME=my-service
export OTEL_EXPORTER_OTLP_ENDPOINT=${ORIGIN}
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf${mode === "hosted" || key ? `\nexport OTEL_EXPORTER_OTLP_HEADERS="${hdr}"` : ""}`;
  }
  function snippets(key, mode) {
    const env = envBlock(key, mode);
    const hdrKey = mode === "hosted" ? "x-pulse0-key" : "Authorization";
    const hdrVal = mode === "hosted" ? key : `Bearer ${key}`;
    return {
      Python: `pip install opentelemetry-distro opentelemetry-exporter-otlp-proto-http
opentelemetry-bootstrap -a install

${env}
opentelemetry-instrument python app.py          # zero code changes`,
      "Node.js": `npm i @opentelemetry/auto-instrumentations-node

${env}
export OTEL_NODE_RESOURCE_DETECTORS=env,host,os
node --require @opentelemetry/auto-instrumentations-node/register app.js   # zero code changes`,
      Java: `# download opentelemetry-javaagent.jar from the OpenTelemetry Java instrumentation releases
${env}
java -javaagent:opentelemetry-javaagent.jar -jar app.jar     # zero code changes`,
      ".NET": `# install the OpenTelemetry .NET automatic instrumentation, or add the OTLP exporter in code
${env}
export OTEL_DOTNET_AUTO_TRACES_EXPORTER=otlp OTEL_DOTNET_AUTO_METRICS_EXPORTER=otlp OTEL_DOTNET_AUTO_LOGS_EXPORTER=otlp
# then start your app through the auto-instrumentation script`,
      Go: `// Go has no agent: add the OTLP/HTTP exporter (it reads the standard OTEL_* variables)
// go get go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp
${env}`,
      "Browser / Angular": `// npm i @opentelemetry/sdk-trace-web @opentelemetry/exporter-trace-otlp-http
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
const exporter = new OTLPTraceExporter({
  url: "${ORIGIN}/v1/traces",
  headers: { "${hdrKey}": "${hdrVal}" },
});
// Browser keys are public: use a dedicated low-quota project for RUM, never a production ingest key.`,
      "OTel Collector": `exporters:
  otlphttp/pulse0:
    endpoint: ${ORIGIN}
    headers:
      ${hdrKey}: "${hdrVal}"
service:
  pipelines:
    traces:  { receivers: [otlp], exporters: [otlphttp/pulse0] }
    logs:    { receivers: [otlp], exporters: [otlphttp/pulse0] }
    metrics: { receivers: [otlp], exporters: [otlphttp/pulse0] }`,
    };
  }

  function choiceCard(id, current, onPick) {
    const sel = P.S.__tn_choice === id;
    const bullets = id === "selfhosted"
      ? ["Runs in your Kubernetes cluster or on your Docker host", "Telemetry is stored on your own volume: nothing leaves your environment", "Flat, predictable cost: you pay only for your infrastructure", "Optional AI assistant is off by default"]
      : ["Nothing to run: send OTLP to your project endpoint", "One isolated project per environment, API keys per project", "Daily ingest quota and retention per organization", "Your data is stored on Pulse infrastructure"];
    return el("button", { class: "tn-card" + (sel ? " on" : ""), type: "button", on: { click: () => onPick(id) } },
      el("h3", {}, id === "selfhosted" ? "Self-hosted" : "Pulse-hosted", el("span", { class: "tag" }, id === "selfhosted" ? "Your cluster, your data" : "We run it, you send data")),
      el("ul", {}, ...bullets.map(b => el("li", {}, b))),
      P.mode === id ? el("span", { class: "here" }, pill("This server", "ok")) : null);
  }

  P.route("/onboarding", {
    title: "Get started", live: false, range: false, scroll: true,
    async render(page, ctx) {
      store.set("onboarded", "1");
      P.S.__tn_choice = P.S.__tn_choice || P.mode;
      let ingestKey = "";
      const body = el("div", { class: "col" });
      const paint = () => {
        const choice = P.S.__tn_choice;
        const cards = el("div", { class: "tn-choices" }, choiceCard("selfhosted", P.mode, pick), choiceCard("hosted", P.mode, pick));
        body.replaceChildren(cards, choice === "selfhosted" ? selfSteps() : hostedSteps());
      };
      const pick = id => { P.S.__tn_choice = id; paint(); };

      function selfSteps() {
        const tok = store.get("token", "") ? "<YOUR_PULSE0_TOKEN>" : "";
        const tabs = snippets(tok, "selfhosted"); let lang = "Python";
        const sn = el("div", {});
        const tabBar = el("div", { class: "tn-tabs" });
        const paintSn = () => { tabBar.replaceChildren(...Object.keys(tabs).map(k => el("button", { class: "btn sm" + (k === lang ? " accent" : ""), on: { click: () => { lang = k; paintSn(); } } }, k))); sn.replaceChildren(codeBlock(tabs[lang])); };
        paintSn();
        return panel("Self-hosted: install and connect", el("div", { class: "tn-steps" },
          el("div", { class: "tn-step" }, el("i", {}, "1"), el("div", { class: "col" }, el("h4", {}, "Install Pulse0 in your cluster"),
            codeBlock("helm upgrade --install pulse0 ./helm/pulse0 -n observability --create-namespace \\\n  --set persistence.size=50Gi --set auth.token=<CHOOSE_A_TOKEN>"),
            el("div", { class: "muted" }, "No Kubernetes? ", el("span", { class: "mono" }, "docker run -p 4318:4318 -v pulse0-data:/data pulse0"), ". EKS, AKS and bare-metal overlays are in the chart (values-eks.yaml, values-aks.yaml, values-baremetal.yaml)."))),
          el("div", { class: "tn-step" }, el("i", {}, "2"), el("div", { class: "col" }, el("h4", {}, "Point your apps at it"), tabBar, sn,
            el("div", { class: "muted" }, "In-cluster apps can use ", el("span", { class: "mono" }, "http://pulse0.observability.svc.cluster.local:4318"), " (or the bundled collector on :4319)."))),
          el("div", { class: "tn-step" }, el("i", {}, "3"), el("div", { class: "col" }, el("h4", {}, "Open Pulse0 and watch it fill"), el("div", {}, el("a", { class: "btn primary", href: "#/home" }, "Go to Home"), " ", el("a", { class: "btn", href: "#/setup" }, "More setup options")))),
          dataFlow("selfhosted")), { sub: P.mode === "selfhosted" ? "You are on this server" : "Run this once on your own infrastructure" });
      }

      function hostedSteps() {
        const tabs = snippets(ingestKey || "<YOUR_INGEST_KEY>", "hosted"); let lang = "Python";
        const sn = el("div", {}); const tabBar = el("div", { class: "tn-tabs" });
        const paintSn = () => { tabBar.replaceChildren(...Object.keys(tabs).map(k => el("button", { class: "btn sm" + (k === lang ? " accent" : ""), on: { click: () => { lang = k; paintSn(); } } }, k))); sn.replaceChildren(codeBlock(tabs[lang])); };
        paintSn();
        const keyBtn = el("button", { class: "btn primary", on: { click: async () => {
          if (!hosted()) { toast("This server is not in Pulse-hosted mode.", "err"); return; }
          try { const k = await createKeyFlow({ type: "ingest", onKey: key => { ingestKey = key; paint(); } }); void k; } catch (e) { if (e instanceof P.AuthError) { toast("Sign in with an organization admin key first (Settings → Access).", "err"); location.hash = "/settings"; } else toast(e.message, "err"); }
        } } }, icon("plus", 14), "Create an ingest key");
        return panel("Pulse-hosted: get a project and send data", el("div", { class: "tn-steps" },
          el("div", { class: "tn-step" }, el("i", {}, "1"), el("div", { class: "col" }, el("h4", {}, "Get your organization"),
            el("div", { class: "muted" }, "Your Pulse operator creates the organization and gives you an organization admin key (starts with ", el("span", { class: "mono" }, "p0a_"), "). Paste it under Settings → Access to sign in."),
            el("div", {}, el("a", { class: "btn", href: "#/settings" }, "Settings → Access"), " ", hosted() ? el("a", { class: "btn", href: "#/organization" }, "Open Organization") : null))),
          el("div", { class: "tn-step" }, el("i", {}, "2"), el("div", { class: "col" }, el("h4", {}, "Create a project and an ingest key"),
            el("div", { class: "muted" }, "Each project is an isolated data silo. Ingest keys only send data; read keys only read it. A key is shown once."), el("div", {}, keyBtn),
            ingestKey ? el("div", { class: "tn-note warn" }, "Key created and inserted into the snippets below. It will not be shown again.") : null)),
          el("div", { class: "tn-step" }, el("i", {}, "3"), el("div", { class: "col" }, el("h4", {}, "Send telemetry"), el("div", { class: "muted" }, "Endpoint: ", el("span", { class: "mono" }, ORIGIN), " · header ", el("span", { class: "mono" }, "x-pulse0-key")), tabBar, sn)),
          dataFlow("hosted")), { sub: P.mode === "hosted" ? "You are on this server" : "Available when your operator runs Pulse in hosted mode" });
      }

      function dataFlow(m) {
        return el("div", { class: "tn-note" }, el("b", {}, "Where your data goes. "), m === "selfhosted"
          ? "Telemetry stays on your own cluster and volume. If you turn on the optional Claude assistant, snippets of query results are sent to Anthropic; alert webhooks and synthetic checks call out to the URLs you configure."
          : "Telemetry is stored on Pulse infrastructure, in your project's own database. The Claude assistant is off in hosted mode unless the operator enables it; alert webhooks and synthetic checks call out to public URLs only.");
      }

      paint();
      page.append(head("Get started with Pulse0", "Choose where your observability data lives. You can switch any time: both modes speak the same OpenTelemetry."), body);
    },
  });

  // ---------------------------------------------------------------- create-key flow (shared)
  async function createKeyFlow({ type = "ingest", projectId = "", onKey } = {}) {
    const me = await oapi("/me");
    const projects = me.projects;
    if (!projects.length) { toast("Create a project first.", "err"); return null; }
    const t = el("select", {}, ...[["ingest", "Ingest (send telemetry)"], ["read", "Read (query only)"], ["admin", "Admin (organization-wide)"]].map(([v, l]) => el("option", { value: v, selected: v === type }, l)));
    const name = el("input", { placeholder: "e.g. payments-prod", value: type === "ingest" ? "ingest key" : "" });
    const proj = el("select", {}, ...projects.map(p => el("option", { value: p.id, selected: p.id === projectId }, p.name)));
    const projField = field("Project", proj, "Ingest and read keys are bound to one project.");
    const sync = () => { projField.style.display = t.value === "admin" ? "none" : ""; };
    t.addEventListener("change", sync); sync();
    return new Promise(resolve => {
      modal({ title: "Create API key", width: 460, onClose: () => resolve(null),
        body: el("div", { class: "col" }, field("Type", t), field("Name", name), projField),
        actions: [{ label: "Cancel" }, { label: "Create key", primary: true, keep: true, onClick: async close => {
          const r = await oapi("/keys", { method: "POST", body: { type: t.value, name: name.value.trim() || t.value + " key", project_id: t.value === "admin" ? undefined : proj.value } });
          close(); showKey(r); onKey?.(r.key); P.render({ refresh: true }); resolve(r);
        } }] });
    });
  }
  function showKey(r) {
    modal({ title: "Copy your key now", width: 520, body: el("div", { class: "col" },
      el("div", { class: "tn-note warn" }, "This key is stored hashed and cannot be shown again. Store it in your secret manager."), codeBlock(r.key),
      el("div", { class: "muted" }, `${r.type} key · ${r.prefix}…`)), actions: [{ label: "I've copied it", primary: true }] });
  }

  // ---------------------------------------------------------------- organization (hosted only)
  P.route("/organization", {
    title: "Organization", live: false, range: false, scroll: true,
    async render(page) {
      if (!hosted()) { page.append(head("Organization"), el("section", { class: "panel" }, empty("Only in Pulse-hosted mode", "This server is self-hosted: there is a single workspace and no organizations.", el("a", { class: "btn", href: "#/onboarding" }, "Compare modes")))); return; }
      let me;
      try { me = await oapi("/me"); }
      catch (e) {
        if (e instanceof P.AuthError) throw e;
        page.append(head("Organization"), el("section", { class: "panel" }, empty("Organization admin key required", e.status === 403 ? "You are signed in with a read key. Sign in with an organization admin key (p0a_…) to manage projects and keys." : e.message, el("a", { class: "btn", href: "#/settings" }, "Settings → Access"))));
        return;
      }
      const o = me.org, active = store.get("project", "");
      const nameOf = id => (me.projects.find(p => p.id === id) || {}).name || "org-wide";
      const actions = [el("button", { class: "btn", on: { click: newProject } }, icon("plus", 14), "New project"), el("button", { class: "btn primary", on: { click: () => createKeyFlow() } }, icon("plus", 14), "Create key")];
      const used = o.used_today, cap = o.daily_cap;
      const week = me.projects.reduce((s, p) => s + sumUse(p.last7d), 0);
      const rejected = me.projects.reduce((s, p) => s + p.today.rejected, 0);
      const kpis = el("div", { class: "kpis" },
        kpi("Ingest today", compact(used), cap ? `of ${compact(cap)} · resets in ${untilReset()}` : "no daily cap", { cls: cap && used >= cap ? "err" : cap && used >= .8 * cap ? "warn" : "" }),
        kpi("Last 7 days", compact(week), "spans + logs + metric points"),
        kpi("Rejected today", compact(rejected), rejected ? "over the daily cap (HTTP 429)" : "none", { cls: rejected ? "err" : "ok" }),
        kpi("Retention", `${o.retention_hours}h`, `${(o.retention_hours / 24).toFixed(1)} days · set by operator`),
        kpi("Projects", me.projects.length, `${me.keys.filter(k => !k.revoked_at).length} active keys`));

      const projTable = P.dataTable({
        keep: "projects", rows: me.projects,
        cols: [
          { key: "name", label: "Project", render: p => el("span", {}, el("b", {}, p.name), " ", p.id === active ? pill("viewing", "acc") : null) },
          { key: "id", label: "ID", sort: false, render: p => el("span", { class: "mono muted", style: "font-size:11.5px" }, p.id) },
          { key: "today", label: "Today", num: true, sort: p => sumUse(p.today), render: p => el("span", { title: `${fmt(p.today.spans)} spans · ${fmt(p.today.logs)} logs · ${fmt(p.today.metrics)} metrics` }, compact(sumUse(p.today))) },
          { key: "last7d", label: "7 days", num: true, sort: p => sumUse(p.last7d), render: p => compact(sumUse(p.last7d)) },
          { key: "db_bytes", label: "Size", num: true, render: p => bytes(p.db_bytes) },
          { key: "act", label: "", sort: false, render: p => el("div", { class: "tn-actions" },
            el("button", { class: "btn sm", on: { click: e => { e.stopPropagation(); store.set("project", p.id); toast(`Viewing project "${p.name}"`); location.hash = "/home"; } } }, "Open"),
            el("button", { class: "btn sm", on: { click: e => { e.stopPropagation(); createKeyFlow({ type: "ingest", projectId: p.id }); } } }, "New key"),
            el("button", { class: "btn sm danger", on: { click: async e => { e.stopPropagation(); if (await confirmBox(`Delete project "${p.name}" and all its telemetry? This cannot be undone.`)) { try { await oapi("/projects/" + p.id, { method: "DELETE" }); if (store.get("project", "") === p.id) store.set("project", ""); toast("Project deleted", "ok"); P.render({ refresh: true }); } catch (err) { toast(err.message, "err"); } } } } }, "Delete")) },
        ],
        empty: empty("No projects yet", "Create a project, then an ingest key to start sending telemetry.", el("button", { class: "btn primary", on: { click: newProject } }, "New project")),
      });

      const keyTable = P.dataTable({
        keep: "keys", rows: me.keys,
        cols: [
          { key: "name", label: "Name", render: k => el("b", {}, k.name) },
          { key: "type", label: "Type", render: k => pill(k.type, k.type === "admin" ? "warn" : k.type === "ingest" ? "info" : "") },
          { key: "prefix", label: "Key", sort: false, render: k => el("span", { class: "tn-key" }, k.prefix + "…") },
          { key: "project_id", label: "Project", sort: k => nameOf(k.project_id), render: k => nameOf(k.project_id) },
          { key: "created", label: "Created", sort: k => k.created, render: k => P.ago(k.created * 1000) },
          { key: "last_used", label: "Last used", sort: k => k.last_used || 0, render: k => k.last_used ? P.ago(k.last_used * 1000) : el("span", { class: "muted" }, "never") },
          { key: "state", label: "Status", sort: k => k.revoked_at ? 1 : 0, render: k => k.revoked_at ? pill("revoked", "err") : k.id === me.key.id ? pill("this key", "acc") : pill("active", "ok") },
          { key: "act", label: "", sort: false, render: k => k.revoked_at || k.id === me.key.id ? "" : el("div", { class: "tn-actions" }, el("button", { class: "btn sm danger", on: { click: async () => { if (await confirmBox(`Revoke key "${k.name}"? Anything using it stops working immediately.`, "Revoke")) { try { await oapi("/keys/" + k.id, { method: "DELETE" }); toast("Key revoked", "ok"); P.render({ refresh: true }); } catch (err) { toast(err.message, "err"); } } } } }, "Revoke")) },
        ],
        empty: empty("No keys", "Create a key to connect an app."),
      });

      const usagePanel = panel("Usage today", el("div", { class: "col" }, meter(used, cap),
        ...me.projects.filter(p => cap && sumUse(p.today)).map(p => el("div", {}, el("div", { class: "tn-meter-row" }, el("span", {}, p.name), el("span", {}, `${fmt(sumUse(p.today))}${p.today.rejected ? ` · ${p.today.rejected} rejected` : ""}`)), el("div", { class: "tn-meter" }, el("i", { style: `width:${Math.min(100, 100 * sumUse(p.today) / Math.max(1, used))}%;background:var(--c1)` })))),
        el("div", { class: "muted", style: "font-size:12px" }, "The daily cap is shared by all projects in the organization and resets at 00:00 UTC. Reads and queries are never limited.")), { sub: "UTC day " + me.day });

      page.append(head(o.name, `Organization · signed in with a${me.key.type === "admin" ? "n admin" : ""} key (${me.key.prefix}…)`, ...actions), kpis,
        el("div", { class: "grid", style: "grid-template-columns:minmax(0,1fr) minmax(280px,360px);align-items:start" },
          el("div", { class: "col" }, panel("Projects", projTable, { sub: "each project is an isolated database", bodyCls: "", style: "min-height:0" }), panel("API keys", keyTable, { sub: "stored hashed; shown once when created", bodyCls: "" })),
          el("div", { class: "col" }, usagePanel, panel("Limits", el("div", { class: "kv", style: "margin:-12px -14px" }, ...[["Daily ingest cap", cap ? fmt(cap) : "unlimited"], ["Retention", o.retention_hours + " hours"], ["Plan changes", "ask your Pulse operator"]].flat().map(x => el("div", {}, x)))))));
    },
  });

  function newProject() {
    const name = el("input", { placeholder: "e.g. production" });
    modal({ title: "New project", width: 420, body: el("div", { class: "col" }, field("Name", name, "A project is an isolated database for one environment or app."), el("div", { class: "muted" }, "You will create an ingest key for it next.")),
      actions: [{ label: "Cancel" }, { label: "Create project", primary: true, onClick: async () => {
        if (!name.value.trim()) { toast("Enter a name", "err"); return false; }
        const p = await oapi("/projects", { method: "POST", body: { name: name.value.trim() } });
        toast(`Project "${p.name}" created`, "ok"); await P.render({ refresh: true }); createKeyFlow({ type: "ingest", projectId: p.id });
      } }] });
  }

  // ---------------------------------------------------------------- Settings: deployment mode panel
  const settingsView = P.views.find(v => v.re.test("/settings"))?.view;
  if (settingsView) {
    const orig = settingsView.render;
    settingsView.render = async function (page, ctx) {
      await orig.call(this, page, ctx);
      const grid = page.querySelector(".grid"); if (!grid) return;
      const proj = store.get("project", "");
      const rows = [["Mode", MODES[P.mode] || P.mode], ["Data lives", hosted() ? "On Pulse infrastructure, one database per project" : "In your own cluster / volume"],
        ["Organizations", hosted() ? "enabled" : "not used (single workspace)"], ["Quotas & retention", hosted() ? "per organization, set by the operator" : "PULSE0_RETENTION_HOURS"],
        ["Claude assistant", P.features.ai_assistant ? "enabled (query snippets go to Anthropic)" : "off"]];
      grid.prepend(panel("Deployment mode", el("div", { class: "col" },
        el("div", { style: "display:flex;gap:8px;align-items:center;flex-wrap:wrap" }, pill(MODES[P.mode] || P.mode, hosted() ? "info" : "ok"), el("a", { class: "btn sm", href: "#/onboarding" }, "Compare modes & connect apps"), hosted() ? el("a", { class: "btn sm", href: "#/organization" }, "Organization") : null),
        el("div", { class: "kv", style: "margin:0 -14px" }, ...rows.flat().map(x => el("div", {}, x))),
        hosted() && proj ? el("div", { style: "display:flex;gap:8px;align-items:center" }, el("span", { class: "muted" }, "Active project"), el("span", { class: "mono" }, proj), el("button", { class: "btn sm", on: { click: () => { store.set("project", ""); toast("Project selection cleared"); P.render(); } } }, "Clear")) : null)));
    };
  }
})();
