"""SSO (OIDC) + RBAC for self-hosted mode, against a fake identity provider (no network)."""
import os, sys, time, urllib.parse

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("PULSE0_CACHE_TTL", "0")
os.environ.setdefault("PULSE0_DB", "/tmp/pulse0_test.db")

import server  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

ISSUER, CLIENT = "https://idp.example.com/realms/acme", "pulse0-client"
DISCOVERY = {"issuer": ISSUER, "authorization_endpoint": ISSUER + "/auth", "token_endpoint": ISSUER + "/token",
             "userinfo_endpoint": ISSUER + "/userinfo"}


def jwt(claims):
    return server._b64(b'{"alg":"RS256"}') + "." + server._b64(__import__("json").dumps(claims).encode()) + ".sig"


class FakeIdP:
    """Remembers the last authorization request so the token endpoint can echo its nonce, like a real IdP."""
    def __init__(self):
        self.claims = {}            # extra/override claims for the next login
        self.nonce = None
        self.userinfo = {}
        self.calls = []

    def __call__(self, url, form=None, headers=None):
        self.calls.append(url)
        if url.endswith("/.well-known/openid-configuration"):
            return DISCOVERY
        if url == DISCOVERY["token_endpoint"]:
            assert form["grant_type"] == "authorization_code" and form["code_verifier"] and form["client_secret"] == "s3cret"
            c = {"iss": ISSUER, "aud": CLIENT, "sub": "u1", "email": "ann@acme.com", "name": "Ann", "exp": time.time() + 300,
                 "nonce": self.nonce, "groups": ["pulse0-viewers"], **self.claims}
            c = {k: v for k, v in c.items() if v is not None}
            return {"id_token": jwt(c), "access_token": "at"}
        if url == DISCOVERY["userinfo_endpoint"]:
            assert headers["Authorization"] == "Bearer at"
            return self.userinfo
        raise AssertionError("unexpected IdP call " + url)


@pytest.fixture
def idp(monkeypatch):
    f = FakeIdP()
    monkeypatch.setitem(server.OIDC, "issuer", ISSUER)
    monkeypatch.setitem(server.OIDC, "client_id", CLIENT)
    monkeypatch.setitem(server.OIDC, "client_secret", "s3cret")
    monkeypatch.setitem(server.OIDC, "admin_groups", ["pulse0-admins"])
    monkeypatch.setitem(server.OIDC, "editor_groups", ["pulse0-editors"])
    monkeypatch.setitem(server.OIDC, "viewer_groups", ["pulse0-viewers"])
    monkeypatch.setitem(server.OIDC, "default_role", "")
    monkeypatch.setitem(server.OIDC, "allowed_domains", [])
    monkeypatch.setitem(server.OIDC, "role_claim", "groups")
    monkeypatch.setitem(server.OIDC, "redirect_url", "https://pulse.example.com/auth/callback")
    monkeypatch.setattr(server, "_idp_request", f)
    monkeypatch.setattr(server, "_disc", {"at": 0.0, "doc": None})
    monkeypatch.setattr(server, "AUTH_TOKEN", "")
    monkeypatch.setattr(server, "READ_TOKEN", "")
    monkeypatch.setattr(server, "INGEST_TOKEN", "")
    return f


def new_client():
    return TestClient(server.app, base_url="https://pulse.example.com", follow_redirects=False)


def login(idp, groups="keep", **claims):
    """Run the whole browser flow and return a client holding the session cookie (or the failed callback response)."""
    c = new_client()
    r = c.get("/auth/login", params={"next": "/#/services"})
    assert r.status_code == 302
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)
    idp.nonce = q["nonce"][0]
    if groups != "keep":
        claims["groups"] = groups          # None drops the claim entirely
    idp.claims = claims
    cb = c.get("/auth/callback", params={"code": "abc", "state": q["state"][0]})
    return c, cb


# ---------------------------------------------------------------- login flow
def test_login_redirect_uses_pkce_state_and_nonce(idp):
    r = new_client().get("/auth/login")
    assert r.status_code == 302
    u = urllib.parse.urlparse(r.headers["location"])
    q = urllib.parse.parse_qs(u.query)
    assert u.geturl().startswith(DISCOVERY["authorization_endpoint"])
    assert q["response_type"] == ["code"] and q["client_id"] == [CLIENT] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["https://pulse.example.com/auth/callback"]
    assert len(q["state"][0]) >= 24 and len(q["nonce"][0]) >= 24 and len(q["code_challenge"][0]) >= 40
    ck = r.headers["set-cookie"].lower()
    assert "httponly" in ck and "samesite=lax" in ck and "secure" in ck


@pytest.mark.parametrize("groups,role", [(["pulse0-admins", "x"], "admin"), (["pulse0-editors"], "editor"),
                                         (["pulse0-viewers"], "viewer"), (["pulse0-viewers", "pulse0-admins"], "admin")])
