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

Routes: `POST /api/command {cmds:[str]}`, `GET /api/state`,
`GET /api/demos` (the built-in demo catalog — see below),
`GET /api/render/{generation,nuts,detailed}?bundle=&candidate=`,
`POST /api/select {bundle,candidate}` (pin a candidate),
`POST /api/unpin {bundle}` (clear the pin — inverse of select),
`POST /api/edit/{open,op,commit,abort}`,
`POST /api/bdb/{open,save,load_pipeline}` (checkpoint), `POST /api/reset`,
`GET /api/health`, `WS /api/ws?session_id=` + `POST /api/stage/{stage}` (progress
streaming — see below). Checkpoint flow: `open` the BDB BEFORE routing so the stages
persist into it live; a fresh session resumes by replaying the setup commands,
`open`ing the same file, and `load_pipeline` (rehydrates bundles + candidates +
plan + NUTS). `save` is a save-as snapshot to a distinct file.
The edit routes drive the headless `edit_*` engine through `run_one` (open a
working copy on a candidate or `new`, apply one `edit_*` op, commit a `USER`
candidate or abort); each response carries the live `EditVerdict`
(`serialize_verdict`, re-judged via `buda.edit_verdict`) + the working-copy
topology so the client shows connectivity violations inline. The render serializers
(`serialize_floorplan/hanan/topology/conn_topology/bundle/generation`,
`serialize_nuts`, `serialize_detailed`) mirror `tools/topo_snapshot.py`;
ConnTopology slide/pull sentinels serialize as JSON `null`, conn order is
preserved (NUTS tie-breaks depend on it), and each detailed bit-wire inherits
its bus `TrackSegment`'s orientation (`_orient_map`, since `NetSegment` carries
no direction flag). Milestone 1 (Phases 0–2) renders the whole flat flow:
floorplan + candidate topologies (generation), placed bus segments (nuts), and
per-bit wires + vias (detailed).

**Per-bundle floorplans (hier).** `serialize_generation` keeps a TOP-LEVEL
`floorplan`/`hanan` (backward-compat + the flat case + the golden), but each
serialized bundle now ALSO carries its OWN `floorplan`/`hanan`, computed by
`bundle_floorplan(session, w)` — the frame the bundle's candidates were generated
in. For the FLAT flow every bundle shares `session.fp`, so the per-bundle fields
duplicate the top-level ones (and the b44 golden's top-level + candidate/analysis
fields are byte-unchanged; only the additive per-bundle keys grew it). For the
HIERARCHY-AWARE flow a pre-expansion HBundle lives in a cell-local / depth /
endpoint frame that DIFFERS from a top-level bundle's die frame (e.g. a template
bundle over `pa_i`/`pb_i`/`pc_i` inside `proc_cell` vs. a top bundle over
`proc_a/…`), so an unfocused multi-bundle render — which one shared top-level
floorplan could not represent — carries the right frame per bundle. The reference
client prefers `bundle.floorplan`/`bundle.hanan` when present (`activeFrame()` in
the generation view) and falls back to the top-level payload otherwise, so its
bbox/viewBox math works off whichever floorplan is used. Covered by
`test_web_hier.py` (flat: per-bundle == top-level; hier `hier_mixed` fixture:
≥2 bundles resolve to distinct block-set frames).

### Demo catalog (`GET /api/demos`, `src/web/demos.py`)

`GET /api/demos` returns `{demos:[{key, label, setup, stages}]}` — the built-in
demos the client's picker offers, so one "load setup + click the stage buttons"
UX drives **both** the flat and the hierarchy-aware flow without the client
knowing either command sequence:

- **`flat`** — the single-bundle b44 bus (3 blocks, 52 bits). `stages` maps each
  button to the flat commands (`run_bundler` / `generate_topologies` /
  `run_planner` / `run_nuts` / `run_detailed_nuts`).
