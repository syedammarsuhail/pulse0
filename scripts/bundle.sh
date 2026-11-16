#!/usr/bin/env bash
# Build an air-gapped install bundle: Pulse0 image, OTel collector image and the Helm chart.
# Run on a machine WITH internet, copy ./pulse0-bundle to the offline site, then see ON-PREM.md.
#   OWNER=syedammarsuhail VERSION=0.1.0 bash scripts/bundle.sh
set -euo pipefail
OWNER="${OWNER:-syedammarsuhail}"; VERSION="${VERSION:-edge}"
OUT="${OUT:-pulse0-bundle}"; mkdir -p "$OUT"
PULSE0_IMG="ghcr.io/${OWNER}/pulse0:${VERSION}"
OTEL_IMG="otel/opentelemetry-collector-contrib:0.111.0"

docker pull "$PULSE0_IMG"; docker pull "$OTEL_IMG"
docker save "$PULSE0_IMG" "$OTEL_IMG" | gzip > "$OUT/images.tar.gz"
helm pull "oci://ghcr.io/${OWNER}/charts/pulse0" --version "${CHART_VERSION:-$VERSION}" -d "$OUT" \
  || helm package helm/pulse0 -d "$OUT"
cat > "$OUT/INSTALL.txt" <<TXT
1. On every node (or push to your registry):  gunzip -c images.tar.gz | sudo ctr -n k8s.io images import -     (k3s: sudo k3s ctr images import -)
2. helm install pulse0 ./pulse0-*.tgz -n observability --create-namespace \
     --set image.repository=ghcr.io/${OWNER}/pulse0 --set-string image.tag=${VERSION} --set image.pullPolicy=Never \
     --set persistence.storageClass=<storageclass> --set auth.token=<token>
TXT
echo "Bundle in $OUT/"; ls -la "$OUT"
