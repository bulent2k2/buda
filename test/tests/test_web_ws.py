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

"""WebSocket progress streaming — the WS endpoint + POST /api/stage/{stage}.

Verifies: (1) a connected WS receives a `started` then a `done` frame carrying a
`state` when a stage runs; (2) the stage actually mutates session state;
(3) a dropped WS neither breaks the running stage nor a later request;
(4) the synchronous /api/command path still works (backward compat).
"""
import pytest

pytest.importorskip("fastapi")   # skip (don't fail) where fastapi is absent
from fastapi.testclient import TestClient  # noqa: E402

from web import server  # noqa: E402


# Minimal single-bundle b44 flow (mirrors test_web_server.py) — enough to reach
# planner / NUTS / detailed-NUTS.
B44_SETUP = [
    "source flow/tracks/tracks4top.buda",
    "add_block blk_07 2960 9750 4660 10250",
    "add_block blk_23 200 10830 2700 11830",
    "add_block io_pad_tl 200 12000 1200 12800",
    "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
]


def _client():
    server._SESSIONS.clear()
    return TestClient(server.app)


def _drain_until(ws, status, name, limit=200):
    """Read frames until a stage frame with the given status arrives (or fail).
    Returns (the matching frame, saw_started_first-is-not-checked-here)."""
    for _ in range(limit):
        f = ws.receive_json()
        if f.get("kind") == "stage" and f.get("status") == status \
                and f.get("name") == name:
            return f
    raise AssertionError(f"never saw stage {name}/{status}")


def _prep_through_topologies(client):
    client.post("/api/command", json={"cmds": B44_SETUP + [
        "run_bundler", "generate_topologies"]})


def test_ws_hello_on_connect():
    client = _client()
    with client.websocket_connect("/api/ws") as ws:
        hello = ws.receive_json()
        assert hello["kind"] == "hello"
        assert hello["session_id"] == "default"
        assert "state" in hello


def test_stage_streams_started_then_done_with_state():
    """Connect the WS, run the planner stage, assert a started then a done frame
    with a `state`; the done frame's state reports the planner stage ran."""
    client = _client()
    _prep_through_topologies(client)
    with client.websocket_connect("/api/ws") as ws:
        assert ws.receive_json()["kind"] == "hello"
        resp = client.post("/api/stage/planner").json()
        assert resp["result"]["ok"] is True

        started = _drain_until(ws, "started", "planner")
        assert started["command"] == "run_planner"
        done = _drain_until(ws, "done", "planner")
        assert done["state"]["stages_run"]["planner"] is True
        assert "summary" in done and "notable" in done

    # The synchronous return mutated real session state too.
    assert client.get("/api/state").json()["stages_run"]["planner"] is True


def test_stage_runs_full_long_pipeline_over_ws():
    """planner -> nuts -> detailed_nuts each stream a done frame and advance
    state; a stage with an arg (ripup 2) also completes."""
    client = _client()
    _prep_through_topologies(client)
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()                              # hello
        for stage, key in [("planner", "planner"), ("nuts", "nuts"),
                           ("detailed_nuts", "dnuts")]:
            r = client.post(f"/api/stage/{stage}").json()
            assert r["result"]["ok"] is True, (stage, r)
            done = _drain_until(ws, "done", stage)
            assert done["state"]["stages_run"][key] is True

        # An arg is appended verbatim (ripup_reroute 2); no-op clean flow is fine.
        r = client.post("/api/stage/ripup", json={"args": "2"}).json()
        assert r["result"]["ok"] is True
        assert r["result"]["ok"] is True


def test_stage_completes_without_any_ws_client():
    """No WS connected: the stage still runs and mutates state (broadcast to an
    empty client set is a no-op)."""
    client = _client()
    _prep_through_topologies(client)
    r = client.post("/api/stage/planner").json()
    assert r["result"]["ok"] is True
    assert r["state"]["stages_run"]["planner"] is True


def test_dropped_ws_does_not_break_stage_or_later_request():
    """A WS that connects then drops must leave the server able to run a stage
    and serve a later request."""
    client = _client()
    _prep_through_topologies(client)
    # Open and immediately close a WS.
    with client.websocket_connect("/api/ws") as ws:
        assert ws.receive_json()["kind"] == "hello"
    # Now run a stage with no live client — must still succeed.
    r = client.post("/api/stage/planner").json()
    assert r["result"]["ok"] is True
    # And a later request is served fine.
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/state").json()["stages_run"]["planner"] is True


def test_unknown_stage_is_contained():
    client = _client()
    r = client.post("/api/stage/bogus").json()
    assert "error" in r
    assert "planner" in r["stages"]
    # Server still alive.
    assert client.get("/api/health").json()["ok"] is True


def test_command_path_still_works_backward_compat():
    """The synchronous /api/command path is unchanged by the WS additions."""
    client = _client()
    r = client.post("/api/command", json={"cmds": B44_SETUP + ["run_bundler"]})
    assert r.status_code == 200
    body = r.json()
    assert all(res["ok"] for res in body["results"]), body["results"]
    assert body["state"]["stages_run"]["bundler"] is True
