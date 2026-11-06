FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn opentelemetry-proto protobuf "anthropic>=0.34"
COPY server.py /app/server.py
COPY static /app/static
ENV PULSE0_HOST=0.0.0.0 PULSE0_PORT=4318
EXPOSE 4318
VOLUME ["/data"]
ENV PULSE0_DB=/data/pulse0.db
CMD ["python", "-u", "server.py"]
