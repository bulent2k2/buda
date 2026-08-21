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

"""NDR phase 1 (path A, slot-quantized): demand conversion, CLI
declaration/attachment, rule-class split, DNUTS placement, R3
realizability, and the R12 byte-identity guard."""
import contextlib
import io

import pytest

import buda
import buda_cli
from buda_cmds import ndr_cmds


# ── The single-sourced demand conversion (R4) ──────────────────────────────

def _spec(width=1, guard=0, mode=0, per_n=0):
    s = buda.NdrSpec()
    s.width_slots, s.guard_slots = width, guard
    s.shield_mode, s.shield_per_n = mode, per_n
    return s


def test_demand_and_layout_are_lockstep():
    # The layout is the placement-side rendering of the demand arithmetic —
    # they must agree for every shape (the R4 no-drift property).
    shapes = [
        _spec(),                            # inactive
        _spec(width=2),
        _spec(width=3, guard=2),
        _spec(guard=1),
        _spec(mode=1),                      # flank-the-bus
        _spec(width=2, guard=1, mode=1),
        _spec(width=2, mode=2),             # flank-every-bit
        _spec(width=2, guard=1, mode=3, per_n=2),
        _spec(mode=3, per_n=3),
    ]
    for s in shapes:
        for nbits in (1, 2, 3, 4, 8, 16):
            du = buda.ndr_group_demand(s, nbits)
            layout = buda.ndr_run_layout(s, nbits)
            assert len(layout) == du, (s.width_slots, s.guard_slots,
                                       s.shield_mode, s.shield_per_n, nbits)
            assert layout.count("B") == nbits


def test_group_demand_is_group_level_not_per_bit():
    # The R4 counter-example from review: a flanked 8-bit group needs TWO
    # shields; two 4-bit groups need FOUR.  Group demand is NOT additive.
    s = _spec(width=1, guard=0, mode=1)
    # active() needs a constraint; flank-only spec:
    assert s.active()
    assert buda.ndr_group_demand(s, 8) == 8 + 2
    assert 2 * buda.ndr_group_demand(s, 4) == 2 * (4 + 2)
    assert buda.ndr_group_demand(s, 8) < 2 * buda.ndr_group_demand(s, 4)


def test_demand_shapes():
    # 4 bits x 2 slots + 3 interior guard gaps (1 each) + 2 end shields.
    assert buda.ndr_group_demand(_spec(2, 1, 1), 4) == 8 + 3 + 2
    # flank-every-bit: interior gaps are shields, ends are shields.
    assert buda.ndr_group_demand(_spec(1, 0, 2), 4) == 4 + 3 + 2
    # per:2 on 4 bits: shield after bit 2 (1 interior), guards elsewhere.
    assert buda.ndr_group_demand(_spec(1, 1, 3, 2), 4) == 4 + 1 + 2 * 1 + 2
    # unshielded spacing keeps end guards (clearance to neighbors).
    assert buda.ndr_group_demand(_spec(1, 2, 0), 2) == 2 + 2 + 2 * 2
    # inactive spec is the identity.
    assert buda.ndr_group_demand(_spec(), 7) == 7


# ── CLI declaration / attachment ───────────────────────────────────────────

def _bare_session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    return s


def _run(s, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


def test_def_ndr_declares_and_quantizes():
    s = _bare_session()
    out = _run(s, "def_ndr clk2x width x2 spacing x2 shield bus net GND")
    assert "width x2 -> 2 slot(s)/bit" in out
    assert "spacing x2 -> 1 guard slot(s)/gap" in out
    assert "shield bus (net GND)" in out


@pytest.mark.parametrize("cmd", [
    "def_ndr dup width x2",                       # (declared twice below)
    "def_ndr r width 2",                          # absolute form refused
    "def_ndr r width x0.5",                       # multiplier < 1
    "def_ndr r shield sideways",                  # unknown shield mode
    "def_ndr r shield per:0",                     # per-N needs N >= 1
    "def_ndr r bogus x2",                         # unknown token
    "def_ndr r",                                  # constrains nothing
])
def test_def_ndr_validation_is_loud(cmd):
    s = _bare_session()
    if cmd.startswith("def_ndr dup"):
        _run(s, cmd)                              # first declaration OK
    with pytest.raises(SystemExit):
        _run(s, cmd)


def test_set_ndr_unknown_rule_is_loud():
    s = _bare_session()
    with pytest.raises(SystemExit):
        _run(s, "set_ndr clk_ nosuchrule")


def test_prefix_resolution_longest_wins():
    s = _bare_session()
    _run(s, "def_ndr a width x2")
    _run(s, "def_ndr b width x3")
    _run(s, "def_ndr c width x4")
    _run(s, "set_ndr * a")
    _run(s, "set_ndr clk_ b")
    _run(s, "set_ndr clk_fast_ c")
    assert ndr_cmds.ndr_rule_for_net(s, "data_0") == "a"       # global
    assert ndr_cmds.ndr_rule_for_net(s, "clk_0") == "b"
    assert ndr_cmds.ndr_rule_for_net(s, "clk_fast_0") == "c"   # longest
    _run(s, "set_ndr clk_ off")
    assert ndr_cmds.ndr_rule_for_net(s, "clk_0") == "a"        # falls back


def test_hier_bundler_accepts_scopes():
    # R2d: run_hier_bundler no longer refuses declared scopes — the hier
    # rule-class split + spec propagation handle them (test_ndr_hier.py
    # pins the real-vehicle behavior).  A bare session still fails on the
    # missing BDB, NOT on the scopes.
    s = _bare_session()
    _run(s, "def_ndr r width x2")
    _run(s, "set_ndr clk_ r")
    out = _run(s, "run_hier_bundler")
    assert "requires an open BDB" in out
    assert "flat flow" not in out


# ── End-to-end flow ────────────────────────────────────────────────────────

_PATTERN = ("0 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 "
            "GND 2 1")

_FLOW_SETUP = [
    "add_block blkA 0 0 200 400",
    "add_block blkB 800 0 1000 400",
    "add_bus clk[4] blkA.p blkB.q",
    "add_bus data[8] blkA.d blkB.e",
    "def_layer 3 M3 H 20",
    "def_layer 4 M4 V 20",
    "def_layer 5 M5 H TOP 20",
    "def_layer 6 M6 V TOP 20",
]

_FLOW_RUN = [
    "run_bundler STRICT",
    "generate_topologies",
    "set_track_pitch 3",
    "run_planner 1",
    f"def_track_pattern 3 {_PATTERN}",
    f"def_track_pattern 4 {_PATTERN}",
    f"def_track_pattern 5 {_PATTERN}",
    f"def_track_pattern 6 {_PATTERN}",
    "run_nuts",
    "run_detailed_nuts",
]


def _flow(ndr_cmd_lines):
    s = _bare_session()
    out = []
    for c in _FLOW_SETUP + ndr_cmd_lines + _FLOW_RUN:
        out.append(_run(s, c))
    return s, "".join(out)


def test_end_to_end_shielded_double_width_bus():
    s, out = _flow(["def_ndr clk2x width x2 spacing x2 shield bus net GND",
                    "set_ndr clk_ clk2x"])
    # The mixed STRICT bundle (clk+data share endpoints) split LOUDLY.
    assert "rule-uniform part(s)" in out
    dr = s.detailed_result
    assert dr.num_unplaced == 0
    clk_bid = next(w.input.original_bundle.id for w in s.bundles
                   if w.input.ndr.active())
    rows = sorted((ns for ns in dr.net_segments if ns.bundle_id == clk_bid),
                  key=lambda n: n.track_position)
    shields = [n for n in rows if n.is_shield]
    bits    = [n for n in rows if not n.is_shield]
    # flank-the-bus: exactly 2 shields, OUTSIDE the 4 bits.
    assert len(shields) == 2 and len(bits) == 4
    assert shields[0] is rows[0] and shields[1] is rows[-1]
    assert {n.bit_index for n in shields} == {-1, -2}
    # 2-slot bits: wider than one slot (slot width 1, pitch 2 -> 3.0).
    for n in bits:
        assert n.width == pytest.approx(3.0)
    # Spacing: adjacent bits at least one empty slot apart (pitch 2 per
    # slot, 2-slot bit footprint = centres >= 6 apart with a guard).
    positions = sorted(n.track_position for n in bits)
    for a, b in zip(positions, positions[1:]):
        assert b - a >= 6.0
    # Shields carry no vias.
    assert all(v.bit_index >= 0 for v in dr.net_vias)
    # The default data bundle is untouched: 1-slot bits, no shields.
    data = [ns for ns in dr.net_segments if ns.bundle_id != clk_bid]
    assert data and all(not n.is_shield for n in data)
    assert all(n.width == pytest.approx(1.0) for n in data)


def test_planner_charges_group_demand():
    # The clk wrapper's segment charge must be the 13-slot group demand,
    # not 4 bits — visible through ndr_group_demand on the stamped spec.
    s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus net GND",
                  "set_ndr clk_ clk2x"])
    w = next(w for w in s.bundles if w.input.ndr.active())
    nb = len(w.input.original_bundle.get_net_names())
    assert nb == 4
    assert buda.ndr_group_demand(w.input.ndr, nb) == 13
    # Abstract NUTS reserved the demand footprint: the clk TrackSegment is
    # wider than the 8-bit default bundle's despite carrying half the bits.
    widths = {ts.bundle_id: ts.width for ts in s.nuts_result.segments}
    clk_bid = w.input.original_bundle.id
    data_bid = next(x.input.original_bundle.id for x in s.bundles
                    if not x.input.ndr.active())
    assert widths[clk_bid] > widths[data_bid]


def test_r3_realizability_refuses_unhostable_rule():
    # width x4 needs 4 physically contiguous SIGNAL slots; the pattern's
    # runs are broken by rails every 2 slots — run_detailed_nuts must
    # refuse LOUDLY (R3), not strand silently.
    narrow = ("0 VDD 2 1 _ 1 1 _ 1 1 GND 2 1")
    s = _bare_session()
    for c in _FLOW_SETUP + ["def_ndr wide width x4", "set_ndr clk_ wide",
                            "run_bundler STRICT", "generate_topologies",
                            "set_track_pitch 3", "run_planner 1",
                            f"def_track_pattern 3 {narrow}",
                            f"def_track_pattern 4 {narrow}",
                            f"def_track_pattern 5 {narrow}",
                            f"def_track_pattern 6 {narrow}",
                            "run_nuts"]:
        _run(s, c)
    with pytest.raises(SystemExit):
        _run(s, "run_detailed_nuts")


def test_r12_declared_but_unattached_rule_is_inert():
    # A declared rule with NO set_ndr scope must be byte-identical to no
    # declaration at all — attachment, not declaration, activates the path.
    base_s, _ = _flow([])
    ndr_s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus"])
    base = [(ns.bundle_id, ns.seg_idx, ns.bit_index, ns.track_position,
             ns.width, ns.span_lo, ns.span_hi)
            for ns in base_s.detailed_result.net_segments]
    ndr = [(ns.bundle_id, ns.seg_idx, ns.bit_index, ns.track_position,
            ns.width, ns.span_lo, ns.span_hi)
           for ns in ndr_s.detailed_result.net_segments]
    assert base == ndr
    assert not any(ns.is_shield for ns in ndr_s.detailed_result.net_segments)


