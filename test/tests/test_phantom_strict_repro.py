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

"""The phantom charge, made decisive — `flow/mst_phantom_strict.buda`.

`tools/experiment/phantom_charge.py` measured the same-bundle double charge on
the corpus and found it real but rare: 4 coincident pairs in 555 committed
bundles, 3 affected bands, and

    ... over capacity BECAUSE of it        0

everywhere.  So the STRICT harm the mechanism implies has never been observed,
and rarity — not smallness — is the whole defence.

This vehicle is the adversarial construction that makes that column read 1.  It
is not a design the corpus produces: the duplicate rides candidates that LOSE,
so it has to be pinned, and the band has to be loaded to within one duplicate's
width of capacity, which takes a 133-bit carrier and an 88-bit victim.

What the tests below hold is the CLAIM, not the arrangement:

  * the pinned candidate is asserted by TYPE, never by index — the index is 18
    here and 14 in the control, because `set_trim_mst_legs` re-sorts the
    WL-ordered pool, and a test keyed on 18 would silently start measuring a
    different candidate the day generation changes;

  * the harm is asserted as the ARITHMETIC (`usage > cap` while
    `usage - phantom <= cap`), which is what "over capacity because of it"
    means and what makes the attribution independent of the control arm;

  * the control asserts the victim's relief AND that bundle 1's own overflow is
    unchanged between the arms — a pre-existing constant of the design, so the
    comparison isolates the victim.

If a future change makes the duplicate stop reaching committed geometry, these
fail LOUD rather than quietly reporting a vehicle that no longer demonstrates
anything.
"""
import contextlib
import io
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

import buda_cli

_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(_ROOT / "tools" / "experiment"))

import phantom_charge as pc  # noqa: E402

_PHANTOM = _ROOT / "flow/mst_phantom_strict.buda"
_CONTROL = _ROOT / "flow/mst_phantom_strict_control.buda"

pytestmark = pytest.mark.mid


def _solve(flow):
    """Run a flow, returning (session, everything it printed)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            s.do_command(f"source {flow}")
        except SystemExit:
            pass
    return s, buf.getvalue()


def _overflow(out, bundle):
    """The planner's committed overflow for one bundle, off its own line."""
    m = re.search(rf"Bundle {bundle} \([\d.]+ units wide\) -> topo .*?"
                  rf"overflow=([\d.]+)", out)
    assert m, f"no commit line for bundle {bundle}"
    return float(m.group(1))


def _pinned_type(session, bundle_id):
    for w in session.bundles:
        if w.input.original_bundle.id == bundle_id:
            assert w.plan and w.plan.selected_topology_index >= 0
            return w.input.candidates[w.plan.selected_topology_index].type
    raise AssertionError(f"bundle {bundle_id} absent")


def _bands(flow):
    """phantom_charge's own P0/P2 verdict for one flow."""
    p0, totals, bands = Counter(), Counter(), []
    pc.run(str(flow), p0, totals, bands)
    return p0, totals, bands


# ------------------------------------------------------------------ the shape

def test_carrier_pins_the_duplicate_bearing_candidate():
    """MST_HV by TYPE — the index is generation-order and will move."""
    s, _ = _solve(_PHANTOM)
    assert _pinned_type(s, 1) == "MST_HV"
    sc, _ = _solve(_CONTROL)
    assert _pinned_type(sc, 1) == "MST_HV"


def test_the_twins_are_co_placed_so_the_charge_is_a_phantom():
    """NUTS must put both legs on ONE track.

    'May share a track' is not 'does share' — a pair placed APART is two wires
    and two charges are CORRECT.  The whole vehicle rests on this being the
    co-placed case, so it is asserted rather than assumed.
    """
    p0, totals, _ = _bands(_PHANTOM)
    assert p0["co-placed (phantom)"] == 1, dict(p0)
    assert p0["placed APART (charge correct)"] == 0, dict(p0)
    assert totals["pairs"] == 1, dict(totals)


# ------------------------------------------------------------------- the harm

def test_the_phantom_is_what_puts_the_band_over_capacity():
    """The arithmetic that IS the claim.

    Measured: cap 700.00, usage 799.46, phantom 300.24 on M4.  Asserted as
    relations, so the vehicle survives a re-tune of the bus widths but fails if
    the phantom stops being decisive.
    """
    _, _, bands = _bands(_PHANTOM)
    assert len(bands) == 1, bands
    (_name, _ci, _b, phantom, cap, usage, layer, bids) = bands[0]

    assert phantom > 0
    assert usage > cap, "the band is not over capacity — nothing to attribute"
    assert usage - phantom <= cap, (
        "the band would overflow even without the phantom, so the phantom is "
        "not what makes it overflow")
    assert layer == 4 and bids == [1], bands[0]


def test_the_victim_is_pushed_out_of_the_strict_tier():
    """The downstream consequence: a bundle whose own metal fits, overflowing."""
    _, out = _solve(_PHANTOM)
    assert _overflow(out, 2) > 0
    assert "Bundle 2: no overflow-free candidate" in out


def test_control_arm_relieves_the_victim_and_holds_the_carrier_fixed():
    """Corroboration, with the confound named.

    The trim removes the duplicated METAL (7 segments -> 5), so this is not a
    charge-only isolation — that is why the attribution above is arithmetic.
    What the control does show is that bundle 1's own overflow is a CONSTANT of
    the design: identical in both arms, so the victim's flip is not a
    side-effect of the carrier landing somewhere else.
    """
    _, phantom_out = _solve(_PHANTOM)
    _, control_out = _solve(_CONTROL)

    assert _overflow(control_out, 2) == 0
    assert _overflow(phantom_out, 2) > 0
    assert _overflow(control_out, 1) == _overflow(phantom_out, 1)

    # And the duplicate really is gone in the control.
    p0, _, bands = _bands(_CONTROL)
    assert p0["co-placed (phantom)"] == 0, dict(p0)
    assert bands == [], bands


def test_the_vehicles_stay_out_of_the_census_list():
    """The separation, asserted where it can actually be broken.

    `phantom_charge.py`'s default `FLOWS` is the census of what REAL designs
    do.  Adding a construction built to overflow would not fail anything below
    — it would simply change the census's verdict, turning a measurement into
    an argument.  So the absence is pinned directly rather than inferred from a
    corpus flow still coming out clean.
    """
    listed = {Path(f).name for f in pc.FLOWS}
    # Non-vacuity: a `not in` over an empty or differently-spelled set would
    # pass for the wrong reason, so anchor on a flow that IS the census.
    assert "four_blocks.buda" in listed, pc.FLOWS
    assert _PHANTOM.name not in listed, pc.FLOWS
    assert _CONTROL.name not in listed, pc.FLOWS


def test_corpus_flows_still_report_no_harm():
    """And the census's verdict itself: on a real design, nothing overflows.

    Complements the test above — that one keeps the vehicle out of the list,
    this one keeps the list's conclusion true.
    """
    _, _, bands = _bands(_ROOT / "flow/four_blocks.buda")
    for row in bands:
        (_n, _ci, _b, phantom, cap, usage, _l, _bids) = row
        assert usage - phantom <= cap
        assert usage <= cap, row
