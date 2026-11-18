> **Start here.** Download the latest package from this repository, **`pulse0-onprem-v10_12.tar.gz`**, and unpack it:
>
> ```bash
> tar -xzf pulse0-onprem-v10_12.tar.gz && cd pulse0-v10_12
> ```
>
> Run every command in this guide from that folder. The files it mentions (`ON-PREM.md`, `demo/`, `scripts/eks.sh`, `helm/pulse0/`) are inside the package.
> Older `pulse0-onprem-v10_*.tar.gz` files in this repository are superseded; use v10.12 or newer.

# Pulse0: running a demo for a client

How to stand up Pulse0 with realistic data for a client demo, how to reset it for the next client, and how to remove it.
For production installs use `ON-PREM.md`. This guide is only about demos.

## Pick a demo setup

| Setup | Best for | Time | Cost | Needs |
|---|---|---|---|---|
| **A. Laptop (Docker Compose)** | Quick look, small meeting | ~10 min | Free | Docker |
| **B. Client's Kubernetes (bare metal / k3s)** | Demo on the client's own network | ~20 min | Free | A cluster, `kubectl`, `helm`, a registry or air-gapped image |
| **C. Throwaway AWS EKS cluster** | Impressive scale, nothing to install on the client side | ~30 min | ~US$0.30/hour while it runs | AWS account, `aws`, `eksctl`, `kubectl`, `helm`, `python`, Git Bash on Windows |

Every setup ends the same way: open the UI, log in with the **token** you chose, and let the demo load run for a few minutes so the pages fill.

## Before any demo

