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


def test_congruence_detects_rotated_leaf_instance_via_orient():
    """A childless instance 90° rotation composes the orient token — caught
    by the detection even when the outline is unchanged (square).  90° is
    fine at mark time (the rotation-class split gives it its own clone
    template); the post-split re-check (allow_90=False) still flags it."""
    db = buda.BDB(":memory:")
    db.add_cell("leaf_cell", 80, 80)
    db.add_inst("L1", "leaf_cell", "", 0, 0)
    db.add_inst("L2", "leaf_cell", "", 200, 0)
    db.rotate_comp("L2", 90)          # 80x80: outline unchanged, orient moves
    s = _bare_session(db)
    assert s._bottom_up_congruence_issues("leaf_cell") == []
    issues = s._bottom_up_congruence_issues("leaf_cell", allow_90=False)
    assert issues and "L2" in issues[0] and "90°" in issues[0]
    assert s._detect_instance_orients("leaf_cell")["L2"] == "W"


def test_congruence_accepts_180_rotated_hier_instance():
    """rotate_comp on a block WITH children rewrites the children and keeps
    orient='N' — the geometric detection recognizes the 180° transform (S),
    which the orientation-aware copies support."""
    db = _two_inst_db()
    db.rotate_comp("proc_i2", 180)    # outline unchanged, children reflected
    comps = {c.name: c for c in db.all_components()}
    assert comps["proc_i2"].orient == "N"   # tokens alone can't see this
    s = _bare_session(db)
    assert s._bottom_up_congruence_issues("proc_cell") == []
    orients = s._detect_instance_orients("proc_cell")
    assert orients["proc_i2"] == "S"


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
    assert issues and "NO orientation" in issues[0] and "T2" in issues[0]


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
    db.move_comp("proc_i2/pa_i", 540, 70)   # child moved: NO orientation fits
    s = _bare_session(db)
    out = _run_cmd(s, "set_bottom_up proc_cell")
    assert "not congruent" in out
    assert db.cell_bottom_up("proc_cell") is False
    # 'off' is always allowed (it only relaxes constraints).
    db2 = _two_inst_db()
    s2 = _bare_session(db2)
    _run_cmd(s2, "set_bottom_up proc_cell")
    db2.move_comp("proc_i2/pa_i", 540, 70)
    assert "bottom_up = off" in _run_cmd(s2, "set_bottom_up proc_cell off")


def test_set_bottom_up_command_accepts_mirrored_180_and_90():
    """Direction-preserving orientations (S/FN/FS) are copied directly; a
    90° instance is fine too — the rotation-class split at run_planner hier
    gives it its own clone template.  Marking must succeed for all."""
    for xform in [lambda db: db.rotate_comp("proc_i2", 180),
                  lambda db: db.flip_comp("proc_i2", True),
                  lambda db: db.flip_comp("proc_i2", False),
                  lambda db: db.rotate_comp("proc_i2", 90),
                  lambda db: db.rotate_comp("proc_i2", 270)]:
        db = _two_inst_db()
        xform(db)
        s = _bare_session(db)
        out = _run_cmd(s, "set_bottom_up proc_cell")
        assert "bottom_up = on" in out, out
        assert db.cell_bottom_up("proc_cell") is True


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


def test_planner_hier_splits_90_rotated_instance_into_clone():
    """A marked cell with a 90°-rotated instance is no longer refused: at
    run_planner hier the rotation class gets its OWN clone template
    (candidates generated from the rotated reference's cell-local
    floorplan), and both classes plan/lock as usual."""
    db = _two_inst_db(derive=False)
    db.rotate_comp("proc_i2", 90)
    buda.BustermGen(db).derive(1)
    s = _flow_session(db)
    _run_cmd(s, "set_bottom_up proc_cell")
    out = _run_cmd(s, "run_planner hier")            # must not raise
    assert "split into clone template" in out and "proc_cell90" in out
    ctxs = {b.cell_context for w in s.bundles
            for b in [w.input.original_bundle] if b.cell_context}
    assert ctxs == {"proc_cell", "proc_cell90"}
    assert s._bu_clone_cells == {"proc_cell90": "proc_cell"}
    # Both classes' instances locked (each carries its own local solve).
    locked = {b.cell_context for w in s.bundles
              for b in [w.input.original_bundle]
              if b.cell_context and w.hier.locked}
    assert locked == {"proc_cell", "proc_cell90"}
    # The clone persisted with provenance (v19 cloned_from → template id).
    clone_rows = [r for r in db.all_bundles() if r.cloned_from]
    assert clone_rows and all(r.cell_context == "proc_cell90"
                              for r in clone_rows)


def test_planner_hier_unmarked_cell_is_not_blocked():
    """Without the bottom-up mark the same rotation is not an error (the
    top-down flow plans each instance separately)."""
    db = _two_inst_db()
    s = _flow_session(db)
    db.rotate_comp("proc_i2", 90)
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


def _outside_die_db():
    """P1 snaps +0.5 to P0's phase -> y2 = 402 > die 401.9."""
    db = buda.BDB(":memory:")
    db.set_die(420, 401.9)
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst("P0", "proc_cell", "", 0, 0)
    db.add_inst("P1", "proc_cell", "", 0, 201.5)   # phase 1.5, in-die
    return db


def test_align_bottom_up_auto_reverts_new_issues():
    """DEFAULT: a move that introduces a NEW issue (here OUTSIDE_DIE) is
    auto-reverted — the exact-geometry slack cap — and the final placement
    audit is clean."""
    db = _outside_die_db()
    s = _placement_session(db)
    out = _run_cmd(s, "align_bottom_up")
    assert "REVERTED" in out and "OUTSIDE_DIE" in out and "P1" in out
    assert "0 instance(s) moved, 1 reverted" in out
    assert "0 new issue(s)" in out
    comps = {c.name: c for c in db.all_components()}
    assert comps["P1"].y1 == pytest.approx(201.5)         # back in place
    assert comps["P1/pa_i"].y1 == pytest.approx(261.5)    # subtree restored