def test_report_wirelength_separates_shield_metal():
    s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus",
                  "set_ndr clk_ clk2x"])
    out = _run(s, "report_wirelength")
    assert "NDR shield metal:" in out
    # 2 shields x 600 span.
    assert "NDR shield metal: 1200" in out


def test_check_design_clean_on_the_demo_shape():
    s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus",
                  "set_ndr clk_ clk2x"])
    out = _run(s, "check_design")
    assert "no violations" in out


# ── Codex on #616: shields must not count as placed signal bits ────────────

def test_shields_do_not_mask_signal_opens_in_rr_accounting():
    # A culled/missing signal bit must keep the bundle in the stage-b open
    # list even though emitted shields pad the row count past the expected
    # bit count (the masking direction: 3 bits + 2 shields = 5 rows >= 4
    # expected reads "complete" if shields count).
    s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus",
                  "set_ndr clk_ clk2x"])
    clk_bid = next(w.input.original_bundle.id for w in s.bundles
                   if w.input.ndr.active())
    assert s._rr_open_bundles() == []          # clean baseline
    dr = s.detailed_result
    kept = [ns for ns in dr.net_segments
            if not (ns.bundle_id == clk_bid and not ns.is_shield
                    and ns.bit_index == 0)]
    assert len(kept) == len(dr.net_segments) - 1
    dr.net_segments = kept                     # drop ONE signal bit
    assert clk_bid in s._rr_open_bundles()


# ── Codex on #616: R3 validation must enumerate REAL declared layer ids ────

def test_r3_validation_reaches_high_layer_ids():
    # def_layer imposes no id bound; a hard-coded 0..63 range silently
    # skipped e.g. layer 70, letting an unhostable rule strand at DNUTS
    # instead of the promised up-front hard error.
    narrow = "0 VDD 2 1 _ 1 1 _ 1 1 GND 2 1"
    s = _bare_session()
    for c in ["add_block blkA 0 0 200 400",
              "add_block blkB 800 0 1000 400",
              "add_bus clk[4] blkA.p blkB.q",
              "def_layer 70 M70 H TOP 20",
              "def_layer 71 M71 V TOP 20",
              "def_ndr wide width x4",
              "set_ndr clk_ wide",
              "run_bundler STRICT",
              "generate_topologies",
              "run_planner 1",
              f"def_track_pattern 70 {narrow}",
              f"def_track_pattern 71 {narrow}",
              "run_nuts 3"]:
        _run(s, c)
    with pytest.raises(SystemExit):
        _run(s, "run_detailed_nuts")


def test_r3_restricted_rule_on_patternless_layer_is_loud():
    # A rule EXPLICITLY restricted to a layer with no track pattern can
    # never be realized — hard error at first resolution, not a strand.
    s = _bare_session()
    for c in _FLOW_SETUP + ["def_ndr wide width x2 layers 5",
                            "set_ndr clk_ wide",
                            "run_bundler STRICT",
                            "generate_topologies",
                            "run_planner 1",
                            f"def_track_pattern 3 {_PATTERN}",
                            f"def_track_pattern 4 {_PATTERN}",
                            f"def_track_pattern 6 {_PATTERN}",
                            "run_nuts 3"]:
        _run(s, c)      # note: NO pattern for layer 5
    with pytest.raises(SystemExit):
        _run(s, "run_detailed_nuts")


# ── v21: BDB rule persistence (ndr_architecture.md §4) ─────────────────────

_BDB_FLOW_NDR = ["def_ndr clk2x width x2 spacing x2 shield bus net GND",
                 "set_ndr clk_ clk2x"]


def _bdb_flow(tmp_path, ndr_cmds_list=None, pre_open_cmds=None):
    """The standard flow with a BDB open from the start (persists bundles,
    plan, and — v21 — rules/scopes)."""
    s = _bare_session()
    out = []
    for c in ((pre_open_cmds or [])
              + [f"open_bdb {tmp_path}/ndr.bdb"]
              + _FLOW_SETUP
              + (_BDB_FLOW_NDR if ndr_cmds_list is None else ndr_cmds_list)
              + _FLOW_RUN):
        out.append(_run(s, c))
    return s, "".join(out)


def test_v21_rules_and_scopes_roundtrip(tmp_path):
    s1, _ = _bdb_flow(tmp_path)
    del s1
    s2 = _bare_session()
    out = _run(s2, f"open_bdb {tmp_path}/ndr.bdb")
    assert "restored 1 rule(s) and 1 scope(s)" in out
    assert ndr_cmds.ndr_rule_for_net(s2, "clk_0") == "clk2x"
    assert ndr_cmds.ndr_rule_for_net(s2, "data_0") is None
    r = s2._ndr_rules["clk2x"]
    assert r["width_x"] == 2.0 and r["spacing_x"] == 2.0
    assert r["shield_mode"] == 1 and r["shield_net"] == "GND"
    dump = _run(s2, "dump_ndr")
    assert "(restored from BDB)" in dump


def test_v21_session_typed_wins_and_converges(tmp_path):
    s1, _ = _bdb_flow(tmp_path)
    del s1
    s2 = _bare_session()
    _run(s2, "def_ndr clk2x width x3")            # typed BEFORE open
    _run(s2, f"open_bdb {tmp_path}/ndr.bdb")
    assert s2._ndr_rules["clk2x"]["width_x"] == 3.0   # session wins
    # ...and the BDB converged to the session's declaration.
    rows = {r.name: r for r in s2.bdb.ndr_rules()}
    assert rows["clk2x"].width_x == 3.0


def test_v21_bundle_rows_carry_the_governing_rule(tmp_path):
    s, _ = _bdb_flow(tmp_path)
    stamps = {b.id: b.ndr_rule for b in s.bdb.all_bundles()}
    clk_bid = str(next(w.input.original_bundle.id for w in s.bundles
                       if w.input.ndr.active()))
    # The stamp is the PRICING FINGERPRINT (name + quantized spec), so the
    # VOID audit is self-contained in the bundle row even if the rule row
    # is later overwritten (Codex on #620).
    assert stamps[clk_bid] == "clk2x|w2|g1|s1|p0|nGND|L"
    assert all(v == "" for k, v in stamps.items() if k != clk_bid)


def _resume_session(tmp_path, pre_load_cmds=None):
    """Fresh session re-declaring the setup, then load_pipeline."""
    s = _bare_session()
    out = []
    for c in ([f"open_bdb {tmp_path}/ndr.bdb"]
              + _FLOW_SETUP
              + [f"def_track_pattern 3 {_PATTERN}",
                 f"def_track_pattern 4 {_PATTERN}",
                 f"def_track_pattern 5 {_PATTERN}",
                 f"def_track_pattern 6 {_PATTERN}"]
              + (pre_load_cmds or [])
              + ["load_pipeline"]):
        out.append(_run(s, c))
    return s, "".join(out)


def test_v21_resume_keeps_plan_when_rules_unchanged(tmp_path):
    s1, _ = _bdb_flow(tmp_path)
    del s1
    s2, out = _resume_session(tmp_path)
    assert "VOIDED" not in out
    w = next(w for w in s2.bundles if w.input.ndr.active())
    assert w.input.ndr.rule_name == "clk2x"
    assert w.input.ndr.width_slots == 2
    assert w.plan.selected_topology_index >= 0    # plan survived


def test_v21_void_on_scope_removed(tmp_path):
    s1, _ = _bdb_flow(tmp_path)
    clk_bid = next(w.input.original_bundle.id for w in s1.bundles
                   if w.input.ndr.active())
    del s1
    # The scope is cleared AFTER open (restored, then typed away) — the
    # governed bundle's plan was priced under clk2x and must VOID.
    s2, out = _resume_session(tmp_path, pre_load_cmds=["set_ndr clk_ off"])
    assert "VOIDED" in out and "clk2x" in out
    w = next(w for w in s2.bundles
             if w.input.original_bundle.id == clk_bid)
    assert w.plan.selected_topology_index == -1
    # The default-rule bundle's plan is untouched.
    other = next(w for w in s2.bundles
                 if w.input.original_bundle.id != clk_bid)
    assert other.plan.selected_topology_index >= 0


def test_v21_void_on_rule_content_change(tmp_path):
    s1, _ = _bdb_flow(tmp_path)
    del s1
    # A session-typed same-name rule with DIFFERENT content shadows the
    # persisted definition: same resolution name, different pricing basis.
    s2 = _bare_session()
    for c in ["def_ndr clk2x width x3"] + [f"open_bdb {tmp_path}/ndr.bdb"] \
             + _FLOW_SETUP \
             + [f"def_track_pattern 3 {_PATTERN}",
                f"def_track_pattern 4 {_PATTERN}",
                f"def_track_pattern 5 {_PATTERN}",
                f"def_track_pattern 6 {_PATTERN}"]:
        _run(s2, c)
    out = _run(s2, "load_pipeline")
    # x3 quantizes to 3 slots vs the checkpoint's 2 — the pricing
    # fingerprints differ, so the plan voids (both fingerprints printed).
    assert "VOIDED" in out and "demand was priced under the old rule" in out
    assert "clk2x|w3" in out and "clk2x|w2" in out


def test_v21_pre_v21_db_migrates(bdb_input, tmp_path):
    # Build a GENUINE v20 database (the committed fixtures regenerate at
    # the current version, so a real downgrade is constructed here: drop
    # the v21 tables + column, stamp user_version=20) and reopen — the
    # migration must bring it to v21 with usable empty NDR tables and the
    # '' governing-rule default on every migrated bundle row.
    import shutil
    import sqlite3
    src = bdb_input("hier_mixed")
    path = str(tmp_path / "v20.bdb")
    shutil.copy(src, path)
    con = sqlite3.connect(path)
    con.executescript(
        "DROP TABLE ndr_scope; DROP TABLE ndr_rule;"
        "ALTER TABLE bundle DROP COLUMN ndr_rule;"
        "PRAGMA user_version = 20;")
    con.close()
    db = buda.BDB(path)
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    assert list(db.ndr_rules()) == []
    assert list(db.ndr_scopes()) == []
    r = buda.NdrRuleRow(); r.name = "post_migration"; r.width_x = 2.0
    db.set_ndr_rule(r)                            # tables usable post-migrate
    assert [x.name for x in db.ndr_rules()] == ["post_migration"]
    assert b_ndr_default(db)


def b_ndr_default(db):
    # Migrated bundle rows read an empty governing rule (the '' default).
    return all(b.ndr_rule == "" for b in db.all_bundles())


def test_v21_scope_fk_is_loud(tmp_path):
    db = buda.BDB(str(tmp_path / "fk.bdb"))
    with pytest.raises(RuntimeError):
        db.set_ndr_scope("clk_", "nosuchrule")


