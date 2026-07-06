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
    """big2's DNUTS opens are driven down by ripup_reroute (validated 60 -> 0 on
    the ARM reference host; 132 -> 72 on x86).

    Both the starting count AND whether it reaches exactly 0 are machine-
    sensitive: the double-based NUTS math rounds slightly differently across CPUs
    under -march=native, so a design at the packing limit strands a few residual
    TOP-layer-oversubscription opens on some hosts that the reference clears.
    (See -ffp-contract=off in CMakeLists, and test_flow_scripts.py's tc3a note —
    'a bundle can tip into a NUTS open on some hosts'.)  So the CPU-invariant
    guard is that ripup makes real progress (strictly reduces the opens), the
    same property test_big2_stage_a_reduces_overlaps and the hier stage-b test
    assert — not an exact clear-to-zero.  On the reference host it does reach 0."""
    s = _big2_to_stage("b")
    base = s.detailed_result.num_unplaced
    assert base > 0, f"expected a nonzero DNUTS-open baseline to reduce, got {base}"
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("ripup_reroute")
    assert s.detailed_result.num_unplaced < base   # ripup makes real progress
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
def test_big2_ripup_flip_move_never_regresses():
    """The flip move source (step 4b) is an ADD-ON to the existing index-alternate
    ripup: it must never make big2 worse.  It IS exercised on this design — a
    selected MST candidate's edge leg contends and _rr_flip_edges surfaces it — but
    WHICH segment contends is FP/CPU/environment-sensitive (the same reason the
    tests above assert 'reduced', not an exact count), so we cannot deterministically
    assert a flip is tried.  Instead assert the invariant that always holds: ripup
    still drives the overlaps down, whether the winning moves are index or flip.
    (The flip's involution + no-false-flip gating are pinned deterministically by
    the fast-tier _mst_session tests.)"""
    s = _big2_to_stage("a")
    base = s.nuts_result.num_overlaps
    assert base > 0
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("ripup_reroute 40")
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
    base = s.detailed_result.num_unplaced
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("ripup_reroute")
    # This test's guard is the bit-order preservation; the exact placed count
    # after ripup is CPU-sensitive (see test_big2_stage_b_clears_opens), so only
    # require that ripup did not regress placement while keeping HI_LO.
    assert s._detailed_bit_order == "HI_LO", "ripup_reroute flipped the bit order"
    assert s.detailed_result.num_unplaced <= base


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


@pytest.mark.mid
def test_snapshot_restores_overwritten_dogleg_slot():
    """A trial's NUTS re-solve OVERWRITES an adopted dogleg's split candidate
    in place (cands[slot] = ..., same count, new content), which the ncand trim
    alone cannot undo — a rejected move would poison later trials and the final
    DNUTS/visualization with unaccepted geometry (Codex review, PR #158).
    Guard the mechanism directly: snapshot, overwrite the slot, restore, and
    the committed split geometry must be back."""
    s = _big2_to_stage("a")
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("ripup_reroute")            # may adopt doglegs en route
    if not s._dogleg_slot:                        # synthesize one: the guard is
        w0 = s.bundles[0]                         # about snapshot mechanics, not
        cands = w0.input.candidates               # how the dogleg came to be
        cands.append(cands[0])
        w0.input.candidates = cands
        s._dogleg_slot[w0.input.original_bundle.id] = len(cands) - 1
        s._dogleg_originals[w0.input.original_bundle.id] = 0
    bid, slot = next(iter(s._dogleg_slot.items()))
    w = s._rr_wrapper(bid)
    before = w.input.candidates[slot]
    snap = s._rr_snapshot()
    # Simulate the trial-side in-place overwrite with a distinguishable topology.
    poisoned = w.input.candidates[(slot + 1) % len(w.input.candidates)]
    cands = w.input.candidates
    cands[slot] = poisoned
    w.input.candidates = cands
    assert w.input.candidates[slot].type == poisoned.type
    s._rr_restore(snap)
    restored = w.input.candidates[slot]
    assert restored.type == before.type
    assert len(restored.segments) == len(before.segments)
    for rs, bs in zip(restored.segments, before.segments):
        assert (rs.start.x, rs.start.y, rs.end.x, rs.end.y) == \
               (bs.start.x, bs.start.y, bs.end.x, bs.end.y)


# ---- negotiate_congestion: measured-congestion feedback (band injection) ----

@pytest.mark.mid
def test_negotiate_requires_pipeline_state():
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("negotiate_congestion")
    assert "Error" in buf.getvalue()


@pytest.mark.mid
def test_negotiate_noop_when_clean():
    """With zero NUTS overlaps the command reports and does nothing."""
    s = _build_session(narrow=False)           # roomy band: no overlap
    assert s.nuts_result.num_overlaps == 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("negotiate_congestion")
    assert "already 0" in buf.getvalue()


