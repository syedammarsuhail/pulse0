#!/usr/bin/env bash
# One-command deploy of the Pulse0 polyglot shop demo.
#   KUBE_CONTEXT=<ctx> PULSE0_URL=http://<pulse0-host>:4318 ./deploy.sh [--skip-build]
# Env: KUBE_CONTEXT (required, never defaults to the current context), PULSE0_URL (browser-reachable Pulse0 URL, used by the
#      storefront's RUM script), COLLECTOR_URL (in-cluster OTLP endpoint), AWS_REGION, ACCOUNT, ECR_REPO.
set -euo pipefail
export MSYS_NO_PATHCONV=1
HERE="$(cd "$(dirname "$0")" && pwd)"
command -v cygpath >/dev/null 2>&1 && HERE="$(cygpath -m "$HERE")"
: "${KUBE_CONTEXT:?set KUBE_CONTEXT to the target cluster context (it is never taken from the current context)}"
: "${PULSE0_URL:?set PULSE0_URL, e.g. http://<load-balancer-host>:4318}"
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT="${ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}"
REPO="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/${ECR_REPO:-pulse0-e2e}"
PY="$(for p in python3 python; do command -v $p >/dev/null 2>&1 && $p -c "import sys" >/dev/null 2>&1 && { echo $p; break; }; done)"
K="kubectl --context $KUBE_CONTEXT"

[ "${1:-}" = "--skip-build" ] || "$HERE/build.sh"
"$PY" "$HERE/k8s/gen_manifest.py" "$REPO" "$PULSE0_URL" ${COLLECTOR_URL:+"$COLLECTOR_URL"} > "$HERE/k8s/shop.yaml"
$K apply -f "$HERE/k8s/shop.yaml"
for d in catalog inventory payments orders gateway storefront loadgen; do $K -n langtest rollout status deploy/$d --timeout=300s; done
cat <<EOF

Shop is running in namespace langtest on $KUBE_CONTEXT.
  loadgen status : $K -n langtest port-forward svc/loadgen 9000:9000   then   curl localhost:9000/status
  switch profile : curl -X POST 'localhost:9000/control?profile=spike&rps=8&duration=60'
  storefront UI  : $K -n langtest port-forward svc/storefront 9100:8000   then open http://localhost:9100
  delete it      : $K delete namespace langtest
EOF
