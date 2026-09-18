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

"""The positional track reservation (convergence ladder item 6):
`set_cell_layer_reserve` and `derive_cell_layer_reserves`.

E1 refuted the fractional share as the rung-4 primitive because a share is
UNIFORM (the first slots of every period) while the top's demand is
POSITIONAL, and a block whose own bus fills its seat cannot give up a
fraction of every period.  A reservation names the tracks instead.  What
is pinned here:

  * the declaration validates loudly and persists (meta `layer_reserves`),
    restored by open_bdb under the share contract (typed entries win);
  * the derivation is the union over the cell's instances of the top's
    tracks, in the CELL's frame, and reports (not refuses) the reserved
    tracks inside the cell's own worst seat and under its own metal;
  * ENFORCEMENT: solved as a template under the derived lines, the cell's
    own bus moves OFF the reserved tracks, the top uses them, nothing
    strands — the shape the uniform share could not express;
  * a reserved cell planned top-down is not enforced and says so
    (BUDA-1920); the LAYER_RESERVE audit reports the violation.
"""
import contextlib
import io
import re

import pytest
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

from test_layer_demand import _DESIGN, _cmd, _quiet  # noqa: E402
from buda_session.util import fmt_pos  # noqa: E402

_TRACKS = Path(__file__).parents[2] / "flow" / "tracks" / "tracks.buda"

_LINE = "set_cell_layer_reserve top_cell M6 83,86,89,92,100,103,106,109"


def _session(*extra):
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, *_DESIGN, *extra)
    return s


def _template_session(*policy, bdb=":memory:"):
    """The E1 recipe's second session: the cell solved as a template under
    `policy` lines declared before bundling."""
    i = _DESIGN.index("run_hier_bundler depth 1")
    design = [f"open_bdb {bdb}" if c == "open_bdb :memory:" else c
              for c in _DESIGN]
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, *design[:i], "set_bottom_up top_cell", *policy, *design[i:],
           "run_nuts", "check_template_tracks on_mismatch independent",
           "run_detailed_nuts")
    return s


def test_the_declaration_validates_and_replaces():
    s = _session()
    out = _cmd(s, "set_cell_layer_reserve top_cell M6 3,7.5,12")
    assert "reserves 3 track(s) at y = 3, 7.5, 12" in out, out
    assert s._cell_layer_reserves[("top_cell", 6)] == (3.0, 7.5, 12.0)
    # re-declaring REPLACES the layer's list
    _cmd(s, "set_cell_layer_reserve top_cell M6 20")
    assert s._cell_layer_reserves[("top_cell", 6)] == (20.0,)
    assert "unknown layer" in _cmd(s, "set_cell_layer_reserve top_cell M9 1")
    assert "unknown cell" in _cmd(s, "set_cell_layer_reserve nosuch M6 1")
    assert "outside the cell's height (200)" in \
        _cmd(s, "set_cell_layer_reserve top_cell M6 999")
    assert "outside the cell's width (600)" in \
        _cmd(s, "set_cell_layer_reserve top_cell M5 601")
    assert "repeated" in _cmd(s, "set_cell_layer_reserve top_cell M6 4,4")
    assert "not a number" in _cmd(s, "set_cell_layer_reserve top_cell M6 4,x")
    assert "usage" in _cmd(s, "set_cell_layer_reserve top_cell M6")
    assert "removed" in _cmd(s, "set_cell_layer_reserve top_cell M6 off")
    assert ("top_cell", 6) not in s._cell_layer_reserves
    _cmd(s, "set_cell_layer_reserve top_cell M6 1")
    _cmd(s, "set_cell_layer_reserve top_cell M5 2")
    assert "cleared 2" in _cmd(s, "set_cell_layer_reserve * off")
    assert not s._cell_layer_reserves
    # no pattern on the layer: a reservation names tracks
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    _quiet(s2, "open_bdb :memory:", "def_layer 8 M8 H TOP 20",
           "add_cell top_cell 600 200", "add_inst u1 top_cell - 0 0")
    assert "no track pattern" in _cmd(s2, "set_cell_layer_reserve top_cell M8 1")
    # a non-finite position parses as a float and passes every comparison
    # (Codex P2 on #936): refused at the declaration, not at the solve
    s3 = _session()
    for tok in ("nan", "inf", "-inf", "3,nan"):
        assert "not a finite number" in _cmd(s3, f"set_cell_layer_reserve top_cell M6 {tok}"), tok
    assert ("top_cell", 6) not in s3._cell_layer_reserves


def test_a_rail_only_pattern_is_refused():
    """A pattern with slots and no SIGNAL slot names no track (Codex P2 on
    #936): refused at the declaration, as the share command already does,
    instead of reserving a corridor of a made-up width."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, "open_bdb :memory:", "def_layer 8 M8 H TOP 20",
           "def_track_pattern 8 0 VDD 2 1 GND 2 1",
           "add_cell top_cell 600 200", "add_inst u1 top_cell - 0 0")
    out = _cmd(s, "set_cell_layer_reserve top_cell M8 1")
    assert "no SIGNAL slots" in out, out
    assert ("top_cell", 8) not in s._cell_layer_reserves


def test_a_position_typed_before_the_bdb_is_revalidated_at_open(tmp_path):
    """With no BDB open the cell's extent is unknown, so the declaration
    can only check the sign (Codex P2 on #936).  The moment a BDB is
    opened every held entry is checked against the cell it names: an
    out-of-cell position is dropped LOUD, the rest kept — and a restored
    entry written against a cell another session has since resized gets
    the same treatment."""
    bdb = tmp_path / "r.bdb"
    _cmd(_session(), f"save_bdb {bdb}")
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, f"source {_TRACKS}")
    assert s.bdb is None
    out = _cmd(s, "set_cell_layer_reserve top_cell M6 3,1e12,999")
    assert "reserves 3 track(s)" in out, out
    out = _cmd(s, f"open_bdb {bdb}")
    assert ("dropped 2 reserved position(s) outside the cell's height (200): "
            "999, 1000000000000") in out, out
    assert s._cell_layer_reserves[("top_cell", 6)] == (3.0,)
    # an entry left with nothing is removed, and the persisted form follows
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, f"source {_TRACKS}", "set_cell_layer_reserve top_cell M5 1e9")
    out = _cmd(s, f"open_bdb {bdb}")
    assert "the reservation is removed" in out, out
    assert ("top_cell", 5) not in s._cell_layer_reserves
    # a RESTORED entry against a since-resized cell: the row says 150 is
    # inside; the cell is 100 tall now (the row planted by hand — an
    # in-session resize drops the position at once, Codex round 7)
    import json
    _quiet(s, "resize_cell top_cell 600 100")
    s.bdb.meta_set("layer_reserves", json.dumps({"top_cell": {"6": [50, 150]}}))
    _cmd(s, f"save_bdb {bdb}")
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    _quiet(s2, f"source {_TRACKS}")
    out = _cmd(s2, f"open_bdb {bdb}")
    assert "restored 1 persisted reservation(s)" in out, out
    assert "dropped 1 reserved position(s) outside the cell's height (100): 150" in out, out
    assert s2._cell_layer_reserves[("top_cell", 6)] == (50.0,)
    # the rect builder applies the same rule (the layer declared AFTER the
    # open is the one shape open_bdb cannot check), and a position that
    # would overflow a keepout call is skipped rather than raised
    s3 = buda_cli.BudaSession()
    s3.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s3.do_command(f"open_bdb {bdb}")
    _quiet(s3, f"source {_TRACKS}")
    s3._cell_layer_reserves[("top_cell", 6)] = (50.0, 1e30)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rects = s3._reserve_rects(6, (50.0, 1e30), 0, 0, 600, 100)
    assert len(rects) == 1, rects
    assert "outside the cell's height (100) and are skipped: 1e+30" in buf.getvalue(), buf.getvalue()


def test_a_cell_the_opened_bdb_does_not_know_is_dropped(tmp_path):
    """The declaration refuses an unknown cell while a BDB is open, so an
    entry naming one can only have been typed with no BDB (a typo, or a
    name from another design) — and it used to survive the open silently,
    persisted and answering `query reserves` with nothing able to enforce
    it (Codex P2 on #936).  Dropped LOUD at the open; a restored row
    naming a cell the file's design lacks goes the same way."""
    bdb = tmp_path / "r.bdb"
    _cmd(_session(), f"save_bdb {bdb}")
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, f"source {_TRACKS}", "set_cell_layer_reserve top_cel M6 3",
           "set_cell_layer_reserve top_cell M6 7")
    out = _cmd(s, f"open_bdb {bdb}")
    assert ("cell 'top_cel' is not in the opened BDB — its M6 reservation "
            "(1 track(s)) is removed") in out, out
    assert ("top_cel", 6) not in s._cell_layer_reserves
    assert s._cell_layer_reserves[("top_cell", 6)] == (7.0,)
    import json
    assert set(json.loads(s.bdb.meta_get("layer_reserves", ""))) == {"top_cell"}
    # a restored row for a cell the design lacks (a hand-edited file)
    s.bdb.meta_set("layer_reserves", json.dumps({"ghost": {"6": [1.0]},
                                                 "top_cell": {"6": [7.0]}}))
    _cmd(s, f"save_bdb {bdb}")
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    _quiet(s2, f"source {_TRACKS}")
    out = _cmd(s2, f"open_bdb {bdb}")
    assert "restored 2 persisted reservation(s)" in out, out
    assert "cell 'ghost' is not in the opened BDB" in out, out
    assert ("ghost", 6) not in s2._cell_layer_reserves
    assert s2._cell_layer_reserves[("top_cell", 6)] == (7.0,)


def test_a_reservation_typed_before_the_open_is_persisted_at_the_open(tmp_path):
    """Typed with no BDB open, a reservation had no home: the declaration
    persists into the BDB open at the time, and there was none, so the
    session enforced what the next one never saw (Codex P2 on #936).  The
    open writes the validated map — and under typed-wins, what the file
    then holds is the typed value, not the older row it outranked."""
    bdb = tmp_path / "r.bdb"
    s0 = _session()
    _quiet(s0, "set_cell_layer_reserve top_cell M6 1", f"save_bdb {bdb}")
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, f"source {_TRACKS}", "set_cell_layer_reserve top_cell M6 3,7.5",
           "set_cell_layer_reserve top_cell M5 40")
    out = _cmd(s, f"open_bdb {bdb}")
    assert "restored" not in out, out          # the typed M6 outranks the row
    assert s._cell_layer_reserves[("top_cell", 6)] == (3.0, 7.5)
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    out = _cmd(s2, f"open_bdb {bdb}")
    assert "restored 2 persisted reservation(s)" in out, out
    assert s2._cell_layer_reserves[("top_cell", 6)] == (3.0, 7.5)
    assert s2._cell_layer_reserves[("top_cell", 5)] == (40.0,)


