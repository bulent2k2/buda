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

"""Hier bundler CONVERGENT / COMBINED strategies + set_bundling overrides.

The hier flow applies the strategy lattice PER BUNDLING DEPTH to same-level
nets (each net bundled once at its most specific level — the depth-aware
semantics: fan-ins in different subtrees or at different specificity depths
stay separate routing problems).  A multi-driver group becomes a fan-in
bundle: reason 'FANIN:root|FROM:leaves' (parsed by _parse_bundle_reason into
the flat root+leaves convention), per-net endpoints in
HBundle.net_drivers/net_receivers (aligned with sorted net_names), and
generation routes it as a per-bit tapered fan-in tree in the bundle's frame
(cell-local for case (a), depth floorplan for case (b)).  Cell-local fan-in
templates merge with their replicas exactly like STRICT templates.
Cross-level nets keep STRICT/BIDIRECTIONAL grouping (documented follow-on)."""

import contextlib
import io

import buda
import buda_cli


def _fanin_db(return_net=False):
    """proc_cell: children pa, pb fan into shared child ps (cell-local
    fan-in), instantiated twice; plus a depth-0 cross-block fan-in
    (L0, L1 → SNK).  return_net adds ps→pa inside each instance — the
    bidirectional partner that only COMBINED chains into the fan-in."""
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 90, 80)
    db.add_inst_to_cell("proc_cell", "pa", "pipe_cell", 10, 60)
    db.add_inst_to_cell("proc_cell", "pb", "pipe_cell", 120, 60)
    db.add_inst_to_cell("proc_cell", "ps", "pipe_cell", 300, 60)
    db.add_inst("proc_i1", "proc_cell", "", 0, 0)
    db.add_inst("proc_i2", "proc_cell", "", 600, 0)
    for i in ("1", "2"):
        for k in range(2):
            db.add_net_pins(f"fa{i}_{k}", f"proc_i{i}/pa.out",
                            [f"proc_i{i}/ps.r{k}"])
        db.add_net_pins(f"fb{i}_0", f"proc_i{i}/pb.out",
                        [f"proc_i{i}/ps.rb"])
        if return_net:
            db.add_net_pins(f"rt{i}_0", f"proc_i{i}/ps.out",
                            [f"proc_i{i}/pa.rr"])
    db.add_cell("leaf_cell", 60, 60)
    db.add_inst("L0", "leaf_cell", "", 0, 400)
    db.add_inst("L1", "leaf_cell", "", 0, 600)
    db.add_inst("SNK", "leaf_cell", "", 1100, 500)
    db.add_net_pins("x0", "L0.out", ["SNK.r0"])
    db.add_net_pins("x1", "L1.out", ["SNK.r1"])
    buda.BustermGen(db).derive(1)
    return db


def _session(db):
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = db
    for c in ("def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
              "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
              "def_track_pattern 6 0 SIGNAL 1 1",
              "def_track_pattern 7 0 SIGNAL 1 1",
              "def_track_pattern 4 0 SIGNAL 1 1",
              "def_track_pattern 5 0 SIGNAL 1 1"):
        _run(s, c)
    return s


def _run(s, cmd):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command(cmd)
    return out.getvalue()


def _by_nets(s):
    return {frozenset(w.input.original_bundle.get_net_names()):
            w.input.original_bundle for w in s.bundles}


def test_hier_convergent_builds_fanin_bundles_and_merges_templates():
    s = _session(_fanin_db())
    _run(s, "run_hier_bundler CONVERGENT")
    bm = _by_nets(s)
    # Depth-0 cross-block fan-in.
    b0 = bm[frozenset({"x0", "x1"})]
    assert b0.reason == "FANIN:SNK|FROM:L0,L1,"
    assert list(b0.net_drivers) == ["L0", "L1"]
    # Cell-local fan-in per instance, template + replica merged.
    t = bm[frozenset({"fa1_0", "fa1_1", "fb1_0"})]
    r = bm[frozenset({"fa2_0", "fa2_1", "fb2_0"})]
    assert t.cell_context == "proc_cell" and r.parent_id == t.id
    assert list(t.instances) == ["proc_i1", "proc_i2"]
    assert t.reason.startswith("FANIN:proc_i1/ps|FROM:")
    # Per-net endpoint metadata aligned with sorted net_names.
    assert list(t.net_drivers) == ["proc_i1/pa", "proc_i1/pa", "proc_i1/pb"]


