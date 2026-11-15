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

  if [ -d test-apps/shop ] && [ "${SKIP_SHOP:-0}" != "1" ]; then
    say "Building shop demo images"
    bash test-apps/shop/build.sh
  fi
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
  # a brand-new EKS endpoint can take a few minutes to resolve; retry instead of aborting
  local i
  for i in $(seq 1 30); do kubectl get nodes >/dev/null 2>&1 && break; echo "  waiting for the EKS API endpoint to resolve ($i/30)"; sleep 10; done
  kubectl get nodes
}

# Create an RDS Postgres instance INSIDE the cluster's own VPC (private, reachable
# only from the cluster) and export SUPABASE_DB_URL so deploy_app wires Pulse0 to it.
RDS_STATE="$ROOT/.eks-rds-state"
ensure_rds() {
  need aws; need python
  say "In-VPC RDS Postgres (${RDS_CLASS:-db.t4g.small})"
  aws eks update-kubeconfig --name "$NAME" --region "$REGION" >/dev/null 2>&1 || true
  local vpc csg cidr subs rsg rid ep pw
  vpc="$(aws eks describe-cluster --name "$NAME" --region "$REGION" --query cluster.resourcesVpcConfig.vpcId --output text)"
  csg="$(aws eks describe-cluster --name "$NAME" --region "$REGION" --query cluster.resourcesVpcConfig.clusterSecurityGroupId --output text)"
  cidr="$(aws ec2 describe-vpcs --vpc-ids "$vpc" --region "$REGION" --query 'Vpcs[0].CidrBlock' --output text)"
  subs="$(aws ec2 describe-subnets --filters Name=vpc-id,Values=$vpc --region "$REGION" --query 'Subnets[].SubnetId' --output text)"
  rid="$NAME-db"

  rsg="$(aws ec2 create-security-group --group-name "$NAME-rds-sg" --description "pulse0 in-vpc rds" \
        --vpc-id "$vpc" --region "$REGION" --query GroupId --output text 2>/dev/null \
        || aws ec2 describe-security-groups --filters Name=group-name,Values=$NAME-rds-sg Name=vpc-id,Values=$vpc \
             --region "$REGION" --query 'SecurityGroups[0].GroupId' --output text)"
  aws ec2 authorize-security-group-ingress --group-id "$rsg" --protocol tcp --port 5432 --source-group "$csg" --region "$REGION" >/dev/null 2>&1 || true
  aws ec2 authorize-security-group-ingress --group-id "$rsg" --protocol tcp --port 5432 --cidr "$cidr" --region "$REGION" >/dev/null 2>&1 || true
  aws rds create-db-subnet-group --db-subnet-group-name "$NAME-sng" --db-subnet-group-description "pulse0" \
    --subnet-ids $subs --region "$REGION" >/dev/null 2>&1 || true

  if aws rds describe-db-instances --db-instance-identifier "$rid" --region "$REGION" >/dev/null 2>&1; then
    echo "  reusing existing $rid — need its password"
    : "${RDS_PASSWORD:?existing RDS found; set RDS_PASSWORD to reuse it, or teardown first}"
    pw="$RDS_PASSWORD"
  else
    pw="$(python -c 'import secrets,string;print("".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(24)))')"
    aws rds create-db-instance --db-instance-identifier "$rid" \
      --db-instance-class "${RDS_CLASS:-db.t4g.small}" --engine postgres \
      --master-username pulse0admin --master-user-password "$pw" \
      --allocated-storage 20 --storage-type gp3 \
      --db-subnet-group-name "$NAME-sng" --vpc-security-group-ids "$rsg" \
      --no-publicly-accessible --backup-retention-period 0 --no-multi-az \
      --no-deletion-protection --region "$REGION" >/dev/null
  fi
  { echo "RDS_ID=$rid"; echo "RDS_SG=$rsg"; echo "RDS_SNG=$NAME-sng"; echo "RDS_PASSWORD=$pw"; } > "$RDS_STATE"
  say "Waiting for RDS to become available (8-12 min)"
  aws rds wait db-instance-available --db-instance-identifier "$rid" --region "$REGION"
  ep="$(aws rds describe-db-instances --db-instance-identifier "$rid" --region "$REGION" --query 'DBInstances[0].Endpoint.Address' --output text)"
  export SUPABASE_DB_URL="postgresql://pulse0admin:${pw}@${ep}:5432/postgres"
  echo "  RDS ready (private) at $ep in VPC $vpc"
}

teardown_rds() {
  [ -f "$RDS_STATE" ] || return 0
  # shellcheck disable=SC1090
  source "$RDS_STATE"
  say "Deleting in-VPC RDS ${RDS_ID:-}"
  aws rds delete-db-instance --db-instance-identifier "$RDS_ID" --skip-final-snapshot --delete-automated-backups --region "$REGION" >/dev/null 2>&1 || true
  aws rds wait db-instance-deleted --db-instance-identifier "$RDS_ID" --region "$REGION" 2>/dev/null || true
  aws rds delete-db-subnet-group --db-subnet-group-name "$RDS_SNG" --region "$REGION" >/dev/null 2>&1 || true
  aws ec2 delete-security-group --group-id "$RDS_SG" --region "$REGION" >/dev/null 2>&1 || true
  rm -f "$RDS_STATE"
}

