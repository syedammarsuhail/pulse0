#!/usr/bin/env bash
# Universal Pulse0 deploy - works on EKS, AKS, bare-metal, k3s, kind.
# Usage:
#   ./deploy.sh                       # auto-detect flavor from kubectl context
#   ./deploy.sh eks | aks | baremetal # force a flavor
#
# Safe-by-default: prints target cluster + chosen overlay and waits for 'yes'.
set -euo pipefail

FLAVOR="${1:-auto}"
NS="${NS:-observability}"
TAG="${TAG:-0.1.0}"
RETENTION="${RETENTION:-168}"

need() { command -v "$1" >/dev/null || { echo "missing: $1"; exit 1; }; }
for b in kubectl helm; do need "$b"; done

CTX="$(kubectl config current-context)"
echo "kube context : $CTX"

# -------- auto-detect
if [ "$FLAVOR" = "auto" ]; then
    SERVER="$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null || true)"
    case "$SERVER" in
        *eks.amazonaws.com*|*eks.*.amazonaws.com*) FLAVOR=eks ;;
        *azmk8s.io*|*azurecontainer.io*)           FLAVOR=aks ;;
        *)                                         FLAVOR=baremetal ;;
    esac
    echo "detected     : $FLAVOR"
fi

VALUES="helm/pulse0/values-${FLAVOR}.yaml"
[ -f "$VALUES" ] || { echo "unknown flavor '$FLAVOR' (expected eks|aks|baremetal)"; exit 1; }
echo "values file  : $VALUES"
kubectl get nodes --no-headers | wc -l | xargs -I{} echo "nodes        : {}"
echo
read -r -p "Deploy Pulse0 to '$CTX' using $FLAVOR overlay? type yes: " ok
[ "$ok" = "yes" ] || { echo "aborted"; exit 1; }

# -------- storage class sanity for bare metal
EXTRA=""
if [ "$FLAVOR" = "baremetal" ]; then
    SC="$(grep -E '^\s*storageClass:' "$VALUES" | head -1 | awk '{print $2}')"
    if ! kubectl get sc "$SC" >/dev/null 2>&1; then
        echo "WARN: StorageClass '$SC' not found on this cluster."
        echo "     Available:"; kubectl get sc --no-headers | awk '{print "       - "$1}'
        read -r -p "override with (blank = cluster default): " SC_OVR
        [ -n "$SC_OVR" ] && EXTRA="--set persistence.storageClass=$SC_OVR" || EXTRA="--set persistence.storageClass="
    fi
fi

# -------- flavor-specific image push
if [ "$FLAVOR" = "eks" ]; then
    need aws; need docker
    REGION="${REGION:-$(aws configure get region || echo us-east-1)}"
    ACCT="$(aws sts get-caller-identity --query Account --output text)"
    REPO="$ACCT.dkr.ecr.$REGION.amazonaws.com/pulse0"
    aws ecr describe-repositories --repository-names pulse0 --region "$REGION" >/dev/null 2>&1 \
      || aws ecr create-repository --repository-name pulse0 --region "$REGION" >/dev/null
    aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ACCT.dkr.ecr.$REGION.amazonaws.com"
    docker build -t "$REPO:$TAG" .
    docker push "$REPO:$TAG"
    IMAGE_ARGS="--set image.repository=$REPO --set image.tag=$TAG"
elif [ "$FLAVOR" = "aks" ]; then
    need az
    REGISTRY="${REGISTRY:?set REGISTRY=<name> for ACR}"
    az acr build -r "$REGISTRY" -t "pulse0:$TAG" .
    IMAGE_ARGS="--set image.repository=${REGISTRY}.azurecr.io/pulse0 --set image.tag=$TAG"
else
    # bare metal: docker build locally and let user handle image distribution
    need docker
    docker build -t "pulse0:$TAG" .
    echo "built pulse0:$TAG locally."
    echo "distribute it: kind load docker-image pulse0:$TAG                            (kind)"
    echo "               sudo k3s ctr images import <(docker save pulse0:$TAG -)       (k3s)"
    echo "               or push to your private registry."
    IMAGE_ARGS="--set image.repository=pulse0 --set image.tag=$TAG --set image.pullPolicy=IfNotPresent"
fi

TOKEN="$(openssl rand -hex 24)"
helm upgrade --install pulse0 ./helm/pulse0 -n "$NS" --create-namespace \
    -f "$VALUES" $IMAGE_ARGS $EXTRA \
    --set auth.token="$TOKEN" \
    --set env.PULSE0_RETENTION_HOURS="$RETENTION" \
    --wait --timeout 5m

kubectl -n "$NS" get pods,pvc,svc
kubectl -n "$NS" port-forward svc/pulse0 4318:4318 >/dev/null 2>&1 &
PF=$!; sleep 4
curl -fsS localhost:4318/healthz && echo
curl -fsS localhost:4318/readyz && echo
kill $PF 2>/dev/null || true

cat <<EOF

Pulse0 is up on '$CTX' (flavor: $FLAVOR).
  UI       : kubectl -n $NS port-forward svc/pulse0 4318:4318  ->  http://localhost:4318
  API token: $TOKEN
  Teardown : helm uninstall pulse0 -n $NS && kubectl delete ns $NS
EOF
