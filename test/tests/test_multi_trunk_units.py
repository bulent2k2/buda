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

"""Fixtureless multi-trunk (BITRUNK_HVH / BITRUNK_VHV) unit tests.

These are plain ``def test_*()`` functions with NO pytest fixtures so that
``tools/unit2buda.py`` can convert each one into a runnable/visualizable
``.buda`` script for eyeballing the input floorplan and the generated tree:

    tools/unit2buda.py test_column_datapath_hvh -o /tmp/col.buda
    bin/buda /tmp/col.buda          # opens the topology explorer

Each test builds its own ``buda.Floorplan`` + ``buda.TopologyGenerator`` and
calls ``generate_candidates`` directly (the exact API unit2buda records), so the
in-process assertions and the emitted script exercise the same case.  The
pytest-bdd behaviour specs in ``features/datapath_trunk.feature`` cover the same
shapes; this file is the unit2buda-friendly mirror.
"""
import contextlib
import io

import buda
import buda_cli
import pytest


# --- self-contained helpers (no conftest import: unit2buda loads this module
#     standalone, without test/tests on sys.path) -----------------------------

def _gen(blocks, driver, receivers, multi_trunk=True, h=4, v=5):
    """Build a floorplan + generator and return (fp, candidates)."""
    fp = buda.Floorplan()
    for name, (x1, y1, x2, y2) in blocks.items():
        fp.add_block(name, x1, y1, x2, y2)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(h, v)
    if multi_trunk:
        g.set_multi_trunk(True)
    return fp, g.generate_candidates(driver, receivers)


def _types(cands, prefix):
    return [c for c in cands if c.type.startswith(prefix)]


def _acyclic(ct):
    segs = list(ct.segs())
    n = len(segs)
    seen = [False] * n
    stack = [False] * n

    def dfs(i, parent):
        seen[i] = stack[i] = True
        for conn in segs[i].conns:
            if conn.kind != buda.SegConnKind.SEG:
                continue
            j = conn.seg_idx
            if not seen[j]:
                if dfs(j, i):
                    return True
            elif stack[j] and j != parent:
                return True
        stack[i] = False
        return False

    for i in range(n):
        if not seen[i] and dfs(i, -1):
            return False
    return True


def _root_with_branches(ct, root_horiz, min_branches=2):
    """True if some root-orientation segment is SEG-joined to >= min_branches
    perpendicular branch segments (the two-level BITRUNK signature)."""
    segs = list(ct.segs())
    for cs in segs:
        if cs.horiz != root_horiz:
            continue
        perp = sum(1 for c in cs.conns
                   if c.kind == buda.SegConnKind.SEG and segs[c.seg_idx].horiz != root_horiz)
        if perp >= min_branches:
            return True
    return False


# Two receiver columns (x~200 and x~500), three rows each — the canonical
# BITRUNK_HVH case: root H spine feeds two V branch trunks down the columns.
_COLUMN = {
    "S":  (0, 0, 60, 60),
    "A0": (200, 100, 260, 160), "A1": (200, 300, 260, 360), "A2": (200, 500, 260, 560),
    "B0": (500, 100, 560, 160), "B1": (500, 300, 560, 360), "B2": (500, 500, 560, 560),
}
_COLUMN_DSTS = ["A0", "A1", "A2", "B0", "B1", "B2"]

# Two receiver rows (y~200 and y~500) — the BITRUNK_VHV case: root V spine
# feeds two H branch trunks across the rows.
_ROW = {
    "S":  (0, 0, 60, 60),
    "A0": (100, 200, 160, 260), "A1": (300, 200, 360, 260), "A2": (500, 200, 560, 260),
    "B0": (100, 500, 160, 560), "B1": (300, 500, 360, 560), "B2": (500, 500, 560, 560),
}
_ROW_DSTS = ["A0", "A1", "A2", "B0", "B1", "B2"]


# --- tests ------------------------------------------------------------------

def test_column_datapath_hvh():
    """Two receiver columns -> a root-H / branch-V BITRUNK_HVH tree exists,
    connects all 7 blocks, and is acyclic."""
    fp, cands = _gen(_COLUMN, "S", _COLUMN_DSTS)
    hvh = _types(cands, "BITRUNK_HVH")
    assert hvh, f"no BITRUNK_HVH; got {sorted({c.type for c in cands})}"
    assert not _types(cands, "BITRUNK_VHV"), "columns should not yield a VHV tree"
    for c in hvh:
        ct = buda.ConnTopology(); ct.build(c, fp)
        assert len(set(c.connected_block_names)) >= 7, f"{c.type} misses blocks"
        assert _acyclic(ct), f"{c.type}: cycle"
        assert _root_with_branches(ct, root_horiz=True), \
            f"{c.type}: no horizontal root with >=2 vertical branches"


