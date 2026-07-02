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

"""Tests for the `ripup_reroute [max_iter]` CLI command.

After run_nuts (stage a) or run_detailed_nuts (stage b) the congestion planner can
leave a NUTS overlap / DNUTS open it did not predict (its band model reports
overflow=0). `ripup_reroute` reads the *actual* overlaps/opens, re-routes a
contending bundle to an alternate topology, re-runs the pipeline, and keeps moves
that reduce the metric.

Canned fast scenarios use a tiny single-H-layer floorplan where two buses pinned
to their straight `I_H` candidate collide in a keepout-narrowed band; the re-route
moves one bundle to an `L_HV` shape and the overlap clears.  Stage b (DNUTS opens)
is hard to force deterministically in a tiny floorplan, so it is covered by the
big2 `@mid` integration test (the validated 60 -> 0 case).
"""
import io
import contextlib
import os
import sys
from pathlib import Path

import pytest
from pytest_bdd import scenarios, given, when, then, parsers

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

_ROOT = Path(__file__).parents[2]
_BIG2 = _ROOT / "flow" / "big_data_test" / "big2"

scenarios("features/ripup_reroute.feature")


# --- canned tiny congestion fixture -----------------------------------------

def _build_session(narrow: bool):
    """Two 8-bit buses from D1/D2 into a shared receiver R on a single H layer
    (M4).  The blocks are tall, so the M4 band is roomy and the two `I_H` routes
    separate cleanly.  When `narrow`, keepouts leave only a 40-unit open band at
    the routes' y (~1200), so the two buses collide into one NUTS overlap."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "def_layer 5 M5 V TOP 50",
        "def_layer 4 M4 H TOP 50",     # the only H layer
        "def_layer 7 M7 V TOP 50",
        "add_block D1 0 1000 200 1400",
        "add_block D2 400 1000 600 1400",
        "add_block R 2400 1000 2600 1400",
        "add_bus a[8] D1.p R.p",
        "add_bus b[8] D2.p R.p",
    ]
    if narrow:
        cmds += ["add_keepout 0 1220 3000 4000 4",     # block M4 above the open band
                 "add_keepout 0 0 3000 1180 4"]          # and below → only [1180,1220]
    cmds += ["run_bundler", "generate_topologies",
             "select_topology 1 1", "select_topology 2 1",   # both straight I_H
             "run_planner", "run_nuts"]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def _selections(s):
    return {w.input.original_bundle.id: w.plan.selected_topology_index
            for w in s.bundles}


@pytest.fixture
def ctx():
    return {"s": None, "out": "", "sel_before": None}


# --- Given ------------------------------------------------------------------

@given("a congested two-bus floorplan pinned into one NUTS overlap")
def congested(ctx):
    ctx["s"] = _build_session(narrow=True)
    assert ctx["s"].nuts_result.num_overlaps > 0, "fixture should start congested"


@given("a clean two-bus floorplan with no NUTS overlap")
def clean(ctx):
    ctx["s"] = _build_session(narrow=False)
    assert ctx["s"].nuts_result.num_overlaps == 0, "fixture should start clean"


# --- When -------------------------------------------------------------------

def _run_ripup(ctx, arg=""):
    ctx["sel_before"] = _selections(ctx["s"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ctx["s"].do_command(f"ripup_reroute {arg}".strip())
    ctx["out"] = buf.getvalue()


@when("I run ripup_reroute after run_nuts")
def run_after_nuts(ctx):
    _run_ripup(ctx)


@when(parsers.parse("I run ripup_reroute with max_iter {n:d}"))
def run_with_max_iter(ctx, n):
    _run_ripup(ctx, str(n))


# --- Then -------------------------------------------------------------------

@then("the NUTS overlap count is 0")
def overlaps_zero(ctx):
    assert ctx["s"].nuts_result.num_overlaps == 0, ctx["out"]


@then("ripup_reroute re-pinned at least one bundle")
def repinned(ctx):
    assert _selections(ctx["s"]) != ctx["sel_before"], ctx["out"]


@then("ripup_reroute reports the metric was already 0")
def reports_noop(ctx):
    assert "metric already 0" in ctx["out"], ctx["out"]


@then("no bundle topology selection changed")
def no_change(ctx):
    assert _selections(ctx["s"]) == ctx["sel_before"], ctx["out"]


def _distinct_iterations(out):
    """Count distinct `iter K` indices in ripup output (each iteration emits
    several lines now — per-contender heartbeats + a COMMIT)."""
    return {line.split(":", 1)[0].split()[-1]
            for line in out.splitlines()
            if line.startswith("[ripup_reroute] iter ")}


@then("ripup_reroute performed at most 1 iteration")
def at_most_one_iter(ctx):
    assert len(_distinct_iterations(ctx["out"])) <= 1, ctx["out"]


# --- big2 integration (@mid): the validated real-world cases ----------------

def _big2_to_stage(stage):
    """Drive big2 in-process up to run_nuts (stage a) or run_detailed_nuts
    (stage b) and return the session."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"source {_BIG2 / 'tracks4top.buda'}")
        s.do_command(f"source {_BIG2 / 'tc3b_flat_x5.buda'}")
        s.do_command("run_bundler")
        s.do_command("generate_topologies")
        s.do_command("run_planner")
        s.do_command("run_nuts")
        if stage == "b":
            s.do_command("run_detailed_nuts")
    return s


