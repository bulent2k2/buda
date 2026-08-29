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

import pytest

# Moved to the mid tier: full-pipeline / BDB round-trip / interchange
# integration (keeps the fast tier < 10s). See
# docs/internal/test/runtime_analysis.md.
pytestmark = pytest.mark.mid

import contextlib
import io
import pathlib
import sqlite3

import buda
import buda_cli
from gds_build import GdsBuilder
from slot_groups import expand_slot_groups


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


def test_export_reimport_is_identical_at_a_non_default_scale(tmp_path):
    # GDS carries its own DBU record, so the µm<->layout-unit conversion is a
    # boundary just like DEF's.  A design whose coordinates are in DBU (import
    # scale 2000) would otherwise export geometry 2000x too large — in a file
    # that parses perfectly and looks like a valid layout.  The round-trip is
    # what pins both directions: export divides by the scale, import
    # multiplies by it, and the design must come back unchanged.
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.set_import_scale(2000.0)
    db.import_gds(str(_hier_lib(tmp_path / "in.gds")), [63])
    before = _snapshot(db)
    db.export_gds(str(tmp_path / "out.gds"))
    db2 = buda.BDB(str(tmp_path / "b.bdb"))
    db2.set_import_scale(2000.0)
    db2.import_gds(str(tmp_path / "out.gds"), [63])
    assert _snapshot(db2) == before

    # …and the scale really is doing something: at the default the SAME file
    # imports 2000x smaller.
    db3 = buda.BDB(str(tmp_path / "c.bdb"))
    db3.import_gds(str(_hier_lib(tmp_path / "in2.gds")), [63])
    c_um = sorted(db3.all_components(), key=lambda c: c.name)[0]
    c_lu = sorted(db.all_components(), key=lambda c: c.name)[0]
    assert abs(c_um.x2 * 2000.0 - c_lu.x2) < 1e-6


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


def _scaled_tracks(tmp_path, scale):
    """flow/rnr/mix_tracks.buda with every pattern distance multiplied by
    `scale` — origin, widths and spacings alike.  Script-declared distances
    are in layout units and the import scale does NOT touch them, so this is
    the hand-scaling the contract says the author owns."""
    out = []
    src = pathlib.Path(__file__).parents[2] / "flow" / "rnr" / "mix_tracks.buda"
    for line in src.read_text().splitlines():
        if line.startswith("def_track_pattern"):
            tok = line.split()
            # `def_track_pattern <layer_id> <origin> [<type> <w> <sp>]...`
            # — the LAYER ID is an identifier, not a distance.
            # The fixtures are written with `( <slots> )x<N>` repetition
            # groups, so expand through the CLI's own parser first: a private
            # triple-splitter reads `0.5)x4` as a spacing.
            head, rest = tok[:3], expand_slot_groups("def_track_pattern",
                                                     tok[3:])
            head[2] = f"{float(head[2]) * scale:g}"           # origin
            for i in range(0, len(rest), 3):                  # type w sp
                rest[i + 1] = f"{float(rest[i + 1]) * scale:g}"
                rest[i + 2] = f"{float(rest[i + 2]) * scale:g}"
            line = " ".join(head + rest)
        out.append(line)
    p = tmp_path / "tracks.buda"
    p.write_text("\n".join(out) + "\n")
    return p


def _boundary_sizes(gds_bytes):
    """[(gds_layer, width_dbu, height_dbu)] for every BOUNDARY in the file.

    A ~20-line record walker beats asserting on raw bytes: the exported
    geometry is what the contract is about, and byte-equality would also fail
    for legitimate reasons (a µm-scale design is quantized to 1 µm, so it
    cannot reproduce the sub-micron detail a DBU-scale one keeps)."""
    import struct
    out, i = [], 0
    layer = dtype = None
    while i + 4 <= len(gds_bytes):
        ln, rt, dt = struct.unpack(">HBB", gds_bytes[i:i + 4])
        if ln < 4:
            break
        body = gds_bytes[i + 4:i + ln]
        if rt == 0x0D:                                   # LAYER
            layer = struct.unpack(">h", body[:2])[0]
        elif rt == 0x10 and layer is not None:           # XY
            n = len(body) // 4
            pts = struct.unpack(f">{n}i", body[:n * 4])
            xs, ys = pts[0::2], pts[1::2]
            out.append((layer, max(xs) - min(xs), max(ys) - min(ys)))
            layer = None
        i += ln
    return out


