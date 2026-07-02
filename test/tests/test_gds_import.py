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

"""GDSII import (Phase G1) — see docs/internal/gds_oa_interchange.md.

Tests generate their binary GDS inputs deterministically with the Phase-G0
writer (tools/gds_build.py; zeroed timestamps), then import via
`BDB.import_gds` / the `import_gds` CLI command and assert on the resulting
cell/component tables. GDS is binary, so no blobs are checked in.
"""

import contextlib
import io

import pytest

import buda
import buda_cli
import bdb_serialize
from gds_build import GdsBuilder


def _basic_lib(path):
    """leaf(5x3) ; mid = bar(20x2) + 2x2 AREF of leaf ; top = mid + mid@90 + leaf(mirrored)."""
    b = GdsBuilder(dbu_um=0.001)
    b.structure("leaf").boundary(10, 0, [(0, 0), (5, 0), (5, 3), (0, 3)])
    b.structure("mid") \
     .boundary(10, 0, [(0, 0), (20, 0), (20, 2), (0, 2)]) \
     .aref("leaf", (0, 4), cols=2, rows=2, col_pitch=(8, 0), row_pitch=(0, 5))
    b.structure("top") \
     .sref("mid", (10, 10), inst_name="m0") \
     .sref("mid", (10, 40), angle=90) \
     .sref("leaf", (100, 0), mirror=True) \
     .text(63, 0, (1, 1), "net_a")
    return b.write(path)


def _import(tmp_path, gds_path):
    db = buda.BDB(str(tmp_path / "g.bdb"))
    return db, db.import_gds(str(gds_path))


def test_cells_get_recursive_footprints(tmp_path):
    db, st = _import(tmp_path, _basic_lib(tmp_path / "t.gds"))
    assert (st.n_structures, st.n_cells) == (3, 3)
    cells = {c.name: (c.width, c.height) for c in db.all_cells()}
    assert cells["leaf"] == (5.0, 3.0)
    # mid's footprint includes the leaf array it references, not just its bar.
    assert cells["mid"] == (20.0, 12.0)


def test_hierarchy_elaborates_with_transforms(tmp_path):
    db, st = _import(tmp_path, _basic_lib(tmp_path / "t.gds"))
    assert list(st.tops) == ["top"]
    comps = {c.name: c for c in db.all_components()}
    # The top structure is the die, NOT a component: its refs elaborate as
    # unprefixed depth-0 roots — exactly how import_verilog elaborates the top
    # module — so the geometry-only merge matches placements by name.
    assert st.n_components == len(comps) == 11
    assert "top" not in comps
    # PROPVALUE names the instance; anonymous refs synthesize <struct>_<n>.
    assert comps["m0"].cell == "mid"
    assert comps["mid_0"].cell == "mid"
    # Depths grow along the dotted path; leaves are marked.
    assert comps["m0"].depth == 0 and not comps["m0"].is_leaf
    assert comps["m0/leaf_0"].depth == 1 and comps["m0/leaf_0"].is_leaf
    # AREF 2x2 expanded at its pitches (absolute µm bboxes).
    l3 = comps["m0/leaf_3"]
    assert (l3.x1, l3.y1, l3.x2, l3.y2) == (18.0, 19.0, 23.0, 22.0)
    # 90-degree rotation swaps the placed bbox dims (20x12 -> 12x20).
    m2 = comps["mid_0"]
    assert (m2.x2 - m2.x1, m2.y2 - m2.y1) == (12.0, 20.0)
    # Mirror about X reflects the leaf below its origin.
    lm = comps["leaf_0"]
    assert (lm.y1, lm.y2) == (-3.0, 0.0)
    # A single top's extent becomes the die (the DEF DIEAREA analogue).
    assert float(db.meta_get("die_w")) == 107.0
    assert float(db.meta_get("die_h")) == 63.0