def test_row_datapath_vhv():
    """Two receiver rows -> a root-V / branch-H BITRUNK_VHV tree exists,
    connects all 7 blocks, and is acyclic."""
    fp, cands = _gen(_ROW, "S", _ROW_DSTS)
    vhv = _types(cands, "BITRUNK_VHV")
    assert vhv, f"no BITRUNK_VHV; got {sorted({c.type for c in cands})}"
    assert not _types(cands, "BITRUNK_HVH"), "rows should not yield an HVH tree"
    for c in vhv:
        ct = buda.ConnTopology(); ct.build(c, fp)
        assert len(set(c.connected_block_names)) >= 7, f"{c.type} misses blocks"
        assert _acyclic(ct), f"{c.type}: cycle"
        assert _root_with_branches(ct, root_horiz=False), \
            f"{c.type}: no vertical root with >=2 horizontal branches"


def test_passthrough_column_multitap():
    """A V branch trunk runs down the whole B-column and taps each block (a
    multi-tap pass-through): some HVH's V branch spans the full column y-extent."""
    fp, cands = _gen(_COLUMN, "S", _COLUMN_DSTS)
    ys = []
    for name in ("B0", "B1", "B2"):
        r = fp.get_block_bounds(name)
        ys += [r.y1, r.y2]
    y_lo, y_hi = min(ys), max(ys)
    for c in _types(cands, "BITRUNK_HVH"):
        ct = buda.ConnTopology(); ct.build(c, fp)
        for cs in ct.segs():
            if not cs.horiz and cs.along_lo <= y_lo and cs.along_hi >= y_hi:
                return
    pytest.fail(f"no BITRUNK_HVH V branch spans column [{y_lo},{y_hi}]")


def test_three_column_comb_hvh():
    """Three receiver columns -> the clustering yields BITRUNK_HVH trees (a comb
    of V branches off one H root); each connects all blocks and is acyclic."""
    blocks = {"S": (0, 0, 60, 60)}
    dsts = []
    for ci, x in enumerate((200, 500, 800)):
        for ri, y in enumerate((100, 300, 500)):
            name = f"C{ci}_{ri}"
            blocks[name] = (x, y, x + 60, y + 60)
            dsts.append(name)
    fp, cands = _gen(blocks, "S", dsts)
    hvh = _types(cands, "BITRUNK_HVH")
    assert hvh, f"no BITRUNK_HVH; got {sorted({c.type for c in cands})}"
    for c in hvh:
        ct = buda.ConnTopology(); ct.build(c, fp)
        assert len(set(c.connected_block_names)) >= len(dsts) + 1
        assert _acyclic(ct), f"{c.type}: cycle"
        assert _root_with_branches(ct, root_horiz=True)


def test_bitrunk_candidates_are_fully_annotated():
    """Single-source-of-topo-truth (Phase 1): every BITRUNK candidate — legacy
    BITRUNK_H and the two-level HVH/VHV trees — annotates seg_busterms for EVERY
    segment (root spine + branch trunks + leaf stubs), so ConnTopology never
    consults its geometric face-search fallback for them. See
    docs/internal/single_source_topo_truth.md."""
    fp, cands = _gen(_COLUMN, "S", _COLUMN_DSTS)   # multi_trunk on -> legacy + HVH
    bitrunks = [c for c in cands if c.type.startswith("BITRUNK")]
    assert bitrunks, "expected at least one BITRUNK candidate"
    saw_legacy = saw_tree = False
    for c in bitrunks:
        missing = [i for i in range(len(c.segments)) if i not in c.seg_busterms]
        assert not missing, \
            f"{c.type} segments {missing} unannotated (would hit the fallback)"
        saw_legacy |= (c.type == "BITRUNK_H")
        saw_tree |= c.type.startswith(("BITRUNK_HVH", "BITRUNK_VHV"))
    assert saw_legacy and saw_tree, "want both legacy + two-level shapes covered"


