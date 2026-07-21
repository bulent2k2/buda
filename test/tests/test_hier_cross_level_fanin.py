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
    """net_drivers is not persisted; the FANIN reason must still recover every
    driver on load_pipeline (the taper falls back to conservative full width)."""
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
    reason_a = next(w.input.original_bundle.reason for w in a.bundles
                    if w.input.original_bundle.drv_spec_depth >= 0)
    assert reason_a.startswith("FANIN:")
    del a

    b = buda_cli.BudaSession(); b.no_viz = True
    _quiet(b, *setup, f"open_bdb {bdb}", "add_blocks_from_bdb 0",
           "add_blocks_from_bdb 1 skip", "load_pipeline",
           "generate_hier_topologies")
    w = next(w for w in b.bundles
             if w.input.original_bundle.drv_spec_depth >= 0)
    hb = w.input.original_bundle
    assert hb.reason.startswith("FANIN:")     # reason persisted
    assert not hb.net_drivers                  # net_drivers NOT persisted
    idx = w.plan.selected_topology_index
    t = w.input.candidates[idx if idx >= 0 else 0]
    assert set(t.connected_block_names) >= {"core_i1/c0", "core_i2/c0", "snk_i"}