def test_align_bottom_up_force_keeps_issue_moves():
    """'force' restores the report-only behavior: the move is kept and the
    new issue is warned."""
    db = _outside_die_db()
    s = _placement_session(db)
    out = _run_cmd(s, "align_bottom_up force")
    assert "1 instance(s) moved" in out and "REVERTED" not in out
    assert "align_bottom_up introduced OUTSIDE_DIE" in out
    assert "1 new issue(s)" in out and "0 pre-existing" in out
    comps = {c.name: c for c in db.all_components()}
    assert comps["P1"].y1 == pytest.approx(202)           # move kept


def test_align_bottom_up_revert_on_overlap_with_neighbor():
    """A nudge into an unmarked neighbor's footprint is reverted; the
    already-aligned sibling keeps its position."""
    db = buda.BDB(":memory:")
    db.add_cell("box_cell", 100, 100)
    db.add_cell("blk_cell", 80, 0.3)
    db.add_inst("A", "box_cell", "", 0, 0)
    db.add_inst("B", "box_cell", "", 0, 100.6)     # phase 0.6
    db.add_inst("X0", "blk_cell", "", 10, 100.05)  # blocker in the gap
    s = _bare_session(db)
    for c in (["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
               "def_layer 4 M4 H 50", "def_layer 5 M5 V 50"] + _PATTERNS
              + ["set_bottom_up box_cell"]):
        _run_cmd(s, c)
    out = _run_cmd(s, "align_bottom_up")
    # B's -0.6 snap lands on the blocker -> reverted; A never moved.
    assert "REVERTED" in out and "OVERLAP" in out
    assert "0 instance(s) moved, 1 reverted" in out
    comps = {c.name: c for c in db.all_components()}
    assert comps["B"].y1 == pytest.approx(100.6)
    assert comps["A"].y1 == pytest.approx(0)


def test_align_bottom_up_validate_quiet_when_clean():
    """No new issues -> no WARNING, just the summary line."""
    db = _two_inst_db(x2=500, y2=301, derive=False)
    s = _placement_session(db)
    out = _run_cmd(s, "align_bottom_up")
    assert "1 instance(s) moved" in out
    assert "introduced" not in out
    assert "0 new issue(s)" in out


def test_align_bottom_up_warns_on_stale_busterms():
    """Codex #244 finding 1: the staleness warning must fire when
    derive_busterms ran before the align — not only after bundling."""
    db = _two_inst_db(x2=500, y2=301, derive=True)   # busterms already derived
    s = _placement_session(db)
    out = _run_cmd(s, "align_bottom_up")
    assert "WARNING" in out and "derived busterms" in out
    assert "stale" in out


def _nested_db(k9_y=61):
    """Marked parent cell containing an instance of a marked child cell,
    plus a standalone child-cell instance (K9)."""
    db = buda.BDB(":memory:")
    db.add_cell("kid_cell", 50, 50)
    db.add_cell("par_cell", 400, 200)
    db.add_inst_to_cell("par_cell", "k_i", "kid_cell", 20, 60)
    db.add_inst("P0", "par_cell", "", 0, 0)
    db.add_inst("P1", "par_cell", "", 500, 301)      # off-phase parent
    db.add_inst("K9", "kid_cell", "", 1000, k9_y)    # standalone child inst
    return db


def test_align_bottom_up_nested_marked_cells():
    """Codex #244 finding 2: with parent AND child cells marked, the parent
    aligns first, the child's coordinates are re-read AFTER the parent move
    (no double shift), and child instances inside marked parents are
    anchors — only the standalone instance moves, toward them."""
    db = _nested_db()
    s = _bare_session(db)
    for c in (["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
               "def_layer 4 M4 H 50", "def_layer 5 M5 V 50"] + _PATTERNS
              + ["set_bottom_up par_cell", "set_bottom_up kid_cell"]):
        _run_cmd(s, c)
    out = _run_cmd(s, "align_bottom_up")
    assert "2 instance(s) moved" in out
    assert "sits off" not in out                     # anchors agree post-parent-move
    comps = {c.name: c for c in db.all_components()}
    assert comps["P1"].y1 == pytest.approx(302)      # parent snapped +1
    assert comps["P1/k_i"].y1 == pytest.approx(362)  # dragged once, NOT re-shifted
    assert comps["K9"].y1 == pytest.approx(62)       # movable: joins the anchors
    assert comps["P0"].y1 == pytest.approx(0)        # majority stands still
    assert s._bottom_up_congruence_issues("par_cell") == []
    assert s._bottom_up_congruence_issues("kid_cell") == []
    assert "MISALIGNED" not in _run_cmd(s, "check_template_tracks")


def test_align_bottom_up_reports_unfixable_anchor():
    """Two child instances placed at incompatible offsets INSIDE the parent
    template cannot be phase-aligned by translation — reported, not moved."""
    db = buda.BDB(":memory:")
    db.add_cell("kid_cell", 50, 50)
    db.add_cell("par_cell", 400, 200)
    db.add_inst_to_cell("par_cell", "k1", "kid_cell", 20, 60)
    db.add_inst_to_cell("par_cell", "k2", "kid_cell", 100, 61)  # off k1's phase
    db.add_inst("P0", "par_cell", "", 0, 0)
    db.add_inst("P1", "par_cell", "", 500, 300)
    s = _bare_session(db)
    for c in (["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
               "def_layer 4 M4 H 50", "def_layer 5 M5 V 50"] + _PATTERNS
              + ["set_bottom_up par_cell", "set_bottom_up kid_cell"]):
        _run_cmd(s, c)
    before = {c.name: (c.x1, c.y1) for c in db.all_components()}
    out = _run_cmd(s, "align_bottom_up")
    assert "sits off" in out and "not fixable by translation" in out
    after = {c.name: (c.x1, c.y1) for c in db.all_components()}
    assert before == after                           # nothing moved


