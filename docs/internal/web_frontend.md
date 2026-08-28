# BUDA Web Frontend

A browser front end (Scala.js) for demos: type BUDA commands, run the flat flow,
visualize candidate topologies, and see NUTS / detailed-NUTS results — with a
local Python server running the *existing* engines behind the browser. The web
layer adds only transport + serialization; it contributes **no routing logic**.

Plan of record: the phased implementation plan (Phases 0–5). This doc tracks the
as-built backend.

## Quick reference: stop → build → restart

The "my server looks stale" recipe (details in
[Restart vs. refresh](#restart-vs-refresh--picking-up-a-change)):

```bash
pkill -f "uvicorn web.server"    # 1. stop (or Ctrl-C in its terminal)
bin/bb                           # 2. rebuild C++ (the layer --reload can NOT hot-swap)
bin/bb web                       # 2b. only if Scala client sources changed (needs sbt)
PYTHONPATH=build:src uvicorn web.server:app --port 8000 --reload   # 3. restart
```

`--reload` covers Python-source edits only. A rebuilt `build/*.so` needs the
stop/restart above; static HTML and a rebuilt `main.js` need only a browser
reload (the mounts are `no-cache`).

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

`GET /api/demos` returns `{demos:[{key, label, note, flow_path, setup, stages,
flow, unavailable?}]}` — the demos the client's picker offers, so one "load
setup + click the stage buttons" UX drives **both** the flat and the
hierarchy-aware flow without the client knowing either command sequence.

**The catalog is DATA, derived from real flows.** `demo/web/demos.json` lists
`{key, label, flow, note}` per demo — nothing but the label is written twice —
and `src/web/demos.py` reads the named `.buda` file and splits it at the first
pipeline command:

| field | derived as |
|---|---|
| `setup` | the commands ABOVE the split (technology, floorplan, netlist, BDB hierarchy) — what the command box shows and "Run commands" runs |
| `stages` | the flow's OWN spelling of each stage, so a hier flow drives `run_hier_bundler` / `generate_hier_topologies` / `run_planner hier …` **without the manifest saying which kind it is** |
| `flow` | the whole pipeline tail in order — what the **Run flow** button replays |

So a demo is added by adding a JSON entry, and a flow that changes takes its
demo with it (the catalog is rebuilt per request, so no restart either). The
derivation paid for itself immediately: the hier demo's planner stage was
hardcoded `run_planner hier 5` while its flow says `run_planner hier
signal_tracks` — the hand-written catalog had already drifted from the flow it
claimed to extract from.

Two rules the parse enforces, both because a browser is not a terminal:

- **Paths.** A flow resolves a relative path against its OWN directory, but
  these lines are replayed one at a time through `/api/command` with no
  enclosing script, where the engine falls back to the CWD. So every argument
  naming an existing FILE relative to the flow's directory is rewritten
  repo-root-relative (the server's documented CWD). Keyed on "is a file", not
  on a list of commands, because a list would silently miss the next
  path-taking command; `:memory:`, a block name and a number are left as
  written. The line is read (and reassembled) through **`buda_script`** — this
  module is the sixth `.buda` reader and is listed in that module's docstring,
  which is where the rule to check lives. Reparsing the syntax here got all
  three of its rules wrong at once on a path the engine handles fine (Codex
  #863 P2): `require_file "rev #2/top.v"` cut at the `#`, so the setup replayed
  `require_file "rev`, the availability check reported `rev` as a missing input
  and marked the demo unavailable, and a `source` beside it went un-rerooted.
  Reassembly re-quotes through `quote_arg`, the tokenizer's own inverse, so a
  rerooted spaced path survives the round trip. Identity on every checked-in
  flow (none quotes anything) — the catalog is byte-identical, test-pinned.
- **Skips.** The `flow` tail drops viewers (`visualize*` — a window nobody can
  see, blocking the server), `exit`, and the artifact writers (`emit_guides`,
  `export_*`, `save_bdb`): clicking a demo button must not scribble output into
  the user's checkout.

A flow whose `require_file` inputs are missing is listed with `unavailable` and
its own hint — the repo's existing precondition mechanism — instead of failing
when clicked.

The eight shipped demos (all with checked-in inputs) are the two originals — the
b44 flat design, saved as `demo/web/flat_b44.buda` since it had no flow file of
its own, and the depth-2 hier `flow/hbundles/06_multipin_stress.buda` — plus
`user_guide`, `comprehensive_demo`, `congestion`, `keepout`, `channel_stress`
(a healer flow) and the RV SoC (`flow/rv/soc.buda`: imported DEF/LEF/Verilog,
1230 nets, ~10s through **Run flow**).

**Picking a different demo resets the session.** A demo is a whole DESIGN and its
setup only ever ADDS, so running a second demo's setup over the first leaves both
designs' blocks live at once — and the two sit in far-apart coordinate ranges
(b44 around y 10000, the hier demo around y 300) while every view frames the
union of ALL blocks, so the frame blows up ~17× (`1220×730` → `6240×14380`) and
the design just run renders as a speck beside the previous demo's three
rectangles. The **topo view hides it** — `activeFrame()` prefers the shown
bundle's own floorplan — so it surfaces only in NUTS and detailed, which have no
per-bundle frame and fall back to the contaminated session floorplan.

Two guards, because the picker is only one route to the hazard. Picking a
different demo resets — immediate feedback that you are starting over. But the
**server session outlives the page**, so a fresh tab, a reopened tab or private
mode arrives with no remembered demo, the picker shows the FIRST catalog entry
against whatever the server still holds, and Run merges the two with no switch
ever happening (measured: 78 hier blocks + the flat setup = 81 and a `blk_07` in
the frame). So the load-bearing guard sits at the hazard itself: **running a
demo's setup VERBATIM starts that demo from a clean session.** An edited or
ad-hoc command list runs as-is — that is the console's job — and a session with
no blocks is left alone so a BDB opened before setup survives. That last test is
what `n_blocks` is for in `serialize_state`: `stages_run` cannot answer "is a
design already loaded", since a declared-but-unbundled floorplan reads as
all-false, which is exactly the state a freshly-served page finds.

A page RELOAD does not reset (reloading should keep the session you were working
in); the picker's selection is remembered in `sessionStorage` so a reloaded page
shows the demo the session is actually in, rather than snapping back to the first
one and turning a re-pick of your own demo into a wipe. That is a convenience —
correctness rests on the setup-run guard above, which holds whether or not the
storage is available. Both halves are pinned by
`test_a_demo_setup_accumulates_onto_the_session_unless_reset` — the fix lives in
the clients but rests on two server properties (setup accumulates; `POST
/api/reset` clears the floorplan), and a silent change to either would strand it.

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
tab's stage progress live. **Both clients** open the WS on load (the reference
client's `connectWS`; the Scala client's `WsClient.connect`, both auto-reconnect
on close), show a pulsing `running <stage>… <s>s` indicator on
`started`/`heartbeat`, clear it on `done`, forward `notable` to the log, and
refresh state — the planner/nuts/dnuts buttons go through `/api/stage/*` (the
instant bundler/topology buttons stay on `/api/command`; the ripup and negotiate
healer buttons also go through the WS path in **both** clients). The Scala client peels the WS
args from the demo command the same way (`run_planner hier 5` → stage `planner`,
args `hier 5`). Because the socket only *streams* progress, a server built
without the uvicorn websocket extra (the `/api/ws` 404s) degrades gracefully in
both clients: the `POST /api/stage/{stage}` still returns the final result and
the caller toggles the indicator itself; the client just retries the socket.

## Frontend

- **`src/web/static/index.html`** — a vanilla-SVG **reference client**, served at
  `/` (StaticFiles). The immediate, toolchain-free way to drive the demo in a
  browser, and the porting reference for the Scala.js renderer. Command console +
  stage buttons (bundler→dnuts, plus the ripup/negotiate healers and a `check`
  button that runs `check_design` — which auto-selects its audit stage
  topo/nuts/dnuts server-side from how far the pipeline has run), a **Run flow**
  button (the demo's whole flow: its setup then the catalog's `flow` tail, from
  a clean session — a setup only ADDS, so replaying it over a loaded design is a
  duplicate-block error) + a `topo/nuts/dnuts` view switch. Browser-
  verified (Playwright/Chromium): the generation view draws the floorplan +
  candidate segments (stepping the 35 b44 candidates); the NUTS view draws placed
  track footprints + centerlines; the detailed view draws all 104 bit-wires + 52
  vias of the routed bus.
- **`web/`** — the Scala.js project (sbt + sbt-scalajs; `ApiClient`, `WsClient`,
  `Renderer`, `Main`). The production frontend target; renders the *same*
  payloads as the reference client, and drives the long stages through the same
  WS progress path (`WsClient` + `POST /api/stage/*`). Served at **`/scala/`**
  (the reference client stays at `/`).
  The bundle is a **build product, not tracked** — `bb web` runs `sbt fullLinkJS`
  and copies `main.js` into the git-ignored `src/web/static/scala/` (only the page
  shell `index.html` is committed). `bb web` needs `sbt`; without it `/scala/`
  shows a "not built" banner pointing back to the toolchain-free client at `/`, so
  a fresh clone without the Scala toolchain still has the full demo. See
  `web/README.md`.

Both clients expose a **Zoom** toggle (button, or `z`) that frames the ONE
bundle on screen — the shown candidate in the topo view, the focused bundle's
placed wires in nuts/dnuts — instead of the whole floorplan. It yields no box
when nothing is isolated (nuts/dnuts showing ALL bundles), so the toggle then
keeps the full view rather than pretending to zoom; a single bundle spanning its
whole design (the `flat` demo) correctly does not move either.

**Bundle-block markers** come in two kinds in the topo view: a block the
candidate TAPS gets a solid magenta ring, one it passes THROUGH — crossed
geometry with no tap — gets a dashed teal ring, slightly larger so a block both
tapped and crossed shows both. Both carry a `<title>`, so hovering names the
kind. The pass-through set is the serializer's `passthru_blocks`, derived by the
SAME predicate `dump_topologies --conn` prints its `passthru:` line from
(`seg_crosses_rect` / `seg_spans_block` / `passthru_blocks` in
`buda_session/util.py`, which `reports.py` now delegates to) — so the marker the
client draws and the line the CLI prints cannot disagree about one candidate.

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

The generation view renders **one bundle at a time** (`?bundle=<id>`), so *which*
one is its own control: a second `◀ ▶` pair on the cand bar, and the `[` / `]`
keys — `n`/`p` and the arrows were already fully consumed by the two rings above.
The ring is the id list from `GET /api/state`'s bundle digests, so it needs
nothing server-side; the hier demo steps 35 bundles where the view used to be
hardcoded to bundle 1. Unlike the focus ring this is a **selection** and
PERSISTS: a stage run or a pin leaves the reader on the bundle they were reading,
and it is re-resolved only when re-bundling retires the id. It also governs what
`Pin`/`Unpin` and `Edit` act on — those posted a literal `bundle: 1` while the
view could not move — and stepping is refused while an edit session is open,
since the session is bound to its bundle server-side.

Two things the hardcoding had masked. `state` is the WHOLE session's digest list,
not the filtered render, so the shown bundle's pin state must be looked up **by
id** — `state.bundles[0]` is the session's first bundle whatever `?bundle=` asked
for, and it was only ever right while the view was stuck there (`shownDigest()`
in the reference client, `pinSuffix` in `Renderer`). And a degenerate placement
can leave a bundle with **no candidates at all**; the stepper can land on one, so
both clients label it rather than dereferencing `candidates[0]`. The bundle id
moved out of the candidate label into the new one, so it is not printed twice.

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

### Restart vs. refresh — picking up a change

What you do to see a change depends on *which layer* changed. The three cases:

- **Backend change** (a route, `server.py`, `demos.py`, `runner.py`,
  `serialize.py`, or anything they import) — the running process holds the **old**
  code in memory, so a browser refresh alone won't see it (e.g. a request to a
  newly-added route just 404s). You must **restart the server**. A plain
  `uvicorn …` launch binds the port, so **stop the old process first** — `Ctrl-C`
  in its terminal, or `pkill -f "uvicorn web.server"` — otherwise the new one
  dies with `[Errno 98] address already in use`. Then relaunch.
- **Reference client** (`static/index.html`) — served static with
  `Cache-Control: no-cache`, so a **normal browser reload** revalidates and picks
  up the new bytes (no hard refresh, no restart). See
  [web_static_caching.md](web_static_caching.md).
- **Scala client** (`static/scala/main.js`) — a git-ignored build product, so
  rebuild it with **`bb web`** (needs `sbt`), then reload the page. The
  `no-cache` mount means a plain reload picks up the rebuilt bundle.

**`--reload` avoids the manual backend restart** — it watches the Python source
and restarts the worker in place on any change on disk, so after a `git pull`
you don't stop/relaunch by hand:

```bash
PYTHONPATH=build:src uvicorn web.server:app --port 8000 --reload
```

Caveat: `--reload` watches Python source only, **not** the `main.js` build
product (still a `bb web` + reload) and not the static HTML (still just a
reload). It's a convenience for backend iteration, not a substitute for the two
static-asset flows above.

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
