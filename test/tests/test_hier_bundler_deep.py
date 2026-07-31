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

"""Deep-hierarchy bundler semantics (the recursive bottom-up enablement):

1. LCA cell context — a bundle whose endpoints share a non-root common
   ancestor DEEPER than a direct parent (an intermediate cell's own buses,
   e.g. the sub-chip's dnuts→dogleg buses in a 3-level design) is a
   cell-local template of that ancestor's cell, with busterm blocks at the
   children-of-LCA level.  The historical direct-parent test demoted these
   to per-occurrence one-offs, unreachable by bottom-up templating.

2. Mixed-depth endpoint retention — a net whose receivers sit at DIFFERENT
   leaf depths (a depth-3 leaf plus a depth-2 block) is CROSS-LEVEL, and
   every path-maximal endpoint stays in the bundle.  The historical
   deepest-only receiver rule silently DROPPED the shallower endpoint from
   the block contract (five chip3 top buses lost their big2 receivers), and
   the single-depth is_cross comparison classified the net same-level.

The fixture is a small nested build: a 2-level sub-design (built by
build_hier_demo from two flat cells) imported with --nest-bdb-cells beside a
flat cell, giving depth-3 leaves under the sub instances and depth-2 leaves
under the flat cell — the chip3 shape in miniature.
"""
import io
import contextlib
import os
import sys

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import buda_db  # noqa: E402
from tools import build_hier_demo  # noqa: E402
import buda_cli  # noqa: E402

pytestmark = pytest.mark.mid

_CELLS = [os.path.join(_ROOT, "flow", f)
          for f in ("dnuts1.buda", "dnuts2.buda")]


@pytest.fixture(scope="module")
def deep_bdb(tmp_path_factory):
    """chip -> {2x sub (nested, leaves at depth 3), 2x dnuts1 (flat, leaves
    at depth 2)} + top buses; the chip3 shape in miniature."""
    d = tmp_path_factory.mktemp("deep")
    sub = str(d / "sub.bdb")
    build_hier_demo.build(sub, _CELLS, seed=1, n_instances=2, n_buses=2)
    out = str(d / "deep.bdb")
    build_hier_demo.build(out, [("sub", sub), ("dnuts1", _CELLS[0])],
                          seed=3, n_instances=2, n_buses=6,
                          nest_bdb_cells=True)
    return out


def _bundle_session(deep_bdb):
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("def_layer 4 M4 H TOP 44.44")
        s.do_command("def_layer 5 M5 V TOP 50.00")
        s.do_command(f"open_bdb {deep_bdb}")
        s.do_command("derive_busterms 3")
        s.do_command("add_blocks_from_bdb 0")
        s.do_command("add_blocks_from_bdb 1 skip")
        s.do_command("add_blocks_from_bdb 2 skip")
        s.do_command("add_blocks_from_bdb 3 skip")
        s.do_command("run_hier_bundler depth 3")
    return s


def test_intermediate_cell_buses_get_lca_cell_context(deep_bdb):
    """The sub-design's own internal buses (endpoints in DIFFERENT child
    instances of a 'sub' occurrence) are cell-local templates of 'sub' —
    context assigned via the LCA, busterm blocks at the children-of-LCA
    level, merged template+replicas across the 2 occurrences."""
    s = _bundle_session(deep_bdb)
    sub_bundles = [w.input.original_bundle for w in s.bundles
                   if w.input.original_bundle.cell_context == "sub"]
    assert sub_bundles, "expected cell-local templates of the nested cell"
    templates = [b for b in sub_bundles if b.parent_id < 0]
    for b in templates:
        # Every template covers BOTH occurrences (no instance-subset
        # templates — the check_template_tracks 'excludes reference' trap).
        assert len(list(b.instances)) == 2, \
            (b.id, list(b.instances), b.get_net_names()[:2])
        # Busterm blocks are the LCA's children (the sub-cell's instances),
        # one level below the deep leaf endpoints.
        for tag in list(b.entry_busterm_ids) + list(b.exit_busterm_ids):
            path = tag.replace("bt:", "")
            assert path.count("/") == 2, tag   # chip/i_sub_k/<child>


def test_mixed_depth_endpoints_are_retained(deep_bdb):
    """A top bus reaching BOTH a depth-3 leaf (inside 'sub') and a depth-2
    leaf (inside the flat cell) keeps both endpoint sides in some bundle —
    the deepest-only rule used to drop the shallower one silently."""
    s = _bundle_session(deep_bdb)
    db = buda_db.BDB(deep_bdb)
    comps = {c.id: c for c in db.all_components()}
    # Find a top bus with mixed-depth leaf endpoints in the netlist.
    mixed = {}
    for n in db.all_nets():
        if not n.name.startswith("top_bus"):
            continue
        depths = set()
        for p in db.all_pins():
            if p.net_id != n.id:
                continue
            c = comps[p.comp_id]
            if c.is_leaf:
                depths.add(c.depth)
        if len(depths) > 1:
            mixed[n.name] = depths
    if not mixed:
        pytest.skip("seed produced no mixed-depth top bus")
    # Every mixed net's bundle must carry endpoints from BOTH depths: the
    # bundle reason (leaf-path signature on the cross-level path) names the
    # deep AND the shallow side.
    by_net = {}
    for w in s.bundles:
        for nm in w.input.original_bundle.get_net_names():
            by_net[nm] = w.input.original_bundle
    for nm in mixed:
        b = by_net.get(nm)
        assert b is not None, f"net {nm} not bundled"
        assert "i_sub_" in b.reason and "i_dnuts1_" in b.reason, \
            (nm, b.reason[:120])
        # And it is NOT mis-claimed as cell-local to the sub instance.
        assert b.cell_context != "sub", (nm, b.cell_context)


