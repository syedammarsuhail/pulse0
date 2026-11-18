> **Install or demo Pulse0:** see [DEMO-GUIDE.md](DEMO-GUIDE.md) and the latest package `pulse0-onprem-v10_16.tar.gz`.

# Pulse0 — OpenTelemetry-Native Observability Platform

Your own observability platform. Single OTLP endpoint for traces, logs, and metrics. Resource-centric UI. Zero code changes in your apps. Runs on any Kubernetes — **EKS, AKS, bare-metal, k3s, kind** — or just Docker. Covers every pillar of what Dash0 charges for, plus a **Service Health Score** feature they don't have.

## Install

Pick one. All three deploy the same product.

```bash
./deploy.sh              # auto-detect EKS / AKS / bare-metal from your kubectl context
./deploy.sh eks          # force EKS (ECR + EBS + ALB)
./deploy.sh aks          # force AKS (ACR + Azure Disk + ingress-nginx)
./deploy.sh baremetal    # force bare-metal (local-path/Longhorn + NodePort/Ingress)
```

Or just:
```bash
docker build -t pulse0 . && docker run -p 4318:4318 -v pulse0-data:/data pulse0
```

## What you get

| | |
|---|---|
| **Ingestion** | OTLP HTTP (`:4318`) — JSON + protobuf + gzip. Standard `/v1/{traces,logs,metrics}`. |
| **Resources** | Services ranked by RED (Rate / Errors / Duration) with sparklines. |
| **Health Score** | 0–100 per service with written reason — synthesizes error rate, p95 drift, log errors, synthetic failures. Unique to Pulse0. |
| **Service Map** | Auto-extracted from span parent/child + named peers (DBs, queues). |
| **Traces** | Trace list + waterfall, correlated logs, attribute drill-downs. |
| **Logs** | Full-text + `key=value` attribute filters, severity histogram, click → trace. |
| **Metrics** | Auto-discovered names; agg + group-by any OTel attribute. |
| **Alerting** | 7 signal types (error_rate, p95_latency, log_errors, metric_avg/max, synthetic_down, request_rate) with Slack-compatible webhooks. |
| **Synthetics** | HTTP + SSL-expiry + latency SLO + uptime % + history. |
| **SLOs** | Service-level objectives with attainment % and error-budget tracking. |
| **Dashboards** | Save your own panels on top of metric queries. |
| **K8s auto-instrument** | OTel Operator CR; one annotation → zero-code Python/Java/Node/.NET/Go/browser. |
| **In-cluster collector** | DaemonSet with Prometheus auto-scrape, host metrics, log tailing, cloud resource detection, tail sampling, memory limiter, retry queue. |

## Verification

**84 automated Python tests (plus Node layout tests in `tests/test_mapkit.js`), all passing**:
```bash
pip install -r requirements.txt pytest pytest-anyio httpx
python3 -m pytest tests/ -v
```
Covers every endpoint, every validation rule, every rejection case, plus the Service Health Score and SLO calculations. Runs in under a second.

## Docs

| File | What it covers |
|------|---------------|
| `INSTALL.md` | Docker, Kubernetes, bare process install paths + config reference. |
| `EKS-AKS.md` | How Pulse0 runs on EKS vs AKS, differences, quickstarts. |
| `PREFLIGHT.md` | Step-by-step cluster-test checklist with common first-run issues. |
| `ZERO-CODE-SETUP.md` | Language-by-language zero-code instrumentation guide (Python/Java/Node/.NET/Go/Angular). |
| `BETTER-THAN-DASH0.md` | Point-for-point comparison with the Dash0 10-minute-setup video. |
| `DASH0-TEARDOWN.md` | Reverse-engineered Dash0 architecture (ClickHouse, SignalControl, SignalStore). |
| `HARDENING.md` | Robustness roadmap tiered by effort. |
| `STORAGE-AND-COST.md` | Where data lives, retention, cost per cluster size. |
| `FEATURES-VS-DASH0.md` | Honest feature parity table. |
| `test-apps/README.md` | 4-language zero-code test kit (`run-all.sh`). |

## Three K8s flavors supported

| | EKS | AKS | Bare-metal / k3s |
|---|---|---|---|
| Registry | ECR | ACR | local load (kind/k3s) or private |
| Storage | EBS → `gp3` | Azure Disk → `managed-csi-premium` | `local-path` / Longhorn / OpenEBS / Rook |
| Expose UI | ALB Ingress | AGIC / ingress-nginx | NodePort / MetalLB / ingress-nginx |
| Values file | `values-eks.yaml` | `values-aks.yaml` | `values-baremetal.yaml` |

Same Helm chart. Same code. The `deploy.sh` script detects which flavor you're on and does the right thing.

## Configuration

| Env / Helm key | Default | Purpose |
|---|---|---|
| `PULSE0_PORT` / `service.port` | `4318` | OTLP + UI port |
| `PULSE0_DB` | `/data/pulse0.db` | SQLite path |
| `PULSE0_RETENTION_HOURS` / `env.PULSE0_RETENTION_HOURS` | `72` | Signal retention |
| `PULSE0_TOKEN` / `auth.token` | *(empty)* | Bearer token on `/v1/*` + `/api/*` |
| `PULSE0_MODE` / `mode` | `selfhosted` | `selfhosted` or `hosted` (multi-tenant), see below |
| `PULSE0_ADMIN_TOKEN` / `hosted.adminTokenSecret` | *(none)* | Platform admin token, required in hosted mode |
| `PULSE0_DATA_DIR` / `hosted.dataDir` | `./tenants` | Where hosted mode keeps one SQLite file per project |
| `PULSE0_DEFAULT_DAILY_CAP` | `5000000` | Default per-organization daily ingest cap in hosted mode (0 = unlimited) |
| `collector.traceSamplingPercent` | `20` | Tail-sample this % of successful traces (errors/slow always kept) |

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

## Status

- ✅ 84/84 Python tests passing
- ✅ Helm chart renders cleanly, all YAML valid
- ✅ Live OTLP round-trip verified (ingest → query → health score)
- ✅ EKS, AKS, bare-metal overlays included and validated
- ✅ Zero-code instrumentation tested end-to-end for Python, Node, Java, Angular
