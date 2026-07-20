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
from pydantic import BaseModel

import buda_cli
from web import runner, serialize

app = FastAPI(title="BUDA Web Backend", version="0.1.0")

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


@app.post("/api/reset")
async def post_reset(session_id: str = "default"):
    """Discard the session and start fresh (demo convenience)."""
    st = _get(session_id)
    async with st.lock:
        st.session = buda_cli.BudaSession()
        st.session.no_viz = True
        return serialize.serialize_state(st.session)


@app.get("/api/health")
async def health():
    return {"ok": True, "sessions": list(_SESSIONS)}