def _two_occurrence_db(tmp_path, fanin_sink_under_driver=False):
    """Hand-built nested shape: cell 'midc' has a DIRECT leaf child ('regf')
    AND a sub-instance ('u_core' of 'corec') with its own leaves — the
    cross-depth cell-local case.  Two congruent occurrences under one chip."""
    db = buda_db.BDB(str(tmp_path / "xdepth.bdb"))
    db.add_cell("leafc", 40.0, 40.0)
    db.add_cell("corec", 120.0, 100.0)
    db.add_inst_to_cell("corec", "alu", "leafc", 10.0, 10.0)
    db.add_inst_to_cell("corec", "lsu", "leafc", 70.0, 10.0)
    db.add_cell("midc", 320.0, 140.0)
    db.add_inst_to_cell("midc", "u_core", "corec", 10.0, 20.0)
    db.add_inst_to_cell("midc", "regf", "leafc", 200.0, 20.0)
    db.add_cell("topc", 800.0, 200.0)
    db.add_inst_to_cell("topc", "m0", "midc", 20.0, 20.0)
    db.add_inst_to_cell("topc", "m1", "midc", 420.0, 20.0)
    db.add_inst("chipX", "topc", "", 0.0, 0.0)
    for m in ("m0", "m1"):
        for i in range(4):
            # Cross-depth cell-local bus: depth-3 leaf -> depth-2 direct leaf.
            db.add_net_pins(f"chipX/{m}/xd_{i}",
                            f"chipX/{m}/u_core/alu.out",
                            [f"chipX/{m}/regf.in"])
            if fanin_sink_under_driver:
                # Deep-LCA multi-driver group whose shared sink shares a
                # child with one driver: drivers alu (in u_core) + regf
                # (direct), sink lsu (in u_core — same child as alu).
                db.add_net_pins(f"chipX/{m}/fi_a_{i}",
                                f"chipX/{m}/u_core/alu.o2",
                                [f"chipX/{m}/u_core/lsu.in"])
                db.add_net_pins(f"chipX/{m}/fi_b_{i}",
                                f"chipX/{m}/regf.o2",
                                [f"chipX/{m}/u_core/lsu.in"])
    return db


def _session_from_db(db_path):
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("def_layer 4 M4 H TOP 44.44")
        s.do_command("def_layer 5 M5 V TOP 50.00")
        s.do_command(f"open_bdb {db_path}")
        s.do_command("derive_busterms 3")
        s.do_command("add_blocks_from_bdb 0")
        s.do_command("add_blocks_from_bdb 1 skip")
        s.do_command("add_blocks_from_bdb 2 skip")
        s.do_command("add_blocks_from_bdb 3 skip")
    return s


def test_cross_depth_cell_local_nets_are_templated(tmp_path):
    """Review #540 P2: a cell-internal net wiring a nested child's leaf to a
    DIRECT child leaf classifies cross-level (mixed depths) but is still
    cell-local — the LCA mapping applies on the cross-level path too, so the
    two occurrences merge template+replica and generation routes it in the
    cell frame without crashing."""
    db = _two_occurrence_db(tmp_path)
    path = str(tmp_path / "xdepth.bdb")
    del db
    s = _session_from_db(path)
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_hier_bundler depth 3")
    xd = [w.input.original_bundle for w in s.bundles
          if any(n.startswith("chipX/m0/xd") or n.startswith("chipX/m1/xd")
                 for n in w.input.original_bundle.get_net_names())]
    assert xd, "expected the cross-depth bundles"
    for b in xd:
        assert b.cell_context == "midc", (b.id, b.cell_context, b.reason[:60])
    templates = [b for b in xd if b.parent_id < 0]
    assert len(templates) == 1 and len(list(templates[0].instances)) == 2
    # Busterm blocks at the children-of-LCA level: u_core + regf.
    tags = set(templates[0].entry_busterm_ids) | set(templates[0].exit_busterm_ids)
    assert any(tag.endswith("/u_core") for tag in tags), tags
    assert any(tag.endswith("/regf") for tag in tags), tags
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("generate_hier_topologies")
    for b in templates:
        w = next(w for w in s.bundles
                 if w.input.original_bundle.id == b.id)
        assert w.input.candidates, buf.getvalue()[-500:]


def test_deep_lca_fanin_sink_under_driver_child_keeps_exit(tmp_path):
    """Review #540 P1: a deep-LCA multi-driver group whose shared sink lives
    under the SAME child as one driver must keep a non-empty exit list (the
    entry-equal skip alone would empty it and crash generation on
    exits[0])."""
    db = _two_occurrence_db(tmp_path, fanin_sink_under_driver=True)
    path = str(tmp_path / "xdepth.bdb")
    del db
    s = _session_from_db(path)
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_hier_bundler depth 3 COMBINED")
    fi = [w.input.original_bundle for w in s.bundles
          if any("/fi_" in n for n in w.input.original_bundle.get_net_names())]
    assert fi, "expected the fan-in bundles"
    for b in fi:
        if not b.cell_context:
            continue
        assert list(b.exit_busterm_ids), \
            (b.id, b.reason[:80], list(b.entry_busterm_ids))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command("generate_hier_topologies")   # must not crash
