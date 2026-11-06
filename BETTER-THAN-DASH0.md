# What Makes Pulse0 Better Than What Dash0 Shows

Reference: the video — *"Setup Observability for any application in 10 minutes | No code + No configuration"* (Abhishek.Veeramalla, Nov 5, 2026).

The video pitches Dash0 on four things: auto-instrumentation, managed OpenTelemetry collection, Kubernetes + Prometheus auto-scraping, and SignalControl Edge (filter/sample in-cluster). Pulse0 covers every one of those and adds **three things Dash0 doesn't show**.

## Parity with the video, point for point

| Feature from video | Pulse0 |
|---|---|
| Auto-instrumentation (no code change) | ✅ OTel Operator CR shipped in Helm chart. Verified end-to-end for Python/Node/Java/Angular. |
| Managed collector in-cluster | ✅ Collector DaemonSet with RBAC + cloud resource-detection (`eks, aks, ec2, azure, gcp`). |
| K8s infra metrics + Prometheus scraping | ✅ Added `prometheus` receiver with pod-annotation discovery — any pod with `prometheus.io/scrape: true` is scraped automatically. |
| SignalControl Edge (filter/sample) | ✅ `tail_sampling` (keep every error + every slow trace + N% success), `filter/noise` (drop health-check logs), `memory_limiter`, retry + sending queue. |

## Three things Pulse0 does that Dash0 doesn't (in that demo)

### 1. Service Health Score — automatic "what's broken?"
Every service gets a **0–100 score** computed from 4 live signals (error rate, p95 latency drift vs the prior window, log error rate, synthetic-check failures). Each component contributes up to a bounded weight (errors up to 40, latency drift up to 25, log errors up to 20, synthetics up to 15), and the response includes a human-readable `reason`. No alert rule configuration needed. Dash0 and most APMs show raw numbers and leave the synthesis to you.

Endpoint: `GET /api/services/{svc}/health?minutes=60` returns `{score, state, reason, signals}`. Surfaced as a pill in the Resources table (green/amber/red).

### 2. Strict input validation on every write endpoint
Every POST is Pydantic-validated with `extra="forbid"` — unknown fields are rejected, bounds are enforced, URL fields check scheme, operator and signal enums are closed sets. Errors come back as `400` with structured `{error, details, request_id}` not stack traces. Every request has an `x-request-id` header.

Dash0's API is strict too — the difference is **Pulse0 ships a test suite that proves it works** (next section).

### 3. SLO tracking
Create a service-level objective (`service`, `objective_pct`, `latency_ms`, `window_days`), and Pulse0 computes **attainment** and **error-budget-used %** on every read, from raw spans. This is the one thing engineering leadership actually looks at, and competitors charge extra for.

Endpoints: `GET/POST/DELETE /api/slos`.

## Proper validation — a real test suite

**28 automated tests, 28 passing**, exercising every API endpoint end-to-end via the ASGI transport (no port needed, runs in CI):

```
tests/test_pulse0.py::test_health_endpoints                   PASSED
tests/test_pulse0.py::test_request_id_header                  PASSED
tests/test_pulse0.py::test_otlp_rejects_malformed             PASSED
tests/test_pulse0.py::test_otlp_handles_gzip                  PASSED
tests/test_pulse0.py::test_services_red_metrics               PASSED
tests/test_pulse0.py::test_service_timeseries                 PASSED
tests/test_pulse0.py::test_operations                         PASSED
tests/test_pulse0.py::test_service_map_edges                  PASSED
tests/test_pulse0.py::test_trace_search_and_detail            PASSED
tests/test_pulse0.py::test_trace_filters                      PASSED
tests/test_pulse0.py::test_log_search                         PASSED
tests/test_pulse0.py::test_metrics_discovery_and_query        PASSED
tests/test_pulse0.py::test_alert_create_valid                 PASSED
tests/test_pulse0.py::test_alert_rejects_unknown_signal       PASSED
tests/test_pulse0.py::test_alert_rejects_unknown_op           PASSED
tests/test_pulse0.py::test_alert_rejects_missing_fields       PASSED
tests/test_pulse0.py::test_alert_rejects_bad_webhook          PASSED
tests/test_pulse0.py::test_alert_rejects_extra_fields         PASSED
tests/test_pulse0.py::test_alert_delete_404                   PASSED
tests/test_pulse0.py::test_alert_fires_on_threshold           PASSED
tests/test_pulse0.py::test_synthetic_create_and_run           PASSED
tests/test_pulse0.py::test_synthetic_rejects_bad_url          PASSED
tests/test_pulse0.py::test_synthetic_rejects_bad_interval     PASSED
tests/test_pulse0.py::test_dashboard_crud                     PASSED
tests/test_pulse0.py::test_dashboard_validation               PASSED
tests/test_pulse0.py::test_slo_crud_and_attainment            PASSED
tests/test_pulse0.py::test_slo_validation                     PASSED
tests/test_pulse0.py::test_service_health_score               PASSED
======================== 28 passed in 0.41s ========================
```

Run them yourself:
```bash
pip install -r requirements.txt pytest pytest-anyio httpx
python3 -m pytest tests/ -v
```

The suite also caught two real bugs during development, both fixed:
- Log attribute filters didn't match numeric values (int vs string JSON comparison)
- Request-ID middleware swallowed Pydantic validation errors and returned 500s instead of 400s

That's the point of tests — they find things eyeballs don't.

## Collector hardening you can't see in the video

Added to the shipped collector config, which Dash0's demo skips over:
- **`memory_limiter`** — refuses new data at 85% RAM instead of OOMKill under burst.
- **`sending_queue`** — 5000-item buffer so a Pulse0 restart doesn't drop data.
- **`retry_on_failure`** — exponential backoff up to 30s.
- **`filter/noise`** — drops kube-system health-check logs before they're billed as storage.
- **`tail_sampling`** keeps every errored trace, every slow trace (>1s), and a tunable percentage of successes (`collector.traceSamplingPercent`, default 20).

All of these are real production concerns that a 10-minute demo video never has time to address.
