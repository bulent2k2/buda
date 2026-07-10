"""Bottom-up template planning — step 1: BDB flag + set_bottom_up + guards.

Covers the v17 schema (cell.bottom_up), its accessors and Python bindings,
the `set_bottom_up` CLI command, and the instance-congruence guards at mark
time and at `run_planner hier` expansion time.

The congruence guard is deliberately geometric, not orient-only:
rotate_comp / flip_comp on a *hierarchical* block rewrite the children's
absolute bboxes and keep orient='N', so the orient token alone cannot
detect a rotated instance of a cell with children.

See docs/internal/hier_bottom_up_planning.md.
"""

import contextlib
import io
import sqlite3

import pytest
import buda
import buda_cli


# ── BDB flag: accessors, persistence, migration ──────────────────────────────

def test_cell_bottom_up_flag_roundtrip():
    db = buda.BDB(":memory:")
    db.add_cell("a_cell", 100, 50)
    db.add_cell("b_cell", 100, 50)
    assert db.cell_bottom_up("a_cell") is False        # default off
    assert db.bottom_up_cells() == []

    db.set_cell_bottom_up("a_cell", True)
    assert db.cell_bottom_up("a_cell") is True
    assert db.cell_bottom_up("b_cell") is False
    assert db.bottom_up_cells() == ["a_cell"]
    rows = {c.name: c.bottom_up for c in db.all_cells()}   # CellRow binding
    assert rows == {"a_cell": True, "b_cell": False}

    db.set_cell_bottom_up("a_cell", False)
    assert db.bottom_up_cells() == []


def test_set_cell_bottom_up_unknown_cell_raises():
    db = buda.BDB(":memory:")
    with pytest.raises(RuntimeError, match="cell not defined"):
        db.set_cell_bottom_up("nope", True)
    # Reads on an unknown cell are non-throwing conveniences.
    assert db.cell_bottom_up("nope") is False


def test_add_cell_and_resize_cell_preserve_flag():
    """add_cell / resize_cell must upsert, not REPLACE — a REPLACE deletes and
    re-inserts the row, silently resetting bottom_up as a resize side effect."""
    db = buda.BDB(":memory:")
    db.add_cell("c", 10, 20)
    db.set_cell_bottom_up("c", True)
    db.add_cell("c", 30, 40)          # re-declare size
    assert db.cell_bottom_up("c") is True
    db.resize_cell("c", 50, 60)
    assert db.cell_bottom_up("c") is True
    sizes = {c.name: (c.width, c.height) for c in db.all_cells()}
    assert sizes["c"] == (50, 60)     # geometry updates still applied


def test_flag_persists_across_reopen(tmp_path):
    p = str(tmp_path / "flag.bdb")
    db = buda.BDB(p)
    db.add_cell("proc_cell", 420, 200)
    db.set_cell_bottom_up("proc_cell", True)
    del db
    db2 = buda.BDB(p)
    assert db2.cell_bottom_up("proc_cell") is True
    assert db2.schema_version() == buda.BDB.SCHEMA_VERSION


