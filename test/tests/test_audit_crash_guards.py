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
