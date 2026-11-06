"""Catalog service. ZERO OpenTelemetry imports; instrumented by opentelemetry-instrument (flask + sqlite3 spans)."""
import logging, random, sqlite3, threading, time
from flask import Flask, jsonify, request

SELF = "catalog"
log = logging.getLogger("catalog")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = Flask(__name__)
db = sqlite3.connect(":memory:", check_same_thread=False)
lock = threading.Lock()
db.execute("CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT, price REAL)")
db.executemany("INSERT INTO products(name, price) VALUES (?, ?)",
               [(f"{w} {i}", round(random.uniform(5, 200), 2)) for i, w in enumerate(["widget", "gizmo", "gadget", "doohickey", "sprocket"] * 10)])
db.commit()


def chaos():
    c = request.headers.get("x-chaos", "")
    if c == f"slow:{SELF}":
        log.warning("injected slowness")
        time.sleep(random.uniform(1.0, 1.8))
    if c == f"error:{SELF}":
        log.error("injected failure: database connection lost")
        return jsonify(error="database connection lost"), 500


@app.route("/health")
def health():
    return "ok"


@app.route("/products")
def products():
    bad = chaos()
    if bad:
        return bad
    with lock:
        rows = db.execute("SELECT id, name, price FROM products ORDER BY id LIMIT 20").fetchall()
    log.info("listed %d products", len(rows))
    return jsonify(products=[dict(id=r[0], name=r[1], price=r[2]) for r in rows])


@app.route("/search")
def search():
    bad = chaos()
    if bad:
        return bad
    q = request.args.get("q", "")
    with lock:
        rows = db.execute("SELECT id, name, price FROM products WHERE name LIKE ? LIMIT 20", (f"%{q}%",)).fetchall()
    log.info("search q=%s hits=%d", q, len(rows))
    return jsonify(hits=len(rows), products=[dict(id=r[0], name=r[1], price=r[2]) for r in rows])


@app.route("/stats")
def stats():
    bad = chaos()
    if bad:
        return bad
    with lock:
        n = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    return jsonify(products=n)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
