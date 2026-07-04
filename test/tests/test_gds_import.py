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
import sqlite3

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
    # 90-degree rotation swaps the placed bbox dims (20x12 -> 12x20) AND records
    # the orientation token (v13) so export can re-emit it faithfully.
    m2 = comps["mid_0"]
    assert (m2.x2 - m2.x1, m2.y2 - m2.y1) == (12.0, 20.0)
    assert m2.orient == "W"                       # 90 deg CCW
    assert comps["m0"].orient == "N"              # unrotated stays identity
    # Mirror about X reflects the leaf below its origin.
    lm = comps["leaf_0"]
    assert (lm.y1, lm.y2) == (-3.0, 0.0)
    assert lm.orient == "FN"                       # mirror-about-X, angle 0
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
    # A far-away label must not grow the cell footprint — and since it lands
    # outside every component, net recovery skips it with a warning.
    b = GdsBuilder()
    b.structure("leaf") \
     .boundary(10, 0, [(0, 0), (5, 0), (5, 3), (0, 3)]) \
     .text(63, 0, (500, 500), "net_far")
    db, st = _import(tmp_path, b.write(tmp_path / "t.gds"))
    assert st.n_texts == 1
    assert st.n_labels_skipped == 1 and st.n_nets == 0
    assert any("outside every component" in w for w in st.warnings)
    assert {c.name: (c.width, c.height)
            for c in db.all_cells()} == {"leaf": (5.0, 3.0)}


def test_labels_recover_nets_and_pins(tmp_path):
    # Phase G2: each TEXT string is a net; the pin lands on the component
    # containing the label. The `label_layers` filter restricts which GDS
    # layers carry labels (layer 9 below is excluded).
    b = GdsBuilder()
    b.structure("blkA").boundary(10, 0, [(0, 0), (200, 0), (200, 400), (0, 400)])
    b.structure("blkB").boundary(10, 0, [(0, 0), (200, 0), (200, 400), (0, 400)])
    b.structure("chip") \
     .sref("blkA", (0, 0), inst_name="A") \
     .sref("blkB", (600, 800), inst_name="B") \
     .text(63, 0, (100, 200), "n_data") \
     .text(63, 0, (700, 1000), "n_data") \
     .text(9, 0, (100, 100), "not_a_label")
    gds = b.write(tmp_path / "c.gds")
    db = buda.BDB(str(tmp_path / "c.bdb"))
    st = db.import_gds(str(gds), [63])
    assert (st.n_nets, st.n_pins, st.n_labels_skipped) == (1, 2, 0)
    comps = {c.name: c for c in db.all_components()}
    for name, (px, py) in (("A", (100.0, 200.0)), ("B", (700.0, 1000.0))):
        pins = db.pins_by_comp(comps[name].id)
        assert [(p.pin_name, p.px, p.py, p.dir) for p in pins] == \
            [("n_data", px, py, "UNKNOWN")]
    # Label nets get net_props rows too, so HPWL/fanout queries see them
    # (Codex #148 P2): the recovered 2-pin net has a real HPWL after compute.
    db.compute_all()
    assert db.nets_by_hpwl(1.0, 1e12) == ["n_data"]   # hpwl = 600 + 800
    with sqlite3.connect(str(tmp_path / "c.bdb")) as con:
        hpwl, fanout = con.execute(
            "SELECT p.hpwl, p.fanout FROM net_props p"
            " JOIN net n ON n.id = p.net_id WHERE n.name='n_data'").fetchone()
    # fanout counts INPUT/INOUT pins only; label pins are UNKNOWN → 0.
    assert (hpwl, fanout) == (1400.0, 0)


def test_label_lands_on_deepest_component(tmp_path):
    # Containment picks the DEEPEST component under the label, not a parent:
    # the label at (45,45) sits inside BOTH o0 (100x100) and o0/i0 (10x10 at
    # 40,40) — the pin must land on the nested instance.
    b = GdsBuilder()
    b.structure("inner").boundary(10, 0, [(0, 0), (10, 0), (10, 10), (0, 10)])
    b.structure("outer") \
     .boundary(10, 0, [(0, 0), (100, 0), (100, 100), (0, 100)]) \
     .sref("inner", (40, 40), inst_name="i0")
    b.structure("top") \
     .sref("outer", (0, 0), inst_name="o0") \
     .text(63, 0, (45, 45), "deep_net")
    db, st = _import(tmp_path, b.write(tmp_path / "t.gds"))
    assert (st.n_nets, st.n_pins) == (1, 1)
    comps = {c.name: c for c in db.all_components()}
    assert db.pins_by_comp(comps["o0/i0"].id)           # deepest wins
    assert not db.pins_by_comp(comps["o0"].id)


