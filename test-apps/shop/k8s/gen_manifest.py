"""Generate shop.yaml.  Usage: python gen_manifest.py <ecr-repo-uri> <pulse0-public-url> [collector-url] > shop.yaml
e.g. python gen_manifest.py 339713148266.dkr.ecr.us-east-1.amazonaws.com/pulse0-e2e http://my-lb:4318
"""
import sys

REPO = sys.argv[1]
PULSE0_URL = sys.argv[2]
COLLECTOR = sys.argv[3] if len(sys.argv) > 3 else "http://pulse0-collector.observability.svc.cluster.local:4319"
NS = "langtest"

# name -> (image tag, memory request, memory limit, extra env)
SERVICES = {
    "gateway":   ("shop-gateway",   "128Mi", "512Mi", {"CATALOG_URL": "http://catalog:8000", "ORDERS_URL": "http://orders:8000"}),
    "catalog":   ("shop-catalog",   "96Mi",  "384Mi", {}),
    "orders":    ("shop-orders",    "320Mi", "768Mi", {"PAYMENTS_URL": "http://payments:8000", "INVENTORY_URL": "http://inventory:8000"}),
    "payments":  ("shop-payments",  "160Mi", "512Mi", {}),
    "inventory": ("shop-inventory", "32Mi",  "128Mi", {}),
}

out = [f"apiVersion: v1\nkind: Namespace\nmetadata: {{name: {NS}}}\n"]


def env_block(env):
    return "\n".join(f'        - {{name: {k}, value: "{v}"}}' for k, v in env.items())


def deployment(name, image, req, lim, env, port=8000, replicas=1, extra_svc_port=None):
    out.append(f"""---
apiVersion: apps/v1
kind: Deployment
metadata: {{name: {name}, namespace: {NS}, labels: {{app: {name}, part-of: pulse0-shop}}}}
spec:
  replicas: {replicas}
  selector: {{matchLabels: {{app: {name}}}}}
  template:
    metadata: {{labels: {{app: {name}, part-of: pulse0-shop}}}}
    spec:
      containers:
      - name: app
        image: {REPO}:{image}
        imagePullPolicy: Always
        ports: [{{containerPort: {port}}}]
        env:
{env_block(env)}
        resources: {{requests: {{cpu: 20m, memory: {req}}}, limits: {{memory: {lim}}}}}
---
apiVersion: v1
kind: Service
metadata: {{name: {name}, namespace: {NS}}}
spec:
  selector: {{app: {name}}}
  ports: [{{port: {extra_svc_port or port}, targetPort: {port}}}]
""")


for name, (image, req, lim, extra) in SERVICES.items():
    env = {"OTEL_SERVICE_NAME": f"shop-{name}", "OTEL_EXPORTER_OTLP_ENDPOINT": COLLECTOR,
           "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
           "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=langtest,service.namespace=pulse0-shop"}
    env.update(extra)
    deployment(name, image, req, lim, env)

# storefront: nginx + browser RUM (sends spans straight from the browser to Pulse0)
deployment("storefront", "shop-storefront", "16Mi", "64Mi", {"PULSE0_URL": PULSE0_URL})
# load generator (control API on :9000)
deployment("loadgen", "shop-loadgen", "32Mi", "128Mi",
           {"TARGET": "http://gateway:8000", "PROFILE": "steady", "RPS": "5", "DURATION": "0",
            "ERROR_RATE": "0.08", "SLOW_RATE": "0.05"}, port=9000)
print("".join(out))
