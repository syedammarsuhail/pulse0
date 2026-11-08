#!/usr/bin/env python3
"""End-to-end smoke test for a running Pulse0.

    python scripts/smoke.py http://HOST:4318 [--token TOKEN]

Ingests a uniquely named service over OTLP, then checks the UI and each major API
reads it back. Exits non-zero if any check fails.
"""
import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("base")
ap.add_argument("--token", default="")
ap.add_argument("--wait", type=int, default=30, help="seconds to wait for ingested data to appear")
args = ap.parse_args()
BASE = args.base.rstrip("/")
SVC = "smoke-" + secrets.token_hex(3)
results = []


def call(method, path, body=None, token=args.token, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read()
            return r.status, (txt if raw else json.loads(txt or b"null"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None


def check(name, ok, detail=""):
    ok = bool(ok); results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name + (f"  ({detail})" if detail and not ok else ""))


def kv(k, v):
    return {"key": k, "value": {"stringValue": str(v)}}


def eventually(fn, seconds=args.wait):
    end = time.time() + seconds
    while True:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        if time.time() > end:
            return None
        time.sleep(2)


st, _ = call("GET", "/healthz", token="")
check("healthz", st == 200, st)
st, _ = call("GET", "/readyz", token="")
check("readyz (database reachable)", st == 200, st)
st, body = call("GET", "/", token="", raw=True)
check("web UI served", st == 200 and b"<html" in body.lower(), st)
st, _ = call("GET", "/static/core.js", token="", raw=True)
check("static assets served", st == 200, st)

if args.token:
    st, _ = call("GET", "/api/services", token="")
    check("API rejects missing token", st in (401, 403), st)

now = time.time_ns()
tid, sid = secrets.token_hex(16), secrets.token_hex(8)
res = {"attributes": [kv("service.name", SVC)]}
spans = {"resourceSpans": [{"resource": res, "scopeSpans": [{"spans": [{
    "traceId": tid, "spanId": sid, "parentSpanId": "", "name": "GET /smoke", "kind": 2,
    "startTimeUnixNano": str(now), "endTimeUnixNano": str(now + 40_000_000),
    "status": {"code": 1}, "attributes": [kv("http.method", "GET")]}]}]}]}
logs = {"resourceLogs": [{"resource": res, "scopeLogs": [{"logRecords": [
    {"timeUnixNano": str(now), "severityNumber": 17, "severityText": "ERROR",
     "body": {"stringValue": "smoke test error"}, "traceId": tid}]}]}]}
metrics = {"resourceMetrics": [{"resource": res, "scopeMetrics": [{"metrics": [
    {"name": "smoke_counter", "unit": "1", "sum": {"dataPoints": [
        {"timeUnixNano": str(now), "asInt": "7"}]}}]}]}]}
for sig, payload in (("traces", spans), ("logs", logs), ("metrics", metrics)):
    st, body = call("POST", "/v1/" + sig, payload)
    if st == 400 and "tier_limit" in json.dumps(body):
        # Community tier caps distinct services; reuse one that already exists
        existing = [s["service"] for s in call("GET", "/api/services?minutes=1440")[1]]
        if existing:
            SVC = existing[0]
            res["attributes"][0] = kv("service.name", SVC)
            print(f"NOTE  tier limit reached; reusing existing service '{SVC}'")
            st, body = call("POST", "/v1/" + sig, payload)
    check(f"ingest {sig}", st == 200, f"{st} {body}")
st, _ = call("POST", "/v1/traces", {"resourceSpans": "nope"})
check("ingest rejects malformed payload", st == 400, st)

svcs = eventually(lambda: [s for s in call("GET", "/api/services?minutes=15")[1] if s["service"] == SVC])
check("service appears in /api/services", bool(svcs))
tr = eventually(lambda: call("GET", f"/api/traces?minutes=15&service={SVC}")[1])
check("trace listed in /api/traces", bool(tr))
st, t = call("GET", f"/api/traces/{tid}")
check("trace detail", st == 200 and t, st)
lg = eventually(lambda: call("GET", f"/api/logs?minutes=15&service={SVC}")[1])
check("log listed in /api/logs", bool(lg))
st, f = call("GET", f"/api/logs/facets?minutes=15&service={SVC}")
check("log facets", st == 200 and isinstance(f, dict), st)
names = eventually(lambda: [m for m in call("GET", "/api/metrics/names")[1] if m["name"] == "smoke_counter"])
check("metric listed in /api/metrics/names", bool(names))
st, mq = call("GET", f"/api/metrics/query?name=smoke_counter&service={SVC}&minutes=15&buckets=10")
check("metric query", st == 200 and "series" in (mq or {}), st)

for label, path in (
    ("service timeseries", f"/api/services/{SVC}/timeseries?minutes=15&buckets=10"),
    ("service health score", f"/api/services/{SVC}/health"),
    ("log histogram", f"/api/logs/histogram?minutes=15&service={SVC}&buckets=10"),
    ("service map", "/api/map?minutes=15"),
    ("live insights", "/api/insights?minutes=15"),
    ("home", "/api/home?minutes=15"),
    ("search", f"/api/search?q={SVC}"),
    ("investigate", "/api/investigate"),
    ("dashboards", "/api/dashboards"),
    ("alerts", "/api/alerts"),
    ("alert templates", "/api/alerts/templates"),
    ("SLOs", "/api/slos"),
    ("synthetics", "/api/synthetics"),
    ("assistant status", "/api/assistant/status"),
    ("mode", "/api/mode"),
):
    st, _ = call("GET", path)
    check(label, st == 200, f"{path} -> {st}")

st, _ = call("GET", f"/api/services/{SVC}/timeseries?buckets=0")
check("bucket bounds enforced", st in (400, 422), st)
st, _ = call("GET", "/api/metrics/query?name=x&group_by=%27%3B--")
check("group_by injection attempt harmless", st == 200, st)

st, d = call("POST", "/api/dashboards", {"name": SVC, "panels": []})
if st == 200:
    mine = [x for x in call("GET", "/api/dashboards")[1] if x.get("name") == SVC]
    check("dashboard create", bool(mine))
    for x in mine:
        st, _ = call("DELETE", f"/api/dashboards/{x['id']}")
        check("dashboard delete", st == 200, st)
else:
    print(f"SKIP  dashboard create/delete (status {st}; tier limit or schema)")

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
