# Sovereign Mode — your data never leaves your environment

Pulse0 is self-hosted: traces, logs, and metrics are written to a database **inside your own
cluster** (SQLite on a local volume, or a Postgres you control). Pulse0 does not phone home, send
usage analytics, or ship telemetry to any vendor. **Sovereign Mode** turns that default posture into
an explicit, verifiable guarantee for data-sensitive and regulated buyers.

## What it does

Set one environment variable:

```
PULSE0_SOVEREIGN=1
```

or with Helm:

```
helm upgrade --install pulse0 ./helm/pulse0 -n observability \
  --set-string env.PULSE0_SOVEREIGN=1
```

When Sovereign Mode is on, Pulse0 makes **zero outbound calls to any third-party vendor**. Concretely:

- The **"Ask Pulse0" Claude assistant is hard-disabled**, even if `ANTHROPIC_API_KEY` is set. No
  telemetry, prompt, or query can reach Anthropic. The assistant silently falls back to the built-in
  rule-based `/api/investigate`, which runs entirely in-process.
- Nothing else in Pulse0 initiates an outbound connection on its own — there is no license
  call-home, no update check, and no usage beacon.

On startup you will see:

```
Pulse0 Sovereign mode ON: no outbound vendor calls; Claude assistant disabled; telemetry stays in your environment
```

## What it deliberately does **not** block

These are endpoints **you** configure, and they point at systems **inside your own network / control**.
Sovereign Mode leaves them working so the product stays fully functional on-prem:

- **SSO** — the OIDC login flow talks to the identity provider *you* point it at (your Okta, Entra ID,
  Keycloak, etc.).
- **Alert webhooks** — alerts post to the URL *you* set on each rule (e.g. your internal Slack/Mattermost).
- **Synthetic checks** — these probe the targets *you* configure.

If you need a hard network guarantee on top of the application-level one, pair Sovereign Mode with a
Kubernetes **NetworkPolicy** (or your egress firewall) that denies all egress from the `pulse0`
namespace except to your own IdP/alert/synthetic endpoints. Pulse0 ships an air-gapped install bundle
(`scripts/airgap-bundle.sh`) for environments with no internet at all.

## How to verify

```
curl -s http://<pulse0>/api/mode | jq '.features.sovereign, .features.ai_assistant'
# true
# false
```

`features.sovereign == true` confirms the mode is active; `features.ai_assistant == false` confirms the
Anthropic path is sealed. For an independent check, run Pulse0 behind a deny-all egress NetworkPolicy
and confirm every page still works — because nothing leaves the cluster.

## Why it matters

SaaS observability vendors (Datadog, Splunk, New Relic, Grafana Cloud) require your telemetry to leave
your environment and land in theirs. For teams under data-residency, sovereignty, or air-gap
requirements, that is a non-starter. Sovereign Mode is a capability those products structurally cannot
offer — your data stays on your infrastructure, by design and by proof.