def test_units_scale_to_um(tmp_path):
    # Same geometry, 10nm dbu: µm coordinates must come out identical.
    b = GdsBuilder(dbu_um=0.01)
    b.structure("leaf").boundary(10, 0, [(0, 0), (5, 0), (5, 3), (0, 3)])
    db, _ = _import(tmp_path, b.write(tmp_path / "u.gds"))
    assert {c.name: (c.width, c.height)
            for c in db.all_cells()} == {"leaf": (5.0, 3.0)}


def test_texts_counted_not_footprinted(tmp_path):
    # A far-away label must not grow the cell footprint (G2 consumes labels).
    b = GdsBuilder()
    b.structure("leaf") \
     .boundary(10, 0, [(0, 0), (5, 0), (5, 3), (0, 3)]) \
     .text(63, 0, (500, 500), "net_far")
    db, st = _import(tmp_path, b.write(tmp_path / "t.gds"))
    assert st.n_texts == 1
    assert {c.name: (c.width, c.height)
            for c in db.all_cells()} == {"leaf": (5.0, 3.0)}


def test_path_width_grows_footprint(tmp_path):
    # A stroked PATH's footprint is centerline ± WIDTH/2 (Codex #143 P2):
    # a 10µm-wide horizontal wire is 100x10 with butt caps (PATHTYPE 0)...
    b = GdsBuilder()
    b.structure("wire").path(3, 0, [(0, 0), (100, 0)], width_um=10)
    db, _ = _import(tmp_path, b.write(tmp_path / "p.gds"))
    assert {c.name: (c.width, c.height)
            for c in db.all_cells()} == {"wire": (100.0, 10.0)}


def test_path_end_caps_extend(tmp_path):
    # ...and PATHTYPE 2 (square caps) extends both ends by WIDTH/2.
    b = GdsBuilder()
    b.structure("wire").path(3, 0, [(0, 0), (100, 0)], width_um=10, pathtype=2)
    db, _ = _import(tmp_path, b.write(tmp_path / "p.gds"))
    assert {c.name: (c.width, c.height)
            for c in db.all_cells()} == {"wire": (110.0, 10.0)}


def test_verilog_pairing_preserves_gds_placement(tmp_path):
    # The documented geometry-only merge: import_gds then import_verilog.
    # GDS roots are unprefixed (the top structure is the die, not a component),
    # so the Verilog UPSERT matches by name and keeps the GDS placement while
    # attaching pins/nets to the SAME components (Codex #143 P2).
    b = GdsBuilder()
    b.structure("blkA").boundary(10, 0, [(0, 0), (200, 0), (200, 400), (0, 400)])
    b.structure("blkB").boundary(10, 0, [(0, 0), (200, 0), (200, 400), (0, 400)])
    b.structure("chip") \
     .sref("blkA", (0, 0), inst_name="A") \
     .sref("blkB", (600, 800), inst_name="B")
    gds = b.write(tmp_path / "chip.gds")
    v = tmp_path / "chip.v"
    v.write_text(
        "module blkA(p); output p; endmodule\n"
        "module blkB(p); input p; endmodule\n"
        "module chip;\n"
        "  wire n1;\n"
        "  blkA A(.p(n1));\n"
        "  blkB B(.p(n1));\n"
        "endmodule\n")

    db = buda.BDB(str(tmp_path / "m.bdb"))
    db.import_gds(str(gds))
    db.import_verilog(str(v))
    comps = {c.name: c for c in db.all_components()}
    # Placement survived the merge (no -1 rows for the netlist endpoints)...
    assert (comps["A"].x1, comps["A"].y1, comps["A"].x2, comps["A"].y2) == \
        (0.0, 0.0, 200.0, 400.0)
    assert (comps["B"].x1, comps["B"].y1) == (600.0, 800.0)
    # ...and the Verilog pins landed on those same placed components.
    assert db.pins_by_comp(comps["A"].id)
    assert db.pins_by_comp(comps["B"].id)