def test_translate_comp_moves_pins_with_subtree(tmp_path):
    """Pin positions are absolute, so translate_comp shifts the subtree's
    pins too (keeping compute_hpwl and pin consumers honest); the (-1, -1)
    unknown-position sentinel is not shifted (owner review #244 finding 1)."""
    p = str(tmp_path / "pins.bdb")
    db = buda.BDB(p)
    db.add_cell("c", 100, 100)
    db.add_cell_pin("c", "p", "OUTPUT", 10, 20)
    db.add_inst("I0", "c", "", 0, 0)
    db.add_inst("I1", "c", "", 200, 0)
    db.add_net_pins("n0", "I0.p", ["I1.p"])
    i0 = {c.name: c.id for c in db.all_components()}["I0"]
    assert [(q.px, q.py) for q in db.pins_by_comp(i0)] == [(10, 20)]
    del db
    con = sqlite3.connect(p)   # a sentinel pin (no producer is Python-bound)
    con.execute("INSERT INTO pin(net_id, comp_id, pin_name, dir, px, py)"
                " VALUES(1, ?, 'ghost', 'UNKNOWN', -1, -1)", (i0,))
    con.commit()
    con.close()
    db = buda.BDB(p)
    db.translate_comp("I0", 5, 7)
    pins = {q.pin_name: (q.px, q.py) for q in db.pins_by_comp(i0)}
    assert pins["p"] == (15, 27)          # absolute pin rides along
    assert pins["ghost"] == (-1, -1)      # unknown sentinel untouched


def test_align_bottom_up_max_shift_guard():
    db = _two_inst_db(x2=500, y2=301, derive=False)
    s = _placement_session(db)
    out = _run_cmd(s, "align_bottom_up max_shift 0.5")
    assert "WARNING" in out and "0 instance(s) moved" in out
    comps = {c.name: c for c in db.all_components()}
    assert comps["proc_i2"].y1 == pytest.approx(301)      # untouched


# ── save_bdb <path>: save-as snapshot ─────────────────────────────────────────

def test_save_bdb_as_snapshot_after_align(tmp_path):
    """The align-checkpoint use case: save_bdb <path> snapshots the CURRENT
    state (aligned coords + bottom_up flags) to a new file — text (.sql,
    reopenable via open_bdb) or binary — without touching any source."""
    db = _two_inst_db(x2=500, y2=301, derive=False)   # :memory: works too
    s = _placement_session(db)
    _run_cmd(s, "align_bottom_up")
    sql_snap = str(tmp_path / "aligned.bdb.sql")
    bin_snap = str(tmp_path / "aligned.bdb")
    assert "wrote snapshot" in _run_cmd(s, f"save_bdb {sql_snap}")
    assert "wrote snapshot" in _run_cmd(s, f"save_bdb {bin_snap}")

    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    _run_cmd(s2, f"open_bdb {sql_snap}")              # text form round-trips
    comps = {c.name: c for c in s2.bdb.all_components()}
    assert comps["proc_i2"].y1 == pytest.approx(302)  # aligned coord kept
    assert s2.bdb.bottom_up_cells() == ["proc_cell"]  # flag kept

    db3 = buda.BDB(bin_snap)                          # binary form directly
    comps3 = {c.name: c for c in db3.all_components()}
    assert comps3["proc_i2"].y1 == pytest.approx(302)
    assert db3.schema_version() == buda.BDB.SCHEMA_VERSION


def test_save_bdb_as_rejects_live_file(tmp_path):
    """Backing up onto the file the live connection holds open is refused."""
    live = str(tmp_path / "live.bdb")
    db = buda.BDB(live)
    db.add_cell("c", 10, 10)
    del db
    s = buda_cli.BudaSession()
    s.no_viz = True
    _run_cmd(s, f"open_bdb {live}")
    out = _run_cmd(s, f"save_bdb {live}")
    assert "Error" in out and "live BDB file" in out
    assert "wrote snapshot" not in out