def test_via_size_stays_in_microns_at_any_design_scale(tmp_path):
    # `export_gds ... via_size <um>` is documented and defaulted in MICRONS —
    # it describes the GDS boundary, not the design, so it is the one distance
    # in the writer that is NOT already in layout units.  Unconverted, the
    # writer's ÷scale would shrink the default 1 µm via to 0.0005 µm at
    # 2000 DBU/µm — a valid-looking file with invisible vias (Codex P1 on
    # #645).  The file's own DBU is 1 nm, so 1 µm is 1000 of them, whatever
    # the design's layout unit happens to be.
    SCALE, VIA_UM, DBU_PER_UM = 2000.0, 1.0, 1000
    seen = []
    for tag, scale in (("um", None), ("dbu", SCALE)):
        d = tmp_path / tag
        d.mkdir()
        b = GdsBuilder()
        b.structure("blkA").boundary(1, 0, [(0, 0), (200, 0), (200, 200), (0, 200)])
        b.structure("blkB").boundary(1, 0, [(0, 0), (200, 0), (200, 200), (0, 200)])
        ch = b.structure("chip") \
            .sref("blkA", (0, 0), inst_name="A") \
            .sref("blkB", (600, 800), inst_name="B")
        for i in range(4):
            ch.text(63, 0, (100, 100 + 10 * i), f"d_{i}")
            ch.text(63, 0, (700, 900 + 10 * i), f"d_{i}")
        gds = b.write(d / "c.gds")

        s = buda_cli.BudaSession()
        s.no_viz = True
        cmds = [f"source {_scaled_tracks(d, scale or 1.0)}"]
        cmds += [f"def_gds_layer {lid} {30 + lid} 0" for lid in range(2, 8)]
        cmds += ["def_gds_layer labels 63", f"open_bdb {d / 'c.bdb'}"]
        if scale:
            cmds.append(f"set_import_scale {scale:g}")
        cmds += [f"import_gds {gds}",
                 "derive_busterms 1", "run_hier_bundler depth 1",
                 "generate_hier_topologies", "run_planner hier 3",
                 "run_nuts", "run_detailed_nuts",
                 f"export_gds {d / 'out.gds'} via_size {VIA_UM:g}"]
        with contextlib.redirect_stdout(io.StringIO()):
            for c in cmds:
                s.do_command(c)

        n_vias = sum(len(s.bdb.net_vias(x.id)) for x in s.bdb.all_bundles())
        assert n_vias > 0, f"{tag}: no vias routed — the test proves nothing"
        squares = [(w, h) for _l, w, h in
                   _boundary_sizes((d / "out.gds").read_bytes())
                   if w == h and w > 0]
        want = (int(VIA_UM * DBU_PER_UM),) * 2
        assert squares.count(want) == n_vias, (
            f"{tag}: expected {n_vias} vias of {want[0]} dbu "
            f"({VIA_UM:g} um); square sizes present: {sorted(set(squares))}")
        seen.append(n_vias)
    assert seen[0] == seen[1], f"different via counts by scale: {seen}"


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


# ── what the DEF+Verilog merge invents survives the round trip (item 3) ─────

_MERGE_LEF = """MACRO m
  CLASS BLOCK ;
  SIZE 10 BY 4 ;
  PIN a
    DIRECTION INPUT ;
    PORT
      LAYER metal1 ;
      RECT 4 1 6 3 ;
    END
  END a
END m
END LIBRARY
"""

_MERGE_DEF = """VERSION 5.8 ;
DESIGN t ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 100000 100000 ) ;
COMPONENTS 2 ;
  - u0/i m + PLACED ( 1000 2000 ) N ;
  - u1/i m + PLACED ( 50000 60000 ) N ;
END COMPONENTS
PINS 1 ;
  - out + NET n0 + DIRECTION OUTPUT + LAYER metal1 ( -250 -250 ) ( 250 250 )
      + PLACED ( 99000 50000 ) N ;
END PINS
NETS 1 ;
  - n0 ( u0/i a ) ( PIN out ) ;
END NETS
END DESIGN
"""

_MERGE_V = """module mid ();
  m i ();
endmodule
module chip (output n0);
  mid u0 ();
  mid u1 ();
endmodule
"""


