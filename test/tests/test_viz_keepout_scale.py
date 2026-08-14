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

"""Keepout drawing must not scale its ARTIST count with the zone count.

The window for `flow/ariane133` took ~30 s to appear and ~28 s again on
every expose — a focus switch, a resize — which reads as a hang.  The cause
was not the routing: `draw_keepouts` created one hatched `Rectangle` AND one
text label per zone, and that design imports 13,034 of them because one
`fakeram45_256x16` carries 99 `OBS` rects.  matplotlib re-renders every
artist on every draw, so the cost was paid again on each one.

Measured per redraw, before: 20.5 s in the texts, 8.2 s in the patches,
0.25 s for everything else; and once the patches were collected, 4.9 s of
what remained was the HATCH alone.

Three caps, each where the thing stops being *information*:
  * the rectangles become ONE PatchCollection — always, no threshold;
  * labels are dropped past `_KOZ_LABEL_MAX` (they overlap into a band);
  * the hatch becomes a translucent fill past `_KOZ_HATCH_MAX` (at a few
    pixels per zone the diagonals cannot resolve anyway).

This test guards the ARTIST COUNT rather than a wall-clock time, which
would be flaky and machine-dependent.  Artist count is the thing that was
actually wrong, and it is exact.
"""
import matplotlib
matplotlib.use("Agg")

import pytest

import buda
import buda_viz
from viz_main.draw_abstract import _KOZ_HATCH_MAX, _KOZ_LABEL_MAX


def _viz_with(n_zones):
    fp = buda.Floorplan()
    fp.add_block("b", 0, 0, 1000, 1000)
    for i in range(n_zones):
        x = (i % 100) * 10
        y = (i // 100) * 10
        fp.add_keepout_zone(x, y, x + 4, y + 4, [3])
    v = buda_viz.BudaVisualizer(fp, [])
    v.draw_blocks()          # calls draw_keepouts()
    return v


def test_one_collection_not_one_patch_per_zone():
    """The rectangles are collected unconditionally — 5,000 zones must not
    mean 5,000 artists for the renderer to walk."""
    v = _viz_with(5000)
    assert len(v.ax.patches) <= 8, len(v.ax.patches)   # blocks only
    hatched_or_koz = [c for c in v.ax.collections]
    assert hatched_or_koz, "keepouts vanished entirely"
    # One artist carries every zone.
    assert sum(len(c.get_paths()) for c in v.ax.collections) >= 5000


def test_labels_stop_at_the_cap():
    """Past the cap the labels are dropped; below it they are all present.

    They were the single largest redraw cost (20.5 s of 27 s) and are the
    first thing to become unreadable, so this is where they stop."""
    under = _viz_with(_KOZ_LABEL_MAX)
    assert len(under.ax.texts) >= _KOZ_LABEL_MAX

    over = _viz_with(_KOZ_LABEL_MAX + 1)
    koz_labels = [t for t in over.ax.texts if t.get_text().startswith("KOZ:")]
    assert not koz_labels, f"{len(koz_labels)} labels survived the cap"


def test_the_hatch_stops_at_its_own_higher_cap():
    """A hatch stays legible longer than a text label, so it has its own cap
    — but it is still 4.9 s of a 5.1 s redraw at 13k zones, so it does stop.
    Below the cap the hatch is kept, which is what a small design sees."""
    small = _viz_with(50)
    assert any(c.get_hatch() for c in small.ax.collections), \
        "a small design lost its hatch"

    big = _viz_with(_KOZ_HATCH_MAX + 1)
    assert not any(c.get_hatch() for c in big.ax.collections), \
        "the hatch survived past its cap"
    # …and the zones are still drawn, now as a translucent fill: dropping the
    # hatch must not drop the shape.
    assert sum(len(c.get_paths()) for c in big.ax.collections) > _KOZ_HATCH_MAX


def test_a_small_design_is_untouched():
    """The whole point of the caps is that they engage only where the old
    behaviour had stopped working.  A handful of hand-declared keepouts keeps
    its hatched rectangles and its per-zone labels exactly as before."""
    v = _viz_with(3)
    assert any(c.get_hatch() for c in v.ax.collections)
    assert len([t for t in v.ax.texts if t.get_text().startswith("KOZ:")]) == 3


def test_the_toggle_still_hides_them():
    """Every consumer of `_keepout_artists` only calls set_visible(), which a
    Collection supports — so collapsing N patches into one must not break the
    [Keepouts] button or the view reset."""
    v = _viz_with(_KOZ_HATCH_MAX + 1)
    assert v._keepout_artists, "nothing registered to toggle"
    for a in v._keepout_artists:
        a.set_visible(False)
    assert not any(a.get_visible() for a in v._keepout_artists)
    for a in v._keepout_artists:
        a.set_visible(True)
    assert all(a.get_visible() for a in v._keepout_artists)