def test_a_malformed_persisted_row_is_skipped_loud(tmp_path):
    """The declaration refuses a non-finite position, but the file can
    carry anything JSON spells — `"bad"` raised in float(), and a NaN
    (Python's decoder accepts it) passed every comparison to fail in
    math.floor at the keepout install (Codex P2 on #936).  Each malformed
    entry is skipped and named; the well-formed ones restore."""
    import json
    bdb = tmp_path / "r.bdb"
    s = _session()
    s.bdb.meta_set("layer_reserves", json.dumps({
        "top_cell": {"6": [3, "bad"], "5": [float("nan")], "x": [1],
                     "7": "notalist", "3": [1.5, 2]},
        "leaf": 7,
    }))
    _cmd(s, f"save_bdb {bdb}")
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    _quiet(s2, f"source {_TRACKS}")
    out = _cmd(s2, f"open_bdb {bdb}")
    assert "layer 6 holds position 'bad', not a finite" in out, out
    assert "layer 5 holds position nan, not a finite" in out, out
    assert "names layer 'x', not an id" in out, out
    assert "layer 7 is not a list" in out, out
    assert "for cell 'leaf' is not a {layer: [pos]} object" in out, out
    assert "restored 1 persisted reservation(s): top_cell:M3x2" in out, out
    assert s2._cell_layer_reserves == {("top_cell", 3): (1.5, 2.0)}
    # a row that is not an object at all
    s2.bdb.meta_set("layer_reserves", "[1, 2]")
    _cmd(s2, f"save_bdb {bdb}")
    s3 = buda_cli.BudaSession()
    s3.no_viz = True
    out = _cmd(s3, f"open_bdb {bdb}")
    assert "row is not a {cell: {layer: [pos]}} object" in out, out
    assert not getattr(s3, "_cell_layer_reserves", None)


def test_own_metal_is_read_with_the_full_ndr_footprint():
    """`own_tracks` recorded each own bit's CENTRE track, while the foreign
    demand counted a wire's WIDTH and an NDR run's guard slots — so a
    widened own wire, or its guard run, sat on a reserved track with
    `own_hit` reading zero (Codex P2 on #936).  Both halves read one
    footprint rule now: a x2-wide 8-bit bus covers 16 tracks, a
    guard-spaced one 17 (8 bits, 7 gaps, the two run ends), the plain bus
    8 — and the foreign `used` count is untouched by any of it."""
    i = _DESIGN.index("run_hier_bundler depth 1")
    got = {}
    for rule in (None, "def_ndr wide width x2", "def_ndr wide spacing x2"):
        s = buda_cli.BudaSession()
        s.no_viz = True
        pre = [rule, "set_ndr loc wide"] if rule else []
        _quiet(s, *_DESIGN[:i], *pre, *_DESIGN[i:], "run_nuts",
               "run_detailed_nuts")
        assert s.detailed_result.num_unplaced == 0
        rows = {r["inst"]: r for r in s._layer_demand()
                if r["layer_name"] == "M6"}
        got[rule] = (len(rows["u1"]["own_tracks"]), rows["u1"]["used"],
                     len(rows["u2"]["own_tracks"]), rows["u2"]["used"])
    assert got[None] == (8, 8, 8, 8), got
    assert got["def_ndr wide width x2"] == (16, 8, 16, 8), got
    assert got["def_ndr wide spacing x2"] == (17, 8, 17, 8), got


def test_a_pre_open_off_is_a_tombstone_the_restore_honours(tmp_path):
    """A generated policy's `... off` line sourced BEFORE the open removed
    an entry from an empty map and recorded nothing, so the open restored
    the very entry it had removed (Codex P2 on #936).  A typed `off` is
    now a tombstone the restore honours — said, and the file follows —
    `* off` holds every persisted entry off, and a later positive
    declaration of the same key wins over its own tombstone."""
    import json
    bdb = tmp_path / "r.bdb"
    s0 = _session()
    _quiet(s0, "set_cell_layer_reserve top_cell M6 3,7.5",
           "set_cell_layer_reserve top_cell M5 40", f"save_bdb {bdb}")
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, f"source {_TRACKS}", "set_cell_layer_reserve top_cell M5 off")
    out = _cmd(s, f"open_bdb {bdb}")
    assert "restored 1 persisted reservation(s): top_cell:M6x2" in out, out
    assert "1 persisted reservation(s) held off by a typed `off`" in out, out
    assert ("top_cell", 5) not in s._cell_layer_reserves
    assert s._cell_layer_reserves[("top_cell", 6)] == (3.0, 7.5)
    assert json.loads(s.bdb.meta_get("layer_reserves", "")) == \
        {"top_cell": {"6": [3.0, 7.5]}}
    # the same key re-declared after its off wins over the tombstone
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    _quiet(s2, f"source {_TRACKS}", "set_cell_layer_reserve top_cell M6 off",
           "set_cell_layer_reserve top_cell M6 9")
    _cmd(s2, f"open_bdb {bdb}")
    assert s2._cell_layer_reserves[("top_cell", 6)] == (9.0,)
    # `* off` before the open holds everything off and empties the file
    s3 = buda_cli.BudaSession()
    s3.no_viz = True
    _quiet(s3, f"source {_TRACKS}", "set_cell_layer_reserve * off")
    out = _cmd(s3, f"open_bdb {bdb}")
    assert "held off by a typed `off`" in out and "restored" not in out, out
    assert not s3._cell_layer_reserves
    assert not s3.bdb.meta_get("layer_reserves", "")


def test_revalidation_re_runs_at_a_late_layer_declaration_a_resize_and_the_planner(tmp_path):
    """`open_bdb` can only check a coordinate on a DECLARED layer, and a
    flow that opens its BDB first (`flow/tcl/hdesign.tcl`) declares its
    stack after — so the open skipped every coordinate and nothing ran
    again (Codex P2 on #936).  The check re-runs at each event that makes
    an entry checkable or stale: the layer's declaration, a `resize_cell`,
    and `run_planner hier` right before the enforcement decision."""
    import json
    bdb = tmp_path / "r.bdb"
    s0 = _session()
    s0.bdb.meta_set("layer_reserves", json.dumps({"top_cell": {"6": [50, 150]}}))
    _quiet(s0, "resize_cell top_cell 600 100", f"save_bdb {bdb}")
    # (1) open FIRST: the layer is unknown, both positions are kept — the
    #     declaration of M6 is what drops the stale one
    s = buda_cli.BudaSession()
    s.no_viz = True
    out = _cmd(s, f"open_bdb {bdb}")
    assert "restored 1 persisted reservation(s)" in out and "dropped" not in out, out
    assert s._cell_layer_reserves[("top_cell", 6)] == (50.0, 150.0)
    out = _cmd(s, f"source {_TRACKS}")
    assert "dropped 1 reserved position(s) outside the cell's height (100): 150" in out, out
    assert s._cell_layer_reserves[("top_cell", 6)] == (50.0,)
    # (2) a resize in-session
    s2 = _session()
    _quiet(s2, "set_cell_layer_reserve top_cell M6 50,150")
    out = _cmd(s2, "resize_cell top_cell 600 100")
    assert "dropped 1 reserved position(s) outside the cell's height (100): 150" in out, out
    assert s2._cell_layer_reserves[("top_cell", 6)] == (50.0,)
    # (3) the planner checks before it decides what to enforce
    i = _DESIGN.index("run_planner hier 3")
    s3 = buda_cli.BudaSession()
    s3.no_viz = True
    _quiet(s3, *_DESIGN[:i])
    s3._cell_layer_reserves = {("top_cell", 6): (50.0, 999.0)}
    out = _cmd(s3, "run_planner hier 3")
    assert "dropped 1 reserved position(s) outside the cell's height (200): 999" in out, out
    assert s3._cell_layer_reserves[("top_cell", 6)] == (50.0,)


def test_a_component_only_cell_is_bounded_by_its_placed_bbox(tmp_path):
    """A cell with no cell-table row (an `add_comp` row, a DEF instance
    the LEF did not describe) used to leave every coordinate unchecked
    (Codex P2 on #936): the extent comes from the reference occurrence's
    bbox now — the frame the positions are stated in — at the
    declaration and at the open alike."""
    import json
    bdb = tmp_path / "r.bdb"
    s = _session()
    _quiet(s, "add_comp lone lone_cell - 700 300 1000 420")
    assert not any(c.name == "lone_cell" for c in s.bdb.all_cells())
    assert "outside the cell's height (120)" in \
        _cmd(s, "set_cell_layer_reserve lone_cell M6 150"), "declaration"
    assert "outside the cell's width (300)" in \
        _cmd(s, "set_cell_layer_reserve lone_cell M5 301"), "declaration"
    assert "reserves 1 track(s)" in _cmd(s, "set_cell_layer_reserve lone_cell M6 100")
    # the uniform form reaches the same component fallback (Codex P2 on
    # #937: it validated the name against the cell table alone)
    out = _cmd(s, "set_cell_layer_reserve lone_cell M6 uniform 2")
    assert "reserves 2 track(s)" in out and "uniform 2" in out, out
    assert all(0 <= p <= 120 for p in s._cell_layer_reserves[("lone_cell", 6)])
    _cmd(s, "set_cell_layer_reserve lone_cell M6 100")
    s.bdb.meta_set("layer_reserves", json.dumps({"lone_cell": {"6": [100, 150]}}))
    _cmd(s, f"save_bdb {bdb}")
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    _quiet(s2, f"source {_TRACKS}")
    out = _cmd(s2, f"open_bdb {bdb}")
    assert "dropped 1 reserved position(s) outside the cell's height (120): 150" in out, out
    assert s2._cell_layer_reserves[("lone_cell", 6)] == (100.0,)