def test_save_bdb_as_rejects_live_file_via_symlink(tmp_path):
    """The live-file guard resolves symlinks (Codex #245): a destination
    reaching the open database through a linked path is refused too."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    live = real_dir / "live.bdb"
    db = buda.BDB(str(live))
    db.add_cell("c", 10, 10)
    del db
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir, target_is_directory=True)
    s = buda_cli.BudaSession()
    s.no_viz = True
    _run_cmd(s, f"open_bdb {live}")
    out = _run_cmd(s, f"save_bdb {link_dir / 'live.bdb'}")
    assert "Error" in out and "live BDB file" in out
    assert "wrote snapshot" not in out


def test_save_bdb_no_arg_message_mentions_save_as():
    s = _bare_session(_two_inst_db(derive=False))
    out = _run_cmd(s, "save_bdb")                     # no writeback armed
    assert "save_bdb <file.bdb[.sql]>" in out


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


# ── Orientation-aware copies (mirrors + 180) ─────────────────────────────────

def test_orient_algebra():
    """Compose/inverse spot checks of the shared orientation-map algebra
    (token = mirror-about-X first, then CCW rotation)."""
    assert buda.orient_compose("S", "S") == "N"      # 180 twice
    assert buda.orient_compose("FN", "FN") == "N"    # mirror is an involution
    assert buda.orient_compose("FS", "FS") == "N"
    assert buda.orient_compose("FN", "S") == "FS"    # 180 then y-mirror
    assert buda.orient_compose("W", "W") == "S"      # 90 twice
    for o in ["N", "S", "FN", "FS", "W", "E", "FW", "FE"]:
        assert buda.orient_compose(o, buda.orient_inverse(o)) == "N"
        assert buda.orient_compose(buda.orient_inverse(o), o) == "N"


def _oriented_bits(s):
    """(bits by (inst, seg, bit), horiz-of map) for the cell-local bundles."""
    inst_of = {w.input.original_bundle.id: b.instances[0]
               for w in s.bundles
               for b in [w.input.original_bundle] if b.cell_context}
    horiz_of = {(ts.bundle_id, ts.seg_idx): ts.horiz
                for ts in s._bottom_up_fixed_segments()}
    bits = {}
    for ns in s.detailed_result.net_segments:
        if ns.bundle_id in inst_of:
            bits[(inst_of[ns.bundle_id], ns.seg_idx, ns.bit_index)] = ns
    return bits, horiz_of


def test_dnuts_copies_180_rotated_instance():
    """A 180°-rotated sibling passes the mirror-normalized track check and
    its copied bits are the exact point reflection of the reference's.
    With pitch-2 patterns (centers 0.5+2k) and a 420x200 cell, the S
    instance aligns at ODD offsets on both axes."""
    db = _two_inst_db(x2=501, y2=301, derive=False)
    db.rotate_comp("proc_i2", 180)
    buda.BustermGen(db).derive(1)
    s, _ = _dnuts_flow(db)
    out = _run_cmd(s, "check_template_tracks")
    assert "ALIGNED" in out and "MISALIGNED" not in out
    out = _run_cmd(s, "run_detailed_nuts")
    assert "[BottomUp] DNUTS:" in out and "Error" not in out
    bits, horiz_of = _oriented_bits(s)
    keys1 = {k[1:] for k in bits if k[0] == "proc_i1"}
    keys2 = {k[1:] for k in bits if k[0] == "proc_i2"}
    assert keys1 and keys1 == keys2
    # ref frame: 420x200 at (0, 0); sibling frame at (501, 301), S orient.
    for si, bi in keys1:
        a = bits[("proc_i1", si, bi)]
        b = bits[("proc_i2", si, bi)]
        if horiz_of[(a.bundle_id, si)]:
            assert b.track_position == pytest.approx(
                301 + 200 - a.track_position)
            assert b.span_lo == pytest.approx(501 + 420 - a.span_hi)
            assert b.span_hi == pytest.approx(501 + 420 - a.span_lo)
        else:
            assert b.track_position == pytest.approx(
                501 + 420 - a.track_position)
            assert b.span_lo == pytest.approx(301 + 200 - a.span_hi)
            assert b.span_hi == pytest.approx(301 + 200 - a.span_lo)


def test_dnuts_copies_x_mirrored_instance():
    """An FS (x-mirrored) sibling: horizontal bits keep their track offset
    and mirror their span; vertical bits mirror their track and keep the
    span offset.  x must be odd for the mirrored phase, y stays even."""
    db = _two_inst_db(x2=501, y2=300, derive=False)
    db.flip_comp("proc_i2", True)        # mirror about vertical centre → FS
    buda.BustermGen(db).derive(1)
    s, _ = _dnuts_flow(db)
    out = _run_cmd(s, "check_template_tracks")
    assert "ALIGNED" in out and "MISALIGNED" not in out
    out = _run_cmd(s, "run_detailed_nuts")
    assert "[BottomUp] DNUTS:" in out and "Error" not in out
    bits, horiz_of = _oriented_bits(s)
    keys1 = {k[1:] for k in bits if k[0] == "proc_i1"}
    keys2 = {k[1:] for k in bits if k[0] == "proc_i2"}
    assert keys1 and keys1 == keys2
    for si, bi in keys1:
        a = bits[("proc_i1", si, bi)]
        b = bits[("proc_i2", si, bi)]
        if horiz_of[(a.bundle_id, si)]:
            assert b.track_position == pytest.approx(a.track_position + 300)
            assert b.span_lo == pytest.approx(501 + 420 - a.span_hi)
            assert b.span_hi == pytest.approx(501 + 420 - a.span_lo)
        else:
            assert b.track_position == pytest.approx(
                501 + 420 - a.track_position)
            assert b.span_lo == pytest.approx(a.span_lo + 300)
            assert b.span_hi == pytest.approx(a.span_hi + 300)


def test_check_template_tracks_flags_misplaced_mirrored_instance():
    """The mirror normalization is not a free pass.  The toy cell's child
    layout is y-symmetric, so a flipped instance matches both S and FS and
    the detection may pick whichever mirror the track phase can absorb —
    any INTEGER y offset is thereby healable (pitch 2, 2σ = 1).  An
    off-grid half-unit offset breaks the phase under every interpretation
    and must be flagged."""
    db = _two_inst_db(x2=501, y2=300.5, derive=False)
    db.flip_comp("proc_i2", True)
    buda.BustermGen(db).derive(1)
    s, _ = _dnuts_flow(db)
    out = _run_cmd(s, "check_template_tracks")
    assert "MISALIGNED" in out and "proc_i2" in out


def test_align_bottom_up_mirrored_instance():
    """The aligner moves a mirrored instance via its EFFECTIVE coordinate:
    with pitch-2 patterns (2σ = 1) and cell width 420, an FS instance
    aligns at ODD x — the aligner nudges it off the even grid where the
    translated majority sits (translation math would leave it be)."""
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst("P0", "proc_cell", "", 0, 0)
    db.add_inst("P1", "proc_cell", "", 500, 0)
    db.add_inst("P2", "proc_cell", "", 1000, 0)
    db.flip_comp("P2", True)
    s = _placement_session(db)
    out = _run_cmd(s, "check_template_tracks")
    assert "MISALIGNED" in out and "P2" in out
    out = _run_cmd(s, "align_bottom_up")
    assert "1 instance(s) moved" in out
    comps = {c.name: c for c in db.all_components()}
    assert comps["P2"].x1 in (999.0, 1001.0)   # odd phase, minimal nudge
    assert comps["P0"].x1 == 0.0 and comps["P1"].x1 == 500.0
    out = _run_cmd(s, "check_template_tracks")
    assert "MISALIGNED" not in out and "ALIGNED" in out


def test_topdown_expansion_transforms_rotated_instance():
    """UNMARKED cells: the top-down expansion must transform each
    instance's candidates geometrically (the old translation-only path
    silently mis-transformed them).  A 90°-rotated instance's candidates
    must fit its rotated (dims-swapped) bbox."""
    db = _two_inst_db(x2=500, y2=300, derive=False)
    db.rotate_comp("proc_i2", 90)        # 420x200 → bbox 200x420
    buda.BustermGen(db).derive(1)
    s = _flow_session(db)
    _run_cmd(s, "run_planner hier")
    w2 = [w for w in s.bundles
          for b in [w.input.original_bundle]
          if b.cell_context and b.instances == ["proc_i2"]]
    assert w2
    for w in w2:
        assert w.input.candidates
        for t in w.input.candidates:
            for seg in t.segments:
                for px, py in [(seg.start.x, seg.start.y),
                               (seg.end.x, seg.end.y)]:
                    assert 500 <= px <= 700 and 300 <= py <= 720, (
                        f"segment endpoint ({px}, {py}) outside the rotated "
                        f"instance bbox — translation-only expansion?")


def _orient_of(db, name):
    return next(c.orient for c in db.all_components() if c.name == name)


def test_detection_rejects_mixed_token_subtree():
    """One token convention per matched instance (Codex #249 P2): a
    180°-rotated instance whose child was ADDITIONALLY back-rotated in
    place (bbox unchanged, leaf token composed to 'S') must not pass —
    the child would satisfy the composed convention while its sibling
    satisfies the raw one, yet no single rigid transform maps the
    reference (the leaf content differs)."""
    db = _two_inst_db()
    db.rotate_comp("proc_i2", 180)          # children reflected, tokens 'N'
    assert _orient_of(db, "proc_i2/pa_i") == "N"
    db.rotate_comp("proc_i2/pa_i", 180)     # in-place: bbox same, token 'S'
    assert _orient_of(db, "proc_i2/pa_i") == "S"
    s = _bare_session(db)
    orients = s._detect_instance_orients("proc_cell")
    assert orients["proc_i2"] is None
    issues = s._bottom_up_congruence_issues("proc_cell")
    assert issues and "NO orientation" in issues[0]


# ── Rotation-class clone templates (90°-family occurrences) ──────────────────

def _four_inst_two_class_db(path=":memory:", y3=0, y4=300):
    """proc_cell placed four times: P1/P2 upright (N), P3/P4 rotated 90° —
    the two-rotation-class vehicle.  Each instance carries its own 4-bit
    cell-local bus."""
    db = buda.BDB(path)
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    for k, (x, y) in enumerate([(0, 0), (500, 300),
                                (1000, y3), (1500, y4)], 1):
        db.add_inst(f"P{k}", "proc_cell", "", x, y)
    db.rotate_comp("P3", 90)
    db.rotate_comp("P4", 90)
    for k in range(1, 5):
        for i in range(4):
            db.add_net_pins(f"n{k}_{i}", f"P{k}/pa_i.out", [f"P{k}/pb_i.in"])
    buda.BustermGen(db).derive(1)
    return db


def _two_class_flow(db):
    """Full bottom-up flow (layers, patterns, bundler, topologies, mark,
    plan, nuts) on a two-rotation-class db."""
    s = _bare_session(db)
    out = []
    for c in (["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
               "def_layer 4 M4 H 50", "def_layer 5 M5 V 50"]
              + _PATTERNS
              + ["run_hier_bundler", "generate_hier_topologies",
                 "set_bottom_up proc_cell", "run_planner hier", "run_nuts"]):
        out.append(_run_cmd(s, c))
    return s, "".join(out)


def test_bu_clone_name_uniquified():
    """`<cell>90` collides with a real cell → `_1` suffix; an existing live
    mapping for the cell is reused (stable across re-runs)."""
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("proc_cell90", 10, 10)      # a REAL cell squats the name
    s = _bare_session(db)
    assert s._bu_clone_name("proc_cell") == "proc_cell90_1"
    s._bu_clone_cells = {"weird_name": "proc_cell"}
    assert s._bu_clone_name("proc_cell") == "weird_name"   # reuse, not rename


def test_v18_to_v19_migration_adds_cloned_from(tmp_path):
    """Opening a pre-v19 DB adds bundle.cloned_from (default '') and stamps
    the new schema version."""
    p = str(tmp_path / "v18.bdb")
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE bundle (id TEXT PRIMARY KEY, level INTEGER DEFAULT 0,
            strategy TEXT, reason TEXT, num_terminals INTEGER DEFAULT 0,
            cell_context TEXT, instances TEXT, parent_id TEXT,
            is_replicated INTEGER DEFAULT 0,
            drv_spec_depth INTEGER DEFAULT -1,
            rcv_spec_depth INTEGER DEFAULT -1,
            drv_spec_path TEXT, rcv_spec_paths TEXT,
            gen_knobs TEXT DEFAULT '', is_expanded INTEGER DEFAULT 0,
            bu_locked INTEGER DEFAULT 0);
        INSERT INTO bundle(id, cell_context) VALUES('1', 'old_cell');
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES('schema_version','18');
        PRAGMA user_version = 18;
        """
    )
    con.commit()
    con.close()
    db = buda.BDB(p)
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    rows = db.all_bundles()
    assert rows and rows[0].cloned_from == ""
    r = buda.BundleRow()
    r.id = "2"; r.cell_context = "old_cell90"; r.cloned_from = "1"
    db.add_bundle(r)
    assert {b.id: b.cloned_from for b in db.all_bundles()}["2"] == "1"


