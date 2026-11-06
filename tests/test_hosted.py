"""Tests for deployment modes: self-hosted (default, unchanged) and hosted (multi-tenant).

server.py is loaded as separate module instances (own env, own databases) so these tests never touch
the module used by test_pulse0.py. Sync TestClient is used: the hosted auth middleware sets a
contextvar that must reach sync endpoints running in the threadpool, which is exactly what is tested.
"""
import importlib.util, os, secrets, sys, tempfile, time
import pytest

os.environ["PULSE0_CACHE_TTL"] = "0"  # tests read their own writes immediately
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADMIN = "test-admin-token"
_ENV_KEYS = ["PULSE0_MODE", "PULSE0_ADMIN_TOKEN", "PULSE0_DATA_DIR", "PULSE0_DB", "PULSE0_TOKEN",
             "PULSE0_DEFAULT_DAILY_CAP", "PULSE0_AUTH_FAIL_LIMIT", "PULSE0_HOSTED_AI", "PULSE0_RETENTION_HOURS"]


def load(name, **env):
    """Import a fresh copy of server.py with the given environment."""
    old = {k: os.environ.get(k) for k in _ENV_KEYS}
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    d = tempfile.mkdtemp(prefix="p0_" + name + "_")
    os.environ["PULSE0_DB"] = os.path.join(d, "default.db")
    os.environ["PULSE0_DATA_DIR"] = os.path.join(d, "tenants")
    os.environ.update({k: str(v) for k, v in env.items()})
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "server.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    finally:
        for k, v in old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    return mod


def H(token):
    return {"Authorization": f"Bearer {token}"}


def spans(service, n=3, error_every=0):
    now = time.time_ns()
    out = []
    for i in range(n):
        out.append({"traceId": secrets.token_hex(16), "spanId": secrets.token_hex(8), "parentSpanId": "", "name": f"GET /{service}",
                    "kind": 2, "startTimeUnixNano": str(now), "endTimeUnixNano": str(now + 5_000_000),
                    "status": {"code": 2 if error_every and i % error_every == 0 else 1}, "attributes": []})
    return {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
                               "scopeSpans": [{"spans": out}]}]}


def logs(service, msg):
    return {"resourceLogs": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
                              "scopeLogs": [{"logRecords": [{"timeUnixNano": str(time.time_ns()), "severityNumber": 17,
                                                             "severityText": "ERROR", "body": {"stringValue": msg}}]}]}]}


def metrics(service, name):
    return {"resourceMetrics": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
                                 "scopeMetrics": [{"metrics": [{"name": name, "unit": "1", "gauge": {"dataPoints": [
                                     {"timeUnixNano": str(time.time_ns()), "asDouble": 3.5}]}}]}]}]}


@pytest.fixture(scope="module")
def hosted():
    m = load("server_hosted", PULSE0_MODE="hosted", PULSE0_ADMIN_TOKEN=ADMIN)
    return m, TestClient(m.app)


def make_org(c, name, **kw):
    r = c.post("/admin/orgs", json={"name": name, **kw}, headers=H(ADMIN))
    assert r.status_code == 200, r.text
    return r.json()


def make_project(c, org, name, key=None):
    if key:
        r = c.post("/org/projects", json={"name": name}, headers=H(key))
    else:
        r = c.post(f"/admin/orgs/{org['id']}/projects", json={"name": name}, headers=H(ADMIN))
    assert r.status_code == 200, r.text
    return r.json()


def make_key(c, org, ktype, name, project_id=None):
    r = c.post(f"/admin/orgs/{org['id']}/keys", json={"type": ktype, "name": name, **({"project_id": project_id} if project_id else {})}, headers=H(ADMIN))
    assert r.status_code == 200, r.text
    return r.json()["key"]


