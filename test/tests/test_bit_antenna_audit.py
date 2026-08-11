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


def _session(flow, verbose=False):
    """Run a flow and keep BOTH the session and everything it printed.

    The printed output matters: a flow's own mid-flow `check_design` calls are
    where a transient finding shows up, and by the time the flow ends the
    healers have usually re-planned it away.  Querying only the final state
    would make a working detector look silent.  For the same reason `verbose`
    has to be set BEFORE the run rather than by re-checking at the end: the
    per-bit detail exists only while the finding does.
    """
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.verbose_conn = verbose
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        try:
            s.do_command(f"source {flow}")
        except SystemExit as e:
            # A flow ending in `exit` is a normal flow, not a failure — but a
            # NON-zero code is the script declaring its own result bad, and
            # silently swallowing that would let a broken vehicle look like a
            # clean one.  Keep the output either way; re-raise the complaint.
            s._test_exit = e.code or 0
            if s._test_exit:
                raise
    s._test_stdout = buf.getvalue()
    return s


def _findings_in_run(session):
    """Per-bit antenna lines the flow itself reported, at any stage."""
    return [ln.strip() for ln in session._test_stdout.splitlines()
            if "dangling metal past its own attachments" in ln]


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


@pytest.fixture(scope="module")
def rv_verbose():
    """The same flow under --verbose-conn, where the summary's grouped line is
    replaced by each violation's own message."""
    return _session(_ROOT / "flow/rv/soc.buda", verbose=True)


# ------------------------------------------------------------ it must FIRE

def test_reports_the_keepout_culled_stub_pair(rv_strict):
    """flow/rv/soc: two Z_VHV bundles whose seg 2 lands on a keepout, so DNUTS
    culls all 32 of its bits.  Seg 1 then keeps 19,600 units of metal aimed at
    a junction that no longer exists — dangling by construction, and invisible
    to every bus-level check because seg 1 is still attached at its other end.

    Reported in the SUMMARY form every per-bit kind uses, since the finding
    carries its bit index: one line per (bundle, segment) group naming how many
    bits it covers.  ALL 32 are dangling — seg 2 was culled entirely, so no bit
    of seg 1 has the junction it was aiming at.

    That count is the point.  Before the finding carried its bit, the reporter
    read the whole group as ONE non-bit violation and printed a single bit's
    message: two lines and a total of 2, where the truth is 2 x 32 bits, each
    19,600 units — 1,254,400 units of dangling metal reported as "2".
    """
    hits = _findings_in_run(rv_strict)
    assert len(hits) == 2, hits
    assert all("Seg 1: 32 bit(s)" in h for h in hits), hits


def test_the_finding_names_its_bit_and_its_magnitude_verbatim(rv_verbose):
    """The per-bit detail the summary line collapses.

    The magnitude is the measurement the audit exists to make, and it must
    survive the summary's collapsing: one message per affected bit, each naming
    its own bit and its own 19,600 units.  64 = 2 bundles x 32 bits, the same
    population the summary line above reports as two groups of 32 — asserting
    both is what keeps the grouped count honest against the per-bit truth.

    Every bit of seg 1 dangles by the same amount because they all aim at the
    same culled junction; they differ only in which track they sit on, which is
    why the reach coordinate varies from line to line and the overhang does not.
    """
    v = _findings_in_run(rv_verbose)
    assert len(v) == 64, len(v)
    assert sum("Seg 1 bit 0 " in h for h in v) == 2, v[:2]
    assert all("19600" in h.replace(",", "") for h in v), v[:2]
    assert len({h.split("Bundle ")[1].split(":")[0] for h in v}) == 2, v[:2]


def test_the_flow_still_ends_clean(rv_strict):
    """The findings above are a MID-flow state: the healers re-pin away from
    the keepout-colliding candidate and the antenna goes with it.  A detector
    whose findings SURVIVED to the endpoint on a vehicle everyone calls clean
    would be reporting its own noise, so this is the other half of the claim.
    """
    assert rv_strict.detailed_result.num_unplaced == 0
    assert _findings(rv_strict) == [], "findings should not survive the healers"


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
    """The invariant behind the axis bugs: every reach coordinate must lie ON
    the wire it is measured against.

    A coordinate off the wire manufactures overhang out of nothing, and the
    first cut of this audit produced one — a reach of 0 on a wire spanning
    [100,400] — from raw block edges admitted without clipping.  Each source
    is now on the wire by construction: a via is a point on it, a served tap
    is `SegConn::face_coord` (the along-axis coordinate of the endpoint that
    lands on the block, which DetailedNUTS extends the bit to cover), and a
    crossing is clipped to the bit's own span.
    """
    s = _session(_ROOT / "flow/dnuts_track_override_kor.buda", verbose=True)
    for h in _findings_in_run(s):
        # "spans [a,b] but this bit only reaches [c,d]" — c,d must be inside
        # [a,b], which is exactly what the unclipped sources violated.
        lo = h.split("spans [")[1].split("]")[0].split(",")
        rc = h.split("reaches [")[1].split("]")[0].split(",")
        s_lo, s_hi = sorted(float(x) for x in lo)
        r_lo, r_hi = sorted(float(x) for x in rc)
        assert s_lo <= r_lo <= s_hi and s_lo <= r_hi <= s_hi, h


def test_a_crossing_is_judged_at_the_bit_not_at_the_nominal_segment():
    """The reach a NOMINAL crossing used to grant, on a bit that crosses nothing.

    flow/rnr/mix2_topdown_refine bundle 35 seg 5 is the case: the nominal
    ConnSeg sits at perp_pos 1020 and grazes `chip/i_dnuts1_4/v0` (y 920..1020)
    on its boundary, so the segment "crosses" it.  The placed bits do not —
    they sit at y 1044.5 and up, outside the block entirely.  Worse, the block
    spans x 2130..2330 while the bits end at x 2069.75, so the credited reach
    was a coordinate BEYOND the end of the wire being judged; that made
    `trail = span_hi - reach_hi` negative and silently cancelled the finding.

    Judged at each bit's own track and span, the 3 bits are short of their span
    by 8.75 / 5.75 / 2.75 — real metal past their last via, and the flow's
    `viol_bundles` goes 0 -> 1 because the audit got sharper, not because the
    routing changed (detailed WL is identical across the change).
    """
    s = _session(_ROOT / "flow/rnr/mix2_topdown_refine.buda", verbose=True)
    hits = _findings_in_run(s)
    assert len(hits) == 3, hits
    assert all("Seg 5 bit" in h for h in hits), hits
    overhangs = sorted(float(h.split("0 + ")[1].split(" of")[0]) for h in hits)
    assert overhangs == [2.75, 5.75, 8.75], overhangs
    # The reach must lie ON the wire — the invariant the nominal crossing broke.
    for h in hits:
        s_hi = max(float(x) for x in
                   h.split("spans [")[1].split("]")[0].split(","))
        r_hi = max(float(x) for x in
                   h.split("reaches [")[1].split("]")[0].split(","))
        assert r_hi <= s_hi, h
