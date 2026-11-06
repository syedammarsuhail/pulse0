FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir flask opentelemetry-distro opentelemetry-exporter-otlp-proto-http \
 && opentelemetry-bootstrap --action=install
COPY app.py .
ENV OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED=true OTEL_LOGS_EXPORTER=otlp OTEL_METRICS_EXPORTER=otlp \
    OTEL_TRACES_EXPORTER=otlp OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
CMD ["opentelemetry-instrument", "python", "app.py"]