1. **Pick a token.** It protects the API and ingest. Make one per demo: `openssl rand -hex 24` (or `python -c "import secrets;print(secrets.token_hex(24))"`). Never reuse a token between clients and never use a shared default.
2. **Know the edition.** Every fresh install starts a **14-day Pro trial**, which is enough for a demo (SSO, per-environment access, audit log, AI assistant, unlimited services/dashboards). After 14 days it drops to Community (10 services, 3 SLOs, 7-day retention). A Pro **license** is separate and is **not part of this repository**; see [Community and Pro](#community-and-pro).
3. **Storage.** Do not run the demo database on NFS: it is many times slower. Use a local disk, or Postgres (`PULSE0_DATABASE_URL`).

## A. Laptop with Docker Compose

```bash
cd demo
docker compose up --build
```

Open <http://localhost:4318>. The compose stack runs Pulse0, a small two-service app (`frontend` to `backend`) and a load generator, already instrumented. It is the fastest way to show traces, the service map, logs and metrics.

Stop and clear everything (this deletes the demo data): `docker compose down -v`.

## B. Client's Kubernetes (bare metal, k3s, kubeadm)

**1. Get the image onto the cluster** (details and air-gapped options in `ON-PREM.md` section 1 (get the image onto your machines)):

```bash
docker build -t registry.example.com/pulse0:demo .
docker push registry.example.com/pulse0:demo
```

**2. Install Pulse0.** Edit `helm/pulse0/values-baremetal.yaml` first if the cluster has no `local-path` storage class.

```bash
TOKEN=$(openssl rand -hex 24); echo "token: $TOKEN"      # write this down
helm install pulse0 ./helm/pulse0 -n observability --create-namespace \
  -f helm/pulse0/values-baremetal.yaml \
  --set image.repository=registry.example.com/pulse0 \
  --set-string image.tag=demo \
  --set auth.token=$TOKEN
kubectl -n observability rollout status statefulset/pulse0
```

**3. Open the UI.**

```bash
kubectl -n observability port-forward svc/pulse0 4318:4318      # then http://localhost:4318
# or: kubectl -n observability get svc pulse0   (NodePort: http://<node-ip>:<nodePort>)
```

The bundled collector already sends the cluster's pod logs and node/cluster metrics, so the **Kubernetes**, **Logs** and **Metrics** pages fill without any app. 

**4. Add demo traffic** (pick one):

```bash
# a) Two small apps with traces, logs and metrics (needs the demo image, see demo/README.md)
kubectl apply -f demo/k8s-demo.yaml

# b) A simulated 22-service shop with deliberate faults (payments errors, slow fraud checks, error bursts...)
docker build -t registry.example.com/pulse0-demo:demo demo && docker push registry.example.com/pulse0-demo:demo
kubectl create namespace demo
kubectl -n demo create secret generic pulse0-token --from-literal=token=$TOKEN
sed 's#pulse0-demo:local#registry.example.com/pulse0-demo:demo#' demo/k8s-sim-platform.yaml | kubectl apply -f -
```

Give it 5 to 10 minutes to show service maps, error rates and insights.

## C. Throwaway AWS EKS cluster

`scripts/eks.sh` builds the image in AWS, creates a small cluster (2 x t3.medium), a private Postgres, deploys Pulse0 and runs a smoke test.

```bash
aws sts get-caller-identity                         # confirm this is the account you mean to bill
RDS_IN_VPC=1 SKIP_SHOP=1 bash scripts/eks.sh all    # ~30 minutes; prints the UI address and the API token
```

- It prints `UI (ELB DNS): http://<load-balancer>:4318` and `API token: ...` at the end. The token is also saved in `.eks-token`.
- Leave out `RDS_IN_VPC=1` to use SQLite on a disk instead (cheaper, single pod).
- Optional friendly hostname: set `PULSE0_DNS_NAME` and `PULSE0_HOSTED_ZONE_ID` (a Route 53 zone you own) before running.
- Add traffic: `kubectl apply -f demo/k8s-sim-platform.yaml` after creating the `pulse0-token` secret and replacing the image as in step B-4 (the script already builds and pushes the demo image as `<account>.dkr.ecr.<region>.amazonaws.com/pulse0-e2e:demo`).
- **It bills until you tear it down:** `bash scripts/eks.sh teardown` (deletes the cluster, load balancer, database, image repository and build resources, about 15 minutes).

## A 5-minute walk-through

1. **Home**: requests, error rate, p95, open insights. "One screen for the whole system."
2. **Services**: RED metrics per service; sort by errors. Open the red one (`payments-service`).
3. **Service map**: the dependency graph built automatically from traces. Click an edge to see its error rate.
4. **Traces**: filter to errors, open one, show the waterfall across services.
5. **Logs**: switch to *Errors only*, expand a row to show the stack trace, the pod, namespace and node, and the linked trace. Drag across the volume bar at the bottom to zoom in time.
6. **Insights, Alerts, SLOs**: rule-based findings ("error rate spike on search-service"), alert templates, error budget.
7. **Kubernetes**: nodes, pods and workloads with their logs.
8. **Ask Pulse0** (Pro trial, needs your own Anthropic API key): "why is checkout slow?"

## New demo for another client

| Goal | How |
|---|---|
| Fresh data on the same cluster | `helm -n observability uninstall pulse0`, then `kubectl -n observability delete pvc --all`, then install again with a **new token** |
| Fresh data, Postgres backend | Drop and recreate the schema: `DROP SCHEMA pulse0 CASCADE;` then restart the pod. Pulse0 recreates the tables |
| A separate instance next to the first | `helm install pulse0-clientb ./helm/pulse0 -n clientb --create-namespace ...` with its own token |
| A clean cloud cluster | `bash scripts/eks.sh teardown`, then `CLUSTER_NAME=clientb bash scripts/eks.sh all`. A new token is generated |

A trial starts when a database is first created, so a fresh database gets a fresh 14-day Pro trial. To show Pro features after a trial has run out, use a new database (see above) or a licensed install.

## Community and Pro

| | Community (free) | Pro (paid) |
|---|---|---|
| Traces, logs, metrics, service map, dashboards, alerts, SLOs, synthetics | Yes | Yes |
| Services / dashboards / alert rules / SLOs / synthetics | 10 / 10 / 10 / 3 / 3 | Unlimited |
| Retention | up to 7 days | Configurable |
| SSO (OIDC) with roles, per-environment access, audit log, AI assistant | No | Yes |

The Pro license check and key generation are proprietary, are **not in this repository or its tar files**, and are supplied separately to licensed customers. Do not commit license keys or the license module here.

## Removing a demo

- Compose: `docker compose down -v`
- Helm: `helm -n observability uninstall pulse0` and `kubectl delete namespace observability demo`
- EKS: `bash scripts/eks.sh teardown`

After a client demo, delete the cluster or release and discard the token.

## Troubleshooting

- **Empty pages:** wait a few minutes and check `kubectl -n observability logs pulse0-0`. Confirm the demo pods are running and that the token in their environment matches.
- **Browser shows an old page:** hard reload (Ctrl+Shift+R) once after an upgrade.
- **`ImagePullBackOff`:** the node cannot reach the registry; see the registry options in ON-PREM.md.
- **Slow pages on a bare-metal cluster:** almost always network storage (NFS). Use local disk or Postgres.
- **Pods cannot be scheduled on EKS:** the cluster needs the two t3.medium nodes to be `Ready`: `kubectl get nodes`.
