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

"""
Phase D tests: generate_hier_topologies on the pipeline test vehicle.

Verifies that topology candidates are generated for all three bundle kinds:
  (a) depth-0 cross-block     src_i → proc_i
  (b) depth-1 cross-block     src_i/buf_i → proc_i/pa_i
  (c) depth-1 cell-level      pa_i → pb_i  (cell-local coords)
"""
import io
import contextlib
import sys
from pathlib import Path

import pytest
from pytest_bdd import scenarios, given, when, then, parsers
import buda

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

scenarios('features/hier_topology.feature')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_pipeline_bdb():
    db = buda.BDB(":memory:")
    db.add_cell("src_cell",  200, 200)
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("snk_cell",  200, 200)
    db.add_cell("gen_cell",   80,  80)
    db.add_cell("buf_cell",   80,  80)
    db.add_cell("pipe_cell", 110,  80)
    db.add_cell("rcv_cell",   80,  80)
    db.add_cell("store_cell", 80,  80)
    db.add_inst_to_cell("src_cell",  "gen_i",   "gen_cell",    20,  60)
    db.add_inst_to_cell("src_cell",  "buf_i",   "buf_cell",   100,  60)
    db.add_inst_to_cell("proc_cell", "pa_i",    "pipe_cell",   20,  60)
    db.add_inst_to_cell("proc_cell", "pb_i",    "pipe_cell",  155,  60)
    db.add_inst_to_cell("proc_cell", "pc_i",    "pipe_cell",  290,  60)
    db.add_inst_to_cell("snk_cell",  "rcv_i",   "rcv_cell",    20,  60)
    db.add_inst_to_cell("snk_cell",  "store_i", "store_cell", 100,  60)
    db.add_inst("src_i",  "src_cell",  "",  50,  50)
    db.add_inst("proc_i", "proc_cell", "", 350,  50)
    db.add_inst("snk_i",  "snk_cell",  "", 870,  50)
    return db


def _add_pipeline_nets(db):
    for i in range(8):
        db.add_net_pins(f"s2p_{i}",   "src_i/buf_i.out",  ["proc_i/pa_i.in"])
        db.add_net_pins(f"pa_pb_{i}", "proc_i/pa_i.out",  ["proc_i/pb_i.in"])
        db.add_net_pins(f"pb_pc_{i}", "proc_i/pb_i.out",  ["proc_i/pc_i.in"])
        db.add_net_pins(f"p2s_{i}",   "proc_i/pc_i.out",  ["snk_i/rcv_i.in"])


def _build_cell_local_fp(db, parent_comp_name):
    """Replicate _build_cell_local_floorplan for testing."""
    comps = {c.name: c for c in db.all_components()}
    parent = comps[parent_comp_name]
    fp = buda.Floorplan()
    for c in comps.values():
        if c.parent_id == parent.id and c.x1 >= 0:
            local_name = c.name.rsplit('/', 1)[-1]
            lx1 = int(round(c.x1 - parent.x1))
            ly1 = int(round(c.y1 - parent.y1))
            lx2 = int(round(c.x2 - parent.x1))
            ly2 = int(round(c.y2 - parent.y1))
            fp.add_block(local_name, lx1, ly1, lx2, ly2)
    return fp


def _setup_full_context():
    """Build DB, add nets, derive busterms, run hier bundler, generate topos."""
    db = _build_pipeline_bdb()
    _add_pipeline_nets(db)
    buda.BustermGen(db).derive(1)
    hb = buda.HierarchicalBundler(db)
    bundles = hb.run(1)
    return db, bundles


