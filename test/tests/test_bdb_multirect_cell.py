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

"""A MULTI-RECT footprint on a BDB CELL — schema v30, `set_cell_rects`.

teg_multirect_status.md limitation 5: no hier declaration produced a
multi-rect block.  A BDB cell was one `width x height` box, so
`derive_busterms` wrote empty rects and every BDB->Floorplan projection was
`add_block(bbox)` — which made the whole multi-rect / TEG machinery
(`teg_mode`, the OVER connection metal, `TEG_OPEN`, the BUDA-1907 thru
census) script-declared only.

The footprint is attached to the CELL rather than the component because that
is what it is a property OF (LEF's `SIZE` is a MACRO property): one
declaration governs every instance, the rects are CELL-LOCAL so a move can
never stale them, and a rotated instance gets them transformed with it.

These tests pin the storage, the declaration validation, the five
BDB->Floorplan projections and the busterm stamp; the end-to-end hier route
is `test_flow_scripts.py::test_teg_hier_cell_flow_routes_clean`.
"""
import contextlib
import io

import pytest

import buda
import buda_cli
import buda_db


def _session(cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in cmds:
        with contextlib.redirect_stdout(io.StringIO()):
            s.do_command(c)
    return s


def _run(cmds):
    """Run commands capturing stdout; return (session, output)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in cmds:
            s.do_command(c)
    return s, buf.getvalue()


def _check(s, stage):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        verdict = s._check_design(stage)
    return verdict, buf.getvalue()


_CELLS = [
    "open_bdb :memory:",
    "add_cell dsp 400 400",
    "add_cell drv 100 80",
]


# ── storage ────────────────────────────────────────────────────────────────

def test_cell_rects_round_trip_through_the_bdb(tmp_path):
    db = buda_db.BDB(str(tmp_path / "a.bdb"))
    db.add_cell("dsp", 400, 400)
    assert db.cell_rects("dsp") == []
    assert db.cell_teg_mode("dsp") == "THRU"
    assert db.multirect_cells() == []

    db.set_cell_rects("dsp", [(0, 0, 400, 100), (0, 0, 100, 400)], "OVER")
    assert [tuple(r) for r in db.cell_rects("dsp")] == [
        (0.0, 0.0, 400.0, 100.0), (0.0, 0.0, 100.0, 400.0)]
    assert db.cell_teg_mode("dsp") == "OVER"
    assert db.multirect_cells() == ["dsp"]
    assert {c.name: c.teg_mode for c in db.all_cells()}["dsp"] == "OVER"

    # Reopening reads the same rows (they are persisted, not session state).
    del db
    db2 = buda_db.BDB(str(tmp_path / "a.bdb"))
    assert db2.multirect_cells() == ["dsp"]
    assert db2.cell_teg_mode("dsp") == "OVER"

    # An empty list clears the footprint (back to the single bbox).
    db2.set_cell_rects("dsp", [], "THRU")
    assert db2.multirect_cells() == []


def test_set_cell_rects_replaces_rather_than_appends(tmp_path):
    db = buda_db.BDB(str(tmp_path / "b.bdb"))
    db.add_cell("c", 200, 200)
    db.set_cell_rects("c", [(0, 0, 200, 100), (0, 100, 200, 200)])
    db.set_cell_rects("c", [(0, 0, 100, 200), (100, 0, 200, 200)], "OVER")
    assert [tuple(r) for r in db.cell_rects("c")] == [
        (0.0, 0.0, 100.0, 200.0), (100.0, 0.0, 200.0, 200.0)]


def test_undeclared_cell_and_degenerate_rect_are_refused(tmp_path):
    db = buda_db.BDB(str(tmp_path / "c.bdb"))
    with pytest.raises(RuntimeError, match="cell not defined"):
        db.set_cell_rects("nope", [(0, 0, 1, 1), (1, 1, 2, 2)])
    db.add_cell("c", 10, 10)
    with pytest.raises(RuntimeError, match="degenerate"):
        db.set_cell_rects("c", [(0, 0, 10, 0), (0, 0, 10, 10)])


# ── declaration (the .buda command) ────────────────────────────────────────

def test_command_stores_the_footprint_and_reports_it():
    s, out = _run(_CELLS + [
        "set_cell_rects dsp rect 0 0 400 100 rect 0 0 100 400 teg_mode over"])
    assert "2 rects (teg_mode over)" in out
    assert s.bdb.multirect_cells() == ["dsp"]
    assert s.bdb.cell_teg_mode("dsp") == "OVER"


def test_command_defaults_to_thru_and_off_clears():
    s, _ = _run(_CELLS + [
        "set_cell_rects dsp rect 0 0 400 100 rect 0 0 100 400"])
    assert s.bdb.cell_teg_mode("dsp") == "THRU"
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("set_cell_rects dsp off")
    assert s.bdb.multirect_cells() == []


@pytest.mark.parametrize("line,needle", [
    # A union that is not the cell box is the split-brain the command exists
    # to prevent: placement reads the component bbox (which comes from the
    # cell SIZE) while routing would read a different shape.
    ("set_cell_rects dsp rect 0 0 400 100 rect 0 0 100 300",
     "but the cell is"),
    # One rect declares nothing `add_cell` has not already said, and teg_mode
    # on a single rectangle has no referent.
    ("set_cell_rects dsp rect 0 0 400 400", "at least 2"),
    ("set_cell_rects nope rect 0 0 1 1 rect 1 1 2 2", "is not defined"),
    ("set_cell_rects dsp rect 0 0 400 100 rect 0 0 100 400 teg_mode sideways",
     "unknown option 'sideways'"),
    ("set_cell_rects dsp rect 0 0 400", "missing <y2>"),
    ("set_cell_rects dsp rect 0 0 400 100 rect 0 0 400 100", "duplicates rect"),
    ("set_cell_rects dsp rect 0 0 400 0 rect 0 0 100 400", "degenerate"),
])
def test_bad_declarations_stop_the_flow(line, needle):
    s = _session(_CELLS)
    buf = io.StringIO()
    with pytest.raises(SystemExit):
        with contextlib.redirect_stdout(buf):
            s.do_command(line)
    assert needle in buf.getvalue(), buf.getvalue()


# ── projection into the routing floorplan ──────────────────────────────────

_HIER = _CELLS + [
    "add_cell unit 700 500",
    "set_cell_rects dsp rect 0 0 400 100 rect 0 0 100 400 teg_mode over",
    "add_inst_to_cell unit dsp_i dsp 30 30",
    "add_inst_to_cell unit drv_i drv 550 200",
    "add_inst u0 unit - 100 100",
    "add_inst u1 unit - 100 700",
]


def test_add_blocks_from_bdb_projects_the_rects_and_the_mode():
    s = _session(_HIER + ["derive_busterms 1",
                          "add_blocks_from_bdb 0",
                          "add_blocks_from_bdb 1 skip"])
    # dsp_i of u0 sits at unit-local (30,30) under a unit at (100,100).
    rects = [tuple(r) for r in s.fp.get_block_rects("u0/dsp_i")]
    assert rects == [(130, 130, 530, 230), (130, 130, 230, 530)]
    assert s.fp.get_block_teg_mode("u0/dsp_i") == buda.TegMode.OVER
    # Every instance inherits the CELL's footprint.
    assert [tuple(r) for r in s.fp.get_block_rects("u1/dsp_i")] == [
        (130, 730, 530, 830), (130, 730, 230, 1130)]
    # A single-bbox cell is untouched.
    assert s.fp.get_block_rects("u0/drv_i") == []


def test_cell_local_frame_carries_the_rects_in_local_coordinates():
    s = _session(_HIER + ["derive_busterms 1",
                          "add_blocks_from_bdb 0",
                          "add_blocks_from_bdb 1 skip"])
    fp = s._build_cell_local_floorplan("u1")
    rects = [tuple(r) for r in fp.get_block_rects("dsp_i")]
    assert rects == [(30, 30, 430, 130), (30, 30, 130, 430)]
    assert fp.get_block_teg_mode("dsp_i") == buda.TegMode.OVER


def test_depth_frame_carries_the_rects():
    s = _session(_HIER + ["derive_busterms 1",
                          "add_blocks_from_bdb 0",
                          "add_blocks_from_bdb 1 skip"])
    fp = s._build_bdb_floorplan(1)
    assert [tuple(r) for r in fp.get_block_rects("u0/dsp_i")] == [
        (130, 130, 530, 230), (130, 130, 230, 530)]


def test_rotating_the_leaf_itself_transforms_its_rects():
    # The whole reason the footprint is CELL-LOCAL: an instance's rects are
    # the cell's, transformed by its orientation over the cell box.  A leaf
    # transformed DIRECTLY composes its own orient token (a childless
    # subtree — BDB::rotate_comp), which is what the projection reads, so the
    # L's arms swap.
    s = _session(_HIER + ["rotate_comp u1/dsp_i 90",
                          "derive_busterms 1",
                          "add_blocks_from_bdb 0",
                          "add_blocks_from_bdb 1 skip"])
    c = {r.name: r for r in s.bdb.all_components()}["u1/dsp_i"]
    assert c.orient == "W"          # the token BDB composed
    got = [tuple(r) for r in s.fp.get_block_rects("u1/dsp_i")]
    # The rects must still union to the component's own bbox — the invariant
    # that keeps routing and placement one shape.
    assert (min(r[0] for r in got), min(r[1] for r in got),
            min(-r[2] for r in got) * -1, min(-r[3] for r in got) * -1) == \
        (round(c.x1), round(c.y1), round(c.x2), round(c.y2))
    # …and they are the ROTATED pair, not the upright one.
    upright = [(round(c.x1), round(c.y1), round(c.x1) + 400, round(c.y1) + 100),
               (round(c.x1), round(c.y1), round(c.x1) + 100, round(c.y1) + 400)]
    assert got != upright
    assert sorted((r[2] - r[0], r[3] - r[1]) for r in got) == [(100, 400),
                                                               (400, 100)]


def test_rotating_a_container_says_the_footprint_cannot_follow():
    # BUDA-1918.  rotate_comp/flip_comp rewrite descendant BBOXES and leave
    # their orientation tokens alone — deliberate, because for a single-bbox
    # instance the bbox rewrite IS the transform.  A multi-rect footprint is
    # geometry the bbox does NOT carry, so it stays upright while the
    # instance turns.  Nothing in the row records that a transform happened,
    # so the only honest place to say it is here.
    s, out = _run(_HIER + ["rotate_comp u1 90"])
    assert "BUDA-1918" in out, out
    assert "u1/dsp_i" in out
    c = {r.name: r for r in s.bdb.all_components()}["u1/dsp_i"]
    assert c.orient == "N"          # the token really is untouched
    # A design with no multi-rect cell is silent.
    plain = [c for c in _HIER if not c.startswith("set_cell_rects")]
    _, quiet = _run(plain + ["rotate_comp u1 90"])
    assert "BUDA-1918" not in quiet


def test_a_stale_footprint_is_refused_at_the_projection():
    # BUDA-1919.  `set_cell_rects` enforces "the rects union to the cell box"
    # at declaration, but a later `resize_cell` breaks it: it rewrites every
    # instance's bbox and keeps the rects.  `add_block_rects` derives the block's bbox
    # FROM the rects, so projecting a stale footprint would hand the routing
    # frame a different shape from the one placement, HPWL and overlap
    # checking read.  Refused at the projection, where every frame passes,
    # rather than trusted to N mutation sites.
    s, out = _run(_HIER + ["resize_cell dsp 500 500",   # rects now stale
                           "derive_busterms 1",
                           "add_blocks_from_bdb 0",
                           "add_blocks_from_bdb 1 skip"])
    assert "BUDA-1919" in out, out
    assert "cell 'dsp'" in out
    # The block is still there — as its single bbox, which is the honest
    # projection of what the BDB now says.
    assert s.fp.get_block_rects("u0/dsp_i") == []
    assert s.fp.has_block("u0/dsp_i")
    # Said ONCE per cell, not once per instance.
    assert out.count("BUDA-1919") == 1


def test_derive_busterms_stamps_the_rects_and_the_mode():
    # `BustermRow.rects` has carried an optional multi-rect JSON since v1 and
    # BustermGen wrote it EMPTY, because a cell had no rects to write.
    s, out = _run(_HIER + ["derive_busterms 1"])
    assert "carry a multi-rect footprint" in out
    rows = {b.hier_path: b for b in s.bdb.all_busterms()}
    assert rows["u0/dsp_i"].rects == "[[130,130,530,230],[130,130,230,530]]"
    assert rows["u0/dsp_i"].teg_mode == "OVER"
    assert rows["u0/drv_i"].rects == ""


# ── the resume seam ────────────────────────────────────────────────────────

def test_the_footprint_survives_a_hier_stage_resume(tmp_path):
    """A HIER stage-resume HOLDS the construction commands (a replayed
    `add_inst` is a duplicate-instance error), so the footprint has to come
    back from the CHECKPOINT — which is exactly why it is stored rather than
    re-declared.  Session 1 builds and routes; session 2 declares the stack,
    re-projects the blocks, `load_pipeline`s and re-solves DNUTS.
    """
    ck = str(tmp_path / "ck.bdb")
    tracks = ["def_track_pattern 4 0 (SIGNAL 2 2)x8",
              "def_track_pattern 5 0 (SIGNAL 2 2)x8"]
    layers = ["def_layer 4 M4 H TOP 0", "def_layer 5 M5 V TOP 0"]
    build = layers + tracks + [
        f"open_bdb {ck}",
        "add_cell dsp 400 400", "add_cell drv 100 80", "add_cell unit 700 500",
        "set_cell_rects dsp rect 0 0 400 100 rect 0 0 100 400 teg_mode over",
        "add_inst_to_cell unit dsp_i dsp 30 30",
        "add_inst_to_cell unit drv_i drv 550 200",
        "add_inst u0 unit - 100 100",
        "derive_busterms 1",
        "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
        "bdb_net_mode on",
        "add_bus d0[4] u0/drv_i.out u0/dsp_i.in",
        "run_hier_bundler depth 1", "generate_hier_topologies",
        "run_planner hier 5", "run_nuts", "run_detailed_nuts",
    ]
    s1 = _session(build)
    built = (len(s1.detailed_result.net_segments), s1.detailed_result.num_unplaced)
    assert built[1] == 0 and built[0] > 0

    # Session 2: the stack + the projections + load_pipeline.  NO
    # set_cell_rects, no add_cell, no add_inst.
    s2 = _session(layers + tracks + [
        f"open_bdb {ck}",
        "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
        "load_pipeline expanded", "run_detailed_nuts",
    ])
    # The footprint came back with the checkpoint, so the resumed frame is
    # the same shape…
    assert [tuple(r) for r in s2.fp.get_block_rects("u0/dsp_i")] == [
        (130, 130, 530, 230), (130, 130, 230, 530)]
    assert s2.fp.get_block_teg_mode("u0/dsp_i") == buda.TegMode.OVER
    # …and the routed endpoint is reproduced exactly.
    assert (len(s2.detailed_result.net_segments),
            s2.detailed_result.num_unplaced) == built
    verdict, out = _check(s2, "dnuts")
    assert "TEG_OPEN" not in out, out


# ── the control: a design declaring none is untouched ──────────────────────

def test_a_design_with_no_cell_rects_projects_exactly_as_before():
    plain = [c for c in _HIER if not c.startswith("set_cell_rects")]
    s = _session(plain + ["derive_busterms 1",
                          "add_blocks_from_bdb 0",
                          "add_blocks_from_bdb 1 skip"])
    assert s.bdb.multirect_cells() == []
    assert s.fp.get_block_rects("u0/dsp_i") == []
    assert s.fp.get_block_teg_mode("u0/dsp_i") == buda.TegMode.THRU
    rows = {b.hier_path: b for b in s.bdb.all_busterms()}
    assert rows["u0/dsp_i"].rects == ""