def test_v21_void_survives_writethrough_before_load(tmp_path):
    # Codex on #620 (P1): re-declare the rule under the same name with the
    # BDB open, then EXIT WITHOUT load_pipeline — the converge write-through
    # overwrites the only stored copy of the old definition.  The pricing
    # fingerprint stamped on the bundle row must still void the plan in the
    # NEXT session.
    s1, _ = _bdb_flow(tmp_path)
    del s1
    s2 = _bare_session()
    _run(s2, "def_ndr clk2x width x3")            # shadow, typed
    _run(s2, f"open_bdb {tmp_path}/ndr.bdb")      # converge overwrites row
    del s2                                        # exit before load_pipeline
    s3, out = _resume_session(tmp_path)
    assert "VOIDED" in out
    w = next(w for w in s3.bundles if w.input.ndr.active())
    assert w.plan.selected_topology_index == -1


def test_v21_void_on_non_leading_net_scope_change(tmp_path):
    # Codex on #620 (P1): a scope matching only a NON-leading net turns the
    # checkpoint's rule-uniform bundle mixed — resolving nets[0] alone
    # would accept the stale plan and split.
    s1, _ = _bdb_flow(tmp_path)
    del s1
    s2, out = _resume_session(
        tmp_path, pre_load_cmds=["def_ndr wide2 width x2",
                                 "set_ndr clk_2 wide2"])   # matches clk_2 only
    assert "MIXED rules" in out and "re-run the bundler" in out
    assert "VOIDED" in out


def test_v21_quantization_invariant_change_keeps_plan(tmp_path):
    # A content change that does NOT move the quantized demand (x2 -> x1.7,
    # both 2 slots / 1 guard) keeps the plan: the pricing is unchanged, so
    # voiding would be false alarm.
    s1, _ = _bdb_flow(tmp_path)
    del s1
    s2 = _bare_session()
    _run(s2, "def_ndr clk2x width x1.7 spacing x1.7 shield bus net GND")
    for c in [f"open_bdb {tmp_path}/ndr.bdb"] + _FLOW_SETUP \
             + [f"def_track_pattern 3 {_PATTERN}",
                f"def_track_pattern 4 {_PATTERN}",
                f"def_track_pattern 5 {_PATTERN}",
                f"def_track_pattern 6 {_PATTERN}"]:
        _run(s2, c)
    out = _run(s2, "load_pipeline")
    assert "VOIDED" not in out
    w = next(w for w in s2.bundles if w.input.ndr.active())
    assert w.plan.selected_topology_index >= 0


# ── R9 typed audit (NDR_WIDTH / NDR_SPACING / NDR_SHIELD) ──────────────────

def test_shield_net_predicate():
    m = buda.ndr_shield_net_matches
    assert m("GND", "GND") and m("GND", "vss") and m("VSS", "Ground")
    assert m("VDD", "VCC") and m("vdd", "POWER")
    assert not m("GND", "VDD") and not m("VSS", "power")
    assert m("AVSS_Q", "avss_q")            # custom labels: exact only
    assert not m("AVSS_Q", "GND")


def _ns_copy(ns, **overrides):
    c = buda.NetSegment()
    for f in ("bundle_id", "seg_idx", "bit_index", "track_position",
              "width", "layer", "span_lo", "span_hi", "is_shield"):
        setattr(c, f, overrides.get(f, getattr(ns, f)))
    return c


def _audited(s):
    return _run(s, "check_design dnuts")


def test_r9_clean_shielded_flow_has_no_ndr_violations():
    s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus net GND",
                  "set_ndr clk_ clk2x"])
    out = _audited(s)
    assert "no violations" in out
    assert "NDR_" not in out


def test_r9_missing_shield_is_loud():
    # The keepout cull (or any future path) removing a shield leaves the
    # bus unshielded — NDR_SHIELD, not silence.
    s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus net GND",
                  "set_ndr clk_ clk2x"])
    dr = s.detailed_result
    clk_bid = next(w.input.original_bundle.id for w in s.bundles
                   if w.input.ndr.active())
    kept, dropped_one = [], False
    for ns in dr.net_segments:
        if not dropped_one and ns.bundle_id == clk_bid and ns.is_shield:
            dropped_one = True
            continue
        kept.append(ns)
    dr.net_segments = kept
    out = _audited(s)
    assert "NDR_SHIELD" in out and "expects 2 shield wire(s)" in out


def test_r9_under_width_bit_is_loud():
    # A governed bit placed at DEFAULT width (1 slot) violates the rule.
    s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus net GND",
                  "set_ndr clk_ clk2x"])
    dr = s.detailed_result
    clk_bid = next(w.input.original_bundle.id for w in s.bundles
                   if w.input.ndr.active())
    rows = []
    shrunk = False
    for ns in dr.net_segments:
        if not shrunk and ns.bundle_id == clk_bid and not ns.is_shield:
            rows.append(_ns_copy(ns, width=1.0))
            shrunk = True
        else:
            rows.append(ns)
    dr.net_segments = rows
    out = _audited(s)
    # The summary collapses per-bit messages into the registered kind
    # reason; --verbose-conn would show the NDR_WIDTH literal.
    assert "governed bit narrower than its rule's width" in out


def test_r9_foreign_wire_in_run_is_loud():
    # A foreign bundle's bit dropped onto a guard slot inside the reserved
    # run violates the rule's clearance.
    s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus net GND",
                  "set_ndr clk_ clk2x"])
    dr = s.detailed_result
    clk_bid = next(w.input.original_bundle.id for w in s.bundles
                   if w.input.ndr.active())
    clk_rows = [ns for ns in dr.net_segments if ns.bundle_id == clk_bid]
    tracks = sorted(ns.track_position for ns in clk_rows)
    gap_track = 0.5 * (tracks[1] + tracks[2])       # inside the run
    donor = next(ns for ns in dr.net_segments
                 if ns.bundle_id != clk_bid and not ns.is_shield)
    intruder = _ns_copy(donor, track_position=gap_track,
                        layer=clk_rows[0].layer,
                        span_lo=clk_rows[0].span_lo,
                        span_hi=clk_rows[0].span_hi)
    dr.net_segments = list(dr.net_segments) + [intruder]
    out = _audited(s)
    assert "foreign wire inside a rule's reserved run" in out


def test_r9_ungoverned_flow_output_unchanged():
    # No rules: the audit adds nothing — not even a header line.
    s, _ = _flow([])
    out = _audited(s)
    assert "NDR_" not in out and "no violations" in out


def test_r9_culled_bit_keeps_intended_shield_layout():
    # Codex #622: a keepout-culled SIGNAL bit shrinks the placed-bit list,
    # but the expected shield arrangement must come from the segment's
    # INTENDED membership — the surviving shields still match the declared
    # bus, and the lost bit is already reported UNPLACED.  shield bit mode:
    # layout for 4 bits has 5 shields; recomputing it for 3 surviving bits
    # would expect 4 and spuriously flag the correct 5.
    s, _ = _flow(["def_ndr clkb width x2 shield bit net GND",
                  "set_ndr clk_ clkb"])
    dr = s.detailed_result
    clk_bid = next(w.input.original_bundle.id for w in s.bundles
                   if w.input.ndr.active())
    kept, dropped_one = [], False
    for ns in dr.net_segments:
        if not dropped_one and ns.bundle_id == clk_bid and not ns.is_shield:
            dropped_one = True
            continue                      # simulate a culled signal bit
        kept.append(ns)
    dr.net_segments = kept
    out = _audited(s)
    assert "NDR_SHIELD" not in out


def test_r9_end_guard_foreign_wire_is_loud():
    # Codex #622: an UNSHIELDED rule's layout reserves guard slots BEYOND
    # the outermost wires too; the audited run must cover them, so a
    # foreign wire on an end guard is a clearance violation even though it
    # sits outside the placed rows' extent.
    s, _ = _flow(["def_ndr sp spacing x2",
                  "set_ndr clk_ sp"])
    dr = s.detailed_result
    clk_bid = next(w.input.original_bundle.id for w in s.bundles
                   if w.input.ndr.active())
    by_seg = {}
    for ns in dr.net_segments:
        if ns.bundle_id == clk_bid:
            by_seg.setdefault(ns.seg_idx, []).append(ns)
    rows = next(v for v in by_seg.values() if len(v) >= 2)
    layer = rows[0].layer
    run_hi = max(r.track_position + r.width / 2.0 for r in rows)
    s_lo = min(min(r.span_lo, r.span_hi) for r in rows)
    s_hi = max(max(r.span_lo, r.span_hi) for r in rows)
    g = s.routing_grid.get_layer_grid(layer)
    period = g.global_pattern().unit_pitch()
    above = g.signal_tracks_in(0.5 * (s_lo + s_hi),
                               run_hi + 1e-9, run_hi + 2 * period)
    guard_pos = above[0][0]               # the end guard slot's centre
    donor = next(ns for ns in dr.net_segments
                 if ns.bundle_id != clk_bid and not ns.is_shield)
    intruder = _ns_copy(donor, track_position=guard_pos, layer=layer,
                        span_lo=rows[0].span_lo, span_hi=rows[0].span_hi)
    dr.net_segments = list(dr.net_segments) + [intruder]
    out = _audited(s)
    assert "foreign wire inside a rule's reserved run" in out


def test_r9_clock_preroute_inside_run_is_loud():
    # Codex #622: fixed pre-route metal never appears in net_segments, so
    # the audit inspects the pattern's rails too.  A CLOCK rail inside the
    # reserved run is an aggressor — LOUD; the VDD/GND rails the same runs
    # straddle stay exempt (the documented straddle-neutral phase-1 model,
    # already pinned by test_r9_clean_shielded_flow_has_no_ndr_violations).
    clk_pat = "0 VDD 2 1 _ 1 1 _ 1 1 CLK 1 1 _ 1 1 _ 1 1 GND 2 1"
    s = _bare_session()
    run = ["run_bundler STRICT", "generate_topologies", "set_track_pitch 3",
           "run_planner 1"] + \
          [f"def_track_pattern {lid} {clk_pat}" for lid in (3, 4, 5, 6)] + \
          ["run_nuts", "run_detailed_nuts"]
    for c in (_FLOW_SETUP
              + ["def_ndr clkx shield bus net GND",
                 "set_ndr clk_ clkx"] + run):
        _run(s, c)
    out = _audited(s)
    assert "CLOCK pre-route" in out
    assert "VDD" not in out and "GND pre-route" not in out


# ── R5a end-shield crediting (phase 2, opt-in `credit`) ────────────────────

def test_credited_pair_is_lockstep():
    # The credited variants must stay lockstep for every credit combination
    # (the R4 no-drift property extended to R5a), and reduce exactly to the
    # base pair for unshielded or uncredited shapes.
    shapes = [_spec(mode=1), _spec(width=2, guard=1, mode=1),
              _spec(width=2, mode=2), _spec(guard=1, mode=3, per_n=2)]
    for s in shapes:
        s.credit_shields = True
        for nbits in (1, 2, 4, 8):
            base = buda.ndr_group_demand(s, nbits)
            for clo in (False, True):
                for chi in (False, True):
                    du = buda.ndr_group_demand_credited(s, nbits, clo, chi)
                    lay = buda.ndr_run_layout_credited(s, nbits, clo, chi)
                    assert len(lay) == du
                    assert lay.count("B") == nbits
                    assert du == base - clo - chi
    g = _spec(guard=2)                    # unshielded: nothing to credit
    assert (buda.ndr_group_demand_credited(g, 4, True, True)
            == buda.ndr_group_demand(g, 4))
    assert (buda.ndr_run_layout_credited(g, 4, True, True)
            == buda.ndr_run_layout(g, 4))


