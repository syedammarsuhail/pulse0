# Pulse0 Pricing & Packaging

**Your Datadog bill, minus the surprise. Your data, on your infra, one flat price.**

Pulse0 is a self-hosted, OpenTelemetry-native observability platform — traces, logs,
metrics, dashboards, alerts, SLOs, synthetics, a service map, and the "Ask Pulse0"
assistant — that runs inside *your own* Kubernetes cluster or bare-metal host. Your
telemetry never leaves your environment, and you are billed a flat per-cluster
subscription: **no per-host metering, no per-GB ingest fees, no bill shock.**

> **⚠️ All Pulse0 prices on this page are a starting proposal, not a committed price
> list.** They are round numbers chosen to anchor a conversation and should be tuned
> against real sales conversations, cost-to-serve, and win/loss data before launch.
> Competitor figures are cited with sources and access dates at the end of this
> document; where a number is not publicly published we say so rather than inventing it.

---

## The three tiers (proposed)

| | **Community** | **Pro** | **Enterprise** |
|---|---|---|---|
| **Price (proposed)** | **Free, forever** | **~$249 / cluster / month** flat | **From ~$1,500 / month** |
| **Billing** | — | Monthly or annual, signed license key | Annual contract |
| **Hosts / services / data** | ~10 services, 7-day retention | **Unlimited** hosts, services & data | Unlimited |
| **Deployment** | Self-hosted | Self-hosted | Self-hosted + Sovereign / air-gapped mode |
| **Traces, logs, metrics** | ✓ | ✓ | ✓ |
| **Service map, RED, health score** | ✓ | ✓ | ✓ |
| **Dashboards** | ✓ | ✓ | ✓ |
| **Alerts, SLOs, synthetics** | Basic | ✓ Full + template library | ✓ |
| **Ask Pulse0 assistant** | Rule-based fallback only | ✓ Claude-backed (your API key) | ✓ |
| **SSO / RBAC (viewer/editor/admin)** | — | ✓ | ✓ + audit export |
| **Retention** | 7 days | Configurable (disk-bound) | Configurable + **S3 cold-storage tier** |
| **Data-residency mode** | Self-hosted | Self-hosted | **Sovereign / air-gapped**, no outbound calls |
| **Support** | Community (GitHub) | Business-hours email | **Priority SLA** |
| **Trial** | — | **14-day Pro auto-trial** on every install | — |

### Rationale for each tier

- **Community — Free, forever.** The on-ramp and the top-of-funnel. It is a genuinely
  useful product for a hobby project or a single small service, which earns trust and
  drives word-of-mouth in the startup/OSS community. The ~10-service / 7-day-retention
  caps are deliberately the point where a *growing* team feels the ceiling and upgrades —
  not so tight that the free tier feels like crippleware.
- **Pro — ~$249/cluster/month flat.** The core commercial tier, priced as a no-brainer
  against a competitor bill that routinely runs into the **thousands** per month for the
  same workload (see the comparison below). A single flat number is the entire pitch:
  finance can forecast it, and it never moves when traffic spikes. $249 is low enough to
  clear a startup's monthly-SaaS approval threshold without a procurement cycle, and the
  14-day auto-trial means every install is already a Pro evaluation. Tune upward only if
  win-rate shows you are leaving money on the table.
- **Enterprise — from ~$1,500/month.** Targets regulated, security-sensitive, or
  air-gapped buyers (fintech, health, gov, defense-adjacent). The value here is not more
  dashboards — it is **Sovereign mode** (zero outbound calls, deployable in a fully
  disconnected network), S3 cold-storage for long retention at low cost, audit export for
  compliance, and a contractual SLA. "From" signals custom scoping; these deals are
  sold, not self-served.

---

## How you're charged

- **Flat per-cluster subscription.** One price covers a whole cluster — every host, pod,
  service, span, log line, and metric point inside it. You are **not** metered on hosts,
  not metered on GB ingested, and not metered on indexed events.
- **Delivered as a signed license key.** Pro/Enterprise entitlements unlock via a signed
  license key you drop into the install (Helm value / env var). The key encodes your tier
  and expiry; no phone-home is required for Sovereign/Enterprise deployments.
- **Billed monthly or annual.** Pay monthly for flexibility or annually for a discount
  (annual discount to be set). Enterprise is an annual contract.
- **14-day Pro trial, automatic.** Every fresh install (and every Postgres-backed install)
  starts a 14-day Pro trial with no key and no card. At day 14 it degrades gracefully to
  Community — your data and your deployment stay exactly where they are.

### Why flat beats metered — the bill-shock problem