@pytest.mark.mid
def test_negotiate_reroutes_canned_overlap():
    """The canned two-bus collision: injecting the measured overlap's bands
    re-prices the contended window, and the planner steers one bus onto an
    alternate topology by COST — no per-candidate NUTS trials.  The pinned
    selections must change (negotiation unpins) and the overlap must clear."""
    s = _build_session(narrow=True)            # forced single NUTS overlap
    assert s.nuts_result.num_overlaps > 0
    before = _selections(s)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("negotiate_congestion")
    out = buf.getvalue()
    assert s.nuts_result.num_overlaps == 0, out
    assert _selections(s) != before, out       # a real re-route, not luck


@pytest.mark.mid
def test_negotiate_big2_then_ripup_clears_overlaps():
    """The headline v1 validation (wishlist-ripup item 1): replaying big2's
    measured overlaps into the planner as band demand lets its own cost model
    steer the offenders off the contended bands — most of the 9 overlaps clear
    in a few sub-second negotiate iterations (no per-candidate NUTS trials),
    and the ripup hill-climb finishes the residual to 0."""
    s = _big2_to_stage("a")
    base = s.nuts_result.num_overlaps
    assert base > 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("negotiate_congestion")
    after_neg = s.nuts_result.num_overlaps
    assert after_neg < base, buf.getvalue()    # negotiation made real progress
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("ripup_reroute")
    assert s.nuts_result.num_overlaps == 0     # combined pipeline: fully clean


@pytest.mark.mid
def test_negotiate_clears_stale_seg_layer_pins():
    """A sidecar/visualizer selection can leave per-segment layer pins
    (input.pinned_seg_layers) that plan_bundle applies to EVERY candidate
    regardless of topology_pinned — a stale pin would force layers onto
    whatever topology negotiation picks (even H/V mismatches that charge no
    cuts).  Negotiation must drop the layer pins along with the topology pin
    for every bundle it re-plans (Codex review, PR #160)."""
    s = _build_session(narrow=True)            # forced single NUTS overlap
    assert s.nuts_result.num_overlaps > 0
    for w in s.bundles:                        # simulate sidecar layer pins on
        w.input.pinned_seg_layers = [4]        # the pinned I_H candidates
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("negotiate_congestion")
    out = buf.getvalue()
    assert s.nuts_result.num_overlaps == 0, out
    for w in s.bundles:                        # both sides were re-planned:
        assert list(w.input.pinned_seg_layers) == [], \
            "stale per-segment layer pin survived negotiation"


# ---- item 5: deterministic tiny stage-b (DNUTS-open) canned fixture ---------