def test_rail_credits_predicate():
    s = _spec(mode=1)                     # shield net defaults to GND
    s.credit_shields = True
    assert buda.ndr_rail_credits(s, "GND", "GROUND")
    assert buda.ndr_rail_credits(s, "VSS", "GROUND")   # same supply family
    assert buda.ndr_rail_credits(s, "", "GROUND")      # falls to slot type
    assert not buda.ndr_rail_credits(s, "VDD", "POWER")  # POWER never GND
    off = _spec(mode=1)                   # no credit opt-in -> never
    assert not buda.ndr_rail_credits(off, "GND", "GROUND")
    noshield = _spec(guard=1)             # nothing to credit against
    noshield.credit_shields = True
    assert not buda.ndr_rail_credits(noshield, "GND", "GROUND")


def test_def_ndr_credit_token():
    s = _bare_session()
    out = _run(s, "def_ndr c shield bus net VSS credit")
    assert "credit" in out
    assert " credit," in _run(s, "dump_ndr")
    with pytest.raises(SystemExit):       # credit needs a shield arrangement
        _run(s, "def_ndr c2 width x2 credit")


def _rail_pattern(*rail_defs, nsig=4):
    """Pattern of one or two rails around nsig SIGNAL slots: [rail0, sigs]
    or [rail0, sigs, rail1].  Rail width 2, signal width 1, spacing 1."""
    slots = [buda.TrackSlot(type=rail_defs[0][0], label=rail_defs[0][1],
                            width=2.0, space_after=1.0)]
    slots += [buda.TrackSlot(type="SIGNAL", label="sig",
                             width=1.0, space_after=1.0)] * nsig
    for t, l in rail_defs[1:]:
        slots.append(buda.TrackSlot(type=t, label=l,
                                    width=2.0, space_after=1.0))
    return buda.TrackPattern(origin=0.0, slots=slots)


def _engine_run(pattern, spec, bit_width=4, interval_hi=28.0):
    stack = buda.RoutingGridStack()
    stack.define_layer(4, pattern, True)
    seg = buda.BusSegment()
    seg.bundle_id, seg.seg_idx, seg.layer = 1, 0, 4
    seg.span_lo, seg.span_hi = 0.0, 100.0
    seg.interval_lo, seg.interval_hi = 0.0, interval_hi
    seg.bit_width = bit_width
    seg.ndr = spec
    return buda.DetailedNUTSEngine(stack).run([seg])


def test_engine_credits_both_rail_flanked_ends():
    # GND rail + 4 sigs per period: the first feasible seat starts rail-
    # adjacent and ends rail-adjacent (the next period's rail), so BOTH end
    # shields credit — 4 bits, ZERO emitted shields, demand 6 -> 4.
    spec = _spec(mode=1)
    spec.credit_shields = True
    spec.rule_name = "c"
    res = _engine_run(_rail_pattern(("GROUND", "GND")), spec,
                      interval_hi=22.0)
    bits = [r for r in res.net_segments if not r.is_shield]
    shields = [r for r in res.net_segments if r.is_shield]
    assert res.num_unplaced == 0
    assert len(bits) == 4 and len(shields) == 0
    assert sorted(round(b.track_position, 1) for b in bits) == [
        3.5, 5.5, 7.5, 9.5]


def test_engine_wrong_family_rail_never_credits():
    # Same geometry but POWER rails against a GND shield spec: no credit,
    # the full uncredited layout places (shields emitted, run straddles
    # the rail as phase 1 allows).
    spec = _spec(mode=1)
    spec.credit_shields = True
    spec.rule_name = "c"
    res = _engine_run(_rail_pattern(("POWER", "VDD")), spec,
                      interval_hi=22.0)
    shields = [r for r in res.net_segments if r.is_shield]
    assert res.num_unplaced == 0
    assert sorted(round(x.track_position, 1) for x in shields) == [3.5, 16.5]


def test_engine_credits_one_end_only():
    # GND below the signal group, VDD above it: only the low end credits —
    # one emitted shield, at the high end of the (rail-straddling) run.
    spec = _spec(mode=1)
    spec.credit_shields = True
    spec.rule_name = "c"
    res = _engine_run(_rail_pattern(("GROUND", "GND"), ("POWER", "VDD")),
                      spec, interval_hi=28.0)
    bits = [r for r in res.net_segments if not r.is_shield]
    shields = [r for r in res.net_segments if r.is_shield]
    assert res.num_unplaced == 0
    assert len(bits) == 4 and len(shields) == 1
    assert round(shields[0].track_position, 1) == 17.5
    assert sorted(round(b.track_position, 1) for b in bits) == [
        3.5, 5.5, 7.5, 9.5]


def test_r5a_e2e_credit_flow_and_audit_agree():
    # Whole-pipeline credit flow on the standard pattern (VDD and GND
    # rails): wherever the seats land, the R9 audit derives the SAME
    # credit decision from the placed geometry (the shared predicate), so
    # the report is clean — the credit/audit-agreement property R5a pins.
    s, out = _flow(["def_ndr clkc width x2 spacing x2 shield bus net GND "
                    "credit",
                    "set_ndr clk_ clkc"])
    assert s.detailed_result.num_unplaced == 0
    chk = _audited(s)
    assert "NDR_" not in chk and "no violations" in chk


def test_v22_fingerprint_credit_suffix():
    # The credit flag is pricing basis (demand changes), so it joins the
    # fingerprint — but ONLY when set, so v21 stamps of non-credit rules
    # still compare equal (a resumed v21 checkpoint must not VOID on a
    # fingerprint-format change).
    s = _bare_session()
    _run(s, "def_ndr a width x2")
    _run(s, "def_ndr b shield bus net GND credit")
    assert ndr_cmds.ndr_pricing_fp(s, "a") == "a|w2|g0|s0|p0|nGND|L"
    assert ndr_cmds.ndr_pricing_fp(s, "b").endswith("|c1")


def test_v22_credit_persists_and_restores(tmp_path):
    s1 = _bare_session()
    _run(s1, f"open_bdb {tmp_path}/c.bdb")
    _run(s1, "def_ndr clkc shield bus net GND credit")
    _run(s1, "set_ndr clk_ clkc")
    del s1
    s2 = _bare_session()
    out = _run(s2, f"open_bdb {tmp_path}/c.bdb")
    assert "restored 1 rule(s)" in out
    assert s2._ndr_rules["clkc"]["credit"] == 1
    assert " credit," in _run(s2, "dump_ndr")


def test_v22_pre_v22_db_migrates(tmp_path):
    # Construct a GENUINE v21 database (drop the credit column, stamp
    # user_version=21) and reopen: the migration adds the column with the
    # 0 default (pre-v22 rules never credited) and the table stays usable.
    import sqlite3
    path = str(tmp_path / "v21.bdb")
    db = buda.BDB(path)
    r = buda.NdrRuleRow()
    r.name, r.width_x = "old", 2.0
    db.set_ndr_rule(r)
    del db
    con = sqlite3.connect(path)
    con.executescript("ALTER TABLE ndr_rule DROP COLUMN credit;"
                      "PRAGMA user_version = 21;")
    con.close()
    db = buda.BDB(path)
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    rows = {x.name: x for x in db.ndr_rules()}
    assert rows["old"].credit == 0
    r2 = buda.NdrRuleRow()
    r2.name, r2.credit = "new", 1
    db.set_ndr_rule(r2)
    assert {x.name: x.credit for x in db.ndr_rules()} == {"old": 0, "new": 1}


def test_credited_helpers_enforce_the_opt_in():
    # Codex #624: the credited pair must be the identity for a spec that
    # never opted into crediting, under ANY flag combination — API callers
    # cannot bypass the rule's declaration.
    s = _spec(mode=1)                     # shielded but credit NOT opted in
    for nbits in (1, 4):
        assert (buda.ndr_group_demand_credited(s, nbits, True, True)
                == buda.ndr_group_demand(s, nbits))
        assert (buda.ndr_run_layout_credited(s, nbits, True, True)
                == buda.ndr_run_layout(s, nbits))


def test_end_credit_allows_gap_only_for_culled_runs():
    # Codex #624: a culled outer bit leaves an empty SIGNAL slot between
    # the surviving outer bit and the credited rail.  The strict adjacency
    # test reads that as an intervening gap (spurious NDR_SHIELD); the
    # audit passes allow_gap for under-populated segments so the credit
    # survives the cull.  Identity and span coverage still apply.
    spec = _spec(mode=1)
    spec.credit_shields = True
    stack = buda.RoutingGridStack()
    stack.define_layer(4, _rail_pattern(("GROUND", "GND")), True)
    # Simulate a credited run whose lowest bit (track 3.5) was culled:
    # surviving bits at 5.5 / 7.5 / 9.5, no emitted shields.
    rows = []
    for i, t in enumerate((5.5, 7.5, 9.5)):
        ns = buda.NetSegment()
        ns.bundle_id, ns.seg_idx, ns.bit_index = 1, 0, i + 1
        ns.track_position, ns.width, ns.layer = t, 1.0, 4
        ns.span_lo, ns.span_hi = 0.0, 100.0
        rows.append(ns)
    strict = ndr_cmds._ndr_end_credit(spec, stack, 4, rows, 0.0, 100.0,
                                      True)
    relaxed = ndr_cmds._ndr_end_credit(spec, stack, 4, rows, 0.0, 100.0,
                                       True, allow_gap=True)
    assert not strict and relaxed
    # A non-matching rail still refuses even with the gap allowed.
    vdd = buda.RoutingGridStack()
    vdd.define_layer(4, _rail_pattern(("POWER", "VDD")), True)
    assert not ndr_cmds._ndr_end_credit(spec, vdd, 4, rows, 0.0, 100.0,
                                        True, allow_gap=True)


# ── R6 shield BONDING (the `bond` token) ───────────────────────────────────
# A phase-1 shield is a wire on a reserved track carrying the rule's net
# NAME with nothing tying it to that net.  Bonding straps each EMITTED
# shield to the power grid wherever an identity-matching rail crosses it on
# an adjacent PERPENDICULAR layer.  Output-only: it moves no demand.

def _bond_stack(rail_type="GROUND", rail_label="GND"):
    """Layer 3 horizontal (dense signal, where the shielded bus lands) with
    layer 4 vertical carrying one rail per period — the bonding target."""
    st = buda.RoutingGridStack()
    st.define_layer(3, buda.TrackPattern(
        origin=0.0, slots=[buda.TrackSlot(type="SIGNAL", label="sig",
                                          width=1.0, space_after=1.0)]), True)
    st.define_layer(4, buda.TrackPattern(
        origin=0.0,
        slots=[buda.TrackSlot(type=rail_type, label=rail_label,
                              width=2.0, space_after=1.0)]
              + [buda.TrackSlot(type="SIGNAL", label="sig",
                                width=1.0, space_after=1.0)] * 3), False)
    return st


