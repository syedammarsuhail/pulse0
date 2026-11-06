#!/usr/bin/env bash
# Spin a local Kubernetes cluster and deploy Pulse0 into it end-to-end.
# Works on Linux and macOS. Picks the lightest option available:
#   1. kind    (Kubernetes in Docker)   -- preferred if Docker is running
#   2. k3d     (k3s in Docker)          -- alternative if available
#   3. minikube                          -- fallback
# Then installs Pulse0 via the bare-metal overlay and exposes the UI.
#
# Usage:
#   ./cluster.sh up     # create cluster + deploy Pulse0 (default)
#   ./cluster.sh down   # delete cluster
#   ./cluster.sh demo   # also deploy the demo apps (load, traces, health score)
set -euo pipefail

ACTION="${1:-up}"
CLUSTER="pulse0-local"
NS=observability

need() { command -v "$1" >/dev/null 2>&1; }
pick_tool() {
    if need kind;     then echo kind
    elif need k3d;    then echo k3d
    elif need minikube; then echo minikube
    else echo ""; fi
}

install_hint() {
    cat <<EOF
No local-cluster tool found. Install one of these first (pick the easiest):

  # kind (needs Docker running)
  macOS:   brew install kind
  Linux:   go install sigs.k8s.io/kind@latest   # or grab from https://kind.sigs.k8s.io/

  # k3d (needs Docker running)
  macOS:   brew install k3d
  Linux:   curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash

  # minikube (bundled drivers)
  macOS:   brew install minikube
  Linux:   https://minikube.sigs.k8s.io/docs/start/

You also need: docker (for kind/k3d), kubectl, helm.
EOF
    exit 1
}

TOOL="$(pick_tool)"
[ -n "$TOOL" ] || install_hint
echo "using: $TOOL"

cluster_up() {
    case "$TOOL" in
        kind)
            if ! kind get clusters | grep -q "^$CLUSTER\$"; then
                cat > /tmp/kind-${CLUSTER}.yaml <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  extraPortMappings:
  - containerPort: 30418
    hostPort: 4318
    protocol: TCP
EOF
                kind create cluster --name "$CLUSTER" --config /tmp/kind-${CLUSTER}.yaml
            fi
            ;;
        k3d)
            if ! k3d cluster list | grep -q "^$CLUSTER "; then
                k3d cluster create "$CLUSTER" -p "4318:30418@server:0" --k3s-arg "--disable=traefik@server:0"
            fi
            ;;
        minikube)
            minikube status -p "$CLUSTER" >/dev/null 2>&1 || minikube start -p "$CLUSTER"
            ;;
    esac
    kubectl get nodes
}

cluster_down() {
    case "$TOOL" in
        kind)     kind delete cluster --name "$CLUSTER" ;;
        k3d)      k3d cluster delete "$CLUSTER" ;;
        minikube) minikube delete -p "$CLUSTER" ;;
    esac
}

load_image() {
    docker build -t pulse0:0.1.0 .
    case "$TOOL" in
        kind)     kind load docker-image pulse0:0.1.0 --name "$CLUSTER" ;;
        k3d)      k3d image import pulse0:0.1.0 -c "$CLUSTER" ;;
        minikube) minikube image load pulse0:0.1.0 -p "$CLUSTER" ;;
    esac
}

deploy_pulse0() {
    helm upgrade --install pulse0 ./helm/pulse0 -n "$NS" --create-namespace \
        -f ./helm/pulse0/values-baremetal.yaml \
        --set image.repository=pulse0 --set image.tag=0.1.0 --set image.pullPolicy=IfNotPresent \
        --set service.type=NodePort --set service.nodePort=30418 \
        --set persistence.storageClass="standard" \
        --set auth.token="$(openssl rand -hex 24)" \
        --wait --timeout 5m
    kubectl -n "$NS" get pods,pvc,svc
    echo
    echo "  Pulse0 UI: http://localhost:4318   (port-forwarded via the cluster)"
    echo "  Health   : curl -s http://localhost:4318/healthz"
}

deploy_demo() {
    kubectl create ns demo 2>/dev/null || true
    # Simple demo workload -- a pair of HTTP services that call each other
    kubectl -n demo apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata: { name: emitter }
spec:
  replicas: 1
  selector: { matchLabels: { app: emitter } }
  template:
    metadata:
      labels: { app: emitter }
    spec:
      containers:
      - name: emit
        image: curlimages/curl:8.11.0
        command: ["sh","-c"]
        args:
        - |
          # Poor man's trace emitter: direct OTLP POST so the cluster shows data
          # without needing the OTel Operator for this smoke test.
          while true; do
            TID=$(cat /proc/sys/kernel/random/uuid | tr -d '-' | head -c 32)
            SID=$(cat /proc/sys/kernel/random/uuid | tr -d '-' | head -c 16)
            NOW=$(date +%s)000000000
            ERR=$(( RANDOM % 10 < 2 ? 2 : 1 ))
            curl -sS -X POST http://pulse0.observability.svc.cluster.local:4318/v1/traces \
              -H "Content-Type: application/json" -d "{
              \"resourceSpans\":[{\"resource\":{\"attributes\":[{\"key\":\"service.name\",\"value\":{\"stringValue\":\"demo-service\"}}]},
              \"scopeSpans\":[{\"spans\":[{\"traceId\":\"$TID\",\"spanId\":\"$SID\",\"parentSpanId\":\"\",\"name\":\"GET /orders\",\"kind\":2,
              \"startTimeUnixNano\":\"$NOW\",\"endTimeUnixNano\":\"$(( NOW + 50000000 ))\",\"status\":{\"code\":$ERR},\"attributes\":[]}]}]}]}" >/dev/null
            sleep 2
          done
EOF
    echo "  demo workload deployed into ns/demo -- it posts traces every 2s."
    echo "  wait ~20s then refresh the Resources tab -- 'demo-service' will appear"
    echo "  with a Health Score."
}

case "$ACTION" in
    up)
        cluster_up
        load_image
        deploy_pulse0
        ;;
    demo)
        cluster_up
        load_image
        deploy_pulse0
        deploy_demo
        ;;
    down)
        cluster_down
        ;;
    *)
        echo "usage: $0 [up|down|demo]"; exit 1
        ;;
esac