def test_merge_scaffolding_survives_the_round_trip(tmp_path):
    """Two kinds of cell created during a DEF+Verilog merge had no size — the
    synthetic __PORT__ cell (no cell row at all) and the containers
    import_verilog creates (0x0) — so the export emitted SREFs to an
    undefined structure, an empty orphan for the top module beside a
    synthetic 'top', and every net-name label re-imported "outside every
    component": the geometry round-tripped, the netlist silently died.

    The sizes now exist where they are derived: __PORT__ from the port
    shapes at DEF import, containers from their (measured size-uniform)
    instances at derive_container_bboxes, and the top module's cell is
    ADOPTED as the top structure's name via meta.verilog_top rather than
    orphaned."""
    (tmp_path / "m.lef").write_text(_MERGE_LEF)
    (tmp_path / "d.def").write_text(_MERGE_DEF)
    (tmp_path / "c.v").write_text(_MERGE_V)
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.import_def_lef(str(tmp_path / "d.def"), str(tmp_path / "m.lef"))
    db.import_verilog(str(tmp_path / "c.v"))
    db.derive_container_bboxes(1.0)

    # The derived sizes are in the CELL table, not only on the instances.
    cells = {c.name: (c.width, c.height) for c in db.all_cells()}
    assert cells["__PORT__"] == (0.5, 0.5)          # the DEF port shape
    assert cells["mid"] == (12.0, 6.0)              # child + 1.0 margin/side
    assert cells["m"] == (10.0, 4.0)                # LEF SIZE, untouched

    st = db.export_gds(str(tmp_path / "out.gds"))
    assert not st.warnings, st.warnings             # no dim-mismatch either

    db2 = buda.BDB(str(tmp_path / "b.bdb"))
    st2 = db2.import_gds(str(tmp_path / "out.gds"), [63])
    # The three historical losses, each pinned:
    assert not any("__PORT__" in w for w in st2.warnings), st2.warnings
    assert not any("no geometry" in w for w in st2.warnings), st2.warnings
    assert not any("outside every component" in w for w in st2.warnings), \
        st2.warnings
    assert st2.tops == ["chip"]                     # one top, the right name
    assert len([c for c in db2.all_components() if c.cell == "__PORT__"]) == 1
    assert sorted(n.name for n in db2.all_nets()) == ["n0"]   # netlist LIVES


def test_disagreeing_instance_sizes_leave_the_cell_unsized(tmp_path):
    """The write-back rule is the COMMON size, not a max: a cell whose
    instances genuinely differ is left 0x0, and the export's dim-mismatch
    warning keeps the gap visible instead of claiming a footprint no
    instance has."""
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.add_cell("box", 0, 0)
    db.add_comp("p", "box", "", -1, -1, -1, -1, False)   # unplaced container
    db.add_comp("q", "box", "", -1, -1, -1, -1, False)
    db.add_comp("p/k1", "leaf", "p", 0, 0, 10, 10, True)
    db.add_comp("q/k1", "leaf", "q", 50, 50, 80, 70, True)  # different child
    db.derive_container_bboxes(0.0)
    box = {c.name: c for c in db.all_cells()}["box"]
    assert (box.width, box.height) == (0.0, 0.0)
    # …while the uniform case in the same design DOES write back:
    db.add_cell("pair", 0, 0)
    db.add_comp("r", "pair", "", -1, -1, -1, -1, False)
    db.add_comp("s", "pair", "", -1, -1, -1, -1, False)
    db.add_comp("r/k", "leaf", "r", 0, 0, 5, 5, True)
    db.add_comp("s/k", "leaf", "s", 90, 90, 95, 95, True)
    db.derive_container_bboxes(0.0)
    pair = {c.name: c for c in db.all_cells()}["pair"]
    assert (pair.width, pair.height) == (5.0, 5.0)


