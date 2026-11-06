"""Backend with ZERO OpenTelemetry imports; instrumented via opentelemetry-instrument."""
import random, time, logging
from flask import Flask, jsonify

log = logging.getLogger("lang-python")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = Flask(__name__)

@app.route("/health")
def health():
    return "ok"

@app.route("/data")
def data():
    log.info("serving /data")
    time.sleep(random.uniform(0.01, 0.05))
    if random.random() < 0.10:
        log.error("database error: connection refused")
        return jsonify(error="db down"), 500
    return jsonify(items=[1, 2, 3])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
