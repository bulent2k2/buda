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

"""Regression tests for the follow-up audit fixes C9-04 and P1-03."""

import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import buda


def _pass_through_scene(seg_bits):
    """A single horizontal segment crossing three blocks as a pass-through,
    with only bit 0 placed. `seg_bits` sets the segment's taper membership."""
    fp = buda.Floorplan()
    fp.add_block("S", 0, 0, 100, 100)
    fp.add_block("X", 200, 40, 260, 60)
    fp.add_block("D", 400, 0, 500, 100)

    topo = buda.Topology()
    topo.type = "TRUNK_H"
    s = buda.Segment()
    s.start = buda.Point(100, 50)
    s.end = buda.Point(400, 50)
    s.layer_hint = 4
    topo.segments = [s]
    topo.connected_block_names = ["S", "X", "D"]
    if seg_bits is not None:
        topo.seg_bits = seg_bits

    ct = buda.ConnTopology()
    ct.build(topo, fp)

    d = buda.DetailedNUTSResult()
    n0 = buda.NetSegment()
    n0.bundle_id = 1
    n0.seg_idx = 0
    n0.bit_index = 0
    n0.track_position = 50
    n0.width = 1
    n0.layer = 4
    n0.span_lo = 100
    n0.span_hi = 400
    d.net_segments = [n0]

    layers = buda.LayerStack()
    layers.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    return buda.check_dnuts(ct, d, topo, fp, layers, 1, 2)


def _busterm_opens(res):
    return [(v.block_name, v.bit_index) for v in res.violations
            if str(v.kind).endswith("BUSTERM_OPEN")]


# ──────────────────────────────────────────────────────────────────────────
# C9-04 — check_dnuts per-bit pass-through coverage honors the fan-in taper
# ──────────────────────────────────────────────────────────────────────────

def test_check_dnuts_passthrough_taper_no_spurious_open():
    # Segment 0 carries only bit 0 (taper); bit 1 legitimately has no wire.
    # Pre-fix every pass-through block reported a spurious BUSTERM_OPEN for
    # bit 1 (3 total). Post-fix: none.
    res = _pass_through_scene(seg_bits={0: [0]})
    assert _busterm_opens(res) == [], res.violations and \
        [(v.block_name, v.bit_index, v.message) for v in res.violations]


def test_check_dnuts_passthrough_no_taper_still_flags_open():
    # Control: with NO taper (every segment carries all bits), a genuinely
    # missing bit-1 wire must STILL be flagged — the fix must not blind the
    # real-open detection.
    res = _pass_through_scene(seg_bits=None)
    opens = _busterm_opens(res)
    assert ("X", 1) in opens, opens


# ──────────────────────────────────────────────────────────────────────────
# P1-03 — bottom-up group solve fails LOUD on a divergent cell-local frame
# ──────────────────────────────────────────────────────────────────────────

import pytest  # noqa: E402
import buda_cli  # noqa: E402


def _bu_wrapper(bid, inst0):
    w = buda.BundleWrapper()
    b = w.input.original_bundle
    b.id = bid
    b.instances = [inst0]
    return w


def _bu_session(orients):
    s = buda_cli.BudaSession()
    s.no_viz = True
    s._bu_cell_of = lambda c: "core"
    s._detect_instance_orients = \
        lambda cell, comps, ref_name=None: dict(orients)
    return s


def test_bottom_up_frame_divergence_raises():
    # Two templates of one cell whose reference instances have DIFFERENT
    # orientations (N vs FN mirror) generated their candidates in different
    # cell-local frames — solving them together would double-transform the
    # copies. The guard must fail LOUD instead of emitting misplaced routing.
    s = _bu_session({"top/i1": "N", "top/i2": "FN"})
    wrappers = [_bu_wrapper(1, "top/i1"), _bu_wrapper(2, "top/i2")]
    with pytest.raises(RuntimeError, match="P1-03"):
        s._check_bottom_up_frame(wrappers, None, "core")


def test_bottom_up_frame_same_orientation_ok():
    # Same orientation, different instance → the origin-normalized cell-local
    # floorplan is identical, so the group solve is valid: no raise.
    s = _bu_session({"top/i1": "N", "top/i2": "N"})
    wrappers = [_bu_wrapper(1, "top/i1"), _bu_wrapper(2, "top/i2")]
    s._check_bottom_up_frame(wrappers, None, "core")   # must not raise


# ──────────────────────────────────────────────────────────────────────────
# C6-09 — an FK-rejected persist row THROWS instead of silently dropping
# ──────────────────────────────────────────────────────────────────────────

import buda_db  # noqa: E402


def test_persist_step_throws_on_fk_violation(tmp_path):
    db = buda_db.BDB(str(tmp_path / "fk.bdb"))
    r = buda_db.BusSegRow()
    r.id = "999"        # bundle_id: no such bundle row → FK violation
    r.seg_idx = 0
    r.layer = 4
    # Pre-fix the failed step was ignored and the row silently vanished;
    # now it raises (loud) so the checkpoint aborts instead of writing a
    # route_snapshot over an incomplete row set.
    with pytest.raises(RuntimeError, match="add_bus_segment"):
        db.add_bus_segment(r)
    # With the parent bundle present the same insert succeeds.
    br = buda_db.BundleRow()
    br.id = "999"
    db.add_bundle(br)
    db.add_bus_segment(r)   # must not raise
