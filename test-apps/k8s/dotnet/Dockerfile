FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
COPY lang-dotnet.csproj Program.cs ./
RUN dotnet publish -c Release -o /out

FROM mcr.microsoft.com/dotnet/aspnet:8.0
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates && rm -rf /var/lib/apt/lists/*
ARG OTEL_VER=v1.9.0
RUN curl -fsSL -o /tmp/otel.zip https://github.com/open-telemetry/opentelemetry-dotnet-instrumentation/releases/download/${OTEL_VER}/opentelemetry-dotnet-instrumentation-linux-glibc-x64.zip \
 && mkdir -p /otel && unzip -q /tmp/otel.zip -d /otel && rm /tmp/otel.zip
WORKDIR /app
COPY --from=build /out .
ENV OTEL_DOTNET_AUTO_HOME=/otel \
    CORECLR_ENABLE_PROFILING=1 \
    CORECLR_PROFILER={918728DD-259F-4A6A-AC2B-B85E1B658318} \
    CORECLR_PROFILER_PATH=/otel/linux-x64/OpenTelemetry.AutoInstrumentation.Native.so \
    DOTNET_ADDITIONAL_DEPS=/otel/AdditionalDeps \
    DOTNET_SHARED_STORE=/otel/store \
    DOTNET_STARTUP_HOOKS=/otel/net/OpenTelemetry.AutoInstrumentation.StartupHook.dll \
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf OTEL_TRACES_EXPORTER=otlp OTEL_METRICS_EXPORTER=otlp OTEL_LOGS_EXPORTER=otlp
ENTRYPOINT ["dotnet", "lang-dotnet.dll"]
