// Plain Express app with ZERO OpenTelemetry imports or configuration.
// Developer's code stays untouched; instrumentation attaches via --require.
const express = require("express");
const app = express();

app.get("/", (_, res) => res.json({ service: "node-app", version: "1.0.0" }));

app.get("/products", async (_, res) => {
  console.log("listing products");
  await new Promise(r => setTimeout(r, 10 + Math.random() * 30));
  res.json([{ id: 1, name: "widget" }, { id: 2, name: "gizmo" }]);
});

app.get("/cart", async (_, res) => {
  console.log("computing cart");
  if (Math.random() < 0.15) {
    console.error("payment gateway timeout");
    return res.status(502).json({ error: "payment gateway timeout" });
  }
  res.json({ total: 42.50 });
});

app.listen(7002, "127.0.0.1", () => console.log("node-app on :7002"));
