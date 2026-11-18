> This guide is also inside the package as `GETTING-STARTED.md`. Download the newest package from this repository:
> **`pulse0-onprem-v10_23.tar.gz`**. Older `pulse0-onprem-v10_*.tar.gz` files are kept for reference only.

# Getting started with Pulse0

Pulse0 is self-hosted observability built on OpenTelemetry. It gives you one endpoint that receives traces, logs and metrics, and a web
UI to explore them: services with request rate, errors and latency, a service map, traces, logs, metrics, Kubernetes, dashboards,
alerts, SLOs, synthetic checks, website monitoring and an optional AI assistant. Everything runs on your own infrastructure and your
data stays there.

This guide takes you from nothing to a working install with data. It takes about 20 minutes.

| If you want to... | Read |
|---|---|
| Install Pulse0 for the first time | this guide |
| See every install option and setting in detail | `ON-PREM.md` (inside the package) |
| Run a demo for a client, or reset one | [DEMO-GUIDE.md](DEMO-GUIDE.md) |
| Upgrade an existing install | `UPGRADE.md` (inside the package) |

---

## 1. What you need

- **Somewhere to run it**, one of:
  - a Kubernetes cluster (k3s, kubeadm, Rancher, OpenShift, EKS, AKS) with `kubectl` and `helm`, **or**
  - one Linux VM or server with Docker and the Compose plugin, **or**
  - a laptop with Docker, just to try it.
