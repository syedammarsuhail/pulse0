# Pulse0 on-premises

Pulse0 runs entirely inside your network. By default it stores everything in one SQLite file, so there is no database to install.

| You have | Use | Section |
|---|---|---|
| A Kubernetes cluster (k3s, kubeadm, Rancher, OpenShift) | Helm chart | [Kubernetes](#kubernetes-bare-metal) |
| One Linux VM or server, Docker allowed | Docker Compose | [VM with Docker](#vm-with-docker) |
| One Linux VM, no Docker | systemd | [VM without Docker](#vm-without-docker) |

Every path ends with the same check: [Verify](#verify).

## 1. Get the image onto your machines

Kubernetes and Docker both need the `pulse0` image. Pick the one that matches your site.

**Private registry (most common).** On a machine with Docker:

```bash
docker build -t registry.example.com/pulse0:0.1.0 .
docker push registry.example.com/pulse0:0.1.0
```

**Air-gapped, no registry.** Export once, copy the file, import on each node:

```bash
docker build -t pulse0:0.1.0 .
docker save pulse0:0.1.0 | gzip > pulse0-0.1.0.tar.gz

# on every Kubernetes node (or the Docker VM):
gunzip -c pulse0-0.1.0.tar.gz | sudo k3s ctr images import -            # k3s
gunzip -c pulse0-0.1.0.tar.gz | sudo ctr -n k8s.io images import -      # kubeadm with containerd
gunzip -c pulse0-0.1.0.tar.gz | docker load                              # plain Docker
```

The OpenTelemetry Collector image (`otel/opentelemetry-collector-contrib:0.111.0`) comes from Docker Hub. In an air-gapped site, mirror it the same way and set `collector.image` in the Helm values.

## Kubernetes (bare metal)

Prerequisites: `kubectl` and `helm` pointed at your cluster, and a storage class (`kubectl get storageclass`).

**1. Choose storage and exposure.** The commented options are in `helm/pulse0/values-baremetal.yaml`. Storage defaults to `local-path` (the k3s default); change `persistence.storageClass` if you use Longhorn, OpenEBS, Rook-Ceph or NFS. The UI is exposed as a `NodePort` by default; switch to `LoadBalancer` if you run MetalLB, or enable `ingress`.

**2. Install.**

```bash
helm install pulse0 ./helm/pulse0 -n observability --create-namespace \
  -f helm/pulse0/values-baremetal.yaml \
  --set image.repository=registry.example.com/pulse0 \
  --set-string image.tag=0.1.0 \
  --set auth.token=$(openssl rand -hex 24)
```

- Air-gapped, image imported on every node: use `--set image.repository=pulse0 --set image.pullPolicy=Never`.
- Private registry that needs a login:
  ```bash
  kubectl -n observability create secret docker-registry regcred \
    --docker-server=registry.example.com --docker-username=<user> --docker-password=<password>
  # then add to the helm command:
  --set imagePullSecrets[0].name=regcred
  ```
- Always pass the tag with `--set-string`. A tag made only of digits is otherwise read as a number and breaks the image name.
- Pulse0 uses SQLite and must stay at one replica. On a multi-node cluster, pin it to one node with `nodeSelector` (see the values file).

**3. Reach the UI.**

```bash
kubectl -n observability get svc pulse0                  # NodePort: open http://<any-node-ip>:<nodePort>
kubectl -n observability port-forward svc/pulse0 4318:4318   # quick look: http://localhost:4318
```

The bundled collector DaemonSet collects pod logs and node and cluster metrics automatically. Point your applications at `http://pulse0-collector.observability.svc.cluster.local:4319` (OTLP/HTTP).

**4. Upgrade and remove.**

```bash
helm upgrade pulse0 ./helm/pulse0 -n observability -f helm/pulse0/values-baremetal.yaml \
  --set image.repository=registry.example.com/pulse0 --set-string image.tag=0.1.1 \
  --set auth.token=<same token>
helm uninstall pulse0 -n observability      # the data volume is kept until you delete the PVC
```

Pass the full set of values on upgrade rather than `--reuse-values`, which keeps stale defaults across chart versions.

## VM with Docker

Needs Docker with the Compose plugin. Files are in `deploy/vm/`.

```bash
cd deploy/vm
cp .env.example .env          # set PULSE0_TOKEN (openssl rand -hex 24); set PULSE0_IMAGE if you use a registry
docker compose up -d
docker compose ps             # both services should be "running" / "healthy"
```

Open `http://<vm-ip>:4318`. Applications send OTLP to `http://<vm-ip>:4318` directly, or through the host collector on `:4317` (gRPC) and `:4319` (HTTP). The collector also reports the VM's CPU, memory, disk, network and load as the service `host-metrics`.

Data lives in the `pulse0-data` Docker volume and survives restarts and upgrades.

Upgrade: load or pull the new image, set `PULSE0_IMAGE`, then `docker compose up -d`.

## VM without Docker

Needs Python 3.12 and root access.

```bash
sudo useradd --system --home /opt/pulse0 --shell /usr/sbin/nologin pulse0
sudo mkdir -p /opt/pulse0 /etc/pulse0 /var/lib/pulse0
sudo cp -r server.py static enterprise requirements.txt /opt/pulse0/
sudo python3.12 -m venv /opt/pulse0/venv
sudo /opt/pulse0/venv/bin/pip install -r /opt/pulse0/requirements.txt
sudo cp deploy/vm/pulse0.env.example /etc/pulse0/pulse0.env      # edit PULSE0_TOKEN
sudo chmod 600 /etc/pulse0/pulse0.env
sudo chown -R pulse0:pulse0 /opt/pulse0 /var/lib/pulse0
sudo cp deploy/vm/pulse0.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pulse0
sudo systemctl status pulse0
```

`PULSE0_HOST=0.0.0.0` in the env file is required for other machines to connect; the default is localhost only. For an air-gapped site, download the wheels with `pip download -r requirements.txt` on a connected machine and install with `pip install --no-index --find-links`.

## Optional: use your own Postgres

Set `PULSE0_DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require`. Pulse0 creates its own schema (`pulse0`, change with `PULSE0_PG_SCHEMA`) and tables on first start, so you only need an empty database and a user that can create schemas. SQLite is simpler and faster for a single instance, so use Postgres only if you need it. Use a Postgres with real disks and memory; a small shared or free-tier instance makes the dashboards slow.

In Helm: create a secret holding the URL, then `--set postgres.enabled=true --set postgres.existingSecret=<secret> --set persistence.enabled=false`.

## Production checklist

- **Token.** Set a strong `PULSE0_TOKEN`. It protects `/v1/*` and `/api/*`. For single sign-on and roles see `SSO-RBAC.md`.
- **HTTPS.** Put a reverse proxy in front of port 4318. With Caddy, a two-line `Caddyfile` does it: `pulse0.example.com { reverse_proxy 127.0.0.1:4318 }`. Apps then send OTLP to `https://pulse0.example.com`.
- **Firewall.** Open only 4318 (or 443 behind the proxy), plus 4317 and 4319 if you use the host collector from other machines.
- **Sizing.** Allow roughly 0.3 to 1 GB of disk per million spans at default settings, and raise retention only as far as disk allows. Give the process memory roughly equal to the database size for fast dashboards.
- **Backups.** SQLite: `sqlite3 /var/lib/pulse0/pulse0.db ".backup /backup/pulse0.db"` works while running. Docker: `docker exec` the same command against `/data/pulse0.db`. Kubernetes: snapshot the PVC.

## Verify

From any machine that can reach Pulse0:

```bash
python scripts/smoke.py http://<host>:4318 --token <your token>
```

It sends a test service over OTLP and checks the UI and each main API read it back. It prints PASS or FAIL per check and ends with a line like `35/35 checks passed`; it exits non-zero if anything failed. The first FAIL line tells you where to look.

Quick manual check: `curl http://<host>:4318/healthz` returns `{"status":"ok"}`, and `curl http://<host>:4318/readyz` confirms the database is reachable.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Pod stuck in `ImagePullBackOff` or `InvalidImageName` | image not on the node or registry login missing (see section 1); a numeric tag passed without `--set-string` |
| Pod `Pending` | no storage class, or none marked default; set `persistence.storageClass` |
| UI unreachable from other machines (VM) | `PULSE0_HOST` still `127.0.0.1`, or the firewall blocks 4318 |
| `401` from the API or collector | token missing or different from `PULSE0_TOKEN` |
| New services not appearing | Community tier allows 5 services; the 14-day Pro trial or a license lifts that |
