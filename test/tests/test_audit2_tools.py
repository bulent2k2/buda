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

"""Regression tests for the slice-2 (tools/) audit fixes.

Each test encodes the corrected behavior of one confirmed finding and was
verified to FAIL against the pre-fix code (git stash of the tools/ diff).
GUI-interaction-only findings (T2-01/02/06/07 — Tk window state, drag
gates, optimizer-cancel wiring) live on the headless-xfail'd floorplanner
GUI path and are exercised by hand; the headless-reachable fixes are here.
"""

import os
import sys

import pytest

# BDB round-trip / converter integration → mid tier (keeps fast tier lean),
# matching test_buda2bdb.py.
pytestmark = pytest.mark.mid

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("MPLBACKEND", "Agg")

import buda_db


# ──────────────────────────────────────────────────────────────────────────
# T3-01 — def_cluster NETS regex dropped the last net of every section
# ──────────────────────────────────────────────────────────────────────────

def test_def_cluster_parses_last_net(tmp_path):
    from tools import def_cluster
    d = tmp_path / "t.def"
    d.write_text(
        "VERSION 5.8 ;\n"
        "UNITS DISTANCE MICRONS 1000 ;\n"
        "DIEAREA ( 0 0 ) ( 1000 800 ) ;\n"
        "NETS 2 ;\n"
        "- neta ( u1 O ) ( u2 I ) ;\n"
        "- netb ( u3 O ) ( u4 I ) ;\n"
        "END NETS\n"
    )
    _units, _die, _comps, nets = def_cluster.parse_def(str(d))
    # Pre-fix the terminator alternation lacked `\Z`, so the FINAL net (netb),
    # which is not followed by `\n- ` or `\nEND` inside the captured body, was
    # silently dropped.
    assert "neta" in nets and "netb" in nets, nets


# ──────────────────────────────────────────────────────────────────────────
# T3-02 — def_viz_shared dropped a leaf placed at x==0 (die left edge)
# ──────────────────────────────────────────────────────────────────────────

def test_def_viz_keeps_leaf_at_x_zero(tmp_path):
    from tools import def_viz_shared
    p = str(tmp_path / "edge.bdb")
    b = buda_db.BDB(p)
    b.set_die(1000, 800)
    # A leaf whose left/bottom edge sits on the die origin — a real placement,
    # NOT the (-1) import_verilog placeholder.
    b.add_comp("edge_blk", "CELL", "", 0, 0, 100, 100, True)
    del b

    data = def_viz_shared.DefVizData()
    data.load_bdb(p)
    # `> 0` (pre-fix) treated x1==0 as unplaced and, with no children to
    # rebuild a bbox, dropped it from inst_info entirely.
    assert "edge_blk" in data.inst_info, data.inst_info
    assert data.inst_info["edge_blk"]["x1"] == 0


# ──────────────────────────────────────────────────────────────────────────
# T3-04 — viz_ipc recv-loop teardown must own the socket + hold the lock
# ──────────────────────────────────────────────────────────────────────────

def test_viz_ipc_recv_teardown_only_disconnects_own_socket():
    from tools import viz_ipc

    class _FakeSock:
        def __init__(self):
            self.closed = False

        def recv(self, _n):
            return b""          # immediately "closed" → recv loop exits

        def close(self):
            self.closed = True

    ipc = viz_ipc.VizIPC("unit-test", verbose=False)
    live = _FakeSock()          # the current, freshly-accepted peer
    stale = _FakeSock()         # a previous socket whose recv thread is winding down
    ipc._sock = live
    ipc._connected = True

    # A stale recv thread finishing on `stale` must NOT disconnect `live`.
    ipc._recv_loop(stale)
    assert ipc._connected is True
    assert ipc._sock is live
    assert live.closed is False

    # The owning thread does tear down.
    ipc._recv_loop(live)
    assert ipc._connected is False
    assert live.closed is True


# ──────────────────────────────────────────────────────────────────────────
# T1-04 — bdb_serialize.dump must percent-encode the path for the file: URI
# ──────────────────────────────────────────────────────────────────────────

