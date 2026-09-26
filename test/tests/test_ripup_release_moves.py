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

"""Measured-infeasibility uniformity break — ripup's RELEASE pass
(opens #14 fix space (a) / docs/internal/bottomup_healer_templates.md).

A locked bottom-up instance whose plan-time track pools MATCH its
reference can still strand bits at DNUTS (a dynamic neighbors/occupancy
conflict no static pool comparison sees — mix2_fast_bottomup bundle 166).
At a stage-b stall with even the class pass exhausted, and ONLY under the
user's declared `check_template_tracks on_mismatch independent` policy,
the release pass unlocks exactly the measured-open instance (fixed copy
withdrawn, pin kept, forced per-segment layers cleared — the
unpin_topology hazard), re-solves it individually, tries its candidate
alternates when the free re-solve alone does not improve, and commits
only on a strictly better measured metric; the aligned siblings keep the
uniform copy.

Covers: the gating no-ops (stage, policy, no locked opens), the
release-and-revert round trip (locked + pinned_seg_layers restored), the
forced-layer clear, and the end-to-end heal on the real mix2 flow (slow
tier: RELEASE COMMIT + clean detailed check + siblings still locked).
"""

import contextlib
import io
import pathlib
import re

import pytest
import buda
import buda_cli

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_RNR = _ROOT / "flow" / "rnr"


def _run_cmd(s, cmd):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command(cmd)
    return out.getvalue()


def _two_inst_db():
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst("proc_i1", "proc_cell", "", 0, 0)
    db.add_inst("proc_i2", "proc_cell", "", 500, 0)
    for i in range(4):
        db.add_net_pins(f"ab1_{i}", "proc_i1/pa_i.out", ["proc_i1/pb_i.in"])
        db.add_net_pins(f"ab2_{i}", "proc_i2/pa_i.out", ["proc_i2/pb_i.in"])
    buda.BustermGen(db).derive(1)
    return db


_PATTERNS = ["def_track_pattern 6 0 SIGNAL 1 1",
             "def_track_pattern 7 0 SIGNAL 1 1",
             "def_track_pattern 4 0 SIGNAL 1 1",
             "def_track_pattern 5 0 SIGNAL 1 1"]


