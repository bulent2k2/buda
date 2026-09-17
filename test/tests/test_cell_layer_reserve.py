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
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

from test_layer_demand import _DESIGN, _cmd, _quiet  # noqa: E402

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
            "instance(s); the top uses 8..8 of them per instance; own metal "
            "on reserved tracks: 0") in out, out
    # the cell-local solve and the reference DNUTS view both said so
    # (captured by _quiet; re-run the enforcement sites' prints directly)
    fp = s._build_cell_local_floorplan("u1")
    n = s._install_cell_reserve_keepouts(fp, "top_cell", "u1")
    assert n == 8 and len(fp.get_keepout_zones()) == 8
    z = fp.get_keepout_zones()[0]
    assert set(z.layer_ids) == {6} and z.bbox.x1 == 0 and z.bbox.x2 == 600
    ko = s._bu_reserve_dnuts_keepouts({w.input.original_bundle.id
                                       for w in s.bundles
                                       if w.input.original_bundle.cell_context})
    assert ko and all(k[0] == 6 for k in ko)


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
