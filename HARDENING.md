# Making Pulse0 Robust — Hardening Roadmap

Pulse0 today is a single FastAPI process + SQLite. That's perfect for a team or a single cluster and runs on a ~$20/mo VM. This document is the path from "works on my cluster" to "I'd run this for a paying client," mapped against what Dash0 actually does (see `DASH0-TEARDOWN.md`).

Ordered by value-for-effort.

## Tier 1 — do these before any client runs it (days)

1. **Turn on the auth token.** Set `PULSE0_TOKEN` (Helm `auth.token`). Without it, anyone who reaches the port can read all telemetry and POST data. Already built — just enable it. Put TLS in front via the Ingress (`ingress.tls`).
2. **Health probes** — `/healthz` (liveness) and `/readyz` (readiness, checks the DB) ship now and the Helm chart wires them up. `/api/stats` exposes row counts so you can monitor Pulse0 with Pulse0.
3. **Resource limits & a PVC** — already in the chart (`resources`, `persistence`). Confirm the PVC's StorageClass exists in the client's cluster.
4. **Back up the SQLite file.** It's one file. A nightly `sqlite3 pulse0.db ".backup /backup/pulse0-$(date +%F).db"` CronJob, or snapshot the PVC. This is your entire data-protection story — simple and real.
5. **Pin image versions.** Replace `:latest` on the collector and auto-instrumentation images with digests so a client's install is reproducible.

## Tier 2 — robustness under real load (1–2 weeks)

6. **Decouple ingest from write with a queue.** Today ingest parses and writes to SQLite inline on the request. Under burst load that blocks. Put an in-process `queue.Queue` (or Redis/NATS) between the OTLP handler and a writer thread that batches inserts every ~1s. This is the single biggest reliability win and mirrors how the OTel Collector batches before ClickHouse.
7. **Add a gRPC OTLP endpoint (`:4317`).** Many collectors/SDKs default to gRPC. Today Pulse0 is HTTP-only (`:4318`); the bundled collector bridges gRPC→HTTP, but native gRPC removes that hop. Add `grpcio` + the OTLP gRPC service.
8. **Backpressure & limits.** Cap request body size, reject/429 when the write queue is full, and add `PRAGMA busy_timeout`. Protects the box from a misconfigured firehose.
9. **Cardinality guards.** A runaway label (e.g. a UUID in a metric attribute) explodes storage. Add a per-metric series cap and drop/aggregate beyond it — Dash0 does this in SignalControl.
10. **Structured internal logging + self-metrics.** Emit Pulse0's own ingest rate, queue depth, write latency as OTLP back into itself.

## Tier 3 — scale past one node (the ClickHouse path)

This is where you follow Dash0's actual architecture. Do it only when SQLite is genuinely the bottleneck (millions of spans/day, multi-team).

11. **Swap the storage layer to ClickHouse.** The storage code is isolated in `server.py` behind `db()`/`q()`. Replace those with a ClickHouse client. Adopt Dash0's schema decisions directly:
    - Primary key `(ResourceHash, Timestamp)` for spans/logs, partitioned by day.
    - Separate per-type metric tables keyed on `MetricHash`, Delta+LZ4 codecs.
    - `minmax` index on `Timestamp`, bloom filters on `mapKeys(Attributes)`.
    - Promote hot attributes to materialized columns.
    Reference implementations: **SigNoz** and **Uptrace** are open-source OTel backends on ClickHouse you can learn from.
12. **Tiered hot/cold storage.** ClickHouse TTL `TO VOLUME 's3'` after ~24h, delete after your retention. Local SSD hot, S3 cold + cache — exactly Dash0's setup. Cuts storage cost by an order of magnitude.
13. **Tail sampling + RED-from-100%.** Move sampling to an OTel Collector `tail_sampling` processor in front of Pulse0, and compute RED metrics with the `spanmetrics` connector so rates stay accurate under sampling. This is SignalControl, assembled from open-source Collector components.
14. **Stateless API replicas.** Once storage is ClickHouse (not local SQLite), the API tier becomes stateless — run N replicas behind the Service and drop the StatefulSet's single-replica constraint.

## Tier 4 — product features (parity with Dash0's premium layer)

15. **Real PromQL** — embed Prometheus' engine (or front metrics with VictoriaMetrics) so clients reuse existing Prometheus alert rules and the 400+ Awesome Prometheus Alerts templates.
16. **Save-your-own dashboards** in Perses JSON (import/export Grafana).
17. **MCP server + CLI + Terraform provider** for config-as-code, matching Dash0's automation surfaces.

## What NOT to over-build

- Don't shard early. Dash0 runs a **single shard** with replicas; sharding complicates migrations. SQLite→ClickHouse-single-shard covers a very large range first.
- Don't add a message bus (Kafka) until the in-process queue is proven insufficient.
- Keep PostgreSQL out until you have multi-tenant settings/users to store — Dash0 uses it *only* for settings, not telemetry.

## Current robustness checklist (what's already true)

- ✅ OTLP JSON + protobuf + gzip ingest, with 400 on bad payloads (won't crash on malformed data)
- ✅ WAL-mode SQLite (concurrent reads during writes), thread-safe access
- ✅ Automatic retention pruning, background alert + synthetic workers isolated with try/except
- ✅ CORS for browser RUM, optional bearer-token auth
- ✅ Liveness/readiness/stats endpoints, persistent volume, resource limits
- ✅ Data survives restart (verified)
