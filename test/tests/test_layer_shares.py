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

"""Fractional layer shares, Phase 3 (docs/internal/hier_layer_caps.md §6).

The share is realized two ways, by where the bundle is planned:
Tier 1 (cell-local solves) — a derived THINNED pattern (keep the first
floor(s x n_signal) SIGNAL slots per period, Q1 resolved contiguous-first)
plus a derived LayerStack with bit_pitch = unit_pitch / n_kept;
Tier 2 (global frame) — s x capacity in the planner's track-mode branch
plus the SCALAR COLLECTIVE BUDGET (Q3 resolved): one counter per
(cell-instance, shared layer), STRICT candidates refused past the lease,
with the §9.7 post-plan audit as defense-in-depth.  No shares anywhere is
byte-identical (corpus-guarded).
"""
import contextlib
import io
import sys
from pathlib import Path

import buda

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402


def _cmd(s, line):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(line)
    return buf.getvalue()


def _quiet(s, *lines):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in lines:
            s.do_command(c)


# ── the thinned pattern (plan §11 test 5) ────────────────────────────────────

_PAT8 = ("VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 "
         "VSS 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1")     # 8 signal slots, pitch 22


def _grid_session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "def_layer 2 M2 H LOW 50", "def_layer 3 M3 V LOW 50",
           "def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
           f"def_track_pattern 4 0 {_PAT8}",
           f"def_track_pattern 5 0 {_PAT8}")
    return s


def test_thinned_pattern_floor_and_identity():
    """share 0.3 on an 8-signal-slot period keeps floor(2.4) = 2 contiguous
    slots (granted 25% <= declared 30%); origin and unit_pitch survive, so
    phase congruence is untouched.  The kept slots are the FIRST signal
    slots of the period (Q1: contiguous-first)."""
    s = _grid_session()
    pat = s.routing_grid.get_layer_grid(4).global_pattern()
    tp = s._thinned_pattern(pat, 0.3)
    kept = [sl for sl in tp.slots if sl.type == "SIGNAL"]
    assert len(kept) == 2
    assert abs(tp.unit_pitch() - pat.unit_pitch()) < 1e-9
    assert tp.origin == pat.origin
    # Contiguous-first: the kept ones are the first two SIGNAL positions.
    sig_seen = 0
    for sl in tp.slots:
        if sl.type == "SIGNAL":
            sig_seen += 1
        if sl.label == "share_cut":
            assert sig_seen >= 2, "a cut slot before the kept run"
    # share 1.0 is the identity view (no derived pair at all).
    lv, thinned = s._cell_share_views("nocell")
    assert lv is s.layers and thinned == {}


def test_share_views_derived_bit_pitch():
    """The Tier-1 view is a PAIR: thinned grid + derived LayerStack with
    bit_pitch = unit_pitch / n_kept — a 25%-granted share on an 8-slot
    period makes each bit 4x wider in channel terms."""
    s = _grid_session()
    s._cell_layer_shares = {("leaf", 4): 0.3}
    lv, thinned = s._cell_share_views("leaf")
    assert 4 in thinned and lv is not s.layers
    pat = s.routing_grid.get_layer_grid(4).global_pattern()
    full_bit = s.layers.eff_bus_width(1, 1.0, 4)
    thin_bit = lv.eff_bus_width(1, 1.0, 4)
    assert abs(full_bit - pat.unit_pitch() / 8) < 1e-9
    assert abs(thin_bit - pat.unit_pitch() / 2) < 1e-9
    # Un-shared layers keep the full pitch in the same view.
    assert abs(lv.eff_bus_width(1, 1.0, 5)
               - s.layers.eff_bus_width(1, 1.0, 5)) < 1e-9
    # Clone-name groups resolve to the base cell.
    s._bu_clone_cells = {"leaf90": "leaf"}
    lv2, thinned2 = s._cell_share_views("leaf90")
    assert 4 in thinned2


def test_share_command_validation():
    s = _grid_session()
    out = _cmd(s, "set_cell_layer_share someCell M9 30")
    assert "unknown layer" in out
    out = _cmd(s, "set_cell_layer_share someCell M2 30")   # no pattern on M2
    assert "no track pattern" in out
    out = _cmd(s, "set_cell_layer_share someCell M4 5")    # floor(0.05*8)=0
    assert "0 tracks" in out and "minimum meaningful share" in out
    out = _cmd(s, "set_cell_layer_share someCell M4 150")
    assert "must be in (0, 100]" in out
    out = _cmd(s, "set_cell_layer_share someCell M4 30")
    assert "2/8 slot(s)/period kept" in out and "granted 25.0%" in out
    assert s._cell_layer_shares[("someCell", 4)] == 0.3
    # 100% = explicit full use: the share is removed.
    out = _cmd(s, "set_cell_layer_share someCell M4 100")
    assert "share removed" in out
    assert ("someCell", 4) not in s._cell_layer_shares