def test_bdb_serialize_dump_uri_metachar_path(tmp_path):
    from tools import bdb_serialize
    # A '?' in the filename is the URI query separator: a raw f-string let
    # SQLite parse the path off at the '?', silently opening a DIFFERENT
    # (empty) db and dumping nothing. quote() escapes it to %3F.
    # Windows forbids '?' in filenames outright (the POSIX spelling cannot
    # even be created — measured, run 25), so the fragment separator '#' —
    # legal on NTFS and eaten identically by an unquoted URI — carries the
    # same property there.
    meta = "#" if os.name == "nt" else "?"
    src = str(tmp_path / f"de{meta}sign.bdb")
    b = buda_db.BDB(src)
    b.set_die(1000, 800)
    b.add_comp("cpu", "CPU", "", 0, 0, 500, 400, False)
    del b

    out = str(tmp_path / "out.sql")
    bdb_serialize.dump(src, out)
    text = open(out).read()
    # A real dump carries the schema + the component row, not just the header.
    assert "CREATE TABLE" in text
    assert "cpu" in text


# ──────────────────────────────────────────────────────────────────────────
# T1-03 — bdb2buda: an UNPLACED parent must not shift children off the die
# ──────────────────────────────────────────────────────────────────────────

def test_bdb2buda_unplaced_parent_origin(tmp_path):
    from tools import bdb2buda
    p = str(tmp_path / "hier.bdb")
    b = buda_db.BDB(p)
    # Parent is the canonical DEF+Verilog placeholder: bbox -1..-1 (unplaced).
    pid = b.add_comp("blkA", "CELLA", "", -1, -1, -1, -1, False)
    assert pid >= 0
    # Two placed children whose extent starts at the die origin.
    b.add_comp("child0", "CH", "blkA", 0, 0, 100, 80, True)
    b.add_comp("child1", "CH", "blkA", 200, 0, 300, 80, True)
    del b

    script = bdb2buda.convert(p, cell_name="blkA", scale=1)
    xs = []
    for line in script.splitlines():
        parts = line.split()
        if parts[:1] == ["add_block"]:
            xs.append(int(parts[2]))    # x1 of each emitted block
    assert xs, script
    # Pre-fix ox = parent.x1 = -1, so every child shifted by +1 and the
    # leftmost block landed at x==1 (outside a die that starts at 0). Post-fix
    # the origin is the children's own min-x, so the leftmost block is at 0.
    assert min(xs) == 0, script


# ──────────────────────────────────────────────────────────────────────────
# T1-01 — bdb2buda: preserve INOUT direction + drop driver self-receivers
# ──────────────────────────────────────────────────────────────────────────

def test_bdb2buda_inout_and_self_loop(tmp_path):
    from tools import bdb2buda
    p = str(tmp_path / "io.bdb")
    b = buda_db.BDB(p)
    b.set_die(1000, 800)
    b.add_comp("a", "CELL", "", 0, 0, 100, 100, True)
    b.add_comp("bb", "CELL", "", 300, 0, 400, 100, True)
    # An INOUT (bidirectional) net across the two blocks.
    b.add_net_pins_inout("bidir", ["a.p", "bb.p"])
    del b

    script = bdb2buda.convert(p, scale=1)
    io_lines = [ln for ln in script.splitlines()
                if ln.startswith("add_net bidir")]
    assert io_lines, script
    # Pre-fix the direction class was lost — INOUT degraded to a plain
    # directed net with no suffix.
    assert io_lines[0].rstrip().endswith(" inout"), io_lines


def test_bdb2buda_inout_keeps_self_loop_endpoint(tmp_path):
    # PR #348 review: an INOUT/UNKNOWN net legitimately allows a pin on the
    # driver block, so the self-loop drop (directed-only) must NOT fire here —
    # and the ` inout` suffix must stay the LAST token (4th arg), not collapse
    # into the receiver-CSV where the CLI would misparse it.
    from tools import bdb2buda
    p = str(tmp_path / "io2.bdb")
    b = buda_db.BDB(p)
    b.set_die(1000, 800)
    b.add_comp("a", "CELL", "", 0, 0, 100, 100, True)
    b.add_comp("bb", "CELL", "", 300, 0, 400, 100, True)
    # Two pins on the driver block 'a' plus one on 'bb'.
    b.add_net_pins_inout("bidir", ["a.p", "a.q", "bb.p"])
    del b

    line = next(ln for ln in bdb2buda.convert(p, scale=1).splitlines()
                if ln.startswith("add_net bidir"))
    toks = line.split()
    assert toks[-1] == "inout", line
    rcvs = toks[3].split(",")
    # The self-pin a.q is retained; every endpoint survives.
    assert any(r.startswith("a.") for r in rcvs), line


