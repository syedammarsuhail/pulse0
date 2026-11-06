"""Demo 'backend' service: mocks a product catalog + orders DB."""
import random, time, logging, sqlite3, os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from opentelemetry import trace

log = logging.getLogger("backend"); logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
tracer = trace.get_tracer("backend")

DB = os.environ.get("ORDER_DB", "/tmp/orders.db")
conn = sqlite3.connect(DB, check_same_thread=False)
conn.execute("CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, items INT, total REAL, ts INTEGER)")

PRODUCTS = [{"id": i, "name": f"widget-{i}", "price": round(random.uniform(5, 100), 2)} for i in range(1, 11)]
app = FastAPI()

class Order(BaseModel):
    items: int
    total: float

@app.get("/health")
def health(): return {"ok": True}

@app.get("/products")
def products():
    with tracer.start_as_current_span("query_products") as span:
        time.sleep(random.uniform(0.005, 0.03))
        span.set_attribute("db.system", "sqlite")
        span.set_attribute("db.statement", "SELECT * FROM products")
        return PRODUCTS

@app.post("/orders")
def place(o: Order):
    with tracer.start_as_current_span("insert_order") as span:
        span.set_attribute("db.system", "sqlite")
        # Simulate flakiness - 8% error, 3% slow
        r = random.random()
        if r < 0.08:
            log.error("database write failed items=%s", o.items)
            span.set_attribute("error", True)
            raise HTTPException(500, "database connection lost")
        if r < 0.11:
            log.warning("slow transaction items=%s", o.items)
            time.sleep(random.uniform(0.8, 1.5))
        else:
            time.sleep(random.uniform(0.01, 0.05))
        conn.execute("INSERT INTO orders(items,total,ts) VALUES(?,?,?)", (o.items, o.total, int(time.time())))
        conn.commit()
        return {"order_id": conn.execute("SELECT last_insert_rowid()").fetchone()[0]}