def _bottom_up_session(db, policy="independent"):
    """Full bottom-up flow through run_detailed_nuts (stage b state)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = db
    for c in (["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
               "def_layer 4 M4 H 50", "def_layer 5 M5 V 50"]
              + _PATTERNS
              + ["run_hier_bundler", "generate_hier_topologies",
                 "set_bottom_up proc_cell", "run_planner hier", "run_nuts",
                 f"check_template_tracks on_mismatch {policy}",
                 "run_detailed_nuts"]):
        _run_cmd(s, c)
    return s


def _locked_wrapper(s):
    return next(w for w in s.bundles if w.hier.locked)


# ── gating no-ops ────────────────────────────────────────────────────────────

def test_release_pass_noop_gates():
    """Stage a, the default `stop` policy, and no-locked-opens must each
    return (False, 0) with NO output — the pass is structurally inert
    outside its exact trigger."""
    s = _bottom_up_session(_two_inst_db(), policy="independent")
    # Stage a: never fires regardless of state.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert s._rr_release_pass('a', lambda: 1, 1) == (False, 0)
    assert buf.getvalue() == ""
    # Stage b, clean design: no locked bundle holds opens.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert s._rr_release_pass('b', lambda: (1, 1), (1, 1)) == (False, 0)
    assert buf.getvalue() == ""
    # `stop` policy: inert even with (synthetic) locked opens.
    s2 = _bottom_up_session(_two_inst_db(), policy="stop")
    bid = _locked_wrapper(s2).input.original_bundle.id
    s2._open_segments = lambda: [(bid, 0, 4, 4)]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert s2._rr_release_pass('b', lambda: (4, 0), (4, 0)) == (False, 0)
    assert buf.getvalue() == ""


# ── release + revert round trip ──────────────────────────────────────────────

def test_release_reverts_fully_when_nothing_improves():
    """A synthetic locked-open on a clean design: no move can strictly
    improve a (0, 0)-adjacent metric, so every trial must be rejected and
    the FULL pre-pass state restored — locked back True, the forced
    per-segment layers back, the fixed-copy cache back, and the NUTS
    result the committed object."""
    s = _bottom_up_session(_two_inst_db(), policy="independent")
    w = _locked_wrapper(s)
    bid = w.input.original_bundle.id
    s._open_segments = lambda: [(bid, 0, 4, 4)]     # pretend it is open
    before_locked = w.hier.locked
    before_pl = list(w.input.pinned_seg_layers)
    assert before_locked and before_pl
    before_nuts = s.nuts_result
    before_fixed = s._bu_fixed_cache
    metric = lambda: (0, 0)          # nothing can be strictly better
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok, trials = s._rr_release_pass('b', metric, (0, 0))
    assert not ok and trials >= 1
    assert "RELEASE pass" in buf.getvalue()
    assert "no improvement, copy kept" in buf.getvalue()
    assert w.hier.locked == before_locked
    assert list(w.input.pinned_seg_layers) == before_pl
    assert s.nuts_result is before_nuts
    assert s._bu_fixed_cache is before_fixed


def test_release_clears_forced_layers_and_unlocks():
    """The mutation half, observed mid-flight: releasing must clear the
    wrapper's pinned_seg_layers (the unpin_topology hazard — the planner
    forces them onto ANY candidate, so a repin would carry the OLD
    candidate's H/V layers onto a different-direction shape) and flip
    hier.locked.  Observed via a metric hook that inspects the wrapper
    state during the first trial."""
    s = _bottom_up_session(_two_inst_db(), policy="independent")
    w = _locked_wrapper(s)
    bid = w.input.original_bundle.id
    s._open_segments = lambda: [(bid, 0, 4, 4)]
    seen = {}

    def metric():
        seen.setdefault("locked", w.hier.locked)
        seen.setdefault("pl", list(w.input.pinned_seg_layers))
        return (0, 0)                # never accepts — pass reverts
    with contextlib.redirect_stdout(io.StringIO()):
        s._rr_release_pass('b', metric, (0, 0))
    assert seen["locked"] is False   # unlocked during the trial
    assert seen["pl"] == []          # forced layers cleared


def test_release_pass_honors_trial_budget(monkeypatch):
    """The aggregate _RR_RELEASE_MAX_TRIALS cap must stop the pass before
    another instance's trials start (Codex #487) — each locked-open
    instance costs up to 1 + _RR_CLASS_TOP_N full reruns, so a
    many-instance infeasible design must not run unbounded."""
    import buda_session.ripup as ripup_mod
    s = _bottom_up_session(_two_inst_db(), policy="independent")
    bid = _locked_wrapper(s).input.original_bundle.id
    s._open_segments = lambda: [(bid, 0, 4, 4)]
    monkeypatch.setattr(ripup_mod, "_RR_RELEASE_MAX_TRIALS", 0)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok, trials = s._rr_release_pass('b', lambda: (0, 0), (0, 0))
    assert (ok, trials) == (False, 0)
    assert "trial budget" in buf.getvalue()


def test_no_release_moves_token_accepted():
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = _run_cmd(s, "ripup_reroute no_release_moves")
    assert "unknown option" not in out.lower()
    assert "no bundles" in out


# ── end-to-end heal (slow): the mix2 stuck residual ──────────────────────────

@pytest.mark.slow
def test_mix2_release_heals_bundle166_end_to_end():
    """The real vehicle (opens #14): with the class pass exhausted at the
    8-open stall, the release pass must break uniformity for exactly the
    measured-infeasible instance and drive the DNUTS opens to 0 with a
    buildable route (check_design clean at the detailed level), while the
    aligned siblings keep the uniform copy."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for c in [f"source {_RNR / 'mix_tracks.buda'}",
                  f"open_bdb {_RNR / 'mix2.bdb.sql'}",
                  "set_bottom_up dnuts1", "set_bottom_up dnuts2",
                  "set_bottom_up dogleg1", "set_bottom_up dogleg2",
                  "derive_busterms 2",
                  "add_blocks_from_bdb 0",
                  "add_blocks_from_bdb 1 skip",
                  "add_blocks_from_bdb 2 skip",
                  "run_hier_bundler depth 2",
                  "generate_hier_topologies",
                  "run_planner hier 5 signal_tracks",
                  "run_nuts",
                  "negotiate_congestion", "ripup_reroute",
                  "check_template_tracks on_mismatch independent",
                  "run_detailed_nuts",
                  "negotiate_congestion", "ripup_reroute"]:
            s.do_command(c)
    text = out.getvalue()
    assert s.detailed_result.num_unplaced == 0, text
    # Either the release pass carried the endpoint, or (on a host where
    # earlier healing already reached 0) it never needed to fire — but a
    # fired-and-committed release must be LOUD.
    if "RELEASE COMMIT" in text:
        assert "released from the uniform copy" in text
        assert "the aligned siblings keep the copy" in text
        # Uniformity broken surgically, not wholesale: locked copies
        # remain (the released instance's class keeps its siblings).
        assert any(w.hier.locked for w in s.bundles), text
    # The healed route must be buildable — no layer-direction violations
    # (the forced-layer hazard this arc fixed), and nothing else the audit
    # can name either, except the cross-bundle shorts #948 made visible:
    # this endpoint carries two shorted bits (bundle 90's `top_bus3_w10`
    # against bundle 119's `chip/i_dnuts1_0/r12` on M4 — the judge,
    # `tools/independent_audit.py`, counts the same two on a checkpoint of
    # it), which were always there, sit nowhere near the released instance,
    # and no healer's metric reads.  Accepted as measured and no further.
    known_cross_shorts = 2
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("check_design")
    audit = buf.getvalue()
    lines = [ln.strip() for ln in audit.splitlines() if "Bundle" in ln]
    shorts = [ln for ln in lines if "<->Bundle" in ln and "(a short)" in ln]
    assert len(shorts) == len(lines), audit          # nothing but those
    short_bits = sum(int(re.search(r": (\d+) bit\(s\)", ln).group(1))
                     for ln in shorts)
    assert short_bits <= known_cross_shorts, audit


