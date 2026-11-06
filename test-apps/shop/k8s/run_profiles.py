"""Run each load profile briefly and compare loadgen's own counters with what Pulse0 recorded.
Usage: python run_profiles.py <kube-context> <pulse0-url> [seconds-per-profile]"""
import json, subprocess, sys, time, urllib.request

CTX, P0 = sys.argv[1], sys.argv[2].rstrip("/")
SECS = int(sys.argv[3]) if len(sys.argv) > 3 else 40
K = ["kubectl", "--context", CTX, "-n", "langtest", "exec", "deploy/loadgen", "--", "python", "-c"]


def lg(method, path):
    code = f"import urllib.request as u;r=u.Request('http://localhost:9000{path}',method='{method}');print(u.urlopen(r).read().decode())"
    return json.loads(subprocess.run(K + [code], capture_output=True, text=True, check=True).stdout)


def p0(path):
    return json.load(urllib.request.urlopen(P0 + "/api" + path, timeout=30))


def totals():
    t = lg("GET", "/status")["totals"]
    return {k: sum(v[k] for v in t.values()) for k in ("n", "err", "slow", "inj_err")}


def gw():
    s = {x["service"]: x for x in p0("/services?minutes=120")}
    return {k: (s.get(k, {}).get("n", 0), s.get(k, {}).get("err", 0)) for k in
            ("shop-gateway", "shop-catalog", "shop-orders", "shop-payments", "shop-inventory")}


rows = []
for prof, extra in [("steady", ""), ("ramp", ""), ("spike", ""), ("soak", ""), ("error-storm", "")]:
    a, g0 = totals(), gw()
    lg("POST", f"/control?profile={prof}&rps=5&duration={SECS}")
    time.sleep(SECS + 6)
    b, g1 = totals(), gw()
    d = {k: b[k] - a[k] for k in a}
    gd = {k: (g1[k][0] - g0[k][0], g1[k][1] - g0[k][1]) for k in g0}
    rows.append((prof, d, gd))
    print(f"{prof:12} loadgen: n={d['n']:4} err={d['err']:3} ({100 * d['err'] / max(d['n'], 1):4.1f}%) injected={d['inj_err']:3} slow>1s={d['slow']:3} | "
          f"pulse0 spans(n/err) " + " ".join(f"{k.replace('shop-', '')}={v[0]}/{v[1]}" for k, v in gd.items()), flush=True)
lg("POST", "/control?profile=steady&rps=5&duration=0")  # leave it running steadily
print("loadgen left on: steady, 5 rps, 8% injected errors, forever", flush=True)