def _build_dnuts_open_session():
    """Deterministic stage-b fixture (wishlist-ripup item 5): an all-POWER
    add_grid_override kills M4's signal tracks exactly under the pinned L_HV
    trunk's Hanan window (x[600,2600] y[900,1550]), so DetailedNUTS finds 0
    tracks and all 8 bits open — while the alternate L_VH's trunk (y>=1600)
    runs through healthy pattern.  The planner is width-based here (no
    signal_tracks mode), so it CANNOT see the dead corridor — precisely the
    Gap-A blindness ripup/negotiation exist to repair.  Fast tier: no big2."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "def_layer 4 M4 H TOP 50",
        "def_layer 5 M5 V TOP 50",
        "def_track_pattern 4 0 SIGNAL 1 4",
        "def_track_pattern 5 0 SIGNAL 1 4",
        "add_grid_override 4 600 900 2600 1550 0 POWER 2 3",
        "add_block D1 0 1000 200 1400",
        "add_block R 2400 1600 2600 2000",
        "add_bus a[8] D1.p R.p",
        "run_bundler", "generate_topologies",
        "select_topology 1 1",                 # pin L_HV through the dead corridor
        "run_planner", "run_nuts", "run_detailed_nuts",
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def test_canned_stage_b_open_is_deterministic():
    """The fixture itself: 8 bits open (0 signal tracks in the dead window),
    zero NUTS overlaps — a pure stage-b case, CPU-invariant by construction
    (pattern arithmetic, no packing ties)."""
    s = _build_dnuts_open_session()
    assert s.detailed_result.num_unplaced == 8
    assert s.nuts_result.num_overlaps == 0


def test_canned_stage_b_ripup_clears_open():
    """Stage-b ripup on the canned fixture: one alternate-candidate trial
    (L_VH, healthy tracks) clears all 8 opens — fast-tier coverage of the
    stage-b loop that previously only big2 (@mid) exercised."""
    s = _build_dnuts_open_session()
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("ripup_reroute")
    assert s.detailed_result.num_unplaced == 0
    assert s.nuts_result.num_overlaps == 0


def test_canned_stage_b_negotiate_clears_open():
    """Stage-b negotiation on the canned fixture (item 1 v2a): injecting the
    open segment's window teaches the width-blind planner about the dead band,
    and ONE cost-driven iteration re-routes the bundle — no per-candidate
    trials.  The deterministic counterpart of the big2/hier measurements."""
    s = _build_dnuts_open_session()
    assert s.detailed_result.num_unplaced == 8
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("negotiate_congestion")
    out = buf.getvalue()
    assert s.detailed_result.num_unplaced == 0, out
    assert s.nuts_result.num_overlaps == 0, out
    assert "stage b" in out, out


def test_rerun_nuts_invalidates_stale_detailed_result():
    """Re-running run_nuts after a detailed solve must drop the (now stale)
    detailed result, so stage detection (ripup_reroute / negotiate_congestion)
    returns to stage a instead of negotiating against a detailed route of the
    PREVIOUS abstract solve (Codex review, PR #161)."""
    s = _build_dnuts_open_session()
    assert s.detailed_result is not None
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_nuts")               # fresh abstract solve
    assert s.detailed_result is None, \
        "stale detailed result survived a re-run run_nuts"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("negotiate_congestion")
    assert "stage a" in buf.getvalue()         # back to abstract negotiation


# ── Step 4b: per-edge MST L/Z flip as a ripup move source ────────────────────
#
# The ripup loop now tries, per contended bundle, its index-alternate candidates
# AND per-edge L/Z flips of a SELECTED MST candidate's *contended* edges
# (_rr_flip_edges -> _rr_apply_move('flip', ...)).  These tests pin the detection
# gate (no false flips), the involution-based undo, and — crucially — that an
# in-place flip PERSISTS to the wrapper's candidate so the replan measures it
# (w.input.candidates returns a list of *references* to the C++ Topology objects;
# only structural changes like dogleg append/delete need a write-back).

def _mst_session():
    """A 4-block fan-out bus whose candidate list includes edge-tagged MST
    shapes, planned + NUTS-solved (roomy bands -> 0 overlaps)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "def_layer 4 M4 H TOP 50",
        "def_layer 5 M5 V TOP 50",
        "add_block A 0 0 100 100",
        "add_block B 800 0 900 100",
        "add_block C 800 800 900 900",
        "add_block D 0 800 100 900",
        "add_bus n[8] A.p B.p,C.p,D.p",
        "run_bundler", "generate_topologies",
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


def _pin(s, want_type):
    """Pin bundle 0 to the first candidate whose type matches want_type (exact
    for non-MST, substring for MST families); return (wrapper, sel)."""
    w = s.bundles[0]
    for i, c in enumerate(w.input.candidates):
        if c.type == want_type or (want_type == "MST" and "MST" in c.type):
            w.plan.selected_topology_index = i
            w.input.topology_pinned = True
            return w, i
    raise AssertionError(f"no {want_type} candidate; got "
                         f"{[c.type for c in w.input.candidates]}")


def test_rr_flip_edges_empty_for_non_mst_selection():
    """A non-MST selected candidate exposes no flip edges (only MST shapes carry
    edge tags), so the flip move source never fires for it."""
    s = _mst_session()
    # An L_HV / I_H style 2-pin-ish candidate has no '+MST' / MST_ type.
    w = s.bundles[0]
    non_mst = next((i for i, c in enumerate(w.input.candidates)
                    if "MST" not in c.type), None)
    assert non_mst is not None
    w.plan.selected_topology_index = non_mst
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_planner")
        s.do_command("run_nuts")
    # force the selection back (planner may have moved it)
    w.plan.selected_topology_index = non_mst
    assert s._rr_flip_edges(w, 'a') == []


def test_rr_flip_edges_empty_without_contention():
    """An MST selection with NO overlap touching it yields no flip edges — flips
    are attempted only for edges an actual overlap/open lands on."""
    s = _mst_session()
    w, sel = _pin(s, "MST")
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_planner")
        s.do_command("run_nuts")
    w.plan.selected_topology_index = sel      # keep the MST pin
    # Roomy bands -> the selected candidate carries no overlap, so no edge is
    # contended even though the candidate is edge-tagged.
    contended = s._rr_flip_edges(w, 'a')
    mine = [od for od in s.nuts_result.overlap_details
            if od.bid_a == w.input.original_bundle.id
            or od.bid_b == w.input.original_bundle.id]
    if not mine:
        assert contended == []


def test_rr_flip_move_is_an_involution():
    """_rr_apply_move('flip', e) mutates the selected candidate's geometry in
    place; _rr_undo_move flips the same edge back (involution), leaving the
    candidate byte-identical — the property the ripup loop relies on to restore a
    rejected flip (since _rr_snapshot does not capture candidate geometry)."""
    s = _mst_session()
    w, sel = _pin(s, "MST")
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_planner")
        s.do_command("run_nuts")
    w.plan.selected_topology_index = sel
    topo = w.input.candidates[sel]
    eids = sorted({sg.edge_id for sg in topo.segments if sg.edge_id >= 0})
    assert eids, "MST candidate should carry edge tags"

    def geom():
        return [(sg.start.x, sg.start.y, sg.end.x, sg.end.y, sg.edge_id)
                for sg in w.input.candidates[sel].segments]

    metric = lambda: s.nuts_result.num_overlaps           # noqa: E731
    for eid in eids:
        before = geom()
        move = ('flip', eid)
        m = s._rr_apply_move(w, move, sel, 'a', metric)
        if m is None:
            # flip rejected (alt bend on an obstacle): geometry unchanged
            assert geom() == before
            continue
        assert geom() != before, "an accepted flip must change the geometry"
        s._rr_undo_move(w, move, sel)
        assert geom() == before, "undo (re-flip) must restore the geometry exactly"


def test_flip_persists_to_wrapper_candidate():
    """An in-place mutation of `w.input.candidates[sel]` PERSISTS to the wrapper
    (the property the flip move relies on): `_rr_apply_move`'s flip mutates the
    candidate in place and then `_rr_trial` replans reading `w.input.candidates`,
    so the mutation must be visible on a FRESH read — not lost to a pybind11 STL
    copy.  `candidates` returns a list of references to the underlying C++
    Topology objects, so field-level edits (start/end/layer_hint — exactly what
    flip_mst_edge writes) round-trip; only structural edits (append/delete, e.g.
    doglegs) need `w.input.candidates = cands` write-back.  Deterministic guard
    against a future binding change that would silently make flip trials measure
    the un-flipped route (Codex #176 P2)."""
    s = _mst_session()
    w = s.bundles[0]
    sel = 0
    # Read via one access, then mutate a segment coord; a FRESH access must see it.
    orig = w.input.candidates[sel].segments[0].start.x
    w.input.candidates[sel].segments[0].start.x = orig + 4242
    assert w.input.candidates[sel].segments[0].start.x == orig + 4242, (
        "in-place edit of w.input.candidates was lost to a copy — flip trials "
        "would measure the un-flipped route"
    )
    # The var-held pattern _rr_apply_move uses ("cands = w.input.candidates").
    cands = w.input.candidates
    cands[sel].segments[0].start.x = orig + 99
    assert w.input.candidates[sel].segments[0].start.x == orig + 99


# ── use_edge_candidates: the flip move-source is opt-in ──────────────────────
#
# The per-edge MST L/Z flip is measured-redundant on the corpus (an index
# alternate always wins the commit), so `ripup_reroute` only consults
# `_rr_flip_edges` when `use_edge_candidates` is passed.  These pin the gate:
# default OFF ⇒ the flip source is never queried (routes unchanged); ON ⇒ it is.

def _flip_edges_call_count(s, arg):
    """Run ripup on the congested canned fixture, counting how many times the
    flip move-source (`_rr_flip_edges`) is consulted.  It is only called from
    inside the `if use_edge_candidates:` branch, so the count is 0 with the
    flag off and >0 with it on (there is a real contended bundle to scan)."""
    calls = [0]
    orig = s._rr_flip_edges

    def spy(w, stage):
        calls[0] += 1
        return orig(w, stage)

    s._rr_flip_edges = spy
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"ripup_reroute {arg}".strip())
    return calls[0]


def test_use_edge_candidates_off_by_default_skips_flip_source():
    """Without the keyword, ripup_reroute never queries the flip move-source —
    the flip is off by default, so routes match the pre-flip behavior."""
    s = _build_session(narrow=True)
    assert s.nuts_result.num_overlaps > 0, "fixture should start congested"
    assert _flip_edges_call_count(s, "") == 0


def test_use_edge_candidates_enables_flip_source():
    """With `use_edge_candidates`, ripup_reroute consults the flip move-source
    for each contended bundle (order-independent with the numeric max_iter)."""
    s = _build_session(narrow=True)
    assert s.nuts_result.num_overlaps > 0, "fixture should start congested"
    assert _flip_edges_call_count(s, "use_edge_candidates") > 0


def test_use_edge_candidates_keyword_order_independent():
    """`use_edge_candidates` and the numeric max_iter may appear in any order;
    both are parsed and the flip source is enabled either way."""
    s = _build_session(narrow=True)
    assert _flip_edges_call_count(s, "3 use_edge_candidates") > 0
    s2 = _build_session(narrow=True)
    assert _flip_edges_call_count(s2, "use_edge_candidates 3") > 0