def _generate_hier_topologies(db, bundles):
    """Pure-Python replicate of generate_hier_topologies logic."""
    comps_list = db.all_components()
    comps = {c.name: c for c in comps_list}

    def build_depth_fp(depth):
        fp = buda.Floorplan()
        for c in comps_list:
            if c.depth == depth and c.x1 >= 0:
                fp.add_block(c.name, int(round(c.x1)), int(round(c.y1)),
                             int(round(c.x2)), int(round(c.y2)))
        return fp

    def build_cell_fp(parent_name):
        parent = comps.get(parent_name)
        if not parent:
            return None
        fp = buda.Floorplan()
        for c in comps_list:
            if c.parent_id == parent.id and c.x1 >= 0:
                lname = c.name.rsplit('/', 1)[-1]
                fp.add_block(lname, int(round(c.x1 - parent.x1)),
                             int(round(c.y1 - parent.y1)),
                             int(round(c.x2 - parent.x1)),
                             int(round(c.y2 - parent.y1)))
        return fp

    fp_cache = {}
    result = {}   # bundle_id → [Topology]

    for b in bundles:
        if b.cell_context and b.entry_busterm_ids:
            parent_name = b.instances[0] if b.instances else None
            if not parent_name:
                continue
            key = ('cell', parent_name)
            if key not in fp_cache:
                fp_cache[key] = build_cell_fp(parent_name)
            fp = fp_cache[key]
            if fp is None:
                continue
            src_local = b.entry_busterm_ids[0].removeprefix('bt:').rsplit('/', 1)[-1]
            dsts_local = [e.removeprefix('bt:').rsplit('/', 1)[-1]
                          for e in b.exit_busterm_ids]
            tg = buda.TopologyGenerator(fp)
            result[b.id] = tg.generate_candidates(src_local, dsts_local)
        else:
            # parse reason "DRV:x|REC:a,b,"
            try:
                drv_part, rec_part = b.reason.split('|REC:')
                src = drv_part[4:]
                dsts = [n for n in rec_part.split(',') if n]
            except ValueError:
                continue
            # Floorplan at the endpoints' depth: b.level is the routing-context
            # depth (common ancestor), which can be shallower than the
            # endpoint paths themselves.
            ep_depth = src.count('/')
            key = ('depth', ep_depth)
            if key not in fp_cache:
                fp_cache[key] = build_depth_fp(ep_depth)
            fp = fp_cache[key]
            tg = buda.TopologyGenerator(fp)
            result[b.id] = tg.generate_candidates(src, dsts)

    return result   # bundle_id → [Topology]


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def context():
    return {}


# ── Background ────────────────────────────────────────────────────────────────

@given("a BDB with the pipeline hierarchy, nets, and busterms")
def load_pipeline(context):
    db = _build_pipeline_bdb()
    _add_pipeline_nets(db)
    buda.BustermGen(db).derive(1)
    context['db'] = db


@given("run_hier_bundler has been called with max_depth 1")
def run_hier_bundler(context):
    hb = buda.HierarchicalBundler(context['db'])
    bundles = hb.run(1)
    context['bundles'] = bundles
    context['by_net'] = {n: b for b in bundles for n in b.net_names}


# ── When ──────────────────────────────────────────────────────────────────────

@when("generate_hier_topologies is called")
def step_generate_hier_topos(context):
    candidates = _generate_hier_topologies(context['db'], context['bundles'])
    context['candidates'] = candidates   # bundle_id → [Topology]


# ── Then ──────────────────────────────────────────────────────────────────────

@then("every bundle has at least 1 candidate")
def check_all_bundles_have_candidates(context):
    cands = context['candidates']
    for b in context['bundles']:
        n = len(cands.get(b.id, []))
        assert n >= 1, (
            f"Bundle {b.id} (level={b.level} reason={b.reason!r}) has 0 candidates"
        )


@then(parsers.re(r'there are (?P<n>\d+) bundles with candidates'))
def check_bundle_count_with_candidates(context, n):
    cands = context['candidates']
    with_cands = sum(1 for b in context['bundles'] if len(cands.get(b.id, [])) > 0)
    assert with_cands == int(n), f"Expected {n} bundles with candidates, got {with_cands}"


@then(parsers.re(r'every candidate segment for "(?P<net>[^"]+)" has x coords in range (?P<lo>-?\d+) (?P<hi>-?\d+)'))
def check_x_in_range(context, net, lo, hi):
    lo_f, hi_f = float(lo), float(hi)
    b = context['by_net'].get(net)
    assert b is not None, f"No bundle for net {net!r}"
    topos = context['candidates'].get(b.id, [])
    assert topos, f"No candidates for bundle of {net!r}"
    for topo in topos:
        for seg in topo.segments:
            for x in (seg.start.x, seg.end.x):
                assert lo_f - 1 <= x <= hi_f + 1, (
                    f"{net!r}: segment x={x} outside [{lo},{hi}] in topo {topo.type}"
                )


@then(parsers.re(r'every candidate segment for "(?P<net>[^"]+)" has y coords in range (?P<lo>-?\d+) (?P<hi>-?\d+)'))
def check_y_in_range(context, net, lo, hi):
    lo_f, hi_f = float(lo), float(hi)
    b = context['by_net'].get(net)
    assert b is not None, f"No bundle for net {net!r}"
    topos = context['candidates'].get(b.id, [])
    assert topos, f"No candidates for bundle of {net!r}"
    for topo in topos:
        for seg in topo.segments:
            for y in (seg.start.y, seg.end.y):
                assert lo_f - 1 <= y <= hi_f + 1, (
                    f"{net!r}: segment y={y} outside [{lo},{hi}] in topo {topo.type}"
                )


