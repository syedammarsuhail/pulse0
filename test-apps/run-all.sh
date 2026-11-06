#!/usr/bin/env bash
# Reproduce the end-to-end zero-code-change test on ONE machine.
# Proves Python, Node, Java, and Angular all report to Pulse0 without
# a single line of OpenTelemetry in their application source.
#
# Prereqs: python3, node, java (JDK), and Pulse0 running on :4318.
# Usage:   ./run-all.sh
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_TRACES_EXPORTER=otlp OTEL_METRICS_EXPORTER=otlp OTEL_LOGS_EXPORTER=otlp
export OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true

echo "==> Python (Flask) via opentelemetry-instrument — app.py has NO otel imports"
pip install -q flask requests opentelemetry-distro opentelemetry-exporter-otlp-proto-http \
    opentelemetry-instrumentation-flask opentelemetry-instrumentation-requests >/dev/null 2>&1 || true
( cd "$HERE/python-app" && OTEL_SERVICE_NAME=python-app \
    opentelemetry-instrument python3 app.py >/tmp/python.log 2>&1 & )

echo "==> Node (Express) via --require — app.js has NO otel imports"
( cd "$HERE/node-app" && npm install --silent express @opentelemetry/api \
    @opentelemetry/auto-instrumentations-node >/dev/null 2>&1
  OTEL_SERVICE_NAME=node-app \
    node --require @opentelemetry/auto-instrumentations-node/register app.js >/tmp/node.log 2>&1 & )

echo "==> Java via -javaagent — App.java has NO otel imports"
if [ ! -f "$HERE/otel-javaagent.jar" ]; then
  curl -sSL -o "$HERE/otel-javaagent.jar" \
    https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v2.9.0/opentelemetry-javaagent.jar
fi
( cd "$HERE/java-app" && javac App.java && OTEL_SERVICE_NAME=java-app \
    java -javaagent:"$HERE/otel-javaagent.jar" App >/tmp/java.log 2>&1 & )

echo "==> Angular/browser: serve the SPA (otel-rum.js is a vendored bundle, app code untouched)"
( cd "$HERE/angular-app" && python3 -m http.server 7005 >/tmp/angular.log 2>&1 & )

sleep 12
echo "==> Generating traffic..."
for i in $(seq 1 40); do
  curl -s http://127.0.0.1:7001/users  >/dev/null; curl -s http://127.0.0.1:7001/order >/dev/null
  curl -s http://127.0.0.1:7002/products>/dev/null; curl -s http://127.0.0.1:7002/cart  >/dev/null
  curl -s http://127.0.0.1:7003/invoices>/dev/null; curl -s http://127.0.0.1:7003/charge>/dev/null
done
echo
echo "Done. Open http://127.0.0.1:4318 — you should see python-app, node-app, java-app"
echo "under Resources, a java-app -> python-app edge on the Service map, traces, logs, and"
echo "(after ~60s) JVM / Node event-loop / HTTP metrics under Metrics."
echo "For the browser app, open http://127.0.0.1:7005/index.html and click the buttons."
