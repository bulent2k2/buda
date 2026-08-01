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

"""`set_layer_caps_by_depth` — bulk per-cell bands from the hierarchy
(docs/internal/hier_layer_caps.md §13 Phase 5 Q1).

Levels are INTRINSIC and BOTTOM-ANCHORED: a childless non-container cell is
level 1, a childless CONTAINER is level 2 (one level of reserved headroom),
and any other cell is 1 + max over its child cells' levels.  Levels past the
argument list are unrestricted; an explicit `set_cell_layer_cap` always
outranks the by-depth default.
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


def _session(db):
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = db
    _quiet(s,
           "def_layer 2 M2 H LOW 50", "def_layer 3 M3 V LOW 50",
           "def_layer 4 M4 H LOW 50", "def_layer 5 M5 V LOW 50",
           "def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50")
    return s


def _three_level_db():
    """top -> mid -> leaf, plus a childless CONTAINER cell instantiated at
    the top (the empty-envelope case)."""
    db = buda.BDB(":memory:")
    db.add_cell("leaf_cell", 100, 60)
    db.add_cell("mid_cell", 400, 200)
    db.add_cell("top_cell", 1200, 800)
    db.add_cell("empty_env", 200, 100)
    db.add_inst_to_cell("mid_cell", "l0", "leaf_cell", 20, 20)
    db.add_inst_to_cell("mid_cell", "l1", "leaf_cell", 200, 20)
    db.add_inst_to_cell("top_cell", "m0", "mid_cell", 0, 0)
    db.add_inst_to_cell("top_cell", "m1", "mid_cell", 600, 0)
    db.add_inst_to_cell("top_cell", "e0", "empty_env", 0, 500)
    db.add_inst("t", "top_cell", "", 0, 0)
    # An empty envelope is a container by declaration, not by content.
    db.set_comp_is_leaf("t/e0", False)
    return db


# ── level computation ────────────────────────────────────────────────────────

def test_levels_are_bottom_anchored():
    s = _session(_three_level_db())
    levels, containers = s._cell_levels()
    assert levels["leaf_cell"] == 1          # childless, not a container
    assert levels["empty_env"] == 2          # childless CONTAINER: headroom
    assert levels["mid_cell"] == 2           # 1 + max(leaf)
    # top holds mid (2) and the empty envelope (2) -> 1 + max = 3
    assert levels["top_cell"] == 3
    assert "empty_env" in containers and "leaf_cell" not in containers


def test_level_is_the_tallest_subtree_not_the_shallowest():
    """A cell holding both a bare leaf and a deep subtree levels by the
    DEEPEST child — it is never capped below what its content needs."""
    db = buda.BDB(":memory:")
    db.add_cell("leaf_cell", 100, 60)
    db.add_cell("mid_cell", 400, 200)
    db.add_cell("mixed_cell", 900, 400)
    db.add_inst_to_cell("mid_cell", "l0", "leaf_cell", 20, 20)
    db.add_inst_to_cell("mixed_cell", "m0", "mid_cell", 0, 0)
    db.add_inst_to_cell("mixed_cell", "l9", "leaf_cell", 500, 0)  # shallow
    db.add_inst("t", "mixed_cell", "", 0, 0)
    s = _session(db)
    levels, _c = s._cell_levels()
    assert levels["mid_cell"] == 2
    assert levels["mixed_cell"] == 3         # not 2 (the shallow leaf)


def test_level_is_intrinsic_across_instantiation_depths():
    """Q1b: the same cell instantiated at two different hierarchy DEPTHS has
    ONE level — the ambiguity the question anticipated does not arise."""
    db = buda.BDB(":memory:")
    db.add_cell("leaf_cell", 100, 60)
    db.add_cell("mid_cell", 400, 200)
    db.add_cell("top_cell", 1600, 800)
    db.add_inst_to_cell("mid_cell", "l0", "leaf_cell", 20, 20)
    db.add_inst_to_cell("top_cell", "m0", "mid_cell", 0, 0)
    db.add_inst_to_cell("top_cell", "l_direct", "leaf_cell", 900, 0)
    db.add_inst("t", "top_cell", "", 0, 0)
    s = _session(db)
    levels, _c = s._cell_levels()
    assert levels["leaf_cell"] == 1          # depth 2 AND depth 1 instances
    assert levels["top_cell"] == 3


def test_levels_from_elaborated_components_without_cell_children():
    """import_verilog elaborates straight into component rows (no
    cell_children edges) — the component tree must level identically."""
    db = buda.BDB(":memory:")
    db.add_comp("t", "top_cell", "", 0, 0, 1000, 800)
    db.add_comp("t/m0", "mid_cell", "t", 0, 0, 400, 200)
    db.add_comp("t/m0/l0", "leaf_cell", "t/m0", 10, 10, 110, 70)
    s = _session(db)
    levels, _c = s._cell_levels()
    assert levels["leaf_cell"] == 1
    assert levels["mid_cell"] == 2
    assert levels["top_cell"] == 3


# ── the command ──────────────────────────────────────────────────────────────

def test_by_depth_assigns_cumulative_bands():
    s = _session(_three_level_db())
    out = _cmd(s, "set_layer_caps_by_depth M3 M5")
    assert s._cell_layer_policy["leaf_cell"] == (-1, 3)
    assert s._cell_layer_policy["mid_cell"] == (-1, 5)
    assert s._cell_layer_policy["empty_env"] == (-1, 5)   # container headroom
    assert "top_cell" not in s._cell_layer_policy         # level 3: free
    assert "level 1 -> band [lowest..M3]" in out
    assert "level 2 -> band [lowest..M5]" in out
    assert "level 3+ unrestricted" in out


def test_by_depth_persists_through_the_bdb():
    s = _session(_three_level_db())
    _quiet(s, "set_layer_caps_by_depth M3 M5")
    assert s.bdb.cell_layer_band("leaf_cell") == (-1, 3)
    assert s.bdb.cell_layer_band("mid_cell") == (-1, 5)
    assert s.bdb.cell_layer_band("top_cell") == (-1, -1)


def test_by_depth_honors_a_floor():
    s = _session(_three_level_db())
    _quiet(s, "set_layer_caps_by_depth M3 M5 -min M2")
    assert s._cell_layer_policy["leaf_cell"] == (2, 3)
    assert s._cell_layer_policy["mid_cell"] == (2, 5)


def test_explicit_cap_outranks_the_by_depth_default():
    s = _session(_three_level_db())
    _quiet(s, "set_cell_layer_cap leaf_cell M7")
    out = _cmd(s, "set_layer_caps_by_depth M3 M5")
    assert s._cell_layer_policy["leaf_cell"] == (-1, 7)   # untouched
    assert s._cell_layer_policy["mid_cell"] == (-1, 5)
    assert "kept 1 explicit policy" in out and "leaf_cell" in out


def test_explicit_cap_after_by_depth_wins_and_sticks():
    """A later explicit cap re-types the entry, so a re-run of the by-depth
    command leaves it alone."""
    s = _session(_three_level_db())
    _quiet(s, "set_layer_caps_by_depth M3 M5",
              "set_cell_layer_cap leaf_cell M7")
    out = _cmd(s, "set_layer_caps_by_depth M3 M5")
    assert s._cell_layer_policy["leaf_cell"] == (-1, 7)
    assert "kept 1 explicit policy" in out


def test_by_depth_rerun_updates_its_own_entries():
    s = _session(_three_level_db())
    _quiet(s, "set_layer_caps_by_depth M3 M5")
    _quiet(s, "set_layer_caps_by_depth M5 M7")
    assert s._cell_layer_policy["leaf_cell"] == (-1, 5)
    assert s._cell_layer_policy["mid_cell"] == (-1, 7)


def test_off_clears_only_by_depth_entries():
    s = _session(_three_level_db())
    _quiet(s, "set_cell_layer_cap top_cell M7", "set_layer_caps_by_depth M3 M5")
    out = _cmd(s, "set_layer_caps_by_depth off")
    assert "leaf_cell" not in s._cell_layer_policy
    assert "mid_cell" not in s._cell_layer_policy
    assert s._cell_layer_policy["top_cell"] == (-1, 7)    # explicit survives
    assert s.bdb.cell_layer_band("leaf_cell") == (-1, -1)
    assert "cleared 3 by-depth policies" in out   # leaf, mid, empty_env


def test_short_list_leaves_the_top_unrestricted():
    s = _session(_three_level_db())
    _quiet(s, "set_layer_caps_by_depth M3")
    assert s._cell_layer_policy["leaf_cell"] == (-1, 3)
    assert "mid_cell" not in s._cell_layer_policy
    assert "top_cell" not in s._cell_layer_policy


# ── LOUD validation ──────────────────────────────────────────────────────────

def test_unknown_layer_is_a_hard_error():
    s = _session(_three_level_db())
    out = _cmd(s, "set_layer_caps_by_depth M3 M9")
    assert "Error" in out and "unknown layer 'M9'" in out
    assert not s._cell_layer_policy


def test_unroutable_band_is_a_hard_error():
    s = _session(_three_level_db())
    out = _cmd(s, "set_layer_caps_by_depth M2 M5")   # level 1: H only
    assert "Error" in out and "grants no V routing layer" in out
    assert not s._cell_layer_policy


def test_floor_above_a_cap_is_a_hard_error():
    s = _session(_three_level_db())
    out = _cmd(s, "set_layer_caps_by_depth M3 M5 -min M4")
    assert "Error" in out and "below the floor" in out
    assert not s._cell_layer_policy


def test_decreasing_caps_are_noted_not_refused():
    s = _session(_three_level_db())
    out = _cmd(s, "set_layer_caps_by_depth M5 M3")
    assert "not non-decreasing" in out
    assert s._cell_layer_policy["leaf_cell"] == (-1, 5)


# ── Codex #551 round: shortened list, provenance across a reload ────────────

def test_shorter_list_frees_the_levels_it_no_longer_names():
    """Codex #551: re-running with a SHORTER cap list reported the dropped
    levels as unrestricted while their stale band survived in the session
    and in the BDB — the report and the policy disagreed."""
    s = _session(_three_level_db())
    _quiet(s, "set_layer_caps_by_depth M3 M5 M7")
    assert s._cell_layer_policy["top_cell"] == (-1, 7)
    out = _cmd(s, "set_layer_caps_by_depth M3")
    assert "top_cell" not in s._cell_layer_policy      # session cleared
    assert "mid_cell" not in s._cell_layer_policy
    assert s.bdb.cell_layer_band("top_cell") == (-1, -1)   # BDB cleared
    assert s.bdb.cell_layer_band("mid_cell") == (-1, -1)
    assert "freed by the shorter list" in out
    assert s._cell_layer_policy["leaf_cell"] == (-1, 3)    # still capped


def test_shorter_list_does_not_free_explicit_entries():
    s = _session(_three_level_db())
    _quiet(s, "set_layer_caps_by_depth M3 M5 M7",
              "set_cell_layer_cap top_cell M5")
    _quiet(s, "set_layer_caps_by_depth M3")
    assert s._cell_layer_policy["top_cell"] == (-1, 5)     # untouched
    assert s.bdb.cell_layer_band("top_cell") == (-1, 5)


def _reopen(path):
    """Fresh session over the same on-disk BDB — the reload path."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s,
           "def_layer 2 M2 H LOW 50", "def_layer 3 M3 V LOW 50",
           "def_layer 4 M4 H LOW 50", "def_layer 5 M5 V LOW 50",
           "def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
           f"open_bdb {path}")
    return s


