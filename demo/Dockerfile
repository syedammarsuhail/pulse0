FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir \
    fastapi uvicorn httpx pydantic \
    opentelemetry-distro opentelemetry-exporter-otlp-proto-http \
    opentelemetry-instrumentation-fastapi \
    opentelemetry-instrumentation-httpx \
    opentelemetry-instrumentation-logging \
    opentelemetry-instrumentation-sqlite3 \
 && opentelemetry-bootstrap --action=install
COPY . /app/
ENV OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true \
    OTEL_LOGS_EXPORTER=otlp \
    OTEL_METRICS_EXPORTER=otlp \
    OTEL_TRACES_EXPORTER=otlp \
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
