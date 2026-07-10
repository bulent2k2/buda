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

"""`set_planner_param kPeak` — routability-aware candidate selection
(wishlist-planner "Selection basis" lever 1).

The planner's soft congestion term (kCong) is overflow-only: it is ZERO for
any band below capacity, so candidate ranking cannot tell a band others
filled to 95% from an empty one until it actually bursts.  kPeak adds a
peak-EXISTING-utilization term (max usage/cap over the bands a segment would
use, PRE-charge — the candidate's own width steers which band, but only
others' accumulated load is priced), so a candidate that squeezes into an
already-loaded corridor loses to one that detours around it BEFORE any
overflow materializes.

Scenario: a wide corridor bundle E1→E2 loads the y[0,100] band; the probe
S→D can either share that band (I_H, shortest) or detour above it through
an empty band (U_VHV via the helper block's Hanan row).  Everything fits —
no overflow anywhere, zero NUTS overlaps in every configuration — so the
default model has no signal and picks the shortest route through the loaded
corridor; kPeak is exactly the term that sees the load.

Default OFF: kPeak=0 skips the term entirely (not merely weights it by
zero), so existing flows are bit-identical — the golden corpora guard that.
"""
import io
import contextlib

import buda_cli


def _route(kpeak):
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = ["def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
            # Corridor bundle: E1→E2 loads the y[0,100] band between them.
            "add_block E1 200 0 300 100", "add_block E2 600 0 700 100",
            # Probe bundle S→D: its straight route shares that band.
            "add_block S 0 0 100 100", "add_block D 900 0 1000 100",
            # Helper block above the corridor: provides the Hanan row for a
            # detour band (its position keeps it out of every route).
            "add_block H1 1200 140 1300 200",
            "add_bus wide[8] E1.p E2.p",
            "add_bus probe[4] S.p D.p",
            "run_bundler strict", "generate_topologies"]
    if kpeak > 0:
        cmds.append(f"set_planner_param kPeak {kpeak}")
    cmds += ["run_planner", "run_nuts"]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    sel = {}
    for w in s.bundles:
        b = w.input.original_bundle
        i = w.plan.selected_topology_index
        assert 0 <= i < len(w.input.candidates)
        sel[b.get_net_names()[0]] = w.input.candidates[i].type
    return s, sel


def test_default_shares_the_loaded_band():
    """Without kPeak the loaded corridor is invisible below capacity: the
    probe picks the shortest route straight through it (the wishlist item's
    'routability-blind selection')."""
    s, sel = _route(0)
    assert sel["probe_0"] == "I_H", sel
    assert s.nuts_result.num_overlaps == 0     # fits — which is why kCong=0


def test_kpeak_steers_off_the_loaded_band():
    """With kPeak the probe pays for the corridor's existing load and takes
    the detour through the empty band instead — before any overflow."""
    s, sel = _route(0.2)
    assert sel["probe_0"].startswith("U_"), sel   # detour, not the corridor
    assert s.nuts_result.num_overlaps == 0
    # The corridor bundle itself is unaffected (it planned first, and the
    # empty design gives its own term nothing to price).
    assert sel["wide_0"] == "I_H"


def test_kpeak_off_is_off():
    """kPeak=0 must be indistinguishable from never setting the knob (the
    term is skipped, not multiplied by zero) — same selection either way."""
    _, sel_unset = _route(0)
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ["def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
                  "add_block E1 200 0 300 100", "add_block E2 600 0 700 100",
                  "add_block S 0 0 100 100", "add_block D 900 0 1000 100",
                  "add_block H1 1200 140 1300 200",
                  "add_bus wide[8] E1.p E2.p", "add_bus probe[4] S.p D.p",
                  "run_bundler strict", "generate_topologies",
                  "set_planner_param kPeak 0", "run_planner", "run_nuts"]:
            s.do_command(c)
    sel_zero = {}
    for w in s.bundles:
        i = w.plan.selected_topology_index
        sel_zero[w.input.original_bundle.get_net_names()[0]] = \
            w.input.candidates[i].type
    assert sel_zero == sel_unset


def test_kpeak_param_is_recognized():
    """`set_planner_param kPeak` must not hit the unknown-param warning."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in ("def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
                  "add_block A 0 0 100 100", "add_block B 300 0 400 100",
                  "add_net n0 A.p B.p", "run_bundler strict",
                  "generate_topologies", "set_planner_param kPeak 0.1",
                  "run_planner"):
            s.do_command(c)
    assert "unknown param" not in buf.getvalue()
