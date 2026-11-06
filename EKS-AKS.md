# Pulse0 on EKS & AKS

The Helm chart is cloud-agnostic; what changes between EKS and AKS is **storage class, how you expose the UI, image registry, and (only at scale) pod identity**. Overlay files `values-eks.yaml` and `values-aks.yaml` capture the differences — install with `-f`.

## How it runs on managed Kubernetes

Same topology on both clouds:

```
 your apps (annotated → OTel agent injected, zero code change)
        │  OTLP
        ▼
 Pulse0 OpenTelemetry Collector  (DaemonSet, one pod per node)
   • scrapes each node's kubelet  (pod/container metrics)
   • tails /var/log/pods          (all container logs)
   • pulls K8s cluster metrics    (deployments, nodes, etc. via K8s API)
   • enriches with k8sattributes + cloud resourcedetection
        │  OTLP → 
        ▼
 Pulse0 server  (StatefulSet, 1 replica + PVC)  ──► SQLite on a cloud disk
        │
        ▼
 UI / API  (Service → Ingress/LoadBalancer)
```

The DaemonSet already has the RBAC (ClusterRole + binding) it needs to read the kubelet and K8s API. `K8S_NODE_NAME` is injected via the downward API so `kubeletstats` hits the right node. This is identical on EKS and AKS — only the surrounding cloud plumbing differs.

## What differs

| Concern | EKS | AKS |
|---|---|---|
| **Container registry** | ECR (`<acct>.dkr.ecr.<region>.amazonaws.com`) | ACR (`<name>.azurecr.io`, attach to cluster) |
| **Persistent disk** | EBS CSI → `storageClass: gp3` | Azure Disk CSI → `managed-csi` / `managed-csi-premium` |
| **Expose the UI** | AWS Load Balancer Controller → ALB Ingress (or NLB) | AGIC or ingress-nginx; internal LB via annotation |
| **TLS cert** | ACM cert ARN on the ALB | cert-manager / Key Vault → `ingress.tls` |
| **Cloud attributes** | `eks` + `ec2` detectors → `k8s.cluster.name`, `cloud.region`, `cloud.availability_zone` | `aks` + `azure` detectors → same attributes |
| **Pod identity (only for S3/Blob tiering, Tier 3)** | IRSA (IAM role on ServiceAccount) | Azure Workload Identity (federated managed identity) |

The collector's `resourcedetection` runs **all** cloud detectors (`eks, aks, ec2, azure, gcp`); unmatched ones are skipped, so the exact same build auto-labels telemetry correctly whether it lands on EKS or AKS. That's how a span from an AT&T preprod EKS cluster and one from an AKS cluster both arrive tagged with their real `k8s.cluster.name` and region, and show up resource-centric in Pulse0.

## EKS — quickstart

```bash
# 0. prereqs: EBS CSI addon + AWS Load Balancer Controller installed on the cluster
aws ecr create-repository --repository-name pulse0
docker build -t <acct>.dkr.ecr.<region>.amazonaws.com/pulse0:0.1.0 .
aws ecr get-login-password | docker login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com
docker push <acct>.dkr.ecr.<region>.amazonaws.com/pulse0:0.1.0

# 1. install the OTel Operator once (for zero-code app instrumentation)
kubectl apply -f https://github.com/open-telemetry/opentelemetry-operator/releases/latest/download/opentelemetry-operator.yaml

# 2. install Pulse0 with the EKS overlay
helm install pulse0 ./helm/pulse0 -n observability --create-namespace \
  -f ./helm/pulse0/values-eks.yaml \
  --set auth.token=$(openssl rand -hex 24)

# 3. instrument a workload (no code change)
kubectl -n myapp patch deploy my-api --type=strategic -p \
 '{"spec":{"template":{"metadata":{"annotations":{"instrumentation.opentelemetry.io/inject-java":"observability/pulse0"}}}}}'
```

Multi-cluster (your AT&T preprod / NovusAG setup): run one Pulse0 per cluster, **or** one central Pulse0 and point each cluster's collector at it over the ALB — set the remote cluster collector's exporter endpoint to `https://pulse0.internal.yourcompany.com` with the bearer token. Each cluster's telemetry stays separable by the `k8s.cluster.name` resource attribute.

## AKS — quickstart

```bash
# 0. prereqs: attach ACR (az aks update --attach-acr), ingress controller installed
az acr build -r <registry> -t pulse0:0.1.0 .

# 1. OTel Operator once
kubectl apply -f https://github.com/open-telemetry/opentelemetry-operator/releases/latest/download/opentelemetry-operator.yaml

# 2. install with the AKS overlay
helm install pulse0 ./helm/pulse0 -n observability --create-namespace \
  -f ./helm/pulse0/values-aks.yaml \
  --set auth.token=$(openssl rand -hex 24)

# 3. instrument a workload (e.g. .NET)
kubectl -n myapp patch deploy my-api --type=strategic -p \
 '{"spec":{"template":{"metadata":{"annotations":{"instrumentation.opentelemetry.io/inject-dotnet":"observability/pulse0"}}}}}'
```

## Notes that bite people on managed K8s

- **StatefulSet is single-replica by design** — SQLite is one writer. The PVC (EBS/Azure Disk) is **zone-bound**; if the node's AZ goes down, the pod reschedules only in the same AZ. For HA you need the Tier-3 ClickHouse path (`HARDENING.md`) which makes the API stateless.
- **EBS/Azure Disk = ReadWriteOnce.** Don't scale the StatefulSet above 1 with this storage. That's fine — it's a vertical workload until you move to ClickHouse.
- **Fargate (EKS):** the log-tailing `filelog` receiver needs host `/var/log/pods`, which Fargate doesn't expose. On Fargate, apps must push OTLP directly to the Pulse0 Service (traces/metrics still work; node-level logs don't). On normal EC2 node groups everything works.
- **Private clusters:** keep the Ingress `internal`/private and reach the UI via your VPN or `kubectl port-forward`.
- **Cost:** on both clouds this is 1 small node's worth of compute + one managed disk. See `STORAGE-AND-COST.md` — roughly $35–50/mo for a team-sized cluster.
```
