#!/usr/bin/env bash
# Pulse0 EKS pipeline: build on AWS (CodeBuild/ECR), deploy to a temporary EKS cluster, smoke-test, tear down.
#
#   scripts/eks.sh deploy      build image, create cluster if missing, helm install, wait for the load balancer
#   scripts/eks.sh test        run scripts/smoke.py against the deployed load balancer
#   scripts/eks.sh all         deploy + test
#   scripts/eks.sh teardown    delete the cluster and the AWS build resources (the database is kept)
#
# Needs: aws, eksctl, kubectl, helm, python. No local Docker (images are built in CodeBuild).
# Required env: SUPABASE_DB_URL   postgres connection string (kept in a Kubernetes secret, never printed)
# Optional env: AWS_REGION (us-east-1)  CLUSTER_NAME (pulse0-test)  IMAGE_TAG (git short sha)  PULSE0_TOKEN
set -euo pipefail
export MSYS_NO_PATHCONV=1

REGION="${AWS_REGION:-us-east-1}"
NAME="${CLUSTER_NAME:-pulse0-test}"
NS=observability
REPO_NAME=pulse0-e2e
ROLE=pulse0-e2e-codebuild
CB_PROJ=pulse0-e2e-build
TOKEN="${PULSE0_TOKEN:-demo-token-2024}"
TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys' >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "missing tool: a working python" >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() { printf '\n=== %s ===\n' "$*"; }
need() { command -v "$1" >/dev/null || { echo "missing tool: $1" >&2; exit 1; }; }

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
REPO="$REGISTRY/$REPO_NAME"
BUCKET="pulse0-e2e-src-$ACCOUNT"

lb_host() { kubectl -n "$NS" get svc pulse0 -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true; }

build_image() {
  say "ECR repository"
  aws ecr describe-repositories --repository-names "$REPO_NAME" --region "$REGION" >/dev/null 2>&1 ||
    aws ecr create-repository --repository-name "$REPO_NAME" --region "$REGION" >/dev/null

  say "Source bundle to S3"
  aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null 2>&1 ||
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  local zip tmp; tmp=".eks-tmp"; rm -rf "$tmp"; mkdir -p "$tmp"; zip="$tmp/src.zip"
  "$PY" - "$zip" <<'PYEOF'
import os, sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_DEFLATED) as z:
    for f in ("Dockerfile", "server.py", "requirements.txt"):
        z.write(f, f)
    for d in ("static", "demo"):
        for root, _, files in os.walk(d):
            for f in files:
                p = os.path.join(root, f)
                z.write(p, p.replace(os.sep, "/"))
