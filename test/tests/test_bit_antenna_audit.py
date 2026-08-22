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
import os
import tempfile
from pathlib import Path

import sys

import pytest

import buda
import buda_cli

_ROOT = Path(__file__).parents[2]

pytestmark = pytest.mark.mid


# Placement-sensitive assertion: MSVC codegen (different FP contraction /
# libm / tie-breaking than the GCC Linux gate, whose -ffp-contract=off and
# pinned -march exist exactly to stop such drift) lands a DIFFERENT-BUT-LEGAL
# placement, and the exact counts/goldens asserted here move with it
# (measured, windows-validate run 25).  QoR is gated on Linux CI; the
# Windows lanes validate the platform, not the numbers.
_WIN_QOR_SKIP = ("MSVC placement divergence: exact-count/golden assertions "
                 "are Linux-gated (measured, windows-validate run 25)")


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

def _with_overhang_restored(session, seg_idx=1, reach=710.0):
    """Put back the metal the entry-6 fix now retracts.

    The reporter's POSITIVE path — grouping per (bundle, segment), naming each
    bit and its own magnitude — had exactly one fixture in the tree: the
    dangling metal rv/soc produced mid-flow.  Fixing the placer deleted it, and
    with it the only test of what the audit SAYS when it does fire.

    So the fixture is rebuilt here instead of harvested from a defect: seg 1's
    bits are stretched back out to where seg 2 used to be, which is precisely
    the geometry the placer used to emit.  That decouples the reporter from the
    placer, so the next placer fix cannot silently take this coverage with it —
    which is what happened the first time.
    """
    segs = session.detailed_result.net_segments
    for n in segs:
        if n.seg_idx == seg_idx:
            n.span_hi = reach
    session.detailed_result.net_segments = segs
    return session


def test_the_reporter_groups_bits_and_names_their_magnitude():
    """What the audit says when it fires, on rebuilt geometry.

    Summary form is one line per (bundle, segment) naming how many bits it
    covers.  That count is the point: before the finding carried its bit index,
    the reporter read a whole group as ONE non-bit violation and printed a
    single bit's message — on rv/soc that turned 2 x 32 bits of 19,600 units
    into a reported "2".
    """
    s = _with_overhang_restored(
        _session(_ROOT / "flow/antenna_culled_partner.buda"))
    hits = _findings(s)
    assert len(hits) == 1, hits
    assert "Seg 1: 4 bit(s)" in hits[0], hits


def test_the_finding_names_its_bit_and_its_magnitude_verbatim():
    """The per-bit detail the summary line collapses.

    One message per affected bit, each naming its own bit and its own overhang.
    Asserting both forms is what keeps the grouped count honest against the
    per-bit truth.

    The magnitudes are the REBUILT fixture's, not the historical placer's: this
    stretches every bit to one coordinate, while the placer stretched each to
    its own partner's track (so the old numbers were 526.5 + 529.5 x 3).  They
    differ per bit here because the bits start at different span_lo, which is
    what makes them a real test of per-bit reporting rather than a constant.
    """
    s = _with_overhang_restored(
        _session(_ROOT / "flow/antenna_culled_partner.buda", verbose=True))
    v = _findings(s)
    assert len(v) == 4, v
    assert sum("Seg 1 bit 0 " in h for h in v) == 1, v
    overhangs = sorted(round(float(h.split("0 + ")[1].split(" of")[0]), 2) for h in v)
    assert overhangs == [522.0, 523.5, 525.0, 526.5], overhangs


def test_rv_soc_no_longer_dangles_mid_flow(rv_verbose):
    """Entry 7 — the same defect at scale, and it went with entry 6.

    Measured before the fix: 64 findings, 2 bundles x 32 bits x 19,600 units =
    1,254,400 units of dangling metal immediately after the first
    `run_detailed_nuts`.  It never reached the endpoint (the healers re-pinned
    away from it, which `test_the_flow_still_ends_clean` holds), so this was
    always latent rather than shipped — but a design without those healers kept
    it.
    """
    assert _findings_in_run(rv_verbose) == []


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


