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

"""GDSII export (Phase G4) — see docs/internal/gds_oa_interchange.md.

The round-trip suite: export streams the persisted BDB tables back out as a
deterministic GDSII file (zeroed timestamps) that import_gds reads back to an
identical BDB — cells, component hierarchy, die, labels-as-nets, and (with
the same def_gds_layer map) routing shapes excluded from footprints.
"""

import contextlib
import io
import sqlite3

import buda
import buda_cli
from gds_build import GdsBuilder


def test_v11_db_migrates_to_current_orient(tmp_path):
    # A v11 component table (no orient column) migrates forward: the column is
    # added and existing rows read as identity ('N').
    p = str(tmp_path / "v11.bdb")
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES('schema_version','11');
        CREATE TABLE component (
            id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, cell TEXT,
            parent_id INTEGER REFERENCES component(id), depth INTEGER DEFAULT 0,
            x1 REAL, y1 REAL, x2 REAL, y2 REAL,
            is_leaf INTEGER DEFAULT 1, is_replicated INTEGER DEFAULT 0);
        INSERT INTO component VALUES(1,'i0','c',NULL,0,0,0,10,5,1,0);
        PRAGMA user_version = 11;
        """
    )
    con.commit()
    con.close()

    db = buda.BDB(p)                              # opening migrates forward
    assert db.schema_version() == buda.BDB.SCHEMA_VERSION
    assert db.meta_get("schema_version") == str(buda.BDB.SCHEMA_VERSION)
    (c,) = db.all_components()
    assert c.name == "i0" and c.orient == "N"     # pre-existing row defaults 'N'
    del db
    con = sqlite3.connect(p)
    cols = {r[1] for r in con.execute("PRAGMA table_info(component)")}
    con.close()
    assert "orient" in cols


def _hier_lib(path):
    """leaf ; mid = bar + 2x2 leaf AREF ; top = 2 mids + a net label."""
    b = GdsBuilder(dbu_um=0.001)
    b.structure("leaf").boundary(10, 0, [(0, 0), (5, 0), (5, 3), (0, 3)])
    b.structure("mid") \
     .boundary(10, 0, [(0, 0), (20, 0), (20, 2), (0, 2)]) \
     .aref("leaf", (0, 4), cols=2, rows=2, col_pitch=(8, 0), row_pitch=(0, 5))
    b.structure("top") \
     .sref("mid", (10, 10), inst_name="m0") \
     .sref("mid", (40, 40), inst_name="m1") \
     .text(63, 0, (11, 11), "net_a")
    return b.write(path)


def _snapshot(db):
    return (sorted((c.name, c.cell, c.depth, c.x1, c.y1, c.x2, c.y2, c.orient)
                   for c in db.all_components()),
            sorted((c.name, c.width, c.height) for c in db.all_cells()),
            sorted(n.name for n in db.all_nets()),
            sorted((p.pin_name, p.px, p.py) for p in db.all_pins()),
            (db.meta_get("die_w"), db.meta_get("die_h")))


def test_export_reimport_is_identical(tmp_path):
    # Import a hierarchical GDS, export the BDB, re-import: components (names,
    # cells, depths, absolute bboxes), cell footprints, nets/pins from labels,
    # and the die all come back identical. This includes the top-cell merge:
    # import materializes a cell row for the top structure (footprint = die),
    # and export re-emits it AS the top rather than orphan + synthetic top.
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.import_gds(str(_hier_lib(tmp_path / "in.gds")), [63])
    before = _snapshot(db)
    st = db.export_gds(str(tmp_path / "out.gds"))
    assert (st.n_structures, st.n_placements, st.n_labels) == (3, 6, 1)
    assert st.stage == "" and not st.warnings          # no routing persisted
    db2 = buda.BDB(str(tmp_path / "b.bdb"))
    db2.import_gds(str(tmp_path / "out.gds"), [63])
    assert _snapshot(db2) == before


def test_export_is_deterministic(tmp_path):
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.import_gds(str(_hier_lib(tmp_path / "in.gds")), [63])
    db.export_gds(str(tmp_path / "o1.gds"))
    db.export_gds(str(tmp_path / "o2.gds"))
    b1 = (tmp_path / "o1.gds").read_bytes()
    assert b1 and b1 == (tmp_path / "o2.gds").read_bytes()


def _routed_session(tmp_path, stop_after):
    """Import a labeled 2-block GDS, map all layers, route to `stop_after`."""
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
    cmds = ["source flow/rnr/mix_tracks.buda"]
    cmds += [f"def_gds_layer {lid} {30 + lid} 0" for lid in range(2, 8)]
    cmds += ["def_gds_layer labels 63",
             f"open_bdb {tmp_path / 'c.bdb'}",
             f"import_gds {gds}",
             "derive_busterms 1", "run_hier_bundler depth 1",
             "generate_hier_topologies", "run_planner hier 3"]
    cmds += {"nuts": ["run_nuts"],
             "dnuts": ["run_nuts", "run_detailed_nuts"]}[stop_after]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in cmds:
            s.do_command(c)
    return s, buf


def test_routed_roundtrip_through_cli(tmp_path):
    # The plan's full-pipeline fingerprint: import a labeled GDS, route to
    # detailed NUTS, export, re-import with the SAME def_gds_layer map — the
    # design comes back identical and every routing shape (bit-wires + vias)
    # is excluded from footprints by the G3 mapping.
    s, _ = _routed_session(tmp_path, "dnuts")
    n_ns = sum(len(s.bdb.net_segments(b.id)) for b in s.bdb.all_bundles())
    n_nv = sum(len(s.bdb.net_vias(b.id)) for b in s.bdb.all_bundles())
    assert n_ns > 0 and n_nv > 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(f"export_gds {tmp_path / 'out.gds'}")
    out = buf.getvalue()
    assert f"{n_ns} wire shape(s) (detailed_nuts), {n_nv} via(s)" in out
    assert "Warning" not in out                # every layer was mapped
    before = (sorted((c.name, c.cell, c.x1, c.y1, c.x2, c.y2)
                     for c in s.bdb.all_components()),
              sorted((c.name, c.width, c.height) for c in s.bdb.all_cells()),
              sorted(n.name for n in s.bdb.all_nets()))

    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        for c in (["source flow/rnr/mix_tracks.buda"]
                  + [f"def_gds_layer {lid} {30 + lid} 0" for lid in range(2, 8)]
                  + ["def_gds_layer labels 63",
                     f"open_bdb {tmp_path / 'c2.bdb'}",
                     f"import_gds {tmp_path / 'out.gds'}"]):
            s2.do_command(c)
    assert f"{n_ns + n_nv} routing shape(s) excluded" in buf2.getvalue()
    after = (sorted((c.name, c.cell, c.x1, c.y1, c.x2, c.y2)
                    for c in s2.bdb.all_components()),
             sorted((c.name, c.width, c.height) for c in s2.bdb.all_cells()),
             sorted(n.name for n in s2.bdb.all_nets()))
    assert after == before


def test_abstract_fallback_exports_bus_segments(tmp_path):
    # No detailed rows persisted -> the exporter falls back to the abstract
    # bus_segment rectangles (+ symbolic bus_via squares).
    s, _ = _routed_session(tmp_path, "nuts")
    n_bs = sum(len(s.bdb.bus_segments(b.id)) for b in s.bdb.all_bundles())
    n_bv = sum(len(s.bdb.bus_vias(b.id)) for b in s.bdb.all_bundles())
    assert n_bs > 0
    st = s.bdb.export_gds(str(tmp_path / "out.gds"),
                          [(lid, 30 + lid, 0) for lid in range(2, 8)])
    assert st.stage == "abstract_nuts"
    assert (st.n_wire_shapes, st.n_via_shapes) == (n_bs, n_bv)


def test_unmapped_layer_warns_and_defaults(tmp_path):
    # Exporting routed geometry with NO def_gds_layer map: one warning per
    # distinct unmapped layer, wires written to the (buda_layer, 0) default.
    s, _ = _routed_session(tmp_path, "dnuts")
    st = s.bdb.export_gds(str(tmp_path / "out.gds"))    # empty layer_map
    assert st.n_wire_shapes > 0
    layers = {r.layer for b in s.bdb.all_bundles()
              for r in s.bdb.net_segments(b.id)}
    assert sum("no def_gds_layer mapping" in w for w in st.warnings) \
        >= len(layers)


def test_labels_off_and_cli_options(tmp_path):
    s, _ = _routed_session(tmp_path, "dnuts")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(f"export_gds {tmp_path / 'o1.gds'} labels off")
        s.do_command(f"export_gds {tmp_path / 'o2.gds'} bogus_option 1")
    out = buf.getvalue()
    assert "0 label(s)" in out
    assert "unknown option 'bogus_option'" in out
    # Direct API: labels back on -> one TEXT per pin row.
    st = s.bdb.export_gds(str(tmp_path / "o3.gds"))
    assert st.n_labels == len(s.bdb.all_pins())


def test_export_requires_open_bdb(capsys):
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.do_command("export_gds /tmp/never.gds")
    assert "open_bdb first" in capsys.readouterr().out


def test_unused_library_cell_keeps_its_structure(tmp_path):
    # An uninstantiated library cell (the DEF/LEF case: import_def_lef
    # inserts every LEF macro) is NOT the top — without a die-size match it
    # must keep its own structure instead of being consumed as the synthetic
    # top, which would destroy its footprint on re-import (Codex #150 P2).
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.set_die(100, 100)
    db.add_cell("used", 10, 5)
    db.add_cell("unused_macro", 7, 3)               # sole orphan, != die size
    db.add_comp("i0", "used", "", 0, 0, 10, 5, True)
    st = db.export_gds(str(tmp_path / "out.gds"))
    assert st.n_structures == 3                     # used, unused_macro, top
    db2 = buda.BDB(str(tmp_path / "b.bdb"))
    db2.import_gds(str(tmp_path / "out.gds"))
    cells = {c.name: (c.width, c.height) for c in db2.all_cells()}
    assert cells["unused_macro"] == (7.0, 3.0)
    assert cells["used"] == (10.0, 5.0)


def test_dim_mismatch_warns_only_for_genuine_resize(tmp_path):
    # A rotation is now representable (orient=W swaps the exported extent), so a
    # correctly-rotated instance does NOT warn; only a bbox that matches neither
    # the cell nor the oriented cell (a genuine resize) does. (v13)
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.add_cell("c", 10, 5)
    db.add_comp("i0", "c", "", 0, 0, 10, 5, True)               # N, matches
    db.add_comp("i1", "c", "", 20, 0, 25, 10, True, "W")        # 90°: 5x10 == W(10x5)
    db.add_comp("i2", "c", "", 40, 0, 45, 10, True)             # 5x10, orient N: RESIZE
    st = db.export_gds(str(tmp_path / "out.gds"))
    mism = [w for w in st.warnings if "ORIENTED cell footprint" in w]
    assert len(mism) == 1 and mism[0].startswith("1 placement")   # only i2
    assert st.n_placements == 3


def test_orientation_round_trips(tmp_path):
    # v13: a rotated (90°) and a mirrored top-level instance survive
    # export -> re-import with matching bbox AND orient token — the round-trip
    # bug the n_dim_mismatch warning used to only flag.
    b = GdsBuilder(dbu_um=0.001)
    b.structure("leaf").boundary(10, 0, [(0, 0), (5, 0), (5, 3), (0, 3)])
    b.structure("mid").boundary(10, 0, [(0, 0), (20, 0), (20, 12), (0, 12)])
    b.structure("top") \
     .sref("mid", (10, 10), inst_name="m0") \
     .sref("mid", (10, 40), angle=90, inst_name="m1") \
     .sref("leaf", (100, 0), mirror=True, inst_name="lm")
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.import_gds(str(b.write(tmp_path / "in.gds")))
    orient = {c.name: c.orient for c in db.all_components()}
    assert orient["m0"] == "N" and orient["m1"] == "W" and orient["lm"] == "FN"
    before = _snapshot(db)
    st = db.export_gds(str(tmp_path / "out.gds"))
    assert not any("footprint" in w for w in st.warnings)   # rotation now clean
    db2 = buda.BDB(str(tmp_path / "b.bdb"))
    db2.import_gds(str(tmp_path / "out.gds"))
    assert _snapshot(db2) == before


def test_rotate_comp_then_export_is_consistent(tmp_path):
    # rotate_comp/flip_comp compose the orient token for a CHILDLESS subtree
    # (rows.size()==1) so a mutated instance exports faithfully (bbox and orient
    # stay consistent). (v13)
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.set_die(200, 200)
    db.add_cell("blk", 20, 12)
    db.add_comp("i0", "blk", "", 0, 0, 20, 12, False)
    db.rotate_comp("i0", 90)
    c = db.all_components()[0]
    assert c.orient == "W" and (c.x2 - c.x1, c.y2 - c.y1) == (12.0, 20.0)
    st = db.export_gds(str(tmp_path / "out.gds"))
    assert not any("footprint" in w for w in st.warnings)
    db2 = buda.BDB(str(tmp_path / "b.bdb"))
    db2.import_gds(str(tmp_path / "out.gds"))
    c2 = [x for x in db2.all_components() if x.cell == "blk"][0]
    assert (c2.orient, c2.x1, c2.y1, c2.x2, c2.y2) == \
           (c.orient, c.x1, c.y1, c.x2, c.y2)


def test_rotate_comp_with_children_does_not_compose_orient(tmp_path):
    # A rotated block WITH descendants keeps orient='N': the loop already
    # rewrote the children's absolute bboxes to carry the rotation, so setting
    # the root orient too would make export re-apply it (a double transform on
    # the reconstructed cell). Guard = rows.size()==1. (Codex #163 P2)
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.set_die(400, 400)
    db.add_cell("blk", 40, 20)
    db.add_cell("sub", 10, 6)
    db.add_comp("i0", "blk", "", 0, 0, 40, 20, False)
    db.add_comp("i0/c", "sub", "i0", 5, 5, 15, 11, True)
    child_before = [c for c in db.all_components() if c.name == "i0/c"][0]
    db.rotate_comp("i0", 90)
    root = [c for c in db.all_components() if c.name == "i0"][0]
    child = [c for c in db.all_components() if c.name == "i0/c"][0]
    assert root.orient == "N"                       # composition skipped
    # The child's absolute bbox WAS rigidly rotated (that carries the rotation);
    # orient stays 'N', so export won't double-transform it.
    assert (child.x1, child.y1) != (child_before.x1, child_before.y1)
    assert child.orient == "N"