@pytest.fixture(scope="module")
def tenants(hosted):
    """Two orgs, one project each, ingest + read keys, with distinct data in each silo."""
    m, c = hosted
    out = {}
    for tag in ("alpha", "beta"):
        org = make_org(c, f"org-{tag}")
        proj = make_project(c, org, f"{tag}-prod")
        ing = make_key(c, org, "ingest", "ingest", proj["id"])
        rd = make_key(c, org, "read", "read", proj["id"])
        svc = f"svc-{tag}"
        assert c.post("/v1/traces", json=spans(svc, 6, error_every=2), headers=H(ing)).status_code == 200
        assert c.post("/v1/logs", json=logs(svc, f"secret-{tag}-log"), headers=H(ing)).status_code == 200
        assert c.post("/v1/metrics", json=metrics(svc, f"metric_{tag}"), headers=H(ing)).status_code == 200
        out[tag] = {"org": org, "proj": proj, "ingest": ing, "read": rd, "admin": org["admin_key"]["key"], "svc": svc}
    return m, c, out


# ---------------------------------------------------------------- modes
def test_default_mode_is_selfhosted_and_unchanged():
    m = load("server_self")
    c = TestClient(m.app)
    assert m.MODE == "selfhosted" and not m.HOSTED
    assert c.get("/api/mode").json() == {"mode": "selfhosted", "features": {
        "multi_tenant": False, "organizations": False, "quotas": False, "admin_api": False, "ai_assistant": False}}
    assert c.get("/api/services").status_code == 200          # no key needed, as before
    assert c.post("/v1/traces", json=spans("x", 2)).status_code == 200
    assert any(s["service"] == "x" for s in c.get("/api/services").json())
    assert c.get("/api/stats").json()["db_path"].endswith("default.db")
    # hosted-only surfaces do not exist
    assert c.get("/admin/orgs", headers=H(ADMIN)).status_code == 404
    assert c.post("/admin/orgs", json={"name": "nope"}).status_code == 404
    assert c.get("/org/me").status_code == 404
    assert not os.path.exists(os.path.join(os.path.dirname(m.DB_PATH), "tenants", "platform.db"))


def test_selfhosted_token_behaviour_unchanged():
    m = load("server_self_tok", PULSE0_TOKEN="s3cret")
    c = TestClient(m.app)
    assert c.get("/api/services").status_code == 401
    assert c.get("/api/services", headers=H("wrong")).status_code == 401
    assert c.get("/api/services", headers=H("s3cret")).status_code == 200
    assert c.post("/v1/traces", json=spans("x", 1)).status_code == 401
    assert c.get("/healthz").status_code == 200


def test_bad_mode_and_missing_admin_token_fail_fast():
    with pytest.raises(RuntimeError):
        load("server_badmode", PULSE0_MODE="enterprise")
    with pytest.raises(RuntimeError):
        load("server_noadmin", PULSE0_MODE="hosted")


def test_mode_endpoint_public_in_hosted(hosted):
    m, c = hosted
    r = c.get("/api/mode")
    assert r.status_code == 200 and r.json()["mode"] == "hosted"
    f = r.json()["features"]
    assert f["multi_tenant"] and f["organizations"] and f["quotas"]
    assert c.get("/healthz").status_code == 200 and c.get("/").status_code == 200


# ---------------------------------------------------------------- admin guard + orgs
def test_admin_requires_platform_token(hosted):
    m, c = hosted
    assert c.get("/admin/orgs").status_code == 401
    assert c.get("/admin/orgs", headers=H("nope")).status_code == 401
    assert c.get("/admin/orgs", headers=H(ADMIN)).status_code == 200
    assert c.get("/admin/orgs", headers={"x-pulse0-key": ADMIN}).status_code == 200
    # a tenant key is not a platform token
    org = make_org(c, "org-guard")
    assert c.get("/admin/orgs", headers=H(org["admin_key"]["key"])).status_code == 401


