"""Demo 'frontend' service: FastAPI app that proxies calls to the backend.

Auto-instrumented via opentelemetry-instrument - sends OTLP to Pulse0.
"""
import os, random, time, logging
import httpx
from fastapi import FastAPI, HTTPException
from opentelemetry import trace, metrics

log = logging.getLogger("frontend"); logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
tracer = trace.get_tracer("frontend")
meter = metrics.get_meter("frontend")
order_counter = meter.create_counter("orders_placed", unit="1", description="Orders placed by the frontend")
cart_value = meter.create_histogram("cart_value_usd", unit="USD", description="Shopping cart value distribution")

BACKEND = os.environ.get("BACKEND_URL", "http://backend:8001")
app = FastAPI()

@app.get("/")
def home(): return {"service": "frontend", "version": "1.0.0"}

@app.get("/health")
def health(): return {"ok": True}

@app.get("/products")
async def products():
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{BACKEND}/products")
        r.raise_for_status()
        return r.json()

@app.post("/checkout")
async def checkout():
    with tracer.start_as_current_span("validate_cart") as span:
        items = random.randint(1, 5)
        total = round(random.uniform(10, 500), 2)
        span.set_attribute("cart.items", items)
        span.set_attribute("cart.total_usd", total)
        time.sleep(random.uniform(0.01, 0.05))
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.post(f"{BACKEND}/orders", json={"items": items, "total": total})
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            log.error("backend rejected order status=%s body=%s", e.response.status_code, e.response.text)
            raise HTTPException(502, f"backend failed: {e}")
    order_counter.add(1, {"status": "success"})
    cart_value.record(total)
    log.info("order placed items=%s total=%.2f", items, total)
    return {"ok": True, "items": items, "total": total}
