# Pulse0 vs Dash0 — Feature Parity

Based on Dash0's six core capabilities (Metrics, Logs, Traces, Resources, Alerting, Dashboards)
plus its cross-cutting features. Honest status — what's done, what's partial, what's not built.

| Dash0 capability | Pulse0 | Notes |
|------------------|:------:|-------|
| **OpenTelemetry-native ingest** (OTLP) | ✅ | HTTP, JSON + protobuf, gzip. Standard `/v1/{traces,logs,metrics}`. |
| **Resource-centric model** | ✅ | Services ranked by RED; full OTel resource attributes per service. |
| **Metrics** | ✅ | Auto-discovery of metric names, agg (avg/max/min/sum), group-by any attribute. |
| **Logs** | ✅ | Full-text + `key=value` attribute filters, severity histogram, trace correlation. |
| **Traces** | ✅ | Trace list + waterfall, span attributes, cross-service, correlated logs. |
| **Service map** | ✅ | Auto-derived from span parent/child + named peers (DBs, queues). |
| **Alerting** | ✅ | 7 signal types on logs/metrics/traces/synthetics; webhook (Slack/PagerDuty-compatible). |
| **Synthetic monitoring** | ✅ | HTTP checks, SSL-expiry, latency SLO, uptime %, history. |
| **Kubernetes monitoring** | ✅ | Helm chart + bundled OTel Collector DaemonSet (pod logs, node & cluster metrics). |
| **Zero-code auto-instrumentation** | ✅ | OTel Operator CR; Python/Java/Node/.NET/Go/browser. Tested end-to-end. |
| **Website / RUM monitoring** | ✅ | Browser SDK bundle; documentLoad + fetch/XHR spans. |
| **PromQL** | ⚠️ Partial | Query builder covers common cases; full PromQL engine not embedded (Dash0 uses real Prometheus). |
| **Perses/Grafana dashboard import** | ❌ | Pulse0 has fixed purpose-built views, not a drag-drop dashboard builder yet. |
| **400+ prebuilt alert templates** | ❌ | You define rules directly; no template library bundled. |
| **AI troubleshooting agent (Agent0)** | ❌ | Not built — this is Dash0's proprietary LLM layer. |
| **Profiles / RUM session replay** | ❌ | Out of scope for v1. |
| **Terraform provider / config-as-code** | ⚠️ Partial | Alerts & synthetics are API-driven (scriptable); no dedicated TF provider. |

## Honest summary

Pulse0 **covers all six of Dash0's core product pillars** (metrics, logs, traces, resources,
alerting, dashboards-as-views) and the things you specifically asked for — zero-code
instrumentation across Python/Java/.NET/Angular, Helm install, a working demo, self-hosted
storage, and flat cost.

What Dash0 has that Pulse0 doesn't, by design, is the **heavy proprietary layer**: a full
embedded PromQL engine, a Perses drag-and-drop dashboard editor, a 400-template alert library,
and their AI agent "Agent0". Those are large efforts and are the main reasons their product
is a paid SaaS. If you want any of them next, the most valuable additions in order would be:

1. **Real PromQL** — embed Prometheus' own engine against the metrics table.
2. **Custom dashboards** — a save-your-own-panel builder (Perses-compatible JSON).
3. **Alert template library** — ship the Awesome Prometheus Alerts set as presets.
