# Pulse0 — Zero-Code-Change Instrumentation

**Short answer: no, your developers do not need to change code.**

Pulse0 accepts standard OpenTelemetry Protocol (OTLP). The OpenTelemetry project ships *auto-instrumentation agents* for all major languages that attach at process startup. No library imports, no decorators, no custom code. Your existing Angular / Python / Java / Node / .NET / Go apps start emitting traces, logs, and metrics with a single Kubernetes annotation or a single environment variable.

## How it works in Kubernetes (the recommended path)

The Helm chart installs a Pulse0 `Instrumentation` custom resource. The OpenTelemetry Operator watches for pods with an annotation and injects the right language agent as an init container.

```bash
# 1) one-time, cluster-wide
kubectl apply -f https://github.com/open-telemetry/opentelemetry-operator/releases/latest/download/opentelemetry-operator.yaml

# 2) install Pulse0 (auto-instrumentation CR is created automatically)
helm install pulse0 ./helm/pulse0 -n observability --create-namespace
```

Now **any** Deployment in your cluster can be instrumented by adding one annotation — the devs don't touch their code or Dockerfiles:

```yaml
# patch an existing Deployment, zero code change
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec:
  template:
    metadata:
      annotations:
        # pick the one matching your runtime:
        instrumentation.opentelemetry.io/inject-python:  "observability/pulse0"
        # instrumentation.opentelemetry.io/inject-java:   "observability/pulse0"
        # instrumentation.opentelemetry.io/inject-nodejs: "observability/pulse0"
        # instrumentation.opentelemetry.io/inject-dotnet: "observability/pulse0"
        # instrumentation.opentelemetry.io/inject-go:     "observability/pulse0"
```

Delete the pod, let it reschedule, and Pulse0 will show it under **Resources** with full traces/logs/metrics.

## One-liner patches

```bash
# instrument every Python deployment in a namespace
kubectl -n myapp get deploy -o name \
  | xargs -I{} kubectl -n myapp patch {} --type=strategic -p \
    '{"spec":{"template":{"metadata":{"annotations":{"instrumentation.opentelemetry.io/inject-python":"observability/pulse0"}}}}}'

# same for Java
kubectl -n myapp get deploy -o name \
  | xargs -I{} kubectl -n myapp patch {} --type=strategic -p \
    '{"spec":{"template":{"metadata":{"annotations":{"instrumentation.opentelemetry.io/inject-java":"observability/pulse0"}}}}}'
```

## Outside Kubernetes

| Runtime | Zero-code command |
|---------|-------------------|
| **Python** | `pip install opentelemetry-distro opentelemetry-exporter-otlp && opentelemetry-bootstrap -a install && OTEL_EXPORTER_OTLP_ENDPOINT=http://pulse0:4318 OTEL_SERVICE_NAME=my-app opentelemetry-instrument python app.py` |
| **Java / Spring Boot** | `curl -LO https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/latest/download/opentelemetry-javaagent.jar && OTEL_EXPORTER_OTLP_ENDPOINT=http://pulse0:4318 OTEL_SERVICE_NAME=my-app java -javaagent:opentelemetry-javaagent.jar -jar app.jar` |
| **Node.js** | `npm i --save @opentelemetry/auto-instrumentations-node && OTEL_EXPORTER_OTLP_ENDPOINT=http://pulse0:4318 OTEL_SERVICE_NAME=my-app node --require @opentelemetry/auto-instrumentations-node/register app.js` |
| **.NET** | `curl -sSfL https://github.com/open-telemetry/opentelemetry-dotnet-instrumentation/releases/latest/download/otel-dotnet-auto-install.sh \| sh && OTEL_EXPORTER_OTLP_ENDPOINT=http://pulse0:4318 OTEL_SERVICE_NAME=my-app ./instrument.sh dotnet run` |
| **Go** | `OTEL_SERVICE_NAME=my-app OTEL_EXPORTER_OTLP_ENDPOINT=http://pulse0:4318 sudo otel-go-instrumentation --target=./my-binary` (uses eBPF — no recompile) |
| **Ruby** | `bundle add opentelemetry-sdk opentelemetry-instrumentation-all && OTEL_EXPORTER_OTLP_ENDPOINT=http://pulse0:4318 OTEL_SERVICE_NAME=my-app ruby app.rb` |
| **PHP** | `pecl install opentelemetry && OTEL_EXPORTER_OTLP_ENDPOINT=http://pulse0:4318 OTEL_SERVICE_NAME=my-app php -d extension=opentelemetry app.php` |

For every one of these the application code is untouched.

## Angular / React / Vue (browser apps)

Browser apps can't be auto-instrumented by a startup agent (there's no process to attach to), but **they still need zero code changes**. Add one `<script>` tag to `index.html`:

```html
<!-- place in <head> of index.html; no Angular/React code change -->
<script src="https://unpkg.com/@opentelemetry/sdk-trace-web/build/sdk-trace-web.umd.js"></script>
<script src="https://unpkg.com/@opentelemetry/exporter-trace-otlp-http/build/exporter-trace-otlp-http.umd.js"></script>
<script src="https://unpkg.com/@opentelemetry/instrumentation-document-load/build/instrumentation-document-load.umd.js"></script>
<script src="https://unpkg.com/@opentelemetry/instrumentation-xml-http-request/build/instrumentation-xml-http-request.umd.js"></script>
<script src="https://unpkg.com/@opentelemetry/instrumentation-fetch/build/instrumentation-fetch.umd.js"></script>
<script>
  (function () {
    var provider = new otelSdkTraceWeb.WebTracerProvider({ resource: new otelResources.Resource({ 'service.name': 'my-angular-app' }) });
    provider.addSpanProcessor(new otelSdkTraceWeb.BatchSpanProcessor(
      new otelExporterTraceOtlpHttp.OTLPTraceExporter({ url: 'https://pulse0.example.com/v1/traces' })));
    provider.register();
    otelInstrumentation.registerInstrumentations({ instrumentations: [
      new otelInstrDocumentLoad.DocumentLoadInstrumentation(),
      new otelInstrFetch.FetchInstrumentation({ propagateTraceHeaderCorsUrls: /.*/ }),
      new otelInstrXhr.XMLHttpRequestInstrumentation({ propagateTraceHeaderCorsUrls: /.*/ }),
    ]});
  })();
</script>
```

Developers never touch Angular's `src/`. Page loads, route changes, XHR, and `fetch()` calls all show up in Pulse0 automatically, correlated end-to-end with the backend traces because trace context propagates over `traceparent` headers.

For an even cleaner setup, use the OpenTelemetry Operator's `inject-sdk` annotation on your nginx/frontend pod — the Operator will patch the HTML on the way out.

## What each app gets, with no code change

- **HTTP server spans** (incoming request, status, latency, route)
- **HTTP client spans** (outbound calls, propagation headers)
- **DB spans** (SQL, Redis, Mongo, etc. — library-specific instrumentation covers 60+ popular libraries)
- **Message queue spans** (Kafka, RabbitMQ, SQS)
- **Process metrics** (CPU, memory, GC)
- **Runtime metrics** (JVM pools, Python GC, Node event loop)
- **Application logs** (auto-correlated with trace/span IDs so Pulse0 links log lines to their trace)

That's the same signal set a commercial APM gives you — except it's standards-based and goes straight to your own Pulse0.
