# Service map / trace drawer "freezes the tab" report

**Report:** on the live EKS cluster, `#/map` (shop + lang graph) and the trace drawer for a 15-span, 4-service trace
made Chrome's renderer unresponsive (CDP `Page.captureScreenshot` timed out, also in fresh tabs).

**What was ruled out (could not reproduce locally, same UI code):**

| Hypothesis | Test | Result |
|---|---|---|
| Layout loop on cyclic / large graphs | `tests/test_mapkit.js` (cycles, 60n/200e, 400n/1500e, 120-deep chain) | all layouts < 15 ms, bounded by V+E |
| Heavy SVG / CSS (filters, blur, animation) | longtask observer on 64 nodes / 131 edges / 1.7k SVG elements | longest task 93 ms |
| The exact live trace (`2507f117...`) | replayed its spans + logs into a local server, opened the same URL | renders instantly |
| Slow API | `/api/map` 1.1 s on 129k spans, `/api/services` 0.4 s | not blocking the renderer |
| Native `alert/confirm/prompt` blocking CDP | grep of `static/*.js` | none in served code (only in the unused `app.js`) |

**Most likely cause: host memory pressure, not page code.** At investigation time the dev PC had 11.7 GB RAM with
2.0 GB free and a commit charge of 16.0 / 18.7 GB (27 Chrome processes = 3.2 GB, plus agent tooling); a background job had
already been reaped for low memory earlier in the session. A starved renderer produces exactly this symptom, and it is
data-independent. This is a strong correlation, not a proof.

**Hardening shipped with the new map** (so the page can never be the cause):
- cycle-safe, O(V+E) layout; virtual-node budget; sweep count scales down with size
- node cap (400) and auto-grouping above 120 nodes; level-of-detail CSS hides text when zoomed out
- flow animation only for graphs <= 80 edges and not under `prefers-reduced-motion`
- no SVG filters, drop-shadows or blur anywhere on the map

**Regression tests:** `node tests/test_mapkit.js` (also run from pytest as `test_map_layout_kit_js`) and
`test_map_endpoints_stay_fast_on_a_big_graph` in `tests/test_pulse0.py`.

**If it happens again**, capture before closing the tab: Chrome Task Manager (Shift+Esc) memory for the tab,
`performance.getEntriesByType('longtask')` via the console, and the Windows free-memory figure.
