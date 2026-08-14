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

"""The picture is a property of the DESIGN, not of the unit it is described in.

The detailed view's bit-wire width (`test_viz_detailed_wire_width.py`) was one
instance of a CLASS: a layout-unit number used where a screen-space one was
meant, or an absolute constant used where a design-proportional one was.  Both
are invisible at micron scale, because a layout unit is then about a point AND
about a micron, so the two systems coincide.  `set_import_scale dbu` separates
them by 2000x and every such site fails at once.

This is the class-level guard: build the SAME design at two unit scales and
compare every screen-space property the renderer will use.  A property that
differs is unit-dependent by definition, whatever the code looks like.

Two things learned building it, both worth keeping:

* the first cut silently DROPPED any property it could not reduce to a
  scalar -- which is exactly a LineCollection's per-segment linewidths, i.e.
  where the known bug lived.  It reported a clean bill against code that had
  the bug.  A sweep that quietly narrows what it examines produces evidence
  of absence, so `_prop` records an unreducible value rather than skipping it;
* an absolute constant and integer QUANTIZATION look alike at one scale and
  separate immediately over a ladder: a constant stays the same number of RAW
  units at every scale, while quantization shrinks.  The engine's remaining
  1-unit differences are the former in the correct sense -- an epsilon meaning
  "one grid step inside", not a physical distance -- so this test compares the
  VIEWER, whose nudges are all meant to be seen.
"""
import matplotlib
matplotlib.use("Agg")

import collections

import pytest

import buda
import buda_cli
import buda_viz

# Two full pipelines plus an explorer window; ~seconds, not milliseconds.
pytestmark = pytest.mark.mid

_FLOW = """\
def_layer 3 M3 H LOW 30
def_layer 4 M4 V LOW 30
def_layer 5 M5 H TOP 30
def_layer 6 M6 V TOP 30
corner_margin dx {m} dy {m}
set_min_stub_length {stub}
add_block A 0 0 {a} {a}
add_block B {b} 0 {c} {a}
add_block C 0 {b} {a} {c}
add_block D {b} {b} {c} {c}
add_keepout {k} {k} {k2} {k2} 3 4
add_bus bus[8] A.o B.i,C.i
add_bus alt[4] D.o A.i
def_track_pattern 3 0 POWER {w2} {w1} (SIGNAL {w1} {w1})x6 GROUND {w2} {w1}
def_track_pattern 4 0 POWER {w2} {w1} (SIGNAL {w1} {w1})x6 GROUND {w2} {w1}
def_track_pattern 5 0 POWER {w2} {w1} (SIGNAL {w1} {w1})x6 GROUND {w2} {w1}
def_track_pattern 6 0 POWER {w2} {w1} (SIGNAL {w1} {w1})x6 GROUND {w2} {w1}
run_bundler STRICT
generate_topologies
run_planner 2
run_nuts
run_detailed_nuts
"""


def _session(scale, tmp_path):
    s = int(scale)
    script = tmp_path / f"s{s}.buda"
    script.write_text(_FLOW.format(
        m=3 * s, stub=2 * s, a=100 * s, b=300 * s, c=400 * s,
        k=150 * s, k2=200 * s, w1=1.0 * s, w2=2.0 * s))
    sess = buda_cli.BudaSession()
    sess.no_viz = True
    sess.run_command(f"source {script}")
    return sess


def _viz(sess):
    v = buda_viz.BudaVisualizer(sess.fp, sess.bundles,
                                routing_grid=sess.routing_grid,
                                layer_stack=sess.layers)
    v.draw_blocks()                      # + keepouts (and their labels)
    v.draw_buses()                       # pre-NUTS lines: the de-overlap fan
    v.draw_nuts_tracks(sess.nuts_result)
    v.draw_detailed_tracks(sess.detailed_result, sess.routing_grid, sess.layers)
    v.draw_preroutes(sess.routing_grid, sess.layers)
    v._build_detailed_artists()
    v._build_rail_artists()
    v.fig.canvas.draw()
    return v


def _prop(out, key, val):
    """Record a screen-space value.  Never skip one it cannot reduce: see
    the module docstring -- dropping the unreducible ones is what made the
    first version of this sweep blind to the bug it was written for."""
    if val is None:
        return
    try:
        seq = [round(float(x), 4) for x in list(val)]
        out[key] = seq[0] if len(seq) == 1 else tuple(sorted(set(seq)))
    except TypeError:
        try:
            out[key] = round(float(val), 4)
        except (TypeError, ValueError):
            out[key] = f"<unreducible {type(val).__name__}>"


