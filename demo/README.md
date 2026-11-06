# Pulse0 Demo Stack

A two-service microservice app (`frontend` → `backend`) plus a load generator, all auto-instrumented with OpenTelemetry Python, that spins up alongside Pulse0.

## Run

```bash
cd demo
docker compose up --build
```

Then open **http://localhost:4318** and you will see:

- **Resources** — `frontend` and `backend` with real RED metrics.
- **Service map** — automatic edge from frontend → backend extracted from traces.
- **Traces** — full waterfall across both services; ~8% deliberately fail so you'll see red bars.
- **Logs** — INFO/WARN/ERROR correlated to their traces (click a log with a trace id).
- **Metrics** — `orders_placed` counter and `cart_value_usd` histogram, plus the usual HTTP server metrics.
- **Alerts** — create one, e.g. `error_rate > 5` on service `backend`, and it will fire once load runs.
- **Synthetics** — add `http://frontend:8000/health` (from another container) or the host-mapped URL.

## Kubernetes

```bash
# 1. Build + load the images (or push to your registry)
docker compose build

# 2. Install Pulse0 via Helm
helm install pulse0 ../helm/pulse0 --create-namespace -n observability

# 3. Deploy the demo apps, pointing OTLP at the bundled collector
kubectl apply -f k8s-demo.yaml
```

The bundled Pulse0 Helm chart ships an OpenTelemetry Collector DaemonSet, so pod logs and node/cluster metrics will also flow in automatically. See the generated NOTES.txt from `helm install` for endpoints.