def test_org_crud_validation_and_quotas(hosted):
    m, c = hosted
    org = make_org(c, "org-crud", daily_cap=1234, retention_hours=48)
    assert org["daily_cap"] == 1234 and org["retention_hours"] == 48 and org["admin_key"]["key"].startswith("p0a_")
    assert c.post("/admin/orgs", json={"name": "org-crud"}, headers=H(ADMIN)).status_code == 409
    assert c.post("/admin/orgs", json={"name": "x"}, headers=H(ADMIN)).status_code == 400
    assert c.post("/admin/orgs", json={"name": "bad/name"}, headers=H(ADMIN)).status_code == 400
    assert c.post("/admin/orgs", json={"name": "okname", "evil": 1}, headers=H(ADMIN)).status_code == 400
    listed = {o["name"]: o for o in c.get("/admin/orgs", headers=H(ADMIN)).json()}
    assert listed["org-crud"]["daily_cap"] == 1234 and "admin_key" not in listed["org-crud"]
    r = c.patch(f"/admin/orgs/{org['id']}", json={"daily_cap": 0, "retention_hours": 24}, headers=H(ADMIN))
    assert r.status_code == 200 and r.json()["daily_cap"] == 0 and r.json()["retention_hours"] == 24
    assert c.patch("/admin/orgs/org_000000000000", json={"daily_cap": 5}, headers=H(ADMIN)).status_code == 404
    assert c.patch(f"/admin/orgs/{org['id']}", json={"retention_hours": 0}, headers=H(ADMIN)).status_code == 400


def test_delete_org_removes_data_and_keys(hosted):
    m, c = hosted
    org = make_org(c, "org-del")
    proj = make_project(c, org, "p")
    ing = make_key(c, org, "ingest", "i", proj["id"])
    assert c.post("/v1/traces", json=spans("gone", 2), headers=H(ing)).status_code == 200
    path = m._project_path(proj["id"])
    assert os.path.exists(path)
    assert c.delete(f"/admin/orgs/{org['id']}", headers=H(ADMIN)).status_code == 200
    assert not os.path.exists(path)
    assert c.post("/v1/traces", json=spans("gone", 1), headers=H(ing)).status_code == 401
    assert c.get(f"/admin/orgs/{org['id']}/usage", headers=H(ADMIN)).status_code == 404


# ---------------------------------------------------------------- keys
def test_keys_are_hashed_one_time_and_revocable(hosted):
    m, c = hosted
    org = make_org(c, "org-keys")
    proj = make_project(c, org, "p")
    r = c.post(f"/admin/orgs/{org['id']}/keys", json={"type": "ingest", "name": "ci", "project_id": proj["id"]}, headers=H(ADMIN))
    k = r.json()
    assert k["key"].startswith("p0i_") and k["prefix"] == k["key"][:10] and "cannot be shown again" in k["note"]
    # stored hashed: the secret is in no column of any table
    rows = m.pq("SELECT * FROM api_keys WHERE id=?", (k["id"],))
    assert rows and k["key"] not in "".join(str(v) for v in rows[0].values()) and rows[0]["hash"] == m._hash_key(k["key"])
    # listing never exposes secret or hash
    listing = c.get(f"/admin/orgs/{org['id']}/keys", headers=H(ADMIN)).json()
    assert all("key" not in x and "hash" not in x for x in listing)
    # works, then last_used is stamped, then revoke is immediate
    assert c.post("/v1/traces", json=spans("s", 1), headers=H(k["key"])).status_code == 200
    assert [x for x in c.get(f"/admin/orgs/{org['id']}/keys", headers=H(ADMIN)).json() if x["id"] == k["id"]][0]["last_used"]
    assert c.delete(f"/admin/keys/{k['id']}", headers=H(ADMIN)).status_code == 200
    assert c.post("/v1/traces", json=spans("s", 1), headers=H(k["key"])).status_code == 401
    assert c.delete("/admin/keys/key_000000000000", headers=H(ADMIN)).status_code == 404