PYEOF
  aws s3 cp "$zip" "s3://$BUCKET/src.zip" --region "$REGION" --only-show-errors

  say "CodeBuild project"
  if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
    aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
      '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"codebuild.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
    sleep 10
  fi
  aws iam put-role-policy --role-name "$ROLE" --policy-name build --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
   {\"Effect\":\"Allow\",\"Action\":\"ecr:GetAuthorizationToken\",\"Resource\":\"*\"},
   {\"Effect\":\"Allow\",\"Action\":[\"ecr:BatchCheckLayerAvailability\",\"ecr:InitiateLayerUpload\",\"ecr:UploadLayerPart\",\"ecr:CompleteLayerUpload\",\"ecr:PutImage\",\"ecr:BatchGetImage\",\"ecr:GetDownloadUrlForLayer\"],\"Resource\":\"arn:aws:ecr:$REGION:$ACCOUNT:repository/$REPO_NAME\"},
   {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:GetObjectVersion\"],\"Resource\":\"arn:aws:s3:::$BUCKET/*\"},
   {\"Effect\":\"Allow\",\"Action\":[\"logs:CreateLogGroup\",\"logs:CreateLogStream\",\"logs:PutLogEvents\"],\"Resource\":\"*\"}]}"
  local spec="version: 0.2
phases:
  pre_build:
    commands:
      - aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REGISTRY
  build:
    commands:
      - sed -i 's#^FROM python#FROM public.ecr.aws/docker/library/python#' Dockerfile demo/Dockerfile
      - docker build -t $REPO:$TAG .
      - docker build -t $REPO:demo ./demo
  post_build:
    commands:
      - docker push $REPO:$TAG
      - docker push $REPO:demo
"
  local proj="$tmp/project.json"
  "$PY" - "$proj" "$CB_PROJ" "$BUCKET" "$ACCOUNT" "$ROLE" "$spec" <<'PYEOF'
import json, sys
out, name, bucket, account, role, spec = sys.argv[1:7]
json.dump({"name": name,
           "source": {"type": "S3", "location": f"{bucket}/src.zip", "buildspec": spec},
           "artifacts": {"type": "NO_ARTIFACTS"},
           "environment": {"type": "LINUX_CONTAINER", "image": "aws/codebuild/standard:7.0",
                           "computeType": "BUILD_GENERAL1_SMALL", "privilegedMode": True},
           "serviceRole": f"arn:aws:iam::{account}:role/{role}"}, open(out, "w"))
PYEOF
  local existing; existing="$(aws codebuild batch-get-projects --names "$CB_PROJ" --region "$REGION" --query 'projects[0].name' --output text 2>/dev/null || true)"
  if [ -z "$existing" ] || [ "$existing" = "None" ]; then
    aws codebuild create-project --cli-input-json "file://$proj" --region "$REGION" >/dev/null
  else
    aws codebuild update-project --cli-input-json "file://$proj" --region "$REGION" >/dev/null
  fi

  say "Building image $REPO:$TAG (3-5 min)"
  local bid st
  bid="$(aws codebuild start-build --project-name "$CB_PROJ" --region "$REGION" --query build.id --output text)"
  while :; do
    sleep 10
    st="$(aws codebuild batch-get-builds --ids "$bid" --region "$REGION" --query 'builds[0].buildStatus' --output text)"
    echo "  $st"
    [ "$st" != "IN_PROGRESS" ] && break
  done
  [ "$st" = "SUCCEEDED" ] || { echo "image build failed: $st" >&2; exit 1; }
  rm -rf "$tmp"
}

ensure_cluster() {
  say "EKS cluster $NAME (15-20 min if new)"
  if aws eks describe-cluster --name "$NAME" --region "$REGION" >/dev/null 2>&1; then
    echo "cluster exists, reusing"
    aws eks update-kubeconfig --name "$NAME" --region "$REGION" >/dev/null
  else
    local cfg=".eks-tmp-cluster.yaml"
    cat >"$cfg" <<EOF
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig
metadata:
  name: $NAME
  region: $REGION
managedNodeGroups:
  - name: ng
    instanceType: t3.medium
    desiredCapacity: 2
    minSize: 2
    maxSize: 2
    volumeSize: 30
    iam:
      withAddonPolicies:
        ebs: true
addons:
  - name: aws-ebs-csi-driver
EOF
    eksctl create cluster -f "$cfg"
    rm -f "$cfg"
  fi
  # never operate on any cluster other than the one this pipeline owns
  kubectl config use-context "$(kubectl config get-contexts -o name | grep "@$NAME\.$REGION\.eksctl.io\|/$NAME\$" | head -1)" >/dev/null
  kubectl get nodes
}

deploy_app() {
  : "${SUPABASE_DB_URL:?set SUPABASE_DB_URL to the postgres connection string}"
  say "Database secret"
  kubectl create namespace "$NS" >/dev/null 2>&1 || true
  kubectl -n "$NS" delete secret pulse0-db >/dev/null 2>&1 || true
  kubectl -n "$NS" create secret generic pulse0-db --from-literal=database-url="$SUPABASE_DB_URL" >/dev/null

  say "Helm deploy"
  helm lint ./helm/pulse0
  helm upgrade --install pulse0 ./helm/pulse0 -n "$NS" --create-namespace \
    -f ./helm/pulse0/values-eks.yaml \
    --set image.repository="$REPO" --set image.tag="$TAG" --set image.pullPolicy=Always \
    --set postgres.enabled=true --set postgres.existingSecret=pulse0-db \
    --set persistence.enabled=false \
    --set service.type=LoadBalancer \
    --set auth.token="$TOKEN" \
    --set env.PULSE0_CACHE_TTL=45 \
    --set env.PULSE0_RETENTION_HOURS="${RETENTION_HOURS:-6}" \
    --wait --timeout 8m
  kubectl -n "$NS" rollout status statefulset/pulse0 --timeout=300s

  say "Demo apps"
  sed -e "s#pulse0-demo:local#$REPO:demo#" -e 's#IfNotPresent#Always#' ./demo/k8s-demo.yaml | kubectl apply -f -
  for d in backend frontend loadgen; do kubectl -n demo rollout status "deploy/$d" --timeout=300s; done
  [ -d ./test-apps/shop/k8s ] && kubectl apply -f ./test-apps/shop/k8s/ >/dev/null 2>&1 || true

  say "Waiting for load balancer"
  local lb=""
  for _ in $(seq 1 60); do lb="$(lb_host)"; [ -n "$lb" ] && break; sleep 10; done
  [ -n "$lb" ] || { echo "load balancer never appeared" >&2; exit 1; }
  echo "UI: http://$lb:4318"
  echo "ui_url=http://$lb:4318" >>"${GITHUB_OUTPUT:-/dev/null}"
}

run_tests() {
  need kubectl
  aws eks update-kubeconfig --name "$NAME" --region "$REGION" >/dev/null
  local lb; lb="$(lb_host)"
  [ -n "$lb" ] || { echo "no load balancer found for $NAME; run deploy first" >&2; exit 1; }
  local url="http://$lb:4318"
  say "Waiting for $url to answer"
  for _ in $(seq 1 60); do
    curl -fsS -m 5 "$url/healthz" >/dev/null 2>&1 && break
    sleep 10
  done
  say "Smoke test"
  "$PY" scripts/smoke.py "$url" --token "$TOKEN"
}

teardown() {
  say "Deleting EKS cluster $NAME (15-20 min)"
  eksctl delete cluster --name "$NAME" --region "$REGION" --wait || true
  aws ecr delete-repository --repository-name "$REPO_NAME" --region "$REGION" --force >/dev/null 2>&1 || true
  aws codebuild delete-project --name "$CB_PROJ" --region "$REGION" >/dev/null 2>&1 || true
  aws s3 rb "s3://$BUCKET" --region "$REGION" --force >/dev/null 2>&1 || true
  aws iam delete-role-policy --role-name "$ROLE" --policy-name build >/dev/null 2>&1 || true
  aws iam delete-role --role-name "$ROLE" >/dev/null 2>&1 || true
  echo "Torn down. The database was not touched."
}

for t in aws "$PY"; do need "$t"; done
case "${1:-}" in
  deploy)   for t in eksctl kubectl helm; do need "$t"; done; build_image; ensure_cluster; deploy_app ;;
  test)     run_tests ;;
  all)      for t in eksctl kubectl helm; do need "$t"; done; build_image; ensure_cluster; deploy_app; run_tests ;;
  teardown) need eksctl; teardown ;;
  *) echo "usage: $0 {deploy|test|all|teardown}" >&2; exit 2 ;;
esac