def test_groups_map_to_highest_role(idp, groups, role):
    c, cb = login(idp, groups)
    assert cb.status_code == 302 and cb.headers["location"] == "/#/services"
    me = c.get("/api/me").json()
    assert me == {"actor": "ann@acme.com", "name": "Ann", "role": role, "via": "session"}


def test_no_matching_group_is_denied_unless_default_role(idp, monkeypatch):
    c, cb = login(idp, ["unrelated"])
    assert cb.status_code == 403 and "no Pulse0 role" in cb.text
    assert c.get("/api/services").status_code == 401
    monkeypatch.setitem(server.OIDC, "default_role", "viewer")
    c, cb = login(idp, ["unrelated"])
    assert cb.status_code == 302 and c.get("/api/me").json()["role"] == "viewer"


def test_comma_string_and_nested_role_claims(idp, monkeypatch):
    c, cb = login(idp, "pulse0-editors, other")
    assert c.get("/api/me").json()["role"] == "editor"
    monkeypatch.setitem(server.OIDC, "role_claim", "realm_access.roles")
    c, cb = login(idp, None, realm_access={"roles": ["pulse0-admins"]})
    assert c.get("/api/me").json()["role"] == "admin"


def test_roles_fetched_from_userinfo_when_token_has_none(idp):
    idp.userinfo = {"groups": ["pulse0-admins"]}
    c, cb = login(idp, None)
    assert cb.status_code == 302 and c.get("/api/me").json()["role"] == "admin"


@pytest.mark.parametrize("bad", [dict(iss="https://evil.example"), dict(aud="someone-else"), dict(exp=time.time() - 3600),
                                 dict(nonce="not-mine"), dict(email_verified=False), dict(email=None, preferred_username=None, sub=None)])
def test_invalid_id_tokens_are_rejected(idp, bad):
    c, cb = login(idp, ["pulse0-admins"], **bad)
    assert cb.status_code in (403, 502), cb.text
    assert c.get("/api/services").status_code == 401


def test_allowed_domains(idp, monkeypatch):
    monkeypatch.setitem(server.OIDC, "allowed_domains", ["acme.com"])
    assert login(idp, ["pulse0-admins"])[1].status_code == 302
    assert login(idp, ["pulse0-admins"], email="mallory@evil.com")[1].status_code == 403


def test_callback_requires_matching_state_and_cookie(idp):
    c = new_client()
    assert c.get("/auth/callback", params={"code": "x", "state": "y"}).status_code == 400   # no login cookie
    c.get("/auth/login")
    assert c.get("/auth/callback", params={"code": "x", "state": "wrong"}).status_code == 400
    assert c.get("/auth/callback", params={"error": "access_denied"}).status_code == 400


def test_open_redirect_is_blocked(idp):
    for evil in ("https://evil.example", "//evil.example", "/\\evil.example"):
        c = new_client()
        r = c.get("/auth/login", params={"next": evil})
        q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)
        idp.nonce, idp.claims = q["nonce"][0], {"groups": ["pulse0-viewers"]}
        cb = c.get("/auth/callback", params={"code": "a", "state": q["state"][0]})
        assert cb.headers["location"] == "/"


def test_sso_endpoints_404_when_not_configured(monkeypatch):
    monkeypatch.setitem(server.OIDC, "issuer", "")
    c = new_client()
    assert c.get("/auth/login").status_code == 404 and c.get("/auth/callback").status_code == 404
    assert c.get("/api/auth/config").json()["sso"]["enabled"] is False


def test_auth_config_is_public_and_tells_the_ui_about_sso(idp, monkeypatch):
    monkeypatch.setattr(server, "AUTH_TOKEN", "tok")
    j = new_client().get("/api/auth/config").json()
    assert j["sso"]["enabled"] and j["sso"]["login_url"] == "/auth/login" and j["token_required"] is True
    r = new_client().get("/api/services")
    assert r.status_code == 401 and r.json()["sso"] is True


