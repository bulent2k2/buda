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

"""`set_keepout_loci stripes`: a thin+long strap keeps only its long-axis loci.

`openroad_pdn_recipe.md` §8.1 / `opens_interchange.md` item 15.  A real PDN
imports as thousands of die-crossing power straps, each blocked as a keepout.
Every keepout edge is a Hanan line and the grid is a PRODUCT of its two axis
sets, so each strap's two THIN-axis edges are fresh grid lines that multiply
without bound — flow/ariane133 + its PDN went ~6,327 -> ~3.2M cells and the
planner stalled.  `outside` (item 12) cannot help: a die-crossing strap is not
inside a block.

The rule is geometric — thin AND long (aspect >= 8), a strap not a block — and
OPT-IN, like `outside`, because it removes candidate positions.  A strap keeps
its LONG-axis edges (its ends, few, often already the die boundary) and drops
the proliferating thin-axis pair.  Blocking is never affected.
"""
import buda


def _fp_with(keepouts, stripes=True, outside_only=False):
    """Hanan grid of one block plus (x1,y1,x2,y2[,inside]) keepouts.

    `stripes` is the new `set_keepout_loci stripes` policy (default here since
    that is what these tests are about; the product default is False, pinned by
    `test_the_default_is_unchanged`).
    """
    fp = buda.Floorplan()
    fp.set_stripe_loci_suppress(stripes)
    fp.set_keepout_loci_outside_only(outside_only)
    fp.add_block("m", 100, 100, 200, 200)
    for k in keepouts:
        x1, y1, x2, y2 = k[:4]
        inside = k[4] if len(k) > 4 else False
        fp.add_keepout_zone(x1, y1, x2, y2, [3], inside)
    return fp.get_hanan_grid()


def test_a_vertical_strap_keeps_only_its_long_axis_loci():
    """Thin in x (100 wide, 2000 tall, aspect 20): the two x-edges vanish, the
    two y-edges (its ends) stay."""
    xs, ys = _fp_with([(500, -1000, 600, 1000)])
    assert xs == [100, 200], xs            # strap x-edges 500/600 dropped
    assert ys == [-1000, 100, 200, 1000]   # strap y-ends kept


def test_a_horizontal_strap_keeps_only_its_long_axis_loci():
    """The mirror: thin in y, so the y-edges go and the x-ends stay."""
    xs, ys = _fp_with([(-1000, 500, 1000, 600)])
    assert xs == [-1000, 100, 200, 1000]
    assert ys == [100, 200]


def test_the_same_strap_with_stripes_off_adds_all_four_edges():
    """The flag is what changes it, not the geometry.  This is also the
    default, so every existing flow is untouched."""
    xs, ys = _fp_with([(500, -1000, 600, 1000)], stripes=False)
    assert xs == [100, 200, 500, 600]
    assert ys == [-1000, 100, 200, 1000]


def test_a_block_shaped_keepout_is_not_a_strap():
    """Aspect 1.2 (600x500): not thin, so `stripes` leaves it fully alone."""
    xs, ys = _fp_with([(300, 300, 900, 800)])
    assert xs == [100, 200, 300, 900]
    assert ys == [100, 200, 300, 800]


def test_the_aspect_threshold_is_eight():
    """Exactly 8:1 is a strap (>=); a hair under is not — the boundary is
    deterministic."""
    at_8, _ = _fp_with([(500, 0, 600, 800)])         # w=100 h=800 -> aspect 8
    assert at_8 == [100, 200], at_8                  # strap: x-edges dropped
    under_8, _ = _fp_with([(500, 0, 600, 799)])      # aspect 7.99 -> not a strap
    assert under_8 == [100, 200, 500, 600], under_8  # kept


def test_straps_do_not_multiply_the_grid():
    """The failure mode in miniature: many die-spanning vertical straps.  With
    stripes off the grid grows as the PRODUCT (each strap adds 2 x-lines); on,
    the x-axis stays flat and only the shared ends survive on y."""
    straps = [(1000 * i, -1000, 1000 * i + 100, 1000) for i in range(1, 60)]
    xs, ys = _fp_with(straps)                         # stripes on
    assert xs == [100, 200], xs                       # no strap x-edge survives
    assert ys == [-1000, 100, 200, 1000]              # all straps share ends
    xs2, ys2 = _fp_with(straps, stripes=False)
    assert len(xs2) * len(ys2) > 20 * len(xs) * len(ys)


def test_stripes_composes_with_outside():
    """Orthogonal knobs.  `outside` drops an inside-a-block keepout entirely;
    `stripes` drops a non-inside strap's thin axis.  Both at once handles a
    design that has both (a PDN over macros with OBS)."""
    xs, ys = _fp_with(
        [(120, 130, 160, 170, True),   # inside block -> outside drops it whole
         (500, -1000, 600, 1000)],     # strap outside -> stripes drops x-edges
        stripes=True, outside_only=True)
    assert xs == [100, 200], xs
    assert ys == [-1000, 100, 200, 1000]


def test_the_default_is_unchanged():
    """OPT-IN: a fresh Floorplan does not suppress, and a strap adds all its
    loci until asked.  Byte-identity for every existing flow rests on this."""
    assert buda.Floorplan().stripe_loci_suppress() is False
    xs, ys = _fp_with([(500, -1000, 600, 1000)], stripes=False)
    assert xs == [100, 200, 500, 600]


def test_a_strap_still_blocks():
    """The whole point: this changes the GRID, never what is routable.  A
    strap-shaped keepout on layer 3 must still be seen as blocking."""
    fp = buda.Floorplan()
    fp.set_stripe_loci_suppress(True)
    fp.add_block("m", 100, 100, 200, 200)
    fp.add_keepout_zone(500, -1000, 600, 1000, [3], False)
    zones = fp.get_keepout_zones()
    assert len(zones) == 1
    assert 3 in zones[0].layer_ids
    assert fp.low_layer_keepouts([3]), "a strap keepout stopped blocking"