def test_two_class_check_and_dnuts_copies():
    """End to end with two rotation classes: each class is checked and
    copied against ITS OWN reference — check reports both groups ALIGNED,
    DNUTS solves one reference per class and translates within the class."""
    db = _four_inst_two_class_db()
    s, out = _two_class_flow(db)
    assert "split into clone template 'proc_cell90'" in out
    out = _run_cmd(s, "check_template_tracks")
    assert out.count("ALIGNED") >= 2 and "MISALIGNED" not in out
    assert set(s._template_track_verdict) == {"proc_cell", "proc_cell90"}
    assert s._template_track_verdict["proc_cell"]["ref"] == "P1"
    assert s._template_track_verdict["proc_cell90"]["ref"] == "P3"
    out = _run_cmd(s, "run_detailed_nuts")   # strict default policy
    assert "[BottomUp] DNUTS:" in out and "Error" not in out
    bits, horiz_of = _oriented_bits(s)
    per_inst = {}
    for (inst, si, bi), ns in bits.items():
        per_inst.setdefault(inst, {})[(si, bi)] = ns
    assert set(per_inst) == {"P1", "P2", "P3", "P4"}
    # Within each class the sibling is an exact translate of its reference.
    for ref, sib, (dx, dy) in [("P1", "P2", (500, 300)),
                               ("P3", "P4", (500, 300))]:
        assert set(per_inst[ref]) == set(per_inst[sib])
        for (si, bi), a in per_inst[ref].items():
            b = per_inst[sib][(si, bi)]
            if horiz_of[(a.bundle_id, si)]:
                assert b.track_position == pytest.approx(
                    a.track_position + dy)
                assert b.span_lo == pytest.approx(a.span_lo + dx)
            else:
                assert b.track_position == pytest.approx(
                    a.track_position + dx)
                assert b.span_lo == pytest.approx(a.span_lo + dy)
    # The two classes genuinely routed in DIFFERENT frames: the rotated
    # class's bus runs vertically where the upright class's runs
    # horizontally (pa->pb is horizontal in the N frame).
    dirs_a = {horiz_of[(ns.bundle_id, si)]
              for (si, bi), ns in per_inst["P1"].items()}
    dirs_b = {horiz_of[(ns.bundle_id, si)]
              for (si, bi), ns in per_inst["P3"].items()}
    assert dirs_a != dirs_b


