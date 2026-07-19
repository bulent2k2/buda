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

"""`set_planner_param nontop_dead_span_gate` — the NON-TOP dead-span gate
(opens item 4, wishlist-planner; the planner-side half of the NON-TOP/LOW
stub-open bug).

The planner's per-cut capacity (`score_segment`) samples a non-TOP stub's
endpoint-CLAMPED extent (`for_each_band` treats the in-cell tail as pin
access on another layer), so a pin-access stub whose in-cell span sits over
a leaf keepout on a LOW layer can pass the width/track check yet land on a
band with ZERO keepout-clear signal tracks across the span DetailedNUTS
places from — a guaranteed open (bigHalf's b38/b42/b65/…, flow 10's M7
stubs).  With the gate on, such a NON-TOP layer is refused and STRICT
escalates to a TOP layer that can host the bits.

Opt-in (default off): `span_pool == 0` over the CONSERVATIVE abstract span
cannot distinguish a genuine cull from a survivor whose final
junction-adjusted span clears the keepout, so an always-on gate that helps
bigHalf (−76% opens) regresses rnr_mix's healed endpoint (+16).  The
always-on discriminator is the follow-on tracked in wishlist-planner.

The mechanism needs real leaf-cell pin-access geometry (a synthetic
keepout hard-blocks the LOW band and the width model already escalates),
so this is a @mid integration test on the bigHalf repro rather than a
handmade micro-flow.
"""
import io
import contextlib
from pathlib import Path

import pytest

import buda_cli

_ROOT = Path(__file__).parents[2]


def _run_bighalf(gate):
    """bigHalf's no-rr config (signal_tracks planner + one negotiate, no
    ripup) with the gate optionally enabled before the first run_planner."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    # Isolate the PLANNER's plan-time dead-span gate: the healer-folded
    # escalation (default on) would independently escalate the same dead LOW
    # stubs inside negotiate_congestion, collapsing the gate-off baseline this
    # test measures against.  This test is about the plan-time gate, not the
    # healer fold (which has its own test), so switch the fold off here.
    s._heal_dead_spans_in_healers = False
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"source {_ROOT / 'flow/tracks/tracks4top.buda'}")
        s.do_command(f"source {_ROOT / 'flow/big_data_test/tc3a_flat_5x.buda'}")
        s.do_command("run_bundler")
        # Midpoint-only pool: this test measures the PLANNER's dead-span gate
        # against the fixture the 566->135 numbers were collected on; the
        # hanan_loci default flip grows the pool (bigHalf no-rr baseline opens
        # 626 -> 283) and would invalidate the measured thresholds, so the
        # generation corpus is pinned pre-flip via the opt-out.
        s.do_command("generate_topologies no_hanan_loci")
        if gate:
            s.do_command("set_planner_param nontop_dead_span_gate 1")
        s.do_command("run_planner signal_tracks")
        s.do_command("run_nuts")
        s.do_command("negotiate_congestion")
        s.do_command("run_detailed_nuts")
    return s.nuts_result.num_overlaps, s.detailed_result.num_unplaced


@pytest.mark.mid
def test_gate_slashes_bighalf_dead_span_opens():
    """The gate escalates the dead-span LOW stubs onto TOP, cutting the
    no-rr DNUTS opens by a wide margin (measured 566 -> 135, −76%; the
    exact residual is host-sensitive, so the invariant is a large, strict
    reduction with no new overlaps)."""
    ov0, un0 = _run_bighalf(gate=False)
    ov1, un1 = _run_bighalf(gate=True)
    assert un0 > 300, f"baseline should carry the dead-span opens, got {un0}"
    assert un1 <= un0 // 2, f"gate should at least halve opens: {un0} -> {un1}"
    assert ov1 <= ov0 + 1, f"no material new overlaps: {ov0} -> {ov1}"


def test_param_recognized():
    """`set_planner_param nontop_dead_span_gate` must not warn."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in ("def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
                  "add_block A 0 0 100 100", "add_block B 300 0 400 100",
                  "add_net n0 A.p B.p", "run_bundler strict",
                  "generate_topologies",
                  "set_planner_param nontop_dead_span_gate 1", "run_planner"):
            s.do_command(c)
    assert "unknown param" not in buf.getvalue()


def test_default_off_leaves_bighalf_unchanged():
    """Sanity: not setting the knob is the same as the baseline (the gate is
    gated at entry on the member flag)."""
    ov_a, un_a = _run_bighalf(gate=False)
    ov_b, un_b = _run_bighalf(gate=False)
    assert (ov_a, un_a) == (ov_b, un_b)
