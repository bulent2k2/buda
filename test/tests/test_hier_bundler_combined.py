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


def test_hier_convergent_single_driver_cross_block_routes():
    """Review #276 blocking 1: a single-driver cross-block CONVERGENT group
    (the overwhelmingly common case — one bus, one driver) must carry a
    reason generation can parse.  The pure-CONVERGENT map key is a
    driverless 'REC:…'; emitted verbatim it warned 'could not parse reason'
    and produced 0 candidates, silently unrouting ordinary top-level buses."""
    db = buda.BDB(":memory:")
    db.add_cell("leaf_cell", 60, 60)
    db.add_inst("L0", "leaf_cell", "", 0, 400)
    db.add_inst("SNK", "leaf_cell", "", 1100, 500)
    db.add_net_pins("x0", "L0.out", ["SNK.r0"])
    db.add_net_pins("x1", "L0.out", ["SNK.r1"])
    buda.BustermGen(db).derive(1)
    s = _session(db)
    _run(s, "run_hier_bundler CONVERGENT")
    bm = _by_nets(s)
    b = bm[frozenset({"x0", "x1"})]        # convergent group, one driver
    assert "DRV:" in b.reason, b.reason    # finest-shared sig, never bare REC:
    out = _run(s, "generate_hier_topologies")
    assert "could not parse reason" not in out
    assert len(s.bundles[0].input.candidates) > 0
    for c in ("run_planner hier", "run_nuts", "run_detailed_nuts"):
        _run(s, c)
    assert s.detailed_result.num_unplaced == 0


def test_hier_pure_bidirectional_return_pair_keeps_historical_emission():
    """Review #276 blocking 2: a pure-BIDIRECTIONAL request/response pair
    (pa→ps with ps→pa) is a multi-driver SAME-SET group and must keep the
    historical ep0 emission (one entry busterm) and the 2-pin candidate
    pool — not flip into npin fan-in generation with the root as its own
    leaf.  Only fan-in bundles and general-path (COMBINED/override)
    multi-driver groups take the all-drivers emission."""
    s = _session(_fanin_db(return_net=True))
    _run(s, "run_hier_bundler BIDIRECTIONAL")
    bm = _by_nets(s)
    b = bm[frozenset({"fa1_0", "fa1_1", "rt1_0"})]   # pa↔ps mixed-direction
    assert b.reason.startswith("BIDIR:")
    assert len(b.entry_busterm_ids) == 1             # historical ep0 path
    assert not list(b.net_drivers)
    out = _run(s, "generate_hier_topologies")
    assert "fan-in" not in out
    assert "could not parse reason" not in out
    for c in ("run_planner hier", "run_nuts", "run_detailed_nuts"):
        _run(s, c)
    assert s.detailed_result.num_unplaced == 0


def test_hier_fanin_root_never_its_own_leaf():
    """Case (a) root filter: a cell-local fan-in whose exits include one of
    the entries must not hand generation a self-destination (generate_npin
    doesn't dedupe — the root block would be instantiated twice)."""
    s = _session(_fanin_db(return_net=True))
    _run(s, "run_hier_bundler COMBINED")
    out = _run(s, "generate_hier_topologies")
    for line in out.splitlines():
        if "fan-in" not in line or "[" not in line:
            continue
        leaves = line.split("[", 1)[1].split("]", 1)[0].split(",")
        root = line.split("]→", 1)[1].split(" ", 1)[0]
        assert root not in leaves, line
        assert len(leaves) == len(set(leaves)), line


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


def test_hier_combined_keeps_bidir_grouping_for_cross_level_pairs():
    """Codex #276 finding 1: COMBINED must never be FINER than
    BIDIRECTIONAL — a cross-level return pair (deep pin ↔ shallow block,
    both directions) merges under BIDIRECTIONAL and must merge under
    COMBINED too (the direction-agnostic drv_spec trunk serves both)."""
    def _db():
        db = buda.BDB(":memory:")
        db.add_cell("proc_cell", 420, 200)
        db.add_cell("pipe_cell", 90, 80)
        db.add_inst_to_cell("proc_cell", "pa", "pipe_cell", 10, 60)
        db.add_inst("proc_i1", "proc_cell", "", 0, 0)
        db.add_cell("leaf_cell", 60, 60)
        db.add_inst("EXT", "leaf_cell", "", 800, 60)
        db.add_net_pins("out0", "proc_i1/pa.out", ["EXT.in"])   # cross-level
        db.add_net_pins("in0", "EXT.out", ["proc_i1/pa.rr"])    # its return
        buda.BustermGen(db).derive(1)
        return db
    for strat, merged in (("BIDIRECTIONAL", True), ("COMBINED", True),
                          ("CONVERGENT", False), ("STRICT", False)):
        s = _session(_db())
        _run(s, f"run_hier_bundler {strat}")
        sets = set(_by_nets(s))
        if merged:
            assert frozenset({"out0", "in0"}) in sets, (strat, sets)
        else:
            assert frozenset({"out0"}) in sets and frozenset({"in0"}) in sets


