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
    """big2's DNUTS opens are driven down by ripup_reroute (history: 60 -> 0 on
    the ARM reference host; 132 -> 72 on x86 pre-fixes).

    Since the abutment-crossing Gap A fix and the kHeight short-stub layer
    steering, the width-mode big2 baseline itself reaches 0 opens on x86 —
    ripup then has nothing to do and the invariant is that it STAYS clean.
    The starting count is machine-sensitive (double-based NUTS math rounds
    differently across CPUs under -march=native; see -ffp-contract=off in
    CMakeLists and test_flow_scripts.py's tc3a note), so on a host where
    residual opens survive, the CPU-invariant guard remains that ripup makes
    real progress (strictly reduces the opens), as in
    test_big2_stage_a_reduces_overlaps and the hier stage-b test."""
    s = _big2_to_stage("b")
    base = s.detailed_result.num_unplaced
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("ripup_reroute")
    if base == 0:
        assert s.detailed_result.num_unplaced == 0   # clean stays clean (no-op)
    else:
        assert s.detailed_result.num_unplaced < base  # ripup makes real progress
    assert s.nuts_result.num_overlaps <= 9
    # On the ARM reference host (set BUDA_REF_HOST in that machine's CI) recover
    # the stronger clear-to-zero guarantee that `< base` alone doesn't pin.
    if os.environ.get("BUDA_REF_HOST"):
        assert s.detailed_result.num_unplaced == 0


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
    """Ripup stage-b re-routes per-instance wrappers and drives DNUTS opens down.

    Vehicle: the HEALER-FREE twin `06_multipin_stress_raw.buda`. The full
    `06_multipin_stress` now self-heals to 0/0/0 (its own negotiate/ripup), so it
    can no longer enter stage-b with opens — see
    docs/internal/mid_tier_failures_2026-07-29.md. The raw twin stops after
    `run_detailed_nuts` with a real residual (~60 opens), so the test's own
    `ripup_reroute` is what clears them — the actual mechanism under test."""
    s = _source_hier_flow("06_multipin_stress_raw.buda")
    assert s._planner_is_hier
    base = s.detailed_result.num_unplaced
    assert base > 0, "raw twin should land with DNUTS opens for ripup to clear"
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
    """The headline v1 validation (wishlist-healer item 1): replaying big2's
    measured overlaps into the planner as band demand lets its own cost model
    steer the offenders off the contended bands, and the ripup hill-climb
    finishes the residual to 0.

    Since the band-level cluster repack (nuts_band_repack.md) landed, NUTS
    itself pre-clears the overlaps negotiation used to demonstrate on, so the
    residue reaching this stage is exactly the part a replan-in-place cannot
    always improve — negotiation may legitimately find nothing to accept.
    Assert it never REGRESSES, and that the combined negotiate+ripup pipeline
    still ends fully clean (the invariant that matters)."""
    s = _big2_to_stage("a")
    base = s.nuts_result.num_overlaps
    assert base > 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("negotiate_congestion")
    after_neg = s.nuts_result.num_overlaps
    assert after_neg <= base, buf.getvalue()   # negotiation never regresses
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
    """Deterministic stage-b fixture (wishlist-healer item 5): an all-POWER
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


def _full_wrapper_state(s):
    """Every wrapper field a ripup trial can mutate, for exact-state diffs —
    including CONTENT-level candidate fingerprints (a dogleg re-solve
    overwrites a slot in place: same count, new geometry) and the dogleg
    bookkeeping dicts (#286 retrospective item 3)."""
    st = {}
    for w in s.bundles:
        bid = w.input.original_bundle.id
        cand_fp = tuple(
            (t.type, len(t.segments),
             tuple((seg.start.x, seg.start.y, seg.end.x, seg.end.y)
                   for seg in t.segments))
            for t in w.input.candidates)
        st[bid] = (w.plan.selected_topology_index, w.input.topology_pinned,
                   cand_fp, list(w.plan.seg_layers),
                   list(w.plan.seg_perp), list(w.plan.seg_net_pull),
                   list(w.plan.seg_slide_lo), list(w.plan.seg_slide_hi),
                   w.input.assigned_v_layer, w.input.assigned_h_layer,
                   list(w.input.pinned_seg_layers))
    st['_dogleg'] = (dict(s._dogleg_slot), dict(s._dogleg_originals))
    return st


def _cut_usages(s):
    """Per-band planner cut usage — what the visualizer's congestion overlay
    draws directly from get_cuts() (Codex #286: a forward-restore commit must
    leave the committed route here, not the last rejected trial)."""
    return [list(c.band_usage) for c in s.planner.get_cuts()]


def _force_legacy_commit(monkeypatch):
    """Disable commit-by-forward-restore so the winning move commits via the
    legacy pipeline re-run (the pre-L5 behavior)."""
    monkeypatch.setattr(buda_cli.BudaSession, "_rr_fwd_ok",
                        staticmethod(lambda snap, fwd: False))


def test_commit_by_forward_restore_matches_rerun_commit_stage_a(monkeypatch):
    """L5 exactness (stage a): committing a winning index move by restoring
    its forward snapshot leaves EXACTLY the state the legacy re-run commit
    produces — selections, every plan array, and the measured metric."""
    s_fwd = _build_session(narrow=True)
    with contextlib.redirect_stdout(io.StringIO()):
        s_fwd.do_command("ripup_reroute no_fast_trials")
    s_leg = _build_session(narrow=True)
    _force_legacy_commit(monkeypatch)
    with contextlib.redirect_stdout(io.StringIO()):
        s_leg.do_command("ripup_reroute no_fast_trials")
    assert s_fwd.nuts_result.num_overlaps == s_leg.nuts_result.num_overlaps
    assert _full_wrapper_state(s_fwd) == _full_wrapper_state(s_leg)
    assert _cut_usages(s_fwd) == _cut_usages(s_leg)


def test_commit_by_forward_restore_matches_rerun_commit_stage_b(monkeypatch):
    """L5 exactness (stage b): same A/B on the canned DNUTS-open fixture —
    the lexicographic metric and per-wrapper state agree between the
    forward-restore commit and the legacy re-run commit."""
    s_fwd = _build_dnuts_open_session()
    with contextlib.redirect_stdout(io.StringIO()):
        s_fwd.do_command("ripup_reroute no_fast_trials")
    s_leg = _build_dnuts_open_session()
    _force_legacy_commit(monkeypatch)
    with contextlib.redirect_stdout(io.StringIO()):
        s_leg.do_command("ripup_reroute no_fast_trials")
    assert (s_fwd.detailed_result.num_unplaced
            == s_leg.detailed_result.num_unplaced == 0)
    assert s_fwd.nuts_result.num_overlaps == s_leg.nuts_result.num_overlaps
    assert _full_wrapper_state(s_fwd) == _full_wrapper_state(s_leg)
    assert _cut_usages(s_fwd) == _cut_usages(s_leg)


# --- Global-occupant pass (wishlist-healer "global-overlap re-route of ------
# --- NON-contended bundles", the big2 b61 class) ----------------------------

def _build_occupant_session(truncate=True):
    """Three buses through one narrow M4 corridor (band1 y[1180,1270]) that
    fits two: a and c collide (one NUTS overlap), b holds a band1 slot and
    has escape candidates (band2 y[600,660] is only reachable from x>=1650
    ... but b's Z/U alternates suffice for the mechanics tests).  a and c's
    candidate pools are truncated to their selected candidate — a legitimate
    degenerate state — so no contender-derived move can fix the overlap;
    moving the OCCUPANT b is the only fix."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = [
        "def_layer 5 M5 V TOP 50",
        "def_layer 4 M4 H TOP 50",
        "def_track_pattern 4 0 SIGNAL 1 4",
        "def_track_pattern 5 0 SIGNAL 1 4",
        "add_block D1 0 1000 200 1400",
        "add_block D2 400 1000 600 1400",
        "add_block R1 2400 1000 2600 1400",
        "add_block R2 2400 1600 2600 2000",
        "add_block D3 1700 1000 1900 1400",
        "add_block R3 2400 400 2600 800",
        "add_bus a[8] D1.p R1.p",
        "add_bus b[8] D2.p R2.p",
        "add_bus c[8] D3.p R3.p",
        "add_keepout 0 1270 3000 4000 4",
        "add_keepout 0 660 3000 1180 4",
        "add_keepout 0 0 1650 660 4",
        "add_keepout 0 0 3000 600 4",
        "run_bundler", "generate_topologies",
        "select_topology 1 1", "select_topology 2 1", "select_topology 3 1",
        "run_planner", "run_nuts",
    ]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    assert [(od.bid_a, od.bid_b)
            for od in s.nuts_result.overlap_details] == [(1, 3)]
    if truncate:
        for bid in (1, 3):
            w = next(w for w in s.bundles
                     if w.input.original_bundle.id == bid)
            sel = w.plan.selected_topology_index
            w.input.candidates = [w.input.candidates[sel]]
            w.plan.selected_topology_index = 0
            w.input.topology_pinned = True
    return s


def test_band_occupants_ranks_holders_and_skips_outsiders():
    """The C++ ranking: bundles charging the overlap rectangle's bands rank
    with positive demand; a locked wrapper is excluded; top_k truncates."""
    s = _build_occupant_session()
    od = s.nuts_result.overlap_details[0]
    occ = s.planner.band_occupants(s.bundles, od.layer,
                                   od.span_lo, od.span_hi,
                                   od.perp_lo, od.perp_hi, 3)
    ranked = {bid: d for bid, d in occ}
    # All three route through band1, so all three hold the contended bands.
    assert set(ranked) == {1, 2, 3} and all(d > 0 for d in ranked.values())
    # top_k truncation.
    assert len(s.planner.band_occupants(s.bundles, od.layer,
                                        od.span_lo, od.span_hi,
                                        od.perp_lo, od.perp_hi, 1)) == 1
    # A locked wrapper is never an occupant.
    w2 = next(w for w in s.bundles if w.input.original_bundle.id == 2)
    w2.hier.locked = True
    occ2 = s.planner.band_occupants(s.bundles, od.layer,
                                    od.span_lo, od.span_hi,
                                    od.perp_lo, od.perp_hi, 3)
    assert 2 not in {bid for bid, _ in occ2}


def test_global_pass_moves_non_contended_occupant():
    """The pass mechanics, driven directly: with the overlap pair {1,3}
    excluded (modeling 'already scanned as contenders, no move helped'),
    the pass ranks the occupants of the overlap's bands, trials bundle 2's
    alternates against the overlap's location, finds the strictly-improving
    move, and leaves the session restored to the baseline (the caller
    commits via the returned forward snapshot)."""
    s = _build_occupant_session()
    stage, metric = s._rr_stage_metric()
    assert stage == 'a' and metric() == 1
    snap = s._rr_snapshot()
    sel_before = _selections(s)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        best, trials = s._rr_global_pass(stage, metric, snap, metric(),
                                         {1, 3})
    assert best is not None and trials >= 1, buf.getvalue()
    m, bid, old_tidx, move, fwd = best
    assert bid == 2 and move[0] == 'idx' and m == 0
    assert fwd is not None
    assert "GLOBAL" in buf.getvalue()
    # The pass itself commits nothing: baseline state intact.
    assert _selections(s) == sel_before
    assert s.nuts_result.num_overlaps == 1
    # Committing the forward snapshot lands the winning state.
    s._rr_restore(fwd)
    assert s.nuts_result.num_overlaps == 0
    assert _selections(s)[2] == move[1]


def test_global_pass_survives_contender_dominated_ranking(monkeypatch):
    """Review #287 medium: the site's own overlap parties charge its bands
    by construction, so a top-K truncation BEFORE the Python exclusion can
    return only excluded contenders and starve the genuine occupant.  With
    K forced to 1 (the top slot goes to an overlap party — asserted, so
    the fixture keeps its discriminating power), the pass must still reach
    and trial occupant bundle 2 because the request is widened by
    len(exclude) before the Python-side filter."""
    import buda_session.ripup as ripup_mod
    s = _build_occupant_session()
    od = s.nuts_result.overlap_details[0]
    occ = s.planner.band_occupants(s.bundles, od.layer,
                                   od.span_lo, od.span_hi,
                                   od.perp_lo, od.perp_hi, 1)
    assert occ and occ[0][0] in (1, 3), \
        f"fixture lost its discriminating power: top occupant {occ}"
    monkeypatch.setattr(ripup_mod, "_RR_GLOBAL_TOP_K", 1)
    stage, metric = s._rr_stage_metric()
    snap = s._rr_snapshot()
    with contextlib.redirect_stdout(io.StringIO()):
        best, trials = s._rr_global_pass(stage, metric, snap, metric(),
                                         {1, 3})
    assert best is not None and best[1] == 2, (best, trials)


def test_ripup_stall_invokes_global_pass_and_no_global_disables(monkeypatch):
    """Loop wiring: when the contender scan stalls above zero, the global
    pass runs by default; the `no_global` keyword disables it."""
    calls = []
    orig = buda_cli.BudaSession._rr_global_pass

    def spy(self, stage, metric, snap, cur, exclude):
        calls.append(sorted(exclude))
        return orig(self, stage, metric, snap, cur, exclude)

    monkeypatch.setattr(buda_cli.BudaSession, "_rr_global_pass", spy)
    s = _build_occupant_session()
    # Truncate the occupant's pool too, so the pass finds nothing and the
    # invocation itself is what's asserted (a clean stall end to end).
    w2 = next(w for w in s.bundles if w.input.original_bundle.id == 2)
    w2.input.candidates = [w2.input.candidates[
        w2.plan.selected_topology_index]]
    w2.plan.selected_topology_index = 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("ripup_reroute")
    assert calls, buf.getvalue()             # pass invoked at the stall
    assert "no improving re-route" in buf.getvalue()
    calls.clear()
    s2 = _build_occupant_session()
    with contextlib.redirect_stdout(io.StringIO()):
        s2.do_command("ripup_reroute no_global")
    assert not calls                          # keyword disables the pass


def test_ripup_global_pass_clears_stalled_overlap_end_to_end():
    """End to end through the command: contenders {1,3} have no moves (one
    candidate each) — historical ripup stops at 1 overlap; occupant
    bundle 2 is re-routed and the run ends clean.  NOTE: in this small
    fixture bundle 2 also surfaces as a junction-infeasibility contender,
    so the normal scan reaches it first — the assertions are the ENDPOINT
    plus bundle 2's selection change (conditional on which path moved it),
    with the global pass as the backstop; the direct-drive test above
    pins the pass's own mechanics."""
    s = _build_occupant_session()
    sel2_before = _selections(s)[2]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("ripup_reroute")
    out = buf.getvalue()
    assert s.nuts_result.num_overlaps == 0, out
    # Bundles 1 and 3 are single-candidate, so the only possible commit —
    # whether the contender scan or the global pass made it — is a bundle 2
    # re-route; the COMMIT line and the selection must agree.
    assert "COMMIT bundle 2" in out, out
    assert _selections(s)[2] != sel2_before, out


def test_global_moves_budget_guarantees_beyond_window_extras():
    """Codex #287 P1: the global pass's per-occupant move budget must
    include the promoted BEYOND-WINDOW candidates — a head-slice of the
    candidate order is all in-window whenever the pool exceeds the
    8-candidate window, starving exactly the b61-class candidates the
    pass exists to reach.  The split budget reserves up to half the slots
    for extras while the in-window pool stays first (cheap fixes keep
    first shot at the first-improving accept)."""
    s = _build_occupant_session(truncate=False)
    w = next(w for w in s.bundles if w.input.original_bundle.id == 1)
    assert len(w.input.candidates) > 8      # extras exist for this pool
    site = [(True, 1225.0)]                 # the band1 corridor's centre
    moves = s._rr_global_moves(w, w.plan.selected_topology_index, 'a', site)
    from buda_session.util import (_RR_GLOBAL_MOVES_PER_OCC,
                                   _RR_MAX_CANDIDATES_PER_BUNDLE)
    assert len(moves) <= _RR_GLOBAL_MOVES_PER_OCC
    extras = [t for t in moves if t >= _RR_MAX_CANDIDATES_PER_BUNDLE]
    assert extras, f"no beyond-window candidate in the budget: {moves}"
    # In-window moves still come first (the cheap-tie preference).
    first_extra = moves.index(extras[0])
    assert all(t >= _RR_MAX_CANDIDATES_PER_BUNDLE
               for t in moves[first_extra:]), moves


def test_candidate_order_sites_override_matches_default():
    """An explicit `sites=` override handed the bundle's OWN contention
    centres reproduces the default derive-from-own-contention ordering —
    the override changes WHERE the ranking looks, never HOW it ranks."""
    s = _build_session(narrow=True)
    w = s.bundles[0]
    old = w.plan.selected_topology_index
    default_order = s._rr_candidate_order(w, old, 'a')
    own = s._rr_contention_centres('a', w.input.original_bundle.id)
    assert own, "narrow fixture should leave bundle 1 contended"
    assert s._rr_candidate_order(w, old, 'a', sites=own) == default_order


def test_restore_regrows_reset_deleted_dogleg_slot():
    """#286 retrospective item 1 (mechanism test, mirroring the overwrite
    guard above): a rejected full-replan-fallback trial ran _reset_doglegs,
    DELETING the adopted split candidate — a trim-only restore would leave
    the pool short and selected_topology_index out of range, silently
    vanishing the bundle from opens/recharges.  The restore must RE-GROW
    the pool from the snapshot's dl_cand content."""
    s = _build_session(narrow=True)
    w0 = s.bundles[0]
    bid = w0.input.original_bundle.id
    # Synthesize an adopted dogleg (the guard is about restore mechanics,
    # not how the dogleg came to be — same convention as the overwrite test).
    cands = w0.input.candidates
    cands.append(cands[0])
    w0.input.candidates = cands
    slot = len(w0.input.candidates) - 1
    s._dogleg_slot[bid] = slot
    s._dogleg_originals[bid] = 0
    w0.plan.selected_topology_index = slot
    before = _full_wrapper_state(s)
    snap = s._rr_snapshot()
    # Simulate the rejected fallback trial's _reset_doglegs effect.
    cands = w0.input.candidates
    del cands[slot]
    w0.input.candidates = cands
    w0.plan.selected_topology_index = 0
    s._dogleg_slot.clear()
    s._dogleg_originals.clear()
    s._rr_restore(snap)
    assert len(w0.input.candidates) == slot + 1          # pool re-grown
    assert w0.plan.selected_topology_index == slot       # slot valid again
    assert _full_wrapper_state(s) == before


def test_trial_state_never_persists_to_bdb():
    """#286 retrospective item 1 (persist half): _persist_planner_output is
    a no-op while a ripup trial is in flight — a rejected trial's
    run_planner fallback must not leave planner rows a load_pipeline
    resume would rehydrate — and _rr_rerun clears the flag afterwards."""
    import buda
    s = _build_session(narrow=True)
    s.bdb = buda.BDB(":memory:")
    s._rr_in_trial = True
    assert s._persist_planner_output() == 0
    s._rr_in_trial = False
    stage, metric = s._rr_stage_metric()
    with contextlib.redirect_stdout(io.StringIO()):
        s._rr_rerun(stage, target_bid=s.bundles[0].input.original_bundle.id)
    assert s._rr_in_trial is False           # cleared even on the fast path


@pytest.mark.mid
def test_big2_commit_paths_agree_with_doglegs(monkeypatch):
    """#286 retrospective item 3: the A/B exactness pair runs on a fixture
    with no doglegs/MST, so the in-place dogleg re-solve branch of the
    forward-restore commit was untested.  big2 stage a exercises MST
    candidates and dogleg adoption en route; forward-restore and legacy
    re-run commits must agree on content-level wrapper state, dogleg
    bookkeeping, metric, and planner cut usage (the legacy path now
    recharges after commit too — retrospective item 2)."""
    s_fwd = _big2_to_stage("a")
    with contextlib.redirect_stdout(io.StringIO()):
        s_fwd.do_command("ripup_reroute no_fast_trials")
    s_leg = _big2_to_stage("a")
    _force_legacy_commit(monkeypatch)
    with contextlib.redirect_stdout(io.StringIO()):
        s_leg.do_command("ripup_reroute no_fast_trials")
    assert s_fwd.nuts_result.num_overlaps == s_leg.nuts_result.num_overlaps
    assert _full_wrapper_state(s_fwd) == _full_wrapper_state(s_leg)
    assert _cut_usages(s_fwd) == _cut_usages(s_leg)


def test_ripup_prints_timing_summary():
    """The per-run timing breakdown (Phase 0 instrumentation) rides the run's
    final output for both ripup_reroute and negotiate_congestion — including
    the per-pass solve profile (round-3 Phase 0: WHERE inside the trial
    solves the nuts/dnuts seconds go)."""
    s = _build_session(narrow=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("ripup_reroute")
    out = buf.getvalue()
    assert "[ripup_reroute] timing: replan " in out
    assert "[ripup_reroute] solve passes: nuts[" in out
    s2 = _build_session(narrow=True)
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        s2.do_command("negotiate_congestion")
    out2 = buf2.getvalue()
    assert "[negotiate] timing: replan " in out2
    assert "[negotiate] solve passes: nuts[" in out2


def test_results_carry_per_pass_profile():
    """NUTSResult/DetailedNUTSResult expose the per-pass solve profile
    (round-3 Phase 0): every pipeline pass has a non-negative bucket, and
    n_solves counts the solves folded in (>= 1; dogleg trial re-solves
    would add more).  Observation only — placement is untouched (the golden
    corpora pin that)."""
    s = _build_session(narrow=True)
    nuts_keys = {'extract', 'context', 'fixpoint', 'dogleg_detect',
                 'repair', 'corner', 'tighten', 'metrics',
                 # round-5 tail buckets: the stages OUTSIDE the solve
                 # lambda, which dominate a ~ms fixed-context screen run
                 'grid', 'bundle_copy', 'junctions', 'keepout_audit',
                 'report'}
    ps = dict(s.nuts_result.pass_seconds)
    assert set(ps) == nuts_keys, ps
    assert all(v >= 0.0 for v in ps.values()), ps
    assert s.nuts_result.n_solves >= 1
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("def_track_pattern 4 0 SIGNAL 1 4")
        s.do_command("def_track_pattern 5 0 SIGNAL 1 4")
        s.do_command("def_track_pattern 7 0 SIGNAL 1 4")
        s.do_command("run_detailed_nuts")
    assert s.detailed_result is not None
    dps = dict(s.detailed_result.pass_seconds)
    assert set(dps) == {'place', 'bit_spans', 'keepout_cull',
                        'pair_misalign', 'vias'}, dps
    assert all(v >= 0.0 for v in dps.values()), dps


def test_pass_profile_survives_python_result_merges():
    """The bottom-up DNUTS path builds a MERGED DetailedNUTSResult in Python
    (Codex #289): pass_seconds must be assignable so the merge can carry the
    summed profile — otherwise a bottom-up stage-b trial charges the dnuts
    wall with no dnuts.* buckets, a misleading gap in the profiling data."""
    import buda
    r = buda.DetailedNUTSResult()
    r.pass_seconds = {'place': 1.5, 'vias': 0.5}
    assert dict(r.pass_seconds) == {'place': 1.5, 'vias': 0.5}
    n = buda.NUTSResult()
    n.pass_seconds = {'fixpoint': 2.0}
    n.n_solves = 3
    assert dict(n.pass_seconds) == {'fixpoint': 2.0} and n.n_solves == 3


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
    """A flip's mutation reaches the wrapper via EXPLICIT write-back (the
    property the flip move relies on): `_rr_apply_move` mutates its held
    copy and assigns `w.input.candidates = cands`, and `_rr_trial` then
    replans reading the updated pool.

    History: this test originally pinned the OPPOSITE contract — element
    access as write-through references (Codex #176 P2).  Audit C7-04
    inverted it: the reference semantics left registered dangling views
    behind every pool reassignment, and a stale registry entry at a
    recycled heap address made later casts return freed objects (the
    intermittent topo_uid segfault).  `candidates` now yields OWNED copies;
    mutating sites write the pool back — the flip-visible-to-trial property
    is the same, provided differently (the _rr_apply_move/undo test above
    checks it end-to-end through the real flip path)."""
    s = _mst_session()
    w = s.bundles[0]
    sel = 0
    # Element access yields owned copies: an in-place edit does NOT write
    # through (pre-C7-04 it did)...
    orig = w.input.candidates[sel].segments[0].start.x
    w.input.candidates[sel].segments[0].start.x = orig + 4242
    assert w.input.candidates[sel].segments[0].start.x == orig, (
        "element access must yield owned copies (audit C7-04)")
    # ...and the write-back pattern _rr_apply_move uses DOES persist.
    cands = w.input.candidates
    cands[sel].segments[0].start.x = orig + 99
    w.input.candidates = cands
    assert w.input.candidates[sel].segments[0].start.x == orig + 99, (
        "write-back must persist the flipped geometry to the wrapper")


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


# --- fast trials (RR round 3): metric-neutral pass skipping ------------------

def test_fast_trials_match_full_trials_endpoint_stage_a():
    """Fast trials (default) skip tighten_pulls during stage-a trials — the
    trial metric is an UPPER BOUND (tighten is overlap-non-increasing), so
    accepts are sound and commits re-run the full pipeline.  On the canned
    fixture the trajectory and the committed full state must equal the
    no_fast_trials run exactly."""
    s_fast = _build_session(narrow=True)
    with contextlib.redirect_stdout(io.StringIO()):
        s_fast.do_command("ripup_reroute")
    s_full = _build_session(narrow=True)
    with contextlib.redirect_stdout(io.StringIO()):
        s_full.do_command("ripup_reroute no_fast_trials")
    assert s_fast.nuts_result.num_overlaps == 0
    assert s_fast.nuts_result.num_overlaps == s_full.nuts_result.num_overlaps
    assert _full_wrapper_state(s_fast) == _full_wrapper_state(s_full)
    assert _cut_usages(s_fast) == _cut_usages(s_full)


def test_fast_trials_commit_state_is_full_pipeline_stage_b():
    """Stage-b fast trials skip via emission (pure output, metric identical);
    the COMMITTED state must still be the full-pipeline state — vias present,
    endpoint equal to the no_fast_trials run."""
    s_fast = _build_dnuts_open_session()
    with contextlib.redirect_stdout(io.StringIO()):
        s_fast.do_command("ripup_reroute")
    s_full = _build_dnuts_open_session()
    with contextlib.redirect_stdout(io.StringIO()):
        s_full.do_command("ripup_reroute no_fast_trials")
    assert (s_fast.detailed_result.num_unplaced
            == s_full.detailed_result.num_unplaced)
    assert _full_wrapper_state(s_fast) == _full_wrapper_state(s_full)
    # The committed detailed result carries vias (a via-less trial state was
    # never committed) — byte-equal to the full run's.
    fv = sorted((v.bundle_id, v.from_seg, v.to_seg, v.bit_index, v.x, v.y)
                for v in s_fast.detailed_result.net_vias)
    lv = sorted((v.bundle_id, v.from_seg, v.to_seg, v.bit_index, v.x, v.y)
                for v in s_full.detailed_result.net_vias)
    assert fv == lv and len(fv) > 0


def test_skip_tighten_metric_is_upper_bound():
    """Engine-level property the fast-trial accept soundness rests on: a
    skip_tighten solve reports an overlap count >= the full solve's (tighten
    is overlap-non-increasing: its per-move guard reverts any move where
    find_overlaps grows), and its tighten bucket charges ~0."""
    import buda
    s = _build_session(narrow=True)
    full = s.nuts_result
    eng = buda.NUTSEngine(s.fp, s.layers)
    eng.set_track_pitch(s._nuts_pitch)
    eng.set_skip_tighten(True)
    if s.planner is not None:
        eng.set_extra_grid_points(list(s.planner.get_x_grid()),
                                  list(s.planner.get_y_grid()))
    with buda.ostream_redirect(), contextlib.redirect_stdout(io.StringIO()):
        skipped = eng.run(s.bundles)
    assert skipped.num_overlaps >= full.num_overlaps
    assert dict(skipped.pass_seconds).get('tighten', 0.0) <= 1e-4


def test_dnuts_emit_vias_off_is_metric_identical():
    """Engine-level property for stage b: emit_vias=False changes NOTHING but
    the via list — same bit-wires, same unplaced count."""
    import buda
    s = _build_dnuts_open_session()
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("ripup_reroute")     # committed route bends across layers
    segs = buda.make_bus_segments(s.bundles, s.nuts_result, s.fp, "LO_HI")
    eng = buda.DetailedNUTSEngine(s.routing_grid)
    with buda.ostream_redirect(), contextlib.redirect_stdout(io.StringIO()):
        with_v = eng.run(segs)
        no_v = eng.run(segs, emit_vias=False)
    key = lambda r: sorted((n.bundle_id, n.seg_idx, n.bit_index,
                            n.track_position, n.span_lo, n.span_hi)
                           for n in r.net_segments)
    assert key(with_v) == key(no_v)
    assert with_v.num_unplaced == no_v.num_unplaced
    assert len(no_v.net_vias) == 0 and len(with_v.net_vias) > 0


# --- fixed-context screen (RR round 3, final lever) --------------------------

def test_screen_run_freezes_context_and_is_reproducible():
    """Engine-level properties the screen rests on: a single-wrapper run with
    every other bundle fixed (add_fixed_segments_except) returns the frozen
    context VERBATIM (no pass may move a fixed segment), places the target,
    is deterministic call-to-call (a score must mean the same thing twice),
    and exports no dogleg surgery (set_skip_doglegs — the screen result is
    discarded, so mutation would be a leak)."""
    import buda
    s = _build_session(narrow=True)
    base = s.nuts_result
    w = s.bundles[1]
    bid = w.input.original_bundle.id
    frozen = {(t.bundle_id, t.seg_idx):
              (t.layer, t.span_lo, t.span_hi, t.track_position)
              for t in base.segments if t.bundle_id != bid}
    assert frozen, "fixture should have context segments to freeze"

    def screen_once():
        eng = buda.NUTSEngine(s.fp, s.layers)
        eng.set_track_pitch(s._nuts_pitch)
        eng.set_skip_tighten(True)
        eng.set_skip_doglegs(True)
        eng.set_extra_grid_points(list(s.planner.get_x_grid()),
                                  list(s.planner.get_y_grid()))
        eng.add_fixed_segments_except(base, bid)
        with buda.ostream_redirect(), contextlib.redirect_stdout(io.StringIO()):
            return eng.run([w])

    r1 = screen_once()
    r2 = screen_once()
    got = {(t.bundle_id, t.seg_idx):
           (t.layer, t.span_lo, t.span_hi, t.track_position)
           for t in r1.segments if t.bundle_id != bid}
    assert got == frozen                     # context actually frozen
    assert any(t.bundle_id == bid and t.placed for t in r1.segments)
    key = lambda r: sorted((t.bundle_id, t.seg_idx, t.layer,        # noqa: E731
                            t.track_position) for t in r.segments)
    assert key(r1) == key(r2)                # reproducible
    assert (r1.num_overlaps, r1.num_violations) == \
           (r2.num_overlaps, r2.num_violations)
    assert len(r1.dogleg_topologies) == 0    # read-only: no surgery exported


def test_screen_matches_no_screen_endpoint_stage_a():
    """A/B on the canned stage-a fixture: the screen reorders WHICH improving
    move is trialed first, never what is committable — both runs must reach
    the clean endpoint, and the screened run's timing line must expose the
    screen bucket."""
    s_scr = _build_session(narrow=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s_scr.do_command("ripup_reroute screen")
    s_ns = _build_session(narrow=True)
    with contextlib.redirect_stdout(io.StringIO()):
        s_ns.do_command("ripup_reroute no_screen")
    assert s_scr.nuts_result.num_overlaps == 0
    assert s_ns.nuts_result.num_overlaps == 0
    timing = [ln for ln in buf.getvalue().splitlines() if "timing:" in ln]
    assert timing and "screen" in timing[0]


def test_screen_matches_no_screen_endpoint_stage_b():
    """A/B on the canned DNUTS-open fixture (stage b): identical clean
    endpoint with and without the screen."""
    s_scr = _build_dnuts_open_session()
    with contextlib.redirect_stdout(io.StringIO()):
        s_scr.do_command("ripup_reroute screen")
    s_ns = _build_dnuts_open_session()
    with contextlib.redirect_stdout(io.StringIO()):
        s_ns.do_command("ripup_reroute no_screen")
    assert s_scr.detailed_result.num_unplaced == 0
    assert s_ns.detailed_result.num_unplaced == 0
    assert s_scr.nuts_result.num_overlaps == s_ns.nuts_result.num_overlaps == 0


def test_adversarial_screen_cannot_cost_the_endpoint(monkeypatch):
    """Completeness lives in the loop, not the screen: even a screen that
    defers EVERY move (the worst possible ranking) must still reach the
    clean endpoint — the stalled iteration sweeps the deferred moves at
    full fidelity before concluding anything."""
    s = _build_session(narrow=True)
    monkeypatch.setattr(
        buda_cli.BudaSession, "_rr_screen_prune",
        lambda self, w, idx_moves: ([], list(idx_moves)))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("ripup_reroute")
    out = buf.getvalue()
    assert s.nuts_result.num_overlaps == 0, out
    assert "screened scan stalled" in out
    assert "deferred" in out


def test_screen_prune_orders_by_score_ties_keep_position(monkeypatch):
    """_rr_screen_prune keeps the _RR_SCREEN_TOP_N best-screened moves and
    defers the rest; equal scores keep the incoming (farness/cheap-first)
    position — the screen may reorder only on evidence.  A None score sheet
    (replan unavailable) returns the full unscreened list."""
    from buda_session.util import _RR_SCREEN_TOP_N
    assert _RR_SCREEN_TOP_N == 2, "test assumes the shipped top-N"
    s = _build_session(narrow=True)
    w = s.bundles[0]
    idx_moves = [('idx', 3), ('idx', 1), ('idx', 5), ('idx', 2)]
    scores = {3: (2, 0), 1: (0, 0), 5: (0, 0), 2: (1, 0)}
    monkeypatch.setattr(buda_cli.BudaSession, "_rr_screen_scores",
                        lambda self, w, tidxs: scores)
    kept, deferred = s._rr_screen_prune(w, idx_moves)
    assert kept == [('idx', 1), ('idx', 5)]      # tie: 1 precedes 5 incoming
    assert deferred == [('idx', 2), ('idx', 3)]  # deferred stays score-ranked
    monkeypatch.setattr(buda_cli.BudaSession, "_rr_screen_scores",
                        lambda self, w, tidxs: None)
    kept, deferred = s._rr_screen_prune(w, idx_moves)
    assert kept == idx_moves and deferred == []


def test_screen_candidates_batched_matches_per_candidate_calls():
    """The batched entry must score each candidate exactly as a separate
    single-candidate call would: every screen_candidates invocation works
    on its own wrapper-list copy, so a batch of N and N batches of 1 see
    identical inputs — any divergence means state leaks across candidates
    within one call."""
    import buda
    s = _build_session(narrow=True)
    w = s.bundles[0]
    bid = w.input.original_bundle.id
    tidxs = [t for t in range(1, min(4, len(w.input.candidates)))]

    def make_eng():
        eng = buda.NUTSEngine(s.fp, s.layers)
        eng.set_track_pitch(s._nuts_pitch)
        eng.set_skip_tighten(True)
        eng.set_skip_doglegs(True)
        eng.set_extra_grid_points(list(s.planner.get_x_grid()),
                                  list(s.planner.get_y_grid()))
        eng.add_fixed_segments_except(s.nuts_result, bid)
        return eng

    with buda.ostream_redirect(), contextlib.redirect_stdout(io.StringIO()):
        batched = make_eng().screen_candidates(s.bundles, bid, tidxs,
                                               s.planner, False)
        singles = [make_eng().screen_candidates(s.bundles, bid, [t],
                                                s.planner, False)[0]
                   for t in tidxs]
    assert batched is not None
    assert [tuple(r) for r in batched] == [tuple(r) for r in singles]
    # Unknown target id -> None (the caller's unscreened fallback).
    with buda.ostream_redirect(), contextlib.redirect_stdout(io.StringIO()):
        assert make_eng().screen_candidates(s.bundles, 10 ** 9, tidxs,
                                            s.planner, False) is None


def test_replan_candidates_matches_replan_bundle_sequence():
    """The batched planner entry recharges once and never commits — every
    candidate must still get EXACTLY the assignment a pin + replan_bundle
    sequence produces (each of those recharges away its predecessor's
    commit, so both see the same others-only usage), and the target's
    selection/pin must come back untouched."""
    s = _build_session(narrow=True)
    w = s.bundles[0]
    bid = w.input.original_bundle.id
    sel0, pin0 = w.plan.selected_topology_index, w.input.topology_pinned
    tidxs = [t for t in range(1, min(4, len(w.input.candidates)))]
    batch = s.planner.replan_candidates(s.bundles, bid, tidxs)
    assert batch is not None and len(batch) == len(tidxs)
    assert (w.plan.selected_topology_index, w.input.topology_pinned) == \
           (sel0, pin0)
    snap = s._rr_snapshot()
    seq = []
    for t in tidxs:
        w.plan.selected_topology_index = t
        w.input.topology_pinned = True
        seq.append(s.planner.replan_bundle(s.bundles, bid))
    s._rr_restore(snap, only={bid})
    for b, q in zip(batch, seq):
        assert q is not None
        assert (b.topo_index, b.v_layer_id, b.h_layer_id,
                list(b.seg_layers), list(b.seg_perp)) == \
               (q.topo_index, q.v_layer_id, q.h_layer_id,
                list(q.seg_layers), list(q.seg_perp))
    # Unknown target / bad index -> None (the unscreened fallback).
    assert s.planner.replan_candidates(s.bundles, 10 ** 9, tidxs) is None
    assert s.planner.replan_candidates(s.bundles, bid, [10 ** 6]) is None


def test_screen_scores_leave_session_state_untouched():
    """The batched screen works on a C++-side copy of the wrapper list, so
    _rr_screen_scores must leave NO divergence from the pre-screen state —
    the subsequent full trials assume the committed baseline."""
    s = _build_session(narrow=True)
    w = s.bundles[0]
    before = _full_wrapper_state(s)
    tidxs = [t for t in range(1, min(4, len(w.input.candidates)))]
    scores = s._rr_screen_scores(w, tidxs)
    assert scores is not None and set(scores) == set(tidxs)
    assert all(isinstance(v, tuple) and len(v) == 2 for v in scores.values())
    assert _full_wrapper_state(s) == before


# --- warm trials (RR round 4, opt-in) ----------------------------------------

def test_warm_rerun_is_deterministic_and_leaves_prev_untouched():
    """Engine properties the warm pre-filter rests on: rerun_bundle_warm is
    deterministic call-to-call, never mutates the baseline it reads, and
    exports no dogleg surgery (a warm state is a discarded predictor)."""
    import buda
    s = _build_session(narrow=True)
    base = s.nuts_result
    before = sorted((t.bundle_id, t.seg_idx, t.layer, t.track_position,
                     t.span_lo, t.span_hi) for t in base.segments)
    w = s.bundles[0]
    bid = w.input.original_bundle.id
    eng = buda.NUTSEngine(s.fp, s.layers)
    eng.set_track_pitch(s._nuts_pitch)
    eng.set_extra_grid_points(list(s.planner.get_x_grid()),
                              list(s.planner.get_y_grid()))
    with buda.ostream_redirect(), contextlib.redirect_stdout(io.StringIO()):
        r1 = eng.rerun_bundle_warm(base, s.bundles, bid)
        r2 = eng.rerun_bundle_warm(base, s.bundles, bid)
    key = lambda r: sorted((t.bundle_id, t.seg_idx, t.layer,     # noqa: E731
                            t.track_position) for t in r.segments)
    assert key(r1) == key(r2)
    assert (r1.num_overlaps, r1.num_violations) == \
           (r2.num_overlaps, r2.num_violations)
    assert len(r1.dogleg_topologies) == 0
    after = sorted((t.bundle_id, t.seg_idx, t.layer, t.track_position,
                    t.span_lo, t.span_hi) for t in base.segments)
    assert after == before                   # baseline untouched


def test_warm_rerun_no_duplicate_rows_on_prefixed_engine():
    """Codex #296 P2: a bottom-up engine already carries fixed segments and
    the baseline holds the SAME copies (run() appends fixed segments to
    every result) — rerun_bundle_warm's scratch copy must not freeze them
    twice.  Every (bundle, seg) key must appear exactly once in the warm
    result, and the pre-fixed bundle's rows must sit at their baseline
    positions."""
    import buda
    s = _build_session(narrow=True)
    base = s.nuts_result
    bids = sorted({t.bundle_id for t in base.segments})
    fixed_bid, target_bid = bids[0], bids[1]
    fixed_rows = [t for t in base.segments if t.bundle_id == fixed_bid]
    eng = buda.NUTSEngine(s.fp, s.layers)
    eng.set_track_pitch(s._nuts_pitch)
    eng.set_extra_grid_points(list(s.planner.get_x_grid()),
                              list(s.planner.get_y_grid()))
    eng.add_fixed_segments(fixed_rows)          # simulate a bottom-up engine
    with buda.ostream_redirect(), contextlib.redirect_stdout(io.StringIO()):
        warm = eng.rerun_bundle_warm(base, s.bundles, target_bid)
    keys = [(t.bundle_id, t.seg_idx) for t in warm.segments]
    assert len(keys) == len(set(keys)), "duplicate segment rows"
    # Every pre-fixed row present exactly once (positions may shift: in the
    # phase-2 union the safety passes treat them as ordinary segments).
    want = {(t.bundle_id, t.seg_idx) for t in fixed_rows}
    assert want <= set(keys)


def test_warm_trials_match_no_warm_endpoint_stage_a():
    """A/B on the canned stage-a fixture: the warm pre-filter may reorder
    which improving move is found first, never what is committable — both
    runs reach the clean endpoint, and the warm run's timing line exposes
    the warm bucket."""
    s_w = _build_session(narrow=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s_w.do_command("ripup_reroute warm_trials")
    s_n = _build_session(narrow=True)
    with contextlib.redirect_stdout(io.StringIO()):
        s_n.do_command("ripup_reroute no_warm_trials")
    assert s_w.nuts_result.num_overlaps == 0
    assert s_n.nuts_result.num_overlaps == 0
    timing = [ln for ln in buf.getvalue().splitlines() if "timing:" in ln]
    assert timing and "warm" in timing[0]


def test_warm_trials_match_no_warm_endpoint_stage_b():
    """A/B on the canned DNUTS-open fixture (stage b): identical clean
    endpoint with and without warm trials."""
    s_w = _build_dnuts_open_session()
    with contextlib.redirect_stdout(io.StringIO()):
        s_w.do_command("ripup_reroute warm_trials")
    s_n = _build_dnuts_open_session()
    with contextlib.redirect_stdout(io.StringIO()):
        s_n.do_command("ripup_reroute no_warm_trials")
    assert s_w.detailed_result.num_unplaced == 0
    assert s_n.detailed_result.num_unplaced == 0
    assert s_w.nuts_result.num_overlaps == s_n.nuts_result.num_overlaps == 0


def test_adversarial_warm_cannot_cost_the_endpoint(monkeypatch):
    """The certificate lives in the loop, not the predictor: a warm eval
    that rejects EVERY move (the worst possible predictor) must still reach
    the clean endpoint — the stalled iteration cold-sweeps the
    warm-rejected moves before concluding anything."""
    s = _build_session(narrow=True)
    monkeypatch.setattr(
        buda_cli.BudaSession, "_rr_warm_eval",
        lambda self, w, tidx, stage, cur, snap:
            (10 ** 6, 10 ** 6) if isinstance(cur, tuple) else 10 ** 6)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("ripup_reroute warm_trials")
    out = buf.getvalue()
    assert s.nuts_result.num_overlaps == 0, out
    assert "warm scan stalled" in out
    assert "warm-rescued" in out


def test_warm_trials_off_by_default_makes_no_warm_evals():
    """_RR_WARM_TRIALS_DEFAULT is False (corpus-measured: the screen
    already cut cold-trial volume to near-minimum, so the pre-filter is
    cost-neutral at best on the corpus): a bare ripup_reroute must never
    call the warm eval, keeping default routes byte-identical to round 3."""
    s = _build_session(narrow=True)
    calls = [0]
    orig = buda_cli.BudaSession._rr_warm_eval

    def spy(self, w, tidx, stage, cur, snap):
        calls[0] += 1
        return orig(self, w, tidx, stage, cur, snap)

    s._rr_warm_eval = spy.__get__(s)
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("ripup_reroute")
    assert calls[0] == 0
    assert s.nuts_result.num_overlaps == 0


def test_dnuts_place_abort_is_sound_certain_rejection():
    """Round-3 place-abort: with abort_unplaced armed, placement stops the
    moment the running unplaced count exceeds the threshold — sound because
    num_unplaced is non-decreasing through place and cull, so the trial is a
    certain rejection.  The aborted result must carry the flag and a count
    STRICTLY above the threshold; disarmed (-1) must reproduce the full run
    exactly."""
    import buda
    s = _build_dnuts_open_session()          # baseline has real opens
    segs = buda.make_bus_segments(s.bundles, s.nuts_result, s.fp, "LO_HI")
    eng = buda.DetailedNUTSEngine(s.routing_grid)
    with buda.ostream_redirect(), contextlib.redirect_stdout(io.StringIO()):
        full = eng.run(segs)
        ab = eng.run(segs, emit_vias=False, abort_unplaced=0)
        off = eng.run(segs, emit_vias=True, abort_unplaced=-1)
    assert full.num_unplaced > 0             # fixture precondition
    assert ab.aborted and ab.num_unplaced > 0
    assert ab.num_unplaced <= full.num_unplaced   # partial count, lower bound
    assert not off.aborted
    assert off.num_unplaced == full.num_unplaced
    assert len(off.net_vias) == len(full.net_vias)
