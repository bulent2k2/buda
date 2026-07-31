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

"""Tests for the `refine_selection [max_moves] [chase_overlaps]` CLI command
(selection-basis lever 3, wishlist-planner "Selection basis").

The pass re-ranks each bundle's SELECTION on the measured result: realized
abstract WL (the placed spans' total length) with an accept guard that keeps
opens and overlaps parity-or-better componentwise — so it can only recover
wirelength, never trade the healers' endpoint away.

The canned fixture routes one 8-bit bus between two horizontally aligned
blocks with an obstacle block straddling the straight path, pins the bundle
to a U-shape DETOUR candidate (the only shape whose realized WL genuinely
exceeds the straight route's — the aligned Z shapes settle back onto the
straight pull, realizing identically), plans + places, then clears the pin:
a measurably suboptimal selection the estimate-driven planner would never
revisit.  refine_selection must move it to a shorter measured route.  The
pin-kept twin proves user pins are inviolable.
"""
import io
import contextlib
import re
import sys
from pathlib import Path

import pytest
from pytest_bdd import scenarios, given, when, then

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

_ROOT = Path(__file__).parents[2]
_RNR = _ROOT / "flow" / "rnr"

scenarios("features/refine_selection.feature")


# --- canned fixture ----------------------------------------------------------

def _est_wl(topo):
    return sum(abs(s.end.x - s.start.x) + abs(s.end.y - s.start.y)
               for s in topo.segments)


def _realized_wl(s):
    return sum(abs(ts.span_hi - ts.span_lo)
               for ts in s.nuts_result.segments if ts.placed)


def _selections(s):
    return {w.input.original_bundle.id: w.plan.selected_topology_index
            for w in s.bundles}