def test_two_class_persistence_round_trip(tmp_path):
    """The clone template persists (v19 cloned_from) and a fresh session's
    load_pipeline expanded restores the registry, the locked wrappers of
    BOTH classes, and the copy path through DNUTS."""
    p = str(tmp_path / "twoclass.bdb")
    db = _four_inst_two_class_db(path=p)
    s1, _ = _two_class_flow(db)
    locked1 = {w.input.original_bundle.id for w in s1.bundles
               if w.hier.locked}
    ctxs1 = {w.input.original_bundle.cell_context for w in s1.bundles
             if w.hier.locked}
    assert ctxs1 == {"proc_cell", "proc_cell90"}
    del s1, db

    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    for c in (["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
               "def_layer 4 M4 H 50", "def_layer 5 M5 V 50"]
              + _PATTERNS
              + [f"open_bdb {p}", "add_blocks_from_bdb 0",
                 "add_blocks_from_bdb 1 skip"]):
        _run_cmd(s2, c)
    out = _run_cmd(s2, "load_pipeline expanded")
    assert "rehydrated" in out
    assert "rotation-class clone template" in out
    assert s2._bu_clone_cells == {"proc_cell90": "proc_cell"}
    locked2 = {w.input.original_bundle.id for w in s2.bundles
               if w.hier.locked}
    assert locked2 == locked1
    out = _run_cmd(s2, "run_detailed_nuts")
    assert "[BottomUp] DNUTS:" in out and "Error" not in out


def test_placement_check_and_align_per_class():
    """Placement-stage check and align_bottom_up operate per rotation
    class: the upright pair is aligned, the rotated pair (off by one unit
    in y) is reported under the clone group and aligned within it — the
    upright instances never move."""
    db = _four_inst_two_class_db(y3=1, y4=300)   # P3 y=1: off P4's phase
    s = _placement_session(db)
    out = _run_cmd(s, "check_template_tracks")
    assert "'proc_cell': ALIGNED" in out
    assert "'proc_cell90': MISALIGNED" in out
    groups = s._bottom_up_placed_instances()
    assert {c.name for c in groups["proc_cell"]} == {"P1", "P2"}
    assert {c.name for c in groups["proc_cell90"]} == {"P3", "P4"}
    out = _run_cmd(s, "align_bottom_up")
    assert "1 instance(s) moved" in out
    comps = {c.name: c for c in db.all_components()}
    assert comps["P1"].x1 == 0.0 and comps["P2"].x1 == 500.0
    # Exactly one of the rotated pair snapped one unit to the other's phase.
    assert (comps["P3"].y1, comps["P4"].y1) in ((0.0, 300.0), (1.0, 301.0))
    out = _run_cmd(s, "check_template_tracks")
    assert "MISALIGNED" not in out


