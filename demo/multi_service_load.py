"""22-service simulated microservice platform that sends OTLP/HTTP to Pulse0.

Not real apps: this emits the same /v1/traces, /v1/logs and /v1/metrics payloads a real
OpenTelemetry SDK would, so every Pulse0 page (map, RED, traces, logs, alerts, SLOs) has
realistic multi-hop data. Deliberate faults make the issues findable:

  payments-service     ~6% errors (card-provider failures)
  fraud-service        slow (p95 > 400 ms)
  search-service       errors that grow in the second half of every 10 minutes
  inventory-service    periodic latency spikes
  email-worker         error bursts every few minutes
  elasticsearch        slow queries

Env: PULSE0_URL (default http://pulse0.observability:4318), PULSE0_TOKEN, BATCH (traces per tick), PERIOD (seconds).
Stdlib only, so it runs in a bare python image.
"""
import json, os, random, time, urllib.request, uuid

URL = os.environ.get("PULSE0_URL", "http://pulse0.observability:4318").rstrip("/")
TOKEN = os.environ.get("PULSE0_TOKEN", "")
BATCH = int(os.environ.get("BATCH", "30"))
PERIOD = float(os.environ.get("PERIOD", "5"))

# name: (team, language, base error rate)
SERVICES = {
    "storefront": ("web", "browser-js", 0.010), "mobile-bff": ("web", "kotlin", 0.012),
    "api-gateway": ("platform", "nodejs", 0.010), "auth-service": ("identity", "go", 0.015),
    "user-service": ("identity", "java", 0.010), "catalog-service": ("catalog", "python", 0.010),
    "search-service": ("catalog", "java", 0.020), "review-service": ("catalog", "python", 0.010),
    "pricing-service": ("catalog", "go", 0.008), "recommendations-service": ("ml", "python", 0.025),
    "cart-service": ("checkout", "nodejs", 0.015), "orders-service": ("checkout", "java", 0.020),
    "payments-service": ("payments", "dotnet", 0.060), "fraud-service": ("payments", "python", 0.020),
    "tax-service": ("payments", "go", 0.008), "inventory-service": ("fulfil", "go", 0.020),
    "shipping-service": ("fulfil", "python", 0.040), "notifications-service": ("growth", "nodejs", 0.010),
    "email-worker": ("growth", "python", 0.010), "analytics-service": ("data", "scala", 0.008),
    "postgres": ("data", "postgresql", 0.004), "redis": ("data", "redis", 0.003),
    "elasticsearch": ("data", "java", 0.010),
}

# flows: (weight, tree)  tree = (service, span name, base_ms, [children])
FLOWS = [
    (30, ("storefront", "page /", 50, [("api-gateway", "GET /products", 30, [
        ("catalog-service", "ListProducts", 25, [("postgres", "SELECT products", 10, []),
                                                  ("redis", "GET catalog:cache", 3, [])]),
        ("pricing-service", "GetPrices", 18, [("redis", "GET price:{sku}", 3, [])]),
        ("recommendations-service", "Recommend", 35, [("redis", "GET recs:{user}", 3, [])]),
        ("review-service", "TopReviews", 22, [("postgres", "SELECT reviews", 9, [])])])])),
    (15, ("storefront", "search", 45, [("api-gateway", "GET /search", 30, [
        ("search-service", "Query", 45, [("elasticsearch", "search products", 60, [])]),
        ("recommendations-service", "Similar", 30, [])])])),
    (8, ("mobile-bff", "GET /home", 40, [("api-gateway", "GET /products", 28, [
        ("catalog-service", "ListProducts", 25, [("postgres", "SELECT products", 10, [])]),
        ("recommendations-service", "Recommend", 35, [])])])),
    (10, ("storefront", "login", 40, [("api-gateway", "POST /login", 28, [
        ("auth-service", "Verify", 25, [("postgres", "SELECT users", 9, []), ("redis", "SET session", 3, [])]),
        ("user-service", "GetProfile", 20, [("postgres", "SELECT profile", 9, [])])])])),
    (22, ("storefront", "checkout", 60, [("api-gateway", "POST /checkout", 45, [
        ("cart-service", "GetCart", 18, [("redis", "GET cart:{id}", 3, [])]),
        ("orders-service", "PlaceOrder", 50, [
            ("fraud-service", "Score", 90, [("redis", "GET risk:{id}", 3, [])]),
            ("tax-service", "Calculate", 15, []),
            ("payments-service", "Charge", 70, []),
            ("inventory-service", "Reserve", 30, [("postgres", "UPDATE stock", 14, [])]),
            ("shipping-service", "CreateShipment", 40, []),
            ("notifications-service", "Enqueue", 20, [("email-worker", "SendEmail", 35, [])]),
            ("analytics-service", "TrackOrder", 15, [])])])])),
    (5, ("storefront", "event", 20, [("api-gateway", "POST /events", 15, [
        ("analytics-service", "Ingest", 20, [("postgres", "INSERT events", 8, [])])])])),
]