def test_explicit_cap_survives_a_reload_and_still_outranks(tmp_path):
    """Codex #551: after a reload every persisted band looked 'restored', so
    a bulk declaration overwrote a persisted EXPLICIT cap — the advertised
    precedence held only within one session."""
    path = str(tmp_path / "lv.bdb")
    s = _session(_three_level_db())
    _quiet(s, "set_layer_caps_by_depth M3 M5", "set_cell_layer_cap leaf_cell M7",
              f"save_bdb {path}")
    s2 = _reopen(path)
    assert s2._cell_layer_policy["leaf_cell"] == (-1, 7)
    _quiet(s2, "set_layer_caps_by_depth M3 M5")
    assert s2._cell_layer_policy["leaf_cell"] == (-1, 7)   # explicit wins
    assert s2._cell_layer_policy["mid_cell"] == (-1, 5)    # by-depth reapplied


def test_off_clears_persisted_by_depth_bands_after_a_reload(tmp_path):
    """The same provenance gap made `off` a no-op after a reload: with an
    empty by_depth set it could not identify the bands it had written."""
    path = str(tmp_path / "lv2.bdb")
    s = _session(_three_level_db())
    _quiet(s, "set_layer_caps_by_depth M3 M5", "set_cell_layer_cap top_cell M7",
              f"save_bdb {path}")
    s2 = _reopen(path)
    out = _cmd(s2, "set_layer_caps_by_depth off")
    assert "leaf_cell" not in s2._cell_layer_policy
    assert "mid_cell" not in s2._cell_layer_policy
    assert s2.bdb.cell_layer_band("leaf_cell") == (-1, -1)
    assert s2._cell_layer_policy["top_cell"] == (-1, 7)    # explicit survives
    assert "cleared 3 by-depth policies" in out