Every major SaaS competitor meters on at least one axis that *grows with your success*:

- **Per-host:** Datadog Infrastructure Pro lists at **$15/host/mo** (annual) / $18 on-demand,
  and is billed on a **high-water mark** — one bursty autoscaling event can set the month's
  bill. APM adds **$31/host/mo** on top (with infra attached). Splunk Observability lists
  Infrastructure at **$15/host/mo** and the App & Infrastructure bundle at **~$60/host/mo**.
- **Per-GB / per-event:** Datadog Log Management charges **$0.10/GB just to ingest** — on
  *every byte you send, indexed or not* — then **$1.70 per million events** to index at
  15-day retention. Splunk Cloud's legacy log model runs roughly **$150–$200/GB/day**.
  New Relic meters **all** signals into one bucket: 100 GB/mo free, then **$0.40/GB**
  ($0.60/GB for Data Plus). Grafana Cloud bills usage above its free tier
  (~$0.45/GB ingest for logs/traces; ~$6.50 per 1,000 active metric series).
- **Per-user:** New Relic full-platform users list around **$349/user/mo** (annual) up to
  **~$419** pay-as-you-go.

The result is a bill that is impossible to predict and that *punishes you for growing* —
more traffic, more hosts, a noisy log burst, or one extra teammate all move the number.
Pulse0's flat per-cluster price removes every one of those meters.

---

## Honest cost comparison

> Competitor numbers below are **list prices** from the sources cited at the end
> (accessed 14 Nov 2026). Real bills are often higher after custom metrics, RUM,
> synthetics, and retention upgrades; enterprise rates are frequently negotiated. We use
> list prices to keep the comparison conservative and verifiable.

### Worked example: a 50-host startup, modest telemetry

Assume a growing startup with **50 hosts**, wanting infrastructure monitoring **and**
distributed tracing (APM), plus a modest **500 GB/month of logs** with **100M events
indexed** at 15-day retention.

**Datadog (list, annual commitment):**

| Line item | Rate | Qty | Monthly |
|---|---|---|---|
| Infrastructure Pro | $15 / host | 50 | $750 |
| APM (infra-attached) | $31 / host | 50 | $1,550 |
| Log ingest | $0.10 / GB | 500 GB | $50 |
| Log indexing (15-day) | $1.70 / M events | 100 M | $170 |
| **Total** | | | **≈ $2,520 / month** |

That is **~$30,000/year**, before RUM, custom metrics, synthetics, or a single
autoscaling spike — and it climbs every time the fleet or traffic grows.

**Pulse0 Pro:** **$249 / month flat**, unlimited hosts and data, SSO/RBAC, alerts, SLOs,
synthetics and Ask Pulse0 included — on the startup's own cluster.

> **≈ $2,520/mo (Datadog list) vs. $249/mo (Pulse0 Pro flat)** for the same 50-host
> workload — roughly a **10× difference**, and the gap *widens* as the fleet grows,
> because Pulse0's price doesn't move.

### Order-of-magnitude, other vendors (same 50-host shop, illustrative)

- **Splunk Observability:** App & Infrastructure bundle at ~$60/host/mo × 50 ≈
  **$3,000/mo** for hosts alone (logs priced separately).
- **New Relic:** usage-based — hard to pin without a data-volume estimate, but 3 full
  platform users alone at ~$349/mo ≈ **$1,047/mo** before a single GB of data is counted
  past the 100 GB free tier.
- **Grafana Cloud:** starts free, but a 50-host shop with real metric cardinality, logs
  and traces commonly lands in the **high hundreds to low thousands per month**; the exact
  figure depends entirely on active series and GB, which is the unpredictability we're
  solving.
- **Dash0:** per-signal pricing (metric data points, spans, log records), not per-GB;
  rates changed in Sept 2026 and the current ingestion/storage split is not cleanly
  published, so we do **not** quote a head-to-head figure here.

**Takeaway:** across every metered vendor, a 50-host shop lands somewhere between
~$1,000 and ~$3,000+ per month and *rises with growth*. Pulse0 Pro is a flat $249.

---

## Why self-hosted wins for data-sensitive buyers

For a fintech, a health-tech, a defense-adjacent, or any EU/data-residency-bound startup,
the cheapest SaaS bill is still a non-starter if it means shipping production traces and
logs — which routinely contain PII, secrets, and customer data — to a third-party cloud
in another jurisdiction. Pulse0 inverts the model: **the data never leaves your
environment.** It runs in your cluster, writes to your SQLite or Postgres, and in
Enterprise **Sovereign mode** makes **zero outbound calls at all** — deployable in a fully
air-gapped network. That turns a hard compliance conversation (DPA reviews, sub-processor
lists, cross-border transfer clauses, SOC2 scope) into a non-event, and it is a moat the
usage-metered SaaS incumbents structurally cannot cross without giving up their business
model.