def test_labels_in_referenced_structs_repeat_per_instance(tmp_path):
    # A label INSIDE a referenced structure elaborates per instance (standard
    # GDS flattening): every instance gets a pin on the SAME net — the short
    # this creates is the semantic the file encodes.
    b = GdsBuilder()
    b.structure("core") \
     .boundary(10, 0, [(0, 0), (50, 0), (50, 50), (0, 50)]) \
     .text(63, 0, (25, 25), "clk")
    b.structure("top") \
     .aref("core", (0, 0), cols=2, rows=1, col_pitch=(100, 0),
           row_pitch=(0, 100))
    db, st = _import(tmp_path, b.write(tmp_path / "t.gds"))
    assert (st.n_nets, st.n_pins) == (1, 2)
    positions = sorted((p.px, p.py) for c in db.all_components()
                       for p in db.pins_by_comp(c.id))
    assert positions == [(25.0, 25.0), (125.0, 25.0)]   # transformed per instance


def test_hier_flow_from_labeled_gds_no_verilog(tmp_path):
    # The Phase-G2 payoff: a LABELED GDS runs the hierarchy-aware flow with
    # zero Verilog — labels -> net/pin rows -> derive_busterms ->
    # run_hier_bundler -> routed through detailed NUTS.
    b = GdsBuilder()
    b.structure("blkA").boundary(10, 0, [(0, 0), (200, 0), (200, 400), (0, 400)])
    b.structure("blkB").boundary(10, 0, [(0, 0), (200, 0), (200, 400), (0, 400)])
    ch = b.structure("chip") \
        .sref("blkA", (0, 0), inst_name="A") \
        .sref("blkB", (600, 800), inst_name="B")
    for i in range(4):                          # a 4-bit labeled bus
        ch.text(63, 0, (100, 100 + 10 * i), f"d_{i}")
        ch.text(63, 0, (700, 900 + 10 * i), f"d_{i}")
    gds = b.write(tmp_path / "c.gds")

    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        for c in ["source flow/rnr/mix_tracks.buda",
                  f"open_bdb {tmp_path / 'c.bdb'}",
                  f"import_gds {gds} labels 63",
                  "derive_busterms 1",
                  "run_hier_bundler depth 1",
                  "generate_hier_topologies",
                  "run_planner hier 3",
                  "run_nuts", "run_detailed_nuts"]:
            s.do_command(c)
    assert "4 net(s) / 8 pin(s) recovered" in buf.getvalue()
    assert len(s.bundles) == 1                  # the 4 labeled bits bundled
    assert s.nuts_result.num_overlaps == 0
    assert s.detailed_result.num_unplaced == 0


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


def test_reimport_into_routed_bdb(tmp_path):
    # Re-importing into a BDB that already holds a ROUTED checkpoint must not
    # trip the FK constraints: clear_design wipes the derived pipeline rows
    # (bundles/topologies/routing/busterms) before the design tables. This is
    # the tools/gds_demo.py re-run scenario, where it was first caught.
    b = GdsBuilder()
    b.structure("blkA").boundary(10, 0, [(0, 0), (200, 0), (200, 400), (0, 400)])
    b.structure("blkB").boundary(10, 0, [(0, 0), (200, 0), (200, 400), (0, 400)])
    b.structure("chip") \
     .sref("blkA", (0, 0), inst_name="A") \
     .sref("blkB", (600, 800), inst_name="B")
    gds = b.write(tmp_path / "chip.gds")

    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ["source flow/rnr/mix_tracks.buda",
                  f"open_bdb {tmp_path / 'chip.bdb'}",
                  f"import_gds {gds}",
                  "add_blocks_from_bdb 0",
                  "add_bus d[8] A.p B.p",
                  "run_bundler", "generate_topologies", "run_planner 3",
                  "run_nuts",
                  f"import_gds {gds}"]:      # re-import over the checkpoint
            s.do_command(c)
    assert not s.bdb.all_bundles()           # derived rows wiped with the design
    assert not s.bdb.route_snapshot().hash
    assert {c.name for c in s.bdb.all_components()} == {"A", "B"}


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


