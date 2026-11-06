"""Demo load generator: hits the frontend in a loop to generate telemetry."""
import os, random, time, httpx
FRONTEND = os.environ.get("FRONTEND_URL", "http://frontend:8000")
RPS = float(os.environ.get("RPS", "3"))

print(f"[loadgen] hitting {FRONTEND} at ~{RPS} rps")
with httpx.Client(timeout=10) as c:
    while True:
        try:
            if random.random() < 0.7:
                c.get(f"{FRONTEND}/products")
            else:
                c.post(f"{FRONTEND}/checkout")
        except Exception as e:
            print("loadgen error:", e)
        time.sleep(max(0.01, 1.0 / RPS + random.uniform(-0.1, 0.1)))