def test_hier_fanin_replica_taper_realigned_to_donor_net_order():
    """Codex #276 finding 2: a replica whose net names sort in the OPPOSITE
    driver order from the template's must still taper each bit along its
    own driver's branch — the expansion re-derives seg_bits from the donor
    metadata instead of reusing the template's indices."""
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 90, 80)
    db.add_inst_to_cell("proc_cell", "pa", "pipe_cell", 10, 60)
    db.add_inst_to_cell("proc_cell", "pb", "pipe_cell", 120, 60)
    db.add_inst_to_cell("proc_cell", "ps", "pipe_cell", 300, 60)
    db.add_inst("proc_i1", "proc_cell", "", 0, 0)
    db.add_inst("proc_i2", "proc_cell", "", 600, 300)   # y-offset: real Hanan grid
    # Template instance i1: sorted nets (aa1, zz1) -> drivers (pa, pb).
    db.add_net_pins("aa1", "proc_i1/pa.out", ["proc_i1/ps.r0"])
    db.add_net_pins("zz1", "proc_i1/pb.out", ["proc_i1/ps.r1"])
    # Replica instance i2: sorted nets (aa2, zz2) -> drivers (pb, pa) —
    # the OPPOSITE order.
    db.add_net_pins("aa2", "proc_i2/pb.out", ["proc_i2/ps.r1"])
    db.add_net_pins("zz2", "proc_i2/pa.out", ["proc_i2/ps.r0"])
    buda.BustermGen(db).derive(1)
    s = _session(db)
    for c in ("run_hier_bundler CONVERGENT", "generate_hier_topologies",
              "run_planner hier", "run_nuts", "run_detailed_nuts"):
        _run(s, c)
    assert s.detailed_result.num_unplaced == 0
    # Ground truth per expanded wrapper: re-derive membership from its own
    # donor metadata and compare with what the pipeline used.
    checked = 0
    for w in s.bundles:
        b = w.input.original_bundle
        if not b.reason.startswith("FANIN:"):
            continue
        assert list(b.net_drivers), b.reason      # donor metadata attached
        sel = w.plan.selected_topology_index
        t = w.input.candidates[sel]
        used = dict(t.seg_bits)
        expect = buda.Topology()
        expect.type = t.type
        expect.segments = list(t.segments)
        expect.seg_busterms = dict(t.seg_busterms)
        expect.seg_conns = dict(t.seg_conns)
        buda.derive_fanin_seg_bits(expect, s.fp, list(b.net_drivers),
                                   [list(r) for r in b.net_receivers])
        assert dict(expect.seg_bits) == used, (b.instances, used)
        # And concretely: any 1-bit branch's bit maps to the net whose
        # driver leaf matches — the bits differ between the two instances.
        checked += 1
    assert checked == 2
    per_inst_bits = {}
    for w in s.bundles:
        b = w.input.original_bundle
        if not b.reason.startswith("FANIN:"):
            continue
        t = w.input.candidates[w.plan.selected_topology_index]
        pa_bits = None
        for si, bits in dict(t.seg_bits).items():
            if len(bits) == 1:
                # A 1-bit branch: resolve its net and driver.
                net = b.get_net_names()[bits[0]]
                drv = list(b.net_drivers)[bits[0]]
                per_inst_bits[net] = drv.rsplit('/', 1)[-1]
    # aa1 rides pa's branch; aa2 (same sorted position!) rides pb's.
    if "aa1" in per_inst_bits and "aa2" in per_inst_bits:
        assert per_inst_bits["aa1"] != per_inst_bits["aa2"]


def test_hier_bundler_rejects_bad_strategy_and_accepts_new_ones():
    s = _session(_fanin_db())
    assert "Error" in _run(s, "run_hier_bundler SOMETHING")
    for strat in ("CONVERGENT", "COMBINED"):
        s2 = _session(_fanin_db())
        out = _run(s2, f"run_hier_bundler {strat}")
        assert "HierBundler:" in out and "Error" not in out