---

## FAQ

**What happens when the 14-day trial ends?**
Nothing breaks and nothing is deleted. Your install degrades gracefully to the free
**Community** tier (Pro-only features like SSO/RBAC and the full alert/SLO/synthetics
suite lock, retention drops to the Community window). Add a license key any time to
restore Pro instantly — the data and deployment stay in place.

**How do I upgrade?**
Buy a Pro or Enterprise license key and apply it as a Helm value or environment variable.
No migration, no re-install, no data movement — the same running cluster simply unlocks.

**What counts as a "cluster"?**
One Pulse0 deployment / ingest endpoint — typically one Kubernetes cluster (or one
bare-metal/VM install) with its own OTLP endpoint and database. Multiple Kubernetes
clusters shipping to one Pulse0 instance count as one cluster for billing; running a
separate Pulse0 per cluster means a license per deployment. (Final definition to be fixed
in the license terms — this is the proposed basis.)

**Is there a per-host or per-GB charge, ever?**
No. That's the whole point. A cluster with 5 hosts and a cluster with 500 hosts pay the
same Pro price. Ingest as much telemetry as your own disk will hold.

**What support do I get?**
Community: GitHub issues and community channels. Pro: business-hours email support.
Enterprise: priority support with a contractual SLA, plus audit export and onboarding
assistance.

**Can I self-host and still use the Claude-backed assistant?**
Yes — "Ask Pulse0" uses *your* Anthropic API key (configured via `ANTHROPIC_API_KEY`).
Without a key it falls back to the rule-based investigator, so the assistant works offline
too, and Sovereign/air-gapped deployments can simply leave the key unset.

---

## Sources

All competitor figures are **list prices** and were accessed **14 November 2026**. Where
noted, figures come from third-party pricing trackers rather than the vendor's own page;
the official vendor pricing pages should be treated as authoritative before any
budgeting decision.

- Datadog official pricing page (Infrastructure Pro $15/host annual · $18 on-demand;
  Infrastructure Enterprise $23/$27; APM infra-attached $31/host, standalone $36; Log
  ingest $0.10/GB; Log indexing $1.70 per million events at 15-day retention) —
  https://www.datadoghq.com/pricing/ (accessed 14 Nov 2026)
- Datadog pricing analysis (high-water-mark billing, hidden fees) — SigNoz,
  https://signoz.io/blog/datadog-pricing/ · OneUptime,
  https://oneuptime.com/blog/post/2026-03-17-datadog-bill-shock-real-cost-observability-2026/view
  (accessed 14 Nov 2026)
- Splunk Observability pricing (Infrastructure $15/host/mo; App & Infrastructure bundle
  ~$60/host/mo; End-to-End ~$75/host/mo; APM standalone ~$55/host/mo; Splunk Cloud legacy
  log ingest ~$150–$200/GB/day) — official page
  https://www.splunk.com/en_us/software/pricing/observability.html ; third-party summary
  https://costbench.com/software/observability/splunk/ (accessed 14 Nov 2026)
- New Relic pricing (100 GB/mo free, then $0.40/GB Original / $0.60/GB Data Plus; full
  platform user ~$349/mo annual, ~$419 pay-as-you-go; core user $49) — vendor explainer
  https://newrelic.com/info/blog/explainer/new-relic-pricing-how-the-usage-based-model-works-and-what-you-pay ;
  third-party summary https://signoz.io/guides/new-relic-pricing/ (accessed 14 Nov 2026)
- Grafana Cloud pricing (free tier: 10k active series, 50 GB logs, 50 GB traces, 3 users;
  Pro ~$0.45/GB logs/traces ingest, ~$6.50 per 1,000 active metric series, $19/mo
  platform fee) — third-party summary https://last9.io/blog/grafana-cloud-pricing/
  (accessed 14 Nov 2026; verify against https://grafana.com/pricing/ )
- Dash0 pricing (per-signal: metric data points, spans, log records; ingestion/storage
  split changed Sept 2026, current rates not cleanly public) — CubeAPM editorial model
  https://cubeapm.com/blog/dash0-pricing-and-review/ ; UsagePricing
  https://www.usagepricing.com/blueprint/dash0 (accessed 14 Nov 2026; verify against
  https://www.dash0.com/pricing )

*Pulse0 prices on this page are proposals to be tuned; they are not a committed price list.*
