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

"""Crash-guard regressions from the 2026-07 whole-codebase audit.

Each test drives a formerly-unguarded entry point with the degenerate input
that used to be undefined behavior (empty-vector indexing, size_t underflow)
and asserts the fail-LOUD behavior that replaced it.
"""
import pytest

import buda


def test_add_block_rects_rejects_empty_list():
    # Audit C4-04: Rect u = norm_rects[0] on an empty input was UB.
    fp = buda.Floorplan()
    with pytest.raises(Exception, match="must not be empty"):
        fp.add_block_rects("blk", [])


def test_add_net_pins_empty_path_endpoint_does_not_underflow():
    # Audit C6-02: an endpoint like ".p" (empty instance path) made the
    # ancestor loop start at size_t(-1) and index an empty vector. It must
    # now complete without crashing (the leaf pin insert itself is handled
    # by _add_pin_by_path; there are simply no ancestors to add).
    db = buda.BDB(":memory:")
    nid = db.add_net_pins("n1", ".p", ["a/b.q"])
    assert isinstance(nid, int)
    # Same guard on the undirected and inout variants (identical loops).
    nid2 = db.add_net_pins_undirected("n2", [".x", "c.y"])
    assert isinstance(nid2, int)


def test_reference_holding_ctors_keep_parent_alive():
    # Audit C7-01/02/03: BustermGen(BDB&), HierarchicalBundler(BDB&) and
    # TopologyGenerator(const Floorplan&) store C++ references but had no
    # py::keep_alive — dropping the Python parent left the child dangling
    # (a temporary parent dangled IMMEDIATELY). With keep_alive the
    # temporary-parent pattern is safe by construction.
    import gc
    import buda_db

    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 300, 0, 400, 100)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    del fp
    gc.collect()
    cands = g.generate_candidates("A", ["B"])   # dangled pre-fix
    assert cands, "generator must still see the kept-alive floorplan"

    db = buda.BDB(":memory:")
    db.add_cell("c", 10, 10)
    gen = buda_db.BustermGen(db)
    del db
    gc.collect()
    gen.derive(0)                               # dangled pre-fix

    db2 = buda.BDB(":memory:")
    hb = buda.HierarchicalBundler(db2)
    del db2
    gc.collect()
    hb.run(1)                                   # dangled pre-fix


def _bs(bundle_id, seg_idx, span, interval=(0.0, 28.0), bits=2):
    s = buda.BusSegment()
    s.bundle_id, s.seg_idx, s.layer = bundle_id, seg_idx, 4
    s.span_lo, s.span_hi = span
    s.interval_lo, s.interval_hi = interval
    s.bit_width = bits
    s.bit_order = "LO_HI"
    return s


def _dnuts_stack(layer=4):
    stack = buda.RoutingGridStack()
    slots = [buda.TrackSlot("POWER", "VDD", 2.0, 1.0)]
    for _ in range(4):
        slots.append(buda.TrackSlot("SIGNAL", "sig", 1.0, 1.0))
    stack.define_layer(layer, buda.TrackPattern(origin=0.0, slots=slots), True)
    return stack


def test_cross_bundle_reservation_sees_inverted_span():
    # Audit C11-03: the cross-bundle track-reservation overlap test used raw
    # span order while the same-bundle branch directly above normalizes with
    # min/max. A bus segment whose span is inverted (span_lo > span_hi — the
    # documented placement-swap state) but physically overlapping another
    # bundle's segment read as disjoint, its tracks were not reserved, and
    # the two bundles landed different nets on the same track: a silent
    # cross-bundle short. Both bundles overlap on [40, 60] here; bundle 2's
    # span is stored inverted.
    a = _bs(1, 0, (0.0, 60.0))
    b = _bs(2, 0, (100.0, 40.0))       # inverted; real extent 40..100
    r = buda.DetailedNUTSEngine(_dnuts_stack()).run([a, b])
    assert r.num_unplaced == 0
    t1 = {ns.track_position for ns in r.net_segments if ns.bundle_id == 1}
    t2 = {ns.track_position for ns in r.net_segments if ns.bundle_id == 2}
    assert not (t1 & t2), \
        f"different bundles share tracks {t1 & t2} — reservation missed"


def test_def_nets_escaped_bracket_connections_survive(tmp_path):
    # Audit C6-01: the COMPONENTS section stores normalized names (escapes
    # stripped: mem\[0\] -> mem[0]) but the NETS connection loop looked up
    # the RAW token, so every connection to an escaped-name instance missed
    # get_comp_id and was SILENTLY dropped — nets on bus-bit-named macros
    # (the standard DEF escaping) lost their pins.
    import buda_db
    lef = tmp_path / "t.lef"
    lef.write_text(
        "MACRO ram\n  SIZE 10 BY 10 ;\n  PIN d\n    DIRECTION INPUT ;\n"
        "    PORT\n      RECT 1 1 2 2 ;\n    END\n  END d\nEND ram\n")
    deff = tmp_path / "t.def"
    deff.write_text(
        "VERSION 5.8 ;\nUNITS DISTANCE MICRONS 1000 ;\n"
        "DIEAREA ( 0 0 ) ( 100000 100000 ) ;\nCOMPONENTS 2 ;\n"
        "- mem\\[0\\] ram + PLACED ( 1000 1000 ) N ;\n"
        "- plain ram + PLACED ( 50000 50000 ) N ;\nEND COMPONENTS\n"
        "NETS 1 ;\n- n1 ( mem\\[0\\] d ) ( plain d ) ;\nEND NETS\nEND DESIGN\n")
    db = buda_db.BDB(str(tmp_path / "t.bdb"))
    db.import_def_lef(str(deff), str(lef))
    comps = {c.name: c.id for c in db.all_components()}
    assert "mem[0]" in comps
    pins_by_comp = {}
    for c in db.all_components():
        pins_by_comp[c.name] = len(db.pins_by_comp(c.id))
    assert pins_by_comp["plain"] == 1
    assert pins_by_comp["mem[0]"] == 1, \
        "escaped-name instance lost its net connection"


