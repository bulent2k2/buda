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

"""Regression tests for the slice-3 audit fixes (wrong-but-loud / leak-perf /
cosmetic). Each encodes the corrected behavior of one confirmed finding and
was verified to FAIL against the pre-fix code.

Findings covered here (headless-reachable): P5-04/05/06/07, P4-06, C6-04,
C8-03, C11-05, C11-06, C11-07. Log-only (C3-02) and GUI-path (P6/P7)
findings are verified by inspection / corpus A/B, not unit tests.
"""

import contextlib
import io
import os
import subprocess
import sys

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import buda
import buda_db
import buda_cli
from subprocess_env import buda_env


# ──────────────────────────────────────────────────────────────────────────
# P2-02 — clone-generation knobs overlay a bundle's persisted v15 memo
# ──────────────────────────────────────────────────────────────────────────

def test_clone_gen_knobs_overlays_persisted_memo(tmp_path):
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = buda_db.BDB(str(tmp_path / "k.bdb"))
    for bid in ("7", "8"):
        br = buda_db.BundleRow(); br.id = bid
        s.bdb.add_bundle(br)          # the memo FKs to a bundle row
    # (center, double_detour, multi_trunk, hanan_loci, spine_relays)
    base = (False, False, False, True, False)   # the all-default resume fallback

    # No memo → base is returned unchanged.
    assert s._clone_gen_knobs(base, 9) == base

    # An opt-in memo turns its knob on and an opt-out flips loci off — the
    # resume path (base = all-default) would otherwise generate coarser.
    s.bdb.set_bundle_gen_knobs("7", "multi_trunk no_hanan_loci")
    assert s._clone_gen_knobs(base, 7) == (False, False, True, False, False)

    s.bdb.set_bundle_gen_knobs("8", "center_mode hanan_loci spine_relays")
    assert s._clone_gen_knobs((False, False, False, False, False), 8) == \
        (True, False, False, True, True)


def _mini_session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ("def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
                  "add_block A 0 0 100 100", "add_block B 300 0 400 100",
                  "add_net n0 A.tx B.rx", "run_bundler strict",
                  "generate_topologies", "run_planner 3", "run_nuts"):
            s.do_command(c)
    return s


def _cap(session, cmd):
    b = io.StringIO()
    with contextlib.redirect_stdout(b):
        session.do_command(cmd)
    return b.getvalue()


# ──────────────────────────────────────────────────────────────────────────
# C10-03 — a net with an OUTPUT driver but UNKNOWN sinks is still bundled
# ──────────────────────────────────────────────────────────────────────────

def test_hier_bundler_output_driver_unknown_sink(tmp_path):
    # producer declares `output y` (→ OUTPUT driver); consumer declares no
    # port, so its sink pin stays UNKNOWN. Pre-fix the positional fallback
    # only fired when NO driver existed, so this net had no receivers and was
    # dropped entirely (never routed).
    v = tmp_path / "mixed.v"
    v.write_text(
        "module producer(output y);\nendmodule\n"
        "module consumer();\nendmodule\n"
        "module top();\n"
        "  wire sig;\n"
        "  producer u_src (.y(sig));\n"
        "  consumer u_dst (.a(sig));\n"
        "endmodule\n"
    )
    db = buda.BDB(":memory:")
    db.import_verilog(str(v))
    bundles = buda.HierarchicalBundler(db).run(2)
    bundled_nets = {n for b in bundles for n in b.net_names}
    assert "sig" in bundled_nets, [list(b.net_names) for b in bundles]


# ──────────────────────────────────────────────────────────────────────────
# P5-05 — `check_design all` audits at the current stage (was: unknown stage)
# ──────────────────────────────────────────────────────────────────────────

def test_check_design_all_audits_current_stage():
    s = _mini_session()
    out = _cap(s, "check_design all")
    # Pre-fix `all` was parsed as the stage token → "unknown stage 'all'".
    assert "unknown" not in out.lower(), out
    assert "Verifying NUTS-level design" in out, out


def test_check_design_bare_before_nuts_audits_topo():
    # A fresh pre-run session: bare `check_design` must audit at the topo
    # stage, not demand run_nuts (pre-fix it defaulted to dnuts and errored).
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ("def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
                  "add_block A 0 0 100 100", "add_block B 300 0 400 100",
                  "add_net n0 A.tx B.rx", "run_bundler strict",
                  "generate_topologies"):
            s.do_command(c)
    out = _cap(s, "check_design")
    assert "Verifying topology-level design" in out, out
    assert "run_nuts required" not in out, out


