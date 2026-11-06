"""Verify the shop in Pulse0.  Usage: python verify_shop.py <pulse0-url> [minutes]"""
import json, sys, urllib.request

P0 = sys.argv[1].rstrip("/")
MIN = sys.argv[2] if len(sys.argv) > 2 else "15"
g = lambda p: json.load(urllib.request.urlopen(P0 + "/api" + p, timeout=30))
SVC = ["shop-storefront", "shop-gateway", "shop-catalog", "shop-orders", "shop-payments", "shop-inventory"]

svcs = {s["service"]: s for s in g(f"/services?minutes={MIN}")}
mn = g("/metrics/names")
print(f"{'service':16}{'spans':>7}{'err%':>7}{'traces':>8}{'logs':>6}{'logs+trace':>11}{'metrics':>8}")
for s in SVC:
    x = svcs.get(s)
    lg = g(f"/logs?minutes={MIN}&service={s}&limit=500")
    tr = g(f"/traces?minutes={MIN}&service={s}&limit=500")
    mets = [m["name"] for m in mn if s in m["services"].split(",")]
    print(f"{s:16}{x['n'] if x else 0:>7}{(x['err_pct'] if x else 0):>7.1f}{len(tr):>8}{len(lg):>6}{sum(1 for l in lg if l.get('trace_id')):>11}{len(mets):>8}")

edges = [(e["src"], e["dst"], e["calls"], e["err"]) for e in g(f"/map?minutes={MIN}") if e["src"].startswith("shop") or e["dst"].startswith("shop")]
print("\nservice-map edges:")
for e in sorted(edges):
    print(f"  {e[0]:16} -> {e[1]:16} calls={e[2]:5} err={e[3]}")

# deepest cross-language trace
best = None
for t in g(f"/traces?minutes={MIN}&service=shop-gateway&limit=300"):
    d = g("/traces/" + t["trace_id"]); sp = d["spans"] if isinstance(d, dict) and "spans" in d else d
    sv = sorted({s["service"] for s in sp})
    if best is None or len(sv) > len(best[1]) or (len(sv) == len(best[1]) and len(sp) > best[2]):
        best = (t["trace_id"], sv, len(sp))
print("\nbest cross-service trace:", best[0][:16], "services:", best[1], "spans:", best[2])
d = g("/traces/" + best[0]); sp = d["spans"] if isinstance(d, dict) and "spans" in d else d
print("  logs correlated to that trace:", len(g(f"/logs?minutes={MIN}&trace_id={best[0]}&limit=100")))