def test_key_type_scoping(hosted):
    m, c = hosted
    org = make_org(c, "org-scope")
    proj = make_project(c, org, "p")
    ing, rd = make_key(c, org, "ingest", "i", proj["id"]), make_key(c, org, "read", "r", proj["id"])
    adm = org["admin_key"]["key"]
    # ingest key: /v1 only
    assert c.post("/v1/traces", json=spans("s", 1), headers=H(ing)).status_code == 200
    assert c.get("/api/services", headers=H(ing)).status_code == 403
    assert c.get("/org/me", headers=H(ing)).status_code == 403
    # read key: GET /api only
    assert c.get("/api/services", headers=H(rd)).status_code == 200
    assert c.post("/v1/traces", json=spans("s", 1), headers=H(rd)).status_code == 403
    assert c.post("/api/alerts", json={"name": "a", "signal": "error_rate", "op": ">", "threshold": 1}, headers=H(rd)).status_code == 403
    assert c.get("/org/me", headers=H(rd)).status_code == 403
    # admin key: read + write /api and /org, but not /v1 and not /admin
    assert c.get("/api/services", headers=H(adm)).status_code == 200
    assert c.post("/api/alerts", json={"name": "a", "signal": "error_rate", "op": ">", "threshold": 1}, headers=H(adm)).status_code == 200
    assert c.post("/v1/traces", json=spans("s", 1), headers=H(adm)).status_code == 403
    assert c.get("/admin/orgs", headers=H(adm)).status_code == 401
    # ingest/read keys must be bound to a project of THIS org
    assert c.post(f"/admin/orgs/{org['id']}/keys", json={"type": "ingest", "name": "x"}, headers=H(ADMIN)).status_code == 400
    other = make_org(c, "org-scope-other")
    foreign = make_project(c, other, "fp")
    assert c.post(f"/admin/orgs/{org['id']}/keys", json={"type": "read", "name": "x", "project_id": foreign["id"]}, headers=H(ADMIN)).status_code == 404


def test_unknown_malformed_and_revoked_keys_get_401(hosted):
    m, c = hosted
    for path in ("/api/services", "/api/traces", "/org/me"):
        assert c.get(path).status_code == 401
        assert c.get(path, headers=H("p0r_doesnotexist")).status_code == 401
        assert c.get(path, headers=H("x" * 500)).status_code == 401
    assert c.post("/v1/traces", json=spans("s", 1)).status_code == 401
    assert c.post("/v1/logs", json=logs("s", "m"), headers=H("p0i_nope")).status_code == 401
    assert c.post("/v1/metrics", json=metrics("s", "m"), headers={"x-pulse0-key": "p0i_nope"}).status_code == 401


def test_key_accepted_via_authorization_or_x_pulse0_key(tenants):
    m, c, t = tenants
    a = t["alpha"]
    assert c.post("/v1/traces", json=spans(a["svc"], 1), headers={"x-pulse0-key": a["ingest"]}).status_code == 200
    assert c.post("/v1/traces", json=spans(a["svc"], 1), headers={"Authorization": f"Bearer {a['ingest']}"}).status_code == 200
    assert c.post("/v1/traces", json=spans(a["svc"], 1), headers={"Authorization": f"bearer {a['ingest']}"}).status_code == 200


# ---------------------------------------------------------------- isolation
def test_tenant_isolation_across_signals(tenants):
    m, c, t = tenants
    a, b = t["alpha"], t["beta"]
    for me, other in ((a, b), (b, a)):
        h = H(me["read"])
        svc = [s["service"] for s in c.get("/api/services?minutes=60", headers=h).json() if s["n"] > 0]
        assert svc == [me["svc"]], svc
        found = c.get("/api/traces?minutes=60", headers=h).json()
        assert found and all(x["root_service"] == me["svc"] for x in found)
        lg = c.get("/api/logs?minutes=60", headers=h).json()
        assert lg and all(f"secret-{other['svc'][4:]}-log" not in x["body"] for x in lg)
        assert any(f"secret-{me['svc'][4:]}-log" in x["body"] for x in lg)
        names = [n["name"] for n in c.get("/api/metrics/names", headers=h).json()]
        assert f"metric_{me['svc'][4:]}" in names and f"metric_{other['svc'][4:]}" not in names
        # another tenant's trace id yields nothing
        other_tid = c.get("/api/traces?minutes=60", headers=H(other["read"])).json()[0]["trace_id"]
        assert c.get(f"/api/traces/{other_tid}", headers=h).status_code == 404
        assert c.get(f"/api/traces/{other_tid}", headers=H(other["read"])).status_code == 200  # control: owner can
        assert c.get("/api/logs?minutes=60&service=" + other["svc"], headers=h).json() == []
        assert c.get("/api/search?q_=" + other["svc"], headers=h).json()["services"] == []
        assert any(me["svc"] in str(x) for x in c.get("/api/search?q_=" + me["svc"], headers=h).json()["services"])  # control


