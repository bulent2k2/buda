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

def _two_inst_db():
    """proc_cell (two pipe_cell children) instantiated twice, with 4-bit
    cell-local buses in each instance — the template-sharing vehicle."""
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
    assert issues and "child placement differs" in issues[0]


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