def _bond_run(stack, spec, span_hi=40.0, bit_width=4):
    seg = buda.BusSegment()
    seg.bundle_id, seg.seg_idx, seg.layer = 1, 0, 3
    seg.span_lo, seg.span_hi = 0.0, span_hi
    seg.interval_lo, seg.interval_hi = 0.0, span_hi
    seg.bit_width = bit_width
    seg.ndr = spec
    return buda.DetailedNUTSEngine(stack).run([seg])


def _bond_spec(net="GND", mode=1):
    s = _spec(mode=mode)
    s.rule_name, s.shield_net, s.bond_stride = "r", net, 1
    return s


def test_bond_straps_every_matching_crossing():
    r = _bond_run(_bond_stack(), _bond_spec())
    shields = [n for n in r.net_segments if n.is_shield]
    assert len(shields) == 2                      # flank-the-bus
    straps = [v for v in r.net_vias if v.to_seg < 0]
    assert r.n_shield_bond_vias == len(straps) > 0
    # Every strap lands on the adjacent V layer, keyed to its own shield.
    per_shield = {}
    for v in straps:
        assert v.from_layer == 3 and v.to_layer == 4
        per_shield.setdefault(v.bit_index, []).append(v)
    assert set(per_shield) == {s.bit_index for s in shields}
    for sh in shields:                            # strapped at its own track
        assert all(v.y == sh.track_position for v in per_shield[sh.bit_index])
    # Strap ordinals are unique per shield, so the net_via primary key
    # (bundle, from_seg, to_seg, bit_index) cannot collide.
    keys = {(v.bundle_id, v.from_seg, v.to_seg, v.bit_index) for v in straps}
    assert len(keys) == len(straps)


def test_bond_is_opt_in():
    spec = _bond_spec()
    spec.bond_stride = 0
    r = _bond_run(_bond_stack(), spec)
    assert r.n_shield_bond_vias == 0
    assert not [v for v in r.net_vias if v.to_seg < 0]


def test_bond_respects_supply_family_and_refuses_the_other():
    # VSS shields bond to GND rails (one ground net); a POWER rail never
    # bonds a ground shield — THE shared identity predicate.
    vss = _bond_run(_bond_stack(), _bond_spec(net="VSS"))
    assert vss.n_shield_bond_vias > 0
    vdd_rails = _bond_run(_bond_stack("POWER", "VDD"), _bond_spec(net="GND"))
    assert vdd_rails.n_shield_bond_vias == 0


def test_bond_needs_a_perpendicular_adjacent_layer():
    # Adjacency alone is not enough: two PARALLEL layers never cross, so
    # there is nothing to via.  No crash, no straps.
    st = _bond_stack()
    st2 = buda.RoutingGridStack()
    st2.define_layer(3, st.get_layer_grid(3).global_pattern(), True)
    st2.define_layer(4, st.get_layer_grid(4).global_pattern(), True)  # H too
    assert _bond_run(st2, _bond_spec()).n_shield_bond_vias == 0
    # And a layer with no neighbour in the stack at all.
    st3 = buda.RoutingGridStack()
    st3.define_layer(3, st.get_layer_grid(3).global_pattern(), True)
    assert _bond_run(st3, _bond_spec()).n_shield_bond_vias == 0


def test_bond_moves_no_demand():
    # Bonding is OUTPUT-only: same placement, same shields, same bit
    # tracks — only the extra straps differ.
    plain, bonded = _bond_spec(), _bond_spec()
    plain.bond_stride = 0
    a, b = _bond_run(_bond_stack(), plain), _bond_run(_bond_stack(), bonded)
    assert ([(n.bit_index, n.track_position, n.width) for n in a.net_segments]
            == [(n.bit_index, n.track_position, n.width)
                for n in b.net_segments])
    assert a.num_unplaced == b.num_unplaced


def test_def_ndr_bond_token():
    s = _bare_session()
    out = _run(s, "def_ndr b shield bus net GND bond")
    assert " bond," in out
    assert " bond," in _run(s, "dump_ndr")
    with pytest.raises(SystemExit):       # bond needs a shield arrangement
        _run(s, "def_ndr b2 width x2 bond")


def test_bond_is_not_in_the_pricing_fingerprint():
    # Bonding emits extra vias and moves neither demand nor placement, so
    # toggling it must NOT void a restored plan.
    s = _bare_session()
    _run(s, "def_ndr p shield bus net GND")
    _run(s, "def_ndr q shield bus net GND bond")
    a, b = ndr_cmds.ndr_pricing_fp(s, "p"), ndr_cmds.ndr_pricing_fp(s, "q")
    assert a.split("|", 1)[1] == b.split("|", 1)[1]


def test_v25_bond_persists_and_restores(tmp_path):
    s1 = _bare_session()
    _run(s1, f"open_bdb {tmp_path}/b.bdb")
    _run(s1, "def_ndr shb shield bit net VSS bond")
    _run(s1, "set_ndr sh_ shb")
    del s1
    s2 = _bare_session()
    assert "restored 1 rule(s)" in _run(s2, f"open_bdb {tmp_path}/b.bdb")
    assert s2._ndr_rules["shb"]["bond"] == 1
    assert " bond," in _run(s2, "dump_ndr")


def test_v25_pre_v25_db_migrates(tmp_path):
    # A GENUINE v24 database (no bond column, user_version=24) reopens with
    # the column added at its 0 default — pre-v25 rules never bonded.
    import sqlite3
    path = str(tmp_path / "v24.bdb")
    db = buda.BDB(path)
    r = buda.NdrRuleRow()
    r.name, r.width_x, r.credit = "old", 2.0, 1
    db.set_ndr_rule(r)
    del db
    con = sqlite3.connect(path)
    con.executescript("ALTER TABLE ndr_rule DROP COLUMN bond;"
                      "PRAGMA user_version = 24;")
    con.close()
    db = buda.BDB(path)
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    rows = {x.name: x for x in db.ndr_rules()}
    assert rows["old"].credit == 1 and rows["old"].bond == 0
    r2 = buda.NdrRuleRow()
    r2.name, r2.bond = "new", 1
    db.set_ndr_rule(r2)
    assert {x.name: x.bond for x in db.ndr_rules()} == {"old": 0, "new": 1}


def test_r9_unbondable_shield_is_loud():
    # An emitted shield with zero straps is floating metal — the failure
    # the opt-in exists to rule out.  Here the adjacent layer's rails are
    # POWER, so no ground shield can bond to them.
    s = _bare_session()
    src = ["add_block A 0 0 100 100", "add_block B 400 0 500 100",
           "add_bus n_[4] A.p B.q", "def_layer 3 M3 H TOP 20",
           "def_layer 4 M4 V 20",
           "def_ndr r shield bus net GND bond", "set_ndr n_ r",
           "run_bundler STRICT", "generate_topologies", "run_planner 1",
           "def_track_pattern 3 0 _ 1 1",
           "def_track_pattern 4 0 VDD 2 1 _ 1 1 _ 1 1 _ 1 1",
           "run_nuts", "run_detailed_nuts"]
    for line in src:
        _run(s, line)
    assert s.detailed_result.n_shield_bond_vias == 0
    out = _audited(s)
    assert "NDR_BOND" in out and "floating metal" in out
    # …and the audit is quiet once the rails DO match the shield's net.
    ok = _bare_session()
    for line in src:
        _run(ok, line.replace("VDD 2 1", "GND 2 1"))
    assert ok.detailed_result.n_shield_bond_vias > 0
    assert "NDR_BOND" not in _audited(ok)


# ── NDR_SHIELD arrangement, all modes (opens_ndr.md smaller residual) ──────
# The count check alone is blind to a right-COUNT shield in the wrong gap,
# which is the whole failure mode under `bit` / `per:N`.  The audit now
# walks the credited layout role-by-role against the ascending placed rows.

def _swap_two_rows(session, bid, i, j):
    """Exchange the track positions of the i-th and j-th rows (ascending)
    of a bundle's placed run — a same-count, wrong-arrangement mutation."""
    dr = session.detailed_result
    rows = sorted((ns for ns in dr.net_segments if ns.bundle_id == bid),
                  key=lambda r: r.track_position)
    a, b = rows[i], rows[j]
    moved = {id(a): b.track_position, id(b): a.track_position}
    out = []
    for ns in dr.net_segments:
        out.append(_ns_copy(ns, track_position=moved[id(ns)])
                   if id(ns) in moved else ns)
    dr.net_segments = out


def _governed_bid(s):
    return next(w.input.original_bundle.id for w in s.bundles
                if w.input.ndr.active())


def test_r9_shield_in_the_wrong_gap_is_loud_under_shield_bit():
    # `shield bit` lays out S B S B S B S B S.  Swapping the first shield
    # with the first bit keeps 5 shields and 4 bits — the count check sees
    # nothing — but the run now opens on a signal wire.
    s, _ = _flow(["def_ndr clkb width x2 shield bit net GND",
                  "set_ndr clk_ clkb"])
    bid = _governed_bid(s)
    assert "NDR_SHIELD" not in _audited(s)      # clean before the mutation
    _swap_two_rows(s, bid, 0, 1)
    out = _audited(s)
    assert "NDR_SHIELD" in out
    assert "not in the gaps the rule declares" in out


def test_r9_shield_in_the_wrong_gap_is_loud_under_shield_per_n():
    # `per:2` puts a shield after every 2nd bit, plus both ends: S B B S B B S.
    # Swapping the interior shield with its neighbouring bit is invisible to
    # every total the old audit computed.
    s, _ = _flow(["def_ndr clkp width x2 shield per:2 net GND",
                  "set_ndr clk_ clkp"])
    bid = _governed_bid(s)
    assert "NDR_SHIELD" not in _audited(s)
    _swap_two_rows(s, bid, 2, 3)
    assert "NDR_SHIELD" in _audited(s)


def test_r9_arrangement_check_subsumes_flank_the_bus():
    # The old check was outermost-rows-only for `shield bus`; the layout walk
    # must still catch exactly that (S B B S -> B S B S opens on a bit).
    s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus net GND",
                  "set_ndr clk_ clk2x"])
    bid = _governed_bid(s)
    assert "NDR_SHIELD" not in _audited(s)
    _swap_two_rows(s, bid, 0, 1)
    assert "NDR_SHIELD" in _audited(s)


def test_r9_arrangement_names_the_expected_and_actual_runs():
    # The message must be diagnosable: both role strings and where they
    # first differ, not just "wrong".
    s, _ = _flow(["def_ndr clkb width x2 shield bit net GND",
                  "set_ndr clk_ clkb"])
    _swap_two_rows(s, _governed_bid(s), 0, 1)
    s.verbose_conn = True                 # print the literal, not the digest
    out = _run(s, "check_design dnuts")
    assert "expects the run to read SBSBSBSBS" in out, out
    assert "but it reads BSSBSBSBS" in out, out
    assert "first mismatch at position 0" in out, out