def test_an_undeclared_layer_id_is_removed_when_the_stack_is_complete(tmp_path):
    """A persisted row on a numeric layer id the stack never declares (an
    old or hand-edited file) decoded fine and stayed forever — a phantom
    every query reported, and one `get_layer_dir` reads as HORIZONTAL if
    a consumer ever folded it in (Codex P2 on #936).  Every consumer now
    reads declared layers only, and the planner's final revalidation
    removes it LOUD once the stack is complete."""
    import json
    j = _DESIGN.index("run_planner hier 3")
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, *_DESIGN[:j])
    # the shape a restore hands back from an old file: a row on layer 9
    s._cell_layer_reserves = {("top_cell", 6): (3.0,), ("top_cell", 9): (1.0,)}
    s._cell_layer_reserves_restored = {("top_cell", 9)}
    s._persist_layer_reserves()
    assert set(json.loads(s.bdb.meta_get("layer_reserves", ""))["top_cell"]) == {"6", "9"}
    assert 9 not in s._cell_reserves_of("top_cell")        # every consumer's guard
    out = _cmd(s, "run_planner hier 3")
    assert ("cell 'top_cell': layer id 9 is not declared in this stack — its "
            "reservation (1 track(s)) is removed") in out, out
    assert ("top_cell", 9) not in s._cell_layer_reserves
    assert s._cell_layer_reserves[("top_cell", 6)] == (3.0,)
    assert set(json.loads(s.bdb.meta_get("layer_reserves", ""))["top_cell"]) == {"6"}


def test_a_derived_line_reproduces_the_track_exactly(tmp_path):
    """`:g` keeps six significant digits, so a large cell-local coordinate
    came back MOVED when the file was sourced — `1234567.5` as
    `1.23457e+06`, a neighbouring track kept free while the top's own stays
    open (Codex P1 on #936).  Every writer goes through one round-trip
    formatter: the derivation's lines, the declaration's echo, the Tcl
    query."""
    from buda_session.util import fmt_pos
    assert fmt_pos(3) == "3" and fmt_pos(3.0) == "3" and fmt_pos(7.5) == "7.5"
    for v in (1234567.5, 600000.5, 0.1, 1e-7, 123456789.25):
        assert float(fmt_pos(v)) == v, (v, fmt_pos(v))
    s = _session("run_nuts", "run_detailed_nuts")
    line = {"cell": "top_cell", "layer": 6, "layer_name": "M6",
            "positions": [600000.5, 1234567.5], "n_inst": 1, "n_skipped": 0,
            "used_lo": 2, "used_hi": 2, "seat_hit": 0, "seat": None,
            "seat_inst": "u1", "own_hit": 0}
    line["yielded"] = line["yield_short"] = 0
    s._derive_cell_layer_reserves = lambda cells=None, **kw: ([line], [], ["top_cell"])
    out = _cmd(s, f"derive_cell_layer_reserves file {tmp_path / 'r.buda'}")
    text = (tmp_path / "r.buda").read_text()
    assert "set_cell_layer_reserve top_cell M6 600000.5,1234567.5" in text, text
    assert "set_cell_layer_reserve top_cell M6 600000.5,1234567.5" in out
    # ... and the declaration echoes what it stored, exactly
    s2 = _session()
    _quiet(s2, "add_cell wide 2000000 2000000", "add_inst w wide - 0 0")
    echo = _cmd(s2, "set_cell_layer_reserve wide M6 600000.5,1234567.5")
    assert "y = 600000.5, 1234567.5" in echo, echo
    assert s2._cell_layer_reserves[("wide", 6)] == (600000.5, 1234567.5)


def test_the_reservation_persists_and_typed_entries_win(tmp_path):
    bdb = tmp_path / "r.bdb"
    s = _session()
    _cmd(s, f"save_bdb {bdb}")
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, f"open_bdb {bdb}", f"source {_TRACKS}")
    _cmd(s, "set_cell_layer_reserve top_cell M6 3,7.5")
    _cmd(s, "set_cell_layer_reserve top_cell M5 40")
    # a fresh session restores both
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s2.do_command(f"open_bdb {bdb}")
    assert "restored 2 persisted reservation(s)" in buf.getvalue(), buf.getvalue()
    assert s2._cell_layer_reserves[("top_cell", 6)] == (3.0, 7.5)
    assert s2._cell_layer_reserves[("top_cell", 5)] == (40.0,)
    # typed BEFORE the open wins; a previous BDB's restored entry drops
    # (typed into a COPY, so the original's rows are what the second open
    # restores; the copy's persist is the typed entry's own home)
    copy = tmp_path / "r_copy.bdb"
    shutil.copy(bdb, copy)
    s3 = buda_cli.BudaSession()
    s3.no_viz = True
    _quiet(s3, f"open_bdb {copy}", f"source {_TRACKS}",
           "set_cell_layer_reserve top_cell M6 1")
    assert s3._cell_layer_reserves[("top_cell", 6)] == (1.0,)
    with contextlib.redirect_stdout(io.StringIO()):
        s3.do_command(f"open_bdb {bdb}")
    assert s3._cell_layer_reserves[("top_cell", 6)] == (1.0,)
    assert s3._cell_layer_reserves[("top_cell", 5)] == (40.0,)
    # `off` persists too (the layer must be declared for its name to resolve)
    _quiet(s2, f"source {_TRACKS}", "set_cell_layer_reserve top_cell M5 off")
    s4 = buda_cli.BudaSession()
    s4.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s4.do_command(f"open_bdb {bdb}")
    assert ("top_cell", 5) not in s4._cell_layer_reserves


def test_the_derivation_is_the_union_in_the_cells_frame(tmp_path):
    s = _session("run_nuts", "run_detailed_nuts")
    lines, notes, scope = s._derive_cell_layer_reserves()
    assert scope == ["top_cell"]
    assert [(l["cell"], l["layer_name"]) for l in lines] == [("top_cell", "M6")]
    l = lines[0]
    # the top's 8 tracks over u1 and u2, at the SAME cell-local positions
    rows = s._layer_demand()
    comps = {c.name: c for c in s.bdb.all_components()}
    expect = set()
    for r in rows:
        if r["cell"] == "top_cell" and r["layer_name"] == "M6":
            for u in r["used_tracks"]:
                expect.add(round(u - comps[r["inst"]].y1, 3))
    assert set(l["positions"]) == expect and len(l["positions"]) == 8
    assert l["n_inst"] == 2 and (l["used_lo"], l["used_hi"]) == (8, 8)
    # the cell's own bus sits on all eight today, inside its seat
    assert l["own_hit"] == 8 and l["seat_hit"] == 8, l
    out = _cmd(s, f"derive_cell_layer_reserves file {tmp_path / 'r.buda'}")
    assert _LINE in out and "seat_hit" in out, out
    text = (tmp_path / "r.buda").read_text()
    assert "# scope: top_cell" in text and _LINE in text
    # apply declares them; a stale scoped reservation is removed
    _cmd(s, "set_cell_layer_reserve top_cell M5 40")
    out = _cmd(s, "derive_cell_layer_reserves apply")
    assert "applied 1 reservation(s)" in out and "removed 1 stale" in out, out
    assert ("top_cell", 5) not in s._cell_layer_reserves
    assert len(s._cell_layer_reserves[("top_cell", 6)]) == 8
    # the file carries the removal as an `off` line
    _cmd(s, "set_cell_layer_reserve top_cell M5 40")
    _cmd(s, f"derive_cell_layer_reserves file {tmp_path / 'r2.buda'}")
    assert "set_cell_layer_reserve top_cell M5 off" in (tmp_path / "r2.buda").read_text()
    assert "needs a NUTS result" in _cmd(_session(), "derive_cell_layer_reserves")
    assert "names no cell" in _cmd(s, "derive_cell_layer_reserves cells ,")


def test_solved_as_a_template_the_cell_leaves_the_reserved_tracks_free():
    """The measurement the share could not make: under the derived lines
    the cell's own 8-bit bus moves OFF the eight tracks the top wants,
    the top uses all eight on both instances, nothing strands."""
    s = _template_session(_LINE)
    assert s.nuts_result.num_overlaps == 0
    assert s.detailed_result.num_unplaced == 0
    rows = s._layer_reserve_audit()
    assert len(rows) == 2, rows
    for r in rows:
        assert r["reserved"] == 8 and r["top_used"] == 8 and r["own_hit"] == 0, r
    out = _cmd(s, "check_design")
    assert "Success" in out
    assert ("LAYER_RESERVE: top_cell M6: 8 track(s) reserved over 2 "
            "instance(s); the top uses 8..8 of them per instance (100% of "
            "its 16 track(s) over them); own metal "
            "on reserved tracks: 0") in out, out
    # the cell-local solve and the reference DNUTS view both said so
    # (captured by _quiet; re-run the enforcement sites' prints directly)
    fp = s._build_cell_local_floorplan("u1")
    n = s._install_cell_reserve_keepouts(fp, "top_cell", "u1")
    assert n == 8 and len(fp.get_keepout_zones()) == 8
    z = fp.get_keepout_zones()[0]
    assert set(z.layer_ids) == {6} and z.bbox.x1 == 0 and z.bbox.x2 == 600
    # the reference DNUTS solve carries the reservation as the reference
    # wrapper's blocked tracks (absolute over u1), the copy carries none
    wr = {w.input.original_bundle.instances[0]: w for w in s.bundles
          if w.input.original_bundle.cell_context == "top_cell"}
    ref = s._template_track_verdict["top_cell"]["ref"]
    other = next(i for i in wr if i != ref)
    y1 = {c.name: c for c in s.bdb.all_components()}[ref].y1
    assert wr[ref].hier.blocked_tracks == {6: [y1 + p for p in
                                                (83, 86, 89, 92, 100, 103, 106, 109)]}
    # u2 is misaligned with u1 on M5 (x offset 850 against a 32 pitch), so
    # it solves in the global run and carries the list in its own frame
    y2 = {c.name: c for c in s.bdb.all_components()}[other].y1
    assert wr[other].hier.blocked_tracks == {6: [y2 + p for p in
                                                  (83, 86, 89, 92, 100, 103, 106, 109)]}
    assert s.routing_grid is s._bu_reference_grid(
        {wr[ref].input.original_bundle.id})[0]     # no clone for a reservation


def test_a_reservation_the_cell_does_not_need_costs_nothing():
    """Reserving tracks the cell never wanted changes nothing measurable:
    still clean, the audit reporting what the top used of them."""
    s = _template_session("set_cell_layer_reserve top_cell M6 5,8")
    assert s.detailed_result.num_unplaced == 0
    rows = s._layer_reserve_audit()
    assert all(r["reserved"] == 2 and r["own_hit"] == 0 for r in rows), rows