- **Docker on one machine** to build the image (the package ships the source, not a prebuilt image).
- **Storage on a local disk or SSD.** Do not put the database on NFS or other network storage: pages become many times slower. If you
  must use network storage, use Postgres instead (see [Storage and performance](#9-storage-and-performance)).
- **Memory roughly equal to the database size** for fast dashboards. The Helm chart requests 1 GiB and allows 2 GiB by default.

## 2. Download and unpack

Download the newest `pulse0-onprem-v10_*.tar.gz` from the repository, then:

```bash
tar -xzf pulse0-onprem-v10_23.tar.gz
cd pulse0-v10_23
```

Run every command below from this folder.

## 3. Build the image

```bash
docker build -t registry.example.com/pulse0:10.23 .
docker push registry.example.com/pulse0:10.23        # skip if you run on this same machine
```

No registry (air-gapped)? Save the image to a file and import it on each node: see `ON-PREM.md` (inside the package) section 1.

## 4. Choose a token

Pulse0 protects its API and its ingest endpoint with a token. Make a long random one and keep it somewhere safe:

```bash
openssl rand -hex 24
```

Use a different token for every install. Never use a shared or example value.

## 5. Install

Pick **one**.

### A. Kubernetes (recommended)

```bash
TOKEN=<the token from step 4>
helm install pulse0 ./helm/pulse0 -n observability --create-namespace \
  -f helm/pulse0/values-baremetal.yaml \
  --set image.repository=registry.example.com/pulse0 \
  --set-string image.tag=10.23 \
  --set auth.token=$TOKEN
kubectl -n observability rollout status statefulset/pulse0
```

- Use `values-eks.yaml`, `values-aks.yaml` or `values-openshift.yaml` instead of `values-baremetal.yaml` on those platforms.
- Edit the values file first if your cluster has no `local-path` storage class (`kubectl get storageclass`).
- Always pass the image tag with `--set-string`.

Open the UI:

```bash
kubectl -n observability port-forward svc/pulse0 4318:4318      # then http://localhost:4318
# or, with the default NodePort: kubectl -n observability get svc pulse0  ->  http://<node-ip>:<nodePort>
```

### B. One VM with Docker

```bash
cd deploy/vm
cp .env.example .env        # set PULSE0_TOKEN to your token, and PULSE0_IMAGE=registry.example.com/pulse0:10.23
docker compose up -d
docker compose ps           # both services should be running / healthy
```

Open `http://<vm-ip>:4318`.

### C. Just try it on a laptop

```bash
cd demo
docker compose up --build
```

Open <http://localhost:4318>. This starts Pulse0 together with two small demo services and a load generator, so data appears straight
away. Stop it and delete the data with `docker compose down -v`.

## 6. First login

1. Open the UI. If it asks for a token, go to **Settings → Access**, paste your token and click **Save token**.
2. A new install starts a **14-day Pro trial**: every feature is unlocked. You will see a banner with the days left.
3. The **Get started** page shows the commands to connect data sources from this point on.

## 7. Get data in

**Kubernetes:** the chart already installs an OpenTelemetry Collector on every node. Within a minute or two you will see pod logs, node
and cluster metrics, and the **Kubernetes** page fills, without changing any application.

**Your applications' traces:**

- **No code changes (Kubernetes):** enable the OpenTelemetry Operator once, then switch tracing on per workload:

  ```bash
  helm upgrade pulse0 ./helm/pulse0 -n observability --reuse-values --set opentelemetry-operator.enabled=true --wait --timeout 5m
  scripts/instrument.sh java -n my-namespace my-deployment       # also: dotnet, python, nodejs, go
  ```

  The pod restarts once with the language agent attached. Do not enable the Operator if it is already installed in the cluster.
- **Apps that already use OpenTelemetry:** point their OTLP exporter at the collector,
  `http://pulse0-collector.observability.svc.cluster.local:4319` (inside the cluster), or straight at Pulse0 on port 4318 with the
  header `Authorization: Bearer <token>`.
- **Docker VM:** apps send to `http://<vm-ip>:4319` (the host collector) or `http://<vm-ip>:4318`.

**Your website (optional):** the **Website** page explains itself and shows the one line to add to your site, with copy buttons.

**CPU and memory dashboard (optional):** the chart already collects host and pod CPU / memory. To get a ready-made
*Cluster - CPU & Memory* dashboard, run `python3 scripts/create-dashboards.py --url http://<host>:4318 --token <token>`. It also
tells you which panels have data yet (allow 2-3 minutes after install).

**Demo traffic (optional):** see [DEMO-GUIDE.md](DEMO-GUIDE.md) for a simulated 22-service shop with realistic faults.

## 8. Check that it works

```bash
python scripts/smoke.py http://<host>:4318 --token <your token>
```

It sends a test service and checks the UI and the main APIs. It ends with a line like `35/35 checks passed`.

On the first start Pulse0 also fills its fast summary tables from any data already stored. When that is done the log says so:

```bash
kubectl -n observability logs pulse0-0 | grep "rollups ready"
# rollups ready: span rollup
# rollups ready: environment counts
# rollups ready: Kubernetes resource registry
```

Until those lines appear, some pages use slower queries (never wrong numbers). On a new install they appear within seconds.

## A quick tour

| Page | What it is for |
|---|---|
| **Home** | Requests, error rate, latency and the problems Pulse0 found, on one screen |
| **Services** | Every service with request rate, errors, p50/p95/p99 latency, a health score and a trend |
| **Service map** | Which service calls which, built from traces; red means errors |
| **Traces** | Search requests and open one to see its path through every service |
| **Logs** | Search and filter; expand a row to see the full message, stack trace, pod and trace; error rows stand out |
| **Kubernetes** | Namespaces, workloads and pods, and which are instrumented |
| **Environments** | Each cluster or deployment environment with its namespaces |
| **Website** | What real visitors experience in their browsers (needs the website script) |
| **Dashboards, Alerts, SLOs, Synthetics** | Your own charts, alert rules with templates, service-level objectives, uptime checks |
| **Ask Pulse0** | Ask questions in plain English (button at the top) |

## AI assistant

**Ask Pulse0** always works with built-in rules, free, and no data leaves your network. Connect an AI model for AI answers. Pulse0 works
with any provider; you bring your own model and key.

| Provider | Helm settings |
|---|---|
| Claude | `--set ai.enabled=true --set ai.provider=anthropic --set ai.existingSecret=pulse0-ai` |
| OpenAI, Azure OpenAI, Groq, Mistral, OpenRouter... | `--set ai.enabled=true --set ai.provider=openai --set ai.baseUrl=<https://.../v1> --set ai.model=<model> --set ai.existingSecret=pulse0-ai` |
| Self-hosted (Ollama, vLLM, LM Studio): nothing leaves your network | `--set ai.enabled=true --set ai.provider=openai --set ai.baseUrl=http://<ollama>:11434/v1 --set ai.model=llama3.1` |

Create the key secret first (not needed for a local model without a key):

```bash
kubectl -n observability create secret generic pulse0-ai --from-literal=api-key=<YOUR_API_KEY>
```

Then restart Pulse0 (`kubectl -n observability rollout restart statefulset/pulse0`). The model must support tool (function) calling.
**Settings → AI assistant** shows whether it is on and, if not, why. With a cloud model, snippets of query results (service names, error
messages, log lines) are sent to that provider.

## Community and Pro

| | Community (free) | Pro |
|---|---|---|
| Traces, logs, metrics, service map, Kubernetes, dashboards, alerts, SLOs, synthetics, website monitoring | Yes | Yes |
| Rule-based assistant | Yes | Yes |
| AI answers (your own model) | 20 per day (`PULSE0_COMMUNITY_AI_DAILY`) | Unlimited |
| Services / dashboards / alert rules / SLOs / synthetic checks | 10 / 10 / 10 / 3 / 3 | Unlimited |
| Retention | Up to 7 days | Configurable |
| SSO with roles, per-environment access, full audit log | No | Yes |

A new install gets a 14-day Pro trial. After it ends Pulse0 keeps running as Community; nothing is deleted. A Pro license is supplied
separately and is not part of this package.

## Upgrading

1. Unpack the new package and build the new image (steps 2 and 3).
2. Read the newest section of `UPGRADE.md` (inside the package).
3. Kubernetes: `helm -n observability upgrade pulse0 ./helm/pulse0 --reset-then-reuse-values --set-string image.tag=<new tag>`.
   Docker VM: set the new `PULSE0_IMAGE` in `.env`, then `docker compose up -d`.
4. Reload the browser once with **Ctrl+Shift+R** so it picks up the new UI.

Your data is kept. New tables are created automatically.

## 9. Storage and performance

- **Use local disk or SSD for the database.** NFS makes every page slow; this is the most common cause of a slow install.
- **Or use Postgres:** set `PULSE0_DATABASE_URL` (Helm: `--set postgres.enabled=true --set postgres.existingSecret=<secret>`), with a
  properly sized Postgres; a small free-tier database is too slow. See `ON-PREM.md` (inside the package).
- **Give it memory:** roughly the size of the database.
- **Retention:** `PULSE0_RETENTION_HOURS` (default 72). Shorter retention means a smaller, faster database.
- Pulse0 is a single instance: keep one replica.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Pages are slow | Storage on NFS? Not enough memory? Wait for the `rollups ready` lines after an upgrade |
| `401` from the UI, API or collector | The token in Settings → Access or in the collector does not match `PULSE0_TOKEN` |
| Pod `ImagePullBackOff` | The image is not in a registry the nodes can reach, or the tag was not passed with `--set-string` |
| Pod `Pending` | No storage class: set `persistence.storageClass` |
| UI unreachable from other machines (VM) | Firewall blocks port 4318 |
| No data | Run `scripts/smoke.py`; check `kubectl -n observability logs pulse0-0` and the collector pods |
| New services do not appear | Community allows 10 services; the trial or a license lifts that |
| Old-looking page after an upgrade | Reload with Ctrl+Shift+R |
| AI answers not working | Settings → AI assistant shows the reason; the pod must be able to reach the model's address |

## Security checklist

- Scan every build before you deploy it: `bash scripts/release-check.sh` (tests, dependency CVEs, code scan, secrets, chart).
  In a GitHub repository, `.github/workflows/security.yml` also runs Snyk and SonarCloud and publishes only when all pass.
- A long random `PULSE0_TOKEN`, different for every install. The chart refuses the placeholder tokens in its values files.
- HTTPS in front of port 4318 (any reverse proxy or ingress).
- Open only the ports you use: 4318 (UI and API), and 4317 / 4319 for the collector if other machines send to it.
- For a website, use an ingest-only token (`PULSE0_INGEST_TOKEN`), never the admin token: web pages are public.
- Keep API keys in Kubernetes secrets or environment variables, never in values files or in Git.
