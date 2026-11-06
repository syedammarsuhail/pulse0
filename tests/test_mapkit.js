// Unit tests for static/mapkit.js (service-map layout). Run: node tests/test_mapkit.js
// Regression guard for "service map freezes the tab": every layout must finish fast, for any graph shape.
const assert = require("assert");
const K = require("../static/mapkit.js");
let passed = 0;
const t = (name, fn) => { const s = Date.now(); fn(); passed++; console.log(`ok  ${name} (${Date.now() - s}ms)`); };
const edge = (src, dst, calls = 10, err = 0) => ({ src, dst, calls, err, avg_ms: 20 });
const svc = (service, n = 100, err_pct = 0) => ({ service, n, err: Math.round(n * err_pct / 100), err_pct, p95: 50, rate: n / 300 });
const rnd = (seed => () => (seed = (seed * 16807) % 2147483647) / 2147483647)(7);

const LIVE = [edge("frontend", "backend", 259, 19), edge("lang-dotnet", "lang-python", 36, 18), edge("lang-go", "lang-python", 24), edge("lang-java", "lang-python", 38, 9),
  edge("lang-node", "lang-python", 31, 11), edge("shop-gateway", "shop-catalog", 32, 8), edge("shop-gateway", "shop-orders", 15, 3), edge("shop-orders", "shop-inventory", 19, 1), edge("shop-orders", "shop-payments", 9)];
const LIVE_SVCS = ["frontend", "backend", "lang-dotnet", "lang-python", "lang-go", "lang-java", "lang-node", "lang-angular", "shop-gateway", "shop-catalog", "shop-orders", "shop-inventory", "shop-payments"].map(s => svc(s));

function check(g, L) {
  assert.ok(L.pos.size === g.nodes.length);
  for (const p of L.pos.values()) assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y));
  for (const e of g.edges) { const p = L.paths.get(e.id); assert.ok(p && p.d.startsWith("M") && !/NaN|Infinity/.test(p.d), "path for " + e.id); }
  // no two real nodes overlap (same layer => separated on the cross axis by at least the node size)
  const ns = g.nodes.map(n => L.pos.get(n.id));
  const key = L.dir === "LR" ? "x" : "y", cr = L.dir === "LR" ? "y" : "x", need = (L.dir === "LR" ? L.NH : L.NW) - 1;
  const by = new Map(); ns.forEach(p => (by.get(p[key]) || by.set(p[key], []).get(p[key])).push(p[cr]));
  for (const arr of by.values()) { arr.sort((a, b) => a - b); for (let i = 1; i < arr.length; i++) assert.ok(arr[i] - arr[i - 1] >= need, "overlap in layer"); }
}

