# BUDA Web Frontend (Scala.js)

The browser client for the BUDA web demo. It talks to the FastAPI backend
(`src/web/server.py`) over `/api/*` and renders the floorplan + candidate
topologies (and, in later phases, NUTS / detailed-NUTS) as SVG.

> **Status:** Phase 1 scaffold. The backend + a working **vanilla reference
> client** (`src/web/static/index.html`, served at `/`) are complete and
> browser-verified. This Scala.js project is the production frontend; it needs
> the Scala toolchain (sbt) to build and has **not** been compiled in the
> environment where it was scaffolded. Treat it as the idiomatic starting point.

## Build

```bash
cd web
sbt fastLinkJS      # dev  -> target/scala-3.3.4/buda-web-fastopt/main.js
sbt fullLinkJS      # prod -> target/scala-3.3.4/buda-web-opt/main.js
cp target/scala-3.3.4/buda-web-fastopt/main.js .   # next to index.html
```

## Run

```bash
# 1. backend
pip install -r ../src/web/requirements.txt
PYTHONPATH=../build:../src BUDA_WEB_DEV=1 uvicorn web.server:app --port 8000
# 2. open web/index.html (or serve it via Vite for HMR). Backend CORS is enabled
#    by BUDA_WEB_DEV=1; the shipped demo copies main.js into src/web/static/ and
#    is served same-origin (no CORS).
```

## Layout

```
build.sbt · project/                 # sbt + sbt-scalajs
src/main/scala/buda/web/
  Main.scala                         # entry: wires console/buttons, drives render
  net/ApiClient.scala                # fetch wrappers over /api/* (js.Dynamic payloads)
  render/Renderer.scala              # SVG scene: floorplan + hanan + candidate segments
index.html                           # dev entry (loads main.js as an ES module)
```

Planned modules as the frontend grows (per the plan): `render/DisplayGeom.scala`
(the ~200 lines of perp-centering / endpoint-snapping / band-extent / via-dedup
math ported from `src/viz_*/draw*.py`, validated against the golden-JSON
snapshot), `stepper/`, `panels/` (NUTS/DNUTS), and `edit/` (the `edit_*`
round-trip).

## Contract

The JSON shapes come from `src/web/serialize.py` (`serialize_generation`,
`serialize_nuts`, …). The vanilla reference client renders the *same* payloads,
so it doubles as a live spec and the porting reference for `Renderer` /
`DisplayGeom`.