def rid(n): return uuid.uuid4().hex[: n * 2]
def attrs(d):
    return [{"key": k, "value": ({"intValue": str(v)} if isinstance(v, int) and not isinstance(v, bool)
                                 else {"stringValue": str(v)})} for k, v in d.items()]
RES = {n: {"attributes": attrs({"service.name": n, "service.version": "1.%d.0" % (i % 9 + 1),
                                "deployment.environment": "prod", "team": t,
                                "telemetry.sdk.language": lang, "host.name": f"{n}-{i % 3 + 1}"})}
       for i, (n, (t, lang, _)) in enumerate(SERVICES.items())}


def error_rate(svc, now_s):
    rate = SERVICES[svc][2]
    if svc == "search-service" and (now_s % 600) > 300:
        rate = 0.22
    if svc == "email-worker" and (now_s % 240) < 40:
        rate = 0.45
    return rate


def latency(svc, ms, now_s):
    ms *= random.uniform(0.6, 1.7)
    if svc == "fraud-service": ms *= random.choice([1, 1, 2, 4])
    if svc == "elasticsearch": ms *= random.choice([1, 1, 1, 3])
    if svc == "inventory-service" and (now_s % 300) < 45: ms *= 6
    return ms


def build(tree, now_ns, now_s, parent, t, spans, logs, start):
    svc, name, base, kids = tree
    dur_ms = latency(svc, base, now_s)
    sid = rid(8)
    err = random.random() < error_rate(svc, now_s)
    attributes = {"rpc.system": SERVICES[svc][1]}
    if svc in ("postgres",): attributes["db.system"] = "postgresql"
    if svc == "redis": attributes["db.system"] = "redis"
    if svc == "elasticsearch": attributes["db.system"] = "elasticsearch"
    if svc == "api-gateway":
        attributes.update({"http.route": name.split(" ", 1)[-1], "http.status_code": 500 if err else 200})
    span = {"traceId": t, "spanId": sid, "name": name, "kind": 2,
            "startTimeUnixNano": str(start), "endTimeUnixNano": str(start + int(dur_ms * 1e6)),
            "status": {"code": 2, "message": "error"} if err else {"code": 1},
            "attributes": attrs(attributes)}
    if parent: span["parentSpanId"] = parent
    spans.setdefault(svc, []).append(span)
    if err:
        logs.setdefault(svc, []).append({"timeUnixNano": str(start + int(dur_ms * 1e6) - 100000),
            "severityNumber": 17, "severityText": "ERROR",
            "body": {"stringValue": f"{name} failed in {svc}"}, "traceId": t, "spanId": sid})
    child_start = start + 1_000_000
    for k in kids:
        build(k, now_ns, now_s, sid, t, spans, logs, child_start)
    return err


def post(path, body):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": "Bearer " + TOKEN} if TOKEN else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def tick():
    now_ns = time.time_ns(); now_s = int(now_ns / 1e9)
    spans, logs, errs = {}, {}, 0
    weights = [w for w, _ in FLOWS]
    for _ in range(BATCH):
        tree = random.choices(FLOWS, weights)[0][1]
        start = now_ns - random.randint(0, int(PERIOD * 1e9))
        errs += build(tree, now_ns, now_s, None, rid(16), spans, logs, start)
    post("/v1/traces", {"resourceSpans": [
        {"resource": RES[s], "scopeSpans": [{"scope": {"name": "sim"}, "spans": v}]} for s, v in spans.items()]})
    if logs:
        post("/v1/logs", {"resourceLogs": [
            {"resource": RES[s], "scopeLogs": [{"scope": {"name": "sim"}, "logRecords": v}]} for s, v in logs.items()]})
    orders = len(spans.get("orders-service", []))
    post("/v1/metrics", {"resourceMetrics": [{"resource": RES["orders-service"], "scopeMetrics": [{
        "scope": {"name": "biz"}, "metrics": [{"name": "orders_placed", "unit": "1", "sum": {
            "aggregationTemporality": 1, "isMonotonic": True, "dataPoints": [{
                "startTimeUnixNano": str(now_ns - int(PERIOD * 1e9)), "timeUnixNano": str(now_ns),
                "asInt": str(orders)}]}}]}]}]})
    return sum(len(v) for v in spans.values()), len(logs), errs


if __name__ == "__main__":
    print(f"[sim] {len(SERVICES)} services -> {URL}, {BATCH} traces every {PERIOD}s", flush=True)
    while True:
        try:
            n, _, e = tick()
            print(f"[sim] {n} spans, {e} failed traces", flush=True)
        except Exception as ex:
            print("[sim] error:", ex, flush=True)
        time.sleep(PERIOD)