def test_check_design_rejects_unknown_arg():
    s = _mini_session()
    out = _cap(s, "check_design bogus")
    assert "unknown argument 'bogus'" in out, out


# ──────────────────────────────────────────────────────────────────────────
# P5-06 — run_detailed_nuts rejects an unrecognized bit-order token
# ──────────────────────────────────────────────────────────────────────────

def test_run_detailed_nuts_bad_bit_order_errors():
    s = _mini_session()
    out = _cap(s, "run_detailed_nuts hilo")
    # Pre-fix an unrecognized token was silently ignored, running LO_HI.
    assert "bit-order must be" in out, out
    assert s.detailed_result is None, "a rejected token must not run DNUTS"


def test_run_detailed_nuts_good_bit_order_still_works():
    s = _mini_session()
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("def_track_pattern 4 0 SIGNAL 1 1")
        s.do_command("def_track_pattern 5 0 SIGNAL 1 1")
    out = _cap(s, "run_detailed_nuts hi_lo")
    assert "bit-order must be" not in out
    assert s._detailed_bit_order == "HI_LO"


# ──────────────────────────────────────────────────────────────────────────
# P5-07 — edit_add_trunk with one lone span bound errors (not silent full-span)
# ──────────────────────────────────────────────────────────────────────────

def test_edit_add_trunk_lone_span_bound_errors():
    s = _mini_session()
    with contextlib.redirect_stdout(io.StringIO()):
        # Open a TopoEdit session on bundle 1's first candidate.
        s.do_command("edit_topology 1 1")
    out = _cap(s, "edit_add_trunk H 50 100")   # perp + ONE bound
    assert "must be given" in out.lower(), out
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("edit_abort")


# ──────────────────────────────────────────────────────────────────────────
# P5-04 — nested `source` resolves against the parent script, not the CWD
# ──────────────────────────────────────────────────────────────────────────

def test_source_resolves_against_parent_not_cwd(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "child.buda").write_text("add_block X 0 0 10 10\n")
    (scripts / "parent.buda").write_text("source child\n")

    # A DIFFERENT cwd that contains a colliding bare-name entry `child`.
    cwd = tmp_path / "run"
    cwd.mkdir()
    (cwd / "child").mkdir()   # unrelated directory named 'child'

    r = subprocess.run(
        [sys.executable, os.path.join(_ROOT, "src", "buda_cli.py"),
         str(scripts / "parent.buda"), "--no-viz"],
        cwd=str(cwd), capture_output=True, text=True,
        env=buda_env(_ROOT, "build"))
    # Pre-fix os.path.exists('child') in cwd was true, so '.buda' was not
    # appended and resolution against the parent dir found no file → exit(1).
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not found" not in (r.stdout + r.stderr).lower()


# ──────────────────────────────────────────────────────────────────────────
# P4-06 — _topo_min_slide reports 0 for a partially-pinched candidate
# ──────────────────────────────────────────────────────────────────────────

def test_topo_min_slide_counts_zero_slide_segment():
    s = _mini_session()

    class _Seg:
        def __init__(self, lo, hi):
            self.perp_lo, self.perp_hi = lo, hi

    class _CT:
        def build(self, topo, fp):
            pass

        def segs(self):
            # One free segment (slide 40) and one PINCHED (slide 0).
            return [_Seg(10, 50), _Seg(30, 30)]

    orig = buda.ConnTopology
    buda.ConnTopology = _CT
    try:
        v = s._topo_min_slide(object(), None)
    finally:
        buda.ConnTopology = orig
    # Pre-fix the zero-slide seg was filtered out, so min = 40; the C++
    # filter_pinched flags ANY zero-slide seg, so the report must show 0.
    assert v == 0, v


# ──────────────────────────────────────────────────────────────────────────
# C6-04 — clear_bundles(keep_user) keeps a kept child's parent (FK closure)
# ──────────────────────────────────────────────────────────────────────────

def test_clear_bundles_keeps_parent_of_kept_user_topology(tmp_path):
    db = buda_db.BDB(str(tmp_path / "c6.bdb"))
    par = buda_db.BundleRow(); par.id = "100"
    db.add_bundle(par)
    ch = buda_db.BundleRow(); ch.id = "101"; ch.parent_id = "100"
    db.add_bundle(ch)
    tr = buda_db.TopoRow()
    tr.id = "101"; tr.cand_index = 0; tr.source = "user"; tr.type = "L_HV"
    db.add_topology(tr)

    # Pre-fix this raised 'FOREIGN KEY constraint failed' (the parent, unused
    # by any topology, was deleted while the kept child still referenced it).
    db.clear_bundles(True)
    ids = {b.id for b in db.all_bundles()}
    assert ids == {"100", "101"}, ids