def test_a_top_down_cell_is_not_enforced_and_says_so():
    s = buda_cli.BudaSession()
    s.no_viz = True
    i = _DESIGN.index("run_hier_bundler depth 1")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in [*_DESIGN[:i], _LINE, *_DESIGN[i:]]:
            s.do_command(c)
    assert "BUDA-1920: WARNING" in buf.getvalue(), buf.getvalue()[-1500:]
    assert "top_cell — mark them set_bottom_up" in buf.getvalue()
    _quiet(s, "run_nuts", "run_detailed_nuts")
    out = _cmd(s, "check_design")
    # the cell's own bus still sits on the reserved tracks: reported
    assert "own metal on reserved tracks: 8..8 per instance" in out and "VIOLATED at u1, u2" in out, out
    # ... and a template session prints no such notice
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _template_session(_LINE)
    assert "BUDA-1920" not in buf.getvalue()


# ── a nested template inherits the corridor ───────────────────────────────

_NEST = [
    f"source {_TRACKS}", "open_bdb :memory:",
    "add_cell leaf 80 80", "add_cell inner 250 80",
    "add_inst_to_cell inner p leaf 10 0", "add_inst_to_cell inner q leaf 160 0",
    "add_cell top_cell 600 300",
    "add_inst_to_cell top_cell a leaf 20 20",
    "add_inst_to_cell top_cell b leaf 300 20",
    "add_inst_to_cell top_cell c inner 20 180",
    "add_inst u1 top_cell - 50 50", "add_inst u2 top_cell - 900 50",
    "derive_busterms 2", "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
    "add_blocks_from_bdb 2 skip", "bdb_net_mode on",
    "add_bus loc[8] u1/a.out u1/b.in", "add_bus loc2[8] u2/a.out u2/b.in",
    "add_bus x[8] u1/b.out u2/a.in",
    "add_bus in1[8] u1/c/p.out u1/c/q.in", "add_bus in2[8] u2/c/p.out u2/c/q.in",
]
_NEST_TAIL = ["run_hier_bundler depth 2", "generate_hier_topologies",
              "run_planner hier 3", "run_nuts",
              "check_template_tracks on_mismatch independent",
              "run_detailed_nuts"]


def _nested(marks, *policy):
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in [*_NEST, *marks, *policy, *_NEST_TAIL]:
            s.do_command(c)
    return s, buf.getvalue()


def _inner_tracks(s, lid):
    """The nested template's own bit tracks on `lid`, per instance."""
    wr = {w.input.original_bundle.id: w for w in s.bundles}
    out = {}
    for ns in s.detailed_result.net_segments:
        ob = wr[ns.bundle_id].input.original_bundle
        if ob.cell_context == "inner" and ns.layer == lid:
            out.setdefault(ob.instances[0], set()).add(ns.track_position)
    return out


def test_a_nested_template_keeps_an_ancestors_corridor_free():
    """A reservation on `top_cell` is a corridor over every top_cell
    INSTANCE, so the nested `inner` template's own metal must keep off it
    too — and `inner` is solved in its own frame, where the corridor is an
    INHERITED reservation (the ancestor's positions projected through the
    child's offset, unioned over the child's occurrences).  Measured need:
    on the SoC vehicle the top's M5 tracks over a cluster sit where the
    nested core's 32-bit bus seats, and a core solved without them lost
    all 32 bits at DNUTS while the cluster's own audit read VIOLATED."""
    s0, _ = _nested(["set_bottom_up *"])
    lid = s0._layer_name_map["M6"]
    before = _inner_tracks(s0, lid)
    assert set(before) == {"u1/c", "u2/c"} and len(before["u1/c"]) == 8
    comps = {c.name: c for c in s0.bdb.all_components()}
    # reserve, ON top_cell, exactly the tracks inner's bus took (top frame)
    pos = sorted(t - comps["u1"].y1 for t in before["u1/c"])
    line = "set_cell_layer_reserve top_cell M6 " + ",".join(f"{p:g}" for p in pos)
    s, out = _nested(["set_bottom_up *"], line)
    assert "[LayerReserve] cell 'inner': local solve with 8 reserved " \
           "track(s) kept free on M6x8 (8 from top_cell)" in out, out
    assert "[LayerReserve] cell 'top_cell': local solve with 8 reserved " \
           "track(s) kept free on M6x8" in out
    assert s.detailed_result.num_unplaced == 0
    after = _inner_tracks(s, lid)
    for inst, tracks in after.items():
        absres = {comps[inst[:2]].y1 + p for p in pos}
        assert len(tracks) == 8 and not (tracks & absres), (inst, tracks)
    # the audit: the corridor over both top_cell instances carries none of
    # the cell's own metal — the nested template's included
    rows = s._layer_reserve_audit()
    assert [(r["inst"], r["own_hit"]) for r in rows] == [("u1", 0), ("u2", 0)]
    assert "inner" not in _cmd(s, "check_design"), "inner reserves nothing itself"
    # a NESTED cell planned top-down escapes the corridor and is named
    s2, out2 = _nested(["set_bottom_up top_cell"], line)
    assert "BUDA-1920: WARNING" in out2 and \
           "inner (inherited from top_cell) — mark them set_bottom_up" in out2, out2
    assert "leaf" not in out2.split("BUDA-1920")[1].splitlines()[0]
    # ... and its metal is audited as the corridor's, under top_cell's row
    rows2 = s2._layer_reserve_audit()
    assert any(r["own_hit"] for r in rows2), rows2


def _flipped(marks, *policy, xform="flip_comp u2 y"):
    """The nested design with the second top instance transformed AFTER
    placement (hierarchical flip/rotate keep the tokens 'N' and rewrite the
    children, so orientation is what detection finds, never the token)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    i = _NEST.index("derive_busterms 2")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in [*_NEST[:i], xform, *_NEST[i:], *marks, *policy, *_NEST_TAIL]:
            s.do_command(c)
    return s, buf.getvalue()


def test_a_mirrored_occurrence_folds_the_corridor_through_its_orientation():
    """Codex P1 on #936: a nested occurrence under a MIRRORED ancestor (u2
    flipped, so u2 and u2/c are FN/FS relative to their references) was
    skipped by the inheritance, and the derivation and audit read the
    token 'N' a hierarchical flip leaves behind.  Every direction-
    preserving occurrence now folds through its orientation's involution
    on the axis — the corridor over u2 is the mirror image of the one over
    u1, and in the templates' own frames the two coincide."""
    s0, _ = _flipped(["set_bottom_up *"])
    comps = {c.name: c for c in s0.bdb.all_components()}
    fr, n_rot = s0._reserve_frames("top_cell")
    assert n_rot == 0 and fr["u1"] == "N" and fr["u2"] in ("FN", "FS", "S"), fr
    lid = s0._layer_name_map["M6"]
    before = _inner_tracks(s0, lid)
    assert set(before) == {"u1/c", "u2/c"}
    # the derivation folds u2's demand into the SAME cell-frame positions
    # (the design is symmetric, so the union is one instance's worth)
    _quiet(s0, "run_nuts", "run_detailed_nuts")
    pos = sorted(t - comps["u1"].y1 for t in before["u1/c"])
    line = "set_cell_layer_reserve top_cell M6 " + ",".join(f"{p:g}" for p in pos)
    s, out = _flipped(["set_bottom_up *"], line)
    # inner's leaves are y-symmetric, so detection reads BOTH occurrences as
    # N in inner's frame while u2 itself is mirrored: the corridor over u2/c
    # lands at the mirror image, and the template (solved once) keeps both
    # images free — the expected union, computed here independently
    expect = set()
    for inst, o in s._reserve_frames("top_cell")[0].items():
        c = comps[inst + "/c"]
        for a in s._reserve_abs_positions("top_cell", inst, o, comps[inst], lid):
            if c.y1 - 1e-6 <= a <= c.y2 + 1e-6:
                expect.add(round(a - c.y1, 6))
    got, src = s._inherited_reserves("inner")
    assert set(got[lid]) == expect and src[lid] == {"top_cell": len(expect)}
    assert len(expect) == 16, expect            # 8 + their 8 mirror images
    assert (f"local solve with 16 reserved track(s) kept free on M6x16 "
            f"(16 from top_cell)") in out, out
    assert s.detailed_result.num_unplaced == 0
    after = _inner_tracks(s, lid)
    for inst, o in s._reserve_frames("top_cell")[0].items():
        absres = set(s._reserve_abs_positions("top_cell", inst, o,
                                              comps[inst], lid))
        assert len(absres) == 8
        assert not (after.get(inst + "/c", set()) & absres), (inst, o, after, absres)
    rows = s._layer_reserve_audit()
    assert [(r["inst"], r["own_hit"]) for r in rows] == [("u1", 0), ("u2", 0)], rows
    # and the mirrored instance's corridor is NOT where the upright one's
    # absolute positions would put it
    u1_abs = set(s._reserve_abs_positions("top_cell", "u1", "N", comps["u1"], lid))
    u2_abs = set(s._reserve_abs_positions("top_cell", "u2", fr["u2"], comps["u2"], lid))
    assert {a - comps["u1"].y1 for a in u1_abs} != {a - comps["u2"].y1 for a in u2_abs}


def test_a_rotated_class_is_not_governed_and_says_so():
    """Codex P1 on #936: a 90-degree-rotated instance class plans through
    its rotation-class CLONE template (`top_cell90`), whose context name
    found no reservation and so got no keepouts and no notice.  A rotated
    frame swaps the axes, so an upright-stated track has no image on the
    same layer there: the class is NOT governed by the cell's own
    reservation and BUDA-1921 says so at its solve; the derivation counts
    the rotated occurrence out and says why."""
    s, out = _flipped(["set_bottom_up *"], _LINE.replace("top_cell M6 ", "top_cell M6 ")
                      .replace("83,86,89,92,100,103,106,109", "205,208,211,219"),
                      xform="rotate_comp u2 90")
    assert "top_cell90" in out, out[-3000:]
    assert "BUDA-1921: WARNING" in out and "top_cell90 (cell 'top_cell')" in out, out
    assert "[LayerReserve] cell 'top_cell': local solve with 4 reserved" in out
    # the upright instance's audit row stands alone: the rotated one is
    # not governed, so it has no row
    rows = s._layer_reserve_audit()
    assert [r["inst"] for r in rows] == ["u1"], rows
    _quiet(s, "run_nuts", "run_detailed_nuts")
    _lines, notes, _scope = s._derive_cell_layer_reserves(cells=["top_cell"])
    assert any("1 90-degree-rotated instance(s) not folded in" in n for n in notes), notes