@pytest.mark.mid
def test_big2_stage_b_clears_opens():
    """big2's remaining DNUTS opens are driven to 0 by ripup_reroute (validated
    60 -> 0 on the reference host).

    The starting count is machine-sensitive — the double-based NUTS math rounds
    slightly differently across CPUs under -march=native — so we assert a nonzero
    baseline rather than exactly 60.  The real guard is that ripup clears them to
    0 (CPU-invariant).  See -ffp-contract=off in CMakeLists (the build-side half
    of this portability fix)."""
    s = _big2_to_stage("b")
    base = s.detailed_result.num_unplaced
    assert base > 0, f"expected a nonzero DNUTS-open baseline to clear, got {base}"
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("ripup_reroute")
    assert s.detailed_result.num_unplaced == 0
    assert s.nuts_result.num_overlaps <= 9


@pytest.mark.mid
def test_big2_stage_a_reduces_overlaps():
    """big2's NUTS overlaps are driven down (validated 9 -> 0)."""
    s = _big2_to_stage("a")
    assert s.detailed_result is None       # stage a (no detailed run yet)
    base = s.nuts_result.num_overlaps
    assert base > 0
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("ripup_reroute")
    assert s.nuts_result.num_overlaps < base


@pytest.mark.mid
def test_big2_stage_b_preserves_hi_lo_bit_order():
    """ripup_reroute's stage-b replay must keep a HI_LO bit-order selection.

    Regression for the bug where the per-trial / committed DNUTS re-run went
    through `do_command("run_detailed_nuts")`, which resets `_detailed_bit_order`
    to LO_HI before parsing its (absent) arg — silently flipping a HI_LO flow."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"source {_BIG2 / 'tracks4top.buda'}")
        s.do_command(f"source {_BIG2 / 'tc3b_flat_x5.buda'}")
        s.do_command("run_bundler")
        s.do_command("generate_topologies")
        s.do_command("run_planner")
        s.do_command("run_nuts")
        s.do_command("run_detailed_nuts hi_lo")
    assert s._detailed_bit_order == "HI_LO"
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("ripup_reroute")
    assert s._detailed_bit_order == "HI_LO", "ripup_reroute flipped the bit order"
    assert s.detailed_result.num_unplaced == 0


@pytest.mark.mid
def test_big2_max_iter_bounds_moves():
    """max_iter caps the outer loop: with 1, at most one iteration runs."""
    s = _big2_to_stage("a")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("ripup_reroute 1")
    out = buf.getvalue()
    assert len(_distinct_iterations(out)) <= 1, out


# --- hier flow (@mid): ripup_reroute re-routes per-instance wrappers ---------
# After `run_planner hier`, self.bundles IS the expanded per-instance list, so
# ripup re-pins a single instance and re-plans the expanded wrappers in place
# (via _rr_replan_hier — no re-expansion).  The two hbundles flows below are the
# clean (no-op) and congested (real re-route) hier vehicles.

_HBUNDLES = _ROOT / "flow" / "hbundles"


def _source_hier_flow(name):
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"source {_HBUNDLES / name}")
    return s


@pytest.mark.mid
def test_hier_supported_and_noop_when_clean():
    """A clean hier flow: ripup_reroute is now supported (no flat-only error) and
    is a no-op."""
    s = _source_hier_flow("01_pipeline_hier.buda")
    assert s._planner_is_hier, "01_pipeline_hier should leave the session in hier mode"
    assert s.nuts_result.num_overlaps == 0 and s.detailed_result.num_unplaced == 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("ripup_reroute")
    out = buf.getvalue()
    assert "flat-flow only" not in out, "hier must no longer be rejected"
    assert "metric already 0" in out, out
    assert s.detailed_result.num_unplaced == 0


@pytest.mark.mid
def test_hier_stage_b_clears_opens():
    """A congested hier flow: ripup re-routes a per-instance wrapper and drives the
    DNUTS opens to zero (validated 8 -> 0 by re-pinning expanded bundle 26)."""
    s = _source_hier_flow("06_multipin_stress.buda")
    assert s._planner_is_hier
    base = s.detailed_result.num_unplaced
    assert base > 0, "06_multipin_stress should start with DNUTS opens"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("ripup_reroute")
    out = buf.getvalue()
    assert "flat-flow only" not in out
    assert s.detailed_result.num_unplaced < base, out


# --- large hier repro (@mid): responsiveness / bounded-cost guardrail --------

_RNR = _ROOT / "flow" / "rnr"


@pytest.mark.mid
def test_hier_large_repro_progress_and_bounded():
    """The `mix.buda` hier repro (~100 expanded bundles, 21 NUTS overlaps) was the
    case that *looked* like a hang: best-over-all-contenders cost ~contenders*
    candidates full pipeline re-runs per iteration, silently.  Guard the fix:
    first-improving-contender keeps each iteration cheap, emits a per-contender
    heartbeat, makes progress, and honors max_iter."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"source {_RNR / 'mix_tracks.buda'}")
        s.do_command(f"open_bdb {_RNR / 'mix.bdb.sql'}")
        s.do_command("derive_busterms 2")
        s.do_command("add_blocks_from_bdb 0")
        s.do_command("add_blocks_from_bdb 1 skip")
        s.do_command("add_blocks_from_bdb 2 skip")
        s.do_command("run_hier_bundler depth 2")
        s.do_command("generate_hier_topologies")
        s.do_command("run_planner hier 5")
        s.do_command("run_nuts")
    assert s._planner_is_hier
    base = s.nuts_result.num_overlaps
    assert base > 0, "mix repro should start with NUTS overlaps"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("ripup_reroute 2")          # cap iterations: bounded cost
    out = buf.getvalue()
    assert "contender" in out, out               # per-contender progress is visible
    assert len(_distinct_iterations(out)) <= 2, out   # max_iter honored
    assert s.nuts_result.num_overlaps < base, out     # made real progress