def test_candidates_element_access_returns_owned_copies():
    # Audit C7-04 (found by the suite's own intermittent segfault): element
    # access on w.input.candidates handed back registered NON-OWNING views
    # into the live vector (def_readwrite getter = reference_internal, the
    # policy the stl caster propagates to elements). A view held across any
    # pool reassignment dangled — and worse, its stale registry entry at
    # the freed address made a LATER cast of a fresh temporary Topology at
    # a recycled address return the stale object instead of the new value:
    # topo_uid then read freed memory (the ~30% fast-tier crash, pystack'd
    # to _merge_more_candidates). The getter must return owned copies.
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 300, 0, 400, 100)
    tg = buda.TopologyGenerator(fp)
    tg.set_layer_ids(4, 5)
    w = buda.BundleWrapper()
    w.input.candidates = tg.generate_candidates("A", ["B"])
    a = w.input.candidates
    b = w.input.candidates
    assert a[0] is not b[0], \
        "element access must yield owned copies, not registered views"
    # The lifetime guarantee the copy provides: elements held across a pool
    # reassignment stay independently valid (pre-fix: dangling views).
    held = list(w.input.candidates)
    uid_before = buda.topo_uid(held[0])
    w.input.candidates = []
    assert buda.topo_uid(held[0]) == uid_before
    # Same guarantee for the dogleg topology map (map<int, Topology> member
    # on NUTSResult — the same stl-caster view semantics applied).
    r = buda.NUTSResult()
    r.dogleg_topologies = {7: held[0]}
    d1 = r.dogleg_topologies
    d2 = r.dogleg_topologies
    assert d1[7] is not d2[7]
    r.dogleg_topologies = {}
    assert buda.topo_uid(d1[7]) == uid_before

def test_hier_bundler_multi_driven_net_attaches_all_driver_blocks():
    # Audit C10-02: a multiply-driven net (two or more OUTPUT pins) silently
    # lost every OUTPUT driver after the first — the extra drivers were
    # neither drivers nor receivers, so their blocks never attached to the
    # bundle's route: a guaranteed open no check could see. Extra same-depth
    # OUTPUT drivers must attach as additional endpoints (the INOUT
    # secondary-driver treatment), loudly.
    import contextlib
    import io

    import buda_cli
    db = buda.BDB(":memory:")
    db.add_cell("cell", 80, 80)
    for name, x in (("A", 0), ("B", 300), ("C", 600)):
        db.add_inst(name, "cell", "", x, 0)
    for i in range(2):
        db.add_net_pins(f"md_{i}", "A.o", [f"C.i{i}"])
        db.add_net_pins(f"md_{i}", "B.o2", [])       # second OUTPUT driver
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = db
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_hier_bundler")
    assert s.bundles, "expected at least one hier bundle"
    b = s.bundles[0].input.original_bundle
    drv, _, rcv = b.reason.partition("|REC:")
    endpoints = {drv.removeprefix("DRV:")} | \
        {r for r in rcv.split(",") if r}
    assert "B" in endpoints, \
        f"second OUTPUT driver's block must be attached ({b.reason})"
    assert {"A", "C"} <= endpoints, b.reason
    assert b.num_terminals == 3


def test_resize_cell_swaps_dims_for_rotated_instances(tmp_path):
    # Audit C6-03: resize_cell wrote x1+w / y1+h to EVERY instance without
    # consulting component.orient, so a 90/270-rotated instance (E/W/FE/FW)
    # got un-swapped dimensions — breaking the bbox-orient invariant the
    # v13 GDS round-trip depends on (export writes orient + bbox-derived
    # placement; a mismatch corrupts the re-imported layout).
    import buda_db
    db = buda_db.BDB(str(tmp_path / "rot.bdb"))
    db.add_cell("alu", 10.0, 20.0)
    db.add_inst("u0", "alu", "", 0.0, 0.0)      # upright: 10x20
    db.add_inst("u1", "alu", "", 100.0, 0.0)
    db.rotate_comp("u1", 90)                     # rotated: bbox 20x10
    db.resize_cell("alu", 12.0, 24.0)
    comps = {c.name: c for c in db.all_components()}
    u0, u1 = comps["u0"], comps["u1"]
    assert (u0.x2 - u0.x1, u0.y2 - u0.y1) == (12.0, 24.0)
    assert (u1.x2 - u1.x1, u1.y2 - u1.y1) == (24.0, 12.0), \
        "rotated instance must get swapped dims on resize"


def test_bitrunk_h_refuses_x_aligned_column():
    # Audit C4-02: the always-on legacy BITRUNK_H generator guarded the
    # degenerate y case (y_t1 == y_t2) but not x_min == x_max — a perfectly
    # x-aligned column of >= 4 blocks emitted a candidate whose two H
    # "trunks" were zero-length points, violating the zero-length-wire
    # invariant (no conn-segs to pin it, no bus, unplaceable by NUTS).
    fp = buda.Floorplan()
    for i in range(4):
        fp.add_block(f"b{i}", 0, i * 300, 100, i * 300 + 100)
    tg = buda.TopologyGenerator(fp)
    tg.set_layer_ids(4, 5)
    cands = tg.generate_candidates("b0", ["b1", "b2", "b3"])
    assert cands
    for c in cands:
        for s in c.segments:
            assert (s.start.x, s.start.y) != (s.end.x, s.end.y), \
                f"zero-length segment emitted in {c.type}"