# ── Phase G3: GDS layer mapping ──────────────────────────────────────────────

def test_gds_layer_mapping_api():
    ls = buda.LayerStack()
    ls.add_layer(3, "M3", buda.LayerDir.VERTICAL, buda.LayerType.LOW)
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    # Unmapped defaults.
    assert ls.get_gds_layer(3) == -1 and ls.get_gds_datatype(3) == 0
    assert ls.layer_for_gds(8, 0) == -1
    assert ls.gds_mapped_pairs() == []
    ls.set_gds_mapping(3, 8)                    # datatype defaults to 0
    ls.set_gds_mapping(4, 9, 5)
    assert (ls.get_gds_layer(3), ls.get_gds_datatype(3)) == (8, 0)
    assert (ls.get_gds_layer(4), ls.get_gds_datatype(4)) == (9, 5)
    # Reverse lookup is datatype-precise; add_layer order on the pair list.
    assert ls.layer_for_gds(8, 0) == 3 and ls.layer_for_gds(8, 1) == -1
    assert ls.layer_for_gds(9, 5) == 4
    assert ls.gds_mapped_pairs() == [(8, 0), (9, 5)]
    # Re-mapping overwrites; unknown layer id is a silent no-op (the
    # set_layer_* convention — the CLI guards with has_layer).
    ls.set_gds_mapping(3, 12, 1)
    assert (ls.get_gds_layer(3), ls.get_gds_datatype(3)) == (12, 1)
    ls.set_gds_mapping(99, 1, 1)
    assert ls.layer_for_gds(1, 1) == -1


def test_routing_shapes_excluded_from_footprint(tmp_path):
    # An exported-then-reimported cell carries wires beyond its outline; with
    # the wire's (layer, datatype) mapped as routing, the footprint must stay
    # the outline — the G4 round-trip requirement.
    def lib(path):
        b = GdsBuilder()
        b.structure("blk") \
         .boundary(10, 0, [(0, 0), (100, 0), (100, 50), (0, 50)]) \
         .path(8, 0, [(-40, 25), (300, 25)], width_um=2) \
         .boundary(8, 0, [(200, 0), (220, 0), (220, 20), (200, 20)])
        return b.write(path)
    # Unmapped: wire + patch grow the footprint (G1 behavior).
    db, st = _import(tmp_path, lib(tmp_path / "a.gds"))
    assert st.n_routing_shapes == 0
    assert {c.name: (c.width, c.height)
            for c in db.all_cells()} == {"blk": (340.0, 50.0)}
    # Mapped: both layer-8 shapes are wires — outline only.
    db2 = buda.BDB(str(tmp_path / "b.bdb"))
    st2 = db2.import_gds(str(lib(tmp_path / "b.gds")),
                         routing_layers=[(8, 0)])
    assert st2.n_routing_shapes == 2
    assert {c.name: (c.width, c.height)
            for c in db2.all_cells()} == {"blk": (100.0, 50.0)}


def test_routing_exclusion_is_datatype_precise(tmp_path):
    # Only the mapped (layer, datatype) pair is a wire: (8,1) still counts
    # as outline geometry when only (8,0) is mapped.
    b = GdsBuilder()
    b.structure("blk") \
     .boundary(10, 0, [(0, 0), (100, 0), (100, 50), (0, 50)]) \
     .boundary(8, 0, [(0, 0), (300, 0), (300, 10), (0, 10)]) \
     .boundary(8, 1, [(0, 0), (100, 0), (100, 80), (0, 80)])
    db = buda.BDB(str(tmp_path / "d.bdb"))
    st = db.import_gds(str(b.write(tmp_path / "d.gds")),
                       routing_layers=[(8, 0)])
    assert st.n_routing_shapes == 1
    assert {c.name: (c.width, c.height)
            for c in db.all_cells()} == {"blk": (100.0, 80.0)}


