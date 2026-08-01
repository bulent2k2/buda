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
The explorer draws a trunk long enough to reach every stub on it (issue #554).

The explorer does not render nominal geometry.  Each segment is drawn at the
CENTRE of its slide interval, and then every trunk endpoint is snapped to the
display position of the segment connecting there, so the two visually meet.

The snap matches a connection to an endpoint with a +/-1 tolerance, and it used
to ASSIGN per match — so when several partners matched one endpoint, whichever
came last won.  A trunk whose outermost tap sits within a unit of an interior
tap therefore had its end dragged inwards, past the very stub that defines it.

#554's repro is exactly that shape: upper and lower blocks offset by half a
phase put u1's tap at x=199 and l1's at x=200, one unit apart, at the trunk's
low end (and u3/l3 at 601/600 at the high end).  The trunk rendered as
[200,600] while the outer stubs displayed at 150 and 650 — 50 units past each
end, drawn detached from a trunk they are genuinely connected to.  NUTS and
DetailedNUTS route it fine; only the picture was wrong.
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")   # headless; no window

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import pytest    # noqa: E402
import buda      # noqa: E402
import buda_cli  # noqa: E402
import buda_viz  # noqa: E402

pytestmark = pytest.mark.mid


def _session():
    """#554's repro: three upper and three lower blocks, offset half a phase in
    x so an outer tap and an interior tap land a single unit apart."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    for cmd in (
        "def_layer 4 M4 H TOP 44.44",
        "def_layer 5 M5 V TOP 50.00",
        "corner_margin dx 1 dy 2",
        "add_block u1 100 500 200 700",
        "add_block l1 150 100 250 300",
        "add_block u2 300 500 500 700",
        "add_block l2 350 100 450 300",
        "add_block u3 600 500 700 700",
        "add_block l3 550 100 650 300",
        "add_bus bus1[8] u1 u2,u3,l1,l2,l3",
        "run_bundler",
        "generate_topologies",
    ):
        s.do_command(cmd)
    return s


def _trunk_candidate(s):
    """The TRUNK_H@y400 candidate — the spine in the channel between the two
    block rows, with all six stubs hanging off it."""
    w = s.bundles[0]
    for i, c in enumerate(w.input.candidates):
        if c.type == "TRUNK_H@y400":
            return i
    pytest.fail("TRUNK_H@y400 candidate not generated")


def _drawn_segments(exp):
    """The routed segments as (x0, y0, x1, y1), read off the explorer's axes.

    Reads what was actually rendered rather than recomputing it, so the test
    cannot pass against a broken renderer by replicating its arithmetic.

    Filtered to SOLID lines at zorder 10 — the segments themselves.  Taking
    every 2-point line also picks up the dashed slide-range bands and Hanan
    grid lines, and a slide band is WIDER than the trunk it belongs to: an
    earlier version of this helper selected one as "the trunk" and every
    assertion below passed vacuously, on broken and fixed code alike.
    """
    out = []
    for ln in exp.ax.get_lines():
        xs, ys = ln.get_xdata(), ln.get_ydata()
        if (len(xs) == 2 and len(ys) == 2
                and ln.get_linestyle() == "-" and ln.get_zorder() == 10):
            out.append((float(xs[0]), float(ys[0]), float(xs[1]), float(ys[1])))
    return out


def test_trunk_is_drawn_long_enough_to_reach_its_outer_stubs():
    """Every vertical stub's drawn x must lie within the drawn horizontal
    trunk's x extent — otherwise it renders as a detached wire.

    Asserted over the drawn artists, so it fails on the real symptom (a stub
    floating off the end) rather than on an internal variable.
    """
    import matplotlib.pyplot as plt
    s = _session()
    idx = _trunk_candidate(s)
    exp = buda_viz.TopologyExplorer(s.fp, s.bundles, layer_stack=s.layers)
    try:
        exp.idx = idx
        exp.fig_redraw()
        segs = _drawn_segments(exp)

        # The trunk is the widest horizontal drawn segment; stubs are vertical.
        horiz = [g for g in segs if abs(g[1] - g[3]) < 1e-6]
        vert = [g for g in segs if abs(g[0] - g[2]) < 1e-6]
        assert len(horiz) == 1, f"expected one trunk, drew {len(horiz)}"
        assert len(vert) == 6, f"expected the six stubs, drew {len(vert)}"

        trunk = horiz[0]
        t_lo, t_hi = sorted((trunk[0], trunk[2]))

        detached = [round(g[0], 1) for g in vert
                    if not (t_lo - 1e-6 <= g[0] <= t_hi + 1e-6)]
        assert not detached, (
            f"stub(s) drawn at x={detached} lie outside the trunk drawn over "
            f"x=[{t_lo:.1f},{t_hi:.1f}] — they render as disconnected wires"
        )
    finally:
        plt.close("all")


def test_endpoint_snap_takes_the_extreme_partner():
    """The mechanism, pinned directly: when several partners match one endpoint
    the snap must take the EXTREME one, not the last one visited.

    Guards the specific regression — an interior tap within the +/-1 match
    tolerance dragging the trunk's end inside the outermost tap.
    """
    import matplotlib.pyplot as plt
    s = _session()
    idx = _trunk_candidate(s)
    topo = s.bundles[0].input.candidates[idx]
    ct = buda.ConnTopology()
    ct.build(topo, s.fp)
    cs_list = list(ct.segs())

    trunk = cs_list[0]
    # Two distinct partners must match the low end within the tolerance for this
    # fixture to exercise the bug at all; assert that rather than assume it.
    lo_matches = [c for c in trunk.conns
                  if c.kind == buda.SegConnKind.SEG
                  and abs(c.at_pos - trunk.along_lo) <= 1]
    assert len(lo_matches) >= 2, (
        "fixture no longer produces two taps within the snap tolerance of the "
        f"trunk's low end — got {[c.at_pos for c in lo_matches]}"
    )

    exp = buda_viz.TopologyExplorer(s.fp, s.bundles, layer_stack=s.layers)
    try:
        exp.idx = idx
        exp.fig_redraw()
        segs = _drawn_segments(exp)
        horiz = [g for g in segs if abs(g[1] - g[3]) < 1e-6]
        assert len(horiz) == 1, f"expected one trunk, drew {len(horiz)}"
        trunk_drawn = horiz[0]
        t_lo, t_hi = sorted((trunk_drawn[0], trunk_drawn[2]))

        vert = [g for g in segs if abs(g[0] - g[2]) < 1e-6]
        xs = [g[0] for g in vert]
        assert t_lo <= min(xs) + 1e-6, (
            f"trunk low end {t_lo} is inside the leftmost stub at {min(xs)}"
        )
        assert t_hi >= max(xs) - 1e-6, (
            f"trunk high end {t_hi} is inside the rightmost stub at {max(xs)}"
        )
    finally:
        plt.close("all")