def test_r9_arrangement_is_skipped_on_a_culled_run():
    # A keepout cull removes bits, so the surviving rows legitimately fail a
    # positional match against the INTENDED layout.  The cull is already
    # LOUD as UNPLACED; the arrangement walk must not pile a spurious
    # NDR_SHIELD on top of it (the guard that keeps #622's fix intact).
    s, _ = _flow(["def_ndr clkb width x2 shield bit net GND",
                  "set_ndr clk_ clkb"])
    dr = s.detailed_result
    bid = _governed_bid(s)
    kept, dropped = [], False
    for ns in dr.net_segments:
        if not dropped and ns.bundle_id == bid and not ns.is_shield:
            dropped = True
            continue
        kept.append(ns)
    dr.net_segments = kept
    assert "NDR_SHIELD" not in _audited(s)


# ── The doomed-seat census in DEMAND units (opens_ndr.md smaller residual) ──
# The engine admits on bus_seg_min_demand; the census compared against the
# BIT count.  A governed seat with enough tracks for its bits but not for
# its bits-plus-guards-plus-shields therefore reported clean and then
# stranded every bit at DNUTS — the census under-reporting exactly the
# class it exists to name.

def _census(s):
    return s._doomed_seats()


def test_census_need_is_the_engines_admission_threshold():
    # _seg_admission_need must mirror bus_seg_min_demand: identity for an
    # ungoverned segment, group demand for a governed one, optimistic
    # credit when the rule opted in.
    s, _ = _flow(["def_ndr clk2x width x2 spacing x2 shield bus net GND",
                  "set_ndr clk_ clk2x"])
    gov = next(w for w in s.bundles if w.input.ndr.active())
    plain = next(w for w in s.bundles if not w.input.ndr.active())
    for w in (gov, plain):
        sel = w.plan.selected_topology_index
        for seg_idx in range(len(w.input.candidates[sel].segments)):
            bits = s._seg_member_bits(w, sel, seg_idx)
            need = s._seg_admission_need(w, sel, seg_idx)
            spec = w.input.ndr
            if not spec.active():
                assert need == bits            # untouched by construction
            else:
                assert need == buda.ndr_group_demand(spec, bits) > bits


def test_census_counts_governed_seats_in_demand_not_bits():
    # A rule whose demand exceeds its bit count, seated on a layer whose
    # window supplies enough for the bits but not the demand: doomed in
    # truth, invisible when measured in bits.
    s, _ = _flow(["def_ndr wide width x3 spacing x3 shield bit net GND",
                  "set_ndr clk_ wide"])
    gov = next(w for w in s.bundles if w.input.ndr.active())
    bid = gov.input.original_bundle.id
    sel = gov.plan.selected_topology_index
    for seg_idx in range(len(gov.input.candidates[sel].segments)):
        bits = s._seg_member_bits(gov, sel, seg_idx)
        need = s._seg_admission_need(gov, sel, seg_idx)
        assert need > bits, "the vehicle must have demand above its bits"
    # Every seat the census reports for this bundle is judged against the
    # DEMAND, and its stranded-bit accounting stays in bits.
    for seg, need, pool, _is_top, nbits in _census(s):
        if seg.bundle_id != bid:
            continue
        assert need > nbits
        assert pool < need


def test_census_is_unchanged_for_ungoverned_designs():
    # R12 at the census boundary: with no rule declared the need is the bit
    # count, so the tuple a non-NDR flow produces is what it always was.
    s, _ = _flow([])
    for seg, need, pool, _is_top, bits in _census(s):
        assert need == bits


def test_census_report_names_the_unit():
    # A governed seat's requirement is demand slots; printing that number
    # as "member bits" would read as a bit count that does not match the
    # bus the user declared.
    s, _ = _flow(["def_ndr wide width x3 spacing x3 shield bit net GND",
                  "set_ndr clk_ wide"])
    if not _census(s):
        pytest.skip("no doomed seat on this vehicle to report")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s._report_doomed_seats()
    out = buf.getvalue()
    assert "demand slot(s)" in out and "under its NDR rule" in out


def test_census_selects_the_pool_on_full_demand_not_the_credited_minimum():
    """Codex P2 on #677: the engine picks the span-clear-vs-midpoint
    fallback against FULL demand (detailed_nuts.cpp:470) and admits
    against the credited MINIMUM (:497).  Passing the optimistic value to
    both would keep a span-clear pool the engine would have abandoned for
    a richer midpoint one, and could then call a credited run doomed that
    DNUTS in fact places."""
    s, _ = _flow(["def_ndr crd width x2 shield bus net GND credit",
                  "set_ndr clk_ crd"])
    gov = next(w for w in s.bundles if w.input.ndr.active())
    sel = gov.plan.selected_topology_index
    lo = s._seg_admission_need(gov, sel, 0)
    hi = s._seg_admission_need(gov, sel, 0, credited=False)
    assert lo < hi, "a credit rule's minimum must sit below full demand"

    # The census must hand the POOL the full value and compare with the
    # minimum — recorded rather than re-derived, so the test exercises the
    # real call rather than restating the arithmetic.
    bid = gov.input.original_bundle.id
    seen = []
    real = s._seg_admission_pool

    def spy(seg, g, need):
        seen.append((seg.bundle_id, need))     # keyed by BUNDLE: an
        return real(seg, g, need)              # ungoverned 8-bit bus can
    s._seg_admission_pool = spy                # collide with lo numerically
    try:
        s._doomed_seats()
    finally:
        s._seg_admission_pool = real
    gov_needs = [n for b, n in seen if b == bid]
    assert gov_needs and all(n == hi for n in gov_needs), \
        f"pool selection must use full demand {hi}, saw {set(gov_needs)}"


def test_census_uncredited_rule_uses_one_value_for_both():
    # Without `credit` the two coincide, so the split is inert — the
    # guarantee that this fix cannot perturb a non-crediting governed flow.
    s, _ = _flow(["def_ndr plain width x2 shield bus net GND",
                  "set_ndr clk_ plain"])
    gov = next(w for w in s.bundles if w.input.ndr.active())
    sel = gov.plan.selected_topology_index
    assert (s._seg_admission_need(gov, sel, 0)
            == s._seg_admission_need(gov, sel, 0, credited=False))

# ── R6 bond STRIDE (opens_ndr.md §1 residual: every crossing was strapped) ──

def _straps_at(stride, span_hi=100.0):
    spec = _bond_spec()
    spec.bond_stride = stride
    r = _bond_run(_bond_stack(), spec, span_hi=span_hi)
    by_shield = {}
    for v in r.net_vias:
        if v.to_seg < 0:
            by_shield.setdefault(v.bit_index, []).append(v.x)
    return {k: sorted(v) for k, v in by_shield.items()}


def test_bond_stride_thins_the_straps():
    every = _straps_at(1)
    third = _straps_at(3)
    assert every and third
    for sh, xs in every.items():
        assert len(third[sh]) < len(xs), "stride 3 must strap fewer"
        assert set(third[sh]) <= set(xs), "strided straps are a SUBSET"


def test_bond_stride_anchors_both_extremes():
    # An unbonded tail past the last strap is the floating metal bonding
    # exists to prevent, so the first and last crossing are always kept
    # whatever the stride divides out to.
    every = _straps_at(1)
    for stride in (2, 3, 4, 7):
        got = _straps_at(stride)
        for sh, xs in every.items():
            assert got[sh][0] == xs[0], f"stride {stride} lost the low end"
            assert got[sh][-1] == xs[-1], f"stride {stride} lost the high end"


def test_bond_stride_1_is_every_crossing():
    # The bare `bond` token is stride 1 — byte-identical to pre-stride
    # behaviour, so turning the knob on cannot change an existing flow.
    spec_default = _bond_spec()
    assert spec_default.bond_stride == 1
    r_off = _bond_run(_bond_stack(), _spec(mode=1))   # never opted in
    assert r_off.n_shield_bond_vias == 0


def test_bond_stride_larger_than_the_run_keeps_both_ends():
    # A stride past the crossing count degenerates to "first and last",
    # never to zero straps.
    got = _straps_at(1000)
    every = _straps_at(1)
    for sh, xs in got.items():
        assert xs == sorted({every[sh][0], every[sh][-1]})


def test_def_ndr_bond_stride_token():
    s = _bare_session()
    out = _run(s, "def_ndr b shield bus net GND bond stride 4")
    assert "bond stride 4" in out
    assert "bond stride 4" in _run(s, "dump_ndr")
    assert s._ndr_rules["b"]["bond"] == 4
    # The bare token stays stride 1 and prints without a stride.
    s2 = _bare_session()
    out2 = _run(s2, "def_ndr b shield bus net GND bond")
    assert " bond," in out2 and "stride" not in out2
    for bad in ("def_ndr c shield bus bond stride 0",
                "def_ndr c shield bus bond stride -2",
                "def_ndr c shield bus bond stride wat"):
        with pytest.raises(SystemExit):
            _run(_bare_session(), bad)


def test_bond_stride_persists_and_restores(tmp_path):
    # The stride rides the EXISTING v25 `bond` column (0 = off, N = stride),
    # so a stride needs no schema bump and a stored 1 still means what it
    # meant before.
    s1 = _bare_session()
    _run(s1, f"open_bdb {tmp_path}/s.bdb")
    _run(s1, "def_ndr shb shield bit net VSS bond stride 6")
    _run(s1, "set_ndr sh_ shb")
    del s1
    s2 = _bare_session()
    _run(s2, f"open_bdb {tmp_path}/s.bdb")
    assert s2._ndr_rules["shb"]["bond"] == 6
    assert "bond stride 6" in _run(s2, "dump_ndr")


def test_bond_stride_is_not_in_the_pricing_fingerprint():
    # Like `bond` itself: straps are output, so changing the stride must
    # not VOID a restored plan.
    s = _bare_session()
    _run(s, "def_ndr p shield bus net GND bond")
    _run(s, "def_ndr q shield bus net GND bond stride 8")
    a, b = ndr_cmds.ndr_pricing_fp(s, "p"), ndr_cmds.ndr_pricing_fp(s, "q")
    assert a.split("|", 1)[1] == b.split("|", 1)[1]


