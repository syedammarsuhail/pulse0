FROM python:3.12-slim
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py /app/server.py
COPY static /app/static
ENV PULSE0_HOST=0.0.0.0 PULSE0_PORT=4318
EXPOSE 4318
VOLUME ["/data"]
ENV PULSE0_DB=/data/pulse0.db
CMD ["python", "-u", "server.py"]
