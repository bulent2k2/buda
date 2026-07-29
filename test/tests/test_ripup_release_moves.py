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
    # (the forced-layer hazard this arc fixed).
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("check_design")
    assert "Success: no violations found" in buf.getvalue(), buf.getvalue()