def test_a_partner_lost_to_the_CULL_retracts_its_neighbour_too():
    """Entry 6, FIXED — this guard used to pin the defect.

    DetailedNUTS runs `place_by_layer` -> `adjust_bit_spans` ->
    `cull_keepout_crossers`.  The middle pass extends each bit to reach its
    partner's track; the last deletes bits whose FINAL span still crosses a
    keepout.  A partner removed by that cull was removed AFTER the neighbour
    was stretched to meet it, so the neighbour kept metal aimed at a wire that
    no longer existed — #678's stale-end rule could not help, living inside
    `adjust_bit_spans` one pass too early.

    The fix re-runs `adjust_bit_spans` once the survivors are known, so that
    same rule fires.  Measured here: seg 1 went from [183.5,710] to
    [183.5,183.5], detailed WL 2922 -> 807.

    `num_keepout_bits` is still what proves the CULL was the path taken.
    `num_unplaced` is 4 either way (see the starved twin below), so asserting
    on it alone would let this guard pass while testing the wrong path — and
    that matters more now, not less: both paths now produce the same retracted
    geometry, so the counter is the ONLY thing telling them apart.
    """
    s = _session(_ROOT / "flow/antenna_culled_partner.buda", verbose=True)
    assert s.detailed_result.num_keepout_bits == 4, "the post-adjustment cull must be the path"
    assert s.detailed_result.num_unplaced == 4

    assert _findings_in_run(s) == [], "the neighbour must no longer dangle"

    # Seg 1's bits retract to their own via instead of reaching for seg 2.
    seg1 = [n for n in s.detailed_result.net_segments if n.seg_idx == 1]
    assert len(seg1) == 4, seg1
    assert all(n.span_hi - n.span_lo == 0 for n in seg1), \
        [(n.bit_index, n.span_lo, n.span_hi) for n in seg1]


def test_the_culled_and_starved_paths_now_agree():
    """The pair's whole point, stated as one assertion.

    Same design, same pin, one number different in the keepout — one loses its
    partner at placement ADMISSION, the other to the post-adjustment CULL.  The
    outcome used to depend on which; it must not any more.
    """
    culled = _session(_ROOT / "flow/antenna_culled_partner.buda")
    starved = _session(_ROOT / "flow/antenna_starved_partner.buda")

    def spans(sess):
        return sorted((n.seg_idx, n.bit_index, n.span_lo, n.span_hi)
                      for n in sess.detailed_result.net_segments)

    assert spans(culled) == spans(starved)
    # ...but by DIFFERENT routes, which the counter still records.
    assert culled.detailed_result.num_keepout_bits == 4
    assert starved.detailed_result.num_keepout_bits == 0


def test_a_partner_STARVED_before_span_adjustment_retracts_it_instead():
    """The control: same design, same pin, one number different in the keepout.

    Here the partner is lost at placement ADMISSION — no eligible track, bits
    never created — which is BEFORE `adjust_bit_spans`.  The neighbour's
    endpoint conn therefore resolves to no wire while spans are being set,
    #678's stale-end rule fires, and it retracts to its own via.

    Together with the twin above this is the whole argument: identical geometry,
    two ways to lose the same partner, opposite outcomes.  So the cause is
    neither the keepout nor the geometry — it is WHERE IN THE PASS ORDER the
    partner goes.  Whichever way that gets fixed, both of these must keep
    saying what they say now.
    """
    s = _session(_ROOT / "flow/antenna_starved_partner.buda", verbose=True)
    assert s.detailed_result.num_keepout_bits == 0, "the cull must NOT be the path here"
    assert s.detailed_result.num_unplaced == 4, "same 4 bits lost, different pass"
    assert _findings_in_run(s) == [], "the retraction should leave no antenna"

    seg1 = [n for n in s.detailed_result.net_segments if n.seg_idx == 1]
    assert len(seg1) == 4, seg1
    assert all(abs(n.span_hi - n.span_lo) < 1.0 for n in seg1), \
        [(n.bit_index, n.span_lo, n.span_hi) for n in seg1]


_REFINE = _ROOT / "flow/rnr/mix2_topdown_refine.buda"