def test_the_derivation_keeps_the_computed_track_unrounded():
    """A three-decimal rounding in the derivation moved a track before the
    exact formatter ever saw it (`10.0004` -> `10.0`, outside the audit's
    1e-6 match; Codex P2 on #936): the computed float is kept, and only
    the line's formatter serializes it."""
    s = _session("run_nuts", "run_detailed_nuts")
    comps = {c.name: c for c in s.bdb.all_components()}
    u1, u2 = comps["u1"], comps["u2"]
    rows = []
    for inst, c in (("u1", u1), ("u2", u2)):
        rows.append({"inst": inst, "cell": "top_cell", "layer": 6,
                     "layer_name": "M6", "used_tracks": [c.y1 + 10.0004],
                     "own_tracks": [], "own_need": 0.0, "own_seat": None,
                     "own_window": None, "bits": 8, "used": 1, "supply": 40,
                     "pct": 2.5})
    s._layer_demand = lambda *a, **k: rows
    lines, _notes, _scope = s._derive_cell_layer_reserves(cells=["top_cell"])
    assert len(lines) == 1 and len(lines[0]["positions"]) == 1, lines
    got = lines[0]["positions"][0]
    assert abs(got - 10.0004) < 1e-9 and got != 10.0, got   # unrounded
    out = _cmd(s, "derive_cell_layer_reserves")
    import re
    m = re.search(r"^\s*set_cell_layer_reserve top_cell M6 (\S+)$", out, re.M)
    assert m and float(m[1]) == got, (out, got)      # the line round-trips it


def test_the_uniform_form_names_real_centred_tracks(tmp_path):
    """E5's conventional arm: `uniform F` reserves F evenly spaced SIGNAL
    tracks over the cell, as cell-local positions of REAL tracks read
    over the reference occurrence (u1, at 50 50) — not a spacing, so the
    same enforcement and audit read both arms."""
    s = _session()
    g = s.routing_grid.get_layer_grid(6)
    u1 = next(c for c in s.bdb.all_components() if c.name == "u1")
    tracks = sorted({p for p, _s in g.signal_tracks_in(0.5 * (u1.x1 + u1.x2),
                                                       u1.y1, u1.y2)})
    n = len(tracks)
    assert n > 8
    out = _cmd(s, "set_cell_layer_reserve top_cell M6 uniform 4")
    assert "reserves 4 track(s)" in out and "uniform 4" in out, out
    pos = s._cell_layer_reserves[("top_cell", 6)]
    assert len(pos) == 4
    # every position is a real track in u1's frame ...
    absolute = [p + u1.y1 for p in pos]
    assert all(a in tracks for a in absolute), (absolute, tracks)
    # ... spread over the extent, no pick leaning on an edge: the gaps
    # between consecutive picks are within one track of each other and
    # the first/last picks sit about half a gap in from the ends
    idx = [tracks.index(a) for a in absolute]
    gaps = [b - a for a, b in zip(idx, idx[1:])]
    assert max(gaps) - min(gaps) <= 1, idx
    assert abs(idx[0] - (n - 1 - idx[-1])) <= 1, idx
    assert idx[0] >= gaps[0] // 2 - 1, idx
    # `uniform 1` is the middle track
    _cmd(s, "set_cell_layer_reserve top_cell M6 uniform 1")
    (mid,) = s._cell_layer_reserves[("top_cell", 6)]
    assert abs(tracks.index(mid + u1.y1) - (n - 1) / 2) <= 1
    # a V layer reads x over the cell's width
    _cmd(s, "set_cell_layer_reserve top_cell M5 uniform 3")
    xs = s._cell_layer_reserves[("top_cell", 5)]
    assert len(xs) == 3 and all(0 <= x <= 600 for x in xs), xs
    # more tracks than the cell has, and the bad counts
    out = _cmd(s, "set_cell_layer_reserve top_cell M6 uniform 999")
    assert f"asks more tracks than the cell has ({n} signal tracks" in out, out
    assert s._cell_layer_reserves[("top_cell", 6)] == (mid,)  # unchanged
    assert "positive" in _cmd(s, "set_cell_layer_reserve top_cell M6 uniform 0")
    assert "positive" in _cmd(s, "set_cell_layer_reserve top_cell M6 uniform x")
    assert "usage" in _cmd(s, "set_cell_layer_reserve top_cell M6 uniform")
    assert "unknown layer" in _cmd(s, "set_cell_layer_reserve top_cell M9 uniform 2")
    assert "unknown cell" in _cmd(s, "set_cell_layer_reserve nosuch M6 uniform 2")
    # `* TOP` names the marked cells on the TOP layers: refused with no
    # mark, one line per (cell, TOP layer) once the cell is marked
    assert "none is marked" in _cmd(s, "set_cell_layer_reserve * TOP uniform 2")
    _quiet(s, "set_bottom_up top_cell")
    out = _cmd(s, "set_cell_layer_reserve * TOP uniform 2")
    assert out.count("[LayerReserve] top_cell:") == 3, out
    for lid in (5, 6, 7):
        assert len(s._cell_layer_reserves[("top_cell", lid)]) == 2, lid
    # `leaf` is placed only as a child; a cell with no placed occurrence
    # at all has no frame to read the tracks over
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    _quiet(s2, f"source {_TRACKS}", "open_bdb :memory:",
           "add_cell lonely 100 100")
    assert "no placed occurrence" in \
        _cmd(s2, "set_cell_layer_reserve lonely M6 uniform 1")
    # the uniform lines persist like typed ones and win at the reopen
    path = tmp_path / "u.bdb"
    s3 = buda_cli.BudaSession()
    s3.no_viz = True
    design = [f"open_bdb {path}" if c == "open_bdb :memory:" else c
              for c in _DESIGN]
    _quiet(s3, *design, "set_cell_layer_reserve top_cell M6 uniform 4")
    kept = s3._cell_layer_reserves[("top_cell", 6)]
    _quiet(s3, "save_bdb")
    s4 = buda_cli.BudaSession()
    s4.no_viz = True
    _quiet(s4, f"source {_TRACKS}", f"open_bdb {path}")
    assert s4._cell_layer_reserves[("top_cell", 6)] == kept


def test_the_uniform_reservation_is_enforced_like_a_typed_one():
    """A cell solved as a template under `uniform F` keeps those F tracks
    free of its own metal — the audit reads own_hit 0 on every instance,
    which is what makes the arm comparable to the derived one."""
    s = _template_session("set_cell_layer_reserve top_cell M6 uniform 4")
    pos = s._cell_layer_reserves[("top_cell", 6)]
    assert len(pos) == 4
    rows = s._layer_reserve_audit()
    hit = [r for r in rows if r["layer_name"] == "M6"]
    assert {r["inst"] for r in hit} == {"u1", "u2"}, rows
    assert all(r["own_hit"] == 0 for r in hit), hit
    assert all(r["reserved"] == 4 for r in hit), hit


def _own_tracks(s, cell_ctx, lid):
    """A template's OWN bit tracks on `lid`, per instance."""
    wr = {w.input.original_bundle.id: w for w in s.bundles}
    out = {}
    for ns in s.detailed_result.net_segments:
        ob = wr[ns.bundle_id].input.original_bundle
        if ob.cell_context == cell_ctx and ns.layer == lid:
            out.setdefault(ob.instances[0], set()).add(ns.track_position)
    return out


def test_an_instance_solved_in_the_global_run_keeps_the_reservation():
    """E5 measured the gap: a reservation reaches the reference solve as
    grid keepouts and every COPY through the copy, but an instance
    MISALIGNED with its reference under `on_mismatch independent` is
    solved in the global DNUTS run on the full grid, where the reserved
    tracks are nobody's keepout (a reservation is room FOR the top) — and
    the audit read the cluster's own metal on one reserved track at the
    SoC's misaligned clusters.  Such an instance now carries the reserved
    tracks, folded into its frame, as its bundles' blocked tracks."""
    i = _NEST.index("add_inst u2 top_cell - 900 50")
    design = [*_NEST[:i], "add_inst u2 top_cell - 900 51", *_NEST[i + 1:]]

    def run(*policy):
        s = buda_cli.BudaSession()
        s.no_viz = True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for c in [*design, "set_bottom_up *", *policy, *_NEST_TAIL]:
                s.do_command(c)
        return s, buf.getvalue()

    s0, out0 = run()
    assert re.search(r"cell 'top_cell': MISALIGNED — u2", out0), out0[-2000:]
    comps = {c.name: c for c in s0.bdb.all_components()}
    own = _own_tracks(s0, "top_cell", 6)
    assert own.get("u2"), own
    # reserve two of the tracks u2's OWN bus sits on, stated in the
    # template frame (u1's), folded back for u2 — the reference's grid
    # cannot see them and u2's window has room for the bus beside them
    u2 = comps["u2"]
    fr = s0._reserve_frames("top_cell")[0]
    hit = sorted(own["u2"])[:2]
    ext = u2.y2 - u2.y1
    pos = [s0._reserve_ref_pos(t - u2.y1, fr["u2"], ext, True) for t in hit]
    line = "set_cell_layer_reserve top_cell M6 " + ",".join(f"{p:g}" for p in pos)
    s, out = run(line)
    assert "keep their reserved tracks as blocked tracks: u1, u2" in out, out[-3000:]
    wr = {w.input.original_bundle.instances[0]: w for w in s.bundles
          if w.input.original_bundle.cell_context == "top_cell"
          and w.input.original_bundle.instances}
    # the reference carries the same list in ITS frame
    u1 = comps["u1"]
    assert wr["u1"].hier.blocked_tracks == {6: sorted(u1.y1 + p for p in pos)}
    assert wr["u2"].hier.blocked_tracks == {6: hit}, wr["u2"].hier.blocked_tracks
    after = _own_tracks(s, "top_cell", 6)
    assert not (after.get("u2", set()) & set(hit)), (after, hit)
    assert len(after["u2"]) == 8 and s.detailed_result.num_unplaced == 0
    rows = s._layer_reserve_audit()
    assert [(r["inst"], r["own_hit"]) for r in rows] == [("u1", 0), ("u2", 0)], rows
    # removing the reservation clears the stamp at the next plan call —
    # a stale list would keep excluding tracks the reservation no longer
    # names (Codex P2 on #937) — and the re-solve is free to use them
    _quiet(s, "set_cell_layer_reserve * off")
    with contextlib.redirect_stdout(io.StringIO()):
        s._bottom_up_dnuts_plan()
    assert all(not w.hier.blocked_tracks for w in s.bundles)
    _quiet(s, "run_detailed_nuts")
    assert s.detailed_result.num_unplaced == 0
    assert len(_own_tracks(s, "top_cell", 6)["u2"]) == 8
    # the same design aligned (u2 back on the phase): only the reference
    # carries the list, the copy honours it through the copy
    s2, out2 = _nested(["set_bottom_up *"], line)
    assert "blocked tracks: u1\n" in out2 or "blocked tracks: u1" in out2, out2[-2000:]
    st = {w.input.original_bundle.instances[0]: bool(w.hier.blocked_tracks)
          for w in s2.bundles if w.input.original_bundle.cell_context == "top_cell"}
    assert st == {"u1": True, "u2": False}, st