deploy_app() {
  local dbargs
  if [ -n "${SUPABASE_DB_URL:-}" ]; then
    say "Database secret (Postgres backend)"
    kubectl create namespace "$NS" >/dev/null 2>&1 || true
    kubectl -n "$NS" delete secret pulse0-db >/dev/null 2>&1 || true
    kubectl -n "$NS" create secret generic pulse0-db --from-literal=database-url="$SUPABASE_DB_URL" >/dev/null
    dbargs="--set postgres.enabled=true --set postgres.existingSecret=pulse0-db --set persistence.enabled=false"
  else
    say "SQLite-on-EBS backend (no Postgres URL given)"
    dbargs="--set postgres.enabled=false --set persistence.enabled=true"
  fi

  say "Helm deploy"
  helm lint ./helm/pulse0
  helm upgrade --install pulse0 ./helm/pulse0 -n "$NS" --create-namespace \
    -f ./helm/pulse0/values-eks.yaml \
    --set image.repository="$REPO" --set-string image.tag="$TAG" --set image.pullPolicy=Always \
    $dbargs \
    --set service.type=LoadBalancer \
    --set auth.token="$TOKEN" \
    --set-string env.PULSE0_CACHE_TTL=45 \
    --set-string env.PULSE0_RETENTION_HOURS="${RETENTION_HOURS:-6}" \
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
  echo "UI (ELB DNS): http://$lb:4318"
  echo "ui_url=http://$lb:4318" >>"${GITHUB_OUTPUT:-/dev/null}"
  maybe_dns "$lb"
}

# Point a friendly Route53 hostname at the load balancer (CNAME).
#   PULSE0_DNS_NAME        desired hostname, e.g. pulse0.example.com
#   PULSE0_HOSTED_ZONE_ID  the Route53 hosted zone that owns that domain
dns() {
  need aws
  : "${PULSE0_DNS_NAME:?set PULSE0_DNS_NAME to the desired hostname, e.g. pulse0.example.com}"
  : "${PULSE0_HOSTED_ZONE_ID:?set PULSE0_HOSTED_ZONE_ID to your Route53 hosted zone id}"
  local lb="${1:-}"
  if [ -z "$lb" ]; then
    aws eks update-kubeconfig --name "$NAME" --region "$REGION" >/dev/null 2>&1 || true
    lb="$(lb_host)"
  fi
  [ -n "$lb" ] || { echo "no load balancer found for $NAME; run deploy first" >&2; exit 1; }
  say "Route53 CNAME $PULSE0_DNS_NAME -> $lb"
  local batch; batch=".eks-dns.json"
  cat >"$batch" <<EOF
{"Comment":"pulse0 endpoint","Changes":[{"Action":"UPSERT","ResourceRecordSet":{"Name":"$PULSE0_DNS_NAME","Type":"CNAME","TTL":60,"ResourceRecords":[{"Value":"$lb"}]}}]}
EOF
  aws route53 change-resource-record-sets --hosted-zone-id "$PULSE0_HOSTED_ZONE_ID" --change-batch "file://$batch" >/dev/null
  rm -f "$batch"
  echo "UI (DNS): http://$PULSE0_DNS_NAME:4318   (propagates in ~1-2 min)"
  echo "dns_url=http://$PULSE0_DNS_NAME:4318" >>"${GITHUB_OUTPUT:-/dev/null}"
}

# Run dns() only when both env vars are set; never fail the deploy if they are not.
maybe_dns() {
  [ -n "${PULSE0_DNS_NAME:-}" ] && [ -n "${PULSE0_HOSTED_ZONE_ID:-}" ] || {
    echo "(no custom DNS: set PULSE0_DNS_NAME + PULSE0_HOSTED_ZONE_ID to get a friendly hostname)"
    return 0
  }
  dns "${1:-}"
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
  if [ -n "${PULSE0_DNS_NAME:-}" ] && [ -n "${PULSE0_HOSTED_ZONE_ID:-}" ]; then
    say "Removing Route53 record $PULSE0_DNS_NAME"
    local cur
    cur="$(aws route53 list-resource-record-sets --hosted-zone-id "$PULSE0_HOSTED_ZONE_ID" \
      --query "ResourceRecordSets[?Name=='${PULSE0_DNS_NAME}.'].ResourceRecords[0].Value | [0]" --output text 2>/dev/null || true)"
    if [ -n "$cur" ] && [ "$cur" != "None" ]; then
      cat >.eks-dns.json <<EOF
{"Changes":[{"Action":"DELETE","ResourceRecordSet":{"Name":"$PULSE0_DNS_NAME","Type":"CNAME","TTL":60,"ResourceRecords":[{"Value":"$cur"}]}}]}
EOF
      aws route53 change-resource-record-sets --hosted-zone-id "$PULSE0_HOSTED_ZONE_ID" --change-batch file://.eks-dns.json >/dev/null 2>&1 || true
      rm -f .eks-dns.json
    fi
  fi
  teardown_rds
  say "Removing load balancer service"
  if aws eks update-kubeconfig --name "$NAME" --region "$REGION" >/dev/null 2>&1; then
    kubectl -n "$NS" delete svc pulse0 --ignore-not-found --timeout=180s || true
  fi
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
  deploy)   for t in eksctl kubectl helm; do need "$t"; done; [ "${SKIP_BUILD:-0}" = 1 ] || build_image; ensure_cluster; [ "${RDS_IN_VPC:-0}" = 1 ] && ensure_rds; deploy_app ;;
  test)     run_tests ;;
  all)      for t in eksctl kubectl helm; do need "$t"; done; [ "${SKIP_BUILD:-0}" = 1 ] || build_image; ensure_cluster; [ "${RDS_IN_VPC:-0}" = 1 ] && ensure_rds; deploy_app; run_tests ;;
  dns)      need kubectl; dns ;;
  rds)      need kubectl; ensure_rds; echo "SUPABASE_DB_URL set for this shell only"; ;;
  teardown) need eksctl; teardown ;;
  *) echo "usage: $0 {deploy|test|all|dns|rds|teardown}  (set RDS_IN_VPC=1 for an in-VPC RDS backend)" >&2; exit 2 ;;
esac
