# BUDA Web Frontend (Scala.js)

The browser client for the BUDA web demo. It talks to the FastAPI backend
(`src/web/server.py`) over `/api` and renders the floorplan + candidate
topologies, the abstract-NUTS and detailed-NUTS placements, and the interactive
topology-edit round-trip as SVG.

> **Status:** Compiled + linked with Scala 3.3.4 / Scala.js 1.17 and
> browser-verified against the backend. It reaches feature parity with the
> vanilla reference client (`src/web/static/index.html`): the generation / NUTS /
> detailed views, the view switch, the candidate stepper, select/pin, the
> interactive edit panel (open / op / commit / abort with a live verdict), and
> the BDB Open / Save / Load row.

## Build

The Scala.js bundle is a **build product, not committed** — only the page shell
`src/web/static/scala/index.html` is tracked. Build the bundle with the repo's
build wrapper:

```bash
bb web        # runs `sbt fullLinkJS`, copies the bundle to src/web/static/scala/main.js
```

`bb web` needs `sbt` on your PATH. The repo does not ship a Scala toolchain; any
recent `sbt` works. The quickest one-time install is [Coursier](https://get-coursier.io):

```bash
# JVM launcher honours a corporate proxy/truststore:
curl -fsSL https://raw.githubusercontent.com/coursier/launchers/master/coursier -o cs
chmod +x cs
./cs install sbt      # or use any locally installed sbt; then re-run: bb web
```

**You do not need this to use the web demo** — the toolchain-free reference client
at `/` covers the whole flow. Only the Scala.js client at `/scala/` needs the build;
until you run `bb web` it shows a "not built" banner pointing back to `/`.

Under the hood `bb web` runs the sbt targets directly:

```bash
cd web
sbt fastLinkJS      # dev  -> target/scala-3.3.4/buda-web-fastopt/main.js (unoptimized)
sbt fullLinkJS      # prod -> target/scala-3.3.4/buda-web-opt/main.js     (what bb web ships)
```

## Run (served same-origin by the backend)

The backend serves the built client at **`/scala/`** (the vanilla reference client
stays at `/`). After a source change, re-run `bb web` to refresh the git-ignored
bundle next to the served page. Boot the backend and open the client:

```bash
PYTHONPATH=build:src uvicorn web.server:app --port 8000
# then open http://127.0.0.1:8000/scala/
```

`web/index.html` is the standalone dev entry (loads `./main.js` as an ES module);
copy `main.js` next to it, or serve `web/` via Vite for HMR. Cross-origin dev
against the backend needs `BUDA_WEB_DEV=1` (enables CORS).

## Layout

```
build.sbt · project/                 # sbt + sbt-scalajs
src/main/scala/buda/web/
  Main.scala                         # controller: wires the console, stage/view
                                     #   buttons, stepper, select/pin, edit panel,
                                     #   BDB row; drives Renderer per view
  net/ApiClient.scala                # fetch wrappers over /api (js.Dynamic payloads)
  render/Renderer.scala              # SVG scene for all three views
  render/DisplayGeom.scala           # perp-centering / endpoint-snapping math
index.html                           # dev entry (loads main.js as an ES module)
```

## Contract

The JSON shapes come from `src/web/serialize.py` (`serialize_generation`,
`serialize_nuts`, `serialize_detailed`, `serialize_edit`, …). The vanilla
reference client renders the *same* payloads, so it doubles as a live spec and
the porting reference for `Renderer` / `DisplayGeom`; the latter is validated
against the golden-JSON snapshot (`test/tests/data/web_golden/b44_generation.json`).