def test_by_depth_memo_does_not_leak_across_bdbs(tmp_path):
    """Switching BDBs must not carry the previous design's by-depth typing:
    the new BDB's memo governs its own restored bands."""
    p1, p2 = str(tmp_path / "a.bdb"), str(tmp_path / "b.bdb")
    s = _session(_three_level_db())
    _quiet(s, "set_layer_caps_by_depth M3 M5", f"save_bdb {p1}")
    s2 = _session(_three_level_db())
    _quiet(s2, "set_cell_layer_cap leaf_cell M7", f"save_bdb {p2}")
    s3 = _reopen(p1)
    assert s3._cell_layer_policy_by_depth               # a's bands are bulk
    _quiet(s3, f"open_bdb {p2}")
    assert s3._cell_layer_policy["leaf_cell"] == (-1, 7)
    assert "leaf_cell" not in s3._cell_layer_policy_by_depth   # b's is explicit


def test_requires_an_open_bdb():
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = _cmd(s, "set_layer_caps_by_depth M3")
    assert "Error" in out and "open BDB" in out


# ── it drives the real policy path ───────────────────────────────────────────

def test_by_depth_bands_mask_the_planner():
    """End-to-end: the by-depth bands resolve onto hier wrappers exactly like
    hand-declared ones — the leaf cell's own interconnect stays in-band."""
    db = buda.BDB(":memory:")
    db.add_cell("leaf_cell", 420, 200)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("leaf_cell", "pa_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("leaf_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst("l_i1", "leaf_cell", "", 0, 0)
    db.add_inst("l_i2", "leaf_cell", "", 520, 0)
    for i in range(4):
        db.add_net_pins(f"ab1_{i}", "l_i1/pa_i.out", ["l_i1/pb_i.in"])
        db.add_net_pins(f"ab2_{i}", "l_i2/pa_i.out", ["l_i2/pb_i.in"])
    buda.BustermGen(db).derive(1)
    s = _session(db)
    levels, _c = s._cell_levels()
    assert levels["pipe_cell"] == 1 and levels["leaf_cell"] == 2
    _quiet(s, "set_layer_caps_by_depth M3 M5",
              "run_hier_bundler", "generate_hier_topologies",
              "run_planner hier")
    governed = [w for w in s.bundles if w.input.allowed_layers]
    assert governed
    for w in governed:
        assert w.input.layer_cap == 5        # leaf_cell's own bundles
        for l in w.plan.seg_layers:
            assert l <= 5
