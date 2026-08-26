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

"""Web backend Phase 0 — the command-capture shim + minimal server.

Verifies the load-bearing invariants: (1) run_one captures output and NEVER
raises (an unknown command / bad input is contained, so a bad request cannot
kill the server); (2) the command layer imports without matplotlib (the headless
requirement); (3) /api/command drives the whole flat flow and /api/state reflects
the stage progression.
"""
import subprocess
import sys

import buda_cli
from web import runner


# The canonical single-bundle b44 flow, as a command list a client would send.
B44_SETUP = [
    "source flow/tracks/tracks4top.buda",
    "add_block blk_07 2960 9750 4660 10250",
    "add_block blk_23 200 10830 2700 11830",
    "add_block io_pad_tl 200 12000 1200 12800",
    "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
]


def _fresh():
    s = buda_cli.BudaSession()
    s.no_viz = True
    return s


def test_run_one_captures_and_reports_ok():
    s = _fresh()
    r = runner.run_one(s, "add_block A 0 0 100 100")
    assert r["ok"] is True
    assert r["error"] is None
    assert isinstance(r["log_lines"], list)


def test_run_one_contains_unknown_command_systemexit():
    """do_command sys.exit(1)s on an unknown command; run_one must contain it
    (return ok=False) rather than let SystemExit escape and kill the server."""
    s = _fresh()
    r = runner.run_one(s, "this_is_not_a_command foo bar")
    assert r["ok"] is False
    assert r["error"][0] == "exit"
    # The session is still usable after a contained failure.
    assert runner.run_one(s, "add_block A 0 0 100 100")["ok"] is True


def test_run_one_contains_bad_args():
    """A handler that fails on bad input is also contained (never raises)."""
    s = _fresh()
    r = runner.run_one(s, "add_block")     # missing all coords
    assert r["ok"] in (True, False)        # whatever it does, it must not raise
    assert isinstance(r["log_lines"], list)


def test_snapshot_if_idle_yields_when_free_and_skips_when_busy():
    """snapshot_if_idle runs the read only when no engine call holds the lock
    (Codex P1/P2): a WS hello must not read a session mid-mutation.  When the
    engine lock is held (a stage running in the executor), it returns None so
    the caller serves a lock-free fallback instead of racing the mutation."""
    assert runner.snapshot_if_idle(lambda: "value") == "value"   # lock free
    with runner._ENGINE_LOCK:                                    # engine "busy"
        assert runner.snapshot_if_idle(lambda: "value") is None
    assert runner.snapshot_if_idle(lambda: "value") == "value"   # released again


def test_run_one_holds_engine_lock_during_dispatch():
    """run_one serializes on the process-wide engine lock while dispatching, so
    a concurrent snapshot_if_idle sees the engine as busy — the invariant that
    keeps the global stdout/stderr swap from cross-capturing across sessions."""
    s = _fresh()
    seen = {}

    def _probe(_session, _cmd, _line):
        # Runs INSIDE do_command, i.e. while run_one holds _ENGINE_LOCK.
        seen["busy"] = runner.snapshot_if_idle(lambda: "x") is None
        return True

    s.do_command = lambda line: _probe(s, None, line)   # type: ignore[assignment]
    runner.run_one(s, "noop")
    assert seen.get("busy") is True


def test_command_layer_imports_without_matplotlib():
    """Importing the command registry (and buda_cli) must not pull matplotlib —
    the headless-server requirement.  Checked in a FRESH subprocess so an
    unrelated test that imported matplotlib can't mask a regression."""
    code = (
        "import sys, buda_cmds, buda_cli\n"
        "assert 'matplotlib' not in sys.modules, 'matplotlib imported'\n"
        "assert 'buda_viz' not in sys.modules, 'buda_viz imported'\n"
        "print('OK')\n"
    )
    root = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
        env={**_env_with_pythonpath()},
    )
    assert root.returncode == 0, root.stderr
    assert "OK" in root.stdout


def _env_with_pythonpath():
    import os
    root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    pp = os.pathsep.join([os.path.join(root, "build"), os.path.join(root, "src")])
    env = dict(os.environ)
    env["PYTHONPATH"] = pp + os.pathsep + env.get("PYTHONPATH", "")
    env["MPLBACKEND"] = "Agg"
    return env


def _client():
    import pytest
    pytest.importorskip("fastapi")   # skip (don't fail) where fastapi is absent
    from fastapi.testclient import TestClient
    from web import server
    # Isolate each test's session.
    server._SESSIONS.clear()
    return TestClient(server.app)


