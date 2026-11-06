FROM public.ecr.aws/docker/library/node:20-slim
WORKDIR /app
COPY package.json .
RUN npm install --omit=dev
COPY app.js .
ENV OTEL_LOGS_EXPORTER=otlp OTEL_METRICS_EXPORTER=otlp OTEL_TRACES_EXPORTER=otlp OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
    NODE_OPTIONS="--require @opentelemetry/auto-instrumentations-node/register"
CMD ["node", "app.js"]
