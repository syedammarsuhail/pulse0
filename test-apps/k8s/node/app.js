// No OpenTelemetry imports. Attached via: node --require @opentelemetry/auto-instrumentations-node/register app.js
const express = require("express");
const pino = require("pino");
const log = pino();
const app = express();
const BACKEND = process.env.BACKEND_URL || "http://lang-python:8000";
app.get("/health", (_, res) => res.send("ok"));
app.get("/work", async (_, res) => {
  log.info("handling /work");
  try {
    const r = await fetch(BACKEND + "/data");
    if (!r.ok) throw new Error("backend " + r.status);
    res.json(await r.json());
  } catch (e) {
    log.error({ err: e.message }, "work failed");
    res.status(500).json({ error: e.message });
  }
});
app.listen(8000, () => log.info("listening on 8000"));