def test_command_endpoint_drives_flat_flow():
    client = _client()
    r = client.post("/api/command", json={"cmds": B44_SETUP})
    assert r.status_code == 200
    body = r.json()
    assert all(res["ok"] for res in body["results"]), body["results"]
    st = body["state"]
    assert st["stages_run"] == {
        "bundler": False, "topologies": False, "planner": False,
        "nuts": False, "dnuts": False,
    }

    # Run the pipeline stages via commands; state advances at each step.
    client.post("/api/command", json={"cmds": ["run_bundler"]})
    assert client.get("/api/state").json()["stages_run"]["bundler"] is True

    client.post("/api/command", json={"cmds": ["generate_topologies"]})
    st = client.get("/api/state").json()
    assert st["stages_run"]["topologies"] is True
    assert st["bundles"][0]["num_candidates"] > 0

    client.post("/api/command", json={"cmds": ["run_planner"]})
    st = client.get("/api/state").json()
    assert st["stages_run"]["planner"] is True
    assert st["bundles"][0]["selected_index"] >= 0


def test_unknown_command_over_http_keeps_server_alive():
    client = _client()
    r = client.post("/api/command", json={"cmds": ["bogus_command 1 2 3"]})
    assert r.status_code == 200
    assert r.json()["results"][0]["ok"] is False
    # Server still serves subsequent requests.
    assert client.get("/api/health").json()["ok"] is True


def test_reset_clears_session():
    client = _client()
    client.post("/api/command", json={"cmds": B44_SETUP + ["run_bundler"]})
    assert client.get("/api/state").json()["stages_run"]["bundler"] is True
    client.post("/api/reset")
    assert client.get("/api/state").json()["stages_run"]["bundler"] is False


def test_render_endpoints_full_pipeline():
    """Drive the whole flat flow over HTTP and fetch each render stage."""
    client = _client()
    client.post("/api/command", json={"cmds": B44_SETUP + [
        "run_bundler", "generate_topologies", "run_planner",
        "run_nuts", "run_detailed_nuts"]})

    gen = client.get("/api/render/generation?bundle=1").json()
    assert set(gen) == {"floorplan", "hanan", "bundles", "state"}
    assert gen["bundles"][0]["candidates"]

    nuts = client.get("/api/render/nuts").json()
    assert nuts["nuts"]["num_overlaps"] == 0
    assert nuts["nuts"]["segments"]

    det = client.get("/api/render/detailed").json()
    assert det["detailed"]["num_unplaced"] == 0
    assert len(det["detailed"]["net_segments"]) == 104
    assert len(det["detailed"]["net_vias"]) == 52

    assert "error" in client.get("/api/render/bogus").json()


def test_demos_catalog_lists_flat_and_hier():
    """GET /api/demos returns both built-in demos, each with a setup script and
    a per-stage command map (flat vs hier drive the SAME buttons — the hier one
    maps to the run_hier_* variants)."""
    client = _client()
    body = client.get("/api/demos").json()
    demos = {d["key"]: d for d in body["demos"]}
    assert set(demos) == {"flat", "hier"}
    for d in demos.values():
        assert d["label"] and d["setup"]
        assert set(d["stages"]) == {
            "bundler", "topologies", "planner", "nuts", "dnuts"}
    # The hier stage map wires the buttons to the hierarchy-aware commands.
    assert demos["hier"]["stages"]["bundler"].startswith("run_hier_bundler")
    assert demos["hier"]["stages"]["topologies"] == "generate_hier_topologies"
    assert demos["hier"]["stages"]["planner"].startswith("run_planner hier")
    # Flat stays on the flat flow.
    assert demos["flat"]["stages"]["bundler"] == "run_bundler"


def test_hier_demo_drives_full_pipeline():
    """The hier demo's setup + its per-stage commands run the whole hierarchy
    flow end to end and yield multiple bundles with distinct bundle_ids in the
    NUTS render (the highlight-isolation feature needs those ids)."""
    client = _client()
    demos = {d["key"]: d for d in client.get("/api/demos").json()["demos"]}
    hier = demos["hier"]

    setup = [ln for ln in hier["setup"].splitlines() if ln.strip()]
    r = client.post("/api/command", json={"cmds": setup})
    assert all(res["ok"] for res in r.json()["results"]), r.json()["results"]

    for stage in ("bundler", "topologies", "planner", "nuts", "dnuts"):
        cmd = hier["stages"][stage]
        assert client.post("/api/command", json={"cmds": [cmd]}).json(
            )["results"][0]["ok"], cmd

    st = client.get("/api/state").json()
    assert st["stages_run"] == {
        "bundler": True, "topologies": True, "planner": True,
        "nuts": True, "dnuts": True,
    }
    assert len(st["bundles"]) > 1
    assert st["bundles"][0]["num_candidates"] > 0

    nuts = client.get("/api/render/nuts").json()["nuts"]
    bundle_ids = {s["bundle_id"] for s in nuts["segments"]}
    assert len(bundle_ids) >= 2                # bundle-focus isolation needs these