def test_multiple_tops_all_elaborate(tmp_path):
    b = GdsBuilder()
    b.structure("leaf").boundary(10, 0, [(0, 0), (2, 0), (2, 2), (0, 2)])
    b.structure("a").sref("leaf", (0, 0))
    b.structure("b").sref("leaf", (10, 10))
    db, st = _import(tmp_path, b.write(tmp_path / "t.gds"))
    assert sorted(st.tops) == ["a", "b"]
    names = {c.name for c in db.all_components()}
    # Both tops' children elaborate as roots; the name collision across tops
    # is qualified with the top name (+ a warning).
    assert {"leaf_0", "b_leaf_0"} <= names
    assert any("collision" in w for w in st.warnings)


def test_undefined_ref_warns_and_continues(tmp_path):
    b = GdsBuilder()
    b.structure("top").sref("ghost", (0, 0)) \
     .boundary(10, 0, [(0, 0), (4, 0), (4, 4), (0, 4)])
    db, st = _import(tmp_path, b.write(tmp_path / "t.gds"))
    assert any("undefined structure 'ghost'" in w for w in st.warnings)
    assert not db.all_components()      # the ghost ref was the only child


def test_bad_files_raise(tmp_path):
    p = tmp_path / "bad.gds"
    p.write_bytes(b"not a gds file at all")
    db = buda.BDB(str(tmp_path / "g.bdb"))
    with pytest.raises(RuntimeError):
        db.import_gds(str(p))
    # Truncated: cut a valid stream before ENDLIB.
    full = _basic_lib(tmp_path / "ok.gds")
    data = open(full, "rb").read()
    q = tmp_path / "trunc.gds"
    q.write_bytes(data[:len(data) - 10])
    with pytest.raises(RuntimeError):
        db.import_gds(str(q))


def test_bdb_sql_roundtrip(tmp_path):
    db, _ = _import(tmp_path, _basic_lib(tmp_path / "t.gds"))
    before = sorted((c.name, c.cell, c.depth, c.x1, c.y1, c.x2, c.y2)
                    for c in db.all_components())
    del db
    sql = str(tmp_path / "g.bdb.sql")
    bdb_serialize.dump(str(tmp_path / "g.bdb"), sql)
    db2 = buda.BDB(bdb_serialize.load(sql, str(tmp_path / "rebuilt.bdb")))
    after = sorted((c.name, c.cell, c.depth, c.x1, c.y1, c.x2, c.y2)
                   for c in db2.all_components())
    assert after == before and before


def test_cli_import_and_route(tmp_path):
    # End-to-end: import a GDS floorplan via the CLI, project its depth-1
    # blocks into the Floorplan, and run the flat pipeline over them.
    b = GdsBuilder()
    b.structure("blkA").boundary(10, 0, [(0, 0), (200, 0), (200, 400), (0, 400)])
    b.structure("blkB").boundary(10, 0, [(0, 0), (200, 0), (200, 400), (0, 400)])
    b.structure("chip") \
     .sref("blkA", (0, 0), inst_name="A") \
     .sref("blkB", (600, 800), inst_name="B")
    gds = b.write(tmp_path / "chip.gds")

    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        for c in ["source flow/rnr/mix_tracks.buda",
                  f"open_bdb {tmp_path / 'chip.bdb'}",
                  f"import_gds {gds}",
                  "add_blocks_from_bdb 0",
                  "add_bus d[8] A.p B.p",
                  "run_bundler", "generate_topologies", "run_planner 3",
                  "run_nuts", "run_detailed_nuts"]:
            s.do_command(c)
    out = buf.getvalue()
    assert "[import_gds] 3 structure(s)" in out
    assert s.nuts_result.num_overlaps == 0
    assert s.detailed_result.num_unplaced == 0
    # The imported design routed AND persisted like any other BDB design.
    snap = s.bdb.route_snapshot()
    assert snap.stage == "detailed_nuts" and snap.n_net_segments >= 1
