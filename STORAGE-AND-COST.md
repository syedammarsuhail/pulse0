# Pulse0 — Data Storage & Cost

## Where is the data stored?

**All telemetry lives in a single SQLite database file that *you* own — nothing leaves your infrastructure.**

- **Local / Docker:** `pulse0.db` (set by `PULSE0_DB`, defaults to `/data/pulse0.db` in the container). Mount a volume and it persists across restarts — verified: services survive a full server restart.
- **Kubernetes:** a `PersistentVolumeClaim` (default 10Gi, configurable in `values.yaml`) mounted at `/data`. Your cloud's block storage (EBS, Azure Disk, GCP PD) backs it.
- **Tables:** `spans`, `logs`, `metrics`, `alert_rules`, `alert_events`, `synthetics`, `synthetic_runs`. Indexed by time and service.
- **Retention:** old data is auto-pruned after `PULSE0_RETENTION_HOURS` (default 72h). Raise it for longer history; disk is the only limit.

There is **no third-party SaaS, no external account, no egress**. This is the opposite of Dash0/Datadog where your data sits on the vendor's servers and you pay per GB ingested.

### Scaling the storage engine

SQLite comfortably handles a single team / small-to-mid cluster (millions of spans). If you outgrow it, the storage layer is isolated in `server.py` (`db()` / `q()` helpers) — swapping to **ClickHouse** or **PostgreSQL + TimescaleDB** is the natural next step and the OTLP ingest code doesn't change. For reference, that's what large OTel backends (SigNoz, Uptrace) use under the hood.

## What does the whole thing cost?

Pulse0 itself is **free** — it's your code, built on open-source (OpenTelemetry, FastAPI, SQLite). Your only cost is the infrastructure it runs on. There are **no per-host, per-GB, or per-seat license fees**.

### Typical self-hosted cost (AWS, Toronto `ca-central-1` pricing ballpark)

| Deployment size | Compute | Storage | Est. monthly |
|-----------------|---------|---------|--------------|
| **Dev / personal** (1 pod + collector) | t4g.small (2 vCPU/2GB) | 10Gi gp3 | **~$15–20** |
| **Team / one cluster** (1 pod + DaemonSet collector, ~20 services) | t4g.medium (2 vCPU/4GB) | 50Gi gp3 | **~$35–50** |
| **Larger** (HA collector, 100+ services, 30-day retention) | t4g.large + 200Gi gp3 | | **~$90–130** |

The OpenTelemetry Collector DaemonSet adds a small per-node footprint (~50–130m CPU, 128–512Mi RAM — set in `values.yaml`).

### How that compares

For the same workload, commercial APM/observability vendors bill by data volume and hosts:

| Vendor | Pricing model | ~100 services, moderate volume |
|--------|--------------|-------------------------------|
| **Datadog** | per host + per GB ingested + per analyzed span | **$1,000s / mo** |
| **Dash0** | per GB ingested (transparent, but still usage-based) | **$100s–1,000s / mo** |
| **Pulse0 (this)** | your infra only | **~$35–130 / mo flat** |

Because Pulse0 is OpenTelemetry-native, you are **not locked in** — the exact same instrumentation can point at Dash0, Grafana, or any OTLP backend later. You're paying for a VM and a disk, not for your own data.

### Cost controls built in
- `PULSE0_RETENTION_HOURS` — shorter retention = less disk.
- Collector `sampler` ratio (`autoInstrumentation.samplingRatio`) — sample traces at high volume to cut storage.
- Single binary + SQLite = no database cluster to run or pay for.