def test_bond_stride_anchors_extremes_under_an_override():
    """Codex on #674: `preroutes_in` emits the global pattern's rails first
    and appends each override region's afterwards, so the query order is
    NOT spatial when the adjacent layer carries an override.  Striding by
    that order would thin an arbitrary interleaving and anchor the last
    APPENDED rail instead of the far extreme — a genuinely unbonded tail
    NDR_BOND cannot see, because the shield still has straps."""
    def straps(stride):
        st = _bond_stack()
        # A GND rail inside a mid-span region, on the adjacent V layer.
        ov = buda.TrackPattern(
            origin=0.0,
            slots=[buda.TrackSlot(type="GROUND", label="GND",
                                  width=2.0, space_after=1.0)]
                  + [buda.TrackSlot(type="SIGNAL", label="sig",
                                    width=1.0, space_after=1.0)] * 3)
        st.add_override(4, 10, -50, 30, 50, ov)
        spec = _bond_spec()
        spec.bond_stride = stride
        r = _bond_run(st, spec, span_hi=100.0)
        out = {}
        for v in r.net_vias:
            if v.to_seg < 0:
                out.setdefault(v.bit_index, []).append(v.x)
        return {k: sorted(v) for k, v in out.items()}

    every = straps(1)
    assert every, "the override vehicle must still bond"
    for stride in (2, 3, 1000):
        got = straps(stride)
        for sh, xs in every.items():
            assert got[sh][0] == xs[0], \
                f"stride {stride} lost the true low extreme under override"
            assert got[sh][-1] == xs[-1], \
                f"stride {stride} lost the true high extreme under override"
            assert got[sh] == sorted(got[sh])


# ── R1 absolute values: the review's contained findings (Codex on #682) ────

def _abs_session(patterns=("3",)):
    s = _bare_session()
    for line in ("add_block a 0 0 100 100", "def_layer 3 M3 H 20",
                 "def_layer 4 M4 V 20"):
        _run(s, line)
    for lid in patterns:
        _run(s, f"def_track_pattern {lid} 0 VDD 2 1 (_ 1 1)x12 GND 2 1")
    return s


def test_absolute_needs_every_governed_layer_patterned():
    # The stored quantization is a MAXIMUM, and a max over a SUBSET is not
    # conservative: a pattern declared later on an omitted layer can need
    # more slots, and routing there would under-charge the declared width.
    s = _abs_session(patterns=("3",))          # L4 declared but unpatterned
    with pytest.raises(SystemExit):
        _run(s, "def_ndr r width 3")
    # Restricting the rule to the patterned layer is one of the ways out
    # the error names, and it must work.
    s2 = _abs_session(patterns=("3",))
    out = _run(s2, "def_ndr ok width 3 layers M3")
    assert "ABSOLUTE" in out and "L3:" in out
    # Patterning both layers is the other.
    s3 = _abs_session(patterns=("3", "4"))
    assert "ABSOLUTE" in _run(s3, "def_ndr both width 3")


def test_absolute_accepts_the_um_suffix():
    # An NDR distance spells a distance the way every other script-declared
    # distance does — the shared require_distance parser, not a local
    # float().  At the default scale 1 um is 1 layout unit.
    s = _abs_session()
    _run(s, "def_ndr um5 width 5um spacing 6um layers M3")
    r = s._ndr_rules["um5"]
    assert (r["width_abs"], r["spacing_abs"]) == (5.0, 6.0)
    # And a bare value still means layout units.
    _run(s, "def_ndr bare5 width 5 layers M3")
    assert s._ndr_rules["bare5"]["width_abs"] == 5.0


def test_absolute_rejects_nonsense_values():
    s = _abs_session()
    for bad in ("def_ndr z width 0 layers M3",
                "def_ndr z width -2 layers M3",
                "def_ndr z width wat layers M3"):
        with pytest.raises(SystemExit):
            _run(_abs_session(), bad)


def test_absolute_resolution_rounds_up_with_an_exact_boundary():
    # The rounding decision, at the level it is actually made.  Pitch 2.5:
    # 3 -> 2 slots (rounds up), 5 -> exactly 2 (must NOT pay 3).
    spec = buda.NdrSpec()
    spec.width_abs = 3.0
    assert buda.ndr_resolve_for_pitch(spec, 2.5).width_slots == 2
    spec.width_abs = 5.0
    assert buda.ndr_resolve_for_pitch(spec, 2.5).width_slots == 2
    spec.width_abs = 5.1
    assert buda.ndr_resolve_for_pitch(spec, 2.5).width_slots == 3
    # Identity for a multiplier-only spec and for a pitch <= 0.
    m = _spec(width=2)
    assert buda.ndr_resolve_for_pitch(m, 2.5).width_slots == 2
    spec.width_abs = 3.0
    assert buda.ndr_resolve_for_pitch(spec, 0.0).width_slots == 1


def test_v26_absolute_persists_and_restores(tmp_path):
    """Codex P1 on #682: an absolute declaration leaves the MULTIPLIER at
    1.0, so without persisting the absolute fields a reopened design
    restores the rule as DEFAULT width — usually inactive, silently
    dropping the constraint the design was routed under."""
    s1 = _bare_session()
    _run(s1, f"open_bdb {tmp_path}/a.bdb")
    for line in ("def_layer 3 M3 H 20",
                 "def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1"):
        _run(s1, line)
    _run(s1, "def_ndr abs3 width 3 spacing 5 layers M3")
    _run(s1, "set_ndr sig_ abs3")
    del s1

    s2 = _bare_session()
    for line in ("def_layer 3 M3 H 20",
                 "def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1"):
        _run(s2, line)
    out = _run(s2, f"open_bdb {tmp_path}/a.bdb")
    assert "restored 1 rule(s)" in out
    r = s2._ndr_rules["abs3"]
    assert (r["width_abs"], r["spacing_abs"]) == (3.0, 5.0)
    # The QUANTIZATION is re-derived against this session's grid, not read
    # back — the same rule against a different stack is a different slot
    # count, and a stored one would be charged against geometry it never
    # measured.
    assert r["width_slots_max"] == 2 and r["guard_slots_max"] == 1
    spec = ndr_cmds._spec_of(s2, "abs3")
    assert spec.active() and spec.width_slots == 2


def _persisted_abs_rule(tmp_path, decl="def_ndr abs3 width 3 layers M3",
                        keep_grid=True):
    """A BDB carrying one absolute rule, ready to reopen.

    `keep_grid=False` drops the v29 grid rows, leaving the rule with no
    pattern to quantize against — which is what a checkpoint written BEFORE
    v29 looks like, and the only way to reach that state now that a track
    pattern is persisted with the design that declared it."""
    s1 = _bare_session()
    _run(s1, f"open_bdb {tmp_path}/b.bdb")
    for line in ("def_layer 3 M3 H 20",
                 "def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1"):
        _run(s1, line)
    _run(s1, decl)
    if not keep_grid:
        s1.bdb.clear_track_patterns()
    del s1
    return f"{tmp_path}/b.bdb"


def test_v26_restored_absolute_without_a_grid_says_so(tmp_path):
    # Rules restore at open_bdb, which can precede the script's pattern
    # declarations.  An unquantizable absolute rule must SAY so rather than
    # quietly charging default width.
    path = _persisted_abs_rule(tmp_path)
    s2 = _bare_session()                      # no layers, no patterns
    out = _run(s2, f"open_bdb {path}")
    assert "quantization deferred" in out, out


def test_v26_restored_absolute_quantizes_when_the_grid_arrives(tmp_path):
    """Codex P1 on #682: quantizing ONLY at restore left a rule opened
    before its patterns with no slot count and no way to acquire one —
    `_spec_of` fell back to 1/0, which for a width/spacing-only rule is an
    INACTIVE spec, so the design routed with the persisted constraint
    silently dropped.  Re-declaring is not a workaround: `def_ndr` refuses
    a duplicate name.  The quantization is now derived at first use."""
    path = _persisted_abs_rule(tmp_path)
    s2 = _bare_session()
    _run(s2, f"open_bdb {path}")              # patterns NOT declared yet
    assert "width_slots_max" not in s2._ndr_rules["abs3"]
    for line in ("def_layer 3 M3 H 20",
                 "def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1"):
        _run(s2, line)
    # First use derives it against the grid that will actually be routed on.
    spec = ndr_cmds._spec_of(s2, "abs3")
    assert spec.width_slots == 2, "the restored constraint must survive"
    assert spec.active(), "a 2-slot rule that reads inactive is the bug"
    assert s2._ndr_rules["abs3"]["width_slots_max"] == 2   # memoized


def test_v26_restored_absolute_still_unpatterned_at_use_is_fatal(tmp_path):
    # Deferral is not permission: a rule whose governed layer STILL has no
    # pattern when it is about to govern routing gets the same refusal
    # `def_ndr` would have made, at the first moment it can be made.
    #
    # `keep_grid=False` because v29 persists the pattern with the design that
    # declared it, so reopening this checkpoint now RESTORES one and the rule
    # quantizes correctly — the refusal is right not to fire.  The state this
    # test is about is a checkpoint that carries the rule and no grid, which
    # is what every pre-v29 BDB is.
    path = _persisted_abs_rule(tmp_path, keep_grid=False)
    s2 = _bare_session()
    _run(s2, f"open_bdb {path}")
    _run(s2, "def_layer 3 M3 H 20")           # layer, but no track pattern
    with pytest.raises(SystemExit):
        ndr_cmds._spec_of(s2, "abs3")


def test_v26_absolute_joins_the_pricing_fingerprint():
    # ceil(width_x) is 1.0 for EVERY absolute rule, so pricing on it would
    # stamp them all as default and a changed absolute could never VOID a
    # restored plan.
    s = _abs_session()
    _run(s, "def_ndr a3 width 3 layers M3")
    _run(s, "def_ndr a9 width 9 layers M3")
    _run(s, "def_ndr m2 width x2 layers M3")
    fp3 = ndr_cmds.ndr_pricing_fp(s, "a3")
    fp9 = ndr_cmds.ndr_pricing_fp(s, "a9")
    fpm = ndr_cmds.ndr_pricing_fp(s, "m2")
    assert fp3 != fp9, "different absolute widths must price differently"
    assert "|w2" in fp3 and "|a3" in fp3
    # A multiplier rule's stamp is unchanged in shape (no |a suffix), so
    # pre-R1 checkpoints of multiplier rules still compare equal.
    assert "|a" not in fpm


def test_v26_pre_v26_db_migrates(tmp_path):
    import sqlite3
    path = str(tmp_path / "v25.bdb")
    db = buda.BDB(path)
    r = buda.NdrRuleRow()
    r.name, r.width_x, r.bond = "old", 2.0, 3
    db.set_ndr_rule(r)
    del db
    con = sqlite3.connect(path)
    con.executescript("ALTER TABLE ndr_rule DROP COLUMN width_abs;"
                      "ALTER TABLE ndr_rule DROP COLUMN spacing_abs;"
                      "PRAGMA user_version = 25;")
    con.close()
    db = buda.BDB(path)
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    rows = {x.name: x for x in db.ndr_rules()}
    assert rows["old"].bond == 3
    assert rows["old"].width_abs == 0.0 and rows["old"].spacing_abs == 0.0


