"""Configurable load generator for the Pulse0 shop demo (stdlib only).

Env: TARGET (default http://gateway:8000), RPS (base requests/s, default 5), DURATION (seconds, 0 = run forever),
     PROFILE (steady|ramp|spike|soak|error-storm), ERROR_RATE (0-1, default 0.08), SLOW_RATE (0-1, default 0.05),
     CONTROL_PORT (default 9000), MIX (scenario weights, default browse=45,search=20,checkout=25,admin=10).
Runtime control:  GET /status    POST /control?profile=spike&rps=10&error_rate=0.1&slow_rate=0.05&duration=60
Failures are injected with an `x-chaos: error:<service>` / `slow:<service>` header that the shop services honour.
"""
import json, os, random, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TARGET = os.environ.get("TARGET", "http://gateway:8000").rstrip("/")
state = {
    "profile": os.environ.get("PROFILE", "steady"),
    "rps": float(os.environ.get("RPS", "5")),
    "duration": float(os.environ.get("DURATION", "0")),
    "error_rate": float(os.environ.get("ERROR_RATE", "0.08")),
    "slow_rate": float(os.environ.get("SLOW_RATE", "0.05")),
    "started": time.time(),
}
MIX = {k: float(v) for k, v in (p.split("=") for p in os.environ.get("MIX", "browse=45,search=20,checkout=25,admin=10").split(","))}
# services each scenario's request path touches (where a failure can be injected)
PATH = {"browse": ["gateway", "catalog"], "search": ["gateway", "catalog"],
        "checkout": ["gateway", "orders", "inventory", "payments"], "admin": ["gateway", "orders", "catalog"]}
PROFILES = ("steady", "ramp", "spike", "soak", "error-storm")
lock = threading.Lock()
stats = {s: {"n": 0, "err": 0, "slow": 0, "inj_err": 0, "lat": 0.0} for s in MIX}
window = {s: {"n": 0, "err": 0, "slow": 0, "inj_err": 0, "lat": 0.0} for s in MIX}


def current():
    """(rps, error_rate, done) for the active profile at this moment."""
    t = time.time() - state["started"]
    d = state["duration"]
    base, er = state["rps"], state["error_rate"]
    p = state["profile"]
    if d and t >= d:
        return 0.0, er, True
    frac = (t / d) if d else (t % 120) / 120.0
    if p == "ramp":
        return base * (0.2 + 2.8 * frac), er, False
    if p == "spike":
        return (base * 5 if 0.4 <= frac < 0.6 else base), er, False
    if p == "error-storm":
        return base, max(er, 0.4), False
    return base, er, False   # steady, soak


def one(scenario, err_rate):
    headers = {"content-type": "application/json"}
    injected = None
    if random.random() < err_rate:
        injected = random.choice(PATH[scenario]); headers["x-chaos"] = "error:" + injected
    elif random.random() < state["slow_rate"]:
        headers["x-chaos"] = "slow:" + random.choice(PATH[scenario])
    if scenario == "browse":
        req = urllib.request.Request(TARGET + "/api/products", headers=headers)
    elif scenario == "search":
        req = urllib.request.Request(TARGET + "/api/search?q=" + random.choice(["widget", "gizmo", "gadget", "zzz"]), headers=headers)
    elif scenario == "checkout":
        body = json.dumps({"items": [{"sku": "widget", "qty": random.randint(1, 3)}]}).encode()
        req = urllib.request.Request(TARGET + "/api/checkout", data=body, headers=headers, method="POST")
    else:
        req = urllib.request.Request(TARGET + "/api/admin/stats", headers=headers)
    t0 = time.time(); failed = False
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
    except urllib.error.HTTPError:
        failed = True
    except Exception:
        failed = True
    dt = time.time() - t0
    with lock:
        for tbl in (stats, window):
            s = tbl[scenario]; s["n"] += 1; s["lat"] += dt
            s["err"] += failed; s["slow"] += dt > 1.0; s["inj_err"] += injected is not None
    return failed


def summary(tbl, secs, label):
    n = sum(s["n"] for s in tbl.values()); e = sum(s["err"] for s in tbl.values())
    inj = sum(s["inj_err"] for s in tbl.values()); sl = sum(s["slow"] for s in tbl.values())
    lat = sum(s["lat"] for s in tbl.values())
    per = " ".join(f"{k}:{v['n']}/{v['err']}e" for k, v in tbl.items())
    print(f"[{label}] profile={state['profile']} rps={n / max(secs, 1e-9):.1f} n={n} err={e} ({100 * e / max(n, 1):.1f}%) "
          f"injected={inj} ({100 * inj / max(n, 1):.1f}%) slow>1s={sl} avg={1000 * lat / max(n, 1):.0f}ms | {per}", flush=True)


class Control(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if urlparse(self.path).path == "/status":
            with lock:
                snap = {k: dict(v) for k, v in stats.items()}
            rps, er, done = current()
            return self._send(200, {"state": state, "effective_rps": rps, "effective_error_rate": er, "done": done, "totals": snap})
        self._send(200, {"usage": "GET /status | POST /control?profile=steady|ramp|spike|soak|error-storm&rps=&error_rate=&slow_rate=&duration="})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/control":
            return self._send(404, {"error": "not found"})
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if "profile" in q:
            if q["profile"] not in PROFILES:
                return self._send(400, {"error": "profile must be one of " + ", ".join(PROFILES)})
            state["profile"] = q["profile"]
        for k in ("rps", "error_rate", "slow_rate", "duration"):
            if k in q:
                state[k] = float(q[k])
        state["started"] = time.time()
        print("[control] ->", {k: state[k] for k in ("profile", "rps", "error_rate", "slow_rate", "duration")}, flush=True)
        self._send(200, state)


def main():
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("CONTROL_PORT", "9000"))), Control).serve_forever, daemon=True).start()
    pool = ThreadPoolExecutor(max_workers=64)
    names, weights = list(MIX), list(MIX.values())
    print(f"loadgen target={TARGET} {state}", flush=True)
    last = time.time(); carry = 0.0
    while True:
        rps, er, done = current()
        now = time.time()
        if done:
            summary(stats, now - state["started"], "final"); print("duration reached; idle (POST /control to start again)", flush=True)
            while current()[2]:
                time.sleep(1)
            continue
        carry += rps * 0.05
        while carry >= 1:
            carry -= 1
            pool.submit(one, random.choices(names, weights)[0], er)
        time.sleep(0.05)
        if now - last >= 10:
            with lock:
                snap = {k: dict(v) for k, v in window.items()}
                for v in window.values():
                    v.update(n=0, err=0, slow=0, inj_err=0, lat=0.0)
            summary(snap, now - last, "10s"); last = now


if __name__ == "__main__":
    main()