def test_share_persistence_round_trip(tmp_path):
    """Write-through + restore + '* off' clearing, the v20 share table."""
    p = str(tmp_path / "shares.bdb")
    db = buda.BDB(p)
    db.add_cell("leaf", 100, 80)
    db.add_inst("L1", "leaf", "", 0, 0)
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = db
    _quiet(s, "def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
           f"def_track_pattern 4 0 {_PAT8}",
           "set_cell_layer_share leaf M4 30")
    assert db.cell_layer_shares("leaf") == [(4, 0.3)]
    del s, db

    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s2.do_command(f"open_bdb {p}")
    assert "restored 1 persisted share(s)" in buf.getvalue()
    assert s2._cell_layer_shares == {("leaf", 4): 0.3}
    _quiet(s2, "def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
           f"def_track_pattern 4 0 {_PAT8}")
    _cmd(s2, "set_cell_layer_cap * off")
    assert not s2._cell_layer_shares
    assert s2.bdb.layer_share_cells() == []


# ── Tier 2: the scalar collective budget (plan §11 test 8) ───────────────────

def _two_bus_session():
    """Two independent 8-bit buses, both governed by one synthetic share
    group on M4 — the collective-budget vehicle: each alone fits the
    budget, both together do not."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = ["def_layer 2 M2 H LOW 50",
            "def_layer 3 M3 V LOW 50",
            "def_layer 4 M4 H TOP 50",
            "def_layer 5 M5 V TOP 50",
            "add_block A 0 1000 200 1400",
            "add_block B 2400 1000 2600 1400",
            "add_block C 0 2000 200 2400",
            "add_block D 2400 2000 2600 2400",
            "add_bus x[8] A.p B.p",
            "add_bus y[8] C.p D.p",
            "run_bundler", "generate_topologies"]
    _quiet(s, *cmds)
    return s


def test_scalar_budget_refuses_second_bundle():
    """Both bundles lease M4 at a budget that fits ONE trunk: the first
    commits on M4, the second's M4 candidates fail the STRICT budget gate
    and it lands on the in-band alternative (M2) — still STRICT, no
    overflow commits.  The gate is per share GROUP, not per bundle: the
    exact hole per-bundle scaling leaves (two 30% bundles taking 60%)."""
    s = _two_bus_session()
    eff = s.layers.eff_bus_width(8, s.bundles[0].input.width, 4)
    for w in s.bundles:
        w.input.layer_shares = [(4, 0.5)]
        w.input.share_group = "leaf@i0"
        w.input.share_budgets = [(4, 1.5 * eff)]   # one fits, two do not
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("run_planner")
    out = buf.getvalue()
    assert "ALLOW_OVERFLOW" not in out and "BEST_EFFORT" not in out
    h_layers = []
    for w in s.bundles:
        t = w.input.candidates[w.plan.selected_topology_index]
        for si, l in enumerate(w.plan.seg_layers):
            seg = t.segments[si]
            if seg.start.y == seg.end.y:      # H segment
                h_layers.append(l)
    assert h_layers.count(4) == 1, (h_layers, "budget must admit exactly one")
    assert h_layers.count(2) >= 1, h_layers
    # No shares: both take the TOP layer (the gate is share-scoped).
    s2 = _two_bus_session()
    with contextlib.redirect_stdout(io.StringIO()):
        s2.do_command("run_planner")
    h2 = [l for w in s2.bundles
          for si, l in enumerate(w.plan.seg_layers)
          if (w.input.candidates[w.plan.selected_topology_index]
              .segments[si].start.y ==
              w.input.candidates[w.plan.selected_topology_index]
              .segments[si].end.y)]
    assert h2.count(4) == 2, h2


def test_share_audit_fires_on_over_consumption():
    """§9.7: a pinned (BEST_EFFORT-forced) commit past the lease is caught
    by the post-plan audit LOUD — never silent."""
    s = _two_bus_session()
    eff = s.layers.eff_bus_width(8, s.bundles[0].input.width, 4)
    for w in s.bundles:
        w.input.layer_shares = [(4, 0.5)]
        w.input.share_group = "leaf@i0"
        w.input.share_budgets = [(4, 0.5 * eff)]   # neither fits
        # Force the M4 assignment past the STRICT gate via layer pins.
        n_segs = len(w.input.candidates[0].segments)
        pins = []
        for si in range(n_segs):
            seg = w.input.candidates[0].segments[si]
            pins.append(4 if seg.start.y == seg.end.y else 5)
        w.input.topology_pinned = True
        w.plan.selected_topology_index = 0
        w.input.pinned_seg_layers = pins
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_planner")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        n_over = s._audit_share_budgets(s.bundles)
    assert n_over >= 1
    assert "exceeds the collective budget" in buf.getvalue()


# ── Tier 1: bottom-up integration (plan §11 test 7) ──────────────────────────

def test_bottom_up_shared_cell_bits_on_kept_slots():
    """A bottom-up cell banded at M3 with `share M4 50`: the cell-local
    solve prices M4 at the thinned bit pitch, the DNUTS reference solve
    runs under the thinned override, and every cell bit that lands on M4
    sits on a KEPT slot (the first 2 of each 4-signal period); usage stays
    within the lease.  End-to-end clean (0 unplaced)."""
    pat4 = "VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1"    # 4 signal slots, pitch 13
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst("proc_i1", "proc_cell", "", 0, 0)
    db.add_inst("proc_i2", "proc_cell", "", 520, 0)   # 520 = 40*13: on phase
    for i in range(4):
        db.add_net_pins(f"ab1_{i}", "proc_i1/pa_i.out", ["proc_i1/pb_i.in"])
        db.add_net_pins(f"ab2_{i}", "proc_i2/pa_i.out", ["proc_i2/pb_i.in"])
    buda.BustermGen(db).derive(1)
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = db
    _quiet(s,
           "def_layer 2 M2 H 50", "def_layer 3 M3 V 50",
           "def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
           "def_track_pattern 2 0 _ 1 5", "def_track_pattern 3 0 _ 1 5",
           f"def_track_pattern 4 0 {pat4}", "def_track_pattern 5 0 _ 1 5",
           "set_bottom_up proc_cell",
           "set_cell_layer_cap proc_cell M3",
           "set_cell_layer_share proc_cell M4 50",
           "run_hier_bundler", "generate_hier_topologies",
           "run_planner hier", "run_nuts", "run_detailed_nuts")
    assert s.detailed_result is not None
    assert s.detailed_result.num_unplaced == 0
    locked = [w for w in s.bundles if w.hier.locked]
    assert locked
    locked_ids = {w.input.original_bundle.id for w in locked}
    # Every cell bit on M4 sits on a kept slot: the thinned period keeps
    # the FIRST 2 of the 4 signal positions.  Signal centres of the full
    # pattern: VDD(2)+1 -> 4, 6, 8, 10 + n*13 (centres 4.5, 6.5, 8.5,
    # 10.5); kept = the first two per period.
    pat = s.routing_grid.get_layer_grid(4).global_pattern()
    period = pat.unit_pitch()
    kept_rel = []
    tp = s._thinned_pattern(pat, 0.5)
    pos = tp.origin
    for sl in tp.slots:
        if sl.type == "SIGNAL":
            kept_rel.append(round((pos + sl.width / 2) % period, 6))
        pos += sl.width + sl.space_after
    m4_bits = [ns for ns in s.detailed_result.net_segments
               if ns.layer == 4 and ns.bundle_id in locked_ids]
    for ns in m4_bits:
        rel = round(ns.track_position % period, 6)
        assert rel in kept_rel, (ns.track_position, rel, kept_rel)


def test_no_shares_resolution_untouched():
    """Bands without shares resolve exactly as before Phase 3 — masks only,
    no layer_shares/share_group on the wrappers (the byte-identity path the
    corpus guards at flow level)."""
    s = _grid_session()
    _quiet(s, "add_block A 0 1000 200 1400",
           "add_block B 2400 1000 2600 1400",
           "add_bus x[8] A.p B.p", "run_bundler", "generate_topologies")
    w = s.bundles[0]
    w.input.original_bundle.cell_context = "leaf"
    s._cell_layer_policy = {"leaf": (-1, 3)}
    with contextlib.redirect_stdout(io.StringIO()):
        s._apply_layer_policies()
    assert list(w.input.allowed_layers) == [2, 3]
    assert list(w.input.layer_shares) == []
    assert w.input.share_group == ""


def test_share_extends_mask_above_cap():
    """§4 resolution: a share GRANTS a bounded slice above the cap — the
    shared layer joins allowed_layers, carrying its fraction."""
    s = _grid_session()
    _quiet(s, "add_block A 0 1000 200 1400",
           "add_block B 2400 1000 2600 1400",
           "add_bus x[8] A.p B.p", "run_bundler", "generate_topologies")
    w = s.bundles[0]
    w.input.original_bundle.cell_context = "leaf"
    s._cell_layer_policy = {"leaf": (-1, 3)}
    s._cell_layer_shares = {("leaf", 4): 0.3}
    with contextlib.redirect_stdout(io.StringIO()):
        s._apply_layer_policies()
    assert list(w.input.allowed_layers) == [2, 3, 4]
    assert list(w.input.layer_shares) == [(4, 0.3)]
    assert abs(w.input.share_of(4) - 0.3) < 1e-9