def test_bdb2buda_top_level_keeps_die_coords(tmp_path):
    # PR #348 review: a top-level export (no -cell) must stay in DIE
    # coordinates — a block at (100,100) inside a larger die must not be
    # rebased to the origin (that path is only for an actual unplaced parent).
    from tools import bdb2buda
    p = str(tmp_path / "top.bdb")
    b = buda_db.BDB(p)
    b.set_die(1000, 800)
    b.add_comp("A", "CA", "", 100, 100, 300, 200, True)
    b.add_comp("B", "CB", "", 400, 400, 600, 500, True)
    del b

    script = bdb2buda.convert(p, scale=1)
    xs = [int(ln.split()[2]) for ln in script.splitlines()
          if ln.split()[:1] == ["add_block"]]
    # The leftmost block keeps x==100 (die coords), NOT shifted to 0.
    assert 100 in xs and min(xs) == 100, script


# ──────────────────────────────────────────────────────────────────────────
# T3-05 — def_cluster only emits generation for FILTERED (valid) buses
# ──────────────────────────────────────────────────────────────────────────

def test_def_cluster_skips_degenerate_bus(tmp_path, capsys):
    from tools import def_cluster
    # A single degenerate bus: the src is only 2µm wide, so the 1µm corner
    # margin shrinks it to zero width — _topo_valid rejects it → grid_valid
    # is empty.
    deg = {"src_bbox": (0, 0, 2, 100),
           "dst_bbox": (200, 0, 300, 100),
           "nets": ["n0", "n1"]}
    out = str(tmp_path / "gen.buda")
    def_cluster.write_buda_script(out, (400, 400), "x.def", [deg], [], args=None)
    text = open(out).read()
    # Pre-fix the emit loop keyed off the UNFILTERED bus list, referencing a
    # g_1_src block that was never declared → a hard CLI error at run time.
    assert "generate_topologies_for_bundle g_1" not in text, text
    assert "add_block g_1_src" not in text, text
    assert "1 degenerate bus(es) skipped" in capsys.readouterr().out


# ──────────────────────────────────────────────────────────────────────────
# T3-03 — render must fail loudly on a bad --bundle instead of rendering [0]
# ──────────────────────────────────────────────────────────────────────────

def test_render_find_wrapper_bad_hint_exits():
    from tools import render

    class _Wrap:
        def __init__(self, i):
            self.input = type("I", (), {
                "original_bundle": type("B", (), {"id": i})()})()

    class _S:
        bundles = [_Wrap(1), _Wrap(2)]

    # A hint matching no id and out of 1-based range: no silent fallback to
    # bundles[0].
    with pytest.raises(SystemExit):
        render._find_wrapper(_S(), "9")

    class _Empty:
        bundles = []

    with pytest.raises(SystemExit):
        render._find_wrapper(_Empty(), "1")


# ──────────────────────────────────────────────────────────────────────────
# T2-03 — floorplanner load_bdb keeps a block at legitimate negative coords
# ──────────────────────────────────────────────────────────────────────────

def test_fpc_load_bdb_keeps_negative_coord_block(tmp_path):
    from tools import floorplanner_commands as fpc
    p = str(tmp_path / "neg.bdb")
    b = buda_db.BDB(p)
    b.set_die(1000, 800)
    # A real placement partly left of the origin — NOT the -1/-1 placeholder.
    b.add_comp("negblk", "CELL", "", -50, -50, 50, 50, True)
    del b

    state = fpc.load_bdb(p)
    # Pre-fix `x1 < 0 or y1 < 0` treated this as unplaced and dropped it,
    # silently losing the block on reopen.
    assert "negblk" in state.block_names, state.block_names