def test_ports_of_different_sizes_get_their_own_cells(tmp_path):
    """One GDS structure has ONE footprint, so independent maxes over a 1x10
    and a 10x1 port would invent a 10x10 cell matching neither, and re-import
    would expand every port to it (Codex P2 on #662).  Differing sizes get
    numbered sibling cells instead; each port round-trips at its own size."""
    deff = _MERGE_DEF.replace(
        """PINS 1 ;
  - out + NET n0 + DIRECTION OUTPUT + LAYER metal1 ( -250 -250 ) ( 250 250 )
      + PLACED ( 99000 50000 ) N ;
END PINS""",
        """PINS 2 ;
  - out + NET n0 + DIRECTION OUTPUT + LAYER metal1 ( -50 -500 ) ( 50 500 )
      + PLACED ( 99000 50000 ) N ;
  - out2 + NET n0 + DIRECTION OUTPUT + LAYER metal1 ( -500 -50 ) ( 500 50 )
      + PLACED ( 50000 99000 ) N ;
END PINS""")
    (tmp_path / "m.lef").write_text(_MERGE_LEF)
    (tmp_path / "d.def").write_text(deff)
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.import_def_lef(str(tmp_path / "d.def"), str(tmp_path / "m.lef"))
    cells = {c.name: (c.width, c.height) for c in db.all_cells()}
    assert cells["__PORT__"] == pytest.approx((0.1, 1.0))    # first class
    assert cells["__PORT__2"] == pytest.approx((1.0, 0.1))   # second class
    st = db.export_gds(str(tmp_path / "out.gds"))
    assert not st.warnings, st.warnings             # no dim mismatch either way
    db2 = buda.BDB(str(tmp_path / "b.bdb"))
    db2.import_gds(str(tmp_path / "out.gds"))
    sizes = sorted((round(c.x2 - c.x1, 6), round(c.y2 - c.y1, 6))
                   for c in db2.all_components()
                   if c.cell.startswith("__PORT__"))
    assert sizes == [pytest.approx((0.1, 1.0)),
                     pytest.approx((1.0, 0.1))]     # each its OWN size


def test_declared_top_outranks_a_die_sized_decoy(tmp_path):
    """The die-size adoption is circumstantial: an unused library cell whose
    dimensions happen to equal the die would be claimed as the top while the
    DECLARED Verilog top orphans — the two-top failure all over again (Codex
    P2 on #662).  meta.verilog_top is direct evidence and goes first."""
    (tmp_path / "m.lef").write_text(_MERGE_LEF)
    (tmp_path / "d.def").write_text(_MERGE_DEF)
    (tmp_path / "c.v").write_text(_MERGE_V)
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.import_def_lef(str(tmp_path / "d.def"), str(tmp_path / "m.lef"))
    db.import_verilog(str(tmp_path / "c.v"))
    db.derive_container_bboxes(1.0)
    db.add_cell("decoy", 100.0, 100.0)              # uninstantiated, == die
    db.export_gds(str(tmp_path / "out.gds"))
    db2 = buda.BDB(str(tmp_path / "b.bdb"))
    st2 = db2.import_gds(str(tmp_path / "out.gds"), [63])
    # `decoy` keeps its OWN structure (an unused library cell must never be
    # consumed as the top), so it is also unreferenced — two tops is correct.
    # The regression this pins would look different: `chip` as the EMPTY one
    # ("no geometry anywhere") with the roots under `decoy`, plus a synthetic
    # 'top'.
    assert "chip" in st2.tops and "top" not in st2.tops, st2.tops
    assert not any("no geometry" in w for w in st2.warnings), st2.warnings
    cells2 = {c.name: (c.width, c.height) for c in db2.all_cells()}
    assert cells2["decoy"] == (100.0, 100.0)        # survived, own structure
    # …and the design's components live under chip, not the decoy.
    assert any(c.cell == "quad" or c.cell == "mid" for c in db2.all_components())


def test_a_fresh_design_load_clears_the_top_memo(tmp_path):
    """meta.verilog_top survives in the meta table, but clear_design() wipes
    the design it described — stale, it could claim an unrelated cell of the
    NEXT design that shares the old top's name (Codex P2 on #662)."""
    (tmp_path / "m.lef").write_text(_MERGE_LEF)
    (tmp_path / "d.def").write_text(_MERGE_DEF)
    (tmp_path / "c.v").write_text(_MERGE_V)
    db = buda.BDB(str(tmp_path / "a.bdb"))
    db.import_def_lef(str(tmp_path / "d.def"), str(tmp_path / "m.lef"))
    db.import_verilog(str(tmp_path / "c.v"))
    assert db.meta_get("verilog_top") == "chip"
    db.import_def_lef(str(tmp_path / "d.def"), str(tmp_path / "m.lef"))
    assert db.meta_get("verilog_top") == ""         # fresh load, fresh slate
