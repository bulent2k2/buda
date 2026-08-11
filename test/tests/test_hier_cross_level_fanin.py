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

"""Cross-level fan-in grouping.

Cross-level nets (driver and receiver at different hierarchy depths) used to
keep STRICT/BIDIRECTIONAL grouping because their single `drv_spec_path`
metadata could not describe a multi-driver group.  Now CONVERGENT/COMBINED
group them by the shared RECEIVER SET into one fan-in bundle carrying per-net
`net_drivers`/`net_receivers` and a `FANIN:root|FROM:leaves` reason, and
generation roots the tree at the shared sink with every driver as a per-bit
tapered leaf — the same treatment as the same-level fan-in, extended across
the hierarchy boundary.  STRICT/BIDIRECTIONAL stay byte-identical.
"""
import contextlib
import io
import os

import pytest

import buda
import buda_cli


def _quiet(s, *cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)


def _xlevel_bdb(width=4):
    """Two deep drivers (core_i1/c0, core_i2/c0 at depth 1) fanning into one
    top-level sink (snk_i at depth 0) — a cross-level fan-in."""
    db = buda.BDB(":memory:")
    db.add_cell("core_cell", 200, 120)
    db.add_cell("leaf_cell", 60, 60)
    db.add_inst_to_cell("core_cell", "c0", "leaf_cell", 20, 30)
    db.add_inst("core_i1", "core_cell", "", 0, 0)
    db.add_inst("core_i2", "core_cell", "", 0, 300)
    db.add_inst("snk_i", "leaf_cell", "", 600, 150)
    for i in range(width):
        db.add_net_pins(f"xa_{i}", "core_i1/c0.out", ["snk_i.in"])
        db.add_net_pins(f"xb_{i}", "core_i2/c0.out", ["snk_i.in"])
    buda.BustermGen(db).derive(1)
    return db


def _bundles(strat, extra=()):
    s = buda_cli.BudaSession(); s.no_viz = True
    s.bdb = _xlevel_bdb()
    _quiet(s, "def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10", *extra,
           f"run_hier_bundler {strat}")
    return s


# ── grouping ──────────────────────────────────────────────────────────────────

def test_strict_keeps_cross_level_drivers_separate():
    s = _bundles("STRICT")
    xl = [w.input.original_bundle for w in s.bundles
          if w.input.original_bundle.drv_spec_depth >= 0]
    assert len(xl) == 2                       # one bundle per driver
    assert all(not b.net_drivers for b in xl)


def test_bidirectional_keeps_distinct_endpoint_sets_separate():
    # The two nets have DIFFERENT endpoint sets ({core_i1/c0,snk_i} vs
    # {core_i2/c0,snk_i}), so BIDIRECTIONAL does not merge them.
    s = _bundles("BIDIRECTIONAL")
    xl = [w.input.original_bundle for w in s.bundles
          if w.input.original_bundle.drv_spec_depth >= 0]
    assert len(xl) == 2


@pytest.mark.parametrize("strat", ["CONVERGENT", "COMBINED"])
def test_convergent_and_combined_form_one_fanin_bundle(strat):
    s = _bundles(strat)
    xl = [w.input.original_bundle for w in s.bundles
          if w.input.original_bundle.drv_spec_depth >= 0]
    assert len(xl) == 1                       # merged into one fan-in bundle
    b = xl[0]
    assert b.reason.startswith("FANIN:snk_i|FROM:")
    assert set(b.reason.split("|FROM:")[1].split(",")) >= {"core_i1/c0",
                                                           "core_i2/c0"}
    # Per-net endpoints aligned with sorted net_names.
    nets = list(b.get_net_names())
    assert len(b.net_drivers) == len(nets)
    for n, d in zip(nets, b.net_drivers):
        assert d == ("core_i1/c0" if n.startswith("xa_") else "core_i2/c0")
    assert all(list(r) == ["snk_i"] for r in b.net_receivers)


def test_set_bundling_no_convergent_keeps_cross_level_separate():
    s = _bundles("CONVERGENT", extra=["set_bundling * no_convergent"])
    xl = [w.input.original_bundle for w in s.bundles
          if w.input.original_bundle.drv_spec_depth >= 0]
    assert len(xl) == 2                       # opted out → not merged


