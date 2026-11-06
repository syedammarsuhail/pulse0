# Pre-Flight Checklist — Testing Pulse0 on Your K8s Cluster

Run these in order tomorrow. Each step is cheap and catches problems before they hit the cluster.

## 0. Validate the chart locally (no cluster needed) — 1 min
```bash
cd pulse0
helm lint ./helm/pulse0
# render everything and eyeball it (use your cloud overlay):
helm template pulse0 ./helm/pulse0 -f ./helm/pulse0/values-eks.yaml | less   # or values-aks.yaml
```
> Already verified here: all YAML parses and all templates have balanced delimiters & correct helpers. `helm lint`/`template` is the final confirmation (needs the helm CLI, which the build sandbox couldn't install).

## 1. Build & push the image — 3 min
```bash
# EKS / ECR
docker build -t <acct>.dkr.ecr.<region>.amazonaws.com/pulse0:0.1.0 .
docker push  <acct>.dkr.ecr.<region>.amazonaws.com/pulse0:0.1.0
# AKS / ACR
az acr build -r <registry> -t pulse0:0.1.0 .
```
Confirm the cluster can pull it (ECR: nodes' IAM has `AmazonEC2ContainerRegistryReadOnly`; ACR: `az aks update --attach-acr`).

## 2. Check cluster prerequisites — 2 min
```bash
kubectl get storageclass            # EKS: gp3 exists?  AKS: managed-csi / managed-csi-premium?
kubectl get ingressclass            # alb (EKS) or nginx (AKS) present?
kubectl get nodes -o wide           # normal node group (not Fargate) for log tailing
```

## 3. Server-side dry run (validates against the live API, changes nothing) — 1 min
```bash
helm install pulse0 ./helm/pulse0 -n observability --create-namespace \
  -f ./helm/pulse0/values-eks.yaml --set auth.token=test \
  --dry-run=server | head -50
```

## 4. Install — 1 min
```bash
helm install pulse0 ./helm/pulse0 -n observability --create-namespace \
  -f ./helm/pulse0/values-eks.yaml \
  --set auth.token=$(openssl rand -hex 24)
```

## 5. Watch it come up — 2 min
```bash
kubectl -n observability get pods -w
# expect: pulse0-0 (server, 1/1) + pulse0-collector-* DaemonSet (one per node)
kubectl -n observability get pvc            # Bound?
kubectl -n observability logs sts/pulse0    # no errors
```

## 6. Health check
```bash
kubectl -n observability port-forward svc/pulse0 4318:4318 &
curl -s localhost:4318/healthz    # {"status":"ok"}
curl -s localhost:4318/readyz     # {"status":"ready",...}
curl -s localhost:4318/api/stats  # row counts
```
Open http://localhost:4318 — the UI loads (Resources will be empty until apps report).

## 7. Prove telemetry flows — 3 min
The bundled collector should already be sending node/pod metrics + logs. To add app traces with **zero code change**:
```bash
# install the OTel Operator once
kubectl apply -f https://github.com/open-telemetry/opentelemetry-operator/releases/latest/download/opentelemetry-operator.yaml

# annotate ONE existing deployment as a test (pick the language)
kubectl -n <your-ns> patch deploy <your-app> --type=strategic -p \
 '{"spec":{"template":{"metadata":{"annotations":{"instrumentation.opentelemetry.io/inject-java":"observability/pulse0"}}}}}'
kubectl -n <your-ns> rollout restart deploy/<your-app>
```
Within ~1 min the app appears under **Resources**, with traces, logs, and (after ~60s) metrics.

## Common first-run issues (and fixes)

| Symptom | Cause | Fix |
|---|---|---|
| `pulse0-0` Pending | PVC can't bind | wrong `storageClass` — set it to one from `kubectl get storageclass` |
| Collector `CrashLoopBackOff` | RBAC or kubelet TLS | check `kubectl -n observability logs ds/pulse0-collector`; `insecure_skip_verify` is already set for kubeletstats |
| No pod logs in UI | Fargate nodes | `filelog` needs host `/var/log/pods`; use EC2/VMSS node group, or push OTLP from apps directly |
| App annotated but no data | Operator not installed, or pod not restarted | install operator, then `rollout restart` the deployment |
| UI reachable but 401 on API | auth token set | include `Authorization: Bearer <token>` or unset `auth.token` for the test |
| ImagePullBackOff | registry not attached | ECR IAM / `az aks update --attach-acr` |

## Clean teardown after testing
```bash
helm uninstall pulse0 -n observability
kubectl delete ns observability          # also removes the PVC (and its data)
```

## Tips for your multi-cluster setup
- Test on **one non-prod cluster** first (e.g. your AT&T preprod EKS).
- Keep `auth.token` set and the Ingress `internal`/private.
- Start with `PULSE0_RETENTION_HOURS=24` and a small PVC for the test; raise both once it's proven.
- One Pulse0 per cluster is simplest; telemetry stays separable by `k8s.cluster.name` if you later centralize.
