# Installing & Running Pulse0 (Client Guide)

A step-by-step guide you can hand to a client. Three install paths — pick one.

---

## Option A — Docker (single host, fastest)

**Prereqs:** Docker.

```bash
tar -xzf pulse0.tar.gz && cd pulse0
docker build -t pulse0:latest .
docker run -d --name pulse0 \
  -p 4318:4318 \
  -v pulse0-data:/data \
  -e PULSE0_TOKEN=change-me-to-a-secret \
  -e PULSE0_RETENTION_HOURS=168 \
  pulse0:latest
```

Open **http://localhost:4318**. Done. Point any OpenTelemetry exporter at `http://<host>:4318`.

> Data lives in the `pulse0-data` Docker volume (`/data/pulse0.db`). It survives restarts and upgrades.

---

## Option B — Kubernetes via Helm (recommended for a company)

**Prereqs:** a cluster, `kubectl`, `helm`, and a registry the cluster can pull from.

### 1. Build & push the images
```bash
cd pulse0
docker build -t <registry>/pulse0:0.1.0 .
docker push <registry>/pulse0:0.1.0
```

### 2. Install
```bash
helm install pulse0 ./helm/pulse0 \
  -n observability --create-namespace \
  --set image.repository=<registry>/pulse0 \
  --set image.tag=0.1.0 \
  --set auth.token=$(openssl rand -hex 24) \
  --set persistence.size=50Gi \
  --set env.PULSE0_RETENTION_HOURS=168
```

This deploys:
- the Pulse0 server (StatefulSet + PVC + Service),
- an **OpenTelemetry Collector DaemonSet** that automatically collects pod logs and node/cluster metrics,
- a Pulse0 `Instrumentation` resource for zero-code app instrumentation.

### 3. Access the UI
```bash
kubectl -n observability port-forward svc/pulse0 4318:4318
# open http://localhost:4318
```
For permanent access, enable the Ingress:
```bash
helm upgrade pulse0 ./helm/pulse0 -n observability --reuse-values \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=pulse0.yourcompany.com \
  --set ingress.hosts[0].paths[0].path=/ \
  --set ingress.hosts[0].paths[0].pathType=Prefix
```

### 4. Instrument your apps — no code changes
Install the OpenTelemetry Operator once:
```bash
kubectl apply -f https://github.com/open-telemetry/opentelemetry-operator/releases/latest/download/opentelemetry-operator.yaml
```
Then annotate any Deployment (see `ZERO-CODE-SETUP.md`):
```bash
kubectl -n myapp patch deploy my-api --type=strategic -p \
 '{"spec":{"template":{"metadata":{"annotations":{"instrumentation.opentelemetry.io/inject-java":"observability/pulse0"}}}}}'
```

---

## Option C — Bare process (dev / air-gapped)

**Prereqs:** Python 3.11+.

```bash
cd pulse0
pip install -r requirements.txt
PULSE0_HOST=0.0.0.0 PULSE0_PORT=4318 PULSE0_TOKEN=change-me python3 server.py
```

---

## Configuration reference

| Env var | Helm key | Default | Purpose |
|---|---|---|---|
| `PULSE0_HOST` | — | `127.0.0.1` | bind host (`0.0.0.0` in containers) |
| `PULSE0_PORT` | `service.port` | `4318` | OTLP + UI port |
| `PULSE0_DB` | — | `/data/pulse0.db` | SQLite path |
| `PULSE0_RETENTION_HOURS` | `env.PULSE0_RETENTION_HOURS` | `72` | how long signals are kept |
| `PULSE0_TOKEN` | `auth.token` | *(none)* | bearer token for `/v1/*` and `/api/*` |

## Verify it's healthy
```bash
curl http://<host>:4318/healthz    # {"status":"ok"}
curl http://<host>:4318/readyz     # {"status":"ready",...}
curl http://<host>:4318/api/stats  # row counts, retention, db path
```

## Send a test signal (no app needed)
```bash
cd pulse0/test-apps && ./run-all.sh    # launches Python/Node/Java demo apps + traffic
```
Within a minute you'll see services under **Resources**, a service map, traces, logs, and metrics.

