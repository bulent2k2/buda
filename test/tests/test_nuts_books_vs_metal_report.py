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

"""
The `[NUTS] books-vs-metal` line is reported only on real divergence (#516).

The line measures pulled segments whose placed track landed far from the band
the planner charged (wishlist-planner "Charge pulled segments at their
predicted pull target").  It used to be emitted whenever the design had ANY
pulled segment, so a perfectly healthy run announced itself as

    [NUTS] books-vs-metal: 0/2 pulled segment(s) placed >100 units from the
    planner's charged band (worst Δ=11)

— zero divergences, phrased like a finding.  That is the noise which trains a
reader to skip the line on the runs where the count is nonzero and it matters.

The invariant is two-sided, and the second half is the one that matters most
here: silencing the clean case must NOT silence the diagnostic itself.
"""
import contextlib
import io

import buda
import buda_cli


def _run_flow(path):
    """Source a flow and return everything it printed."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        s.do_command(f"source {path}")
    return buf.getvalue()


def _books_lines(out):
    return [ln for ln in out.splitlines() if "books-vs-metal" in ln]


def test_quiet_when_nothing_diverges():
    """A flow with pulled segments but no divergence reports nothing.

    four_blocks_3_bundles has 4 pulled segments, worst Δ=22 — comfortably
    inside the 100-unit threshold, so there is nothing to report.
    """
    lines = _books_lines(_run_flow("flow/four_blocks_3_bundles.buda"))
    assert lines == [], (
        "books-vs-metal reported a clean result as a finding: " + "; ".join(lines)
    )


def test_interval_model_cleans_historical_divergence_vehicles():
    """The anchor-interval pull model (issue #523) ELIMINATED the natural
    divergence on both historical vehicles: stub_order_swap's pulled
    segments (worst Δ=1249 under the old gain-only votes) and pull1's
    (Δ=101) now place within the 100-unit threshold of their charged bands,
    so the line is silent — books and metal genuinely agree.  Pinned as a
    positive claim: this is the model repair working, not the diagnostic
    going quiet."""
    for flow in ("flow/stub_order_swap.buda", "flow/pull1.buda"):
        lines = _books_lines(_run_flow(flow))
        assert lines == [], (flow, lines)


def test_still_reports_real_divergence():
    """The counter-check: gating the clean case must not silence the metric.

    No corpus flow diverges naturally any more (see the test above — and
    stub_order_swap's old Δ=1249 pulls were ENTIRELY the gain-only artifact:
    it has zero pulled segments under the interval model), so the
    divergence is created by DOCTORING THE BOOKS on pull1 (10 genuinely
    pulled segments): after a normal run, one pulled segment's charged band
    (plan.seg_perp) is moved 500 units from its placed track and run_nuts
    re-runs.  A pulled segment's placement outranks seg_perp (NUTS never
    consumes it for pulled segments — the books-vs-metal premise), so the
    metal stays put, the books now lie, and the diagnostic MUST fire.
    """
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
        s.do_command("source flow/pull1.buda")
    doctored = False
    for ts in s.nuts_result.segments:
        if ts.net_pull == 0 or not ts.placed:
            continue
        w = next(w for w in s.bundles
                 if w.input.original_bundle.id == ts.bundle_id)
        sp = list(w.plan.seg_perp)
        if ts.seg_idx >= len(sp):
            # seg_perp unpopulated for this wrapper: pad with the INT_MIN
            # sentinel (= "no charged band"), exactly what the diagnostic
            # skips for the other segments.
            sp += [-(2 ** 31)] * (ts.seg_idx + 1 - len(sp))
        sp[ts.seg_idx] = int(ts.track_position) + 500
        w.plan.seg_perp = sp
        doctored = True
        break
    assert doctored, "fixture lost its pulled segment — pick another flow"
    out = io.StringIO()
    with contextlib.redirect_stdout(out), buda.ostream_redirect():
        s.do_command("run_nuts")
    lines = [ln for ln in out.getvalue().splitlines()
             if "books-vs-metal" in ln]
    assert lines, (
        "books-vs-metal went silent on a genuinely diverging charge — "
        "the diagnostic was suppressed, not just de-noised"
    )


def test_never_reports_a_zero_count():
    """Whenever the line appears at all, its numerator is nonzero.

    Guards the gate directly rather than via either flow's specific numbers,
    so a future threshold or metric change cannot quietly reintroduce a
    '0/N' line through some other path.
    """
    for flow in ("flow/four_blocks_3_bundles.buda",
                 "flow/stub_order_swap.buda",
                 "flow/pull1.buda"):
        for ln in _books_lines(_run_flow(flow)):
            count = ln.split("books-vs-metal:")[1].split("/")[0].strip()
            assert count != "0", f"{flow}: zero-count line emitted: {ln}"