def test_v16_to_v17_migration_adds_bottom_up(tmp_path):
    """Opening a pre-v17 DB adds cell.bottom_up (default 0) and stamps the
    new schema version."""
    p = str(tmp_path / "v16.bdb")
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE cell (name TEXT PRIMARY KEY,
                           width REAL NOT NULL, height REAL NOT NULL);
        INSERT INTO cell VALUES('old_cell', 10, 20);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES('schema_version','16');
        PRAGMA user_version = 16;
        """
    )
    con.commit()
    con.close()

    db = buda.BDB(p)
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    assert db.meta_get("schema_version") == str(buda.BDB.SCHEMA_VERSION)
    assert db.cell_bottom_up("old_cell") is False
    db.set_cell_bottom_up("old_cell", True)
    assert db.bottom_up_cells() == ["old_cell"]


def test_meta_set_binding():
    db = buda.BDB(":memory:")
    assert db.meta_get("bu_policy", "unset") == "unset"
    db.meta_set("bu_policy", "stop")
    assert db.meta_get("bu_policy") == "stop"
    db.meta_set("bu_policy", "independent")     # upsert overwrites
    assert db.meta_get("bu_policy") == "independent"


# ── Congruence helper ─────────────────────────────────────────────────────────

def _two_inst_db(x2=500, y2=0, cross_net=False, path=":memory:", derive=True):
    """proc_cell (two pipe_cell children) instantiated twice, with 4-bit
    cell-local buses in each instance — the template-sharing vehicle.
    cross_net adds a depth-0 4-bit bus between two leaf blocks placed left
    and right of proc_i1, whose direct route crosses that instance.
    derive=False skips busterm derivation (for flows that align placement
    first and derive afterwards)."""
    db = buda.BDB(path)
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst("proc_i1", "proc_cell", "", 0, 0)
    db.add_inst("proc_i2", "proc_cell", "", x2, y2)
    for i in range(4):
        db.add_net_pins(f"ab1_{i}", "proc_i1/pa_i.out", ["proc_i1/pb_i.in"])
        db.add_net_pins(f"ab2_{i}", "proc_i2/pa_i.out", ["proc_i2/pb_i.in"])
    if cross_net:
        db.add_cell("leaf_cell", 60, 60)
        db.add_inst("L0", "leaf_cell", "", -200, 70)
        db.add_inst("R0", "leaf_cell", "", 1200, 70)
        for i in range(4):
            db.add_net_pins(f"lr_{i}", "L0.out", ["R0.in"])
    if derive:
        buda.BustermGen(db).derive(1)
    return db


def _bare_session(db):
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = db
    return s


def test_congruence_ok_for_translated_instances():
    s = _bare_session(_two_inst_db())
    assert s._bottom_up_congruence_issues("proc_cell") == []


def test_congruence_flags_rotated_leaf_instance_via_orient():
    """A childless instance rotation composes the orient token — caught by
    the orientation check even when the outline is unchanged (square)."""
    db = buda.BDB(":memory:")
    db.add_cell("leaf_cell", 80, 80)
    db.add_inst("L1", "leaf_cell", "", 0, 0)
    db.add_inst("L2", "leaf_cell", "", 200, 0)
    db.rotate_comp("L2", 90)          # 80x80: outline unchanged, orient moves
    s = _bare_session(db)
    issues = s._bottom_up_congruence_issues("leaf_cell")
    assert issues and "L2" in issues[0] and "orientation" in issues[0]


def test_congruence_flags_rotated_hier_instance_via_geometry():
    """rotate_comp on a block WITH children rewrites the children and keeps
    orient='N' — only the geometric child comparison can catch it."""
    db = _two_inst_db()
    db.rotate_comp("proc_i2", 180)    # outline unchanged, children reflected
    comps = {c.name: c for c in db.all_components()}
    assert comps["proc_i2"].orient == "N"   # precondition for this test
    s = _bare_session(db)
    issues = s._bottom_up_congruence_issues("proc_cell")
    assert issues and "subtree differs" in issues[0]


def test_congruence_flags_moved_grandchild():
    """The comparison walks the FULL subtree: a moved grandchild leaves the
    outline and the direct children matching, so a one-level check would
    pass — and the translation-only copies would be wrong (PR review #1)."""
    db = buda.BDB(":memory:")
    db.add_cell("leaf_cell", 30, 30)
    db.add_cell("mid_cell", 100, 100)
    db.add_cell("top_cell", 300, 200)
    db.add_inst_to_cell("mid_cell", "l_i", "leaf_cell", 10, 10)
    db.add_inst_to_cell("top_cell", "m_i", "mid_cell", 50, 50)
    db.add_inst("T1", "top_cell", "", 0, 0)
    db.add_inst("T2", "top_cell", "", 400, 0)
    s = _bare_session(db)
    assert s._bottom_up_congruence_issues("top_cell") == []
    db.move_comp("T2/m_i/l_i", 475, 65)      # grandchild of T2 only
    issues = s._bottom_up_congruence_issues("top_cell")
    assert issues and "subtree differs" in issues[0] and "m_i/l_i" in issues[0]


# ── set_bottom_up command ─────────────────────────────────────────────────────

def _run_cmd(s, cmd):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command(cmd)
    return out.getvalue()


def test_set_bottom_up_command_on_off():
    db = _two_inst_db()
    s = _bare_session(db)
    out = _run_cmd(s, "set_bottom_up proc_cell")
    assert "bottom_up = on" in out and "2 instance(s)" in out
    assert db.cell_bottom_up("proc_cell") is True
    out = _run_cmd(s, "set_bottom_up proc_cell off")
    assert "bottom_up = off" in out
    assert db.cell_bottom_up("proc_cell") is False


def test_set_bottom_up_command_errors():
    s = buda_cli.BudaSession()
    s.no_viz = True
    assert "open_bdb first" in _run_cmd(s, "set_bottom_up proc_cell")
    s.bdb = _two_inst_db()
    assert "Error" in _run_cmd(s, "set_bottom_up no_such_cell")
    assert "on|off" in _run_cmd(s, "set_bottom_up proc_cell maybe")
    assert s.bdb.bottom_up_cells() == []


def test_set_bottom_up_command_rejects_non_congruent():
    db = _two_inst_db()
    db.rotate_comp("proc_i2", 180)
    s = _bare_session(db)
    out = _run_cmd(s, "set_bottom_up proc_cell")
    assert "not congruent" in out
    assert db.cell_bottom_up("proc_cell") is False
    # 'off' is always allowed (it only relaxes constraints).
    db2 = _two_inst_db()
    s2 = _bare_session(db2)
    _run_cmd(s2, "set_bottom_up proc_cell")
    db2.rotate_comp("proc_i2", 180)
    assert "bottom_up = off" in _run_cmd(s2, "set_bottom_up proc_cell off")


# ── run_planner hier expansion guard ──────────────────────────────────────────

def _flow_session(db):
    """Session driven through the hier flow up to (not including) planning."""
    s = _bare_session(db)
    for c in ["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
              "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
              "run_hier_bundler", "generate_hier_topologies"]:
        _run_cmd(s, c)
    return s


def test_planner_hier_ok_for_congruent_bottom_up_cell():
    db = _two_inst_db()
    s = _flow_session(db)
    _run_cmd(s, "set_bottom_up proc_cell")
    _run_cmd(s, "run_planner hier")          # must not raise
    # Template expanded to one wrapper per instance, as usual.
    insts = {b.instances[0] for w in s.bundles
             for b in [w.input.original_bundle] if b.cell_context}
    assert insts == {"proc_i1", "proc_i2"}


def test_planner_hier_rejects_rotated_instance_of_bottom_up_cell():
    """Placement may change after marking — the expansion re-checks congruence
    and hard-errors, because translation-only copies would be wrong."""
    db = _two_inst_db()
    s = _flow_session(db)
    _run_cmd(s, "set_bottom_up proc_cell")   # congruent at mark time
    db.rotate_comp("proc_i2", 180)           # ... then placement changes
    with pytest.raises(RuntimeError, match="bottom-up cell 'proc_cell'"):
        _run_cmd(s, "run_planner hier")


def test_planner_hier_unmarked_cell_is_not_blocked():
    """Without the bottom-up mark the same rotation is not an error (the
    top-down flow plans each instance separately)."""
    db = _two_inst_db()
    s = _flow_session(db)
    db.rotate_comp("proc_i2", 180)
    _run_cmd(s, "run_planner hier")          # must not raise


# ── Step 2: local template solve + pin broadcast + hier.locked ────────────────

def _cell_wrappers(s):
    """Expanded per-instance wrappers of the cell-local template, by instance."""
    return {b.instances[0]: w for w in s.bundles
            for b in [w.input.original_bundle] if b.cell_context}


def test_bottom_up_instances_share_one_assignment():
    """The marked cell is planned once locally; every instance carries the
    same pinned candidate index AND identical per-segment layers, and is
    hier.locked."""
    db = _two_inst_db()
    s = _flow_session(db)
    _run_cmd(s, "set_bottom_up proc_cell")
    out = _run_cmd(s, "run_planner hier")
    assert "[BottomUp] cell 'proc_cell'" in out
    ws = _cell_wrappers(s)
    assert set(ws) == {"proc_i1", "proc_i2"}
    w1, w2 = ws["proc_i1"], ws["proc_i2"]
    assert w1.hier.locked and w2.hier.locked
    assert w1.input.topology_pinned and w2.input.topology_pinned
    assert w1.plan.selected_topology_index == w2.plan.selected_topology_index
    assert list(w1.plan.seg_layers) == list(w2.plan.seg_layers)
    assert len(w1.plan.seg_layers) > 0
    # And the two selected topologies are exact translates (same type,
    # same segment count).
    t1 = w1.input.candidates[w1.plan.selected_topology_index]
    t2 = w2.input.candidates[w2.plan.selected_topology_index]
    assert t1.type == t2.type and len(t1.segments) == len(t2.segments)


def test_unmarked_instances_are_not_locked():
    db = _two_inst_db()
    s = _flow_session(db)
    _run_cmd(s, "run_planner hier")
    ws = _cell_wrappers(s)
    assert ws and all(not w.hier.locked for w in ws.values())


def test_locked_wrappers_excluded_from_ripup_and_replan():
    """ripup_reroute must never pick a locked wrapper as a re-route contender,
    and the planner's incremental replan refuses a locked target outright."""
    db = _two_inst_db()
    s = _flow_session(db)
    _run_cmd(s, "set_bottom_up proc_cell")
    _run_cmd(s, "run_planner hier")
    _run_cmd(s, "run_nuts")
    locked_ids = {w.input.original_bundle.id for w in s.bundles
                  if w.hier.locked}
    assert locked_ids
    assert not (set(s._rr_contenders('a')) & locked_ids)
    for bid in locked_ids:
        assert s.planner.replan_bundle(s.bundles, bid) is None
        assert s.planner.replan_bundle_ripup(s.bundles, bid) == []


# ── Step 3: local NUTS solve + fixed per-instance copies ──────────────────────

def _fixed_by_inst_seg(s):
    """(instance, seg_idx) → TrackSegment for the cell-instance wrappers."""
    inst_of = {w.input.original_bundle.id: b.instances[0]
               for w in s.bundles
               for b in [w.input.original_bundle] if b.cell_context}
    return {(inst_of[ts.bundle_id], ts.seg_idx): ts
            for ts in s.nuts_result.segments if ts.bundle_id in inst_of}


def test_bottom_up_nuts_copies_are_uniform_translates():
    """The marked cell's NUTS layout is solved once and copied: every
    instance segment equals the sibling's shifted by the instance offset,
    on BOTH axes (instances offset in x and y)."""
    db = _two_inst_db(x2=500, y2=300)
    s = _flow_session(db)
    _run_cmd(s, "set_bottom_up proc_cell")
    _run_cmd(s, "run_planner hier")
    out = _run_cmd(s, "run_nuts")
    assert "[BottomUp] cell 'proc_cell': local NUTS placed" in out
    segs = _fixed_by_inst_seg(s)
    n = len({k[1] for k in segs})
    assert n > 0
    for si in range(n):
        a, b = segs[("proc_i1", si)], segs[("proc_i2", si)]
        assert a.placed and b.placed
        assert a.layer == b.layer and a.horiz == b.horiz
        d_along, d_perp = (500, 300) if a.horiz else (300, 500)
        assert b.track_position == pytest.approx(a.track_position + d_perp)
        assert min(b.span_lo, b.span_hi) == pytest.approx(
            min(a.span_lo, a.span_hi) + d_along)
        assert max(b.span_lo, b.span_hi) == pytest.approx(
            max(a.span_lo, a.span_hi) + d_along)


def test_bottom_up_fixed_blocks_crossing_bundle():
    """A depth-0 bus routed across a marked instance must not overlap the
    fixed copies — they enter every solver pass as hard occupancy."""
    db = _two_inst_db(cross_net=True)
    s = _flow_session(db)
    _run_cmd(s, "set_bottom_up proc_cell")
    _run_cmd(s, "run_planner hier")
    _run_cmd(s, "run_nuts")
    assert s.nuts_result.num_overlaps == 0
    # The fixed copies and the crossing bundle are both in the result.
    bids = {ts.bundle_id for ts in s.nuts_result.segments}
    locked = {w.input.original_bundle.id for w in s.bundles if w.hier.locked}
    assert locked and locked <= bids and len(bids) > len(locked)


def test_bottom_up_fixed_stable_across_ripup():
    """ripup_reroute must leave the fixed copies exactly in place (their
    bundles are excluded as contenders and extraction skips them)."""
    db = _two_inst_db(cross_net=True)
    s = _flow_session(db)
    _run_cmd(s, "set_bottom_up proc_cell")
    _run_cmd(s, "run_planner hier")
    _run_cmd(s, "run_nuts")
    before = {(ts.bundle_id, ts.seg_idx): ts.track_position
              for ts in s.nuts_result.segments}
    _run_cmd(s, "ripup_reroute 2")
    locked = {w.input.original_bundle.id for w in s.bundles if w.hier.locked}
    after = {(ts.bundle_id, ts.seg_idx): ts.track_position
             for ts in s.nuts_result.segments}
    for key, pos in after.items():
        if key[0] in locked:
            assert pos == pytest.approx(before[key])


# ── Steps 4+5: check_template_tracks + DNUTS reference solve & copy ──────────

_PATTERNS = ["def_track_pattern 6 0 SIGNAL 1 1",
             "def_track_pattern 7 0 SIGNAL 1 1",
             "def_track_pattern 4 0 SIGNAL 1 1",
             "def_track_pattern 5 0 SIGNAL 1 1"]


def _dnuts_flow(db, policy_cmd=None):
    """Full bottom-up flow up to run_nuts, with track patterns (pitch 2)."""
    s = _bare_session(db)
    out = []
    for c in (["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
               "def_layer 4 M4 H 50", "def_layer 5 M5 V 50"]
              + _PATTERNS
              + ["run_hier_bundler", "generate_hier_topologies",
                 "set_bottom_up proc_cell", "run_planner hier", "run_nuts"]
              + ([policy_cmd] if policy_cmd else [])):
        out.append(_run_cmd(s, c))
    return s, "".join(out)


def test_check_template_tracks_aligned():
    """Instances offset by a multiple of the track pitch (2) on both axes
    see identical relative track pools — ALIGNED."""
    s, _ = _dnuts_flow(_two_inst_db(x2=500, y2=300))
    out = _run_cmd(s, "check_template_tracks")
    assert "ALIGNED" in out and "MISALIGNED" not in out
    v = s._template_track_verdict["proc_cell"]
    assert v["misaligned"] == {} and len(v["aligned"]) == 2


def test_check_template_tracks_misaligned():
    """A half-pitch offset (y2=301, pitch 2) shifts every horizontal-layer
    track pool — MISALIGNED, and the default 'stop' policy refuses DNUTS."""
    s, _ = _dnuts_flow(_two_inst_db(x2=500, y2=301))
    out = _run_cmd(s, "check_template_tracks")
    assert "MISALIGNED" in out and "proc_i2" in out
    out = _run_cmd(s, "run_detailed_nuts")
    assert "Error" in out and "different signal tracks" in out
    assert s.detailed_result is None


def test_dnuts_copies_aligned_instances():
    """Aligned cell: the reference instance's bits are solved once and the
    sibling's bits are exact translates; vias copied too."""
    s, _ = _dnuts_flow(_two_inst_db(x2=500, y2=300))
    out = _run_cmd(s, "run_detailed_nuts")   # implicit check, then copy
    assert "[BottomUp] DNUTS:" in out
    inst_of = {w.input.original_bundle.id: b.instances[0]
               for w in s.bundles
               for b in [w.input.original_bundle] if b.cell_context}
    horiz_of = {(ts.bundle_id, ts.seg_idx): ts.horiz
                for ts in s._bottom_up_fixed_segments()}
    bits = {}
    for ns in s.detailed_result.net_segments:
        if ns.bundle_id in inst_of:
            bits[(inst_of[ns.bundle_id], ns.seg_idx, ns.bit_index)] = ns
    keys1 = {k[1:] for k in bits if k[0] == "proc_i1"}
    keys2 = {k[1:] for k in bits if k[0] == "proc_i2"}
    assert keys1 and keys1 == keys2
    for si, bi in keys1:
        a = bits[("proc_i1", si, bi)]
        b = bits[("proc_i2", si, bi)]
        horiz = horiz_of[(a.bundle_id, si)]
        d_along, d_perp = (500, 300) if horiz else (300, 500)
        assert b.track_position == pytest.approx(a.track_position + d_perp)
        assert b.span_lo == pytest.approx(a.span_lo + d_along)
        assert b.span_hi == pytest.approx(a.span_hi + d_along)
    vias = {}
    for v in s.detailed_result.net_vias:
        if v.bundle_id in inst_of:
            vias.setdefault(inst_of[v.bundle_id], []).append(v)
    assert len(vias.get("proc_i1", [])) == len(vias.get("proc_i2", []))


def test_dnuts_independent_policy_solves_outliers():
    """'independent' policy: the aligned reference still solves; the
    misaligned sibling is solved individually — all bits land on real
    tracks, none silently dropped."""
    s, _ = _dnuts_flow(_two_inst_db(x2=500, y2=301),
                       policy_cmd="check_template_tracks "
                                  "on_mismatch independent")
    out = _run_cmd(s, "run_detailed_nuts")
    assert "Error" not in out
    assert s.detailed_result is not None
    inst_of = {w.input.original_bundle.id: b.instances[0]
               for w in s.bundles
               for b in [w.input.original_bundle] if b.cell_context}
    per_inst = {}
    for ns in s.detailed_result.net_segments:
        if ns.bundle_id in inst_of:
            per_inst[inst_of[ns.bundle_id]] = \
                per_inst.get(inst_of[ns.bundle_id], 0) + 1
    # Both instances fully routed (4-bit bus, every seg gets 4 bits).
    assert per_inst.get("proc_i1", 0) > 0
    assert per_inst.get("proc_i1") == per_inst.get("proc_i2")
    assert s.detailed_result.num_unplaced == 0


def test_post_nuts_planner_skips_locked_wrappers():
    """run_planner post_nuts must not reassign a locked wrapper's layers:
    extraction skips fixed bundles, so a reassignment would diverge
    plan.seg_layers from the placed copies (and a later resume would
    restore an internally inconsistent pin).  PR #238 re-review finding 1
    — thresholds chosen so the locked trunks would otherwise qualify."""
    db = _two_inst_db(cross_net=True)
    s = _flow_session(db)
    _run_cmd(s, "set_bottom_up proc_cell")
    _run_cmd(s, "run_planner hier")
    _run_cmd(s, "run_nuts")
    locked_layers = {w.input.original_bundle.id: list(w.plan.seg_layers)
                     for w in s.bundles if w.hier.locked}
    assert locked_layers
    _run_cmd(s, "run_planner post_nuts V 50 300 H 50 300")
    for w in s.bundles:
        bid = w.input.original_bundle.id
        if bid in locked_layers:
            assert list(w.plan.seg_layers) == locked_layers[bid], (
                f"post_nuts moved locked bundle {bid}'s layers")


# ── Placement-stage check + align_bottom_up ──────────────────────────────────

def _placement_session(db):
    """Layers + patterns + mark only — NO derive_busterms / bundler / routing."""
    s = _bare_session(db)
    for c in (["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
               "def_layer 4 M4 H 50", "def_layer 5 M5 V 50"]
              + _PATTERNS + ["set_bottom_up proc_cell"]):
        _run_cmd(s, c)
    return s


def test_check_template_tracks_placement_stage():
    """check_template_tracks works BEFORE derive_busterms: with no routing
    it compares whole-instance windows per grid layer."""
    s = _placement_session(_two_inst_db(x2=500, y2=301, derive=False))
    out = _run_cmd(s, "check_template_tracks")
    assert "placement-stage" in out and "MISALIGNED" in out
    assert "proc_i2" in out
    s2 = _placement_session(_two_inst_db(x2=500, y2=300, derive=False))
    out2 = _run_cmd(s2, "check_template_tracks")
    assert "placement-stage" in out2 and "MISALIGNED" not in out2
    assert "ALIGNED" in out2


def test_align_bottom_up_then_strict_flow():
    """align_bottom_up nudges the off-phase instance by half a pitch, the
    placement check turns ALIGNED, and the full flow then completes DNUTS
    under the STRICT default policy with the reference-solve-and-copy path."""
    db = _two_inst_db(x2=500, y2=301, derive=False)
    s = _placement_session(db)
    out = _run_cmd(s, "align_bottom_up")
    assert "[Align]" in out and "1 instance(s) moved" in out
    comps = {c.name: c for c in db.all_components()}
    assert comps["proc_i2"].y1 == pytest.approx(302)      # +1 to phase 0
    assert comps["proc_i2/pa_i"].y1 == pytest.approx(362) # subtree moved too
    assert s._bottom_up_congruence_issues("proc_cell") == []
    assert "MISALIGNED" not in _run_cmd(s, "check_template_tracks")
    for c in ["derive_busterms 1", "run_hier_bundler",
              "generate_hier_topologies", "run_planner hier", "run_nuts"]:
        _run_cmd(s, c)
    out = _run_cmd(s, "run_detailed_nuts")                # strict default
    assert "[BottomUp] DNUTS:" in out and "Error" not in out


def test_align_bottom_up_minimal_total_movement():
    """The target phase minimizes TOTAL movement: with phases 0, 1, 1 the
    majority phase wins — the reference moves 1, the other two stand still
    (a keep-reference-fixed policy would move 2)."""
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst("P0", "proc_cell", "", 0, 0)
    db.add_inst("P1", "proc_cell", "", 500, 301)
    db.add_inst("P2", "proc_cell", "", 1000, 601)
    s = _placement_session(db)
    out = _run_cmd(s, "align_bottom_up")
    assert "1 instance(s) moved" in out
    comps = {c.name: c for c in db.all_components()}
    assert comps["P0"].y1 == pytest.approx(1)      # ref moved to the majority phase
    assert comps["P1"].y1 == pytest.approx(301)    # untouched
    assert comps["P2"].y1 == pytest.approx(601)    # untouched
    assert s._bottom_up_congruence_issues("proc_cell") == []


def test_align_bottom_up_validate_reports_new_issues():
    """After the moves, FloorplannerEngine.validate() audits the placement:
    a nudge that pushes an instance outside the die (or into an overlap) is
    reported as a NEW issue, distinct from pre-existing ones."""
    db = buda.BDB(":memory:")
    db.set_die(420, 401.9)
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst("P0", "proc_cell", "", 0, 0)
    db.add_inst("P1", "proc_cell", "", 0, 201.5)   # phase 1.5, in-die
    s = _placement_session(db)
    out = _run_cmd(s, "align_bottom_up")
    # P1 snaps +0.5 to P0's phase -> y2 = 402 > die 401.9.
    assert "1 instance(s) moved" in out
    assert "align_bottom_up introduced OUTSIDE_DIE" in out and "P1" in out
    assert "1 new issue(s)" in out and "0 pre-existing" in out


def test_align_bottom_up_validate_quiet_when_clean():
    """No new issues -> no WARNING, just the summary line."""
    db = _two_inst_db(x2=500, y2=301, derive=False)
    s = _placement_session(db)
    out = _run_cmd(s, "align_bottom_up")
    assert "1 instance(s) moved" in out
    assert "introduced" not in out
    assert "0 new issue(s)" in out


def test_align_bottom_up_max_shift_guard():
    db = _two_inst_db(x2=500, y2=301, derive=False)
    s = _placement_session(db)
    out = _run_cmd(s, "align_bottom_up max_shift 0.5")
    assert "WARNING" in out and "0 instance(s) moved" in out
    comps = {c.name: c for c in db.all_components()}
    assert comps["proc_i2"].y1 == pytest.approx(301)      # untouched


# ── Step 6: persistence round-trip (v18 bu_locked + template selection) ──────

def test_bottom_up_persistence_round_trip(tmp_path):
    """Full flow into a file BDB, then a fresh session resumes with
    load_pipeline expanded: locked wrappers (pins included), the fixed
    routing, the template's persisted selection, and the mismatch policy all
    come back, and DNUTS still runs the reference-solve-and-copy path."""
    db_path = str(tmp_path / "bu.bdb")
    db = _two_inst_db(x2=500, y2=300, path=db_path)
    s1, _ = _dnuts_flow(db, policy_cmd="check_template_tracks "
                                       "on_mismatch independent")
    template_id = next(w.input.original_bundle.id
                       for w in s1._hier_bundles_orig
                       if w.input.original_bundle.cell_context)
    # The template's own decision is persisted (canonical local-solve record).
    assert any(t.is_selected for t in s1.bdb.topologies(str(template_id)))
    locked1 = {w.input.original_bundle.id for w in s1.bundles
               if w.hier.locked}
    pos1 = {(ts.bundle_id, ts.seg_idx): ts.track_position
            for ts in s1.nuts_result.segments if ts.bundle_id in locked1}
    assert locked1 and pos1
    del s1, db

    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    for c in (["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
               "def_layer 4 M4 H 50", "def_layer 5 M5 V 50"]
              + _PATTERNS
              + [f"open_bdb {db_path}", "add_blocks_from_bdb 0",
                 "add_blocks_from_bdb 1 skip"]):
        _run_cmd(s2, c)
    out = _run_cmd(s2, "load_pipeline expanded")
    assert "rehydrated" in out
    locked2 = {w.input.original_bundle.id for w in s2.bundles
               if w.hier.locked}
    assert locked2 == locked1
    for w in s2.bundles:
        if w.hier.locked:
            assert w.input.topology_pinned
            assert list(w.input.pinned_seg_layers)
    assert s2._bu_mismatch_policy == "independent"       # meta row restored
    # Resume path: the fixed copies come from the persisted routing.
    fixed2 = {(ts.bundle_id, ts.seg_idx): ts.track_position
              for ts in s2._bottom_up_fixed_segments()}
    assert fixed2 == pytest.approx(pos1)
    out = _run_cmd(s2, "run_detailed_nuts")
    assert "[BottomUp] DNUTS:" in out and "Error" not in out


def test_user_pin_on_template_survives_local_solve():
    """A user-pinned template keeps its pinned candidate through the local
    solve — bottom-up only adds the uniform layer assignment on top."""
    db = _two_inst_db()
    s = _flow_session(db)
    _run_cmd(s, "set_bottom_up proc_cell")
    template = next(w for w in s.bundles
                    if w.input.original_bundle.cell_context)
    assert len(template.input.candidates) >= 2, "need alternatives to pin"
    pin_idx = 1
    template.input.topology_pinned = True
    template.plan.selected_topology_index = pin_idx
    _run_cmd(s, "run_planner hier")
    ws = _cell_wrappers(s)
    assert all(w.plan.selected_topology_index == pin_idx
               for w in ws.values())
    assert all(w.hier.locked for w in ws.values())