# ──────────────────────────────────────────────────────────────────────────
# C8-03 — import_gds bounds the AREF array explosion
# ──────────────────────────────────────────────────────────────────────────

def test_import_gds_bounds_aref_array(tmp_path):
    from tools import gds_build
    g = str(tmp_path / "big.gds")
    b = gds_build.GdsBuilder()
    b.structure("leaf").boundary(1, 0, [(0, 0), (1, 0), (1, 1), (0, 1)])
    b.structure("top").aref("leaf", (0, 0), cols=32767, rows=32767,
                            col_pitch=(2, 0), row_pitch=(0, 2))
    b.write(g)
    db = buda_db.BDB(str(tmp_path / "x.bdb"))
    with pytest.raises(RuntimeError, match="AREF array"):
        db.import_gds(g)


# ──────────────────────────────────────────────────────────────────────────
# C11-07 — write_bdb disambiguates same-leaf blocks of differing size
# ──────────────────────────────────────────────────────────────────────────

def test_floorplanner_write_bdb_cell_name_collision(tmp_path):
    e = buda.FloorplannerEngine()
    e.set_die(2000, 2000)
    e.add_block("top1", 0, 0, 50, 50)
    e.add_block("top1/core", 0, 0, 10, 10)
    e.add_block("top2", 100, 0, 160, 60)
    e.add_block("top2/core", 100, 0, 120, 20)
    # A real leaf block whose name equals the '/'->'_' flattening of the
    # ambiguous 'top1/core' path — the collision the PR #348 review flagged.
    e.add_block("top1_core", 500, 500, 540, 540)
    db = buda_db.BDB(str(tmp_path / "c11.bdb"))
    e.write_bdb(db)

    cells = {c.name: (c.width, c.height) for c in db.all_cells()}
    comps = {c.name: c.cell for c in db.all_components()}
    c1, c2 = comps["top1/core"], comps["top2/core"]
    # Pre-fix both cores shared 'core_cell' and the last write (20x20)
    # overwrote the first's dims.
    assert c1 != c2, comps
    assert cells[c1] == (10.0, 10.0) and cells[c2] == (20.0, 20.0), cells
    # The ambiguous path must NOT collide with the real leaf's cell — each
    # keeps its own dims (10x10 vs 40x40).
    assert comps["top1/core"] != comps["top1_core"], comps
    assert cells[comps["top1_core"]] == (40.0, 40.0), cells


# ──────────────────────────────────────────────────────────────────────────
# C11-05 — optimizer keeps blocks in-die after swaps of differing sizes
# (C11-06, the reshape min_w re-check, is a one-line guard mirroring the
#  adjacent min_h check; SA's accept/reject masks the sub-min_w reshape from
#  the observable final state, so it is verified by inspection, not here.)
# ──────────────────────────────────────────────────────────────────────────

def test_optimizer_swap_keeps_blocks_in_die():
    # C11-05: after many SA moves (incl. swaps of different-sized blocks),
    # every block must still lie fully inside the die. A big block swapped to
    # a small block's corner would, unclamped, hang past the die edge while
    # _cost reports overlap=0.
    DW, DH = 200.0, 200.0
    opt = buda.PlacementOptimizer(DW, DH, 1.0)
    opt.add_block_ex("big", 150.0, 150.0, x=0.0, y=0.0)
    opt.add_block_ex("sm1", 20.0, 20.0, x=150.0, y=0.0)
    opt.add_block_ex("sm2", 20.0, 20.0, x=0.0, y=150.0)
    opt.add_net([("big", (0.0, 0.0)), ("sm1", (0.0, 0.0)),
                 ("sm2", (0.0, 0.0))])
    res = opt.run_sa(400, w_wl=1.0, w_area=0.0, w_ovlp=1.0, seed=7)
    for p in res.placements:
        assert p.x >= -1e-6 and p.y >= -1e-6, (p.name, p.x, p.y)
        assert p.x + p.w <= DW + 1e-6 and p.y + p.h <= DH + 1e-6, \
            (p.name, p.x, p.y, p.w, p.h)
