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

"""`set_keepout_loci outside`: an interior keepout blocks, but adds no loci.

`opens_interchange.md` item 12.  Phase 3c imports macro `OBS` as keepouts,
which is right — that metal is occupied.  What is unbounded is the GRID:
every keepout edge is a Hanan line, and the grid is a PRODUCT of its two
axis sets, so a technology that draws obstruction finely buys quadratic grid.
On `demo/ariane` that is 2,479 -> 2,508,972 cells and a planner that does not
finish in 50 minutes.

The policy is geometric rather than by provenance — "inside a block", not
"came from OBS" — and it is OPT-IN.  Both halves are corrections to how this
first shipped, and both are pinned below: it went in unconditionally, argued
as free on the grounds that a position inside a block is unreachable anyway.
That is FALSE.  A trunk may cross a block over-the-cell, so an interior
locus is a real candidate position, and dropping it is a trade rather than a
tidy-up.  `flow/rv/soc_conv_div` proved it — see
`test_the_default_is_unchanged`.

A keepout that pokes OUT (a halo, a blockage in open space) always
contributes, in either mode.
"""
import buda


def _fp_with(keepouts, outside_only=True):
    """The Hanan grid of one block plus (x1,y1,x2,y2,inside_block) keepouts.

    `outside_only` is the `set_keepout_loci outside` policy.  It defaults to
    True here because that is what these tests are about; the DEFAULT in the
    product is the opposite, and `test_the_default_is_unchanged` pins it.
    """
    fp = buda.Floorplan()
    fp.set_keepout_loci_outside_only(outside_only)
    fp.add_block("m", 100, 100, 200, 200)
    for x1, y1, x2, y2, inside in keepouts:
        fp.add_keepout_zone(x1, y1, x2, y2, [3], inside)
    return fp.get_hanan_grid()


def test_a_block_alone_gives_its_own_four_edges():
    xs, ys = _fp_with([])
    assert (xs, ys) == ([100, 200], [100, 200])


def test_an_interior_keepout_adds_no_loci():
    """The item-12 rule.  The keepout is real and still blocks; it just has
    nothing to add to a grid that already brackets the footprint."""
    xs, ys = _fp_with([(120, 130, 160, 170, True)])
    assert (xs, ys) == ([100, 200], [100, 200])


def test_the_same_keepout_unflagged_does_add_loci():
    """The flag is what changes it — not the geometry, not the layer.  This
    is also the default, so every hand-declared `add_keepout` is untouched."""
    xs, ys = _fp_with([(120, 130, 160, 170, False)])
    assert xs == [100, 120, 160, 200]
    assert ys == [100, 130, 170, 200]


def test_a_keepout_reaching_outside_the_block_still_contributes():
    """A halo is the motivating case: it extends past the footprint by
    construction, so its edges ARE reachable trunk positions.  The importer
    measures containment and leaves these unflagged."""
    xs, ys = _fp_with([(90, 95, 210, 205, False)])
    assert xs == [90, 100, 200, 210]
    assert ys == [95, 100, 200, 205]


def test_interior_keepouts_do_not_multiply_the_grid():
    """The failure mode in the item, in miniature: many fine rects inside one
    footprint.  Before the rule this grid grew as the PRODUCT of the two
    axis sets; now it is flat in the number of rects."""
    fine = [(100 + i, 100 + i, 101 + i, 101 + i, True) for i in range(1, 60)]
    xs, ys = _fp_with(fine)
    assert (len(xs), len(ys)) == (2, 2), (len(xs), len(ys))
    # …and the same rects unflagged are what the item measured.
    xs2, ys2 = _fp_with([(a, b, c, d, False) for a, b, c, d, _ in fine])
    assert len(xs2) * len(ys2) > 100 * len(xs) * len(ys)


def test_the_default_is_unchanged():
    """The policy is OPT-IN, and this is the test that says so.

    The first version of this change shipped unconditionally, on the
    reasoning that a position inside a block is unreachable anyway.  That is
    FALSE — a trunk may cross a block over-the-cell — so dropping those loci
    removes real candidate positions.  It cost `flow/rv/soc_conv_div`'s
    bundle 42 a 2-segment trunk sitting exactly on an OBS edge, which the
    QoR corpus could not see and `test_tapered_bit_spans` caught.
    """
    xs, ys = _fp_with([(120, 130, 160, 170, True)], outside_only=False)
    assert xs == [100, 120, 160, 200]
    assert ys == [100, 130, 170, 200]
    # …and a fresh Floorplan is in that mode without being asked.
    assert buda.Floorplan().keepout_loci_outside_only() is False


def test_an_interior_keepout_still_blocks():
    """The whole point: this changes the GRID, never what is routable.  A
    keepout covering the block on layer 3 must still be seen as blocking, or
    the fix would have traded a real obstruction for a fast one."""
    fp = buda.Floorplan()
    fp.add_block("m", 100, 100, 200, 200)
    fp.set_keepout_loci_outside_only(True)
    fp.add_keepout_zone(100, 100, 200, 200, [3], True)
    zones = fp.get_keepout_zones()
    assert len(zones) == 1
    assert zones[0].inside_block is True
    assert 3 in zones[0].layer_ids
    # `low_layer_keepouts` is a consumer that must not care about the flag.
    assert fp.low_layer_keepouts([3]), "an interior keepout stopped blocking"
