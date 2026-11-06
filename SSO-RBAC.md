# Single sign-on and roles

Self-hosted Pulse0 supports login through any OpenID Connect (OIDC) provider and three roles. Hosted mode keeps its own
organization/project API keys and is unaffected.

## Roles

| Role | Can do |
|---|---|
| `viewer` | Read everything: services, map, traces, logs, metrics, dashboards, alerts, SLOs, Ask Pulse0 |
| `editor` | Everything a viewer can, plus create, change and delete dashboards, alert rules, SLOs and synthetic checks |
| `admin` | Everything an editor can, plus read the audit log (Settings, or `GET /api/audit`) |

A user gets the highest role among the groups they belong to. Users in none of your groups get `defaultRole`
(`viewer` by default; set it to `none` to refuse them).

## Identity sources

1. **Single sign-on** (browser): a signed, HTTP-only session cookie after an OIDC login.
2. **`PULSE0_TOKEN`**: bearer token, treated as `admin` (unchanged from before).
3. **`PULSE0_READ_TOKEN`** (optional): bearer token that is `viewer`, for dashboards or scripts that only read.
4. **`PULSE0_INGEST_TOKEN`** (optional): can only write telemetry (`/v1/*`). Give this to apps and collectors, never the admin token.
5. With none of the above configured, access is open, exactly as before.

Telemetry ingest (`/v1/*`) never accepts a login session; it needs `PULSE0_TOKEN` or `PULSE0_INGEST_TOKEN` when either is set.
**If you enable SSO, also set an ingest token**, otherwise anyone who can reach the port can still write telemetry.

## Set it up

1. In your identity provider create a **confidential web client** (authorization-code flow). Redirect URI:
   `https://<your-pulse0-host>/auth/callback`.
2. Make the token carry a groups claim (default claim name `groups`).
3. Create the secrets and enable it:

```bash
kubectl -n observability create secret generic pulse0-oidc \
  --from-literal=clientSecret=<CLIENT_SECRET> --from-literal=sessionSecret=$(openssl rand -hex 32)

helm upgrade --install pulse0 ./helm/pulse0 -n observability --create-namespace \
  -f helm/pulse0/values-baremetal.yaml \
  --set auth.token=<ADMIN_TOKEN> --set auth.ingestToken=<INGEST_TOKEN> \
  --set auth.oidc.enabled=true \
  --set auth.oidc.issuer=https://login.example.com/realms/acme \
  --set auth.oidc.clientId=pulse0 --set auth.oidc.existingSecret=pulse0-oidc \
  --set auth.oidc.redirectUrl=https://pulse0.example.com/auth/callback \
  --set 'auth.oidc.adminGroups={pulse0-admins}' --set 'auth.oidc.editorGroups={pulse0-editors}' \
  --set 'auth.oidc.viewerGroups={pulse0-viewers}'
```

(`helm upgrade --reuse-values` can drop chart defaults; pass the full set.) Without Helm, set the `PULSE0_OIDC_*` environment
variables listed below.

| Variable | Meaning |
|---|---|
| `PULSE0_OIDC_ISSUER`, `PULSE0_OIDC_CLIENT_ID`, `PULSE0_OIDC_CLIENT_SECRET` | The client you created. Enabling needs issuer and client id |
| `PULSE0_SESSION_SECRET` | Signs session cookies. Set it (and keep it stable) or sessions end on every restart |
| `PULSE0_OIDC_REDIRECT_URL` | Public callback URL. Set it whenever TLS ends before Pulse0, so the URL and the `Secure` cookie flag are right |
| `PULSE0_OIDC_ADMIN_GROUPS`, `_EDITOR_GROUPS`, `_VIEWER_GROUPS` | Comma-separated group names |
| `PULSE0_OIDC_ROLE_CLAIM` | Claim holding groups (default `groups`; dotted paths such as `realm_access.roles` work; a comma or space separated string is accepted) |
| `PULSE0_OIDC_DEFAULT_ROLE` | `viewer` (default), `editor`, `admin` or `none` |
| `PULSE0_OIDC_ALLOWED_DOMAINS` | Optional email-domain allow-list |
| `PULSE0_OIDC_SCOPES`, `PULSE0_SESSION_TTL_HOURS`, `PULSE0_OIDC_PROVIDER_NAME` | `openid email profile`, `12`, `SSO` |
| `PULSE0_AUDIT_RETENTION_DAYS` | How long audit rows are kept (default 90) |

Provider notes (check your provider's documentation): Okta needs a `groups` claim added to the ID token; Microsoft Entra ID
puts group object IDs, not names, in `groups`, so list those IDs; Keycloak can add a groups mapper or use `realm_access.roles`.

## What it does for security

- Authorization-code flow with PKCE, a random `state` and `nonce`; the ID token's issuer, audience, expiry and nonce are checked.
- Sessions are HMAC-signed, HTTP-only, `SameSite=Lax`, and `Secure` over HTTPS.
- Writes made with a session must come from the same origin (CSRF check).
- `next` redirects are restricted to local paths.
- Every change (dashboards, alerts, SLOs, synthetics), sign-in and sign-out is recorded in the audit log with who, role, method, path and result.

Not covered yet: ID-token signature verification (the token is accepted because it arrives over TLS from the issuer's token
endpoint, which OpenID Connect allows, so use `https://` issuers in production), logout at the identity provider (signing out
ends the Pulse0 session only), SCIM user provisioning, per-dashboard permissions, and SAML (use an OIDC bridge such as Keycloak or Dex).
It has been tested against a built-in fake provider (`python tests/fake_idp.py`), not yet against a real Okta, Entra ID,
Keycloak or ADFS.

## On-premises and air-gapped

Nothing in Pulse0 needs the internet. For a datacenter or air-gapped cluster:

- **Images:** mirror `pulse0` and `otel/opentelemetry-collector-contrib` to your registry and set `image.repository` and
  `collector.image`. Start from `values-baremetal.yaml`.
- **Storage:** pick a StorageClass you run (Longhorn, Ceph, local-path, NFS). Pulse0 is a single-replica StatefulSet: snapshot
  or back up the volume yourself, and plan capacity with `PULSE0_RETENTION_HOURS`.
- **Exposure:** NodePort, MetalLB or an ingress with your internal CA, and set `auth.oidc.redirectUrl` to that URL.
- **Identity provider:** it must be reachable from the Pulse0 pod (discovery and token endpoints) and from users' browsers.
  Keycloak, Dex, ADFS and Okta all work if they speak OIDC. Active Directory logins go through ADFS, Keycloak or Dex's LDAP connector.
- **Private CA:** if the identity provider uses an internal CA, mount the CA bundle into the pod and set `env.SSL_CERT_FILE`
  in the chart values so Pulse0 trusts it.
- **No outbound traffic:** leave the Ask Pulse0 Claude integration off (it is by default), and Pulse0 makes no external calls.
  Webhook alerts and synthetic checks only call the URLs you configure.