def test_generation_bundle_stepper_contract():
    """The generation view renders ONE bundle at a time, so the front end steps
    through them with `?bundle=<id>` over the id ring in GET /api/state.  Pins the
    three properties both clients rely on.

    (c) is the one that bit: `state` is the WHOLE session's digest list, NOT the
    filtered render, so `state.bundles[0]` is the first bundle of the session
    whatever `?bundle=` asked for.  Reading the shown bundle's pin state off index
    0 was only ever right while the view was hardcoded to the first bundle — which
    is exactly what the stepper stops being true.
    """
    client = _client()
    demos = {d["key"]: d for d in client.get("/api/demos").json()["demos"]}
    hier = demos["hier"]
    client.post("/api/command", json={
        "cmds": [ln for ln in hier["setup"].splitlines() if ln.strip()]})
    for stage in ("bundler", "topologies", "planner"):
        client.post("/api/command", json={"cmds": [hier["stages"][stage]]})

    ids = [b["id"] for b in client.get("/api/state").json()["bundles"]]
    # (a) the ring is a set of DISTINCT ids — the client cycles it positionally,
    # and a duplicate would be an id the stepper could never leave.
    assert len(ids) > 1 and len(set(ids)) == len(ids)

    # (b) each id serves its OWN single bundle, with its own candidate pool.
    for bid in ids[:4]:
        gen = client.get(f"/api/render/generation?bundle={bid}").json()
        assert [b["id"] for b in gen["bundles"]] == [bid]
        assert gen["bundles"][0]["candidates"]

    # (c) state is unfiltered, so the shown bundle's digest must be found by id.
    other = ids[2]
    gen = client.get(f"/api/render/generation?bundle={other}").json()
    assert gen["state"]["bundles"][0]["id"] != other
    assert [b for b in gen["state"]["bundles"] if b["id"] == other]

def test_a_demo_setup_accumulates_onto_the_session_unless_reset():
    """A demo's setup only ADDS to the session, so running a second demo over the
    first leaves BOTH designs' blocks live — and the two live in far-apart
    coordinate ranges, so a view that frames the union of all blocks blows up.

    This is why the client resets on a demo switch.  Pinned here because the fix
    lives in the client but rests entirely on two server properties: that setup
    accumulates (the hazard) and that POST /api/reset clears the floorplan (the
    remedy).  Either one changing silently would strand the client-side fix.
    """
    client = _client()
    demos = {d["key"]: d for d in client.get("/api/demos").json()["demos"]}

    def run_setup(key):
        client.post("/api/command", json={
            "cmds": [ln for ln in demos[key]["setup"].splitlines() if ln.strip()]})

    # The SESSION floorplan, as the nuts/detailed views frame it — deliberately
    # not the generation payload, whose top-level floorplan is the last shown
    # BUNDLE's frame (cell-local under hier) rather than the session's.
    def blocks():
        return client.get("/api/render/nuts").json()["floorplan"]["blocks"]

    run_setup("flat")
    flat = {b["name"] for b in blocks()}
    flat_ys = [b["bbox"]["y1"] for b in blocks()]

    # (a) the hazard: a second setup with NO reset keeps the first demo's blocks.
    run_setup("hier")
    both = {b["name"] for b in blocks()}
    assert flat < both, "flat blocks should have survived the hier setup"

    # ...and the two designs are decades apart in y, which is what wrecks a frame
    # taken over the union.
    hier_ys = [b["bbox"]["y1"] for b in blocks() if b["name"] not in flat]
    assert min(flat_ys) > max(hier_ys) * 5

    # (b) the remedy: reset clears the floorplan, so the next setup starts clean.
    client.post("/api/reset", json={})
    run_setup("hier")
    assert not ({b["name"] for b in blocks()} & flat)

    # (c) `n_blocks` is how a client decides whether a design is already loaded
    # before it runs a demo's setup.  stages_run CANNOT answer that — a declared
    # but unbundled floorplan reads as all-false — which is exactly the state a
    # freshly-served page finds when the server session outlives it.
    st = client.get("/api/state").json()
    assert st["n_blocks"] == len(blocks()) > 0
    assert not any(st["stages_run"].values()), "setup alone runs no stage"
    client.post("/api/reset", json={})
    assert client.get("/api/state").json()["n_blocks"] == 0


def test_static_assets_send_no_cache_but_revalidate():
    """The demo static mount stamps Cache-Control: no-cache so a rebuilt bundle
    (`bb web` → main.js) is picked up on a normal reload, while ETag revalidation
    still answers 304 for an unchanged file — no full re-download, no hard
    refresh.  See docs/internal/web_static_caching.md."""
    client = _client()
    r = client.get("/")                       # reference client index.html
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"
    etag = r.headers.get("etag")
    assert etag                                # StaticFiles sends a validator
    r2 = client.get("/", headers={"If-None-Match": etag})
    assert r2.status_code == 304               # revalidation still short-circuits


def test_missing_static_asset_404_is_also_no_cache():
    """A 404 for an absent asset (e.g. main.js before `bb web`) must ALSO carry
    no-cache — StaticFiles RAISES for a missing file, so without handling it the
    404 would ship with no header and a browser could cache the failure and keep
    showing the not-built banner after a rebuild (Codex #394)."""
    client = _client()
    r = client.get("/scala/__definitely_missing__.js")   # never on disk
    assert r.status_code == 404
    assert r.headers.get("cache-control") == "no-cache"