@then(parsers.re(r'at least one candidate segment for "(?P<net>[^"]+)" has x coord above (?P<threshold>-?\d+)'))
def check_any_x_above(context, net, threshold):
    t = float(threshold)
    b = context['by_net'].get(net)
    assert b is not None, f"No bundle for net {net!r}"
    topos = context['candidates'].get(b.id, [])
    assert topos, f"No candidates for bundle of {net!r}"
    found = any(max(seg.start.x, seg.end.x) > t
                for topo in topos for seg in topo.segments)
    assert found, (
        f"No segment for {net!r} with x > {threshold}; "
        f"max x seen: {max((max(s.start.x,s.end.x) for t in topos for s in t.segments), default='N/A')}"
    )


@then(parsers.re(r'the bundle for "(?P<net>[^"]+)" has at least (?P<n>\d+) candidate'))
def check_bundle_has_candidates(context, net, n):
    b = context['by_net'].get(net)
    assert b is not None, f"No bundle for net {net!r}"
    got = len(context['candidates'].get(b.id, []))
    assert got >= int(n), f"Bundle for {net!r}: expected ≥{n} candidates, got {got}"


# ── Standalone unit tests ─────────────────────────────────────────────────────

def test_cell_local_floorplan_coords():
    """Cell-local floorplan for proc_i has sub-components at correct relative positions."""
    db = _build_pipeline_bdb()
    fp = _build_cell_local_fp(db, "proc_i")
    block_names = {name for name, _ in fp.get_all_blocks()}
    assert "pa_i" in block_names, f"pa_i not in {sorted(block_names)}"
    # proc_i/pa_i abs (370,110,480,190); proc_i origin (350,50) → local (20,60,130,140)
    pa_rect = fp.get_block_bounds("pa_i")
    assert pa_rect.x1 == 20  and pa_rect.y1 == 60
    assert pa_rect.x2 == 130 and pa_rect.y2 == 140


def test_cell_local_topology_in_bounds():
    """pa_pb topology candidates lie within proc_cell dimensions (420×200)."""
    db = _build_pipeline_bdb()
    _add_pipeline_nets(db)
    buda.BustermGen(db).derive(1)
    _, bundles = _setup_full_context()
    cands = _generate_hier_topologies(db, bundles)
    by_net = {n: b for b in bundles for n in b.net_names}
    pa_pb = by_net["pa_pb_0"]
    topos = cands[pa_pb.id]
    assert topos, "pa_pb bundle produced no candidates"
    for topo in topos:
        for seg in topo.segments:
            for x in (seg.start.x, seg.end.x):
                assert -1 <= x <= 421, f"pa_pb: x={x} out of proc_cell width [0,420]"
            for y in (seg.start.y, seg.end.y):
                assert -1 <= y <= 201, f"pa_pb: y={y} out of proc_cell height [0,200]"


def test_cross_block_topology_absolute_coords():
    """s2p topology (level 0, leaf-precision endpoints) spans
    src_i/buf_i to proc_i/pa_i in absolute coords."""
    db = _build_pipeline_bdb()
    _add_pipeline_nets(db)
    buda.BustermGen(db).derive(1)
    _, bundles = _setup_full_context()
    cands = _generate_hier_topologies(db, bundles)
    # s2p bundle: src_i/buf_i (150-230) → proc_i/pa_i (370-480), level 0
    s2p_d0 = next((b for b in bundles if b.level == 0 and "s2p_0" in b.net_names), None)
    assert s2p_d0 is not None
    topos = cands[s2p_d0.id]
    assert topos, "s2p bundle produced no candidates"
    all_xs = [coord for t in topos for s in t.segments
              for coord in (s.start.x, s.end.x)]
    # src_i/buf_i extends to x=230; proc_i/pa_i starts at x=370 → must span > 300
    assert max(all_xs) > 300, f"s2p max x = {max(all_xs)}, expected > 300"


def test_all_bundles_get_candidates():
    """Every bundle (one per bus) receives at least one topology candidate."""
    db = _build_pipeline_bdb()
    _add_pipeline_nets(db)
    buda.BustermGen(db).derive(1)
    _, bundles = _setup_full_context()
    cands = _generate_hier_topologies(db, bundles)
    assert len(bundles) == 4, f"Expected 4 bundles, got {len(bundles)}"
    for b in bundles:
        n = len(cands.get(b.id, []))
        assert n >= 1, (
            f"Bundle {b.id} level={b.level} ctx={b.cell_context!r} "
            f"reason={b.reason!r}: 0 candidates"
        )


# ── multi_trunk opt-in in the hier flow ───────────────────────────────────────
#
# `generate_hier_topologies multi_trunk` must thread the flag through to the
# TopologyGenerator exactly like the flat `generate_topologies multi_trunk`, so
# the two-level BITRUNK_HVH/VHV datapath trees are generated for hier bundles.
# Before this was wired, the hier path built the generator without the flag and
# the keyword was silently ignored.

