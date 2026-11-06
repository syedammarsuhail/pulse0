# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What Pulse0 is

A self-hosted, OpenTelemetry-native observability platform (Dash0 alternative): one OTLP HTTP endpoint (`:4318`) ingests traces/logs/metrics into SQLite and serves a vanilla-JS web UI plus a JSON API. Features: services/RED + health score, service map, traces, logs, metrics explorer, dashboards builder, alerts (with template library), SLOs, synthetics, rule-based Live Insights, an "Ask Pulse0" assistant (Claude-backed when a key is configured, rule-based fallback), and an optional multi-tenant hosted mode.

The repo root also holds `pulse0.tar.gz` (the original archive, already extracted here). It is not a git repo.

## Commands

```bash
pip install -r requirements.txt pytest pytest-anyio httpx
python -m pytest tests/ -q                       # 89 tests; in-process via httpx.ASGITransport, no port needed
python -m pytest tests/test_hosted.py -q         # hosted/multi-tenant suite
python -m pytest tests/ -k <name> -xvs           # single test
node tests/test_mapkit.js                        # service-map layout engine tests (no deps)
python server.py                                 # run locally on :4318
helm lint helm/pulse0 && helm template p helm/pulse0 -f helm/pulse0/values-eks.yaml --set auth.token=x
```

Env: `PULSE0_PORT`, `PULSE0_DB`, `PULSE0_RETENTION_HOURS`, `PULSE0_TOKEN` (bearer on `/v1/*` + `/api/*`), `PULSE0_MODE=selfhosted|hosted`, `PULSE0_ADMIN_TOKEN` + `PULSE0_DATA_DIR` (hosted), `ANTHROPIC_API_KEY` / `PULSE0_AI_MODEL` (assistant). Tests set `PULSE0_DB=/tmp/pulse0_test.db` (POSIX path; fine under Git Bash).

## Architecture

- **`server.py`** (single-file FastAPI, ~2000 lines): SQLite helpers (`db()` resolves the DB path from a contextvar so hosted mode gets one DB file per project) → OTLP decoding (JSON/protobuf/gzip) → middleware (request-id, auth, CORS echoing the request origin with credentials because `sendBeacon` is credentialed) → `/v1/*` ingest → `/api/*` queries (services, map + `/api/map/nodes`, traces, logs + facets, metrics, health, SLOs, dashboards, alerts + templates, synthetics, `/api/insights`, `/api/home`, `/api/search`, `/api/investigate`, `/api/assistant`) → `/admin/*` and `/org/*` (hosted only; 404 in selfhosted) → background worker (alerts, synthetics, retention; iterates all tenants in hosted mode).
- **Selfhosted is the default and must stay byte-compatible**; hosted adds orgs/projects/hashed API keys/quotas. Webhooks and synthetics have an SSRF guard in hosted mode only.
- **Assistant:** `POST /api/assistant` uses the `anthropic` SDK with 10 read-only tools over the same query code; no key means a 503 with `fallback:/api/investigate`. `anthropic` is listed in both `requirements.txt` and the Dockerfile's inline `pip install`; keep them in sync.
- **`static/`**: no build step. `index.html` loads `core.js` (router, helpers, charts), `views.js`/`views2.js` (pages), `map.js` + `mapkit.js` (service map; mapkit is the pure layout engine, tested by Node), `assistant.js`, `tenancy.js`. The server sends `Cache-Control: no-cache` on `/` and `/static/*`.
- **`helm/pulse0/`**: StatefulSet (persistence comes from `volumeClaimTemplates`; there is no standalone PVC), service, optional ingress, an OTel collector DaemonSet (hostmetrics, kubeletstats, prometheus, filelog; the collector's own logs are excluded) plus a single-replica cluster-collector Deployment for the `k8s_cluster` receiver (needs the `autoscaling/horizontalpodautoscalers` RBAC rule or it terminates after its 10-minute sync timeout). `instrumentation.yaml` renders only when the OTel Operator CRD exists. Overlays: `values-eks|aks|baremetal.yaml`. `collector.traceSamplingPercent` defaults to 100; below 100 skews stored error rates.
- **`test-apps/`**: per-language apps (`k8s/` for Python/Node/Java/.NET/Go/Angular) and `shop/` (polyglot connected shop + configurable load generator with `build.sh`/`deploy.sh`). `demo/` is the original two-service demo.
- `tests/MAP_FREEZE.md` documents an unreproduced browser-tab freeze (likely dev-PC memory pressure, not an app loop).

## Gotchas learned the hard way

- `helm upgrade --reuse-values` ignores new chart defaults and can render empty required values; pass the full value set explicitly when upgrading across chart changes.
- Pin `--kube-context` on every kubectl/helm call when other clusters exist in kubeconfig; never run in-cluster cleanup against the current context.
- CodeBuild source zips must be made with Windows `tar -a -c -f x.zip` (`/c/Windows/System32/tar.exe` from Git Bash), not `Compress-Archive` (backslash paths) or GNU tar. Docker Hub rate-limits CodeBuild; use `public.ecr.aws/docker/library/...` base images. In Git Bash set `MSYS_NO_PATHCONV=1` for `aws` log-group names and S3 paths.
- After a UI redeploy, browsers may need one hard reload (Ctrl+Shift+R).
- Known open items: collector `filelog` logs `reader.go:148 process: %w` errors for some pods (cause not isolated); the cluster-collector split and hosted mode have not been run on a live cluster; the Claude assistant has not been tried with a real key.
