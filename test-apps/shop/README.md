# Pulse0 polyglot shop demo

A connected demo shop in five languages plus a browser, all exporting traces, logs and metrics (OTLP) to Pulse0, with a
configurable load generator. Opening Pulse0's **Service map** shows every language in one graph.

```
browser storefront (Angular-style RUM, nginx)  ->  gateway (Node.js, zero-code)
        gateway -> catalog   (Python, zero-code, sqlite spans)
        gateway -> orders    (Java, javaagent zero-code, async "queue" hop)
                      orders -> inventory (Go, OTel SDK + otelhttp + manual DB span)
                      orders -> payments  (.NET, automatic instrumentation)
                      orders ~> inventory /ship   (async fulfilment via an executor "queue")
loadgen (Python, stdlib)  ->  gateway   (control API on :9000)
```

| Service | Language | Instrumentation | Notes |
|---|---|---|---|
| `shop-storefront` | browser JS | vendored `otel-rum.js` (document-load + fetch) | spans go straight from the browser to Pulse0; `traceparent` propagated through nginx |
| `shop-gateway` | Node.js | `@opentelemetry/auto-instrumentations-node` via `--require` | pino logs correlated to traces |
| `shop-catalog` | Python | `opentelemetry-instrument` | in-memory sqlite, `sqlite3` DB spans |
| `shop-orders` | Java 21 | OTel Java agent (`-javaagent`) | `java.util.logging` bridged to OTLP logs; async hop across an executor |
| `shop-payments` | .NET 8 | OTel .NET Automatic Instrumentation (CLR profiler) | no OpenTelemetry packages in the app |
| `shop-inventory` | Go | OTel SDK + `otelhttp` + `otelslog` | **Go has no true zero-code path (eBPF not used)**; manual `SELECT stock` span, business counters |

Failures and slowness are injected per request by the load generator with an `x-chaos: error:<service>` /
`slow:<service>` header that every service honours, so the failing service is known and the injected rate is exact.

## Run it

Prerequisites: a cluster running Pulse0 (Helm chart in `helm/pulse0`), `kubectl`, `aws` CLI with access to ECR + CodeBuild
(the `pulse0-e2e-build` project and `pulse0-e2e` ECR repo that `eks-e2e-aws.ps1` creates), `python`.

```bash
cd test-apps/shop
export KUBE_CONTEXT=<your cluster context>        # required; never taken from the current context
export PULSE0_URL=http://<pulse0-host>:4318       # URL your *browser* can reach (storefront RUM sends spans there)
# optional: COLLECTOR_URL (default http://pulse0-collector.observability.svc.cluster.local:4319), AWS_REGION, ECR_REPO
./deploy.sh                 # builds 7 images via CodeBuild, generates k8s/shop.yaml, applies, waits for rollout
./deploy.sh --skip-build    # redeploy without rebuilding
./build.sh gateway orders   # rebuild only some services (own S3 key + ECR tag per service: shop-<svc>)
```

Everything is created in namespace `langtest`. Remove it with:

```bash
kubectl --context <ctx> delete namespace langtest
```

## Load generator

Env vars: `TARGET`, `RPS` (base), `DURATION` (s, `0` = forever), `PROFILE`, `ERROR_RATE` (default `0.08`),
`SLOW_RATE` (default `0.05`), `MIX` (default `browse=45,search=20,checkout=25,admin=10`).

| Profile | Behaviour |
|---|---|
| `steady` | constant `RPS` |
| `ramp` | `0.2x` -> `3x` RPS over `DURATION` |
| `spike` | `RPS`, then `5x` for the middle 20% of `DURATION` |
| `soak` | constant `RPS`, meant to run for hours (`DURATION=0`) |
| `error-storm` | at least 40% injected errors |

Scenarios: `browse`, `search`, `checkout`, `admin`. It prints a summary every 10 s. Switch profile at runtime:

```bash
kubectl --context <ctx> -n langtest port-forward svc/loadgen 9000:9000 &
curl localhost:9000/status
curl -X POST 'localhost:9000/control?profile=spike&rps=8&duration=60'
curl -X POST 'localhost:9000/control?profile=error-storm&error_rate=0.5&duration=120'
curl -X POST 'localhost:9000/control?profile=steady&rps=5&duration=0'      # back to normal
```

Drive the browser tier by hand (generates RUM traces that continue through the gateway):

```bash
kubectl --context <ctx> -n langtest port-forward svc/storefront 9100:8000   # then open http://localhost:9100
```

Use the page's buttons or **Auto-run**.

## Known issues / notes

* **Pulse0 CORS bug:** the server answers `access-control-allow-origin: *`, which browsers reject for the credentialed
  requests `navigator.sendBeacon` makes, so the RUM bundle's spans never arrive. `storefront/index.html` carries a
  one-line `sendBeacon` workaround; remove it once the server reflects the request `Origin` with `allow_credentials`.
* **Sampling skews RED stats:** the bundled collector tail-samples (keeps all errors, ~20% of successes), so error
  percentages computed from stored traces are higher than the true rate. Use the loadgen counters, or the (unsampled)
  `http.server.*` metrics, for the true rate.
* `test-apps/k8s/` holds the earlier per-language smoke tests (Python, Node, Java, .NET, Go, Angular).