def _datapath_hier_db():
    """A depth-0 datapath: source S0 on the left fanning an 8-bit bus to two
    columns × five rows of receivers (aligned in x) — the column-aligned shape
    the BITRUNK_HVH two-level tree targets."""
    db = buda.BDB(":memory:")
    db.add_cell("s_cell", 80, 80)
    db.add_cell("d_cell", 80, 80)
    db.add_inst("S0", "s_cell", "", 0, 100)
    for g in range(2):
        for i in range(5):
            db.add_inst(f"D{g}_{i}", "d_cell", "", 500 + g * 350, 60 + i * 180)
    dsts = [f"D{g}_{i}.p" for g in range(2) for i in range(5)]
    for bit in range(8):
        db.add_net_pins(f"bus_{bit}", "S0.p", dsts)
    buda.BustermGen(db).derive(1)
    return db


def _run_hier_gen(multi_trunk: bool):
    """Drive a BudaSession through the hier flow (run_hier_bundler →
    generate_hier_topologies[ multi_trunk]) and return the per-bundle candidate
    type sets."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.bdb = _datapath_hier_db()
    gen = ("generate_hier_topologies multi_trunk" if multi_trunk
           else "generate_hier_topologies")
    cmds = ["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
            "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
            "run_hier_bundler", gen]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


# Only the two-level trees are gated behind the flag; legacy BITRUNK_H exists on
# the default path too (Codex #179 P2), so key on the two-level types exactly.
_TWO_LEVEL = ("BITRUNK_HVH", "BITRUNK_VHV")


def _types(s):
    return {c.type.split("@")[0] for w in s.bundles for c in w.input.candidates}


def _total(s):
    return sum(len(w.input.candidates) for w in s.bundles)


def test_generate_hier_topologies_multi_trunk_adds_two_level_trees():
    """generate_hier_topologies multi_trunk generates the two-level BITRUNK_HVH
    tree that the plain hier generate does not — proving the flag threads through
    the hier path (previously it was silently dropped)."""
    plain = _run_hier_gen(multi_trunk=False)
    multi = _run_hier_gen(multi_trunk=True)

    plain_types, multi_types = _types(plain), _types(multi)
    # The default hier path never emits a two-level tree.
    assert not (set(_TWO_LEVEL) & plain_types), (
        f"plain generate_hier_topologies should not emit two-level trees, "
        f"got {sorted(plain_types)}")
    # multi_trunk does — specifically BITRUNK_HVH for this column datapath.
    assert "BITRUNK_HVH" in multi_types, (
        f"expected BITRUNK_HVH under multi_trunk, got {sorted(multi_types)}")
    # And it only adds candidates (never drops the plain ones).
    assert _total(multi) >= _total(plain)


def test_generate_hier_topologies_default_no_multi_trunk():
    """Without the keyword the hier flow is unchanged — the default remains
    opt-out of the two-level trees (regression guard on the flag's default)."""
    plain = _run_hier_gen(multi_trunk=False)
    assert not (set(_TWO_LEVEL) & _types(plain))


def test_template_sharing_two_proc_instances():
    """Two instances of proc_cell share the same cell-local topology template."""
    db = buda.BDB(":memory:")
    db.add_cell("proc_cell", 420, 200)
    db.add_cell("pipe_cell", 110,  80)
    db.add_inst_to_cell("proc_cell", "pa_i", "pipe_cell",  20, 60)
    db.add_inst_to_cell("proc_cell", "pb_i", "pipe_cell", 155, 60)
    db.add_inst("proc_i1", "proc_cell", "",   0, 0)
    db.add_inst("proc_i2", "proc_cell", "", 500, 0)
    for i in range(4):
        db.add_net_pins(f"ab1_{i}", "proc_i1/pa_i.out", ["proc_i1/pb_i.in"])
        db.add_net_pins(f"ab2_{i}", "proc_i2/pa_i.out", ["proc_i2/pb_i.in"])
    buda.BustermGen(db).derive(1)
    bundles = buda.HierarchicalBundler(db).run(1)
    cands = _generate_hier_topologies(db, bundles)

    d1 = [b for b in bundles if b.level == 1]
    template = next((b for b in d1 if len(b.instances) == 2), None)
    assert template is not None, "No template bundle with 2 instances"

    # Template generates candidates in cell-local coords
    topos = cands.get(template.id, [])
    assert topos, "Template bundle produced no candidates"

    # All candidate segments should lie within proc_cell (420×200)
    for topo in topos:
        for seg in topo.segments:
            for x in (seg.start.x, seg.end.x):
                assert -1 <= x <= 421, f"template x={x} out of [0,420]"
