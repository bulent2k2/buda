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

"""A BUSTERM tap belongs to the bits whose own path terminates at that block.

Taps are recorded per SEGMENT while bits are per BIT.  On a tapered tree
(fan-in / fan-OUT) a trunk taps blocks only SOME of its bits terminate at, so
applying every tap to every bit holds the other bits out to a block they have
no net at — metal reaching nothing, and invisible to the ANTENNA audit, which
reads the BUS-level ConnSeg graph where the segment is legitimately attached
there (flow/antenna_taper_passthru.buda found it).

Two stages read the membership and they must read it identically:

  * DetailedNUTS re-extends a bit's span to a tap it serves and lets a stale
    end retract past one it does not;
  * check_dnuts requires a bit's span to reach the taps it serves.

If only the placer learns the taper, every retracted bit reads as a
BUSTERM_OPEN; if only the audit does, the metal stays and nothing reports it.
Hence the single predicate (`seg_busterm_serves_bit`) — and hence this file
asserts BOTH the geometry and the verdict on the same solve.
"""
import io
import contextlib
from pathlib import Path

import pytest

import buda_cli

_ROOT = Path(__file__).parents[2]
_FLOW = _ROOT / "flow/antenna_taper_passthru.buda"

pytestmark = pytest.mark.mid

# The vehicle: DIVERGENT folds three buses off one driver into one fan-out
# tree, so the trunk carries all 20 bits while each branch carries its own.
#   bits  0..7   a_*  ->  r_near, a stub off the trunk at x~330
#   bits  8..11  c_*  ->  mid,    the block at x 450..520
#   bits 12..19  b_*  ->  r_far,  the block at x 700..800
_A_BITS = range(0, 8)
_C_BITS = range(8, 12)
_B_BITS = range(12, 20)


@pytest.fixture(scope="module")
def solved():
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        s.do_command(f"source {_FLOW}")
    return s


def _trunk(solved):
    return {w.bit_index: (min(w.span_lo, w.span_hi), max(w.span_lo, w.span_hi))
            for w in solved.detailed_result.net_segments
            if w.bundle_id == 1 and w.seg_idx == 0}


def test_only_the_bits_a_tap_serves_are_held_out_to_it(solved):
    """THE regression.  The trunk taps `r_far` at x=700, and that tap used to
    vouch for all 20 bits — including the twelve that branch off at x<=520."""
    t = _trunk(solved)
    assert len(t) == 20, sorted(t)
    for b in list(_A_BITS) + list(_C_BITS):
        assert t[b][1] == 520, f"bit {b} still reaches past its own path: {t[b]}"


def test_a_bit_the_tap_does_serve_keeps_its_reach(solved):
    """The retraction is not a blanket trim: `b_*` really do terminate at
    `r_far`, so their trunk metal must still land on it."""
    for b in _B_BITS:
        assert _trunk(solved)[b][1] == 700, \
            f"bit {b} lost the tap its own net needs: {_trunk(solved)[b]}"


def test_the_trunk_metal_drops_by_the_overhang_and_no_more(solved):
    """20 bits x 500 = 10000 before; the twelve short bits now stop at 520."""
    total = sum(hi - lo for lo, hi in _trunk(solved).values())
    assert total == 7840, total


def test_the_audit_agrees_with_the_placer(solved):
    """The other half of the fix.  check_dnuts demands a bit's span reach the
    busterm taps of its segment; read per SEGMENT that is a demand for metal
    to a block the bit has no net at, and every retracted bit reports
    BUSTERM_OPEN.  Read through the same predicate, the design is clean."""
    assert solved.detailed_result.num_unplaced == 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        solved.do_command("check_design dnuts")
    assert "no violations found" in buf.getvalue(), buf.getvalue()[-800:]
