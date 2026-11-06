#!/usr/bin/env bash
# One-shot Pulse0 deploy to AWS EKS. Run from the repo root in the VS Code terminal.
#   ./deploy-eks.sh                 # uses current kubectl context + AWS profile
#   REGION=us-east-1 ./deploy-eks.sh
# Safe by default: shows the target cluster and asks you to confirm before changing anything.
set -euo pipefail

REGION="${REGION:-$(aws configure get region || echo us-east-1)}"
NS="${NS:-observability}"
TAG="${TAG:-0.1.0}"
RETENTION="${RETENTION:-24}"      # hours; raise after the test
PVC_SIZE="${PVC_SIZE:-20Gi}"
SC="${SC:-}"                      # leave empty to use the cluster default StorageClass

need() { command -v "$1" >/dev/null || { echo "missing: $1"; exit 1; }; }
for b in aws kubectl helm docker; do need "$b"; done

ACCT="$(aws sts get-caller-identity --query Account --output text)"
CTX="$(kubectl config current-context)"
echo "AWS account : $ACCT   region: $REGION"
echo "kube context: $CTX"
kubectl get nodes -o wide | head -5
echo
echo "!! Make sure this is a NON-PROD / test cluster (not a client's production)."
read -r -p "Deploy Pulse0 to '$CTX'? type yes: " ok
[ "$ok" = "yes" ] || { echo "aborted"; exit 1; }

echo "== 1/6 storage class =="
kubectl get storageclass
[ -n "$SC" ] || echo "(using default StorageClass; set SC=gp3 to override)"

echo "== 2/6 ECR repo + image =="
REPO="$ACCT.dkr.ecr.$REGION.amazonaws.com/pulse0"
aws ecr describe-repositories --repository-names pulse0 --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name pulse0 --region "$REGION" >/dev/null
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ACCT.dkr.ecr.$REGION.amazonaws.com"
docker build -t "$REPO:$TAG" .
docker push "$REPO:$TAG"

echo "== 3/6 helm lint + template =="
helm lint ./helm/pulse0
helm template pulse0 ./helm/pulse0 -n "$NS" -f helm/pulse0/values-eks.yaml \
  --set image.repository="$REPO" --set image.tag="$TAG" \
  --set ingress.enabled=false >/dev/null && echo "template OK"

echo "== 4/6 install =="
TOKEN="$(openssl rand -hex 24)"
helm upgrade --install pulse0 ./helm/pulse0 -n "$NS" --create-namespace \
  -f helm/pulse0/values-eks.yaml \
  --set image.repository="$REPO" --set image.tag="$TAG" \
  --set ingress.enabled=false \
  --set auth.token="$TOKEN" \
  --set env.PULSE0_RETENTION_HOURS="$RETENTION" \
  --set persistence.size="$PVC_SIZE" \
  ${SC:+--set persistence.storageClass="$SC"} \
  --wait --timeout 5m

echo "== 5/6 status =="
kubectl -n "$NS" get pods,pvc

echo "== 6/6 health =="
kubectl -n "$NS" port-forward svc/pulse0 4318:4318 >/dev/null 2>&1 &
PF=$!; sleep 4
curl -fsS localhost:4318/healthz && echo
curl -fsS localhost:4318/readyz && echo
kill $PF 2>/dev/null || true

cat <<EOF

Pulse0 is up in namespace '$NS'.
  UI:      kubectl -n $NS port-forward svc/pulse0 4318:4318   ->  http://localhost:4318
  API token (save it): $TOKEN
  Zero-code instrumentation: install the OTel Operator, then annotate a deployment (see PREFLIGHT.md step 7).
  Teardown: helm uninstall pulse0 -n $NS && kubectl delete ns $NS
EOF
