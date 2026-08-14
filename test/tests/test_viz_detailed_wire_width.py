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

"""A bit-wire's drawn thickness is a POINTS value; its track width is not.

`_build_detailed_artists` used to bake `linewidths = max(0.6, ns.width*0.6)`
-- a matplotlib point-width computed straight from a layout-unit number.
That is only sane while one layout unit happens to be about one point, which
is true at micron scale and is why the picture looked right for years.

`flow/ariane133` declares `set_import_scale dbu`, so a layout unit is 1/2000
um and metal9's 1.6 um wire is 3200 units: the view asked for a 1920-POINT
line, about 27 inches on a 14-inch figure.  Each wire was drawn with its
correct SPAN and a thickness spanning the whole canvas, so the detailed view
of one bundle was two enormous crossed bands -- while the topology explorer,
which uses its own fixed point-widths, drew the same route perfectly.

The width now travels as the physical number it is and the existing zoom
sync (`_sync_nuts_linewidths`, which the abstract NUTS lines have always
used) converts it to points at the current zoom.

The invariant these tests pin is SCALE INDEPENDENCE: the same design
described in different units must draw the same picture.  That is the
property the old code lacked, and it is exact -- no wall-clock, no pixels.
"""
import matplotlib
matplotlib.use("Agg")

import pytest

import buda
import buda_viz
from viz_main.draw_detailed import _DETAILED_LW_FLOOR


def _viz(scale):
    """One H and one V bit-wire on a square design, described at `scale`
    layout units per micron.  scale=1 is the historical micron flow;
    scale=2000 is what `set_import_scale dbu` produces."""
    fp = buda.Floorplan()
    fp.add_block("a", 0, 0, int(100 * scale), int(100 * scale))

    layers = buda.LayerStack()
    layers.add_layer(9, "M9", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    layers.add_layer(10, "M10", buda.LayerDir.VERTICAL, buda.LayerType.TOP)

    res = buda.DetailedNUTSResult()
    segs = []
    for lid, width_um in ((9, 1.6), (10, 1.68)):
        ns = buda.NetSegment()
        ns.bundle_id = 1
        ns.seg_idx = 0 if lid == 9 else 1
        ns.bit_index = 0
        ns.layer = lid
        ns.width = width_um * scale
        ns.track_position = 50 * scale
        ns.span_lo = 10 * scale
        ns.span_hi = 90 * scale
        segs.append(ns)
    res.net_segments = segs

    v = buda_viz.BudaVisualizer(fp, [], layer_stack=layers)
    v.draw_blocks()
    v.draw_detailed_tracks(res, None, layers)
    v._build_detailed_artists()
    v.fig.canvas.draw()          # runs the zoom sync, as an open window does
    return v


def _linewidths(v, bid=1):
    out = []
    for e in v._detailed_bundle_artists.get(bid, []):
        if e.get('phys_w') is None:
            continue             # via markers carry no wire width
        lw = e['artist'].get_linewidth()
        out.append(float(lw[0] if hasattr(lw, "__len__") and len(lw) else lw))
    return out


def test_a_dbu_scale_wire_is_not_drawn_a_thousand_points_wide():
    """The reported bug, at its own scale.  A 3200-unit wire must not ask for
    a ~1920-point line -- the figure is about 14 inches, i.e. ~1000 points
    across, so anything near that covers the canvas."""
    lws = _linewidths(_viz(2000))
    assert lws, "no bit-wire artists were registered"
    assert max(lws) < 50.0, f"bit-wire drawn {max(lws):.0f}pt wide: {lws}"


def test_the_same_design_draws_the_same_at_any_unit_scale():
    """THE invariant.  Microns, DBU, or anything else: the picture is a
    property of the design, not of the number used to describe it."""
    a = _linewidths(_viz(1))
    b = _linewidths(_viz(2000))
    assert len(a) == len(b) == 2
    for wa, wb in zip(a, b):
        assert wa == pytest.approx(wb, rel=0.02), \
            f"unit scale changed the drawn width: {a} vs {b}"


def test_a_wire_stays_visible_when_it_is_far_below_a_pixel():
    """A real track on a full-design view is a small fraction of one pixel.
    Physical truth alone would draw nothing at all, so the floor is what
    keeps the route on screen -- the detailed view's job at that zoom is
    'where did the bits go', not 'how wide are they'."""
    lws = _linewidths(_viz(2000))
    assert len(lws) == 2, f"expected both bit-wires to carry a width: {lws}"
    for lw in lws:
        assert lw >= _DETAILED_LW_FLOOR


def test_zooming_in_grows_the_wire_to_its_true_width():
    """The other half of being physical: zoomed in, a bit-wire has to keep
    matching the [Tracks] rails under it, which are drawn in data
    coordinates.  This is what a baked point-width could never do."""
    v = _viz(2000)
    home = _linewidths(v)
    x0, x1 = v.ax.get_xlim()
    y0, y1 = v.ax.get_ylim()
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    sx, sy = (x1 - x0) / 400, (y1 - y0) / 400
    v.ax.set_xlim(cx - sx, cx + sx)
    v.ax.set_ylim(cy - sy, cy + sy)
    v.fig.canvas.draw()
    zoomed = _linewidths(v)
    assert len(home) == len(zoomed) == 2, f"{home} / {zoomed}"
    for h, z in zip(home, zoomed):
        assert z > h * 10, f"width did not track the zoom: {home} -> {zoomed}"


def test_the_abstract_view_keeps_its_own_floor():
    """The two views want different floors -- an abstract bus line is one
    line per bundle and can afford 2.5pt, a detailed view draws every BIT
    and a thousand of them at 2.5pt is a smear.  Pinned so a later tidy-up
    does not collapse them into one number."""
    assert _DETAILED_LW_FLOOR < buda_viz.BudaVisualizer._LW_MIN_PTS