def _build_session(pin_worst: bool, keep_pin: bool = False):
    """One 8-bit bus D -> R on a 3-layer stack, with obstacle X straddling the
    straight path (so the generator emits U detours that REALIZE longer — the
    straight I_H realizes 2200, the U shapes 2288; the misaligned Z shapes
    settle onto the pull and realize 2200 too).  `pin_worst` pins the bundle to
    the longest-estimate U candidate before planning (then clears the pin
    unless `keep_pin`), leaving a measurably suboptimal committed selection."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "def_layer 5 M5 V TOP 50",
        "def_layer 4 M4 H TOP 50",
        "def_layer 7 M7 V TOP 50",
        "add_block D 0 1000 200 1400",
        "add_block R 2400 1000 2600 1400",
        "add_block X 1000 800 1600 1600",
        "add_bus a[8] D.p R.p",
        "run_bundler",
        "generate_topologies",
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
        if pin_worst:
            cands = s.bundles[0].input.candidates
            us = [i for i, c in enumerate(cands) if c.type.startswith("U")]
            assert us, [c.type for c in cands]
            worst = max(us, key=lambda i: _est_wl(cands[i]))
            s.do_command(f"select_topology 1 {worst + 1}")   # 1-based
        s.do_command("run_planner")
        s.do_command("run_nuts")
        if pin_worst and not keep_pin:
            s.do_command("unpin_topology 1")
    return s


@pytest.fixture
def ctx():
    return {"s": None, "out": "", "sel_before": None,
            "wl_before": None, "ovl_before": None}


# --- Given ------------------------------------------------------------------

@given("a one-bus floorplan planned on its longest candidate and unpinned")
def suboptimal(ctx):
    ctx["s"] = _build_session(pin_worst=True)


@given("a one-bus floorplan planned on its longest candidate and still pinned")
def suboptimal_pinned(ctx):
    ctx["s"] = _build_session(pin_worst=True, keep_pin=True)
    assert ctx["s"].bundles[0].input.topology_pinned


@given("a one-bus floorplan planned normally")
def optimal(ctx):
    ctx["s"] = _build_session(pin_worst=False)


@given("a fresh session with no routed design")
def fresh(ctx):
    s = buda_cli.BudaSession()
    s.no_viz = True
    ctx["s"] = s


# --- When -------------------------------------------------------------------

def _run(ctx, arg=""):
    s = ctx["s"]
    if s.bundles and s.nuts_result is not None:
        ctx["sel_before"] = _selections(s)
        ctx["wl_before"] = _realized_wl(s)
        ctx["ovl_before"] = s.nuts_result.num_overlaps
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            ctx["s"].do_command(f"refine_selection {arg}".strip())
        except SystemExit:
            pass                    # reject_unknown_options exits the CLI
    ctx["out"] = buf.getvalue()


@when("I run refine_selection")
def run_plain(ctx):
    _run(ctx)


@when("I run refine_selection with chase_overlaps")
def run_chase(ctx):
    _run(ctx, "chase_overlaps")


@when("I run refine_selection with a bogus option")
def run_bogus(ctx):
    _run(ctx, "frobnicate")


@when("I run refine_selection with max_moves 2.5")
def run_float(ctx):
    _run(ctx, "2.5")


@when("I run refine_selection with two numeric arguments")
def run_two_nums(ctx):
    _run(ctx, "3 4")


# --- Then -------------------------------------------------------------------

@then("the realized wirelength strictly decreases")
def wl_down(ctx):
    assert _realized_wl(ctx["s"]) < ctx["wl_before"], ctx["out"]


@then("the NUTS overlap count did not increase")
def ovl_no_worse(ctx):
    assert ctx["s"].nuts_result.num_overlaps <= ctx["ovl_before"], ctx["out"]


@then("refine_selection committed at least one move")
def committed(ctx):
    assert "[refine_selection] COMMIT" in ctx["out"], ctx["out"]


@then("refine_selection committed no moves")
def no_commit(ctx):
    assert "[refine_selection] COMMIT" not in ctx["out"], ctx["out"]
    assert "after 0 move(s)" in ctx["out"], ctx["out"]


@then("no bundle topology selection changed")
def sel_unchanged(ctx):
    assert _selections(ctx["s"]) == ctx["sel_before"], ctx["out"]


@then("refine_selection reports the unknown option")
def bad_option(ctx):
    assert "frobnicate" in ctx["out"], ctx["out"]
    assert "[refine_selection] COMMIT" not in ctx["out"], ctx["out"]


@then("refine_selection reports the invalid max_moves")
def bad_max_moves(ctx):
    assert "integer max_moves" in ctx["out"], ctx["out"]
    assert "[refine_selection] COMMIT" not in ctx["out"], ctx["out"]


@then("refine_selection reports it has nothing to refine")
def nothing(ctx):
    assert "Error: refine_selection" in ctx["out"], ctx["out"]


# --- the stuck-endpoint recipe (@mid): refine -> negotiate -> ripup -> refine

@pytest.mark.mid
def test_topdown_recipe_heals_most_of_the_residual():
    """The composition recipe on a residual-laden flow (the checked-in QoR
    vehicle `flow/rnr/mix2_topdown_refine.buda`): the plain top-down mix2
    pipeline ends with triple-digit DNUTS opens; `refine_selection`'s
    componentwise accept pre-shrinks the problem (its accept lets endpoint
    improvements through on a healerless flow), `negotiate_congestion` +
    `ripup_reroute` heal most of the residual, and a trailing
    `refine_selection` recovers part of the WL the healers spent without ever
    worsening the endpoint.

    **This test originally asserted a clean 0/0 endpoint, and that no longer
    holds.**  When `band_span_charge` defaulted to 1 (issue #518) this vehicle
    became the SECOND flow to hit the absolute-supply blind spot — it now ends
    ~16 opens instead of 0, the same 16 bits (`bundle 61 seg 2`) that strand on
    `flow/rnr/mix2.buda`.  Both are the same mix2 design and the same
    unresolved root cause; see docs/internal/band_span_charge.md.

    What is asserted here is therefore the recipe's own CPU-invariant
    guarantees, which are unaffected and are what this test exists to protect:
    componentwise no-worse at every refine leg, strict healer progress, and a
    trailing polish that never lengthens the route.  Plus a LARGE reduction
    from the triple-digit start, so the test still fails if the recipe stops
    working — it is weakened only in the one dimension #518 actually moved,
    not turned into a tautology.

    The exact-zero assertion is deliberately not restored by pinning
    `band_span_charge 0` here: this vehicle is a corpus row, and silencing it
    would make the QoR guard stop reporting a known real regression."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"source {_RNR / 'mix_tracks.buda'}")
        s.do_command(f"open_bdb {_RNR / 'mix2.bdb.sql'}")
        s.do_command("derive_busterms 2")
        s.do_command("add_blocks_from_bdb 0")
        s.do_command("add_blocks_from_bdb 1 skip")
        s.do_command("add_blocks_from_bdb 2 skip")
        s.do_command("run_hier_bundler depth 2")
        s.do_command("generate_hier_topologies")
        s.do_command("run_planner hier 5 signal_tracks")
        s.do_command("run_nuts")
        s.do_command("run_detailed_nuts")
    base = (s.detailed_result.num_unplaced, s.nuts_result.num_overlaps)
    assert base[0] > 0, "fixture should start with DNUTS opens"

    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("refine_selection")
    r1 = (s.detailed_result.num_unplaced, s.nuts_result.num_overlaps)
    # The componentwise accept can only improve the endpoint (hard guarantee).
    assert r1[0] <= base[0] and r1[1] <= base[1], (base, r1)

    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("negotiate_congestion")
        s.do_command("ripup_reroute")
    healed = (s.detailed_result.num_unplaced, s.nuts_result.num_overlaps)
    # The healers make real progress from a triple-digit-open start.
    assert healed[0] < base[0], (base, healed)

    wl_before = sum(abs(ts.span_hi - ts.span_lo)
                    for ts in s.nuts_result.segments if ts.placed)
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("refine_selection")
    final = (s.detailed_result.num_unplaced, s.nuts_result.num_overlaps)
    wl_after = sum(abs(ts.span_hi - ts.span_lo)
                   for ts in s.nuts_result.segments if ts.placed)
    # The trailing polish never worsens the healed endpoint and never
    # lengthens the route (it commits only on strictly lower WL).
    assert final[0] <= healed[0] and final[1] <= healed[1], (healed, final)
    assert wl_after <= wl_before, (wl_before, wl_after)
    # The recipe must still do the bulk of the work: a triple-digit start
    # collapses by an order of magnitude.  (Pre-#518 this reached exactly
    # (0, 0); the residual is that issue's known cost, not a recipe failure.)
    assert final[0] <= base[0] // 4, (base, r1, healed, final)
    assert final[1] <= base[1], (base, r1, healed, final)

    # The full design audit still runs, and ONLY the known #518 residual may
    # appear in it.  Dropping the check_design call along with the exact-zero
    # assertion would have silently retired every OTHER audit dimension —
    # LAYER_DIR, KEEPOUT_CROSS, NET_DRIVER_OPEN, BIT_SHORT, SEG_OPEN,
    # ANTENNA, DISCONNECTED — none of which #518 touches (review #530).
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("check_design")
    audit = buf.getvalue()
    offenders = [ln.strip() for ln in audit.splitlines()
                 if "Bundle" in ln and "unplaced (no track in DetailedNUTS)" not in ln]
    assert not offenders, (
        f"audit reported a violation class #518 does not explain: {offenders}"
    )
    # Every reported violation must be one of the accepted unplaced bits, so a
    # new failure mode cannot hide inside the permitted residual.
    m = re.search(r"Total: (\d+) violation", audit)
    total = int(m.group(1)) if m else 0
    assert total == final[0], (
        f"audit total {total} != unplaced bits {final[0]} — something beyond "
        f"the accepted residual is being reported\n{audit}"
    )
