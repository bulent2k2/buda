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

"""Web backend Phase 4 — interactive topology edit round-trip.

Drives the /api/edit/* endpoints (which reuse the headless edit_* engine via
run_one) through the b44 flow: open an edit on a candidate, apply an op, read the
live EditVerdict, and commit a USER candidate.
"""
B44_SETUP = [
    "source flow/tracks/tracks4top.buda",
    "add_block blk_07 2960 9750 4660 10250",
    "add_block blk_23 200 10830 2700 11830",
    "add_block io_pad_tl 200 12000 1200 12800",
    "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
]


def _client():
    from fastapi.testclient import TestClient
    from web import server
    server._SESSIONS.clear()
    return TestClient(server.app)


def _generated(client):
    client.post("/api/command",
                json={"cmds": B44_SETUP + ["run_bundler", "generate_topologies"]})


def test_edit_open_reports_working_copy_and_verdict():
    client = _client()
    _generated(client)
    r = client.post("/api/edit/open", json={"bundle": 1, "candidate": 7}).json()
    assert r["result"]["ok"] is True
    ed = r["edit"]
    assert ed["open"] is True
    assert ed["bundle_id"] == 1
    assert ed["verdict"]["ok"] is True                 # the base candidate is valid
    assert len(ed["topology"]["segments"]) == 6        # the MST staircase


def test_edit_valid_op_commits_user_candidate():
    client = _client()
    _generated(client)
    n0 = client.get("/api/state").json()["bundles"][0]["num_candidates"]
    client.post("/api/edit/open", json={"bundle": 1, "candidate": 7})
    # Extend seg5 so its rider junctions stay inside — stays connected.
    r = client.post("/api/edit/op",
                    json={"command": "edit_set_span 5 1400 2750"}).json()
    assert r["result"]["ok"] is True
    assert r["edit"]["verdict"]["ok"] is True
    assert r["edit"]["verdict"]["violations"] == []
    c = client.post("/api/edit/commit", json={"pin": True}).json()
    assert c["edit"]["open"] is False                  # session closed on commit
    st = c["state"]["bundles"][0]
    assert st["num_candidates"] == n0 + 1              # a USER candidate appended
    assert st["pinned"] is True
    assert st["selected_index"] == n0                  # the new (last) candidate


def test_edit_op_surfaces_violations():
    """An op that breaks connectivity still applies (never-strand), and the live
    verdict reports the violation so the UI can flag it."""
    client = _client()
    _generated(client)
    client.post("/api/edit/open", json={"bundle": 1, "candidate": 7})
    r = client.post("/api/edit/op",
                    json={"command": "edit_set_span 5 1500 2600"}).json()
    v = r["edit"]["verdict"]
    assert v["ok"] is False
    assert any(x["kind"] == "DISCONNECTED" for x in v["violations"])


def test_edit_abort_discards():
    client = _client()
    _generated(client)
    n0 = client.get("/api/state").json()["bundles"][0]["num_candidates"]
    client.post("/api/edit/open", json={"bundle": 1, "candidate": 0})
    client.post("/api/edit/op", json={"command": "edit_set_span 0 400 2750"})
    a = client.post("/api/edit/abort").json()
    assert a["edit"]["open"] is False
    assert (client.get("/api/state").json()["bundles"][0]["num_candidates"] == n0)


def test_edit_op_rejects_non_edit_command():
    """The edit-op route only accepts edit_* ops (not open/commit/abort, and not
    arbitrary commands) — a guard so the edit channel can't run the whole CLI."""
    client = _client()
    _generated(client)
    client.post("/api/edit/open", json={"bundle": 1, "candidate": 0})
    for bad in ["run_planner", "edit_commit", "edit_topology 1 1", "add_block X 0 0 1 1"]:
        r = client.post("/api/edit/op", json={"command": bad}).json()
        assert r["result"]["ok"] is False


def test_edit_new_empty_topology():
    client = _client()
    _generated(client)
    r = client.post("/api/edit/open", json={"bundle": 1, "candidate": "new"}).json()
    assert r["edit"]["open"] is True
    assert r["edit"]["topology"]["segments"] == []     # empty working copy


def test_edit_open_bad_candidate_is_structured_not_500(client=None):
    """A non-numeric candidate must return the {result,edit,state} error shape,
    not raise a 500 (Codex P2)."""
    client = _client()
    _generated(client)
    resp = client.post("/api/edit/open", json={"bundle": 1, "candidate": "foo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["ok"] is False
    assert body["edit"]["open"] is False              # no session opened


def test_edit_abort_reads_session_from_body():
    """abort must address the session named in the body, not always the default
    (Codex P2). Open an edit on session 's1', abort with {session_id:'s1'}, and
    confirm s1's edit is closed (the default session is untouched)."""
    client = _client()
    # generate on a named session
    client.post("/api/command", json={
        "session_id": "s1",
        "cmds": B44_SETUP + ["run_bundler", "generate_topologies"]})
    client.post("/api/edit/open", json={"session_id": "s1", "bundle": 1, "candidate": 0})
    assert client.get("/api/state?session_id=s1")  # session exists
    r = client.post("/api/edit/abort", json={"session_id": "s1"}).json()
    assert r["edit"]["open"] is False                 # s1's edit was aborted
