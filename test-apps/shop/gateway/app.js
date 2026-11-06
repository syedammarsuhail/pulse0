// API gateway. No OpenTelemetry imports: attached with node --require @opentelemetry/auto-instrumentations-node/register
const express = require("express");
const pino = require("pino");
const log = pino();
const app = express();
app.use(express.json());
const U = {
  catalog: process.env.CATALOG_URL || "http://catalog:8000",
  orders: process.env.ORDERS_URL || "http://orders:8000",
};
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function chaos(req, res) {
  const c = req.headers["x-chaos"] || "";
  if (c === "slow:gateway") { log.warn("injected slowness"); await sleep(1000 + Math.random() * 800); }
  if (c === "error:gateway") { log.error("injected failure: upstream pool exhausted"); res.status(500).json({ error: "gateway failure" }); return true; }
  return false;
}
async function up(req, url, init = {}) {
  const headers = { "content-type": "application/json" };
  if (req.headers["x-chaos"]) headers["x-chaos"] = req.headers["x-chaos"];
  const r = await fetch(url, { ...init, headers });
  const body = await r.text();
  if (!r.ok) throw Object.assign(new Error(`${url} -> ${r.status}`), { status: r.status });
  return JSON.parse(body || "{}");
}
function route(path, method, fn) {
  app[method](path, async (req, res) => {
    if (await chaos(req, res)) return;
    try { res.json(await fn(req)); }
    catch (e) { log.error({ err: e.message }, "request failed"); res.status(502).json({ error: e.message }); }
  });
}
app.get("/health", (_, res) => res.send("ok"));
route("/api/products", "get", req => { log.info("browse"); return up(req, U.catalog + "/products"); });
route("/api/search", "get", req => { log.info({ q: req.query.q }, "search"); return up(req, U.catalog + "/search?q=" + encodeURIComponent(req.query.q || "")); });
route("/api/checkout", "post", req => { log.info({ items: (req.body.items || []).length }, "checkout"); return up(req, U.orders + "/orders", { method: "POST", body: JSON.stringify(req.body) }); });
route("/api/admin/stats", "get", async req => {
  log.info("admin stats");
  const [o, c] = await Promise.all([up(req, U.orders + "/stats"), up(req, U.catalog + "/stats")]);
  return { orders: o, catalog: c };
});
app.listen(8000, () => log.info("gateway listening on 8000"));
