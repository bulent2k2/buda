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
import sys
from pathlib import Path

import pytest
from pytest_bdd import scenarios, given, when, then

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

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