def test_hier_convergent_fanin_routes_clean_with_taper():
    s = _session(_fanin_db())
    for c in ("run_hier_bundler CONVERGENT", "generate_hier_topologies",
              "run_planner hier", "run_nuts", "run_detailed_nuts"):
        out = _run(s, c)
    assert s.detailed_result.num_unplaced == 0
    assert s.nuts_result.num_overlaps == 0
    # Every fan-in wrapper's selected candidate carries taper membership
    # covering all its bits (union of per-segment members = all bits).
    # After run_planner hier self.bundles is the EXPANDED per-instance list:
    # the reason and the tapered candidates ride the expansion copies
    # (net_drivers stays on the pre-expansion template only).
    seen = 0
    for w in s.bundles:
        b = w.input.original_bundle
        if (not b.reason.startswith("FANIN:")
                or w.plan.selected_topology_index < 0):
            continue
        t = w.input.candidates[w.plan.selected_topology_index]
        assert t.seg_bits, b.reason
        members = sorted({bit for v in dict(t.seg_bits).values() for bit in v})
        assert members == list(range(len(b.get_net_names())))
        seen += 1
    assert seen >= 3          # depth-0 fan-in + both cell instances


def test_hier_combined_chains_return_net_into_fanin():
    """The return net ps→pa merges with the fan-in only under COMBINED
    (bidir with nothing alone; the chain runs through the shared blocks):
    CONVERGENT keeps it separate, COMBINED joins all four nets."""
    s = _session(_fanin_db(return_net=True))
    _run(s, "run_hier_bundler CONVERGENT")
    conv = set(_by_nets(s))
    assert frozenset({"rt1_0"}) in conv       # return net alone
    s2 = _session(_fanin_db(return_net=True))
    out = _run(s2, "run_hier_bundler COMBINED")
    assert "COMBINED" in out
    comb = set(_by_nets(s2))
    assert frozenset({"fa1_0", "fa1_1", "fb1_0", "rt1_0"}) in comb
    # And it still routes clean end to end.
    for c in ("generate_hier_topologies", "run_planner hier", "run_nuts",
              "run_detailed_nuts"):
        _run(s2, c)
    assert s2.detailed_result.num_unplaced == 0


def test_hier_set_bundling_override_gates_merges():
    """'fb strict' keeps the fb nets out of the cell fan-in merge while the
    fa nets still form their (single-driver) bundle."""
    s = _session(_fanin_db())
    _run(s, "set_bundling fb strict")
    _run(s, "run_hier_bundler CONVERGENT")
    bm = _by_nets(s)
    assert frozenset({"fb1_0"}) in bm         # excluded from the merge
    assert frozenset({"fa1_0", "fa1_1"}) in bm
    # x nets unaffected (no matching prefix): still the depth-0 fan-in.
    assert frozenset({"x0", "x1"}) in bm


def test_hier_strict_and_bidirectional_unchanged():
    """The pure modes keep their historical partitions (no FANIN reasons,
    no fan-in metadata)."""
    for strat in ("STRICT", "BIDIRECTIONAL"):
        s = _session(_fanin_db())
        _run(s, f"run_hier_bundler {strat}")
        for w in s.bundles:
            b = w.input.original_bundle
            assert not b.reason.startswith("FANIN:"), (strat, b.reason)
            assert not list(b.net_drivers), (strat, b.reason)
        bm = _by_nets(s)
        assert frozenset({"x0"}) in bm and frozenset({"x1"}) in bm


def test_hier_bundler_rejects_bad_strategy_and_accepts_new_ones():
    s = _session(_fanin_db())
    assert "Error" in _run(s, "run_hier_bundler SOMETHING")
    for strat in ("CONVERGENT", "COMBINED"):
        s2 = _session(_fanin_db())
        out = _run(s2, f"run_hier_bundler {strat}")
        assert "HierBundler:" in out and "Error" not in out
