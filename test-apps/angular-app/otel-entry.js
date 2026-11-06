// This is the ONE file ops adds to a frontend build to enable RUM.
// Angular/React/Vue source code is never touched.
import { WebTracerProvider } from "@opentelemetry/sdk-trace-web";
import { BatchSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { Resource } from "@opentelemetry/resources";
import { registerInstrumentations } from "@opentelemetry/instrumentation";
import { FetchInstrumentation } from "@opentelemetry/instrumentation-fetch";
import { DocumentLoadInstrumentation } from "@opentelemetry/instrumentation-document-load";
import { ZoneContextManager } from "@opentelemetry/context-zone";

const ENDPOINT = window.PULSE0_ENDPOINT || "http://127.0.0.1:4318/v1/traces";
const provider = new WebTracerProvider({
  resource: new Resource({ "service.name": window.PULSE0_SERVICE || "angular-app", "service.version": "1.0.0" }),
});
provider.addSpanProcessor(new BatchSpanProcessor(new OTLPTraceExporter({ url: ENDPOINT })));
provider.register({ contextManager: new ZoneContextManager() });
registerInstrumentations({
  instrumentations: [
    new DocumentLoadInstrumentation(),
    new FetchInstrumentation({ propagateTraceHeaderCorsUrls: /.*/ }),
  ],
});
window.__otel_ready = true;