- **`hier`** — a depth-2 hierarchy (multi-pin stress, 35 buses). Its `setup` is
  **extracted** from `flow/hbundles/06_multipin_stress.buda` (everything up to
  the first pipeline command, comments stripped, the file-relative
  `source ../tracks/…` rewritten repo-root-relative) rather than duplicated, so
  editing the flow keeps the demo current (the catalog is rebuilt per request).
  Its `stages` maps to the hier commands (`run_hier_bundler depth 2` /
  `generate_hier_topologies` / `run_planner hier 5` / `run_nuts` /
  `run_detailed_nuts`).

`stages` keys are the button ids (`bundler`/`topologies`/`planner`/`nuts`/
`dnuts`), so the reference client's `runDemoStage(key)` just looks up the active
demo's command for that button. For the WS-streamed long stages
(planner/nuts/dnuts) the reference client splits the demo command into the
`/api/stage/{stage}` key + args (e.g. `run_planner hier 5` → stage `planner`,
args `hier 5`) so those keep the progress path; the Scala client (no WS) runs
every stage through `/api/command`. Both flows persist nothing new server-side —
the catalog is pure transport over the same `.buda` vocabulary.

### Progress streaming (`WS /api/ws` + `POST /api/stage/{stage}`)

The long stages (`run_planner`/`run_nuts`/`run_detailed_nuts`/`ripup_reroute`/
`negotiate_congestion`) can take tens of seconds. They stream coarse progress to
any connected WebSocket client instead of the UI just blocking on the request.

- **`WS /api/ws?session_id=`** — a client connects **once**; the server registers
  it in a per-session `clients` set (`_Session.clients`) and pushes JSON frames.
  On connect it sends `{"kind":"hello","session_id","state":<StateSummary>|null}`.
  The `state` read is guarded by `runner.snapshot_if_idle` (a **non-blocking**
  acquire of the engine lock, below): if a stage is mid-run it sends `state:null`
  rather than reading a session another thread is mutating — the client ignores
  hello `state` and gets the full state in the stage `done` frame anyway. The
  socket carries no commands (drive stages via the POST below); its reads are
  drained only to detect disconnect. A dropped socket is pruned on the next
  broadcast (a `send_json` error `discard`s it) — it never kills the stage or the
  server, and the WS handler swallows `WebSocketDisconnect`.
- **`POST /api/stage/{stage}` `{args?, session_id?}`** — `stage` ∈
  `{planner, nuts, detailed_nuts, ripup, negotiate}` (mapped to the `.buda`
  command via `_STAGE_CMDS`; `args` is appended verbatim, e.g. `ripup` + `"8"` →
  `ripup_reroute 8`). Holds the session lock for the **whole** engine call (state
  is not reentrant) but runs the blocking `run_one` in
  `loop.run_in_executor(None, …)` so the event loop stays free to push heartbeat
  frames while it runs. An unknown stage returns `{error, stages}` (never 500s).
  Returns the same `{result, state, notable}` shape synchronously, so a client
  with no WS still gets the outcome.

