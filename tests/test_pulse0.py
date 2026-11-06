"""Comprehensive Pulse0 test suite.

Runs the server in-process via httpx.ASGITransport (no port needed) and verifies:
  - OTLP ingest (traces/logs/metrics, JSON + protobuf, gzip)
  - Service discovery, RED metrics, service map, operations
  - Trace waterfall + correlated logs
  - Log search with attribute key=value filters
  - Metric query engine (agg + group-by)
  - Alert rule validation (good + bad payloads)
  - Alert firing on error_rate threshold
  - Synthetic check creation + execution
  - Health endpoints and request-id propagation
  - Dashboards (CRUD)
  - SLOs (create, attainment calc, delete)
  - Service Health Score (unique Pulse0 feature)

Run:  pytest -xvs tests/
"""
import os, sys, json, time, secrets, random, importlib
import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["PULSE0_DB"] = "/tmp/pulse0_test.db"
if os.path.exists("/tmp/pulse0_test.db"):
    os.remove("/tmp/pulse0_test.db")
for sfx in ("-shm", "-wal"):
    try: os.remove("/tmp/pulse0_test.db" + sfx)
    except FileNotFoundError: pass

import server  # noqa: E402

transport = httpx.ASGITransport(app=server.app)
client = httpx.AsyncClient(transport=transport, base_url="http://test")


# ---------- helpers
def _tid(): return secrets.token_hex(16)
def _sid(): return secrets.token_hex(8)
def _kv(k, v):
    if isinstance(v, bool): return {"key": k, "value": {"boolValue": v}}
    if isinstance(v, int): return {"key": k, "value": {"intValue": v}}
    if isinstance(v, float): return {"key": k, "value": {"doubleValue": v}}
    return {"key": k, "value": {"stringValue": str(v)}}


def _resource(svc, **attrs):
    return {"attributes": [_kv("service.name", svc)] + [_kv(k, v) for k, v in attrs.items()]}


def _span(tid, sid, parent, name, kind=2, dur_ms=50, error=False, **attrs):
    now = time.time_ns()
    return {"traceId": tid, "spanId": sid, "parentSpanId": parent, "name": name,
            "kind": kind, "startTimeUnixNano": str(now),
            "endTimeUnixNano": str(now + int(dur_ms * 1e6)),
            "status": {"code": 2 if error else 1},
            "attributes": [_kv(k, v) for k, v in attrs.items()]}


@pytest.fixture(scope="module", autouse=True)
def seed_data():
    """Push a realistic dataset once for all tests."""
    import anyio

    async def go():
        # 100 traces across two services, 15% errors
        rs = []
        for _ in range(100):
            t = _tid(); s1 = _sid(); s2 = _sid()
            err = random.random() < 0.15
            rs.append({"resource": _resource("api", **{"service.version": "1.0.0"}),
                       "scopeSpans": [{"spans": [_span(t, s1, "", "GET /checkout", 2, random.uniform(20, 200), err,
                                                       **{"http.method": "GET", "http.status_code": 500 if err else 200})]}]})
            rs.append({"resource": _resource("db", **{"service.version": "2.0.0"}),
                       "scopeSpans": [{"spans": [_span(t, s2, s1, "SELECT orders", 2, random.uniform(5, 40), err,
                                                       **{"db.system": "postgres", "peer.service": "orders-db"})]}]})
        r = await client.post("/v1/traces", json={"resourceSpans": rs})
        assert r.status_code == 200
        # logs
        now = time.time_ns()
        logs = {"resourceLogs": [{"resource": _resource("api"), "scopeLogs": [{"logRecords": [
            {"timeUnixNano": str(now), "severityNumber": 9, "severityText": "INFO",
             "body": {"stringValue": "request completed"},
             "attributes": [_kv("http.status_code", 200)]},
            {"timeUnixNano": str(now), "severityNumber": 17, "severityText": "ERROR",
             "body": {"stringValue": "database connection refused"},
             "attributes": [_kv("http.status_code", 500)]},
            {"timeUnixNano": str(now), "severityNumber": 13, "severityText": "WARN",
             "body": {"stringValue": "slow query detected"}},
        ]}]}]}
        r = await client.post("/v1/logs", json=logs)
        assert r.status_code == 200
        # metrics
        metrics = {"resourceMetrics": [{"resource": _resource("api"), "scopeMetrics": [{"metrics": [
            {"name": "orders_placed", "unit": "1", "sum": {"dataPoints": [
                {"timeUnixNano": str(now - int(i * 60e9)), "asInt": str(random.randint(1, 20))} for i in range(20)]}},
        ]}]}]}
        r = await client.post("/v1/metrics", json=metrics)
        assert r.status_code == 200

    anyio.run(go)
    yield