def test_r3_absolute_is_checked_per_layer_not_against_the_maximum():
    """Codex P1 on #682: R3 compared every layer against the conservative
    MAXIMUM, so a coarse layer whose per-layer width genuinely fits could
    be hard-errored — a false rejection of a legal design, not a
    conservative charge.

    The stack here is the vehicle's: a COARSE layer (pitch 5) whose
    pattern offers only single isolated SIGNAL slots between wide rails,
    and a FINE layer (pitch 2.5) with a long contiguous run.  `width 4`
    needs 2 slots on the fine layer and 1 on the coarse one — so the
    coarse layer is realizable, and checking it against the maximum of 2
    would refuse it."""
    s = _bare_session()
    for line in ("add_block a 0 0 200 200", "add_block b 800 0 1000 200",
                 "add_bus w_[2] a.p b.q",
                 "def_layer 3 M3 H TOP 20", "def_layer 4 M4 V 20",
                 # fine: 12 contiguous signal slots, pitch 2.5
                 "def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1",
                 # coarse: every signal slot isolated between rails, pitch 5
                 "def_track_pattern 4 0 (VDD 2 2 _ 2 2 GND 2 2 _ 2 2)x3"):
        _run(s, line)
    _run(s, "def_ndr abs4 width 4")
    _run(s, "set_ndr w_ abs4")
    for line in ("run_bundler STRICT", "generate_topologies",
                 "set_track_pitch 3", "run_planner 1"):
        _run(s, line)
    gov = next(w for w in s.bundles if w.input.ndr.active())
    spec = gov.input.ndr
    # The conservative max comes from the FINE layer…
    assert spec.width_slots == 2
    # …while the coarse layer needs only 1, so it must not be refused for
    # lacking a 2-slot contiguous run it never needed.
    coarse = buda.ndr_resolve_for_pitch(spec, s.layers.bit_pitch(4))
    assert coarse.width_slots == 1
    ndr_cmds.validate_ndr_realizability(s)     # must NOT sys.exit


# ── R1 part 2: the ROUTING consumers resolve per layer ──────────────────────
# Part 1 landed the declaration, the persistence and the R3 check; the three
# routing stages still priced an absolute rule at its conservative MAXIMUM.
# Safe in direction (over-charge, never under) but it is capacity a design
# does not owe, and it left the stages disagreeing about a governed group's
# demand — the single-sourcing invariant R4 exists to protect.

def _abs_mixed_pitch_session(extra=()):
    """Two blocks on a stack whose H and V pairs have DIFFERENT per-signal-
    slot pitches (2.5 vs 5.0), with one absolute rule (`width 4`) that
    therefore costs 2 slots/bit horizontally and 1 vertically."""
    s = _bare_session()
    for line in ("add_block a 0 0 200 200", "add_block b 800 600 1000 800",
                 "add_bus w_[4] a.p b.q",
                 "def_layer 3 M3 H TOP 20", "def_layer 4 M4 V TOP 20",
                 "def_track_pattern 3 0 VDD 2 1 (_ 1 1)x12 GND 2 1",
                 "def_track_pattern 4 0 VDD 4 2 (_ 2 2)x12 GND 4 2",
                 "def_ndr abs4 width 4", "set_ndr w_ abs4",
                 "run_bundler STRICT", "generate_topologies",
                 "set_track_pitch 3", "run_planner 1", "run_nuts",
                 *extra):
        _run(s, line)
    return s


def test_absolute_rule_is_resolved_per_layer_at_stage_9():
    s = _abs_mixed_pitch_session()
    gov = next(w for w in s.bundles if w.input.ndr.active())
    assert gov.input.ndr.width_slots == 2          # the conservative max
    assert s.layers.bit_pitch(3) == pytest.approx(2.5)
    assert s.layers.bit_pitch(4) == pytest.approx(5.0)
    fine = [seg for seg in buda.make_bus_segments(
                s.bundles, s.nuts_result, s.fp, "LO_HI", s.layers)
            if seg.bundle_id == gov.input.original_bundle.id]
    assert fine, "the vehicle must route the governed bundle"
    # The V segments sit on a layer whose ONE slot already exceeds the
    # declared width, so the rule resolves to the default there…
    by_layer = {seg.layer: seg.ndr for seg in fine}
    assert 4 in by_layer and 3 in by_layer, "needs both directions routed"
    assert by_layer[3].width_slots == 2
    assert by_layer[4].width_slots == 1
    # …and WITHOUT the layer stack every segment keeps the maximum: the
    # over-charge this change removes (and the documented safe fallback).
    coarse = {seg.layer: seg.ndr for seg in buda.make_bus_segments(
                  s.bundles, s.nuts_result, s.fp, "LO_HI")
              if seg.bundle_id == gov.input.original_bundle.id}
    assert coarse[3].width_slots == 2 and coarse[4].width_slots == 2


def test_absolute_rule_per_layer_charge_matches_the_engine():
    # The invariant the refactor is for: the seat census (Python) and the
    # engine (C++) must measure a governed seat in the SAME units.  On the
    # coarse layer that is the plain bit count; on the fine one it is the
    # 2-slot group demand.
    s = _abs_mixed_pitch_session()
    gov = next(w for w in s.bundles if w.input.ndr.active())
    sel = gov.plan.selected_topology_index
    seen = set()
    for seg in s.nuts_result.segments:
        if seg.bundle_id != gov.input.original_bundle.id:
            continue
        bits = s._seg_member_bits(gov, sel, seg.seg_idx)
        need = s._seg_admission_need(gov, sel, seg.seg_idx, layer=seg.layer)
        expect = buda.ndr_group_demand(
            buda.ndr_resolve_for_pitch(gov.input.ndr,
                                       s.layers.bit_pitch(seg.layer)), bits)
        assert need == expect
        seen.add(seg.layer)
    # Both pitches must actually be exercised, or the assertion above is
    # vacuous (it holds trivially when one layer resolves to the maximum).
    assert {3, 4} <= seen


def test_absolute_rule_audit_does_not_flag_a_coarse_layer_bit():
    # The R9 audit read the UNRESOLVED spec, so a correctly-placed bit on a
    # layer where the rule needs one slot was reported NDR_WIDTH against
    # the maximum of two — a false violation on a clean design.
    s = _abs_mixed_pitch_session(extra=("run_detailed_nuts",))
    gov = next(w for w in s.bundles if w.input.ndr.active())
    viols = ndr_cmds.audit_ndr_dnuts(s, gov)
    assert [v.message for v in viols
            if v.kind.name == "NDR_WIDTH"] == []


# ── a set_ndr SCOPE that governs nothing (opens_ndr §2 residual) ───────────
#
# `set_ndr` takes a net-name PREFIX, so a typo attaches a good rule to
# nothing and the design routes ungoverned while every command reports
# success.  Two verdicts, because two mistakes end in "governs nothing" and
# the remedy differs — and one deliberate exemption, which is the guard that
# matters most here.

def _verdicts(out):
    return [l for l in out.splitlines()
            if l.startswith(("BUDA-1915", "BUDA-1916"))]


def test_a_scope_matching_no_net_is_reported():
    """The headline fault: a prefix naming nothing.  It cannot be
    deliberate, so it is a WARNING, and it names the prefix — the whole
    point is to send someone to the typo."""
    _s, out = _flow(["def_ndr r width x2", "set_ndr clock_ r"])
    v = _verdicts(out)
    assert len(v) == 1, v
    assert v[0].startswith("BUDA-1915: WARNING:")
    assert "'clock_'" in v[0] and "matches no net" in v[0]


def test_a_shadowed_scope_is_reported_with_the_prefix_that_took_it():
    """The shape a rule-level check cannot see.  Both scopes name the SAME
    rule, so "is the rule used?" answers yes while the `c` declaration does
    nothing at all — which is why the report resolves the winning PREFIX
    and not just the rule.

    INFO, not WARNING: a layered set of scopes can shadow one on purpose.
    It names an example shadower, because once you know a scope is
    outranked, by what is the only question left."""
    _s, out = _flow(["def_ndr r width x2",
                     "set_ndr c r",          # matches clk_0..3, wins on none
                     "set_ndr clk_ r"])      # …because this is longer
    v = _verdicts(out)
    assert len(v) == 1, v
    assert v[0].startswith("BUDA-1916: INFO:")
    assert "'c'" in v[0] and "'clk_'" in v[0]


def test_a_scope_that_governs_says_nothing():
    """The false-positive guard.  A diagnostic that fires on a correct
    design is one a methodology learns to filter out, taking the case it
    exists for with it."""
    _s, out = _flow(["def_ndr r width x2", "set_ndr clk_ r"])
    assert _verdicts(out) == []


def test_the_global_default_is_exempt_when_every_net_has_a_longer_prefix():
    """The documented false alarm, and the reason this needed thinking
    about before it needed coding.

    `*` is the global default and is outranked by any real prefix BY
    CONSTRUCTION, so a design whose every net matches a longer prefix
    leaves it legitimately unused.  Here `clk_` and `data` cover all twelve
    nets, so `*` governs zero — and must still be silent."""
    _s, out = _flow(["def_ndr r width x2",
                     "set_ndr * r", "set_ndr clk_ r", "set_ndr data r"])
    assert _verdicts(out) == []


def test_the_verdict_is_said_once_not_once_per_site():
    """It is emitted at bundling AND before detailed NUTS — the second
    because `load_pipeline` restores bundles without re-bundling, so a
    resumed session never reaches the first.  A flow that runs both must
    still hear it once; the memo is keyed on the verdict, so a repeat is
    silent while a verdict that genuinely changed is not."""
    _s, out = _flow(["def_ndr r width x2", "set_ndr clock_ r"])
    assert out.count("BUDA-1915") == 1, out


def test_the_scope_index_resolves_exactly_as_the_scan_it_replaced():
    """The prefix index exists for speed, so what it must not change is the
    ANSWER.

    `translate_def_ndrs` attaches one `set_ndr` scope per net, so an
    imported design can have as many scopes as nets and a per-net scan over
    all of them is quadratic.  Probing the net's own prefixes instead is
    O(distinct scope lengths) — but longest-prefix-wins with `*` as a
    fallback has enough corners (a scope equal to the whole name, two
    scopes where one is a prefix of the other, `*` present or absent, no
    scope matching at all) that "obviously equivalent" is not good enough.

    So it is checked against the scan it replaced, on randomized scope sets
    built FROM the net names, which is what makes the prefixes actually
    collide rather than miss uninterestingly."""
    import random
    from buda_cmds.ndr_cmds import ndr_scope_index, ndr_scopes_matching

    def scan(scopes, net):                     # the pre-index implementation
        best_p, best_len = None, -1
        for prefix in scopes:
            if prefix == "*":
                if best_len < 0:
                    best_p, best_len = prefix, 0
            elif net.startswith(prefix) and len(prefix) > best_len:
                best_p, best_len = prefix, len(prefix)
        return best_p

    class _S:
        pass

    rnd = random.Random(7)
    checked = 0
    for _ in range(2000):
        nets = ["".join(rnd.choice("abc_") for _ in range(rnd.randint(1, 8)))
                for _ in range(6)]
        keys = set()
        for _ in range(rnd.randint(0, 6)):
            src = rnd.choice(nets)
            keys.add(src[:rnd.randint(1, len(src))])
        if rnd.random() < 0.5:
            keys.add("*")
        s = _S()
        s._ndr_scopes = {k: "r" for k in keys}
        index = ndr_scope_index(s)
        for n in nets:
            assert ndr_scopes_matching(n, index)[0] == scan(s._ndr_scopes, n), (
                n, sorted(keys))
            checked += 1
    assert checked > 10000, checked
