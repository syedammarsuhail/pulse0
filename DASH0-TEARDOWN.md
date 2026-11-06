# Dash0 Architecture Teardown (Reverse-Engineered)

Compiled from Dash0's own engineering writeups (the ClickHouse blog and their SignalStore page) plus their public docs. This is how Dash0 actually works under the hood, and where Pulse0 sits relative to it.

## The big picture

Dash0 is three layers:

```
   apps / collectors (OTLP)
          │
   ┌──────▼───────┐
   │ SignalControl │   ← ingestion gateway: spam filter, tail sampling,
   └──────┬───────┘      RED-metric computation, signal→metric, aggregation
          │
   ┌──────▼───────┐
   │  SignalStore  │   ← ClickHouse (all telemetry) + PostgreSQL (settings only)
   └──────┬───────┘
          │
   dashboards · PromQL · SQL · NL query · MCP server · CLI · API
```

### 1. SignalControl — the ingestion gateway

Everything enters through one OpenTelemetry-native path. Before anything is stored, SignalControl does:

- **Spam filtering** — drop known-noise telemetry at ingest (you don't pay to store it).
- **Distributed tail sampling** — decide whether to keep a trace *after* seeing all its spans (so you keep the slow/errored ones, drop the boring ones).
- **RED metrics from 100% of traffic** — Rate/Errors/Duration are computed from every request *before* sampling, so your dashboards are accurate even when traces are sampled.
- **Signal-to-metrics conversion & time-series aggregation** — turn high-cardinality logs/spans into cheap pre-aggregated metrics.

**Key insight:** you only pay to store what survives SignalControl. This is their cost lever.

### 2. SignalStore — the storage engine

- **ClickHouse** holds *all* observability data (logs, metrics, traces, profiles, events, RUM). **PostgreSQL** holds only customer settings. They migrated here from Instana's old Cassandra + Elasticsearch stack.
- **"Resource as Context" is literally the schema.** The primary key for spans and logs is `ResourceHash` (a hash of *all* resource attributes) then `Timestamp`, partitioned by day. The standard OTel ClickHouse exporter keys on `service.name`; Dash0 deliberately keys on the whole resource so everything about one pod/host/service clusters together on disk. That's why their UI is resource-centric — the storage is too.
- **Metrics** live in separate tables per metric type, join-keyed on `MetricHash`, ordered `(MetricHash, TimeUnix)`, partitioned by day, with Delta+LZ4 timestamp codecs. Metric *metadata* is an `AggregatingMergeTree` keyed `(TimeUnix, MetricName, MetricHash)` storing each series' attributes once with `FirstSeen`/`LastSeen` so they can filter series by time range.
- **Frequently-queried attributes are promoted to materialized columns.** Bloom filters on `mapKeys(Attributes)` accelerate key-existence checks. A `minmax` index on `Timestamp` was their single biggest query win. Every query carries a time range.
- **Tiered storage:** hot data (~1–2 days) on local SSD, cold data on S3, with a local cache for S3 reads. A TTL rule moves data to cold after 25h and deletes after 13 months. Metrics are kept 1yr+ for seasonal analysis; logs/spans are shorter-lived.
- **Sampling for triage:** a fast sampled query returns preliminary results while the full query runs in the background. The sort key `ResourceHash, toStartOfHour(Timestamp), xxh3(SpanId)` makes sampling cheap.
- **Topology:** multiple replicas, single shard (sharding complicates migrations — each shard needs its own run).

### 3. Open query & access surfaces

- **PromQL** for metrics (the real engine, not an emulation) — you can reuse Prometheus alert rules.
- **SQL** for logs and events.
- **Natural-language** querying on top.
- **Perses** for dashboards (open format — import/export Grafana & Perses, no lock-in).
- Access for humans (UI), agents (**MCP server**), automation (**CLI**), and code (**API**) — all reading the same store.
- **Terraform provider** manages dashboards, check rules, recording rules, saved views, synthetic checks, notification channels as code.

## How Pulse0 maps to this

| Dash0 | Pulse0 today | Same idea? |
|-------|--------------|------------|
| SignalControl gateway | OTLP endpoints + inline ingest | ⚠️ No tail sampling/spam filter yet (roadmap) |
| RED from 100% traffic | RED computed from stored spans | ✅ (we store all, so it's accurate) |
| ClickHouse, ResourceHash key | SQLite, `service` + indexed time | ✅ same resource-centric *model*, smaller engine |
| Per-type metric tables | one `metrics` table | ✅ simpler, same query shape |
| Tiered hot/cold + S3 | single file + TTL prune | ⚠️ no S3 tiering (roadmap) |
| PromQL engine | query builder | ⚠️ partial |
| Perses dashboards | fixed views | ❌ roadmap |
| MCP / CLI / Terraform | REST API | ⚠️ API only |

**Takeaway:** Pulse0 copies Dash0's *design philosophy* (OTel-native, single store, resource-centric, open) at a scale that runs on one small VM. Dash0's heavy lifting is the ClickHouse tiered backend + SignalControl + PromQL/Perses/Agent0. Those are exactly the items on Pulse0's hardening roadmap (see `HARDENING.md`).

## Sources
- [Building an Observability Solution with ClickHouse at Dash0](https://clickhouse.com/blog/building-an-observability-solution-with-clickhouse-at-dash0)
- [SignalStore: OpenTelemetry-Native Data Platform (Dash0)](https://www.dash0.com/signalstore)
- [Dash0 Documentation](https://www.dash0.com/docs/dash0)