@contextlib.contextmanager
def _pre_placed_endpoints():
    """Run with `set_placed_endpoints` OFF — the pre-flip DNUTS reading.

    The two tests below are the audit's own regression tests (#695), and their
    vehicle is bundle 35 seg 5 of `mix2_topdown_refine`: a junction that is
    mid-span at NOMINAL and becomes the placed end, so its bits keep the shared
    abstract end.  `set_placed_endpoints` — default ON since the flip — removes
    exactly that metal at stage 9, which makes the finding disappear and the
    `set_trim_mst_legs` saving on this wire go to zero (805,553 -> 805,536
    either way; the two fixes are redundant HERE).

    The audit is unaffected and still finds the shape; there is simply nothing
    left for it to find in the default configuration.  So these tests pin the
    OLD reading deliberately, to keep exercising the checker on the geometry
    that justified it.  Whatever later removes the CAUSE must not be able to
    silently retire the DETECTOR.

    The flag is process-global (one module flag so every make_bus_segments
    caller agrees), so it is restored on the way out — a leak would change
    every later test in the same worker.
    """
    prev = buda.dnuts_placed_endpoints()
    buda.dnuts_set_placed_endpoints(False)
    try:
        yield
    finally:
        buda.dnuts_set_placed_endpoints(prev)


def _session_without_the_trim(flow):
    """Run `flow` with its `set_trim_mst_legs on` line removed.

    The flow OPTS IN, so its shipped result is the trimmed one.  Recovering the
    pre-trim state by stripping that line keeps both sides measurable from a
    single checked-in vehicle, and means the "before" numbers below are a real
    run rather than a remembered one.
    """
    src = [ln for ln in flow.read_text().splitlines(keepends=True)
           if ln.strip() != "set_trim_mst_legs on"]
    assert len(src) < len(flow.read_text().splitlines()), "opt-in line not found"

    # UNIQUE name, and in the flow's OWN directory.  Both halves are load-bearing:
    # the directory because the flow does `source mix_tracks.buda`, which a
    # command resolves against the SCRIPT's directory — a tmp_path copy would not
    # find it; the uniqueness because CI runs `-p xdist -n 4` and BOTH tests in
    # this module call this helper, so a fixed name lets one worker unlink the
    # file the other is still running against (Codex #732).
    fd, path = tempfile.mkstemp(dir=flow.parent, prefix="_no_trim_", suffix=".buda")
    tmp = Path(path)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("".join(src))
        return _session(tmp, verbose=True)
    finally:
        tmp.unlink(missing_ok=True)


def _detailed_wl(session):
    for ln in session._test_stdout.splitlines():
        if "total detailed WL" in ln:
            return float(ln.split("total detailed WL = ")[1].split()[0])
    raise AssertionError("no detailed WL line in the run")


@pytest.mark.skipif(sys.platform == "win32", reason=_WIN_QOR_SKIP)
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
    by 8.75 / 5.75 / 2.75 — real metal past their last via.

    The flow now opts in to the trim that removes this metal at the source, so
    the finding is recovered by stripping that one line.  This is the audit's
    own regression test (#695) and it must keep working on the shape that
    justified it, whatever generation later does about the cause.

    Pinned to the PRE-FLIP DNUTS reading: `set_placed_endpoints` (default on)
    removes this same metal at stage 9, so in the default configuration there
    is nothing left for the audit to find here.  See `_pre_placed_endpoints`.
    """
    with _pre_placed_endpoints():
        hits = _findings_in_run(_session_without_the_trim(_REFINE))
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


@pytest.mark.skipif(sys.platform == "win32", reason=_WIN_QOR_SKIP)
def test_the_flow_opts_in_and_that_metal_is_gone_at_the_source():
    """The shipped flow: opted in, and clean because the wire is gone.

    The audit goes silent because there is nothing left to report, not because
    the checker stopped looking — so the detailed wirelength must ALSO drop, by
    the amount the findings above reported (8.75 + 5.75 + 2.75 = 17.25, rounded
    in the reported total).

    Asserting both is the point.  A trim that silenced the audit without
    removing wire, or removed wire elsewhere while leaving this, passes either
    assertion on its own.

    BOTH sides run pre-flip.  `set_placed_endpoints` (default on) removes the
    same 17 units at stage 9, so with it the trim saves nothing here and the
    two sides are equal -- that redundancy is real and documented, but it is
    not what THIS test is about, which is that the trim removes the wire.
    """
    with _pre_placed_endpoints():
        trimmed = _session(_REFINE, verbose=True)
        assert _findings_in_run(trimmed) == [], "the trim should remove the metal"
        assert trimmed.detailed_result.num_unplaced == 0

        base = _session_without_the_trim(_REFINE)
    assert _detailed_wl(base) - _detailed_wl(trimmed) == pytest.approx(17, abs=1), \
        (_detailed_wl(base), _detailed_wl(trimmed))
