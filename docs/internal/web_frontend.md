# BUDA Web Frontend

A browser front end (Scala.js) for demos: type BUDA commands, run the flat flow,
visualize candidate topologies, and see NUTS / detailed-NUTS results — with a
local Python server running the *existing* engines behind the browser. The web
layer adds only transport + serialization; it contributes **no routing logic**.

Plan of record: the phased implementation plan (Phases 0–5). This doc tracks the
as-built backend.

## Backend (`src/web/`)

Three thin modules over a single headless `BudaSession`:

- **`runner.py`** — `run_one(session, cmd_line)` drives one `.buda` command
  through `BudaSession.do_command` (the same dispatch the CLI uses), capturing
  Python + C++ output via `buda.ostream_redirect` and **returning** it as
  `{ok, error, log_lines, num_warnings, num_errors, summary}`. It catches
  `SystemExit` (raised by `do_command` on an unknown command, and by some
  handlers on bad input) so **a bad command can never kill the server**.
  `run_many` runs a batch, each command reporting its own `ok`.
- **`serialize.py`** — pure, print-free struct→JSON. `serialize_state(session)`
  returns a `StateSummary` (`stages_run`, per-bundle digest, `has_bdb`). Render
  serializers (`serialize_floorplan/hanan/topology/conn_topology/nuts/detailed`)
  land in Phases 1–2. Nothing here parses command print output — all routed data
  is read off the pybind structs. The topology traversal mirrors
  `tools/topo_snapshot.py`.
- **`server.py`** — FastAPI app. One process, one `BudaSession`, one
  `asyncio.Lock` (engine calls hold the GIL; the session is not reentrant).
  Routes build `.buda` command strings and drive them through `run_one`.
  `session_id` (default `"default"`) is carried for a later worker-per-session
  manager. `BUDA_WEB_DEV=1` enables permissive CORS for a Vite dev server.

Routes so far: `POST /api/command {cmds:[str]}`, `GET /api/state`,
`POST /api/reset`, `GET /api/health`.

### The headless requirement
Importing the command registry must not pull matplotlib. `buda_viz` (and thus
matplotlib/numpy) is imported **lazily** inside the two visualize handlers in
`src/buda_cmds/verify_viz_cmds.py`, so a headless embedder imports `buda_cli` /
`buda_cmds` matplotlib-free (asserted in `test_web_server.py`).

## Run

```bash
pip install -r src/web/requirements.txt          # fastapi, uvicorn, httpx
PYTHONPATH=build:src uvicorn web.server:app --port 8000
# then, e.g.:
curl -s -X POST localhost:8000/api/command -H 'content-type: application/json' \
  -d '{"cmds":["add_block A 0 0 100 100","def_layer 1 M1 H TOP 0.0"]}'
curl -s localhost:8000/api/state
```

## Tests
`test/tests/test_web_server.py` — `run_one` capture + `SystemExit` containment,
the no-matplotlib import (in a subprocess), and the `/api/command` → `/api/state`
flow progression via FastAPI `TestClient`.
