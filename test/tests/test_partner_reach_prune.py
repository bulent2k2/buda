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

"""Partner-reach seat pruning (nuts.cpp prune_unreachable_partner_windows).

NUTS places every segment against keepouts on its OWN layer.  A segment's
seat is one END of every perpendicular partner's span, so where it lands
decides what its partners must cross — and nothing used to consult the
keepouts on a PARTNER's layer.  flow/keepout_blocks_partner_reach.buda is
that shape: a WL-FLAT trunk window on M4 whose centre sits inside an M5
keepout's band, culling every bit of the M5 stub below it.

The BOUNDARY is the whole test.  A guard that only asserted "clean" would
pass just as well if the prune threw the entire window away, which would be
a large silent QoR loss on every design with a keepout — so the bound is
asserted exactly, in both directions:

  perp >= 450   the keepout's far along-edge; measured legal (the probe
                reads 450 inclusive, matching cull_keepout_crossers' STRICT
                span test: span_lo == k_a2 does not overlap)
  perp <= 580   UNCHANGED from the candidate's own slide window, i.e. the
                prune took the doomed band and nothing else

and the realized WL is pinned to the hand-slid control, which is what says
the prune cost nothing rather than merely avoided the keepout.
"""
import contextlib
import io
from pathlib import Path

import pytest

import buda_cli

_ROOT = Path(__file__).parents[2]
_FLOW = _ROOT / "flow" / "keepout_blocks_partner_reach.buda"

# The vehicle's declared geometry, restated so a silent edit to the flow is a
# test failure rather than a test that quietly stops meaning anything.
_KEEPOUT_Y_HI = 450.0      # add_keepout 600 380 900 450 5
# The trunk's UNPRUNED upper bound as apply_interval_constraints leaves it:
# the candidate's slide window is [220,580] and a trunk pays that pass's 10%
# margin (span 360 -> 36) on the side opposite its pull, so 580 - 36 = 544.
# Deliberately NOT the raw 580 — asserting the value the prune INHERITS is
# what proves it moved `lo` alone and left `hi` exactly as it found it.
_SLIDE_HI     = 544.0

pytestmark = pytest.mark.mid


def _run():
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        s.do_command(f"source {_FLOW}")
    s._test_stdout = buf.getvalue()
    return s


@pytest.fixture(scope="module")
def run():
    return _run()


def _trunk(session):
    """The bundle's only HORIZONTAL placed segment — the M4 trunk."""
    segs = [t for t in session.nuts_result.segments if t.horiz and t.placed]
    assert len(segs) == 1, [(t.bundle_id, t.seg_idx) for t in segs]
    return segs[0]


def test_the_flow_routes_every_bit_with_no_healer(run):
    """Before the prune: 4 of 12 bits culled, and the flow calls no healer.

    The vehicle deliberately has no `ripup_reroute` — a healer DOES rescue
    this, by re-pinning to a detour, which is the expensive answer this pass
    exists to avoid needing.
    """
    assert run.detailed_result.num_unplaced == 0
    assert run.detailed_result.num_keepout_bits == 0


def test_the_trunk_is_seated_clear_of_the_partner_s_keepout(run):
    """The seat itself, not just the outcome.

    `interval_lo` bounds the bus EXTENT (preferred_fit keeps the centre half a
    width inside), so the lowest BIT lands at the bound — and a bit exactly at
    450 does not cross, because the cull's span test is strict.
    """
    t = _trunk(run)
    lowest_bit = t.track_position - t.width / 2.0
    assert lowest_bit >= _KEEPOUT_Y_HI - 1e-9, (lowest_bit, t.track_position, t.width)


def test_the_prune_takes_the_doomed_band_and_nothing_more(run):
    """The other direction, and the reason this file exists.

    Throwing the whole window away would also produce a clean route here while
    costing real freedom everywhere else.  The upper bound must come through
    the pass unchanged — see _SLIDE_HI for where its value comes from.
    """
    t = _trunk(run)
    assert t.interval_lo == pytest.approx(_KEEPOUT_Y_HI), t.interval_lo
    assert t.interval_hi == pytest.approx(_SLIDE_HI), t.interval_hi


def test_the_prune_costs_no_wirelength_here(run):
    """The window is WL-FLAT (pull=none(0), opt spanning it), so every legal
    seat realizes identically — measured 3715 at 450, 451 and 460 alike.  This
    is the hand-slid control, so a regression that reached cleanliness by
    routing further would be caught rather than congratulated.
    """
    assert "total detailed WL = 3715" in run._test_stdout, run._test_stdout[-2000:]


def test_the_pass_says_what_it_did(run):
    """Not silent.  A window narrowed behind the user's back is the kind of
    thing that turns up months later as an unexplained QoR difference.
    """
    assert "seat window(s) pruned to what a perpendicular partner can reach" \
        in run._test_stdout


def test_a_design_with_no_declared_keepout_is_untouched():
    """The early-out, pinned: with no zones the pass must not run at all, which
    is what makes 'byte-identical unless a keepout exists' checkable rather
    than merely claimed.
    """
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        for line in (_FLOW.read_text().splitlines()):
            line = line.split("#")[0].strip()
            if not line or line.startswith("add_keepout"):
                continue
            s.do_command(line)
    assert "seat window(s) pruned" not in buf.getvalue()
    assert s.detailed_result.num_unplaced == 0
