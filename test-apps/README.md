# Pulse0 Zero-Code End-to-End Test Kit

These four tiny apps prove that **no developer code change** is needed. Each app's
source has **zero** OpenTelemetry imports — instrumentation is attached at startup.

| App | File | How it's instrumented | What's changed in the code |
|-----|------|----------------------|----------------------------|
| **Python** | `python-app/app.py` | `opentelemetry-instrument python3 app.py` | nothing |
| **Node** | `node-app/app.js` | `node --require @opentelemetry/auto-instrumentations-node/register app.js` | nothing |
| **Java** | `java-app/App.java` | `java -javaagent:otel-javaagent.jar App` | nothing |
| **Angular/browser** | `angular-app/index.html` | one vendored `<script src="otel-rum.js">` added by ops | nothing in the app's own components |

Open any of these source files — you'll see ordinary Flask / Express / Java HTTP / HTML. No tracing code.

## Run it

```bash
# 1. start Pulse0 (in the repo root)
python3 server.py          # or: docker run -p 4318:4318 pulse0

# 2. run the four apps + traffic
cd test-apps && ./run-all.sh

# 3. open http://localhost:4318
```

## What this verifies (observed in a real run)

- **Resources**: `python-app`, `node-app`, `java-app`, `angular-app` all appear with RED metrics.
- **Service map**: an automatically-derived `java-app → python-app` edge — the trace context
  propagated across a JVM process and a Python process with no code change on either side.
- **Traces**: a single distributed trace shows `[java-app] GET` → `[python-app] GET /users`.
- **Logs**: 288 Python log lines and 86 Java log lines captured automatically, trace-correlated.
- **Metrics**: 37 metric series auto-collected — `jvm.memory.used`, `jvm.gc.duration`,
  `nodejs.eventloop.delay.*`, `http.server.duration`, `http.client.request.duration`, etc.
- **Browser RUM**: `documentLoad` traces (documentFetch + documentLoad + resourceFetch spans)
  from the Angular-style SPA.

## Note on the browser app

Browser apps can't be attached to by a startup agent (there's no server process), so the
OTel Web SDK is bundled once into `otel-rum.js` (via `esbuild otel-entry.js --bundle`) and
referenced with a single `<script>` tag. The Angular/React/Vue component code is never touched.
`otel-entry.js` is the only file ops maintains; rebuild the bundle with:

```bash
cd angular-app && npm i && ./node_modules/.bin/esbuild otel-entry.js --bundle --format=iife --outfile=otel-rum.js --minify
```