# ---------------------------------------------------------------- RBAC
def test_rbac_matrix(idp):
    viewer, _ = login(idp, ["pulse0-viewers"])
    editor, _ = login(idp, ["pulse0-editors"])
    admin, _ = login(idp, ["pulse0-admins"])
    dash = {"name": "rbac-dash", "panels": []}
    assert viewer.get("/api/services").status_code == 200
    r = viewer.post("/api/dashboards", json=dash)
    assert r.status_code == 403 and "editor role" in r.json()["message"] and "you are viewer" in r.json()["message"]
    assert viewer.post("/api/investigate", json={"q": "errors"}).status_code != 403   # read-only POSTs stay open to viewers
    r = editor.post("/api/dashboards", json=dash)
    assert r.status_code == 200, r.text
    assert editor.get("/api/audit").status_code == 403                                  # audit log is admin-only
    assert viewer.get("/api/audit").status_code == 403
    rows = admin.get("/api/audit").json()
    assert any(a["actor"] == "ann@acme.com" and a["method"] == "POST" and a["path"] == "/api/dashboards"
               and a["role"] == "editor" and a["status"] == 200 for a in rows)
    assert not any(a["actor"] == "ann@acme.com" and a["method"] == "POST" and a["role"] == "viewer"
                   and a["path"] == "/api/dashboards" for a in rows)               # refused requests never reach the handler
    did = next(d["id"] for d in editor.get("/api/dashboards").json() if d["name"] == "rbac-dash")
    assert viewer.delete(f"/api/dashboards/{did}").status_code == 403
    assert editor.delete(f"/api/dashboards/{did}").status_code == 200
    assert any(a["method"] == "LOGIN" for a in rows)


def test_tokens_keep_working_and_map_to_roles(idp, monkeypatch):
    monkeypatch.setattr(server, "AUTH_TOKEN", "admintok")
    monkeypatch.setattr(server, "READ_TOKEN", "readtok")
    monkeypatch.setattr(server, "INGEST_TOKEN", "ingesttok")
    c = new_client()
    H = lambda t: {"authorization": "Bearer " + t}
    assert c.get("/api/me", headers=H("admintok")).json()["role"] == "admin"
    assert c.get("/api/me", headers=H("readtok")).json()["role"] == "viewer"
    assert c.post("/api/dashboards", json={"name": "t", "panels": []}, headers=H("readtok")).status_code == 403
    assert c.get("/api/services", headers=H("ingesttok")).status_code == 401           # ingest token cannot read
    assert c.get("/api/services", headers=H("nope")).status_code == 401
    assert c.get("/api/services").status_code == 401
    body = {"resourceSpans": []}
    assert c.post("/v1/traces", json=body, headers=H("ingesttok")).status_code == 200
    assert c.post("/v1/traces", json=body, headers=H("admintok")).status_code == 200
    assert c.post("/v1/traces", json=body, headers=H("readtok")).status_code == 401
    assert c.post("/v1/traces", json=body).status_code == 401


def test_session_cookie_cannot_authorize_ingest(idp, monkeypatch):
    monkeypatch.setattr(server, "INGEST_TOKEN", "ingesttok")
    admin, _ = login(idp, ["pulse0-admins"])
    assert admin.post("/v1/traces", json={"resourceSpans": []}).status_code == 401


def test_forged_tampered_and_expired_cookies_are_rejected(idp):
    c = new_client()
    good = server._sign({"email": "a@b.c", "role": "admin", "exp": time.time() + 60})
    c.cookies.set("p0_session", good)
    assert c.get("/api/me").status_code == 200
    body, sig = good.split(".")
    forged = server._b64(b'{"email":"a@b.c","role":"admin","exp":9999999999}') + "." + sig
    for bad in (forged, body + ".AAAA", "garbage", server._sign({"email": "a@b.c", "role": "admin", "exp": time.time() - 5}),
                server._sign({"email": "a@b.c", "role": "superuser", "exp": time.time() + 60})):
        c2 = new_client(); c2.cookies.set("p0_session", bad)
        assert c2.get("/api/me").status_code == 401


def test_cross_origin_writes_with_a_session_are_refused(idp):
    editor, _ = login(idp, ["pulse0-editors"])
    r = editor.post("/api/dashboards", json={"name": "csrf", "panels": []}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and "cross-origin" in r.json()["message"]
    r = editor.post("/api/dashboards", json={"name": "same-origin", "panels": []}, headers={"origin": "https://pulse.example.com"})
    assert r.status_code == 200
    r = editor.post("/auth/logout", headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_logout_ends_the_session_and_is_audited(idp):
    admin, _ = login(idp, ["pulse0-admins"])
    assert admin.get("/api/me").status_code == 200
    r = admin.post("/auth/logout")
    assert r.status_code == 200 and r.json() == {"ok": True}
    admin.cookies.clear()
    assert admin.get("/api/me").status_code == 401
    other, _ = login(idp, ["pulse0-admins"], email="boss@acme.com")
    assert any(a["method"] == "LOGOUT" and a["actor"] == "ann@acme.com" for a in other.get("/api/audit").json())


def test_open_mode_is_unchanged_without_tokens_or_sso(monkeypatch):
    monkeypatch.setitem(server.OIDC, "issuer", "")
    monkeypatch.setattr(server, "AUTH_TOKEN", "")
    monkeypatch.setattr(server, "READ_TOKEN", "")
    c = new_client()
    assert c.get("/api/services").status_code == 200
    assert c.get("/api/me").json() == {"actor": "anonymous", "name": "", "role": "admin", "via": "open"}
    assert c.post("/v1/traces", json={"resourceSpans": []}).status_code == 200
