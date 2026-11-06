# Pulse0 data residency and deployment

Pulse0 runs in two modes. Both speak the same OpenTelemetry protocol and serve the same UI.

| | Self-hosted (default) | Pulse-hosted (multi-tenant) |
|---|---|---|
| Where it runs | Your Kubernetes cluster or Docker host | Operated by the Pulse0 provider |
| Where telemetry is stored | A volume in your environment (SQLite on a PVC) | Per-project SQLite files on the provider's storage |
| Setup | `helm upgrade --install pulse0 ./helm/pulse0 ...` | Send OTLP to your project endpoint with an API key |
| Isolation | Your own cluster and network | One database file per project, hashed API keys, daily quotas |

## Data flow (self-hosted)

```
your apps --OTLP--> collector (DaemonSet / cluster collector) --OTLP--> Pulse0 pod --> PVC (your volume)
                                                                          ^
                                                      browser (UI) -------+  via your LB / ingress / port-forward
```

Everything above lives inside your cluster. Pulse0 makes no outbound calls on its own.

## What can leave your environment

- **Nothing by default.** There is no telemetry, license check or phone-home in the server.
- **The Ask Pulse0 assistant is off by default.** If you enable it by providing `ANTHROPIC_API_KEY`, the questions you ask and the query results the assistant reads are sent to the Anthropic API to produce an answer. Leave the key unset and the assistant falls back to rule-based investigation, which makes no external calls.
- **Webhooks and synthetic checks** call the URLs you configure. In hosted mode they have an SSRF guard that blocks internal addresses. In self-hosted mode they can reach whatever your cluster can reach.

## Claims you can make, and claims you should not

Supported by the code:
- Telemetry is stored on a volume you own when self-hosted.
- No outbound traffic unless you enable the assistant or configure webhooks/synthetics.
- Bearer-token auth on `/v1/*` and `/api/*` (`auth.token` in the chart).

Not implemented yet, so do not promise them:
- SSO / SAML / OIDC login and role-based access control inside the UI.
- Encryption at rest beyond what your storage class provides.
- High availability: Pulse0 is a single-replica SQLite StatefulSet.
- Audit logging of UI actions.
- A third-party security audit or compliance certification.

## Security checklist before exposing it

1. Set `auth.token` to a strong random value.
2. Restrict the load balancer to known source ranges, or keep the service internal and use an ingress with TLS.
3. Use an encrypted storage class for the PVC.
4. Set `PULSE0_RETENTION_HOURS` to match your data-retention policy.
5. Leave the assistant disabled unless you accept sending query results to the Anthropic API.
