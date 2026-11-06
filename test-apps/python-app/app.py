"""Plain Flask app with ZERO OpenTelemetry imports. Represents a developer's
existing application. Instrumentation happens via `opentelemetry-instrument`
at startup - nothing in this file imports or references OTel."""
import random, time, logging
from flask import Flask, jsonify
import requests

log = logging.getLogger("python-app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = Flask(__name__)


@app.route("/")
def home():
    return jsonify(service="python-app", version="1.0.0")


@app.route("/users")
def users():
    log.info("fetching users list")
    time.sleep(random.uniform(0.01, 0.05))
    return jsonify([{"id": i, "name": f"user-{i}"} for i in range(5)])


@app.route("/order")
def order():
    log.info("processing order")
    try:
        r = requests.get("https://httpbin.org/get", timeout=3)
        r.raise_for_status()
    except Exception as e:
        log.error("upstream call failed: %s", e)
    if random.random() < 0.15:
        log.error("database error: connection refused")
        return jsonify(error="db down"), 500
    return jsonify(order_id=random.randint(1000, 9999))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=7001)