# ---------- health + request IDs
@pytest.mark.anyio
async def test_health_endpoints():
    r = await client.get("/healthz"); assert r.status_code == 200 and r.json()["status"] == "ok"
    r = await client.get("/readyz");  assert r.status_code == 200 and r.json()["status"] == "ready"
    r = await client.get("/api/stats"); assert r.json()["spans"] >= 200


@pytest.mark.anyio
async def test_request_id_header():
    r = await client.get("/healthz", headers={"x-request-id": "testreq123"})
    assert r.headers["x-request-id"] == "testreq123"
    r = await client.get("/healthz")
    assert len(r.headers.get("x-request-id", "")) == 12


@pytest.mark.anyio
async def test_container_logs_without_service_name_use_container_name():
    res = {"attributes": [_kv("k8s.container.name", "filelog-box")]}
    body = {"resourceLogs": [{"resource": res, "scopeLogs": [{"logRecords": [
        {"timeUnixNano": str(time.time_ns()), "body": {"stringValue": "from a pod log file"}}]}]}]}
    assert (await client.post("/v1/logs", json=body)).status_code == 200
    rows = (await client.get("/api/logs", params={"q": "from a pod log file"})).json()
    assert rows and rows[0]["service"] == "filelog-box"


# ---------- ingest validation
@pytest.mark.anyio
async def test_otlp_rejects_malformed():
    r = await client.post("/v1/traces", content=b"not json",
                          headers={"content-type": "application/json"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "http_error"
    assert "request_id" in body


@pytest.mark.anyio
async def test_otlp_handles_gzip():
    import gzip
    payload = json.dumps({"resourceSpans": []}).encode()
    r = await client.post("/v1/traces", content=gzip.compress(payload),
                          headers={"content-type": "application/json", "content-encoding": "gzip"})
    assert r.status_code == 200


# ---------- resources + RED
@pytest.mark.anyio
async def test_services_red_metrics():
    r = await client.get("/api/services?minutes=60")
    assert r.status_code == 200
    svcs = {s["service"]: s for s in r.json()}
    assert "api" in svcs and "db" in svcs
    assert svcs["api"]["n"] == 100
    assert 5 < svcs["api"]["err_pct"] < 30
    assert svcs["api"]["p95"] > 0


@pytest.mark.anyio
async def test_service_timeseries():
    r = await client.get("/api/services/api/timeseries?minutes=60&buckets=10")
    assert r.status_code == 200
    ts = r.json()
    assert len(ts) == 10
    assert all("rate" in p and "err" in p for p in ts)


@pytest.mark.anyio
async def test_operations():
    r = await client.get("/api/operations?service=api&minutes=60")
    ops = r.json()
    assert any(o["name"] == "GET /checkout" for o in ops)


# ---------- map
@pytest.mark.anyio
async def test_service_map_edges():
    r = await client.get("/api/map?minutes=60")
    edges = r.json()
    assert any(e["src"] == "api" and e["dst"] == "db" for e in edges)


# ---------- traces
@pytest.mark.anyio
async def test_trace_search_and_detail():
    r = await client.get("/api/traces?minutes=60"); traces = r.json()
    assert len(traces) == 100
    r = await client.get(f"/api/traces/{traces[0]['trace_id']}")
    assert r.status_code == 200
    detail = r.json()
    assert len(detail["spans"]) == 2
    assert {s["service"] for s in detail["spans"]} == {"api", "db"}


@pytest.mark.anyio
async def test_trace_filters():
    r = await client.get("/api/traces?minutes=60&errors=true")
    err_traces = r.json()
    assert all(t["errors"] > 0 for t in err_traces)
    r = await client.get("/api/traces/does-not-exist")
    assert r.status_code == 404


# ---------- logs
@pytest.mark.anyio
async def test_log_search():
    r = await client.get("/api/logs?minutes=60"); assert len(r.json()) >= 3
    r = await client.get("/api/logs?minutes=60&min_severity=17")
    errs = r.json(); assert all(l["severity"] >= 17 for l in errs)
    # attribute filter
    r = await client.get("/api/logs?minutes=60&q_=http.status_code=500")
    assert len(r.json()) >= 1


# ---------- metrics
@pytest.mark.anyio
async def test_metrics_discovery_and_query():
    r = await client.get("/api/metrics/names")
    assert any(m["name"] == "orders_placed" for m in r.json())
    r = await client.get("/api/metrics/query?name=orders_placed&minutes=60&buckets=20&agg=sum")
    d = r.json()
    assert len(d["t"]) == 20
    assert any(any(v is not None for v in s) for s in d["series"].values())


# ---------- alert validation
@pytest.mark.anyio
async def test_alert_create_valid():
    r = await client.post("/api/alerts", json={
        "name": "test-high-errors", "signal": "error_rate", "op": ">",
        "threshold": 5, "window_s": 300, "service": "api"})
    assert r.status_code == 200


@pytest.mark.anyio
async def test_alert_rejects_unknown_signal():
    r = await client.post("/api/alerts", json={
        "name": "bad", "signal": "not_a_signal", "op": ">", "threshold": 1})
    assert r.status_code == 400
    assert r.json()["error"] == "validation_failed"


@pytest.mark.anyio
async def test_alert_rejects_unknown_op():
    r = await client.post("/api/alerts", json={
        "name": "bad", "signal": "error_rate", "op": "==", "threshold": 1})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_alert_rejects_missing_fields():
    r = await client.post("/api/alerts", json={"name": "incomplete"})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_alert_rejects_bad_webhook():
    r = await client.post("/api/alerts", json={
        "name": "bad-webhook", "signal": "error_rate", "op": ">",
        "threshold": 1, "webhook": "not-a-url"})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_alert_rejects_extra_fields():
    r = await client.post("/api/alerts", json={
        "name": "extras", "signal": "error_rate", "op": ">",
        "threshold": 1, "sneaky_field": "nope"})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_alert_delete_404():
    r = await client.delete("/api/alerts/999999")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_alert_fires_on_threshold():
    # evaluate and read back
    server.evaluate_rules()
    r = await client.get("/api/alerts")
    rules = r.json()
    rule = next(x for x in rules if x["name"] == "test-high-errors")
    # 15% error rate seeded, threshold 5 -> should fire
    assert rule["last_value"] is not None and rule["last_value"] > 5
    assert rule["state"] == "firing"


# ---------- synthetics validation
@pytest.mark.anyio
async def test_synthetic_create_and_run():
    r = await client.post("/api/synthetics", json={"name": "self-check",
                                                    "url": "http://127.0.0.1:9", "interval_s": 60})
    assert r.status_code == 200
    r = await client.get("/api/synthetics"); syns = r.json()
    assert any(s["name"] == "self-check" for s in syns)


@pytest.mark.anyio
async def test_synthetic_rejects_bad_url():
    r = await client.post("/api/synthetics", json={"name": "x", "url": "not-a-url"})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_synthetic_rejects_bad_interval():
    r = await client.post("/api/synthetics", json={
        "name": "too-fast", "url": "https://example.com", "interval_s": 1})
    assert r.status_code == 400  # below min 10


# ---------- dashboards (new feature)
@pytest.mark.anyio
async def test_dashboard_crud():
    panels = [{"title": "API rate", "metric": "orders_placed", "agg": "sum"}]
    r = await client.post("/api/dashboards", json={"name": "api-overview", "panels": panels})
    assert r.status_code == 200
    r = await client.get("/api/dashboards")
    ds = r.json()
    d = next(x for x in ds if x["name"] == "api-overview")
    assert d["panels"] == panels
    # upsert
    r = await client.post("/api/dashboards", json={"name": "api-overview", "panels": []})
    assert r.status_code == 200
    r = await client.delete(f"/api/dashboards/{d['id']}")
    assert r.status_code == 200
    r = await client.delete(f"/api/dashboards/{d['id']}")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_dashboard_validation():
    r = await client.post("/api/dashboards", json={"name": "", "panels": []})
    assert r.status_code == 400
    r = await client.post("/api/dashboards",
                          json={"name": "x", "panels": [{} for _ in range(60)]})
    assert r.status_code == 400  # too many panels


# ---------- SLOs (new feature)
@pytest.mark.anyio
async def test_slo_crud_and_attainment():
    r = await client.post("/api/slos", json={"name": "api-latency",
                                              "service": "api", "objective_pct": 99.0,
                                              "latency_ms": 100, "window_days": 30})
    assert r.status_code == 200
    r = await client.get("/api/slos"); rows = r.json()
    slo = next(x for x in rows if x["name"] == "api-latency")
    assert slo["attainment_pct"] is not None
    assert slo["events"] == 100
    # duplicate
    r = await client.post("/api/slos", json={"name": "api-latency",
                                              "service": "api", "objective_pct": 99.0,
                                              "latency_ms": 100, "window_days": 30})
    assert r.status_code == 409
    r = await client.delete(f"/api/slos/{slo['id']}"); assert r.status_code == 200
    r = await client.delete(f"/api/slos/{slo['id']}"); assert r.status_code == 404


@pytest.mark.anyio
async def test_slo_validation():
    r = await client.post("/api/slos", json={"name": "bad", "service": "api",
                                              "objective_pct": 110, "latency_ms": 100})
    assert r.status_code == 400  # over 100
    r = await client.post("/api/slos", json={"name": "bad", "service": "api",
                                              "objective_pct": 99, "latency_ms": -5})
    assert r.status_code == 400


# ---------- service health score (unique differentiator)
@pytest.mark.anyio
async def test_service_health_score():
    r = await client.get("/api/services/api/health?minutes=60")
    assert r.status_code == 200
    h = r.json()
    assert "score" in h and 0 <= h["score"] <= 100
    assert h["state"] in {"healthy", "degraded", "unhealthy"}
    # 15% errors -> degraded or unhealthy
    assert h["state"] != "healthy"
    # signals surfaced
    assert "signals" in h and "error_pct" in h["signals"]
    # unknown service
    r = await client.get("/api/services/does-not-exist/health")
    assert r.json()["score"] is None


# ---------- Dash0-parity layer: insights, home, investigate, search, templates, facets
@pytest.mark.anyio
async def test_insights_flags_failing_service():
    r = await client.get("/api/insights?minutes=60")
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list) and items
    for i in items:
        assert {"severity", "kind", "service", "title", "detail", "link"} <= set(i)
        assert i["severity"] in {"critical", "warning", "info"}
    assert any(i["kind"] == "error_rate" and i["service"] == "api" for i in items)
    # most severe first
    rank = {"critical": 0, "warning": 1, "info": 2}
    assert [rank[i["severity"]] for i in items] == sorted(rank[i["severity"]] for i in items)


@pytest.mark.anyio
async def test_home_summary():
    r = await client.get("/api/home?minutes=60&buckets=30")
    assert r.status_code == 200
    h = r.json()
    assert h["totals"]["requests"] >= 200 and h["totals"]["services"] >= 2
    assert len(h["timeseries"]) == 30 and {"t", "rate", "err_rate"} <= set(h["timeseries"][0])
    assert h["top_errors"][0]["err_pct"] >= h["top_errors"][-1]["err_pct"]
    assert set(h["insight_counts"]) == {"critical", "warning", "info"}
    assert isinstance(h["events"], list) and isinstance(h["slos"], list)
    assert all(s["n"] > 0 for s in h["top_latency"])


@pytest.mark.anyio
async def test_investigate_intents():
    cases = {"which services are slow?": "slow_services", "what broke?": "what_broke",
             "show error logs": "error_logs", "any cpu saturation": "saturation", "hello": "overview"}
    for text, intent in cases.items():
        r = await client.get("/api/investigate", params={"q_": text})
        assert r.status_code == 200, text
        d = r.json()
        assert d["intent"] == intent and d["summary"] and isinstance(d["findings"], list) and d["suggestions"]
    d = (await client.get("/api/investigate", params={"q_": "what broke"})).json()
    assert d["findings"] and d["findings"][0]["label"] in {"api", "db"}
    assert d["findings"][0]["link"].startswith("#/traces")


@pytest.mark.anyio
async def test_search_palette():
    d = (await client.get("/api/search", params={"q_": "api"})).json()
    assert {"name": "api"} in d["services"]
    d = (await client.get("/api/search", params={"q_": "checkout"})).json()
    assert d["traces"] and "checkout" in d["traces"][0]["name"]
    d = (await client.get("/api/search", params={"q_": "orders_pl"})).json()
    assert {"name": "orders_placed"} in d["metrics"]
    assert (await client.get("/api/search", params={"q_": ""})).json() == {"services": [], "traces": [], "metrics": []}


@pytest.mark.anyio
async def test_alert_templates_are_creatable():
    r = await client.get("/api/alerts/templates")
    assert r.status_code == 200
    tpls = r.json()
    assert len(tpls) >= 12 and len({t["id"] for t in tpls}) == len(tpls)
    t = tpls[0]
    body = {k: t[k] for k in ("name", "signal", "expr", "op", "threshold", "window_s")}
    assert (await client.post("/api/alerts", json={**body, "name": "tpl-test"})).status_code == 200
    for t in tpls:   # every preset must pass rule validation
        body = {k: t[k] for k in ("name", "signal", "expr", "op", "threshold", "window_s")}
        from pydantic import ValidationError
        try:
            server.AlertRuleIn(**body)
        except ValidationError as e:  # pragma: no cover
            raise AssertionError(f"template {t['id']} invalid: {e}")
    rule = [a for a in (await client.get("/api/alerts")).json() if a["name"] == "tpl-test"][0]
    assert (await client.delete(f"/api/alerts/{rule['id']}")).status_code == 200


@pytest.mark.anyio
async def test_log_facets():
    d = (await client.get("/api/logs/facets?minutes=60")).json()
    assert any(x["value"] == "api" for x in d["service"])
    sev = {x["value"]: x["n"] for x in d["severity"]}
    assert sev.get("ERROR", 0) >= 1 and sev.get("WARN", 0) >= 1
    d = (await client.get("/api/logs/facets", params={"minutes": 60, "q_": "database"})).json()
    assert [x["value"] for x in d["severity"]] == ["ERROR"]


@pytest.mark.anyio
async def test_dashboard_panel_roundtrip():
    panels = [{"id": "p1", "type": "line", "title": "Orders", "metric": "orders_placed", "agg": "sum",
               "group_by": "", "service": "api", "w": 6, "h": 3}]
    assert (await client.post("/api/dashboards", json={"name": "builder-test", "panels": panels})).status_code == 200
    d = [x for x in (await client.get("/api/dashboards")).json() if x["name"] == "builder-test"][0]
    assert d["panels"] == panels
    assert (await client.delete(f"/api/dashboards/{d['id']}")).status_code == 200


# ---------- Claude assistant (anthropic client mocked, no network)
from types import SimpleNamespace as NS


def _text(t): return NS(type="text", text=t)
def _tool(i, name, **inp): return NS(type="tool_use", id=i, name=name, input=inp)
def _resp(stop, *blocks): return NS(stop_reason=stop, content=list(blocks))


class FakeAnthropic:
    """Scripted stand-in for anthropic.Anthropic: replays `script` (or raises) and records each call."""
    def __init__(self, script=None, exc=None, loop=None):
        self.script, self.exc, self.loop, self.calls = list(script or []), exc, loop, []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc: raise self.exc
        if self.loop: return self.loop
        return self.script.pop(0)


def _use_ai(monkeypatch, fake, key="sk-ant-test-SECRET"):
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    monkeypatch.setattr(server, "_ai_client", lambda: fake)


ASK = {"messages": [{"role": "user", "content": "which service is failing?"}]}


@pytest.mark.anyio
async def test_assistant_status_disabled_and_enabled(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert (await client.get("/api/assistant/status")).json() == {"enabled": False, "model": None, "mode": "rules"}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.delenv("PULSE0_AI_MODEL", raising=False)
    d = (await client.get("/api/assistant/status")).json()
    assert d == {"enabled": True, "model": "claude-sonnet-5-5", "mode": "claude"}
    monkeypatch.setenv("PULSE0_AI_MODEL", "claude-opus-5-5")
    assert (await client.get("/api/assistant/status")).json()["model"] == "claude-opus-5-5"


@pytest.mark.anyio
async def test_assistant_disabled_points_to_rule_fallback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = await client.post("/api/assistant", json=ASK)
    assert r.status_code == 503
    d = r.json()
    assert d["error"] == "assistant_disabled" and d["mode"] == "rules" and d["fallback"] == "/api/investigate"
    assert (await client.get("/api/investigate", params={"q_": "what broke?"})).status_code == 200


@pytest.mark.anyio
async def test_assistant_tool_loop_runs_tool_and_returns_text(monkeypatch):
    fake = FakeAnthropic([_resp("tool_use", _text("checking"), _tool("t1", "list_services", minutes=60)),
                          _resp("end_turn", _text("The **api** service has errors."))])
    _use_ai(monkeypatch, fake)
    r = await client.post("/api/assistant", json=ASK)
    assert r.status_code == 200
    d = r.json()
    assert d["mode"] == "claude" and d["answer"] == "The **api** service has errors." and d["tools"] == ["list_services"]
    assert "sk-ant-test-SECRET" not in r.text
    assert len(fake.calls) == 2
    sent = fake.calls[1]["messages"]
    assert sent[-1]["role"] == "user" and sent[-1]["content"][0]["type"] == "tool_result"
    assert sent[-1]["content"][0]["tool_use_id"] == "t1" and '"api"' in sent[-1]["content"][0]["content"]
    assert {t["name"] for t in fake.calls[0]["tools"]} >= {"list_services", "get_trace", "search_logs", "metric_query"}
    assert fake.calls[0]["model"] == server._ai_model()


@pytest.mark.anyio
async def test_assistant_unknown_tool_is_reported_not_executed(monkeypatch):
    fake = FakeAnthropic([_resp("tool_use", _tool("t1", "delete_everything")), _resp("end_turn", _text("done"))])
    _use_ai(monkeypatch, fake)
    d = (await client.post("/api/assistant", json=ASK)).json()
    assert d["answer"] == "done"
    res = fake.calls[1]["messages"][-1]["content"][0]
    assert res.get("is_error") is True and "unknown tool" in res["content"]


@pytest.mark.anyio
async def test_assistant_api_error_returns_502_without_leaking_key(monkeypatch):
    _use_ai(monkeypatch, FakeAnthropic(exc=RuntimeError("401 bad key sk-ant-test-SECRET")))
    r = await client.post("/api/assistant", json=ASK)
    assert r.status_code == 502
    d = r.json()
    assert d["error"] == "assistant_error" and d["fallback"] == "/api/investigate" and d["mode"] == "rules"
    assert "sk-ant-test-SECRET" not in r.text and "***" in d["message"]


@pytest.mark.anyio
async def test_assistant_tool_loop_is_capped(monkeypatch):
    fake = FakeAnthropic(loop=_resp("tool_use", _tool("t1", "list_slos")))
    _use_ai(monkeypatch, fake)
    r = await client.post("/api/assistant", json=ASK)
    assert r.status_code == 502 and "tool rounds" in r.json()["message"]
    assert len(fake.calls) == server.AI_MAX_ITERATIONS


@pytest.mark.anyio
async def test_assistant_streams_events(monkeypatch):
    fake = FakeAnthropic([_resp("tool_use", _tool("t1", "list_insights")), _resp("end_turn", _text("All quiet. " * 10))])
    _use_ai(monkeypatch, fake)
    r = await client.post("/api/assistant", json={**ASK, "stream": True})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "tool" and "tool_result" in kinds and kinds[-1] == "done" and kinds.count("text") > 1
    assert "".join(e["text"] for e in evs if e["type"] == "text") == "All quiet. " * 10
    assert evs[-1]["tools"] == ["list_insights"]


@pytest.mark.anyio
async def test_assistant_validation(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    bad = [{"messages": []},
           {"messages": [{"role": "assistant", "content": "hi"}]},
           {"messages": [{"role": "system", "content": "x"}]},
           {"messages": [{"role": "user", "content": ""}]},
           {"messages": [{"role": "user", "content": "x"}], "extra": 1}]
    for body in bad:
        assert (await client.post("/api/assistant", json=body)).status_code == 400, body


@pytest.mark.anyio
async def test_assistant_requires_auth_when_token_set(monkeypatch):
    monkeypatch.setattr(server, "AUTH_TOKEN", "s3cret")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert (await client.get("/api/assistant/status")).status_code == 401
    assert (await client.post("/api/assistant", json=ASK)).status_code == 401
    h = {"Authorization": "Bearer s3cret"}
    assert (await client.get("/api/assistant/status", headers=h)).status_code == 200
    assert (await client.post("/api/assistant", json=ASK, headers=h)).status_code == 503


# ---------- service map
@pytest.mark.anyio
async def test_map_nodes_metadata():
    t, a, b, c = _tid(), _sid(), _sid(), _sid()
    r = await client.post("/v1/traces", json={"resourceSpans": [
        {"resource": _resource("map-web", **{"telemetry.sdk.language": "nodejs", "k8s.namespace.name": "shop", "service.version": "3.1"}),
         "scopeSpans": [{"spans": [_span(t, a, "", "GET /", 2), _span(t, b, a, "HTTP GET", 3)]}]},
        {"resource": _resource("map-api", **{"telemetry.sdk.language": "go"}),
         "scopeSpans": [{"spans": [_span(t, c, b, "GET /api", 2)]}]}]})
    assert r.status_code == 200
    r = await client.get("/api/map/nodes?minutes=60")
    assert r.status_code == 200
    by = {n["service"]: n for n in r.json()}
    assert (by["map-web"]["language"], by["map-web"]["namespace"], by["map-web"]["version"]) == ("nodejs", "shop", "3.1")
    assert (by["map-api"]["language"], by["map-api"]["namespace"]) == ("go", "")
    edges = (await client.get("/api/map?minutes=60")).json()
    assert any(e["src"] == "map-web" and e["dst"] == "map-api" for e in edges)


@pytest.mark.anyio
async def test_map_endpoints_stay_fast_on_a_big_graph():
    """Regression guard for the service-map freeze report: a 60-service / ~150-edge graph (with cycles) must
    be served quickly by both map endpoints. The browser-side layout is covered by tests/test_mapkit.js."""
    names = [f"big-{i:02d}" for i in range(60)]
    pairs = {(names[random.randrange(60)], names[random.randrange(60)]) for _ in range(180)}
    pairs = [p for p in pairs if p[0] != p[1]][:150] + [(names[1], names[0]), (names[0], names[1])]   # cycle
    by_svc = {}
    for src, dst in pairs:
        t, a, b, c = _tid(), _sid(), _sid(), _sid()
        by_svc.setdefault(src, []).extend([_span(t, a, "", "GET /x", 2), _span(t, b, a, "HTTP GET", 3)])
        by_svc.setdefault(dst, []).append(_span(t, c, b, "GET /y", 2, error=random.random() < .1))
    body = {"resourceSpans": [{"resource": _resource(s), "scopeSpans": [{"spans": sp}]} for s, sp in by_svc.items()]}
    assert (await client.post("/v1/traces", json=body)).status_code == 200
    for path in ("/api/map?minutes=60", "/api/map/nodes?minutes=60"):
        t0 = time.time(); r = await client.get(path)
        assert r.status_code == 200 and time.time() - t0 < 5, path
    assert len((await client.get("/api/map?minutes=60")).json()) >= 100


@pytest.mark.anyio
async def test_map_layout_kit_js():
    """Run the Node unit tests for the layout engine when Node is available."""
    import shutil, subprocess
    node = shutil.which("node")
    if not node: pytest.skip("node not installed")
    root = os.path.dirname(os.path.dirname(__file__))
    r = subprocess.run([node, os.path.join(root, "tests", "test_mapkit.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


# ---------- pytest-anyio config
@pytest.fixture
def anyio_backend(): return "asyncio"


@pytest.mark.anyio
async def test_cors_echoes_origin_for_credentialed_beacon():
    r = await client.options("/v1/traces", headers={"Origin": "http://app.example", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") == "http://app.example"
    assert r.headers.get("access-control-allow-credentials") == "true"


@pytest.mark.anyio
async def test_slo_budget_matches_attainment():
    """A breached SLO must show more than 100% of its error budget used (and a met one less than 100%)."""
    r = await client.post("/api/slos", json={"name": "qa-budget-breach", "service": "api", "objective_pct": 99.99,
                                              "latency_ms": 1, "window_days": 1})
    assert r.status_code in (200, 201)
    rows = (await client.get("/api/slos")).json()
    slo = next(x for x in rows if x["name"] == "qa-budget-breach")
    assert slo["attainment_pct"] is not None and slo["attainment_pct"] < slo["objective_pct"]
    assert slo["error_budget_used_pct"] > 100
    expected = (100 - slo["attainment_pct"]) / (100 - slo["objective_pct"]) * 100
    assert abs(slo["error_budget_used_pct"] - expected) < 0.2
    r = await client.post("/api/slos", json={"name": "qa-budget-ok", "service": "api", "objective_pct": 50,
                                              "latency_ms": 60000, "window_days": 1})
    ok = next(x for x in (await client.get("/api/slos")).json() if x["name"] == "qa-budget-ok")
    assert ok["attainment_pct"] >= ok["objective_pct"] and ok["error_budget_used_pct"] < 100
    for x in (slo, ok):
        await client.delete(f"/api/slos/{x['id']}")


# ---------- static UI regression guards (no browser available in CI)
def _static(name):
    with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", name), encoding="utf-8") as f:
        return f.read()


def test_ui_panel_form_does_not_reuse_modal_footer_class():
    # `.modal .mf` is the modal footer (padding, border-top, flex); reusing "mf" for form fields broke the layout.
    assert '"mf"' not in _static("views2.js")


def test_ui_chart_breaks_lines_at_missing_data():
    # null buckets must not be plotted as zero (Y(v || 0)) in line/area charts.
    core = _static("core.js")
    assert "Y(v || 0)" not in core and "runs.forEach" in core


def test_ui_ai_command_is_self_contained_for_reuse_values():
    # `helm upgrade --reuse-values` does not pick up new chart defaults, so the command must pass the secret name.
    v2 = _static("views2.js")
    assert "--set ai.existingSecret=pulse0-anthropic" in v2 and "--set ai.secretKey=api-key" in v2


def test_ui_rail_expands_on_hover_or_keyboard_only_and_has_mobile_bar():
    css = _static("app.css")
    assert "focus-within" not in css  # a clicked link must not keep the rail expanded
    assert "#rail:has(a:focus-visible)" in css and "padding-bottom:52px" in css  # bottom nav bar on phones
