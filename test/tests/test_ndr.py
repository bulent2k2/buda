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


def test_hier_bundler_refuses_with_scopes():
    s = _bare_session()
    _run(s, "def_ndr r width x2")
    _run(s, "set_ndr clk_ r")
    with pytest.raises(SystemExit):
        _run(s, "run_hier_bundler")


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
