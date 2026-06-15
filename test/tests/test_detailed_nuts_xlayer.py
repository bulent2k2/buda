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

"""
Detailed-NUTS cross-trunk-layer spacing.

A cross-layer corner overlap (two M5 stubs hanging off trunks on different
layers, M4 and M6) is resolved abstractly with a g=1 split.  But when the two
trunk layers have an aligned signal track, detailed NUTS used to snap both
trunks to the SAME coordinate, so the stubs met end-to-end on M5 — a bit-level
short that detailed NUTS does not report (it only counts unplaced bits).

The fix carries the abstract per-trunk split bound into detailed NUTS and filters
each trunk's signal tracks to its committed side, so the trunks land on different
real tracks and the stubs no longer meet.  This test checks the detailed
NetSegments directly: no two different-net segments on the same layer share a
track while overlapping in span.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402  (adds build/ to sys.path and imports the buda module)

FLOW = os.path.join(os.path.dirname(__file__), '..', '..', 'flow', 'nuts_corner_touch.buda')


def _detailed_shorts_and_unplaced(flow_path):
    """Run a flow in-process; return (#stub shorts, #unplaced bits)."""
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    sess.do_command(f"source {os.path.abspath(flow_path)}")
    ns = sess.detailed_result.net_segments
    shorts = 0
    for i in range(len(ns)):
        a = ns[i]
        for j in range(i + 1, len(ns)):
            b = ns[j]
            if a.layer != b.layer or a.bundle_id == b.bundle_id:
                continue
            same_track = abs(a.track_position - b.track_position) < 1e-6
            # Same track + different net: the span axis is CLOSED, so an
            # end-to-end touch (a.span_hi == b.span_lo) counts as a short — that
            # collinear bit-end butt is exactly the cross-layer failure mode here.
            span_touch = a.span_lo <= b.span_hi and b.span_lo <= a.span_hi
            if same_track and span_touch:
                shorts += 1
    return shorts, sess.detailed_result.num_unplaced


def test_cross_layer_corner_no_detailed_short():
    # nuts_corner_touch pins bundle 1's trunk to M4 and bundle 2's to M6 over
    # aligned grids — the cross-layer corner case.  With the carry-bound fix the
    # trunks snap to disjoint signal tracks, so the M5 stubs do not short.
    shorts, unplaced = _detailed_shorts_and_unplaced(FLOW)
    assert shorts == 0, f"detailed-NUTS stub shorts on M5: {shorts} (expected 0)"
    assert unplaced == 0, f"unplaced bits: {unplaced} (expected 0)"