def test_the_abstract_stage_reports_own_metal_as_an_estimate():
    """Before detailed NUTS the audit reads the ABSTRACT seat footprint —
    conservative by design (bits or bits+1 tracks by phase), so a bus
    seated flush against a reserved track reads as touching it while its
    placed bits do not (E5's SoC: io_blk_cell 6 of 48 at the abstract
    stage, 0 at the detailed one).  The abstract line says it is an
    estimate and never says VIOLATED; the verdict is on placed bits."""
    i = _DESIGN.index("run_hier_bundler depth 1")
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, *_DESIGN[:i], "set_bottom_up top_cell", _LINE, *_DESIGN[i:],
           "run_nuts")
    out = _cmd(s, "check_design")
    assert "own metal on reserved tracks (abstract seat footprint" in out, out
    assert "VIOLATED" not in out
    _quiet(s, "check_template_tracks on_mismatch independent",
           "run_detailed_nuts")
    out = _cmd(s, "check_design")
    assert "own metal on reserved tracks: 0..0 per instance" in out, out
    assert "abstract seat footprint" not in out


# ── the top-side half: the top is steered onto the reserved tracks (6b) ──

# Eight tracks INSIDE the top bus's M6 seat window (absolute [110, 190]
# over u1: cell-local [60, 140]) that the top does not use on its own —
# its bits sit at cell-local 83..109 (`_LINE`, the derived reservation).
# Four below and four above, so landing on them is a choice, not a drift;
# no 34-wide window (the bus's footprint) holds the eight, so the abstract
# seat is NOT steered and the bits are (the window holds them all).
_STEER = "set_cell_layer_reserve top_cell M6 66,69,72,75,117,120,123,126"
# A contiguous run of eight (spread 26 < 34): half over the top's natural
# tracks (100..109 = the upper four of `_LINE`) and half beyond them.
_STEER_RUN = "set_cell_layer_reserve top_cell M6 100,103,106,109,117,120,123,126"


def _template_run(*policy):
    """`_template_session` with the console captured: (session, output)."""
    i = _DESIGN.index("run_hier_bundler depth 1")
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in [*_DESIGN[:i], "set_bottom_up top_cell", *policy,
                  *_DESIGN[i:], "run_nuts",
                  "check_template_tracks on_mismatch independent",
                  "run_detailed_nuts"]:
            s.do_command(c)
    return s, buf.getvalue()


def _top_seat(s):
    """The top bus's M6 abstract seat (the one cross-instance bundle)."""
    top = [w.input.original_bundle.id for w in s.bundles
           if not w.input.original_bundle.instances]
    assert len(top) == 1, top
    ts = [t for t in s.nuts_result.segments
          if t.bundle_id == top[0] and t.layer == 6]
    assert len(ts) == 1, ts
    return ts[0]


def test_the_top_is_steered_onto_the_reserved_tracks():
    """E5's refutation of the half-built primitive: the block left the
    reserved tracks free and the top landed on them 6-11 % of the time,
    since nothing steered it there.  Now the reservation is also a
    CORRIDOR: abstract NUTS seats the crossing bus on the reserved tracks
    and DetailedNUTS lands its bits there first — on this vehicle every
    one of the top's 16 tracks over the two instances is a reserved one,
    where the first half alone put none of them there.  Off — the
    default — the E5 reading comes back exactly."""
    s, out = _template_run("set_reserve_steer on", _STEER)
    assert ("[LayerReserve] 2 corridor(s) over 2 instance(s) steer the "
            "crossing buses onto the reserved tracks") in out, out[-2000:]
    assert s.routing_grid.has_reserve_corridors()
    assert s.nuts_result.num_overlaps == 0
    assert s.detailed_result.num_unplaced == 0
    # the abstract seat stays at its pull (no footprint-wide window of the
    # corridor holds the eight), the bits take the corridor
    assert _top_seat(s).track_position == pytest.approx(150.0)
    rows = s._layer_reserve_audit()
    assert [(r["inst"], r["reserved"], r["top_used"], r["top_total"],
             r["own_hit"]) for r in rows] == \
        [("u1", 8, 8, 8, 0), ("u2", 8, 8, 8, 0)], rows
    out = _cmd(s, "check_design")
    assert "Success" in out
    assert ("the top uses 8..8 of them per instance (100% of its 16 "
            "track(s) over them); own metal on reserved tracks: 0..0") in out, out
    # the Tcl row carries the total as its LAST field
    sys.path.insert(0, str(Path(__file__).parents[2] / "tools"))
    import buda_server
    assert buda_server._reserve_audit(s) == \
        "{{u1} {top_cell} {M6} 8 8 0 8} {{u2} {top_cell} {M6} 8 8 0 8}"

    # the first half alone (steering off — the DEFAULT): the tracks are
    # free and unused
    s0, out0 = _template_run(_STEER)
    assert "corridor(s)" not in out0
    assert not s0.routing_grid.has_reserve_corridors()
    assert s0.detailed_result.num_unplaced == 0
    rows = s0._layer_reserve_audit()
    assert all(r["top_used"] == 0 and r["own_hit"] == 0 for r in rows), rows
    assert "(0% of its 16 track(s) over them)" in _cmd(s0, "check_design")
    assert _top_seat(s0).track_position == pytest.approx(150.0)
    # ... and switching it on re-solves onto the corridor at the next NUTS
    assert "steering on — 2 corridor(s) installed" in \
        _cmd(s0, "set_reserve_steer on")
    assert "reserve_steer is on" in _cmd(s0, "set_reserve_steer")
    assert "expects on|off" in _cmd(s0, "set_reserve_steer maybe")
    _quiet(s0, "run_nuts", "run_detailed_nuts")
    assert all(r["top_used"] == 8 for r in s0._layer_reserve_audit())
    assert "steering off" in _cmd(s0, "set_reserve_steer off")
    assert not s0.routing_grid.has_reserve_corridors()

    # a contiguous run the footprint can host: the abstract SEAT moves onto
    # it (its centre, 163) and every bit lands on it; off, the top's own
    # tracks overlap the run's lower half by construction — 4 of 8
    s2, _ = _template_run("set_reserve_steer on", _STEER_RUN)
    assert s2.detailed_result.num_unplaced == 0
    assert _top_seat(s2).track_position == pytest.approx(163.0)
    assert [r["top_used"] for r in s2._layer_reserve_audit()] == [8, 8]
    s3, _ = _template_run(_STEER_RUN)
    assert _top_seat(s3).track_position == pytest.approx(150.0)
    assert [r["top_used"] for r in s3._layer_reserve_audit()] == [4, 4]


def test_the_env_knob_and_a_design_with_no_reservation():
    """BUDA_RESERVE_STEER=1 is the same lever from the environment (a
    whole run's worth); a design reserving nothing installs no corridor
    whatever the setting (byte-identical, corpus-guarded)."""
    import os
    old = os.environ.get("BUDA_RESERVE_STEER")
    os.environ["BUDA_RESERVE_STEER"] = "1"
    try:
        s = _template_session(_STEER)
    finally:
        if old is None:
            del os.environ["BUDA_RESERVE_STEER"]
        else:
            os.environ["BUDA_RESERVE_STEER"] = old
    assert s._reserve_steer and s.routing_grid.has_reserve_corridors()
    assert all(r["top_used"] == 8 for r in s._layer_reserve_audit())
    s, out = _template_run("set_reserve_steer on")
    assert s._reserve_steer
    assert "corridor" not in out
    assert not s.routing_grid.has_reserve_corridors()
    assert s._sync_reserve_corridors() == 0
    assert s._reserve_corridors() == []
    s, _ = _template_run()
    assert not s._reserve_steer


# ── a nested child's corridor steers the ENCLOSING cell's own bus ────────

# The SoC's shape: a core inside a cluster, the cluster's own bus crossing
# it.  `inner` (250 x 80) sits between a and b, so top_cell's loc bus
# (a -> b, M6) crosses it; inner's own bus in1 (p -> q) lives on M6 too.
_NEST_X = [
    f"source {_TRACKS}", "open_bdb :memory:",
    "add_cell leaf 80 80", "add_cell inner 250 80",
    "add_inst_to_cell inner p leaf 10 0", "add_inst_to_cell inner q leaf 160 0",
    "add_cell top_cell 700 200",
    "add_inst_to_cell top_cell a leaf 20 20",
    "add_inst_to_cell top_cell b leaf 600 20",
    "add_inst_to_cell top_cell c inner 200 20",
    "add_inst u1 top_cell - 50 50", "add_inst u2 top_cell - 1000 50",
    "derive_busterms 2", "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
    "add_blocks_from_bdb 2 skip", "bdb_net_mode on",
    "add_bus loc[8] u1/a.out u1/b.in", "add_bus loc2[8] u2/a.out u2/b.in",
    "add_bus x[8] u1/b.out u2/a.in",
    "add_bus in1[8] u1/c/p.out u1/c/q.in", "add_bus in2[8] u2/c/p.out u2/c/q.in",
]


