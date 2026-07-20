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

"""Web backend Phases 3 + 5 — candidate select/pin and BDB checkpoint resume."""
B44_SETUP = [
    "source flow/tracks/tracks4top.buda",
    "add_block blk_07 2960 9750 4660 10250",
    "add_block blk_23 200 10830 2700 11830",
    "add_block io_pad_tl 200 12000 1200 12800",
    "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
]


def _client():
    import pytest
    pytest.importorskip("fastapi")   # skip (don't fail) where fastapi is absent
    from fastapi.testclient import TestClient
    from web import server
    server._SESSIONS.clear()
    return TestClient(server.app)


# ── Phase 3: select / pin ────────────────────────────────────────────────────
def test_select_pins_candidate():
    client = _client()
    client.post("/api/command",
                json={"cmds": B44_SETUP + ["run_bundler", "generate_topologies"]})
    r = client.post("/api/select", json={"bundle": 1, "candidate": 5}).json()
    assert r["result"]["ok"] is True
    b = r["state"]["bundles"][0]
    assert b["selected_index"] == 5
    assert b["pinned"] is True
    # A subsequent planner run keeps the pinned selection.
    client.post("/api/command", json={"cmds": ["run_planner"]})
    assert client.get("/api/state").json()["bundles"][0]["selected_index"] == 5


# ── Phase 5: BDB checkpoint save/resume ──────────────────────────────────────
def test_checkpoint_resume_round_trip(tmp_path):
    bdb = str(tmp_path / "ckpt.bdb")

    # Session A: open the BDB first, then route — the stages persist into it.
    a = _client()
    a.post("/api/bdb/open", json={"path": bdb})
    a.post("/api/command", json={"cmds": B44_SETUP + [
        "run_bundler", "generate_topologies", "run_planner", "run_nuts"]})
    sa = a.get("/api/state").json()
    assert sa["stages_run"]["nuts"] is True
    n_cand = sa["bundles"][0]["num_candidates"]

    # Session B (fresh): re-declare the floorplan/layers, open the SAME BDB,
    # then load_pipeline rehydrates bundles + candidates + plan + NUTS.
    b = _client()
    from web import server
    server._SESSIONS.clear()          # ensure B is a brand-new session
    b.post("/api/command", json={"cmds": B44_SETUP})
    b.post("/api/bdb/open", json={"path": bdb})
    r = b.post("/api/bdb/load_pipeline", json={}).json()
    assert r["result"]["ok"] is True
    sb = r["state"]
    assert sb["stages_run"]["nuts"] is True          # NUTS restored
    assert sb["bundles"][0]["num_candidates"] == n_cand
    assert sb["has_bdb"] is True


def test_bdb_path_with_whitespace_is_rejected(tmp_path):
    """A path with whitespace can't round-trip through the whitespace-tokenized
    .buda command layer, so it's rejected with a structured error rather than
    silently opening a truncated path (Codex P2)."""
    c = _client()
    bad = str(tmp_path / "My Project" / "ckpt.bdb")   # has a space
    r = c.post("/api/bdb/open", json={"path": bad}).json()
    assert r["result"]["ok"] is False
    assert "whitespace" in r["result"]["summary"]
    assert r["state"]["has_bdb"] is False              # nothing opened
    # save-as with whitespace is rejected too
    rs = c.post("/api/bdb/save", json={"path": bad}).json()
    assert rs["result"]["ok"] is False


def test_bdb_save_snapshot(tmp_path):
    """save_bdb <path> snapshots the current routed BDB to a new file."""
    src = str(tmp_path / "work.bdb")
    snap = str(tmp_path / "snap.bdb")
    c = _client()
    c.post("/api/bdb/open", json={"path": src})
    c.post("/api/command", json={"cmds": B44_SETUP + [
        "run_bundler", "generate_topologies", "run_planner", "run_nuts"]})
    r = c.post("/api/bdb/save", json={"path": snap}).json()
    assert r["result"]["ok"] is True
    import os
    assert os.path.exists(snap)