def test_rotated_only_bundle_reassigned_to_clone_class():
    """Codex #253 finding 1: a bundle that exists ONLY in the 90°
    occurrences has no cross-class members of its own — classification
    against a per-cell reference must still move it WHOLESALE to the clone
    class, or mixed coordinate frames get grouped under the real
    cell_context (reintroducing the H/V-swap the split exists to avoid)."""
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst_to_cell("proc_cell", "pc_i", "pipe_cell", 290, 60)
    for k, (x, y) in enumerate([(0, 0), (500, 300),
                                (1000, 0), (1500, 300)], 1):
        db.add_inst(f"P{k}", "proc_cell", "", x, y)
    db.rotate_comp("P3", 90)
    db.rotate_comp("P4", 90)
    for k in range(1, 5):
        for i in range(4):
            db.add_net_pins(f"n{k}_{i}", f"P{k}/pa_i.out", [f"P{k}/pb_i.in"])
    # An extra bus (pb -> pc: its own signature, hence its own template)
    # that lives ONLY in the rotated instances P3/P4.
    for k in (3, 4):
        for i in range(2):
            db.add_net_pins(f"x{k}_{i}", f"P{k}/pb_i.out", [f"P{k}/pc_i.in"])
    buda.BustermGen(db).derive(1)
    s, out = _two_class_flow(db)
    assert "reassigned to clone template 'proc_cell90'" in out
    # No template under the REAL context may reference a rotated instance.
    for w in s._hier_bundles_orig:
        b = w.input.original_bundle
        if b.cell_context == "proc_cell":
            assert not ({"P3", "P4"} & set(b.instances)), (
                f"bundle {b.id} kept rotated instances under 'proc_cell'")
    # The reassigned row persisted with provenance to a real-context row.
    rows = {r.id: r for r in db.all_bundles()}
    reassigned = [r for r in rows.values()
                  if r.cloned_from and r.cell_context == "proc_cell90"]
    assert reassigned
    for r in reassigned:
        assert rows[r.cloned_from].cell_context == "proc_cell"
    # And the flow still completes cleanly per class.
    out = _run_cmd(s, "check_template_tracks")
    assert "MISALIGNED" not in out
    out = _run_cmd(s, "run_detailed_nuts")
    assert "[BottomUp] DNUTS:" in out and "Error" not in out


def test_rebundle_drops_stale_clone_provenance():
    """Codex #253 finding 2: _bu_clone_from is keyed by numeric bundle id;
    a re-run of the bundler creates fresh bundles that reuse those ids, so
    the map must be cleared or the next persist stamps a bogus cloned_from
    on an unrelated bundle (which load_pipeline would then restore as a
    clone of the wrong cell)."""
    db = _four_inst_two_class_db()
    s, _ = _two_class_flow(db)
    assert s._bu_clone_from                       # the split recorded ids
    _run_cmd(s, "run_hier_bundler")               # fresh bundles, same ids
    assert s._bu_clone_from == {}
    assert all(r.cloned_from == "" for r in db.all_bundles())
    # The NAME registry survives, so a re-split reuses the same name.
    out = _run_cmd(s, "run_planner hier")
    assert "'proc_cell90'" in out
    rows = {r.id: r for r in db.all_bundles()}
    for r in rows.values():
        if r.cloned_from:
            assert rows[r.cloned_from].cell_context == "proc_cell"
            assert r.cell_context == "proc_cell90"


# ── Dogleg-split templates are copied (opens.md conditional) ─────────────────

def _dogleg_cell_flow(path=":memory:"):
    """flow/nuts_dogleg_cycle.buda's cyclic-constraint geometry transplanted
    INSIDE a cell, instantiated twice: two buses crossing the same horizontal
    channel with the above/below assignment swapped between the columns — the
    cell-LOCAL solve must dogleg one trunk.  Both templates are pinned to the
    same Z_VHV candidates the flat repro forces (identical cell-local
    geometry → identical candidate ordering)."""
    db = buda.BDB(path)
    db.add_cell("dl_cell", 300, 200)
    db.add_cell("blk_cell", 20, 50)
    for name, (x, y) in [("A_top", (80, 150)), ("B_bot", (80, 0)),
                         ("A_bot", (190, 0)), ("B_top", (190, 150))]:
        db.add_inst_to_cell("dl_cell", name, "blk_cell", x, y)
    db.add_inst("D1", "dl_cell", "", 0, 0)
    # Offsets are multiples of BOTH pattern pitches (M4: 34, M5: 32) so the
    # instances share one track phase (x mod 32 == 0, y mod 34 == 0).
    db.add_inst("D2", "dl_cell", "", 640, 340)
    for k in (1, 2):
        for i in range(4):
            db.add_net_pins(f"x{k}_{i}", f"D{k}/A_top.o", [f"D{k}/A_bot.i"])
            db.add_net_pins(f"y{k}_{i}", f"D{k}/B_bot.o", [f"D{k}/B_top.i"])
    buda.BustermGen(db).derive(1)
    s = _bare_session(db)
    for c in (["def_layer 4 M4 H TOP 52.94", "def_layer 5 M5 V TOP 50.00"]
              + ["def_track_pattern 4 -400 POWER 4 1 SIGNAL 2 1 SIGNAL 2 1 "
                 "SIGNAL 2 1 SIGNAL 2 1 GROUND 4 1 SIGNAL 2 1 SIGNAL 2 1 "
                 "SIGNAL 2 1 SIGNAL 2 1",
                 "def_track_pattern 5 0 POWER 3 1 SIGNAL 2 1 SIGNAL 2 1 "
                 "SIGNAL 2 1 SIGNAL 2 1 GROUND 3 1 SIGNAL 2 1 SIGNAL 2 1 "
                 "SIGNAL 2 1 SIGNAL 2 1",
                 "corner_margin dy 2 dx 2", "set_min_stub_length 2",
                 "run_hier_bundler", "generate_hier_topologies",
                 "set_bottom_up dl_cell"]):
        _run_cmd(s, c)
    # Pin both templates to the flat repro's cyclic Z_VHV pair (candidate 4).
    templates = [w for w in s.bundles
                 for b in [w.input.original_bundle]
                 if b.cell_context and b.parent_id < 0]
    assert len(templates) == 2
    for w in templates:
        assert len(w.input.candidates) >= 4
        assert "Z" in w.input.candidates[3].type, \
            w.input.candidates[3].type
        w.input.topology_pinned = True
        w.plan.selected_topology_index = 3
    return s