Because a stage runs in a thread-pool executor, distinct per-session asyncio
locks no longer serialize engine calls across sessions/threads. `run_one`/
`run_many` therefore hold a **process-wide** `threading.Lock`
(`runner._ENGINE_LOCK`) across the capture region: `run_one` swaps the *global*
`sys.stdout`/`sys.stderr` and enters `buda.ostream_redirect` (a process-global
C++ stream redirect), so two runs must never overlap even for different
`session_id`s (they would cross-capture each other's log output). The lock is a
leaf (acquired only around the capture region, never while holding another lock),
so it cannot deadlock. `runner.snapshot_if_idle(fn)` is the read-side companion:
it runs `fn()` under a **non-blocking** acquire and returns `None` when the
engine is busy — used by the WS `hello` frame so a connect never races a running
stage's mutation.

Frame schema (`kind`):
- `hello` — `{session_id, state}` on connect.
- `stage` `status:"started"` — `{name, command}` before the engine call.
- `heartbeat` — `{name, elapsed}` every ~0.5 s while running (fires in the GIL
  windows between the stage's C++ calls — coarse, no C++-loop instrumentation).
- `stage` `status:"done"` — `{name, elapsed, state:<StateSummary>, summary,
  notable:[str], result}`. `notable` is the last few captured log lines matching
  the metric/outcome keywords (`_notable_lines` over `run_one`'s `log_lines` —
  the ripup/nuts `done:`/`placed`/`overlap` lines), so the client shows the
  result without re-fetching.

Broadcasts go to every client of the session, so a second viewer sees another
tab's stage progress live. The reference client opens the WS on load
(`connectWS`, auto-reconnect on close), shows a pulsing "running <stage>… <s>s"
indicator on `started`/`heartbeat`, clears it on `done`, forwards `notable` to
the log, and refreshes state — the planner/nuts/dnuts/ripup buttons go through
`/api/stage/*` (the instant bundler/topology buttons stay on `/api/command`).

## Frontend

- **`src/web/static/index.html`** — a vanilla-SVG **reference client**, served at
  `/` (StaticFiles). The immediate, toolchain-free way to drive the demo in a
  browser, and the porting reference for the Scala.js renderer. Command console +
  stage buttons (bundler→dnuts) + a `topo/NUTS/detailed` view switch. Browser-
  verified (Playwright/Chromium): the generation view draws the floorplan +
  candidate segments (stepping the 35 b44 candidates); the NUTS view draws placed
  track footprints + centerlines; the detailed view draws all 104 bit-wires + 52
  vias of the routed bus.
- **`web/`** — the Scala.js project (sbt + sbt-scalajs; `ApiClient`, `Renderer`,
  `Main`). The production frontend target; renders the *same* payloads as the
  reference client. Served at **`/scala/`** (the reference client stays at `/`).
  The bundle is a **build product, not tracked** — `bb web` runs `sbt fullLinkJS`
  and copies `main.js` into the git-ignored `src/web/static/scala/` (only the page
  shell `index.html` is committed). `bb web` needs `sbt`; without it `/scala/`
  shows a "not built" banner pointing back to the toolchain-free client at `/`, so
  a fresh clone without the Scala toolchain still has the full demo. See
  `web/README.md`.

Both clients expose the **demo picker** (a dropdown filled from `GET /api/demos`;
selecting one drops its `setup` into the command textarea and rebinds the stage
buttons to that demo's `stages` map) and **keyboard navigation**: with focus off
the textarea/inputs, `n` / `ArrowRight` step forward and `p` / `ArrowLeft` step
back — in the **generation** view they cycle the shown bundle's candidate
topologies (same as the `◀ ▶` cand-bar), and in the **NUTS / detailed** views
they cycle a **bundle-focus ring** `[all, id₀, id₁, …]`. Isolating a bundle dims
every other bundle's tracks/bit-wires/vias to α = 0.1 (overlap markers stay full
opacity); a `◀ ▶` focus bar shows `all N bundles` or `bundle X · k/N`. The focus
resets to "all" on any stage run or view switch. Both clients are byte-for-byte
equivalent here — the Scala client's dimming lives in `Renderer.bundleAlpha`, the
reference client's in `bundleAlpha`.

Both clients are served by a `_NoCacheStatic` mount that stamps
`Cache-Control: no-cache`, so a `bb web` rebuild of `main.js` is picked up on a
normal reload (no hard refresh) while ETags still short-circuit unchanged files
to `304`. Trade-offs in [web_static_caching.md](web_static_caching.md).

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
the no-matplotlib import (in a subprocess), the `/api/command` → `/api/state`
flow progression via FastAPI `TestClient`, and the demo catalog (`/api/demos`
lists flat + hier with the right per-stage command maps; the hier demo's
setup + stages drive the whole hierarchy flow to ≥2 distinct NUTS `bundle_id`s —
the ids the bundle-focus isolation needs). `test/tests/test_web_serialize.py` —
the struct→JSON serializers + the frozen b44 generation golden. `test/tests/
test_web_hier.py` — per-bundle floorplans: the flat b44 bundle floorplan equals
the top-level one; the hier `hier_mixed` fixture yields ≥2 bundles in distinct
frames. `test/tests/test_web_ws.py` — the WS endpoint + `POST /api/stage/{stage}`:
a connected WS receives `started` then `done` (with a `state`) frames, the stage
mutates state, a dropped WS breaks neither the stage nor a later request, an
unknown stage is contained, and the synchronous `/api/command` path still works.