def test_override_does_not_fragment_a_strict_equivalent_bus():
    """A set_bundling override that disables convergent for one bus must not
    FRAGMENT that bus: its strictly-identical bits (same driver + sink) stay
    bundled via the strict signature, only the fan-in merge with the other
    driver is suppressed (Codex #384 P2 — the union-find always unions strict)."""
    db = buda.BDB(":memory:")
    db.add_cell("core_cell", 200, 120)
    db.add_cell("leaf_cell", 60, 60)
    db.add_inst_to_cell("core_cell", "c0", "leaf_cell", 20, 30)
    db.add_inst("core_i1", "core_cell", "", 0, 0)
    db.add_inst("core_i2", "core_cell", "", 0, 300)
    db.add_inst("snk_i", "leaf_cell", "", 600, 150)
    for i in range(4):                        # one strict bus, deep→top
        db.add_net_pins(f"blk_{i}", "core_i1/c0.out", ["snk_i.in"])
        db.add_net_pins(f"other_{i}", "core_i2/c0.out", ["snk_i.in"])
    buda.BustermGen(db).derive(1)
    s = buda_cli.BudaSession(); s.no_viz = True
    s.bdb = db
    _quiet(s, "def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10",
           "set_bundling blk_ no_convergent", "run_hier_bundler CONVERGENT")
    xl = {frozenset(w.input.original_bundle.get_net_names())
          for w in s.bundles if w.input.original_bundle.drv_spec_depth >= 0}
    # blk_* stays whole (one strict bundle); other_* is its own bundle.
    assert frozenset(f"blk_{i}" for i in range(4)) in xl
    assert frozenset(f"other_{i}" for i in range(4)) in xl


# ── generation + routing ──────────────────────────────────────────────────────

@pytest.mark.mid
def test_cross_level_fanin_routes_to_every_driver():
    s = buda_cli.BudaSession(); s.no_viz = True
    s.bdb = _xlevel_bdb()
    _quiet(s, "def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10",
           "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
           "run_hier_bundler CONVERGENT", "generate_hier_topologies",
           "set_track_pitch 2.0", "run_planner hier 3", "run_nuts")
    w = next(w for w in s.bundles
             if w.input.original_bundle.drv_spec_depth >= 0)
    t = w.input.candidates[w.plan.selected_topology_index]
    assert set(t.connected_block_names) >= {"core_i1/c0", "core_i2/c0", "snk_i"}
    assert t.seg_bits                          # per-bit tapered
    assert s.nuts_result.num_overlaps == 0
    check = io.StringIO()
    with contextlib.redirect_stdout(check):
        s.do_command("check_design nuts")
    assert "Success" in check.getvalue()


# ── resume ────────────────────────────────────────────────────────────────────

