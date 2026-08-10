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

"""A tapered tree's bit must not keep the whole trunk's span.

`adjust_bit_spans` snaps a bit-wire's end to the connected segment's same-bit
track — but only when that segment HAS a wire for this bit.  A per-bit tapered
tree (fan-in / fan-OUT) gives each stub only its own bits, so for every other
bit the endpoint connection resolves to nothing, `has_ep_*` stays false, and
the "keep the abstract span" rule leaves that bit spanning the entire trunk.

The residue is dangling metal past the bit's outermost junction, and the
ANTENNA audit cannot see it: that audit reads the BUS-level ConnSeg graph,
where the trunk is attached at every stub and looks perfectly healthy.

Measured on the vehicle below before the fix: all 32 trunk bits spanned the
full 108,400 while the neediest wanted 800 — 73.5% of the vertical metal was
dangling, with `check_design` reporting Success.
"""
import io
import contextlib
import collections
from pathlib import Path

import pytest

import buda
import buda_cli

_ROOT = Path(__file__).parents[2]
_FLOW = _ROOT / "flow/rv/soc_conv_div.buda"

pytestmark = pytest.mark.mid


@pytest.fixture(scope="module")
def solved():
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        s.do_command(f"source {_FLOW}")
    return s


def _dangling(s, bundle_id):
    """Per bit-wire: metal lying past its own outermost via, per axis.

    A via marks a real junction, so anything beyond the outermost one on a
    wire attaches to nothing.  Wires whose ends are busterm taps (no via
    there) are excluded — a tap is a legitimate end.
    """
    dr = s.detailed_result
    is_h = (lambda lid:
            s.layers.get_layer_dir(lid) == buda.LayerDir.HORIZONTAL)
    vias = collections.defaultdict(list)
    for v in dr.net_vias:
        if v.bundle_id == bundle_id:
            vias[(v.bit_index, v.from_seg)].append(v)
            vias[(v.bit_index, v.to_seg)].append(v)
    out = {True: 0.0, False: 0.0}
    for w in dr.net_segments:
        if w.bundle_id != bundle_id:
            continue
        vs = vias.get((w.bit_index, w.seg_idx))
        if not vs or len(vs) < 2:
            continue                       # an end is a tap, not a junction
        coord = (lambda v: v.x) if is_h(w.layer) else (lambda v: v.y)
        lo, hi = min(map(coord, vs)), max(map(coord, vs))
        lead = max(lo - min(w.span_lo, w.span_hi), 0.0)
        trail = max(max(w.span_lo, w.span_hi) - hi, 0.0)
        out[is_h(w.layer)] += lead + trail
    return out


def test_fanout_trunk_bits_carry_no_dangling_metal(solved):
    """THE regression.  Bundle 1 is a 32-bit FANOUT whose trunk carries every
    bit while each stub carries one; before the fix every trunk bit spanned
    the whole trunk."""
    d = _dangling(solved, 1)
    assert d[False] == 0.0, f"vertical (trunk) dangling metal: {d[False]}"


def test_every_tapered_bundle_is_clean(solved):
    """Not just bundle 1 — no bundle in the design may carry dangling metal
    between two of its own junctions."""
    worst = {}
    for w in solved.bundles:
        bid = w.input.original_bundle.id
        d = _dangling(solved, bid)
        if d[True] + d[False] > 0:
            worst[bid] = d
    assert not worst, f"bundles with dangling metal: {worst}"


def test_each_trunk_bit_spans_exactly_its_own_junctions(solved):
    """The positive statement: bit k's trunk wire runs from its driver
    junction to its OWN receiver stub, not to the trunk's extremes."""
    dr = solved.detailed_result
    trunk = [w for w in dr.net_segments if w.bundle_id == 1 and w.seg_idx == 0]
    assert len(trunk) == 32, len(trunk)
    spans = {abs(w.span_hi - w.span_lo) for w in trunk}
    assert len(spans) > 1, \
        "every trunk bit has an identical span — the taper was ignored"
    assert max(spans) < 108_400, \
        f"a trunk bit still spans the full abstract trunk: {max(spans)}"


def test_the_flow_still_ends_clean(solved):
    """Retracting metal must not open anything: the fix removes wire that was
    attached at neither end, so connectivity is unchanged by construction —
    and this checks it rather than asserting it."""
    assert solved.detailed_result.num_unplaced == 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        solved.do_command("check_design dnuts")
    assert "no violations found" in buf.getvalue(), buf.getvalue()[-600:]