# ──────────────────────────────────────────────────────────────────────────
# T2-04 — import_verilog into a pre-existing BDB rebuilds from scratch
# ──────────────────────────────────────────────────────────────────────────

def test_fpc_import_verilog_rebuilds_pre_existing(tmp_path):
    from tools import floorplanner_commands as fpc
    p = str(tmp_path / "reuse.bdb")
    b = buda_db.BDB(p)
    b.set_die(1000, 800)
    # A stale top-level component from a previous, unrelated design.
    b.add_comp("STALE_INST", "OLD", "", 0, 0, 100, 100, True)
    del b

    v = tmp_path / "d.v"
    v.write_text(
        "module leaf(a);\n input a;\nendmodule\n"
        "module top;\n leaf u1 (.a(n1));\nendmodule\n"
    )
    state = fpc.import_verilog(str(v), p)
    # Pre-fix the stale component survived the UPSERT and was seeded as a
    # phantom placeholder block.
    assert "STALE_INST" not in state.block_names, state.block_names
    # The fresh design's own instances are seeded instead.
    assert state.block_names and "u1" in state.block_names, state.block_names


# ──────────────────────────────────────────────────────────────────────────
# T1-05 — buda2bdb cell-replace prunes stale routing/pipeline tables
# ──────────────────────────────────────────────────────────────────────────

def test_buda2bdb_cell_replace_prunes_routing(tmp_path):
    import sqlite3
    from tools import buda2bdb
    p = str(tmp_path / "routed.bdb")
    b = buda_db.BDB(p)
    b.set_die(1000, 800)
    b.add_comp("cpu", "CPU", "", 0, 0, 500, 400, False)
    del b

    # Inject a persisted-route remnant + a routing-time busterm row.
    con = sqlite3.connect(p)
    con.execute("INSERT INTO bundle (id) VALUES (1)")
    con.execute("INSERT INTO busterm (id, hier_path, depth) "
                "VALUES ('tb:stale', 'tb:stale', 0)")
    con.commit()
    con.close()

    buda2bdb._delete_cell_footprint(p, "CPU")

    con = sqlite3.connect(p)
    n_bundle = con.execute("SELECT COUNT(*) FROM bundle").fetchone()[0]
    n_tb = con.execute(
        "SELECT COUNT(*) FROM busterm WHERE id LIKE 'tb:%'").fetchone()[0]
    con.close()
    # Pre-fix these dangling rows survived the cell replace and corrupted a
    # later load_pipeline rehydrate.
    assert n_bundle == 0
    assert n_tb == 0


# ──────────────────────────────────────────────────────────────────────────
# T2-08 — export_hbundle_script writes a cwd-independent absolute open_bdb path
# ──────────────────────────────────────────────────────────────────────────

def test_fpc_export_hbundle_script_absolute_bdb_path(tmp_path, monkeypatch):
    from tools import floorplanner_commands as fpc
    p = str(tmp_path / "fp.bdb")
    b = buda_db.BDB(p)
    b.set_die(1000, 800)
    b.add_comp("cpu", "CPU", "", 0, 0, 500, 400, True)
    del b

    # A state whose bdb_path is RELATIVE (a floorplanner launched from cwd).
    # chdir into tmp_path first: on the Windows runner tmp (C:) and checkout
    # (D:) are different drives, where a relative spelling from the repo cwd
    # does not EXIST (ntpath.relpath raises ValueError — measured, run 25).
    # The premise is "launched from a cwd with a relative path", so take it
    # from a cwd that has one.
    monkeypatch.chdir(tmp_path)
    rel = os.path.relpath(p)
    state = fpc.load_bdb(p)
    state.bdb_path = rel

    out = str(tmp_path / "flow_hbundle.buda")
    fpc.export_hbundle_script(state, out, visualize=False)
    text = open(out).read()
    open_lines = [ln for ln in text.splitlines() if ln.startswith("open_bdb")]
    assert open_lines, text
    # Pre-fix the open_bdb path echoed the relative bdb_path, which resolves
    # against the CLI subprocess cwd (=_ROOT), not the GUI cwd.
    bdb_arg = open_lines[0].split()[1]
    assert os.path.isabs(bdb_arg), open_lines[0]