@pytest.mark.mid
def test_cross_level_fanin_route_survives_resume(tmp_path):
    """A resumed fan-in comes back COMPLETE and TAPERED.

    Two things have to hold, and they used to be one.  The route is recovered
    from the persisted `FANIN:` reason — that was always true.  The per-bit
    taper is recovered from the per-bit endpoints persisted on `bundle_net`
    (v27); before that it fell back to "conservative full width", which reads
    like a safe default and is really a DIFFERENT, WIDER design than the one
    checkpointed, reported perfectly clean.  So this now asserts the taper is
    back, not that it is missing."""
    bdb = str(tmp_path / "xl.bdb")
    setup = ["def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10"]

    a = buda_cli.BudaSession(); a.no_viz = True
    _quiet(a, *setup, f"open_bdb {bdb}")
    db = a.bdb
    db.add_cell("core_cell", 200, 120); db.add_cell("leaf_cell", 60, 60)
    db.add_inst_to_cell("core_cell", "c0", "leaf_cell", 20, 30)
    db.add_inst("core_i1", "core_cell", "", 0, 0)
    db.add_inst("core_i2", "core_cell", "", 0, 300)
    db.add_inst("snk_i", "leaf_cell", "", 600, 150)
    buda.BustermGen(db).derive(1)
    _quiet(a, "bdb_net_mode on")
    for i in range(4):
        _quiet(a, f"add_net xa_{i} core_i1/c0.out snk_i.in",
               f"add_net xb_{i} core_i2/c0.out snk_i.in")
    _quiet(a, "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
           "run_hier_bundler CONVERGENT", "generate_hier_topologies",
           "set_track_pitch 2.0", "run_planner hier 3", "run_nuts")
    wa = next(w for w in a.bundles
              if w.input.original_bundle.drv_spec_depth >= 0)
    reason_a = wa.input.original_bundle.reason
    assert reason_a.startswith("FANIN:")
    # The taper the SAVING session had, per candidate — the thing the resume
    # has to reproduce.  Keyed by uid, not index: the comparison must not
    # depend on the pool coming back in the same order (it does, but a test
    # that would silently pass if it stopped is not testing this).
    taper_a = {buda.topo_uid(t): {k: sorted(v) for k, v in t.seg_bits.items()}
               for t in wa.input.candidates}
    assert any(taper_a.values()), "the saving session had no taper to lose"
    del a

    b = buda_cli.BudaSession(); b.no_viz = True
    _quiet(b, *setup, f"open_bdb {bdb}", "add_blocks_from_bdb 0",
           "add_blocks_from_bdb 1 skip", "load_pipeline",
           "generate_hier_topologies")
    w = next(w for w in b.bundles
             if w.input.original_bundle.drv_spec_depth >= 0)
    hb = w.input.original_bundle
    assert hb.reason.startswith("FANIN:")     # reason persisted
    # v27: the per-bit endpoints ride the bundle_net rows, in bit order.
    assert len(hb.net_drivers) == len(hb.get_net_names())
    assert set(hb.net_drivers) == {"core_i1/c0", "core_i2/c0"}
    assert all(list(r) == ["snk_i"] for r in hb.net_receivers)
    idx = w.plan.selected_topology_index
    t = w.input.candidates[idx if idx >= 0 else 0]
    assert set(t.connected_block_names) >= {"core_i1/c0", "core_i2/c0", "snk_i"}
    # ...and the taper itself comes back, candidate for candidate.  Compared
    # against what the saving session HAD rather than against a guessed
    # shape: on this small design the winning 2-segment trunk legitimately
    # carries every bit on both segments, so "some segment is narrower" is
    # not the property — "the same as before" is.
    taper_b = {buda.topo_uid(c): {k: sorted(v) for k, v in c.seg_bits.items()}
               for c in w.input.candidates}
    shared = set(taper_a) & set(taper_b)
    assert shared, "no candidate survived the round trip by uid"
    mismatched = {u: (taper_a[u], taper_b[u])
                  for u in shared if taper_a[u] != taper_b[u]}
    assert not mismatched, (
        f"{len(mismatched)} restored candidate(s) came back with a different "
        f"per-bit taper: {list(mismatched.items())[:2]}")


@pytest.mark.mid
def test_a_resumed_fanin_routes_the_same_metal_without_regenerating(tmp_path):
    """The resume path proper: load the checkpoint and go straight on.

    The sibling test above regenerates after loading, which re-derives the
    taper on the way — so it covers the endpoints being PERSISTED but not
    them being USED on load.  This one never regenerates, which is what a
    real resume does (`load_pipeline` -> `run_nuts` -> `run_detailed_nuts`),
    and asserts the payoff directly: the same number of bit-wires as the
    session that saved it.  Untapered, the driver stubs carry all 8 bits
    instead of their own 4, and the resumed design is quietly wider.
    """
    bdb = str(tmp_path / "xr.bdb")
    # Track patterns, unlike the sibling test: bit-wires are a DETAILED-NUTS
    # artifact, and without signal tracks there is nothing to place them on.
    setup = ["def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10",
             "def_track_pattern 4 0 (SIGNAL 1 1)x16",
             "def_track_pattern 5 0 (SIGNAL 1 1)x16"]

    a = buda_cli.BudaSession(); a.no_viz = True
    _quiet(a, *setup, f"open_bdb {bdb}")
    db = a.bdb
    db.add_cell("core_cell", 200, 120); db.add_cell("leaf_cell", 60, 60)
    db.add_inst_to_cell("core_cell", "c0", "leaf_cell", 20, 30)
    db.add_inst("core_i1", "core_cell", "", 0, 0)
    db.add_inst("core_i2", "core_cell", "", 0, 300)
    db.add_inst("snk_i", "leaf_cell", "", 600, 150)
    buda.BustermGen(db).derive(1)
    _quiet(a, "bdb_net_mode on")
    for i in range(4):
        _quiet(a, f"add_net xa_{i} core_i1/c0.out snk_i.in",
               f"add_net xb_{i} core_i2/c0.out snk_i.in")
    _quiet(a, "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
           "run_hier_bundler CONVERGENT", "generate_hier_topologies")

    wa = next(w for w in a.bundles
              if w.input.original_bundle.drv_spec_depth >= 0)
    nbits = len(wa.input.original_bundle.get_net_names())
    # Pin a GENUINELY tapered candidate: on this design the cheapest shape is
    # a 2-segment trunk every bit traverses, where tapered and untapered are
    # the same map and the test would prove nothing.
    tapered = [i for i, c in enumerate(wa.input.candidates)
               if c.seg_bits and any(len(v) < nbits for v in c.seg_bits.values())]
    assert tapered, "no tapered candidate to pin"
    bid = wa.input.original_bundle.id
    _quiet(a, f"select_topology id:{bid} {tapered[0] + 1}",   # 1-based
           "set_track_pitch 2.0", "run_planner hier 3", "run_nuts",
           "run_detailed_nuts")
    saved_bits = len(a.detailed_result.net_segments)
    sel_a = wa.input.candidates[wa.plan.selected_topology_index]
    taper_a = {k: sorted(v) for k, v in sel_a.seg_bits.items()}
    assert any(len(v) < nbits for v in taper_a.values()), "pinned the wrong one"
    del a

    b = buda_cli.BudaSession(); b.no_viz = True
    _quiet(b, *setup, f"open_bdb {bdb}", "add_blocks_from_bdb 0",
           "add_blocks_from_bdb 1 skip", "load_pipeline")   # NO regeneration
    wb = next(w for w in b.bundles
              if w.input.original_bundle.drv_spec_depth >= 0)
    sel_b = wb.input.candidates[wb.plan.selected_topology_index]
    assert {k: sorted(v) for k, v in sel_b.seg_bits.items()} == taper_a, \
        "the restored candidate's per-bit taper differs from the saved one"

    _quiet(b, "set_track_pitch 2.0", "run_nuts", "run_detailed_nuts")
    assert len(b.detailed_result.net_segments) == saved_bits, (
        f"resumed design routes {len(b.detailed_result.net_segments)} "
        f"bit-wires where the checkpoint had {saved_bits}")