def test_tenant_isolation_alerts_dashboards_slos(tenants):
    m, c, t = tenants
    a, b = t["alpha"], t["beta"]
    assert c.post("/api/alerts", json={"name": "alpha-rule", "signal": "error_rate", "op": ">", "threshold": 1}, headers=H(a["admin"]), params={"project": a["proj"]["id"]}).status_code == 200
    assert c.post("/api/dashboards", json={"name": "alpha-dash", "panels": []}, headers=H(a["admin"])).status_code == 200
    assert c.post("/api/slos", json={"name": "alpha-slo", "service": a["svc"], "objective_pct": 99, "latency_ms": 500}, headers=H(a["admin"])).status_code == 200
    assert [r["name"] for r in c.get("/api/alerts", headers=H(a["read"])).json()] == ["alpha-rule"]
    assert c.get("/api/alerts", headers=H(b["read"])).json() == []
    assert "alpha-dash" in str(c.get("/api/dashboards", headers=H(a["read"])).json())
    assert "alpha-dash" not in str(c.get("/api/dashboards", headers=H(b["read"])).json())
    assert c.get("/api/slos", headers=H(b["read"])).json() == []
    assert c.get("/api/alert-events", headers=H(b["read"])).json() == []
    assert c.get("/api/synthetics", headers=H(b["read"])).json() == []
    # separate files on disk
    assert m._project_path(a["proj"]["id"]) != m._project_path(b["proj"]["id"]) and os.path.exists(m._project_path(b["proj"]["id"]))


def test_org_admin_project_selection_cannot_cross_orgs(tenants):
    m, c, t = tenants
    a, b = t["alpha"], t["beta"]
    # single project: chosen automatically
    assert c.get("/api/services", headers=H(a["admin"])).status_code == 200
    # naming another org's project is a 404 (never a read)
    r = c.get("/api/services", headers={**H(a["admin"]), "x-pulse0-project": b["proj"]["id"]})
    assert r.status_code == 404 and r.json()["error"] == "unknown_project"
    assert c.get("/api/services?project=" + b["proj"]["id"], headers=H(a["admin"])).status_code == 404
    # several projects: must choose
    p2 = make_project(c, a["org"], "alpha-staging", key=a["admin"])
    r = c.get("/api/services", headers=H(a["admin"]))
    assert r.status_code == 400 and r.json()["error"] == "project_required" and len(r.json()["projects"]) == 2
    ok = c.get("/api/services", headers={**H(a["admin"]), "x-pulse0-project": p2["name"]})
    assert ok.status_code == 200 and [s for s in ok.json() if s["n"] > 0] == []   # staging is empty: no leakage from prod
    assert c.delete(f"/org/projects/{p2['id']}", headers=H(a["admin"])).status_code == 200


def test_stats_hides_server_path_in_hosted(tenants):
    m, c, t = tenants
    s = c.get("/api/stats", headers=H(t["alpha"]["read"])).json()
    assert s["db_path"] == "hosted" and s["spans"] > 0
    assert s["spans"] != c.get("/api/stats", headers=H(t["beta"]["read"])).json().get("nonexistent", -1)