t("live-like graph lays out, edges flow left to right", () => {
  const g = K.buildGraph(LIVE, LIVE_SVCS, {});
  const L = K.layout(g, { dir: "LR" }); check(g, L);
  for (const e of g.edges) assert.ok(L.pos.get(e.src).x < L.pos.get(e.dst).x, `${e.src} -> ${e.dst} must go right`);
  assert.ok(g.byId.has("lang-angular"), "isolated instrumented service stays visible");
});
t("top-down layout", () => { const g = K.buildGraph(LIVE, LIVE_SVCS, {}); const L = K.layout(g, { dir: "TB" }); check(g, L); for (const e of g.edges) assert.ok(L.pos.get(e.src).y < L.pos.get(e.dst).y); });
t("cycles (a<->b, 3-cycle, self loop) never hang", () => {
  const g = K.buildGraph([edge("a", "b"), edge("b", "a"), edge("b", "c"), edge("c", "d"), edge("d", "b"), edge("x", "x")], [svc("a"), svc("b"), svc("c"), svc("d")], {});
  const L = K.layout(g); check(g, L); assert.ok(L.cycles >= 1); assert.strictEqual(g.edges.some(e => e.src === e.dst), false);
});
t("empty and single-node graphs", () => {
  const e = K.layout(K.buildGraph([], [], {})); assert.ok(Number.isFinite(e.bounds.x0));
  const g = K.buildGraph([], [svc("solo")], {}); check(g, K.layout(g));
});
t("60 nodes / 200 edges layout is fast", () => {
  const names = Array.from({ length: 60 }, (_, i) => "svc-" + i), E = [];
  while (E.length < 200) { const a = (rnd() * 60) | 0, b = (rnd() * 60) | 0; if (a !== b) E.push(edge(names[a], names[b], 5 + ((rnd() * 500) | 0), (rnd() * 20) | 0)); }
  const g = K.buildGraph(E, names.map(n => svc(n)), {}), s = Date.now(); const L = K.layout(g); check(g, L);
  assert.ok(Date.now() - s < 1500, "layout took " + (Date.now() - s) + "ms");
});
t("stress: 400 nodes / 1500 edges stays bounded", () => {
  const names = Array.from({ length: 400 }, (_, i) => "n" + i), E = [];
  while (E.length < 1500) { const a = (rnd() * 400) | 0, b = (rnd() * 400) | 0; if (a !== b) E.push(edge(names[a], names[b], 5)); }
  const g = K.buildGraph(E, names.map(n => svc(n)), {}), s = Date.now(); const L = K.layout(g);
  assert.ok(Date.now() - s < 4000, "layout took " + (Date.now() - s) + "ms"); assert.ok(L.pos.size === g.nodes.length);
});
t("deep chain of 120 services", () => {
  const E = Array.from({ length: 119 }, (_, i) => edge("c" + i, "c" + (i + 1))), g = K.buildGraph(E, [], {}); const L = K.layout(g); check(g, L); assert.strictEqual(L.layers, 120);
});
t("filters: only errors / hide externals / min calls", () => {
  const E = [edge("a", "b", 100, 5), edge("b", "c", 100, 0), edge("a", "redis", 50, 0)], S = [svc("a"), svc("b"), svc("c")];
  assert.strictEqual(K.buildGraph(E, S, {}, { onlyErrors: true }).edges.length, 1);
  const noExt = K.buildGraph(E, S, {}, { hideExternal: true }); assert.ok(!noExt.byId.has("redis"));
  assert.strictEqual(K.buildGraph(E, S, {}, { minCalls: 80 }).edges.length, 2);
  assert.ok(K.buildGraph(E, S, {}).byId.get("redis").external);
});
t("duplicate edges merge, self loops dropped", () => {
  const g = K.buildGraph([edge("a", "b", 10, 1), edge("a", "b", 30, 3), edge("a", "a", 5)], [svc("a"), svc("b")], {});
  assert.strictEqual(g.edges.length, 1); assert.strictEqual(g.edges[0].calls, 40); assert.strictEqual(g.edges[0].err, 4);
});
t("disconnected components are packed side by side, not stacked in one column", () => {
  const g = K.buildGraph(LIVE, LIVE_SVCS, {}); const L = K.layout(g, { dir: "LR", aspect: 2 }); check(g, L);
  assert.strictEqual(L.components, 4);
  const w = L.bounds.x1 - L.bounds.x0, h = L.bounds.y1 - L.bounds.y0; assert.ok(w / h > 0.9, "aspect " + (w / h).toFixed(2));
  const iso = K.buildGraph([], Array.from({ length: 30 }, (_, i) => svc("solo" + i)), {}); const Li = K.layout(iso, { aspect: 2 }); check(iso, Li);
  const iw = Li.bounds.x1 - Li.bounds.x0, ih = Li.bounds.y1 - Li.bounds.y0; assert.ok(iw / ih > 1 && iw / ih < 4, "isolated aspect " + (iw / ih).toFixed(2));
  // components never overlap: every pair of nodes is separated by at least a node box on some axis
  const ps = [...Li.pos.values()]; for (let i = 0; i < ps.length; i++) for (let j = i + 1; j < ps.length; j++) assert.ok(Math.abs(ps[i].x - ps[j].x) >= 207 || Math.abs(ps[i].y - ps[j].y) >= 77, "overlap");
});
t("grouping by namespace and prefix, collapse aggregates", () => {
  const meta = { a: { namespace: "shop" }, b: { namespace: "shop" }, c: { namespace: "demo", language: "go" } };
  let g = K.buildGraph([edge("a", "b", 10, 1), edge("b", "c", 20, 2)], [svc("a"), svc("b"), svc("c")], meta);
  K.assignGroups(g, "namespace"); assert.strictEqual(K.groupSizes(g).get("shop"), 2);
  const c = K.collapse(g, new Set(["shop"]));
  assert.strictEqual(c.nodes.length, 2); assert.strictEqual(c.edges.length, 1); const grp = c.byId.get("grp:shop"); assert.strictEqual(grp.members.length, 2); assert.strictEqual(grp.n, 200);
  const p = K.buildGraph([edge("shop-a", "shop-b"), edge("shop-b", "lone")], [svc("shop-a"), svc("shop-b"), svc("lone")], {}); K.assignGroups(p, "prefix");
  assert.strictEqual(p.byId.get("lone").group, null); assert.strictEqual(p.byId.get("shop-a").group, "shop");
});
t("reach, search, fit, badges", () => {
  const g = K.buildGraph(LIVE, LIVE_SVCS, {}); const r = K.reach(g, "shop-orders");
  assert.ok(r.nodes.has("shop-gateway") && r.nodes.has("shop-payments") && !r.nodes.has("frontend"));
  assert.strictEqual(K.search(g, "gate")[0].name, "shop-gateway");
  const L = K.layout(g), f = K.fit(L.bounds, 1000, 600); assert.ok(f.k > 0 && Number.isFinite(f.tx));
  assert.deepStrictEqual(K.langBadge("python")[0], "Py"); assert.strictEqual(K.langBadge(""), null);
});
console.log(`\n${passed} map layout tests passed`);