def test_a_nested_childs_corridor_steers_the_enclosing_cells_bus():
    """The cell-local solve of `top_cell` is the TOP over its child `c`:
    with `inner` reserving tracks (in ITS frame), the corridor reaches
    the cluster-level solve translated into top_cell's frame — the local
    NUTS seats the loc bus on it and the reference DNUTS view (absolute)
    lands the bits there — so every instance of the template routes its
    bus over the child on the child's reserved tracks."""
    def run(*policy):
        s = buda_cli.BudaSession()
        s.no_viz = True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for c in [*_NEST_X, "set_bottom_up *", *policy, *_NEST_TAIL]:
                s.do_command(c)
        return s, buf.getvalue()

    s0, _ = run()
    assert s0.detailed_result.num_unplaced == 0
    rows0 = {(r["inst"], r["layer_name"]): r for r in s0._layer_demand()}
    used_c = rows0[("u1/c", "M6")]["used_tracks"]
    assert len(used_c) == 8, used_c          # loc's 8 bits cross c
    comps = {c.name: c for c in s0.bdb.all_components()}
    c1 = comps["u1/c"]
    # reserve, in inner's frame, the tightest run of eight M6 tracks inside
    # c's extent that the loc bus does NOT use today (a run the bus's
    # footprint can host, so the cell-local SEAT is steered too)
    g = s0.routing_grid.get_layer_grid(6)
    free = [p for p, _ in g.signal_tracks_in(0.5 * (c1.x1 + c1.x2), c1.y1, c1.y2)
            if all(abs(p - u) > 1e-6 for u in used_c)]
    assert len(free) >= 8, free
    pick = min((free[i:i + 8] for i in range(len(free) - 7)),
               key=lambda w: w[-1] - w[0])
    assert pick[-1] - pick[0] <= 34.0, pick
    line = "set_cell_layer_reserve inner M6 " + ",".join(
        f"{p - c1.y1:g}" for p in pick)
    s, out = run("set_reserve_steer on", line)
    assert "[LayerReserve] cell 'inner': local solve with 8 reserved" in out
    assert s.detailed_result.num_unplaced == 0
    aud = {r["inst"]: r for r in s._layer_reserve_audit()}
    assert set(aud) == {"u1/c", "u2/c"}, aud
    for inst, r in aud.items():
        assert (r["reserved"], r["top_used"], r["own_hit"]) == (8, 8, 0), (inst, r)
    # the cell-local engine saw the child's corridor in its own frame
    cl = s._cell_local_corridors("u1")
    assert [(lid, own) for lid, _a, _b, _t, own in cl] == [(6, "u1/c")]
    assert cl[0][3] == pytest.approx([p - 50.0 for p in pick])   # u1.y1 = 50
    assert (cl[0][1], cl[0][2]) == (200.0, 450.0)                # c's x extent

    def loc_seat(sess):
        bid = next(w.input.original_bundle.id for w in sess.bundles
                   if w.input.original_bundle.instances == ["u1"])
        return next(t.track_position for t in sess.nuts_result.segments
                    if t.bundle_id == bid and t.layer == 6)
    # the cluster's local solve seated its bus on the corridor's centre
    assert loc_seat(s) == pytest.approx(0.5 * (min(pick) + max(pick)))
    # off: the child's tracks are merely free — the bus keeps the seat it
    # had with nothing reserved, and whether its bits fall on them is
    # chance (the E5 reading), not steering
    s1, _ = run(line)
    assert loc_seat(s1) == pytest.approx(loc_seat(s0))
    assert loc_seat(s1) != pytest.approx(loc_seat(s))


def test_the_parallel_screen_and_sweep_seat_like_the_sequential_ones():
    """The bits-only study mode (BUDA_RESERVE_STEER_NUTS=0) leaves every
    abstract seat at its pull; the parallel screen and sweep used to take
    the grid's corridors unconditionally while the sequential screen and
    trial did not, so a sweep could rank and pick on placements the replay
    never makes (Codex P2 on #938).  One predicate now gates both: in
    either mode the parallel screen's scores equal the sequential one's,
    and the sweep's outcomes the sequential trials'."""
    import os
    import buda
    old = os.environ.get("BUDA_RESERVE_STEER_NUTS")
    try:
        for mode in ("0", "1"):
            os.environ["BUDA_RESERVE_STEER_NUTS"] = mode
            s, _ = _template_run("set_reserve_steer on", _STEER_RUN)
            assert s.routing_grid.has_reserve_corridors()
            assert s._reserve_steer_nuts() == (mode == "1")
            w = next(w for w in s.bundles
                     if not w.input.original_bundle.instances)
            alts = [t for t in range(len(w.input.candidates))
                    if t != w.plan.selected_topology_index]
            assert alts
            seq = s._rr_screen_scores(w, alts)
            par = s._rr_screen_scores_many([(w, alts)])[0]
            assert seq is not None and seq == par, (mode, seq, par)
            # the sweep's NUTS engines get the grid (stage a needs it for
            # the corridors alone) and the same NUTS-half verdict
            bid = w.input.original_bundle.id
            _b, _n, dn = s._rr_sweep_stage_setup([(0, bid, 0, alts[0])],
                                                 'a', lambda: (0, 0))
            assert dn.get("grid") is s.routing_grid
            assert dn.get("nuts_corridors") == (mode == "1")
            # the seat itself: steered onto the run only when the NUTS
            # half is on (the bits are steered either way)
            assert _top_seat(s).track_position == pytest.approx(
                163.0 if mode == "1" else 150.0)
            assert [r["top_used"] for r in s._layer_reserve_audit()] == [8, 8]
    finally:
        if old is None:
            os.environ.pop("BUDA_RESERVE_STEER_NUTS", None)
        else:
            os.environ["BUDA_RESERVE_STEER_NUTS"] = old


# ── ladder item 6d: the derivation yields the block its seat ─────────────

def _wide(n, *policy, yield_seat=False):
    """The two-instance vehicle with an n-bit cell-local bus and the cell
    banded to [M5..M6] — M6 the only H layer its bus can take, so the
    local solve cannot escape a reservation by re-planning (unbanded, the
    8-bit vehicle moves its bus to M4/M2 and any reservation costs it
    nothing).  Returns (blind session, derived lines, notes, the
    template session routed under the derived lines)."""
    design = [c.replace("loc[8]", f"loc[{n}]").replace("loc2[8]", f"loc2[{n}]")
              for c in _DESIGN]
    i = design.index("run_hier_bundler depth 1")
    a = buda_cli.BudaSession()
    a.no_viz = True
    _quiet(a, *design[:i], "set_cell_layer_cap top_cell M6 -min M5",
           *design[i:], "run_nuts", "run_detailed_nuts")
    lines, notes, _ = a._derive_cell_layer_reserves(yield_seat=yield_seat)
    text = [f"set_cell_layer_reserve {l['cell']} {l['layer_name']} "
            + ",".join(fmt_pos(q) for q in l["positions"]) for l in lines]
    t = buda_cli.BudaSession()
    t.no_viz = True
    _quiet(t, *design[:i], "set_bottom_up top_cell",
           "set_cell_layer_cap top_cell M6 -min M5", *policy, *text,
           *design[i:], "run_nuts",
           "check_template_tracks on_mismatch independent",
           "run_detailed_nuts")
    return a, lines, notes, t


def _placed_bits(t):
    out = {}
    for w in t.bundles:
        b = w.input.original_bundle
        bits = {n.bit_index for n in t.detailed_result.net_segments
                if n.bundle_id == b.id}
        out[b.get_net_names()[0]] = (len(b.get_net_names()), len(bits))
    return out


def test_the_derivation_yields_the_block_its_seat_where_the_union_covers_it():
    """Ladder item 6d.  A 12-bit cell-local bus in a 19-track seat with the
    top's 8 tracks inside it, banded so the bus cannot leave M6: under the
    full union the block's bus strands (24 bits — every bit on both
    instances, the doomed-seat shape E5's top-down NQ = 2 fixpoint
    showed).  The window keeps no run of 12 consecutive free tracks with
    the 8 in its middle, so `yield` gives back the block's current seat —
    all 8, every one under its own metal — the line reserves nothing (a
    removal), the block keeps its bus, and here the top keeps every bit
    too (it seats elsewhere)."""
    a, lines, notes, t = _wide(12)
    assert [(len(l["positions"]), l["seat_hit"], l["yielded"]) for l in lines] \
        == [(8, 8, 0)], lines
    assert lines[0]["seat"][2:] == (12, 19), lines[0]["seat"]
    assert t.detailed_result.num_unplaced == 24
    assert _placed_bits(t) == {"loc_0": (12, 0), "loc2_0": (12, 0),
                               "x_0": (8, 8)}, _placed_bits(t)
    a2, lines2, notes2, t2 = _wide(12, yield_seat=True)
    assert [(len(l["positions"]), l["seat_hit"], l["yielded"],
             l["yield_short"]) for l in lines2] == [(0, 0, 8, 0)], lines2
    assert any("8 of 8 reserved track(s) yielded to the cell's own seat "
               "(bundle 4 seg 0 needs 12 of 19 at u1; the top loses them) "
               "— nothing left to reserve here" in n for n in notes2), notes2
    assert t2.detailed_result.num_unplaced == 0
    assert _placed_bits(t2) == {"loc_0": (12, 12), "loc2_0": (12, 12),
                                "x_0": (8, 8)}
    assert "Success" in _cmd(t2, "check_design")
    # the command's surface: the token, the column, the summary, the header
    out = _cmd(a2, "derive_cell_layer_reserves yield")
    assert "yielding each cell its own seat" in out and "  yield" in out
    assert ("1 reservation(s) yielded 8 track(s) to a seat (the top loses "
            "them; 1 line(s) yielded whole reserve nothing — a removal)") in out
    assert "nothing to declare: the top takes no track the cells' seats " \
        "can spare" in out, out
    assert "unknown token" in _cmd(a2, "derive_cell_layer_reserves yields")