# ---------------------------------------------------------------- quotas + usage
def test_daily_cap_returns_429_and_is_counted(hosted):
    m, c = hosted
    org = make_org(c, "org-cap", daily_cap=5)
    proj = make_project(c, org, "p")
    ing = make_key(c, org, "ingest", "i", proj["id"])
    assert c.post("/v1/traces", json=spans("capped", 3), headers=H(ing)).status_code == 200
    assert c.post("/v1/traces", json=spans("capped", 3), headers=H(ing)).status_code == 200  # 3 < 5 when checked; now 6
    r = c.post("/v1/traces", json=spans("capped", 1), headers=H(ing))
    assert r.status_code == 429 and r.json()["error"] == "quota_exceeded"
    assert 0 < int(r.headers["Retry-After"]) <= 86400
    assert c.post("/v1/logs", json=logs("capped", "x"), headers=H(ing)).status_code == 429
    u = c.get(f"/admin/orgs/{org['id']}/usage", headers=H(ADMIN)).json()
    p = u["projects"][0]
    assert u["org"]["used_today"] == 6 and u["org"]["daily_cap"] == 5
    assert p["today"]["spans"] == 6 and p["today"]["rejected"] == 2 and p["last7d"]["spans"] == 6 and p["db_bytes"] > 0
    # reads are never blocked by the ingest cap
    rd = make_key(c, org, "read", "r", proj["id"])
    assert c.get("/api/services", headers=H(rd)).status_code == 200
    # raising the cap restores ingest immediately; 0 = unlimited
    assert c.patch(f"/admin/orgs/{org['id']}", json={"daily_cap": 100}, headers=H(ADMIN)).status_code == 200
    assert c.post("/v1/traces", json=spans("capped", 1), headers=H(ing)).status_code == 200
    assert c.patch(f"/admin/orgs/{org['id']}", json={"daily_cap": 0}, headers=H(ADMIN)).status_code == 200
    assert c.post("/v1/traces", json=spans("capped", 50), headers=H(ing)).status_code == 200


def test_cap_is_per_org_not_per_project_and_isolated_between_orgs(hosted):
    m, c = hosted
    o1, o2 = make_org(c, "org-cap-a", daily_cap=4), make_org(c, "org-cap-b", daily_cap=4)
    p1a, p1b, p2 = make_project(c, o1, "a"), make_project(c, o1, "b"), make_project(c, o2, "a")
    k1a, k1b, k2 = (make_key(c, o1, "ingest", "i", p1a["id"]), make_key(c, o1, "ingest", "i", p1b["id"]), make_key(c, o2, "ingest", "i", p2["id"]))
    assert c.post("/v1/traces", json=spans("s", 4), headers=H(k1a)).status_code == 200
    assert c.post("/v1/traces", json=spans("s", 1), headers=H(k1b)).status_code == 429   # same org, other project
    assert c.post("/v1/traces", json=spans("s", 1), headers=H(k2)).status_code == 200    # other org unaffected


def test_usage_counts_all_three_signals(hosted):
    m, c = hosted
    org = make_org(c, "org-usage", daily_cap=0)
    proj = make_project(c, org, "p")
    ing = make_key(c, org, "ingest", "i", proj["id"])
    c.post("/v1/traces", json=spans("u", 4), headers=H(ing)); c.post("/v1/logs", json=logs("u", "m"), headers=H(ing)); c.post("/v1/metrics", json=metrics("u", "mm"), headers=H(ing))
    adm = org["admin_key"]["key"]
    me = c.get("/org/me", headers=H(adm)).json()
    t = me["projects"][0]["today"]
    assert (t["spans"], t["logs"], t["metrics"]) == (4, 1, 1) and me["org"]["used_today"] == 6 and me["key"]["type"] == "admin"
    assert c.get("/org/usage", headers=H(adm)).json()["projects"][0]["last7d"]["spans"] == 4
    assert any(x["org"]["name"] == "org-usage" for x in c.get("/admin/usage", headers=H(ADMIN)).json())