def test_all_routing_structure_is_zero_size_cell(tmp_path):
    # A structure whose only geometry is routing wires has no outline at all.
    b = GdsBuilder()
    b.structure("channel").path(8, 0, [(0, 0), (500, 0)], width_um=4)
    db = buda.BDB(str(tmp_path / "e.bdb"))
    st = db.import_gds(str(b.write(tmp_path / "e.gds")),
                       routing_layers=[(8, 0)])
    assert st.n_routing_shapes == 1
    assert {c.name: (c.width, c.height)
            for c in db.all_cells()} == {"channel": (0.0, 0.0)}
    assert any("no geometry anywhere" in w for w in st.warnings)


def test_cli_def_gds_layer_excludes_wires(tmp_path):
    # def_gds_layer binds def_layer metals to GDS pairs; import_gds picks the
    # mapping up automatically and reports the exclusion.
    b = GdsBuilder()
    b.structure("blkA") \
     .boundary(10, 0, [(0, 0), (200, 0), (200, 400), (0, 400)]) \
     .path(8, 0, [(100, 200), (900, 200)], width_um=2)
    b.structure("blkB").boundary(10, 0, [(0, 0), (200, 0), (200, 400), (0, 400)])
    b.structure("chip") \
     .sref("blkA", (0, 0), inst_name="A") \
     .sref("blkB", (600, 800), inst_name="B")
    gds = b.write(tmp_path / "chip.gds")

    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        for c in ["source flow/rnr/mix_tracks.buda",
                  "def_gds_layer 3 8 0",
                  f"open_bdb {tmp_path / 'chip.bdb'}",
                  f"import_gds {gds}"]:
            s.do_command(c)
    assert "1 routing shape(s) excluded" in buf.getvalue()
    comps = {c.name: c for c in s.bdb.all_components()}
    assert (comps["A"].x2 - comps["A"].x1) == 200.0   # wire didn't bloat A
    assert (s.layers.get_gds_layer(3), s.layers.get_gds_datatype(3)) == (8, 0)


def test_cli_def_gds_layer_file_and_labels(tmp_path):
    # Map-file form (with comments) + `labels` form: registered label layers
    # become import_gds's default TEXT filter, so the layer-9 text is skipped
    # without an explicit `labels` argument on the import command.
    mapfile = tmp_path / "gds.map"
    mapfile.write_text(
        "# buda_layer  gds_layer  datatype\n"
        "3  8  0\n"
        "4  9\n"                                # datatype defaults to 0
        "\n"
        "5  10 2\n")
    b = GdsBuilder()
    b.structure("blkA").boundary(10, 0, [(0, 0), (200, 0), (200, 400), (0, 400)])
    b.structure("chip") \
     .sref("blkA", (0, 0), inst_name="A") \
     .text(63, 0, (100, 200), "n_real") \
     .text(9, 0, (100, 100), "not_a_label")
    gds = b.write(tmp_path / "chip.gds")

    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ["source flow/rnr/mix_tracks.buda",
                  f"def_gds_layer file {mapfile}",
                  "def_gds_layer labels 63",
                  f"open_bdb {tmp_path / 'chip.bdb'}",
                  f"import_gds {gds}"]:
            s.do_command(c)
    assert s.layers.gds_mapped_pairs() == [(8, 0), (9, 0), (10, 2)]
    assert [n.name for n in s.bdb.all_nets()] == ["n_real"]


def test_cli_def_gds_layer_errors_and_warnings(capsys, tmp_path):
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.do_command("def_layer 3 M3 V LOW 0")
    s.do_command("def_layer 4 M4 H TOP 0")
    # Unknown BUDA layer id: rejected, nothing mapped.
    s.do_command("def_gds_layer 99 8 0")
    assert "unknown layer id 99" in capsys.readouterr().out
    assert s.layers.gds_mapped_pairs() == []
    # Map-file install is ATOMIC: a bad line anywhere rejects the whole file
    # (sourced scripts continue past the error — a partial map would make a
    # following import_gds exclude only some of the mapped wires).
    bad = tmp_path / "bad.map"
    bad.write_text("3 8 0\n99 9 0\n")
    s.do_command(f"def_gds_layer file {bad}")
    assert "unknown layer id 99" in capsys.readouterr().out
    assert s.layers.gds_mapped_pairs() == []
    # Duplicate GDS pair across two layers: warned, both still map (first
    # match wins on reverse lookup).
    s.do_command("def_gds_layer 3 8 0")
    s.do_command("def_gds_layer 4 8 0")
    assert "already mapped to layer 3" in capsys.readouterr().out
    assert s.layers.layer_for_gds(8, 0) == 3
