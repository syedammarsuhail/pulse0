"""A tiny stand-in OpenID Connect provider for trying Pulse0 SSO locally (NOT secure - never expose it).

    python tests/fake_idp.py                # listens on http://127.0.0.1:9001
    PULSE0_OIDC_ISSUER=http://127.0.0.1:9001 PULSE0_OIDC_CLIENT_ID=pulse0 PULSE0_OIDC_CLIENT_SECRET=x \
    PULSE0_OIDC_ADMIN_GROUPS=pulse0-admins PULSE0_OIDC_EDITOR_GROUPS=pulse0-editors PULSE0_OIDC_VIEWER_GROUPS=pulse0-viewers \
    PULSE0_SESSION_SECRET=dev python server.py

The authorize page offers one click per persona; the token endpoint returns an unsigned id_token (Pulse0 trusts the token
endpoint over TLS and does not verify signatures, so this is enough for a local run).
"""
import base64, json, secrets, sys, time, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9001
ISSUER = f"http://127.0.0.1:{PORT}"
PERSONAS = {"viewer": ("vera@example.com", "Vera Viewer", ["pulse0-viewers"]),
            "editor": ("eli@example.com", "Eli Editor", ["pulse0-editors"]),
            "admin": ("ada@example.com", "Ada Admin", ["pulse0-admins"]),
            "norole": ("nora@example.com", "Nora NoRole", ["marketing"])}
CODES = {}


def b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json", headers=None):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/.well-known/openid-configuration":
            return self.send(200, json.dumps({"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                                              "token_endpoint": ISSUER + "/token"}))
        if u.path == "/authorize":
            links = ""
            for k, (email, name, groups) in PERSONAS.items():
                code = secrets.token_urlsafe(12); CODES[code] = (k, q.get("nonce", ""), q.get("client_id", ""))
                back = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": code, "state": q.get("state", "")})
                links += f"<p><a href='{back}'>Sign in as <b>{name}</b> ({k}) - {email}</a></p>"
            return self.send(200, f"<!doctype html><meta charset=utf-8><title>Fake IdP</title><body style='font:16px system-ui;"
                                  f"max-width:480px;margin:12vh auto'><h2>Fake identity provider</h2>{links}</body>", "text/html")
        self.send(404, "{}")

    def do_POST(self):
        if self.path != "/token":
            return self.send(404, "{}")
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
        persona, nonce, client = CODES.pop(form.get("code", ""), (None, "", ""))
        if not persona:
            return self.send(400, json.dumps({"error": "invalid_grant"}))
        email, name, groups = PERSONAS[persona]
        claims = {"iss": ISSUER, "aud": client, "sub": persona, "email": email, "email_verified": True, "name": name,
                  "groups": groups, "nonce": nonce, "iat": int(time.time()), "exp": int(time.time()) + 300}
        self.send(200, json.dumps({"id_token": b64(b'{"alg":"none"}') + "." + b64(json.dumps(claims).encode()) + ".x",
                                   "access_token": "fake", "token_type": "Bearer"}))


if __name__ == "__main__":
    print(f"fake IdP on {ISSUER}")
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