# ---------------------------------------------------------------- org self-service
def test_org_self_service_lifecycle(hosted):
    m, c = hosted
    org = make_org(c, "org-self")
    adm = org["admin_key"]["key"]
    proj = make_project(c, org, "web", key=adm)
    assert c.post("/org/projects", json={"name": "web"}, headers=H(adm)).status_code == 409
    k = c.post("/org/keys", json={"type": "ingest", "name": "ci", "project_id": proj["id"]}, headers=H(adm)).json()
    assert c.post("/v1/traces", json=spans("web", 1), headers=H(k["key"])).status_code == 200
    keys = c.get("/org/keys", headers=H(adm)).json()
    assert {x["name"] for x in keys} >= {"initial admin key", "ci"} and all("hash" not in x for x in keys)
    assert c.delete(f"/org/keys/{k['id']}", headers=H(adm)).status_code == 200
    assert c.post("/v1/traces", json=spans("web", 1), headers=H(k["key"])).status_code == 401
    # cannot revoke the key you are signed in with, nor another org's key
    assert c.delete(f"/org/keys/{org['admin_key']['id']}", headers=H(adm)).status_code == 400
    other = make_org(c, "org-self-other")
    assert c.delete(f"/org/keys/{other['admin_key']['id']}", headers=H(adm)).status_code == 404
    assert c.delete(f"/org/projects/{make_project(c, other, 'x')['id']}", headers=H(adm)).status_code == 404
    assert c.delete("/org/projects/../../etc", headers=H(adm)).status_code in (404, 405)
    # org admins cannot change their quota
    assert c.patch(f"/admin/orgs/{org['id']}", json={"daily_cap": 0}, headers=H(adm)).status_code == 401


def test_failed_auth_throttle_never_blocks_valid_keys():
    m = load("server_throttle", PULSE0_MODE="hosted", PULSE0_ADMIN_TOKEN=ADMIN, PULSE0_AUTH_FAIL_LIMIT=3)
    c = TestClient(m.app)
    org = make_org(c, "org-throttle")
    adm = org["admin_key"]["key"]
    codes = [c.get("/api/services", headers=H("bad")).status_code for _ in range(6)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429, 429]
    r = c.get("/admin/orgs", headers=H("bad"))
    assert r.status_code == 429 and r.headers["Retry-After"] == "60"
    assert c.get("/org/me", headers=H(adm)).status_code == 200            # valid key unaffected
    assert c.get("/admin/orgs", headers=H(ADMIN)).status_code == 200      # valid admin token unaffected


# ---------------------------------------------------------------- worker + retention
def test_worker_services_every_tenant(hosted):
    m, c = hosted
    org = make_org(c, "org-worker", retention_hours=1, daily_cap=0)
    adm = org["admin_key"]["key"]
    sil = {}
    for name in ("w1", "w2"):
        p = make_project(c, org, name)
        ing = make_key(c, org, "ingest", "i", p["id"])
        assert c.post("/v1/traces", json=spans("fresh", 4, error_every=1), headers=H(ing)).status_code == 200
        r = c.post("/api/alerts", json={"name": f"rule-{name}", "signal": "error_rate", "op": ">", "threshold": 1}, headers={**H(adm), "x-pulse0-project": p["id"]})
        assert r.status_code == 200
        sil[name] = m._project_path(p["id"])
        tok = m._cur_db.set(sil[name])      # plant an old span straight into the silo
        try:
            old = time.time_ns() - int(3 * 3600e9)
            with m.db() as con:
                con.execute("INSERT INTO spans VALUES('t','s','','old','op',2,?,?,1,1,'','{}','{}')", (old, old + 1))
        finally:
            m._cur_db.reset(tok)
    assert {path for path, _ in m._tenant_silos()} >= set(sil.values())
    state = {"last_alert": 0, "last_prune": 0}
    m.worker_tick(state)
    assert state["last_alert"] and state["last_prune"]
    for name, path in sil.items():
        tok = m._cur_db.set(path)
        try:
            assert m.q("SELECT COUNT(*) n FROM spans WHERE service='old'")[0]["n"] == 0, name   # pruned by org retention (1h)
            assert m.q("SELECT COUNT(*) n FROM spans WHERE service='fresh'")[0]["n"] == 4, name  # recent data kept
            rule = m.q("SELECT * FROM alert_rules")[0]
            assert rule["last_eval"] and rule["state"] == "firing", name                         # evaluated in this silo
        finally:
            m._cur_db.reset(tok)
    # the default (self-hosted) database was never touched
    assert m.q("SELECT COUNT(*) n FROM spans")[0]["n"] == 0