def test_bitrunk_trunk_endpoint_grazing_a_face_stays_a_junction():
    """Codex P1 guard: a BITRUNK trunk/stub endpoint that coincidentally lies on a
    NON-terminal (or sibling) block face must remain a wire junction, not become a
    spurious busterm. The generator completes trunk endpoints as null (authoritative
    junction) and records the stub↔trunk junction in seg_conns (topo-truth Phase 4's
    one-time annotation, mirrored here via annotate_seg_conns) — ConnTopology must
    NOT geometrically tap the grazed block (which would disconnect the stub)."""
    fp = buda.Floorplan()
    fp.add_block("own", 180, 0, 220, 40)      # stub taps this block's top face
    fp.add_block("nbr", 150, 100, 250, 140)   # bottom face y=100 grazes the stub's trunk-end

    t = buda.Topology()
    t.type = "BITRUNK_H"
    trunk = buda.Segment(); trunk.start = buda.Point(0, 100); trunk.end = buda.Point(400, 100)
    stub = buda.Segment(); stub.start = buda.Point(200, 40); stub.end = buda.Point(200, 100)
    t.segments = [trunk, stub]
    # generator-style annotation: stub start taps 'own'; every other endpoint is a
    # null junction entry (NOT geometrically annotated as a busterm).
    own_bt = buda.Busterm(); own_bt.block_name = "own"; own_bt.bbox = buda.Rect(180, 0, 220, 40)
    t.seg_busterms = {0: (None, None), 1: (own_bt, None)}
    # ...and the generator's one-time seg-to-seg junction annotation (the post-pass
    # every emitted candidate gets); ConnTopology no longer scans for touches.
    buda.annotate_seg_conns(t)

    ct = buda.ConnTopology(); ct.build(t, fp)
    taps = [cc.block_name for cs in ct.segs() for cc in cs.conns
            if cc.kind == buda.SegConnKind.BUSTERM]
    assert "nbr" not in taps, f"trunk-end grazing 'nbr' was mis-tapped: {taps}"
    assert "own" in taps
    # stub (seg 1) joins the trunk (seg 0) via a SEG junction, not through 'nbr'
    stub_segs = [cc.seg_idx for cc in ct.segs()[1].conns if cc.kind == buda.SegConnKind.SEG]
    assert 0 in stub_segs, "stub must connect to the trunk via SEG"


def test_optin_guard_default_has_no_bitrunk_trees():
    """Without the multi_trunk flag the two-level trees are suppressed, but the
    legacy single-level BITRUNK_H stays on the default path (byte-identical set)."""
    fp, cands = _gen(_COLUMN, "S", _COLUMN_DSTS, multi_trunk=False)
    assert not _types(cands, "BITRUNK_HVH"), "HVH leaked onto the default path"
    assert not _types(cands, "BITRUNK_VHV"), "VHV leaked onto the default path"
    assert any(c.type == "BITRUNK_H" for c in cands), \
        "legacy BITRUNK_H must remain on the default path"


# --- mid-tier end-to-end regression -----------------------------------------

def _run(session, cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            session.do_command(c)


def _column_flow_cmds():
    """A driver broadcasting an 8-bit bus down two receiver columns — the
    BITRUNK_HVH datapath case, as a flat .buda flow."""
    cmds = [
        "def_layer 4 M4 H TOP 50",
        "def_layer 5 M5 V TOP 50",
        "add_block S 0 0 60 480",
    ]
    for x in (200, 500):
        for y in (100, 300, 500):
            cmds.append(f"add_block {'A' if x==200 else 'B'}{(y-100)//200} "
                        f"{x} {y} {x+60} {y+60}")
    cmds.append("add_bus d[8] S.p A0.p,A1.p,A2.p,B0.p,B1.p,B2.p")
    cmds.append("run_bundler")
    return cmds


@pytest.mark.mid
def test_multi_trunk_flow_routes_clean():
    """End-to-end: generate multi_trunk candidates, pin a BITRUNK_HVH, and run
    planner -> NUTS.  The two-level tree must route with zero NUTS overlaps."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    _run(s, _column_flow_cmds() + ["generate_topologies multi_trunk"])

    # Find the (bundle_id, 1-based index) of a BITRUNK_HVH candidate.
    pinned = None
    for w in s.bundles:
        for i, cand in enumerate(w.input.candidates):
            if cand.type.startswith("BITRUNK_HVH"):
                pinned = (w.input.original_bundle.id, i + 1)
                break
        if pinned:
            break
    assert pinned, "no BITRUNK_HVH candidate generated in the flow"

    bid, idx = pinned
    _run(s, [f"select_topology {bid} {idx}", "run_planner", "run_nuts"])
    assert s.nuts_result is not None
    assert s.nuts_result.num_overlaps == 0, \
        f"BITRUNK_HVH routed with {s.nuts_result.num_overlaps} NUTS overlaps"


@pytest.mark.mid
def test_multi_trunk_flag_leaves_default_flow_unchanged():
    """The opt-in flag is purely additive: the default candidate set (per bundle,
    by type) is identical with and without `multi_trunk`, minus the new trees."""
    def _sets(gen_cmd):
        s = buda_cli.BudaSession(); s.no_viz = True
        _run(s, _column_flow_cmds() + [gen_cmd])
        return [sorted(c.type for c in w.input.candidates) for w in s.bundles]

    default = _sets("generate_topologies")
    multi = _sets("generate_topologies multi_trunk")
    assert len(default) == len(multi)
    for d, m in zip(default, multi):
        # Everything in the default set survives; the flag only ADDS BITRUNK trees.
        m_minus_new = [t for t in m if not t.startswith(("BITRUNK_HVH", "BITRUNK_VHV"))]
        assert m_minus_new == d, "default candidate set changed under multi_trunk"
