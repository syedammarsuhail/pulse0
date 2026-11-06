#!/usr/bin/env bash
# Build all shop images through AWS CodeBuild (no local Docker needed).
# Each service gets its own S3 source key (shop-<svc>.zip) and ECR tag (shop-<svc>); nothing else is overwritten.
# Usage:  ./build.sh [service ...]        (default: all)
# Env:    AWS_REGION (us-east-1)  ACCOUNT (auto)  CODEBUILD_PROJECT (pulse0-e2e-build)
#         SRC_BUCKET (pulse0-e2e-src-<account>)   ECR_REPO (pulse0-e2e)
set -euo pipefail
export MSYS_NO_PATHCONV=1
HERE="$(cd "$(dirname "$0")" && pwd)"
command -v cygpath >/dev/null 2>&1 && HERE="$(cygpath -m "$HERE")"
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT="${ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}"
PROJECT="${CODEBUILD_PROJECT:-pulse0-e2e-build}"
BUCKET="${SRC_BUCKET:-pulse0-e2e-src-$ACCOUNT}"
REPO="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/${ECR_REPO:-pulse0-e2e}"
ALL=(gateway catalog orders payments inventory storefront loadgen)
SERVICES=("${@:-${ALL[@]}}")
WORK="$(mktemp -d)"; command -v cygpath >/dev/null 2>&1 && WORK="$(cygpath -m "$WORK")"; trap 'rm -rf "$WORK"' EXIT
PY="$(for p in python3 python; do command -v $p >/dev/null 2>&1 && $p -c "import sys" >/dev/null 2>&1 && { echo $p; break; }; done)"

# the storefront ships the vendored browser RUM bundle from test-apps/angular-app
cp "$HERE/../angular-app/otel-rum.js" "$HERE/storefront/otel-rum.js"

zip_dir() {  # portable zip with forward-slash paths (Compress-Archive / GNU tar both get this wrong on Windows)
  "$PY" - "$1" "$2" <<'PYEOF'
import os, sys, zipfile
src, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(src):
        for f in files:
            p = os.path.join(root, f); z.write(p, os.path.relpath(p, src).replace(os.sep, "/"))
PYEOF
}

declare -A BUILD
for S in "${SERVICES[@]}"; do
  zip_dir "$HERE/$S" "$WORK/shop-$S.zip"
  aws s3 cp "$WORK/shop-$S.zip" "s3://$BUCKET/shop-$S.zip" --region "$REGION" --only-show-errors
  cat > "$WORK/bs-$S.yml" <<EOF
version: 0.2
phases:
  pre_build:
    commands:
      - aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com
  build:
    commands:
      - docker build -t $REPO:shop-$S .
  post_build:
    commands:
      - docker push $REPO:shop-$S
EOF
  BUILD[$S]=$(aws codebuild start-build --project-name "$PROJECT" --region "$REGION" \
      --source-location-override "$BUCKET/shop-$S.zip" --buildspec-override "file://$WORK/bs-$S.yml" \
      --query build.id --output text)
  echo "started $S: ${BUILD[$S]}"
done

FAIL=0
for S in "${SERVICES[@]}"; do
  while :; do
    ST=$(aws codebuild batch-get-builds --ids "${BUILD[$S]}" --region "$REGION" --query "builds[0].buildStatus" --output text)
    [ "$ST" != "IN_PROGRESS" ] && break; sleep 10
  done
  echo "$S: $ST"; [ "$ST" = SUCCEEDED ] || FAIL=1
done
exit $FAIL