def test_one_broken_tenant_does_not_stop_the_worker(hosted, monkeypatch):
    m, c = hosted
    org = make_org(c, "org-broken", daily_cap=0)
    ok_p, bad_p = make_project(c, org, "ok"), make_project(c, org, "bad")
    adm = org["admin_key"]["key"]
    for p in (ok_p, bad_p):
        c.post("/api/alerts", json={"name": "r", "signal": "error_rate", "op": ">", "threshold": 1}, headers={**H(adm), "x-pulse0-project": p["id"]})
    real = m.evaluate_rules

    def flaky():
        if m._cur_db.get() == m._project_path(bad_p["id"]):
            raise RuntimeError("boom")
        return real()
    monkeypatch.setattr(m, "evaluate_rules", flaky)
    m.worker_tick({"last_alert": 0, "last_prune": 0})
    tok = m._cur_db.set(m._project_path(ok_p["id"]))
    try:
        assert m.q("SELECT last_eval FROM alert_rules")[0]["last_eval"]
    finally:
        m._cur_db.reset(tok)


# ---------------------------------------------------------------- outbound guard + AI
@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://localhost:8080", "http://169.254.169.254/latest/meta-data",
                                 "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.9/", "http://[::1]/", "http://0.0.0.0/",
                                 "file:///etc/passwd", "ftp://example.com/", "http://[::ffff:127.0.0.1]/"])
def test_ssrf_guard_blocks_internal_destinations_in_hosted_mode(hosted, url):
    m, c = hosted
    assert m._blocked_dest(url)


def test_ssrf_guard_allows_public_ip_and_is_off_in_selfhosted(hosted):
    m, c = hosted
    assert m._blocked_dest("http://93.184.216.34/") is None
    s = load("server_self_ssrf")
    assert s._blocked_dest("http://127.0.0.1/") is None and s._blocked_dest("http://10.0.0.1/") is None


def test_synthetic_check_to_internal_address_is_blocked_in_hosted(hosted):
    m, c = hosted
    org = make_org(c, "org-ssrf", daily_cap=0)
    p = make_project(c, org, "p")
    adm = org["admin_key"]["key"]
    hh = {**H(adm), "x-pulse0-project": p["id"]}
    r = c.post("/api/synthetics", json={"name": "meta", "url": "http://169.254.169.254/latest/meta-data", "interval_s": 60}, headers=hh)
    assert r.status_code == 200
    cid = r.json().get("id") or c.get("/api/synthetics", headers=hh).json()[0]["id"]
    run = c.post(f"/api/synthetics/{cid}/run", headers=hh)
    assert run.status_code == 200
    tok = m._cur_db.set(m._project_path(p["id"]))
    try:
        row = m.q("SELECT * FROM synthetic_runs")[0]
        assert row["ok"] == 0 and "not allowed" in row["err"]
    finally:
        m._cur_db.reset(tok)


def test_assistant_disabled_in_hosted_unless_opted_in(hosted, monkeypatch):
    m, c = hosted
    org = make_org(c, "org-ai")
    p = make_project(c, org, "p")
    rd = make_key(c, org, "read", "r", p["id"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert c.get("/api/assistant/status", headers=H(rd)).json()["enabled"] is False
    assert c.post("/api/assistant", json={"messages": [{"role": "user", "content": "hi"}]}, headers=H(rd)).status_code == 503
    assert c.get("/api/mode").json()["features"]["ai_assistant"] is False
    monkeypatch.setenv("PULSE0_HOSTED_AI", "1")
    assert c.get("/api/assistant/status", headers=H(rd)).json()["enabled"] is True
    # read keys may reach the read-only assistant/investigate POSTs but nothing else that writes
    assert c.post("/api/investigate", headers=H(rd)).status_code != 403
    assert c.post("/api/slos", json={}, headers=H(rd)).status_code == 403
