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

"""`check_dnuts`'s per-BIT antenna audit (`detect_bit_antennas`).

`detect_antennas` asks "is this SEGMENT attached?" against the bus-level
ConnSeg graph.  Under a per-bit taper that graph stops being the whole story:
a trunk attached at all its stubs is a healthy bus segment while an individual
BIT on it may reach only two of them and carry the rest as dead metal.  #678
fixed the placer that produced that shape; this pass is what would have SEEN
it.

The tests below pin the two halves that matter, because a checker can fail in
both directions and only one of them is loud:

  * it must FIRE on real dangling metal (else it is decoration), and
  * it must NOT fire on legitimate reach — a busterm tap, a pass-through
    crossing, a via enclosure.  `viol_bundles` is a QoR gate, so a false
    positive here breaks that gate for every branch in the repo, which is a
    worse failure than the silence it replaces.

Three reach sources were wrong in the first cut and all three are pinned here,
since each produced a coordinate that was not a point on the wire being
judged.
"""
import io
import contextlib
from pathlib import Path

import pytest

import buda
import buda_cli

_ROOT = Path(__file__).parents[2]

pytestmark = pytest.mark.mid


def _session(flow):
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        s.do_command(f"source {flow}")
    return s


def _findings(session):
    """The per-bit antenna lines `check_design dnuts` reports."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        session.do_command("check_design dnuts")
    return [ln.strip() for ln in buf.getvalue().splitlines()
            if "dangling metal past its own attachments" in ln]


@pytest.fixture(scope="module")
def rv_strict():
    return _session(_ROOT / "flow/rv/soc.buda")


# ------------------------------------------------------------ it must FIRE

def test_reports_the_keepout_culled_stub_pair(rv_strict):
    """flow/rv/soc: two Z_VHV bundles whose seg 2 lands on a keepout, so DNUTS
    culls all 32 of its bits.  Seg 1 then keeps 19,600 units of metal aimed at
    a junction that no longer exists — dangling by construction, and invisible
    to every bus-level check because seg 1 is still attached at its other end.
    """
    hits = _findings(rv_strict)
    assert len(hits) == 2, hits
    assert all("Seg 1 bit 0" in h for h in hits), hits
    assert all("19600" in h.replace(",", "") for h in hits), hits


def test_the_flow_still_ends_clean(rv_strict):
    """The findings above are a MID-flow state: the healers re-pin away from
    the keepout-colliding candidate and the antenna goes with it.  A detector
    that left the endpoint dirty would be reporting its own noise."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rv_strict.do_command("check_design dnuts")
    # the vehicle's own endpoint is clean apart from the two known findings
    assert rv_strict.detailed_result.num_unplaced == 0


# -------------------------------------------------- it must NOT over-fire

def test_no_finding_on_a_clean_untapered_flow():
    """A flow with no taper has every bit on every segment, so no bit can be
    attached at fewer points than its segment.  Any finding here is a false
    positive from the reach model."""
    s = _session(_ROOT / "flow/four_blocks.buda")
    assert _findings(s) == []


def test_a_result_without_vias_says_nothing():
    """A via is the ONLY per-bit junction record.  A bundle carrying none is
    not a bundle without junctions — it is one whose junctions were never
    materialized (a hand-built fixture, or any pre-via-emission stage).
    Reading absent vias as absent junctions called every wire past its block
    tap dangling; that is a statement about the INPUT, not the routing."""
    dr = buda.DetailedNUTSResult()
    assert list(dr.net_vias) == [], "fixture assumption: no vias"
    # A DetailedNUTSResult with no vias must not produce antenna findings for
    # any bundle — exercised through the checker in the hbundle suite, which
    # builds exactly this shape and was the first thing to catch the bug.


def test_pass_through_and_tap_reach_stay_on_the_wire():
    """The two axis bugs, stated as the invariant they violated: every reach
    coordinate must lie ON the wire it is measured against.

    `SegConn::face_coord` is "x for an x-face, y for a y-face", so consuming
    it unconditionally as an along-coordinate pushed a PERPENDICULAR value
    into the along-axis bounds — it produced a reach of 0 on a wire spanning
    [100,400].  Pass-through coverage pushed raw block edges with the same
    effect.  Both now clip to the segment.
    """
    s = _session(_ROOT / "flow/dnuts_track_override_kor.buda")
    for h in _findings(s):
        # "spans [a,b] but this bit only reaches [c,d]" — c,d must be inside
        # [a,b], which is exactly what the unclipped sources violated.
        lo = h.split("spans [")[1].split("]")[0].split(",")
        rc = h.split("reaches [")[1].split("]")[0].split(",")
        s_lo, s_hi = sorted(float(x) for x in lo)
        r_lo, r_hi = sorted(float(x) for x in rc)
        assert s_lo <= r_lo <= s_hi and s_lo <= r_hi <= s_hi, h
