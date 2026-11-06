FROM public.ecr.aws/docker/library/eclipse-temurin:21-jdk
WORKDIR /app
ADD https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/latest/download/opentelemetry-javaagent.jar /app/otel-agent.jar
COPY App.java .
RUN javac App.java
ENV OTEL_LOGS_EXPORTER=otlp OTEL_METRICS_EXPORTER=otlp OTEL_TRACES_EXPORTER=otlp OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
    JAVA_TOOL_OPTIONS="-javaagent:/app/otel-agent.jar"
CMD ["java", "-cp", ".", "App"]