def test_doglegged_template_is_copied_not_abandoned():
    """A cell whose LOCAL solve needs a dogleg no longer falls back to
    per-instance NUTS: the split is adopted on the template and every
    locked instance, the copies stay uniform (D2 = exact translate of D1,
    jog included), and the strict DNUTS copy path completes."""
    s = _dogleg_cell_flow()
    out = _run_cmd(s, "run_planner hier")
    out = _run_cmd(s, "run_nuts")
    assert "split candidate adopted" in out, out
    assert "falling back" not in out
    assert getattr(s, "_bu_dogleg_slot", None), "template slot bookkeeping"
    # Every instance stayed locked, selection points at the adopted split,
    # and the plan arrays cover the extra jog segment.
    inst_ws = [w for w in s.bundles
               for b in [w.input.original_bundle]
               if b.cell_context and w.hier.locked]
    assert len(inst_ws) == 4          # 2 templates x 2 instances
    fixed = s._bottom_up_fixed_segments()
    n_dogleg = 0
    for w in inst_ws:
        b = w.input.original_bundle
        sel = w.input.candidates[w.plan.selected_topology_index]
        segs = [ts for ts in fixed if ts.bundle_id == b.id]
        assert len(segs) == len(sel.segments) == len(w.plan.seg_layers)
        if len(sel.segments) > 3:     # Z(3) + split piece + jog
            n_dogleg += 1
    assert n_dogleg >= 2, "the split must reach BOTH instances of the cell"
    # Uniformity: per template, D2's fixed segments are exact translates
    # of D1's (paired by seg_idx WITHIN the template — seg indices collide
    # across the two bus templates).
    for _cell, _tid, iws in s._bottom_up_instance_groups():
        bid_of = {iw.input.original_bundle.instances[0]:
                  iw.input.original_bundle.id for iw in iws}
        segs = {inst: sorted((ts for ts in fixed if ts.bundle_id == bid),
                             key=lambda t: t.seg_idx)
                for inst, bid in bid_of.items()}
        assert len(segs["D1"]) == len(segs["D2"]) > 0
        for a, b2 in zip(segs["D1"], segs["D2"]):
            da, dp = (640, 340) if a.horiz else (340, 640)
            assert b2.span_lo == pytest.approx(a.span_lo + da)
            assert b2.span_hi == pytest.approx(a.span_hi + da)
            assert b2.track_position == pytest.approx(a.track_position + dp)
    assert s.nuts_result.num_overlaps == 0
    out = _run_cmd(s, "check_template_tracks")
    assert "MISALIGNED" not in out
    out = _run_cmd(s, "run_detailed_nuts")   # strict default policy
    assert "[BottomUp] DNUTS:" in out and "Error" not in out
    assert s.detailed_result.num_unplaced == 0


def test_doglegged_template_replan_resets_and_readopts():
    """A re-plan drops the adopted template split (pristine pool, original
    selection) and the next run_nuts re-adopts it — the slot-overwrite
    semantics keep the pool from growing across cycles."""
    s = _dogleg_cell_flow()
    _run_cmd(s, "run_planner hier")
    _run_cmd(s, "run_nuts")
    tpl = [w for w in (s._hier_bundles_orig or [])
           for b in [w.input.original_bundle]
           if b.cell_context and b.parent_id < 0]
    sizes1 = {w.input.original_bundle.id: len(w.input.candidates)
              for w in tpl}
    _run_cmd(s, "run_planner hier")
    _run_cmd(s, "run_nuts")
    sizes2 = {w.input.original_bundle.id: len(w.input.candidates)
              for w in tpl}
    assert sizes1 == sizes2, "template pool grew across re-plan cycles"
    assert s.nuts_result.num_overlaps == 0


def test_doglegged_template_resume_round_trip(tmp_path):
    """Codex #256 P2: the adopted split is PERSISTED (template row with
    source='dogleg' + per-instance selected topologies), so a checkpoint
    taken after run_nuts restores wrappers whose segment indices match the
    persisted bus rows — the resumed strict DNUTS routes the split
    correctly instead of mis-deriving connections for the jog segments."""
    p = str(tmp_path / "dl.bdb")
    s1 = _dogleg_cell_flow(path=p)
    _run_cmd(s1, "run_planner hier")
    out = _run_cmd(s1, "run_nuts")
    assert "split candidate adopted" in out
    n_fixed1 = len(s1._bottom_up_fixed_segments())
    locked1 = {w.input.original_bundle.id for w in s1.bundles
               if w.hier.locked}
    del s1

    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    for c in (["def_layer 4 M4 H TOP 52.94", "def_layer 5 M5 V TOP 50.00"]
              + ["def_track_pattern 4 -400 POWER 4 1 SIGNAL 2 1 SIGNAL 2 1 "
                 "SIGNAL 2 1 SIGNAL 2 1 GROUND 4 1 SIGNAL 2 1 SIGNAL 2 1 "
                 "SIGNAL 2 1 SIGNAL 2 1",
                 "def_track_pattern 5 0 POWER 3 1 SIGNAL 2 1 SIGNAL 2 1 "
                 "SIGNAL 2 1 SIGNAL 2 1 GROUND 3 1 SIGNAL 2 1 SIGNAL 2 1 "
                 "SIGNAL 2 1 SIGNAL 2 1",
                 f"open_bdb {p}", "add_blocks_from_bdb 0",
                 "add_blocks_from_bdb 1 skip"]):
        _run_cmd(s2, c)
    out = _run_cmd(s2, "load_pipeline expanded")
    assert "rehydrated" in out
    # Every locked wrapper's restored selected candidate carries the SPLIT
    # segment count (matching the persisted bus rows), with full layers.
    locked2 = {w.input.original_bundle.id for w in s2.bundles
               if w.hier.locked}
    assert locked2 == locked1
    for w in s2.bundles:
        if not w.hier.locked:
            continue
        sel = w.input.candidates[w.plan.selected_topology_index]
        n_bus = sum(1 for ts in s2.nuts_result.segments
                    if ts.bundle_id == w.input.original_bundle.id)
        assert len(sel.segments) == n_bus == len(w.plan.seg_layers)
    assert len(s2._bottom_up_fixed_segments()) == n_fixed1
    out = _run_cmd(s2, "run_detailed_nuts")
    assert "[BottomUp] DNUTS:" in out and "Error" not in out
    assert s2.detailed_result.num_unplaced == 0