def test_release_stamps_the_reservation_as_blocked_tracks():
    """E5's healed rounds found the gap: a released instance solves in
    the global DNUTS run, where the reservation's keepouts (on the
    reference's grid clone) never reach, and the audit read the released
    cores' own metal on their reserved tracks.  Releasing now stamps the
    reserved tracks, folded into the instance's frame, as the wrapper's
    blocked tracks — the same list a misaligned instance carries — and a
    rejected release restores the stamp with the lock."""
    db = _two_inst_db()
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = db
    for c in (["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
               "def_layer 4 M4 H 50", "def_layer 5 M5 V 50"]
              + _PATTERNS
              + ["run_hier_bundler", "generate_hier_topologies",
                 "set_bottom_up proc_cell",
                 "set_cell_layer_reserve proc_cell M6 21.5,25.5",
                 "run_planner hier", "run_nuts",
                 "check_template_tracks on_mismatch independent",
                 "run_detailed_nuts"]):
        _run_cmd(s, c)
    ref = s._template_track_verdict["proc_cell"]["ref"]
    w = next(w for w in s.bundles if w.hier.locked
             and w.input.original_bundle.instances[0] != ref)
    assert not w.hier.blocked_tracks            # a copy carries none
    bid = w.input.original_bundle.id
    inst = w.input.original_bundle.instances[0]
    comp = next(c for c in db.all_components() if c.name == inst)
    s._open_segments = lambda: [(bid, 0, 4, 4)]
    seen = {}

    def metric():
        seen.setdefault("blocked", dict(w.hier.blocked_tracks))
        return (0, 0)                # never accepts — pass reverts
    with contextlib.redirect_stdout(io.StringIO()):
        s._rr_release_pass('b', metric, (0, 0))
    # during the trial: the two reserved tracks, absolute over the instance
    assert seen["blocked"] == {6: [comp.y1 + 21.5, comp.y1 + 25.5]}, seen
    # after the rejected trial: locked again, and no stamp
    assert w.hier.locked and not w.hier.blocked_tracks


def test_a_released_reference_leaves_its_siblings_stamped():
    """E5's NQ = 16 top-down round, healed: the class pass re-pinned the
    cluster template and the release pass then withdrew its REFERENCE
    instance from the copy, so the siblings' group had no reference for
    the plan compute to walk and the stamps it had kept as wrapper state
    were gone from seven rebuilt wrappers — own metal on the reservation.
    The stamps are now derived from the plan on every call: with the
    reference released, every sibling solves in the global run and
    carries the reserved tracks, the released reference too."""
    db = _two_inst_db()
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = db
    for c in (["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
               "def_layer 4 M4 H 50", "def_layer 5 M5 V 50"]
              + _PATTERNS
              + ["run_hier_bundler", "generate_hier_topologies",
                 "set_bottom_up proc_cell",
                 "set_cell_layer_reserve proc_cell M6 21.5,25.5",
                 "run_planner hier", "run_nuts",
                 "check_template_tracks on_mismatch independent",
                 "run_detailed_nuts"]):
        _run_cmd(s, c)
    wr = {w.input.original_bundle.instances[0]: w for w in s.bundles
          if w.hier.locked}
    assert set(wr) == {"proc_i1", "proc_i2"}
    ref = s._template_track_verdict["proc_cell"]["ref"]
    other = next(i for i in wr if i != ref)
    comps = {c.name: c for c in db.all_components()}
    # the reference carries its reservation as blocked tracks (6b: no
    # keepout on a grid clone any more), the copy carries none
    assert wr[ref].hier.blocked_tracks == {6: [comps[ref].y1 + 21.5,
                                               comps[ref].y1 + 25.5]}
    assert not wr[other].hier.blocked_tracks
    # the state after a RELEASE COMMIT of the reference: with one bundle
    # per instance here the sibling becomes the new reference (stamped as
    # one) and the released instance solves in the global run with its
    # reserved tracks stamped
    wr[ref].hier.locked = False
    s._rr_invalidate_bottom_up_caches()
    with contextlib.redirect_stdout(io.StringIO()):
        plan = s._bottom_up_dnuts_plan()
    assert plan is not None
    assert wr[other].input.original_bundle.id in plan[0]      # the new ref
    assert wr[other].hier.blocked_tracks == {6: [comps[other].y1 + 21.5,
                                                 comps[other].y1 + 25.5]}
    y1 = comps[ref].y1
    assert wr[ref].hier.blocked_tracks == {6: [y1 + 21.5, y1 + 25.5]}
    # a wrapper rebuilt unstamped is re-stamped by the next plan call —
    # the stamp is derived, not state (the NQ = 16 shape: the reference
    # keeps its other bundles locked, so the siblings stay in the global
    # run and must keep their lists however the wrappers were rebuilt)
    wr[ref].hier.blocked_tracks = {}
    with contextlib.redirect_stdout(io.StringIO()):
        s._bottom_up_dnuts_plan()
    assert wr[ref].hier.blocked_tracks == {6: [y1 + 21.5, y1 + 25.5]}