def test_the_pick_is_the_contiguous_run_and_the_current_seat_first(tmp_path):
    """The rule behind `yield` (measured on the SoC's core: with even ONE
    corridor track left in a 36-track window the 32-bit bus's local
    planner fled to a dead LOW layer; with none it routed clean): an
    abstract seat is one rectangle, so the test is the longest run of
    consecutive reserved-free tracks, not the count.  Given back first is
    the block's CURRENT seat — every reserved track under its own metal's
    span — then the nearest remaining one at a time until a run of `need`
    opens; the shortfall left with nothing reserved is the block's own."""
    pick = buda_cli.BudaSession._pick_yield
    s = buda_cli.BudaSession()
    t = [60.0 + 4 * i for i in range(19)]          # the window's tracks
    res = t[5:13]                                   # 8 reserved, mid-window
    cands = [(q, q) for q in res]
    own = {"own_seat": (4, 0, 12, 19), "own_window": (60.0, 132.0),
           "own_tracks": t[9:13]}                    # its metal on 4 of them
    give, short = pick(s, cands, own, t)
    # the 4 under its metal, then t[8] and t[7] (nearest its centre) —
    # after which t[7..18] is a run of 12
    assert set(give[:4]) == set(t[9:13]) and give[4:] == [t[8], t[7]], give
    assert short == 0
    # room enough: the run to the right of 4 reserved tracks holds 8
    assert pick(s, [(q, q) for q in t[5:9]],
                {"own_seat": (4, 0, 8, 19), "own_window": (60.0, 132.0),
                 "own_tracks": []}, t) == ([], 0)
    # a seat that cannot host its bus even unreserved: everything back,
    # the shortfall said
    give, short = pick(s, cands, {"own_seat": (4, 0, 20, 19),
                                  "own_window": (60.0, 132.0),
                                  "own_tracks": []}, t)
    assert sorted(give) == res and short == 1, (give, short)
    # no grid to read the tracks off: the count model, exact shortfall
    assert pick(s, cands, own, None) == (t[10:11], 0)   # nearest its centre
    # through a session: a whole line yielded is a removal, said, and
    # the file carries the `off` line for a reservation it replaces
    a, lines, notes, _t = _wide(12, yield_seat=False)
    a._pick_yield = lambda cands, own, tracks, fixed=(): (
        [k for k, _ in cands], 1)
    lines, notes, _ = a._derive_cell_layer_reserves(yield_seat=True)
    assert [l["positions"] for l in lines] == [[]], lines
    assert any("8 of 8 reserved track(s) yielded" in n
               and "nothing left to reserve here" in n for n in notes), notes
    # the seat's own shortfall is its own note: a give-back can be split
    # across the cell's line and its ancestors', and the shortfall is the
    # SEAT's, not any one line's
    assert any("still 1 short" in n and "cannot host its own bus" in n
               for n in notes), notes
    _cmd(a, "set_cell_layer_reserve top_cell M6 83,86")
    out = _cmd(a, f"derive_cell_layer_reserves yield file {tmp_path / 'y.buda'}")
    text = (tmp_path / "y.buda").read_text()
    assert "each cell's own seat yielded" in text
    assert "set_cell_layer_reserve top_cell M6 off" in text, text
    assert "1 removal line(s)" in out and "nothing to declare" in out, out


def test_yield_changes_nothing_where_the_seat_keeps_a_long_enough_run():
    """The 8-bit vehicle with a 4-bit top: 4 reserved tracks in a 19-track
    window leave a run of 8 free, so `yield` gives nothing back and the
    lines are the plain derivation's.  (With the 8-bit top the 8 reserved
    tracks sit mid-window and no run of 8 survives, so the whole line is
    yielded — although E5 measured that block moving its bus to another
    layer: the floor reads the current plan's seat, not the alternatives,
    which the docs say.)"""
    design = [c.replace("add_bus x[8]", "add_bus x[4]") for c in _DESIGN]
    s = buda_cli.BudaSession()
    s.no_viz = True
    _quiet(s, *design, "run_nuts", "run_detailed_nuts")
    plain, notes, _ = s._derive_cell_layer_reserves()
    yld, notes2, _ = s._derive_cell_layer_reserves(yield_seat=True)
    assert [l["positions"] for l in yld] == [l["positions"] for l in plain]
    assert len(plain[0]["positions"]) == 4 and plain[0]["seat_hit"] == 4
    assert all(l["yielded"] == 0 for l in yld) and notes2 == notes
    out = _cmd(s, "derive_cell_layer_reserves yield")
    assert "0 reservation(s) yielded 0 track(s)" in out, out
    s8 = _session("run_nuts", "run_detailed_nuts")
    yld8, notes8, _ = s8._derive_cell_layer_reserves(yield_seat=True)
    assert [(l["positions"], l["yielded"]) for l in yld8] == [([], 8)]


_BAND_INNER = "set_cell_layer_cap inner M6 -min M5"


def _seat_runs(t, cell, lname):
    """Per occurrence of `cell`, the longest run of consecutive signal
    tracks its worst seat window leaves free of the reservations IN FORCE
    there — `_effective_reserves`, the set the cell-local solve keeps free
    (own UNION inherited).  Computed off the routed session rather than
    off the derivation, so it is an independent check of what the yield
    was supposed to buy: {inst: (free run, need, window tracks)}."""
    lid = t._layer_name_map[lname]
    eff, _src, _clone = t._effective_reserves(cell)
    comps = {c.name: c for c in t.bdb.all_components()}
    fr, _rot = t._reserve_frames(cell)
    out = {}
    for r in t._layer_demand():
        if r["cell"] != cell or r["layer"] != lid or not r["own_seat"]:
            continue
        d, od = comps[r["inst"]], fr[r["inst"]]
        lo, hi = r["own_window"]
        blocked = [a for a in t._reserve_abs_of(eff.get(lid, ()), od, d, lid)
                   if lo - 1e-6 <= a <= hi + 1e-6]
        tk = t._seat_tracks(r)
        out[r["inst"]] = (buda_cli.BudaSession._largest_free_run(tk, blocked),
                          int(r["own_seat"][2]), len(tk))
    return out


def _derived_text(s, yield_seat):
    lines, notes, _ = s._derive_cell_layer_reserves(yield_seat=yield_seat)
    return [f"set_cell_layer_reserve {l['cell']} {l['layer_name']} "
            + ",".join(fmt_pos(q) for q in l["positions"])
            for l in lines if l["positions"]], lines, notes


def test_the_yield_sees_the_union_of_own_and_inherited_at_every_occurrence():
    """Codex on #940, the two findings that share one cause.  What a
    cell-local solve keeps free is `_effective_reserves` — the cell's OWN
    line UNION every ancestor corridor projected into its frame — so that
    union is what fragments its seat.  Two ways of testing less than the
    union were measured here:

    (1) an ancestor track has a DIFFERENT image at each occurrence of the
        nested cell (that is why the inheritance unions over occurrences),
        and keying the candidates by (ancestor, track) kept only the
        first, so the run test saw 8 of the 16 images; and
    (2) the cell's own tracks and the inherited images were tested
        SEPARATELY, so each half could find a long enough run while the
        union left none.

    The vehicle is the nested design with its second top instance
    MIRRORED, so the two occurrences' images genuinely differ, and the
    nested template banded to [M5..M6] so its bus cannot answer a
    reservation by fleeing to a free layer (unbanded it moves to M4 and
    the seat stops existing, which is why no test reached this pass).

    Measured: `inner`'s 8-bit bus has a 19-track window at each of its two
    occurrences.  Under the plain derivation both are fragmented below
    what the bus needs; under the yield both clear."""
    s0, _ = _flipped(["set_bottom_up *"], _BAND_INNER)
    plain, _pl, _pn = _derived_text(s0, False)
    t0, _o0 = _flipped(["set_bottom_up *"], _BAND_INNER, *plain)
    before = _seat_runs(t0, "inner", "M6")
    assert before == {"u1/c": (7, 8, 19), "u2/c": (6, 8, 19)}, before
    text, lines, notes = _derived_text(s0, True)
    t, _out = _flipped(["set_bottom_up *"], _BAND_INNER, *text)
    after = _seat_runs(t, "inner", "M6")
    assert after == {"u1/c": (19, 8, 19), "u2/c": (13, 8, 19)}, after
    for inst, (run, need, _n) in after.items():
        assert run >= need, (inst, run, need)
    # the give-back is split across the cell's own line and the
    # ANCESTOR's, each said with the seat it served, and the ancestor is
    # charged by name (the core has no line of its own on the SoC — its
    # stranded bits were the cluster's corridor crossing its seat)
    byc = {(l["cell"], l["layer_name"]): l for l in lines}
    assert byc[("inner", "M6")]["yielded"] == 7
    assert byc[("top_cell", "M6")]["yielded"] == 7
    assert len(byc[("top_cell", "M6")]["positions"]) == 9, byc
    assert any("top_cell M6: 7 of 16 reserved track(s) yielded to nested "
               "inner's own seat" in n and "16 inherited track(s) in its "
               "window" in n for n in notes), notes
    assert any("inner M6: 7 of 8 reserved track(s) yielded to the cell's "
               "own seat" in n for n in notes), notes
    # and the counts the table prints follow the line it reduced
    assert byc[("top_cell", "M6")]["seat_hit"] == 1
    assert byc[("inner", "M6")]["seat_hit"] == 1


def test_an_out_of_scope_ancestors_corridor_is_blocked_but_not_yielded():
    """Codex on #940: `_report_cell_layer_reserves` removes a held
    reservation only when its cell is IN SCOPE, so an ancestor outside the
    scope keeps its corridor and still crosses the nested cell's seat when
    the next round routes.  The walk must therefore read the map IN FORCE
    — what is held, with the derived lines in the scoped cells' place —
    not the derived lines alone, which came back with no inheritance at
    all and yielded nothing to the very seat the policy is for.

    Scoped to `inner`, the enclosing corridor is real and un-givable: it
    still narrows the run test (so the cell's own line gives way to it),
    and no line is invented for the cell that was not asked for."""
    s0, _ = _flipped(["set_bottom_up *"], _BAND_INNER)
    text, _l, _n = _derived_text(s0, False)
    top = [t for t in text if t.startswith("set_cell_layer_reserve top_cell M6")]
    assert len(top) == 1, text
    s, _o = _flipped(["set_bottom_up *"], _BAND_INNER, *top)
    lines, notes, scope = s._derive_cell_layer_reserves(cells=["inner"],
                                                        yield_seat=True)
    assert scope == ["inner"]
    assert {l["cell"] for l in lines} == {"inner"}
    m6 = [l for l in lines if l["layer_name"] == "M6"][0]
    # The held corridor is not this derivation's to move, so the cell's
    # OWN line gives way to it — all 8 tracks — and what the corridor
    # still costs the seat is SAID rather than left as a reservation
    # covering it.  (Reading the derived lines alone instead of the map in
    # force, the ancestor is invisible: the line keeps all 8 tracks,
    # reports seat_hit 8, yields nothing and says nothing.)
    assert (m6["positions"], m6["yielded"], m6["yield_short"]) == ([], 8, 1)
    assert m6["seat_hit"] == 0
    assert any("still 1 short at u1/c" in n
               and "with every yieldable track in its window given back"
               in n for n in notes), notes
    assert not any("top_cell" in n for n in notes), notes
    # and the seat really is still short under those lines: a corridor the
    # derivation cannot move is one the block has to live with
    txt = [f"set_cell_layer_reserve {l['cell']} {l['layer_name']} "
           + ",".join(fmt_pos(q) for q in l["positions"])
           for l in lines if l["positions"]]
    t, _o2 = _flipped(["set_bottom_up *"], _BAND_INNER, *top, *txt)
    runs = _seat_runs(t, "inner", "M6")
    assert all(run < need for run, need, _n in runs.values()), runs
