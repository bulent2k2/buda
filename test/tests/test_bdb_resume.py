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

"""Resume / rehydrate the pipeline from the BDB (`load_pipeline`, schema v9).

Two-phase flows: phase 1 runs part of the pipeline with a BDB open and stops;
phase 2 is a FRESH session that re-declares the setup (layers/patterns/blocks —
geometry-derived state is recomputed, not persisted), opens the same BDB,
`load_pipeline`s, and continues. Continuations must reproduce the single-session
results exactly (same rows, same route_snapshot hash): rehydrated candidates are
re-annotated via `annotate_topology`, so ConnTopology/planner/NUTS see the same
authoritative endpoint taps as freshly generated ones.
"""

import contextlib
import io

import buda
import buda_cli


def _quiet(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        for c in cmds:
            session.do_command(c)
    return buf.getvalue()


def _fresh(*cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = _quiet(s, *cmds)
    return s, out


SETUP = ("source flow/rnr/mix_tracks.buda",
         "add_block A 0 0 200 400",
         "add_block B 600 800 800 1200")
NETS = ("add_bus d[8] A.p B.p",)


def _bus_rows(s, bid="1"):
    return [(g.seg_idx, g.layer, g.x1, g.y1, g.x2, g.y2, g.track_position)
            for g in s.bdb.bus_segments(bid)]


def test_resume_after_topogen_continues_to_planner_and_nuts(tmp_path):
    # Stop after generate_topologies -> reopen -> load_pipeline -> run_planner
    # -> run_nuts. Must equal a single-session run of the same flow.
    db = str(tmp_path / "a.bdb")
    s1, _ = _fresh(*SETUP, f"open_bdb {db}", *NETS,
                   "run_bundler", "generate_topologies")
    n_cands = len(s1.bundles[0].input.candidates)
    del s1

    s2, out = _fresh(*SETUP, f"open_bdb {db}", "load_pipeline")
    assert "rehydrated 1 bundle(s)" in out
    w = s2.bundles[0]
    assert len(w.input.candidates) == n_cands
    assert w.plan.selected_topology_index == -1          # planner not run yet
    assert list(w.input.original_bundle.get_net_names()) == \
        [f"d_{i}" for i in range(8)]
    _quiet(s2, "run_planner 3", "run_nuts")

    ref, _ = _fresh(*SETUP, f"open_bdb {tmp_path / 'ref.bdb'}", *NETS,
                    "run_bundler", "generate_topologies", "run_planner 3",
                    "run_nuts")
    assert s2.bundles[0].plan.selected_topology_index == \
        ref.bundles[0].plan.selected_topology_index
    assert list(s2.bundles[0].plan.seg_layers) == \
        list(ref.bundles[0].plan.seg_layers)
    assert _bus_rows(s2) == _bus_rows(ref)
    assert s2.bdb.route_snapshot().hash == ref.bdb.route_snapshot().hash


def test_resume_after_planner_continues_to_nuts(tmp_path):
    # Stop after run_planner -> reopen -> load_pipeline (selection + layers
    # restored) -> run_nuts. Must equal the single-session routing.
    db = str(tmp_path / "b.bdb")
    s1, _ = _fresh(*SETUP, f"open_bdb {db}", *NETS,
                   "run_bundler", "generate_topologies", "run_planner 3")
    sel = s1.bundles[0].plan.selected_topology_index
    layers = list(s1.bundles[0].plan.seg_layers)
    del s1

    s2, _ = _fresh(*SETUP, f"open_bdb {db}", "load_pipeline")
    w = s2.bundles[0]
    assert w.plan.selected_topology_index == sel         # plan restored
    assert list(w.plan.seg_layers) == layers
    _quiet(s2, "run_nuts")
    assert s2.nuts_result.num_overlaps == 0
    assert all(t.placed for t in s2.nuts_result.segments)

    ref, _ = _fresh(*SETUP, f"open_bdb {tmp_path / 'ref.bdb'}", *NETS,
                    "run_bundler", "generate_topologies", "run_planner 3",
                    "run_nuts")
    assert _bus_rows(s2) == _bus_rows(ref)
    assert s2.bdb.route_snapshot().hash == ref.bdb.route_snapshot().hash


def test_resume_after_nuts_continues_to_dnuts(tmp_path):
    # Stop after run_nuts -> reopen -> load_pipeline (NUTSResult rehydrated from
    # bus_segment incl. the v9 interval columns) -> run_detailed_nuts. The
    # per-bit rows and the detailed route_snapshot must equal a single-session run.
    db = str(tmp_path / "c.bdb")
    s1, _ = _fresh(*SETUP, f"open_bdb {db}", *NETS,
                   "run_bundler", "generate_topologies", "run_planner 3",
                   "run_nuts")
    rows1 = _bus_rows(s1)
    del s1

    s2, out = _fresh(*SETUP, f"open_bdb {db}", "load_pipeline")
    assert "placed bus segment(s)" in out                # NUTS stage detected
    assert s2.nuts_result is not None
    # Rehydrated TrackSegments carry the persisted geometry + solver state.
    ts = {t.seg_idx: t for t in s2.nuts_result.segments}
    for g in s2.bdb.bus_segments("1"):
        t = ts[g.seg_idx]
        assert (t.layer, t.horiz, t.placed) == (g.layer, g.is_horiz, g.placed)
        assert t.track_position == g.track_position
        assert (t.interval_lo, t.interval_hi) == (g.interval_lo, g.interval_hi)
    _quiet(s2, "run_detailed_nuts")
    assert s2.detailed_result.num_unplaced == 0

    ref, _ = _fresh(*SETUP, f"open_bdb {tmp_path / 'ref.bdb'}", *NETS,
                    "run_bundler", "generate_topologies", "run_planner 3",
                    "run_nuts", "run_detailed_nuts")
    key = lambda s: sorted((n.seg_idx, n.bit_index, n.layer, n.track_position,
                            n.span_lo, n.span_hi)
                           for n in s.detailed_result.net_segments)
    assert key(s2) == key(ref)
    assert _bus_rows(s2) == rows1                        # untouched by resume
    assert s2.bdb.route_snapshot().stage == "detailed_nuts"
    assert s2.bdb.route_snapshot().hash == ref.bdb.route_snapshot().hash


# --- congested scenario: ripup_reroute genuinely moves a bundle --------------
# Two 8-bit buses into a shared receiver with all H layers keeped-out except a
# 30-unit band: pinned straight routes collide (NUTS overlap). ripup_reroute
# re-routes one bus to a detour candidate whose H trunk clears the band, then
# RE-PERSISTS the final routing — so a fresh session can resume from it.

NARROW = ("def_layer 4 M4 H TOP 50",
          "def_layer 5 M5 V TOP 50",
          "def_layer 7 M7 V TOP 50",
          "def_track_pattern 4 -200 POWER 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 "
          "SIGNAL 1 0.5 SIGNAL 1 0.5 GROUND 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 "
          "SIGNAL 1 0.5 SIGNAL 1 0.5",
          "def_track_pattern 5 0 POWER 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 "
          "SIGNAL 1 0.5 SIGNAL 1 0.5 GROUND 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 "
          "SIGNAL 1 0.5 SIGNAL 1 0.5",
          "def_track_pattern 7 0 POWER 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 "
          "SIGNAL 1 0.5 SIGNAL 1 0.5 GROUND 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 "
          "SIGNAL 1 0.5 SIGNAL 1 0.5",
          "add_block D1 0 1000 200 1400",
          "add_block D2 400 1000 600 1400",
          "add_block R 2400 1000 2600 1400",
          "add_keepout 0 1220 3000 4000 4",   # M4 blocked above the band...
          "add_keepout 0 0 3000 1190 4")      # ...and below -> only [1190,1220]
NARROW_NETS = ("add_bus a[8] D1.p R.p",
               "add_bus b[8] D2.p R.p")


def _narrow_phase1(db):
    s, _ = _fresh(*NARROW, f"open_bdb {db}", *NARROW_NETS,
                  "run_bundler", "generate_topologies",
                  "select_topology 1 1", "select_topology 2 1",
                  "run_planner", "run_nuts")
    return s


def test_resume_after_nuts_and_ripup_continues_to_dnuts(tmp_path):
    # Stop after run_nuts + ripup_reroute -> reopen -> load_pipeline ->
    # run_detailed_nuts.
    db = str(tmp_path / "rr.bdb")
    s1 = _narrow_phase1(db)
    assert s1.nuts_result.num_overlaps > 0, "fixture should start congested"
    pre_hash = s1.bdb.route_snapshot().hash
    out = _quiet(s1, "ripup_reroute")
    assert s1.nuts_result.num_overlaps == 0, out         # ripup cleared it
    assert "re-persisted post-ripup routing" in out      # BDB reflects final state
    post_hash = s1.bdb.route_snapshot().hash
    assert post_hash != pre_hash                         # routing really changed
    sels1 = {w.input.original_bundle.id: w.plan.selected_topology_index
             for w in s1.bundles}
    _quiet(s1, "run_detailed_nuts")                      # single-session reference
    assert s1.detailed_result.num_unplaced == 0
    ref_hash = s1.bdb.route_snapshot().hash
    ref_nets = sorted((n.seg_idx, n.bit_index, n.layer, n.track_position)
                      for n in s1.detailed_result.net_segments)
    # Reset the BDB back to the post-ripup checkpoint (drop the detailed rows)
    # so phase 2 resumes from run_nuts+ripup, not from the reference DNUTS.
    s1.bdb.clear_detailed_routing()
    s1._persist_route_snapshot(s1.bdb.route_snapshot().n_bus_segments, 0,
                               "abstract_nuts")
    del s1

    s2, out = _fresh(*NARROW, f"open_bdb {db}", "load_pipeline")
    assert "placed bus segment(s)" in out
    sels2 = {w.input.original_bundle.id: w.plan.selected_topology_index
             for w in s2.bundles}
    assert sels2 == sels1                    # post-ripup selections restored
    _quiet(s2, "run_detailed_nuts")
    assert s2.detailed_result.num_unplaced == 0
    got_nets = sorted((n.seg_idx, n.bit_index, n.layer, n.track_position)
                      for n in s2.detailed_result.net_segments)
    assert got_nets == ref_nets              # identical per-bit routing
    assert s2.bdb.route_snapshot().hash == ref_hash


def test_hier_expanded_resume(bdb_input):
    # Hier flow: stop after run_planner hier + run_nuts -> fresh session ->
    # re-declare layers + load blocks from the BDB -> load_pipeline expanded.
    # The post-expansion per-instance wrappers, their selections, and the NUTS
    # result all rehydrate.
    db = bdb_input("hier_mixed")
    s1, _ = _fresh("source flow/rnr/mix_tracks.buda", f"open_bdb {db}",
                   "derive_busterms 1", "run_hier_bundler depth 1",
                   "generate_hier_topologies", "run_planner hier 3", "run_nuts")
    sels1 = {w.input.original_bundle.id: w.plan.selected_topology_index
             for w in s1.bundles}
    n_bus = len(s1.nuts_result.segments)
    del s1

    s2, out = _fresh("source flow/rnr/mix_tracks.buda", f"open_bdb {db}",
                     "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
                     "add_blocks_from_bdb 2 skip", "load_pipeline expanded")
    assert "rehydrated" in out
    sels2 = {w.input.original_bundle.id: w.plan.selected_topology_index
             for w in s2.bundles}
    assert sels2 == sels1                     # per-instance wrappers + selections
    assert s2.nuts_result is not None
    assert len(s2.nuts_result.segments) == n_bus


# --- guards -------------------------------------------------------------------

def test_load_pipeline_requires_open_bdb():
    s, out = _fresh(*SETUP, "load_pipeline")
    assert "requires an open BDB" in out and not s.bundles


def test_load_pipeline_requires_persisted_bundles(tmp_path):
    s, out = _fresh(*SETUP, f"open_bdb {tmp_path / 'empty.bdb'}",
                    "load_pipeline")
    assert "no persisted bundles" in out and not s.bundles


def test_load_pipeline_fails_fast_on_missing_blocks(tmp_path):
    # Phase 2 forgot to re-declare the blocks: fail with a clear message, do
    # not build wrappers over a floorplan that can't route them.
    db = str(tmp_path / "m.bdb")
    s1, _ = _fresh(*SETUP, f"open_bdb {db}", *NETS,
                   "run_bundler", "generate_topologies")
    del s1
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    out = _quiet(s2, "source flow/rnr/mix_tracks.buda",   # layers but NO blocks
                 f"open_bdb {db}", "load_pipeline")
    assert "not in the current Floorplan" in out
    assert not s2.bundles