## Back up your data
It's a single file:
```bash
# Docker
docker exec pulse0 sqlite3 /data/pulse0.db ".backup /data/backup.db"
# Kubernetes — snapshot the PVC, or:
kubectl -n observability exec sts/pulse0 -- sqlite3 /data/pulse0.db ".backup /data/backup.db"
```

## Upgrade
```bash
# Docker: rebuild, then
docker stop pulse0 && docker rm pulse0 && docker run ... pulse0:latest   # volume persists
# Helm:
helm upgrade pulse0 ./helm/pulse0 -n observability --reuse-values --set image.tag=<new>
```

## Deployment modes: self-hosted or Pulse-hosted

Same code, same UI, one switch (`PULSE0_MODE` / Helm `mode`).

| | **Self-hosted** (default) | **Pulse-hosted** (`hosted`) |
|---|---|---|
| Who runs it | You, in your cluster / Docker host | A platform operator, for many customers |
| Where data lives | Your own volume. Nothing leaves your environment | The operator's volume, one SQLite database per project |
| Isolation | The whole instance is yours | organization -> projects -> API keys; a key can only reach its own project |
| Auth | Optional `PULSE0_TOKEN` | Hashed API keys: `ingest` (send), `read` (query), `admin` (organization-wide) |
| Limits | None | Per-organization daily ingest cap (HTTP 429 + `Retry-After`) and retention |
| Operator API | not exposed (404) | `/admin/*` guarded by `PULSE0_ADMIN_TOKEN` |

Hosted mode quick start:
```bash
kubectl -n observability create secret generic pulse0-admin --from-literal=admin-token=$(openssl rand -hex 32)
kubectl -n observability create secret generic pulse0-collector-key --from-literal=ingest-key=<p0i_... from your own project>   # or set collector.enabled=false
helm upgrade --install pulse0 ./helm/pulse0 -n observability --create-namespace \
  --set mode=hosted --set hosted.adminTokenSecret=pulse0-admin --set hosted.collectorKeySecret=pulse0-collector-key
# create an organization (the response contains its admin key exactly once)
curl -X POST $URL/admin/orgs -H "Authorization: Bearer $ADMIN" -H 'content-type: application/json' \
  -d '{"name":"acme","daily_cap":5000000,"retention_hours":72}'
```
Customers open the UI, paste the organization admin key (Settings -> Access), and manage projects, keys and usage on the
**Organization** page. Exporters send `x-pulse0-key: <ingest key>` (or `Authorization: Bearer`), e.g.
`OTEL_EXPORTER_OTLP_HEADERS="x-pulse0-key=<key>"`.

**Where the data goes (be exact when you market this).**
Self-hosted keeps telemetry in the customer's own cluster. If the optional Claude assistant is enabled, snippets of query
results are sent to Anthropic. Alert webhooks and synthetic checks call the URLs the user configures. In hosted mode the
assistant is off unless the operator sets `hosted.allowAI` and `ai.*`, and webhooks/synthetics may only reach public
addresses (loopback, private ranges and cloud-metadata addresses are refused).

**Known limits of hosted mode (v1).** No self-service sign-up, billing or SSO: the operator creates organizations and hands
out the first admin key. One SQLite file per project on one volume (single replica) is simple and strongly isolated but does
not scale horizontally. Quota checks are per request, so one batch can overshoot the daily cap. The failed-auth throttle keys
on the client IP (set `PULSE0_TRUST_PROXY=1` only behind a proxy you control). DNS-rebinding between the outbound-URL check
and the request is not fully closed.

## Security checklist for production
- [ ] Set `PULSE0_TOKEN` to a strong secret
- [ ] Serve behind TLS (Ingress `tls:` or a reverse proxy)
- [ ] Restrict the Service/Ingress to your network
- [ ] Set a realistic `PULSE0_RETENTION_HOURS` and PVC size
- [ ] Schedule the backup job
- [ ] Pin image tags/digests (not `:latest`)
- [ ] Hosted mode: keep `PULSE0_ADMIN_TOKEN` in a Secret, put TLS in front, back up the whole `hosted.dataDir`
