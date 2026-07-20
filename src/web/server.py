# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""BUDA web backend — FastAPI transport over a single headless BudaSession.

Demo model: ONE server process hosting ONE `BudaSession`, guarded by a single
lock so requests serialize (they must anyway — every engine call holds the GIL
and the session state is not reentrant).  The API carries a `session_id`
(default "default") so a later worker-per-session manager is an additive change.

Every route builds a `.buda` command string and drives it through
`web.runner.run_one`, then reads routed state via `web.serialize`.  No route
contains routing logic.

Run (dev):
    PYTHONPATH=build:src uvicorn web.server:app --reload --port 8000
"""
import asyncio
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import buda_cli
from web import runner, serialize

app = FastAPI(title="BUDA Web Backend", version="0.1.0")

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Dev-only CORS: a Scala.js Vite dev server on another origin needs it.  The
# shipped demo serves the built client from this same origin (StaticFiles, added
# in a later phase), so production stays CORS-free.
if os.environ.get("BUDA_WEB_DEV") == "1":
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
        allow_headers=["*"],
    )


class _Session:
    """The one demo session + its serialization lock."""
    def __init__(self):
        self.session = buda_cli.BudaSession()
        self.session.no_viz = True
        self.lock = asyncio.Lock()


# Keyed by session_id for forward-compatibility; only "default" is created now.
_SESSIONS = {}


def _get(session_id):
    st = _SESSIONS.get(session_id)
    if st is None:
        st = _SESSIONS[session_id] = _Session()
    return st


# ── request models ──────────────────────────────────────────────────────────
class CommandRequest(BaseModel):
    cmds: list[str]
    session_id: str = "default"


class EditOpenRequest(BaseModel):
    bundle: int
    candidate: int | str = "new"     # int index (0-based) or "new"
    session_id: str = "default"


class EditOpRequest(BaseModel):
    command: str                     # a full edit_* command string (CLI syntax)
    session_id: str = "default"


class EditCommitRequest(BaseModel):
    pin: bool = False
    session_id: str = "default"


class SessionRequest(BaseModel):
    """Bare session-id body for POSTs that take no other argument (abort/reset),
    so all POST routes address the session via the JSON body consistently."""
    session_id: str = "default"


# ── routes ──────────────────────────────────────────────────────────────────
@app.post("/api/command")
async def post_command(req: CommandRequest):
    """Run one or more `.buda` commands verbatim; return per-command results
    plus the resulting StateSummary.  A failing command does not abort the
    batch (each result carries its own `ok`)."""
    st = _get(req.session_id)
    async with st.lock:
        results = runner.run_many(st.session, req.cmds)
        state = serialize.serialize_state(st.session)
    return {"results": results, "state": state}


@app.get("/api/state")
async def get_state(session_id: str = "default"):
    st = _get(session_id)
    async with st.lock:
        return serialize.serialize_state(st.session)


@app.get("/api/render/{stage}")
async def get_render(stage: str, session_id: str = "default",
                     bundle: int | None = None,
                     candidate: int | None = None):
    """Render payload for a stage:
      generation — floorplan + Hanan + per-bundle candidate topologies
                   (+ ConnTopology analysis); `bundle`/`candidate` narrow it.
      nuts       — floorplan + placed bus segments + overlap sites.
      detailed   — floorplan + per-bit wires + per-bit vias.
    """
    st = _get(session_id)
    async with st.lock:
        if stage == "generation":
            return serialize.serialize_generation(st.session, bundle, candidate)
        if stage == "nuts":
            return serialize.serialize_render_nuts(st.session)
        if stage == "detailed":
            return serialize.serialize_render_detailed(st.session)
        return {"error": f"unknown render stage '{stage}'"}


def _edit_payload(session, result):
    return {"result": result,
            "edit": serialize.serialize_edit(session),
            "state": serialize.serialize_state(session)}


def _edit_error(session, summary):
    """Structured `{result, edit, state}` for bad edit input — the web command
    layer never 500s on client input, it returns ok=False (mirrors run_one)."""
    return _edit_payload(session, {
        "ok": False, "error": ["error", summary], "log_lines": [],
        "num_warnings": 0, "num_errors": 1, "summary": summary})


@app.post("/api/edit/open")
async def edit_open(req: EditOpenRequest):
    """Open an interactive edit session on a candidate (or `new` = empty). Mirrors
    the CLI `edit_topology <bundle> [<cand#>|new]`; the working copy + live
    verdict come back in `edit`."""
    st = _get(req.session_id)
    cand = req.candidate
    if cand == "new":
        which = "new"
    else:
        try:                                    # int or numeric string → 1-based
            which = str(int(cand) + 1)
        except (TypeError, ValueError):
            # A non-numeric candidate is bad input, not a server error — return
            # the structured shape (Codex P2) instead of raising a 500.
            return _edit_error(
                st.session, f"invalid candidate {cand!r} — use an int index or 'new'")
    async with st.lock:
        res = runner.run_one(st.session, f"edit_topology {req.bundle} {which}")
        return _edit_payload(st.session, res)


@app.post("/api/edit/op")
async def edit_op(req: EditOpRequest):
    """Apply one `edit_*` operation (full CLI-syntax command string, e.g.
    `edit_add_trunk H 700`). Returns the refreshed working copy + verdict."""
    st = _get(req.session_id)
    cmd = req.command.strip()
    if not cmd.startswith("edit_") or cmd.split()[0] in (
            "edit_topology", "edit_commit", "edit_abort"):
        return _edit_error(st.session, "use edit_add_trunk/edit_set_span/… here")
    async with st.lock:
        res = runner.run_one(st.session, cmd)
        return _edit_payload(st.session, res)


@app.post("/api/edit/commit")
async def edit_commit(req: EditCommitRequest):
    """Commit the edit as a USER candidate (`pin` also selects it)."""
    st = _get(req.session_id)
    async with st.lock:
        res = runner.run_one(st.session, "edit_commit pin" if req.pin else "edit_commit")
        return _edit_payload(st.session, res)


@app.post("/api/edit/abort")
async def edit_abort(req: SessionRequest = SessionRequest()):
    """Discard the open edit session. Takes the session id from the JSON body,
    like the other edit routes (Codex P2 — a query-only param silently aborted
    the default session for a non-default client)."""
    st = _get(req.session_id)
    async with st.lock:
        res = runner.run_one(st.session, "edit_abort")
        return _edit_payload(st.session, res)


@app.post("/api/reset")
async def post_reset(req: SessionRequest = SessionRequest()):
    """Discard the session and start fresh (demo convenience)."""
    st = _get(req.session_id)
    async with st.lock:
        st.session = buda_cli.BudaSession()
        st.session.no_viz = True
        return serialize.serialize_state(st.session)


@app.get("/api/health")
async def health():
    return {"ok": True, "sessions": list(_SESSIONS)}


# Serve the reference static client at "/" (mounted last so it never shadows the
# /api/* routes).  This is a small vanilla-SVG demo client — the immediate,
# toolchain-free way to drive the demo in a browser and the porting reference for
# the Scala.js DisplayGeom.  The production Scala.js bundle can be built into this
# same dir (or its own) and served identically.
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