@pytest.mark.mid
def test_a_kept_fanin_bundle_keeps_its_endpoints_through_a_re_persist(tmp_path):
    """A fan-in bundle held alive only by a USER topology keeps its per-bit
    endpoints when a LATER run re-persists without it (Codex P2 on #694).

    `_persist_bundles` clears and rewrites, and `clear_bundles(keep_user)`
    keeps such a row while wiping every `bundle_net` row — which is where the
    endpoints live.  The membership is snapshotted and put back (audit
    P3-04); without the endpoints riding along, the bundle survives with bare
    names and resumes untapered, and nothing in THIS run would show it.
    """
    bdb = str(tmp_path / "keep.bdb")
    setup = ["def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10"]

    s = buda_cli.BudaSession(); s.no_viz = True
    _quiet(s, *setup, f"open_bdb {bdb}")
    db = s.bdb
    db.add_cell("core_cell", 200, 120); db.add_cell("leaf_cell", 60, 60)
    db.add_inst_to_cell("core_cell", "c0", "leaf_cell", 20, 30)
    db.add_inst("core_i1", "core_cell", "", 0, 0)
    db.add_inst("core_i2", "core_cell", "", 0, 300)
    db.add_inst("snk_i", "leaf_cell", "", 600, 150)
    buda.BustermGen(db).derive(1)
    _quiet(s, "bdb_net_mode on")
    for i in range(4):
        _quiet(s, f"add_net xa_{i} core_i1/c0.out snk_i.in",
               f"add_net xb_{i} core_i2/c0.out snk_i.in")
    _quiet(s, "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
           "run_hier_bundler CONVERGENT", "generate_hier_topologies")

    w = next(w for w in s.bundles
             if w.input.original_bundle.drv_spec_depth >= 0)
    bid = str(w.input.original_bundle.id)
    stored = s.bdb.bundle_net_endpoints(bid)
    assert any(d for d, _r in stored), "nothing was stored to lose"

    # Hold the row alive the way a USER topology does, then re-persist a run
    # this bundle is not part of — the absent-membership path.
    tr = buda.TopoRow()
    tr.id = bid; tr.cand_index = 99; tr.source = "user"; tr.type = "USER"
    s.bdb.add_topology(tr)
    s.bundles = [b for b in s.bundles if b is not w]
    s._persist_bundles("CONVERGENT")

    assert bid in {b.id for b in s.bdb.all_bundles()}, "the row was not kept"
    assert s.bdb.bundle_net_endpoints(bid) == stored, (
        "the kept bundle lost its per-bit endpoints, so it would resume "
        "untapered")