def _screen_props(v):
    out = collections.OrderedDict()
    for i, a in enumerate(v.ax.lines):
        _prop(out, f"line[{i}].lw", a.get_linewidth())
        _prop(out, f"line[{i}].ms", a.get_markersize())
        _prop(out, f"line[{i}].mew", a.get_markeredgewidth())
    for i, a in enumerate(v.ax.patches):
        _prop(out, f"patch[{i}].lw", a.get_linewidth())
    for i, a in enumerate(v.ax.collections):
        _prop(out, f"coll[{i}].lw", a.get_linewidth())
        if hasattr(a, "get_sizes"):
            _prop(out, f"coll[{i}].sizes", a.get_sizes())
    for i, a in enumerate(v.ax.texts):
        _prop(out, f"text[{i}].fontsize", a.get_fontsize())
    return out


def _label_props(v, scale):
    """Keepout-label positions, normalised.  A label sits at the zone rect
    plus the gap nudge and nothing else, so this isolates the nudge from
    engine geometry -- which is deliberately NOT scale-invariant to the last
    unit: BUDA's geometry is integer, and a 1-unit epsilon there means "one
    grid step", not one micron."""
    out = collections.OrderedDict()
    for i, a in enumerate(v.ax.texts):
        x, y = a.get_position()
        out[f"text[{i}].pos"] = (round(x / scale, 3), round(y / scale, 3))
    return out


def _fan_offset(v, sess, scale):
    """The de-overlap fan actually applied to the first bundle's first drawn
    bus segment: drawn coordinate minus the ENGINE's own, normalised.
    Measuring the difference cancels the engine's geometry entirely, so what
    is left is the viewer's nudge."""
    w = sess.bundles[0]
    topo = w.input.candidates[w.plan.selected_topology_index]
    seg = topo.segments[0]
    for a in v.ax.lines:
        xd, yd = a.get_xdata(), a.get_ydata()
        if len(xd) == 2 and abs(float(xd[1]) - float(xd[0]) -
                                (seg.end.x - seg.start.x)) < 1e-6:
            return round((float(xd[0]) - seg.start.x) / scale, 6)
    return None


@pytest.fixture(scope="module")
def swept(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("scale")
    out = {}
    for scale in (1, 2000):
        sess = _session(scale, tmp)
        v = _viz(sess)
        out[scale] = (_screen_props(v), _label_props(v, scale), v,
                      _fan_offset(v, sess, scale))
    return out


def test_the_instrument_sees_something_at_all(swept):
    """A sweep that finds nothing must first be shown capable of finding
    something: assert it collected a substantial, matched property set."""
    a, b = swept[1][0], swept[2000][0]
    assert len(a) > 80, f"only {len(a)} screen properties collected"
    assert set(a) == set(b), "the two scales produced different artist sets"
    assert any(k.startswith("coll[") for k in a), "no collections swept"
    assert any(k.startswith("text[") for k in a), "no texts swept"


def test_no_screen_space_property_depends_on_the_unit_scale(swept):
    """THE invariant.  Microns or DBU: same design, same picture."""
    a, b = swept[1][0], swept[2000][0]
    bad = {k: (a[k], b[k]) for k in a if a[k] != b[k]}
    assert not bad, (
        "screen-space properties changed with the unit scale — a layout-unit "
        f"number used where points were meant: {bad}")


def test_the_keepout_label_gap_is_design_proportional(swept):
    """The label gap was `r.y2 + 5` — five layout units, which is a readable
    gap at micron scale and about a nanometre under `set_import_scale dbu`,
    where the label lands on the box edge.  Every linewidth is identical
    either way, so only a DATA-space comparison catches it."""
    a, b = swept[1][1], swept[2000][1]
    assert a, "no labels were drawn — the keepout never made it into the design"
    bad = {k: (a[k], b[k]) for k in a if k in b and a[k] != b[k]}
    assert not bad, (
        "a label position changed with the unit scale — an absolute constant "
        f"where a design-proportional gap was meant: {bad}")


def test_the_de_overlap_fan_is_design_proportional(swept):
    """Coincident bundles are fanned apart so you can see there are several.
    The fan was an absolute 2.0 layout units: at DBU scale that is 1 nm and
    the buses draw exactly on top of each other."""
    f1, f2 = swept[1][3], swept[2000][3]
    assert f1 is not None and f2 is not None, "the fanned bus line was not found"
    assert f1 != 0, "the fan is zero — nothing is being separated"
    assert f1 == pytest.approx(f2, rel=1e-6), (
        f"the de-overlap fan changed with the unit scale: {f1} vs {f2}")


def test_the_nudge_scales_with_the_design(swept):
    """Directly, so the intent survives a refactor of the sweep above."""
    v1, v2 = swept[1][2], swept[2000][2]
    assert v2._nudge() == pytest.approx(v1._nudge() * 2000, rel=1e-6)
    # Non-zero on a degenerate design, so a nudge is never a no-op.
    assert v1._nudge() > 0
