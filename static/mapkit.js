/* Pulse0 service-map kit: graph building, grouping and layered layout. Pure functions (no DOM),
   usable in the browser (window.MapKit) and in Node (module.exports) so it can be unit-tested.
   Every loop is bounded by V+E (cycles are broken before layering), so no input can hang the page. */
(function (root) {
  "use strict";
  const GROUP_PREFIX = n => { const i = n.search(/[-_.:/]/); return i > 0 ? n.slice(0, i) : n; };
  const errState = p => (p >= 5 ? "err" : p >= 1 ? "warn" : "ok");

  /* ---- graph ------------------------------------------------------------------------------------------- */
  // edges: /api/map rows {src,dst,calls,err,avg_ms}; services: /api/services rows; meta: {svc: {language,namespace,...}}
  function buildGraph(edges, services, meta, opts) {
    const o = Object.assign({ onlyErrors: false, hideExternal: false, minCalls: 0, minErrPct: 0 }, opts || {});
    meta = meta || {};
    const info = new Map((services || []).map(s => [s.service, s]));
    const nodes = new Map();
    const ensure = name => {
      let n = nodes.get(name);
      if (n) return n;
      const s = info.get(name), m = meta[name] || {}, live = !!(s && s.n > 0);
      n = nodes.set(name, {
        id: name, name, external: !live && !m.language, language: m.language || "", namespace: m.namespace || "", version: m.version || "",
        n: live ? s.n : 0, err: live ? s.err || 0 : 0, err_pct: live ? s.err_pct || 0 : 0, p95: live ? s.p95 || 0 : 0, rate: live ? s.rate || 0 : 0,
        group: null, members: null, in: 0, out: 0, calls_in: 0,
      }).get(name);
      return n;
    };
    const merged = new Map();
    const edgeFilter = o.onlyErrors || o.hideExternal || o.minCalls > 0 || o.minErrPct > 0;
    for (const e of edges || []) {
      if (!e || !e.src || !e.dst || e.src === e.dst) continue;
      const a = ensure(e.src), b = ensure(e.dst);
      const calls = e.calls || 0, err = e.err || 0, ep = calls ? (100 * err) / calls : 0;
      if (calls < o.minCalls || ep < o.minErrPct) continue;
      if (o.onlyErrors && !err) continue;
      if (o.hideExternal && (a.external || b.external)) continue;
      const k = e.src + "\u0000" + e.dst;
      const m = merged.get(k);
      if (m) { m.avg_ms = (m.avg_ms * m.calls + (e.avg_ms || 0) * calls) / Math.max(1, m.calls + calls); m.calls += calls; m.err += err; }
      else merged.set(k, { id: k, src: e.src, dst: e.dst, calls, err, avg_ms: e.avg_ms || 0 });
    }
    const outEdges = [...merged.values()];
    const keep = new Set();
    for (const e of outEdges) { keep.add(e.src); keep.add(e.dst); }
    // isolated, instrumented services (e.g. a browser app) stay visible unless an edge filter is hiding things
    for (const s of info.values()) {
      if (s.n > 0 && !keep.has(s.service) && (!edgeFilter || (o.onlyErrors && s.err > 0 && !o.hideExternal))) { ensure(s.service); keep.add(s.service); }
    }
    const list = [...nodes.values()].filter(n => keep.has(n.id));
    const byId = new Map(list.map(n => [n.id, n]));
    for (const e of outEdges) { byId.get(e.src).out++; byId.get(e.dst).in++; byId.get(e.dst).calls_in += e.calls; }
    return { nodes: list, edges: outEdges, byId };
  }

  /* ---- grouping ---------------------------------------------------------------------------------------- */
  function assignGroups(g, mode) {
    const count = new Map();
    for (const n of g.nodes) {
      n.group = mode === "namespace" ? (n.external ? "external" : n.namespace || "default") : mode === "prefix" ? GROUP_PREFIX(n.name) : null;
      if (n.group) count.set(n.group, (count.get(n.group) || 0) + 1);
    }
    if (mode === "prefix") for (const n of g.nodes) if (count.get(n.group) < 2) n.group = null; // singletons stay ungrouped
    return g;
  }
  function groupSizes(g) { const m = new Map(); for (const n of g.nodes) if (n.group) m.set(n.group, (m.get(n.group) || 0) + 1); return m; }

  // collapse selected groups into one aggregate node; edges re-pointed and merged
  function collapse(g, collapsed) {
    if (!collapsed || !collapsed.size) return g;
    const map = new Map(), nodes = [], agg = new Map();
    for (const n of g.nodes) {
      if (n.group && collapsed.has(n.group)) {
        let a = agg.get(n.group);
        if (!a) { a = { id: "grp:" + n.group, name: n.group, group: n.group, collapsedGroup: true, members: [], external: true, language: "", namespace: "", version: "", n: 0, err: 0, err_pct: 0, p95: 0, rate: 0, in: 0, out: 0, calls_in: 0 }; agg.set(n.group, a); nodes.push(a); }
        a.members.push(n.id); a.n += n.n; a.err += n.err; a.rate += n.rate; a.p95 = Math.max(a.p95, n.p95); a.external = a.external && n.external;
        map.set(n.id, a.id);
      } else { nodes.push(Object.assign({}, n, { in: 0, out: 0, calls_in: 0 })); map.set(n.id, n.id); }
    }
    for (const a of agg.values()) a.err_pct = a.n ? (100 * a.err) / a.n : 0;
    const merged = new Map();
    for (const e of g.edges) {
      const s = map.get(e.src), d = map.get(e.dst);
      if (s === d) continue;
      const k = s + "\u0000" + d, m = merged.get(k);
      if (m) { m.avg_ms = (m.avg_ms * m.calls + e.avg_ms * e.calls) / Math.max(1, m.calls + e.calls); m.calls += e.calls; m.err += e.err; }
      else merged.set(k, { id: k, src: s, dst: d, calls: e.calls, err: e.err, avg_ms: e.avg_ms });
    }
    const byId = new Map(nodes.map(n => [n.id, n])), edges = [...merged.values()];
    for (const e of edges) { byId.get(e.src).out++; byId.get(e.dst).in++; byId.get(e.dst).calls_in += e.calls; }
    return { nodes, edges, byId };
  }

  /* ---- layered layout (Sugiyama-style, every step O(V+E) or bounded sweeps) ---------------------------- */
  function solveOne(g, o) {
    const LR = o.dir !== "TB";
    const MAIN = LR ? o.NW + 112 : o.NH + 66;      // distance between layers
    const SLOT = LR ? o.NH + 18 : o.NW + 26;       // cross-axis size reserved for a real node
    const DUMMY = 16;                              // cross-axis size reserved for an edge bend
    const N = g.nodes.length;
    const idx = new Map(g.nodes.map((n, i) => [n.id, i]));
    const E = g.edges.map(e => ({ s: idx.get(e.src), d: idx.get(e.dst), e }));
    const outE = Array.from({ length: N }, () => []);
    E.forEach((x, i) => outE[x.s].push(i));

    // 1) break cycles with an iterative DFS (back edges are flipped for layering only)
    const indeg = new Int32Array(N);
    E.forEach(x => indeg[x.d]++);
    const order = [...Array(N).keys()].sort((a, b) => indeg[a] - indeg[b] || g.nodes[b].n - g.nodes[a].n);
    const color = new Uint8Array(N), rev = new Uint8Array(E.length);
    for (const s of order) {
      if (color[s]) continue;
      const stack = [[s, 0]]; color[s] = 1;
      while (stack.length) {
        const top = stack[stack.length - 1], u = top[0];
        if (top[1] < outE[u].length) {
          const ei = outE[u][top[1]++], v = E[ei].d;
          if (color[v] === 0) { color[v] = 1; stack.push([v, 0]); } else if (color[v] === 1) rev[ei] = 1;
        } else { color[u] = 2; stack.pop(); }
      }
    }
    // 2) longest-path layering on the resulting DAG (Kahn)
    const fs = E.map((x, i) => (rev[i] ? x.d : x.s)), fd = E.map((x, i) => (rev[i] ? x.s : x.d));
    const succ = Array.from({ length: N }, () => []), pred = Array.from({ length: N }, () => []);
    const deg = new Int32Array(N);
    E.forEach((_, i) => { succ[fs[i]].push(fd[i]); pred[fd[i]].push(fs[i]); deg[fd[i]]++; });
    const layer = new Int32Array(N), topo = [], q = [];
    for (let i = 0; i < N; i++) if (!deg[i]) q.push(i);
    for (let h = 0; h < q.length; h++) { const u = q[h]; topo.push(u); for (const v of succ[u]) { if (layer[v] < layer[u] + 1) layer[v] = layer[u] + 1; if (--deg[v] === 0) q.push(v); } }
    // pull pure sources next to their first consumer so their edges stay short
    for (let i = topo.length - 1; i >= 0; i--) { const u = topo[i]; if (!pred[u].length && succ[u].length) layer[u] = Math.min(...succ[u].map(v => layer[v])) - 1; }
    let minL = 0; for (let i = 0; i < N; i++) minL = Math.min(minL, layer[i]);
    for (let i = 0; i < N; i++) layer[i] -= minL;

    // 3) virtual nodes for edges that span several layers (bounded)
    const vlayer = Array.from(layer), kinds = new Array(N).fill(0); // kinds: 0 real node, 1 virtual bend
    const vin = Array.from({ length: N }, () => []), vout = Array.from({ length: N }, () => []);
    let dummies = 0; const chain = new Array(E.length).fill(null);
    let need = 0; E.forEach((_, i) => { need += Math.max(0, layer[fd[i]] - layer[fs[i]] - 1); });
    const useDummies = need <= o.maxDummies;
    const addV = ly => { vlayer.push(ly); kinds.push(1); vin.push([]); vout.push([]); return vlayer.length - 1; };
    E.forEach((_, i) => {
      let a = fs[i];
      const ch = [];
      if (useDummies) for (let l = layer[fs[i]] + 1; l < layer[fd[i]]; l++) { ch.push(addV(l)); dummies++; }
      for (const k of ch.concat([fd[i]])) { vout[a].push(k); vin[k].push(a); a = k; }
      chain[i] = ch;
    });
    const V = vlayer.length;
    // 4) order nodes inside layers: barycenter sweeps
    const maxLayer = Math.max(0, ...vlayer);
    const rows = Array.from({ length: maxLayer + 1 }, () => []);
    const seed = [...Array(V).keys()].sort((a, b) => (kinds[a] || 0) - (kinds[b] || 0) || ((g.nodes[b]?.n || 0) - (g.nodes[a]?.n || 0)));
    for (const v of seed) rows[vlayer[v]].push(v);
    const rank = new Float64Array(V);
    const reindex = () => rows.forEach(r => r.forEach((v, i) => (rank[v] = i)));
    reindex();
    const sweeps = V > 900 ? 2 : V > 300 ? 4 : 8;
    const bary = (v, nb) => { if (!nb[v].length) return rank[v]; let s = 0; for (const u of nb[v]) s += rank[u]; return s / nb[v].length; };
    for (let it = 0; it < sweeps; it++) {
      const down = it % 2 === 0;
      const ls = down ? rows.map((_, i) => i).slice(1) : rows.map((_, i) => i).slice(0, -1).reverse();
      for (const li of ls) {
        const keyed = rows[li].map(v => [bary(v, down ? vin : vout), rank[v], v]);
        keyed.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
        rows[li] = keyed.map(k => k[2]);
        rows[li].forEach((v, i) => (rank[v] = i));
      }
    }
    // 5) coordinates: even packing, then a few barycentric straightening passes that keep order and spacing
    const size = v => (kinds[v] ? DUMMY : SLOT);
    const cross = new Float64Array(V);
    for (const r of rows) { let y = 0; for (const v of r) { cross[v] = y + size(v) / 2; y += size(v); } const mid = y / 2; for (const v of r) cross[v] -= mid; }
    const pack = r => { // resolve overlaps keeping order, minimal displacement
      for (let i = 1; i < r.length; i++) { const need = (size(r[i - 1]) + size(r[i])) / 2; if (cross[r[i]] - cross[r[i - 1]] < need) cross[r[i]] = cross[r[i - 1]] + need; }
      for (let i = r.length - 2; i >= 0; i--) { const need = (size(r[i + 1]) + size(r[i])) / 2; if (cross[r[i + 1]] - cross[r[i]] < need) cross[r[i]] = cross[r[i + 1]] - need; }
    };
    for (let it = 0; it < 4; it++) {
      const down = it % 2 === 0, ls = rows.map((_, i) => i); if (!down) ls.reverse();
      for (const li of ls) {
        const r = rows[li];
        for (const v of r) { const nb = (down ? vin : vout)[v].concat(it > 1 ? (down ? vout : vin)[v] : []); if (nb.length) { let s = 0; for (const u of nb) s += cross[u]; cross[v] = (cross[v] + s / nb.length) / 2; } }
        pack(r);
      }
    }
    // 6) raw positions + edge waypoints (SVG paths are built after component packing, in finish())
    const raw = new Map(), way = new Map();
    const place = v => (LR ? { x: vlayer[v] * MAIN, y: cross[v] } : { x: cross[v], y: vlayer[v] * MAIN });
    for (let i = 0; i < N; i++) raw.set(g.nodes[i].id, place(i));
    const hw = o.NW / 2, hh = o.NH / 2;
    E.forEach((x, i) => {
      const a = place(fs[i]), b = place(fd[i]);
      const pts = [LR ? { x: a.x + hw, y: a.y } : { x: a.x, y: a.y + hh }];
      for (const k of chain[i]) pts.push(place(k));
      pts.push(LR ? { x: b.x - hw, y: b.y } : { x: b.x, y: b.y - hh });
      if (rev[i]) pts.reverse();
      way.set(g.edges[i].id, { pts, reversed: !!rev[i] });
    });
    return { pos: raw, way, layers: maxLayer + 1, dummies, cycles: rev.reduce((a, b) => a + b, 0) };
  }

  // weakly connected components (union-find), as arrays of node indexes
  function components(g) {
    const idx = new Map(g.nodes.map((n, i) => [n.id, i])), par = g.nodes.map((_, i) => i);
    const find = x => { while (par[x] !== x) { par[x] = par[par[x]]; x = par[x]; } return x; };
    for (const e of g.edges) { const a = find(idx.get(e.src)), b = find(idx.get(e.dst)); if (a !== b) par[a] = b; }
    const by = new Map();
    g.nodes.forEach((_, i) => { const r = find(i); (by.get(r) || by.set(r, []).get(r)).push(i); });
    return [...by.values()].sort((a, b) => b.length - a.length);
  }

  // smooth path through waypoints (horizontal tangents for LR, vertical for TB)
  function pathOf(pts, LR) {
    let d = "M" + r1(pts[0].x) + "," + r1(pts[0].y);
    for (let j = 1; j < pts.length; j++) {
      const p = pts[j - 1], c = pts[j];
      if (LR) { const dx = Math.max(24, Math.abs(c.x - p.x) / 2) * Math.sign(c.x - p.x || 1); d += `C${r1(p.x + dx)},${r1(p.y)} ${r1(c.x - dx)},${r1(c.y)} ${r1(c.x)},${r1(c.y)}`; }
      else { const dy = Math.max(24, Math.abs(c.y - p.y) / 2) * Math.sign(c.y - p.y || 1); d += `C${r1(p.x)},${r1(p.y + dy)} ${r1(c.x)},${r1(c.y - dy)} ${r1(c.x)},${r1(c.y)}`; }
    }
    return d;
  }

  function boundsOf(pos, hw, hh) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of pos.values()) { x0 = Math.min(x0, p.x - hw); x1 = Math.max(x1, p.x + hw); y0 = Math.min(y0, p.y - hh); y1 = Math.max(y1, p.y + hh); }
    return { x0, y0, x1, y1 };
  }

  function layout(g, opts) {
    const o = Object.assign({ dir: "LR", NW: 208, NH: 78, maxDummies: 1500, aspect: 1.8 }, opts || {});
    const LR = o.dir !== "TB", hw = o.NW / 2, hh = o.NH / 2, N = g.nodes.length;
    const comps = components(g);
    let solved;
    if (comps.length <= 1) solved = solveOne(g, o);
    else { // lay every component out on its own, then shelf-pack the boxes to roughly match the canvas shape
      const parts = comps.map(ids => {
        const set = new Set(ids.map(i => g.nodes[i].id));
        const sub = { nodes: ids.map(i => g.nodes[i]), edges: g.edges.filter(e => set.has(e.src)) };
        const s = solveOne(sub, o); s.box = boundsOf(s.pos, hw, hh); return s;
      });
      const gap = 56, area = parts.reduce((a, s) => a + (s.box.x1 - s.box.x0 + gap) * (s.box.y1 - s.box.y0 + gap), 0);
      const rowW = Math.max(...parts.map(s => s.box.x1 - s.box.x0), Math.sqrt(area * o.aspect));
      let x = 0, y = 0, rowH = 0;
      solved = { pos: new Map(), way: new Map(), layers: 0, dummies: 0, cycles: 0 };
      for (const s of parts) {
        const w = s.box.x1 - s.box.x0, h = s.box.y1 - s.box.y0;
        if (x > 0 && x + w > rowW) { x = 0; y += rowH + gap; rowH = 0; }
        const dx = x - s.box.x0, dy = y - s.box.y0;
        for (const [id, p] of s.pos) solved.pos.set(id, { x: p.x + dx, y: p.y + dy });
        for (const [id, wv] of s.way) solved.way.set(id, { reversed: wv.reversed, pts: wv.pts.map(q => ({ x: q.x + dx, y: q.y + dy })) });
        x += w + gap; rowH = Math.max(rowH, h);
        solved.layers = Math.max(solved.layers, s.layers); solved.dummies += s.dummies; solved.cycles += s.cycles;
      }
    }
    const paths = new Map();
    for (const e of g.edges) {
      const wv = solved.way.get(e.id), pts = wv.pts, m = (pts.length - 1) >> 1, p = pts[m], c = pts[m + 1];
      paths.set(e.id, { d: pathOf(pts, LR), lx: (p.x + c.x) / 2, ly: (p.y + c.y) / 2, reversed: wv.reversed, pts });
    }
    let bounds = boundsOf(solved.pos, hw, hh);
    if (!N) bounds = { x0: -100, y0: -100, x1: 100, y1: 100 };
    // group hulls (only when the group's box is free of foreign nodes, otherwise skipped)
    const hulls = [], byGroup = new Map(), list = g.nodes.map(n => solved.pos.get(n.id));
    g.nodes.forEach((n, i) => { if (n.group && !n.collapsedGroup) (byGroup.get(n.group) || byGroup.set(n.group, []).get(n.group)).push(i); });
    for (const [name, ids] of byGroup) {
      if (ids.length < 2) continue;
      const pad = 22; let a0 = Infinity, b0 = Infinity, a1 = -Infinity, b1 = -Infinity;
      for (const i of ids) { const p = list[i]; a0 = Math.min(a0, p.x - hw - pad); a1 = Math.max(a1, p.x + hw + pad); b0 = Math.min(b0, p.y - hh - pad); b1 = Math.max(b1, p.y + hh + pad + 12); }
      const set = new Set(ids); let clean = true;
      for (let i = 0; i < N && clean; i++) if (!set.has(i) && list[i].x > a0 && list[i].x < a1 && list[i].y > b0 && list[i].y < b1) clean = false;
      if (clean) hulls.push({ name, x: a0, y: b0, w: a1 - a0, h: b1 - b0, count: ids.length });
    }
    return { pos: solved.pos, paths, bounds, layers: solved.layers, dummies: solved.dummies, cycles: solved.cycles, components: comps.length, hulls, NW: o.NW, NH: o.NH, dir: LR ? "LR" : "TB" };
  }
  const r1 = n => Math.round(n * 10) / 10;

  /* ---- helpers ----------------------------------------------------------------------------------------- */
  // BFS in one or both directions; returns {nodes:Set, edges:Set(edge ids)}
  function reach(g, id) {
    const out = new Map(), inn = new Map();
    for (const e of g.edges) { (out.get(e.src) || out.set(e.src, []).get(e.src)).push(e); (inn.get(e.dst) || inn.set(e.dst, []).get(e.dst)).push(e); }
    const nodes = new Set([id]), edges = new Set();
    for (const [adj, nxt] of [[out, "dst"], [inn, "src"]]) {
      const seen = new Set([id]), q = [id];
      for (let h = 0; h < q.length; h++) for (const e of adj.get(q[h]) || []) { edges.add(e.id); const v = e[nxt]; nodes.add(v); if (!seen.has(v)) { seen.add(v); q.push(v); } }
    }
    return { nodes, edges };
  }
  function search(g, text) {
    const t = (text || "").trim().toLowerCase();
    if (!t) return [];
    return g.nodes.filter(n => n.name.toLowerCase().includes(t) || (n.members || []).some(m => m.toLowerCase().includes(t))).sort((a, b) => (a.name.toLowerCase().startsWith(t) ? 0 : 1) - (b.name.toLowerCase().startsWith(t) ? 0 : 1) || b.n - a.n);
  }
  function fit(bounds, w, h, pad, maxK) {
    pad = pad == null ? 48 : pad; maxK = maxK || 1.25;
    const bw = Math.max(1, bounds.x1 - bounds.x0), bh = Math.max(1, bounds.y1 - bounds.y0);
    const k = Math.max(0.04, Math.min(maxK, (w - 2 * pad) / bw, (h - 2 * pad) / bh));
    return { k, tx: w / 2 - (k * (bounds.x0 + bounds.x1)) / 2, ty: h / 2 - (k * (bounds.y0 + bounds.y1)) / 2 };
  }
  const LANGS = { python: ["Py", "#3776ab"], java: ["Jv", "#e76f00"], nodejs: ["JS", "#5fa04e"], go: ["Go", "#00acd7"], dotnet: ["C#", "#7b3fe4"], webjs: ["Web", "#e34c8c"], ruby: ["Rb", "#cc342d"], php: ["Php", "#777bb3"], rust: ["Rs", "#b7410e"], cpp: ["C++", "#00599c"], erlang: ["Er", "#a90533"], swift: ["Sw", "#f05138"] };
  const langBadge = l => (l ? LANGS[String(l).toLowerCase()] || [String(l).slice(0, 2), "#7b889c"] : null);

  const api = { buildGraph, assignGroups, groupSizes, collapse, layout, reach, search, fit, langBadge, errState, GROUP_PREFIX };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.MapKit = api;
})(typeof window !== "undefined" ? window : globalThis);
