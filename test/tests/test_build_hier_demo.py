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

import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

import buda_db
from tools import build_hier_demo

# Builds full hierarchical BDBs (hundreds of components/pins) — heavier than a
# unit test, so it lives in the mid tier.
pytestmark = pytest.mark.mid

_CELLS = [os.path.join(_ROOT, "flow", f)
          for f in ("dnuts1.buda", "dnuts2.buda", "channel_stress.buda")]


@pytest.fixture(scope="module")
def default_demo_bdb(tmp_path_factory):
    """Build the default demo (`_CELLS`, seed=1) ONCE for the read-only tests
    that need it. The BDB assembly is ~4s and several tests otherwise rebuild
    the identical design; sharing collapses those into a single build. Tests
    using this fixture must treat the BDB as READ-ONLY (a test that runs the
    bundler / planner, which persist into the BDB, builds its own instead)."""
    path = str(tmp_path_factory.mktemp("demo_default") / "default.bdb")
    build_hier_demo.build(path, _CELLS, seed=1)
    return path


def _top_bus_coverage(db):
    """Map each depth-1 instance → number of DISTINCT top buses touching it."""
    cid2name = {c.id: c.name for c in db.all_components()}
    netname = {n.id: n.name for n in db.all_nets()}
    cov = {c.name: set() for c in db.all_components() if c.depth == 1}
    for p in db.all_pins():
        nm = netname[p.net_id]
        comp = cid2name[p.comp_id]
        if nm.startswith("top_bus") and comp in cov:
            cov[comp].add(nm.rsplit("_", 1)[0])   # strip _<bit> → bus prefix
    return {inst: len(buses) for inst, buses in cov.items()}


def test_resolve_cells_path_and_extension():
    bd = build_hier_demo
    ROOT = bd._ROOT
    flow = os.path.join(ROOT, "flow")
    # Bare names (no --path): looked up in flow/, .buda inferred.
    assert bd._resolve_cells(["dnuts1", "dnuts2"], None) == [
        ("dnuts1", os.path.join(flow, "dnuts1.buda")),
        ("dnuts2", os.path.join(flow, "dnuts2.buda"))]
    # An explicit .buda extension is not doubled.
    assert bd._resolve_cells(["dnuts1.buda"], None) == [
        ("dnuts1", os.path.join(flow, "dnuts1.buda"))]
    # The defaults resolve to the historical flow/*.buda set.
    assert bd._resolve_cells(bd._DEFAULT_CELLS, None) == [
        (os.path.splitext(f)[0], os.path.join(flow, f)) for f in
        ("dnuts1.buda", "dnuts2.buda", "channel_stress.buda")]
    # --path directory is used for bare names.  The expectation is BUILT with
    # os.path.join rather than written out: `_resolve_cells` abspath()s the
    # --path argument and joins with the platform separator, so a literal
    # "/tmp/cells/foo.buda" only ever matched on POSIX — on Windows the call
    # returns "C:\\tmp\\cells\\foo.buda".  (The other cases here are already
    # platform-neutral: `/abs/foo` stays as-is because ntpath.isabs accepts a
    # leading slash, and the dir-qualified ones join the same way both sides.)
    cells = os.path.abspath(os.path.join(os.sep, "tmp", "cells"))
    assert bd._resolve_cells(["foo", "bar"], cells) == [
        ("foo", os.path.join(cells, "foo.buda")),
        ("bar", os.path.join(cells, "bar.buda"))]
    # A directory-qualified entry stays relative to the repo root (back-compat).
    assert bd._resolve_cells(["flow/two.buda"], None) == [
        ("two", os.path.join(ROOT, "flow/two.buda"))]
    assert bd._resolve_cells(["flow/two"], None) == [
        ("two", os.path.join(ROOT, "flow/two.buda"))]
    # An absolute path is used as-is (extension still inferred), --path
    # ignored.  Built with abspath rather than the literal "/abs/...": a bare
    # leading slash is NOT absolute to ntpath on 3.13+ (isabs("/abs") is
    # False), so the POSIX literal exercised the dir-qualified branch and
    # came back on the repo drive (measured, windows-validate run 25).
    absfoo = os.path.join(os.path.abspath(os.sep), "abs", "foo")
    assert bd._resolve_cells([absfoo], None) == [("foo", absfoo + ".buda")]
    assert bd._resolve_cells([absfoo + ".buda"], "/ignored") == [
        ("foo", absfoo + ".buda")]


def test_resolve_cells_bdb_and_named_entries():
    """The chip-scale enhancement: NAME=PATH entries and .bdb/.bdb.sql cell
    sources (imported as BDB cells rather than parsed as flat scripts)."""
    bd = build_hier_demo
    ROOT = bd._ROOT
    # NAME=PATH names the cell explicitly.
    assert bd._resolve_cells(
        ["big2=flow/big_data_test/big2/tc3b_flat_x5.buda"], None) == [
        ("big2", os.path.join(ROOT, "flow/big_data_test/big2/tc3b_flat_x5.buda"))]
    # A .bdb.sql entry keeps its extension (no .buda inference) and derives
    # its default cell name from the basename minus the compound extension.
    assert bd._resolve_cells(["flow/rnr/mix2.bdb.sql"], None) == [
        ("mix2", os.path.join(ROOT, "flow/rnr/mix2.bdb.sql"))]
    absx = os.path.join(os.path.abspath(os.sep), "abs", "x.bdb")
    assert bd._resolve_cells([absx], None) == [("x", absx)]
    # Duplicate resolved names are a hard error (SystemExit).
    with pytest.raises(SystemExit):
        bd._resolve_cells(["dnuts1", "dnuts1=flow/rnr/mix2.bdb.sql"], None)


def test_bdb_cell_import_unplaced_root_rebases_on_leaves(tmp_path):
    """A source whose root is the canonical UNPLACED placeholder (-1..-1
    bbox, the DEF+Verilog merge state) must rebase on the placed leaves'
    extent — not on the -1 corner (review #529: every leaf shifted by +1)."""
    src_path = str(tmp_path / "unplaced_root.bdb")
    src = buda_db.BDB(src_path)
    src.add_cell("leafc", 10.0, 10.0)
    src.add_cell("topc", 20.0, 20.0)
    src.add_comp("rootX", "topc", "", -1.0, -1.0, -1.0, -1.0)
    src.add_comp("rootX/u1", "leafc", "rootX", 3.0, 4.0, 13.0, 14.0)
    src.add_comp("rootX/u2", "leafc", "rootX", 23.0, 4.0, 33.0, 14.0)
    del src

    out = buda_db.BDB(str(tmp_path / "target.bdb"))
    w, h, blocks, nets, centers = build_hier_demo._define_bdb_cell(
        out, "sub", src_path)
    # Size and origin from the leaves' own extent (not -1, not the die).
    assert (w, h) == (30.0, 10.0)
    assert blocks == ["u1", "u2"]
    assert centers["u1"] == (5.0, 5.0)      # 3..13 rebased to 0..10
    assert centers["u2"] == (25.0, 5.0)


def test_bdb_cell_import_drops_directed_self_receiver(tmp_path):
    """A directed source net with driver AND receiver pins on the same leaf
    keeps only the off-block receivers (bdb2buda's filter — review #529);
    the flat CLI rejects same-block directed endpoints."""
    src_path = str(tmp_path / "selfrcv.bdb")
    src = buda_db.BDB(src_path)
    src.add_cell("leafc", 10.0, 10.0)
    src.add_cell("topc", 40.0, 20.0)
    src.add_inst_to_cell("topc", "u1", "leafc", 0.0, 0.0)
    src.add_inst_to_cell("topc", "u2", "leafc", 20.0, 0.0)
    src.add_inst("rootY", "topc", "", 0.0, 0.0)
    src.add_net_pins("n_self", "rootY/u1.tx", ["rootY/u1.rx", "rootY/u2.rx"])
    src.add_net_pins("n_only_self", "rootY/u1.tx2", ["rootY/u1.rx2"])
    del src

    out = buda_db.BDB(str(tmp_path / "target2.bdb"))
    _w, _h, _blocks, nets, _c = build_hier_demo._define_bdb_cell(
        out, "sub", src_path)
    by_name = {n["name"]: n for n in nets}
    # The self-receiver is dropped, the cross-block one kept.
    assert by_name["n_self"]["rcvs"] == ["u2.rx"]
    # A directed net left with NO receivers is skipped entirely.
    assert "n_only_self" not in by_name


def test_bdb_cell_import_rejects_folded_name_collision(tmp_path):
    """'/'->'__' folding is not injective (a/b__c vs a__b/c); a silent
    collision would overwrite one child's definition (review #529) — the
    import must refuse loudly instead."""
    src_path = str(tmp_path / "collide.bdb")
    src = buda_db.BDB(src_path)
    src.add_cell("leafc", 5.0, 5.0)
    src.add_cell("m1", 10.0, 10.0)
    src.add_cell("m2", 10.0, 10.0)
    src.add_inst_to_cell("m1", "b__c", "leafc", 0.0, 0.0)
    src.add_inst_to_cell("m2", "c", "leafc", 0.0, 0.0)
    src.add_cell("topc", 30.0, 10.0)
    src.add_inst_to_cell("topc", "a", "m1", 0.0, 0.0)
    src.add_inst_to_cell("topc", "a__b", "m2", 15.0, 0.0)
    src.add_inst("rootZ", "topc", "", 0.0, 0.0)
    del src

    out = buda_db.BDB(str(tmp_path / "target3.bdb"))
    with pytest.raises(SystemExit):
        build_hier_demo._define_bdb_cell(out, "sub", src_path)


def test_nested_bdb_cell_import_deepens_hierarchy(tmp_path, default_demo_bdb):
    """--nest-bdb-cells: a BDB cell is imported PRESERVING its internal
    hierarchy — instantiating it materializes the source's own instances one
    level deeper (a 2-level source becomes depth 1..3 in the target), with
    hierarchical net names add_net_pins resolves through the deep tree."""
    src_db = buda_db.BDB(default_demo_bdb)
    src_leaves = sum(1 for c in src_db.all_components() if c.is_leaf)
    src_insts = sum(1 for c in src_db.all_components() if c.depth == 1)
    out = str(tmp_path / "chip3.bdb")
    build_hier_demo.build(out, [("sub", default_demo_bdb)],
                          seed=1, n_instances=2, n_buses=2,
                          nest_bdb_cells=True)
    db = buda_db.BDB(out)
    comps = db.all_components()
    hist = {}
    for c in comps:
        hist[c.depth] = hist.get(c.depth, 0) + 1
    # chip -> 2x sub -> sub's instances -> sub's leaves.
    assert hist[1] == 2
    assert hist[2] == 2 * src_insts
    assert hist[3] == 2 * src_leaves
    # Deep hierarchical net names (no '__' folding in the nested mode).
    deep = [n.name for n in db.all_nets() if n.name.count("/") >= 3]
    assert deep, "expected depth-3 hierarchical net names"
    # Deep leaf pins exist at depth 3 (interface pins re-propagated).
    leaf_ids = {c.id: c for c in comps if c.depth == 3}
    assert any(p.comp_id in leaf_ids for p in db.all_pins())


def test_nested_bdb_cell_import_unplaced_root_rebases(tmp_path):
    """The nested importer needs the same unplaced-root fallback as the flat
    one (review #538): a -1..-1 root rebases origin AND size on its placed
    depth-1 children's extent."""
    src_path = str(tmp_path / "nested_unplaced.bdb")
    src = buda_db.BDB(src_path)
    src.add_cell("leafc", 4.0, 4.0)
    src.add_cell("midc", 10.0, 10.0)
    src.add_comp("rootN", "midc_top", "", -1.0, -1.0, -1.0, -1.0)
    src.add_comp("rootN/a", "midc", "rootN", 3.0, 4.0, 13.0, 14.0)
    src.add_comp("rootN/a/u", "leafc", "rootN/a", 5.0, 6.0, 9.0, 10.0)
    src.add_comp("rootN/b", "midc", "rootN", 23.0, 4.0, 33.0, 14.0)
    src.add_comp("rootN/b/u", "leafc", "rootN/b", 25.0, 6.0, 29.0, 10.0)
    del src

    out = buda_db.BDB(str(tmp_path / "target.bdb"))
    w, h, blocks, _nets, centers = build_hier_demo._define_bdb_cell_nested(
        out, "sub", src_path)
    assert (w, h) == (30.0, 10.0)           # children's extent, not -1/die
    assert blocks == ["a/u", "b/u"]
    assert centers["a/u"] == (4.0, 4.0)     # rebased to origin (3,4)
    assert centers["b/u"] == (24.0, 4.0)


def test_build_derives_busterms_to_max_depth(tmp_path, default_demo_bdb):
    """With --nest-bdb-cells the built BDB has depth-3 leaves; busterm
    derivation (and the printed recipe) must reach the ACTUAL max depth, not
    the historical 2 (review #538) — else the deepest cell-internal buses
    never enter hierarchical bundling."""
    out = str(tmp_path / "deep.bdb")
    build_hier_demo.build(out, [("sub", default_demo_bdb)],
                          seed=1, n_instances=2, n_buses=2,
                          nest_bdb_cells=True)
    db = buda_db.BDB(out)
    max_comp_depth = max(c.depth for c in db.all_components())
    assert max_comp_depth == 3
    bt_depths = {bt.depth for bt in db.all_busterms()}
    assert max(bt_depths) == 3, bt_depths


def test_align_occurrences_snaps_to_shared_rows(tmp_path):
    """--align-occurrences: same-cell instances snap to the class-median
    row/column when the move stays overlap-free, so their congruent block
    edges coincide in the flat Hanan grid.  The default single-row layout is
    already y-aligned; verify the invariants on a built BDB: every cell
    class's instances share y, and no two instances overlap."""
    out = str(tmp_path / "aligned.bdb")
    build_hier_demo.build(out, _CELLS[:2], seed=1, n_instances=3, n_buses=2,
                          align_occurrences=True)
    db = buda_db.BDB(out)
    insts = [c for c in db.all_components() if c.depth == 1]
    by_cell = {}
    for c in insts:
        by_cell.setdefault(c.cell, []).append(c)
    for cell, group in by_cell.items():
        ys = {c.y1 for c in group}
        assert len(ys) == 1, f"{cell} instances not row-aligned: {ys}"
    for i, a in enumerate(insts):
        for b in insts[i + 1:]:
            assert not (a.x1 < b.x2 and a.x2 > b.x1
                        and a.y1 < b.y2 and a.y2 > b.y1), \
                f"overlap {a.name} vs {b.name}"


def test_bdb_cell_import_roundtrip(tmp_path, default_demo_bdb):
    """Import a previously-built hierarchical BDB as a CELL of a new chip
    (_define_bdb_cell): its leaf blocks appear as '__'-folded child blocks of
    every instance, and its nets (cell-internal AND its own top buses) are
    replicated per instance as the new chip's cell-internal nets."""
    src_db = buda_db.BDB(default_demo_bdb)
    n_src_leaves = sum(1 for c in src_db.all_components() if c.is_leaf)
    out = str(tmp_path / "chip2.bdb")
    build_hier_demo.build(out, [("sub", default_demo_bdb)],
                          seed=1, n_instances=2, n_buses=2)
    db = buda_db.BDB(out)
    comps = db.all_components()
    insts = [c for c in comps if c.depth == 1]
    assert sorted(c.name for c in insts) == ["chip/i_sub_0", "chip/i_sub_1"]
    leaves = [c for c in comps if c.is_leaf]
    # Every source leaf appears once per instance, path-folded with '__'.
    assert len(leaves) == 2 * n_src_leaves
    assert any("__" in c.name.rsplit("/", 1)[-1] for c in leaves)
    # The source's nets are replicated into BOTH instances with
    # instance-qualified names (source top buses become cell-internal here).
    names = [n.name for n in db.all_nets()]
    for inst in ("chip/i_sub_0", "chip/i_sub_1"):
        assert any(nm.startswith(f"{inst}/") for nm in names)
    # Net-count identity: each instance carries every >=2-leaf-pin source net.
    src_leaf_ids = {c.id for c in src_db.all_components() if c.is_leaf}
    per_net = {}
    for p in src_db.all_pins():
        if p.comp_id in src_leaf_ids:
            per_net[p.net_id] = per_net.get(p.net_id, 0) + 1
    n_expected = sum(1 for k, v in per_net.items() if v >= 2)
    n_cell_nets = sum(1 for nm in names if nm.startswith("chip/i_sub_"))
    assert n_cell_nets == 2 * n_expected


def test_optimizer_param_parsing():
    bd = build_hier_demo
    assert bd._parse_param("iter=20k") == ("iter", 20000)
    assert bd._parse_param("iter=1m") == ("iter", 1_000_000)
    assert bd._parse_param("wl=2.0") == ("wl", 2.0)
    assert bd._parse_param("seed=7") == ("seed", 7)
    # Friendly keys map to the right run_sa/run_ga kwargs.
    sa = bd._opt_kwargs("sa", {"iter": 5000, "wl": 2.0}, default_seed=3)
    assert sa["max_iter"] == 5000 and sa["w_wl"] == 2.0 and sa["seed"] == 3
    ga = bd._opt_kwargs("ga", {"iter": 100, "pop": 40}, default_seed=3)
    assert ga["generations"] == 100 and ga["population"] == 40
    # Defaults applied when iter omitted.
    assert bd._opt_kwargs("sa", {}, 1)["max_iter"] == 20000
    assert bd._opt_kwargs("ga", {}, 1)["generations"] == 200


def test_runtime_param_parsing():
    bd = build_hier_demo
    assert bd._parse_time("5s") == 5.0
    assert bd._parse_time("2m") == 120.0
    assert bd._parse_time("1h") == 3600.0
    assert bd._parse_time("30") == 30.0
    # 'time=2m' is 2 minutes, NOT 2 million.
    assert bd._parse_param("time=2m") == ("time", 120.0)
    assert bd._parse_param("patience=15") == ("patience", 15)
    # Time budget maps to time_budget_s; iter becomes 0 (no cap) + patience on.
    sa = bd._opt_kwargs("sa", {"time": 0.5}, default_seed=1)
    assert sa["time_budget_s"] == 0.5 and sa["max_iter"] == 0 and sa["patience"] == 10
    ga = bd._opt_kwargs("ga", {"runtime": 0.5}, default_seed=1)
    assert ga["time_budget_s"] == 0.5 and ga["generations"] == 0
    # Iteration mode unchanged (no time budget, no auto-patience).
    it = bd._opt_kwargs("sa", {"iter": 5000}, 1)
    assert it["max_iter"] == 5000 and "time_budget_s" not in it and "patience" not in it


def test_optimize_with_runtime_budget_builds(tmp_path):
    out = str(tmp_path / "tb.bdb")
    build_hier_demo.build(out, [os.path.join(_ROOT, "flow", "dnuts2.buda")],
                          seed=1, optimize="sa", opt_params={"time": 0.3})
    db = buda_db.BDB(out)
    assert len([c for c in db.all_components() if c.depth == 1]) == 2


def test_bloat_parsing_and_sizing():
    bd = build_hier_demo
    assert bd._parse_bloat("20%") == {"pct": 20.0}
    assert bd._parse_bloat("50") == {"dx": 50.0, "dy": 50.0}
    assert bd._parse_bloat("dx=50,dy=80") == {"dx": 50.0, "dy": 80.0}
    assert bd._bloated_size(100, 80, {"pct": 25}) == (125.0, 100.0)
    assert bd._bloated_size(100, 80, {"dx": 40, "dy": 20}) == (140.0, 100.0)
    assert bd._bloated_size(100, 80, None) == (100, 80)


def test_optimize_sa_places_instances_compactly(tmp_path):
    out = str(tmp_path / "opt.bdb")
    build_hier_demo.build(out, _CELLS, seed=1, optimize="sa",
                          opt_params={"iter": 2000}, bloat={"pct": 25})
    db = buda_db.BDB(out)
    insts = [c for c in db.all_components() if c.depth == 1]
    assert len(insts) == 6
    # Optimized layout is 2D (not the row where every instance has y1 == 0).
    assert any(c.y1 > 0 for c in insts), "optimizer should spread in Y"
    # And more compact in X than the 3740-wide row.
    assert max(c.x2 for c in insts) < 3740


def test_optimize_is_deterministic(tmp_path):
    a, b = str(tmp_path / "a.bdb"), str(tmp_path / "b.bdb")
    kw = dict(seed=5, optimize="sa", opt_params={"iter": 1500}, bloat={"pct": 20})
    build_hier_demo.build(a, _CELLS, **kw)
    build_hier_demo.build(b, _CELLS, **kw)

    def _inst_boxes(path):
        db = buda_db.BDB(path)
        return {c.name: (c.x1, c.y1, c.x2, c.y2)
                for c in db.all_components() if c.depth == 1}

    assert _inst_boxes(a) == _inst_boxes(b)


def test_export_flow_covers_full_hierarchy(tmp_path, default_demo_bdb):
    # Export Flow must report the design's full depth and load every level.
    import floorplanner_commands as fpc
    bdb = default_demo_bdb                             # chip(0) → inst(1) → leaf(2)
    state = fpc.load_bdb(bdb)
    assert fpc.design_max_depth(state) == 2
    script = str(tmp_path / "full_flow.buda")
    depth = fpc.export_hbundle_script(state, script, visualize=False)
    assert depth == 2
    text = open(script).read()
    for d in (0, 1, 2):                                # full hierarchy loaded
        assert (f"add_blocks_from_bdb {d}" in text)
    assert "derive_busterms 2" in text
    assert "run_hier_bundler depth 2" in text
    assert "run_detailed_nuts" in text
    # Self-contained tech sidecar with layers + track patterns.
    sidecar = tmp_path / "full_flow_tracks.buda"
    assert sidecar.exists()
    sc = sidecar.read_text()
    assert "def_layer" in sc and "def_track_pattern" in sc


def test_hier_flow_run_nuts_does_not_crash(tmp_path):
    # Regression: run_nuts after run_planner hier used to segfault because the
    # flat Floorplan (and so its Hanan grid) is empty in the hier flow, and
    # NUTS extract_segments dereferenced an empty grid.  Drive the documented
    # hier flow end-to-end in-process and assert it places segments.
    import buda_cli  # on pythonpath via pytest.ini (build src)
    out = str(tmp_path / "hf.bdb")
    build_hier_demo.build(out, [os.path.join(_ROOT, "flow", "dnuts2.buda")], seed=1)
    sess = buda_cli.BudaSession()
    for cmd in (f"open_bdb {out}", "run_hier_bundler depth 2",
                "generate_hier_topologies", "run_planner hier", "run_nuts"):
        sess.do_command(cmd)
    assert sess.nuts_result is not None
    assert len(sess.nuts_result.segments) > 0


def test_build_hier_demo_hierarchy_and_buses(default_demo_bdb):
    db = buda_db.BDB(default_demo_bdb)
    comps = db.all_components()
    by_depth = {}
    for c in comps:
        by_depth.setdefault(c.depth, []).append(c)

    # chip (top) → 6 instances → leaf blocks (2 × (4 + 4 + 16) = 48).
    assert len(by_depth[0]) == 1
    assert by_depth[0][0].name == "chip"
    assert len(by_depth[1]) == 6
    assert len(by_depth[2]) == 48

    # Cell-internal nets replicated ×2: dnuts1 128 + dnuts2 16 +
    # channel_stress 200 = 344 per set, ×2 = 688.
    names = {n.name for n in db.all_nets()}
    assert sum(1 for n in names if n.startswith("chip/i_")) == 688
    # 7 base top buses (70 nets) plus any coverage-repair buses (≥70 nets).
    assert sum(1 for n in names if n.startswith("top_bus")) >= 70
    # Every instance is wired to ≥3 top buses.
    assert min(_top_bus_coverage(db).values()) >= 3


def test_cell_internal_nets_replicated_per_instance(default_demo_bdb):
    names = {n.name for n in buda_db.BDB(default_demo_bdb).all_nets()}
    # Same cell net exists once per instance, with the instance-path prefix.
    assert "chip/i_dnuts1_0/n11_0" in names
    assert "chip/i_dnuts1_1/n11_0" in names
    assert "chip/i_dnuts2_0/b1_0" in names
    assert "chip/i_dnuts2_1/b1_0" in names
    # channel_stress contributes its sourced nets too.
    assert any(n.startswith("chip/i_chan_0/") for n in names)


def test_cell_internal_nets_are_intra_instance(default_demo_bdb):
    db = buda_db.BDB(default_demo_bdb)
    cid2name = {c.id: c.name for c in db.all_components()}
    nid = {n.name: n.id for n in db.all_nets()}["chip/i_dnuts1_0/n11_0"]
    pins = [p for p in db.all_pins() if p.net_id == nid]
    assert pins
    # Every pin sits on a component under the one instance (no cross-instance).
    assert all(cid2name[p.comp_id].startswith("chip/i_dnuts1_0/") for p in pins)


def test_cell_nets_templated_by_hier_bundler(tmp_path):
    # The core goal: the two instances of each cell bundle into ONE template.
    import buda
    out = str(tmp_path / "tmpl.bdb")
    build_hier_demo.build(out, _CELLS, seed=1)   # build derives busterms
    db = buda_db.BDB(out)
    hbs = buda.HierarchicalBundler(db).run(2)    # depth 2 reaches cell-internal nets
    dnuts1 = [h for h in hbs if h.cell_context == "dnuts1"]
    assert dnuts1, "expected dnuts1 cell-level bundles"
    # At least one template covers BOTH occurrences.
    templated = [h for h in dnuts1
                 if set(h.instances) >= {"chip/i_dnuts1_0", "chip/i_dnuts1_1"}]
    assert templated, (
        "the two dnuts1 instances should merge into one template; "
        f"got instance sets {[list(h.instances) for h in dnuts1]}")


def test_no_cell_nets_flag(tmp_path):
    out = str(tmp_path / "lean.bdb")
    build_hier_demo.build(out, _CELLS, seed=1, cell_nets=False)
    db = buda_db.BDB(out)
    names = {n.name for n in db.all_nets()}
    # Only top buses (the 7 base + any coverage-repair buses), no cell nets.
    assert names and all(n.startswith("top_bus") for n in names)
    assert len(names) >= 70
    assert not any(n.startswith("chip/i_dnuts1_0/") for n in names)
    assert min(_top_bus_coverage(db).values()) >= 3


@pytest.mark.parametrize("seed", [1, 2, 7])
def test_build_hier_demo_buses_are_hierarchical(tmp_path, seed):
    # Every top bus must be a genuine cross-instance net (common ancestor = top),
    # i.e. it must carry depth-1 interface pins on ≥2 distinct "chip/i_*"
    # ancestors — for ANY seed (regression for the all-in-one-instance case).
    out = str(tmp_path / f"hier_{seed}.bdb")
    build_hier_demo.build(out, _CELLS, seed=seed)

    db = buda_db.BDB(out)
    cid2name = {c.id: c.name for c in db.all_components()}
    nets = {n.name: n.id for n in db.all_nets()}
    assert "top_bus6_w16_15" in nets       # widest bus's last bit exists

    # Check the first bit-net of each of the 7 buses.
    for bi, w in enumerate(range(4, 17, 2)):
        nid = nets[f"top_bus{bi}_w{w}_0"]
        pins = [p for p in db.all_pins() if p.net_id == nid]
        assert any(p.dir == "OUTPUT" for p in pins)
        assert any(p.dir == "INPUT" for p in pins)
        # Depth-1 instance ancestors carrying the net's interface pin.
        iface_insts = {cid2name[p.comp_id] for p in pins
                       if p.pin_name == f"top_bus{bi}_w{w}_0"
                       and cid2name[p.comp_id].count("/") == 1}
        assert len(iface_insts) >= 2, (
            f"seed={seed} bus {bi} (w={w}) is not cross-instance: {iface_insts}")


def test_instances_count_configurable(tmp_path):
    # --instances N instantiates each cell N times: depth-1 = 3 cells × N,
    # depth-2 leaf blocks = N × (4 + 4 + 16) = 24N.
    out = str(tmp_path / "i3.bdb")
    build_hier_demo.build(out, _CELLS, seed=1, n_instances=3)
    db = buda_db.BDB(out)
    by_depth = {}
    for c in db.all_components():
        by_depth.setdefault(c.depth, []).append(c)
    assert len(by_depth[1]) == 9
    assert len(by_depth[2]) == 72
    # A third instance's name and its cell-internal nets exist.
    names = {n.name for n in db.all_nets()}
    assert "chip/i_dnuts1_2/n11_0" in names


def test_buses_count_configurable(tmp_path):
    # --buses N emits at least N base top buses, bit widths cycling the palette.
    out = str(tmp_path / "b10.bdb")
    build_hier_demo.build(out, _CELLS, seed=1, n_buses=10, cell_nets=False)
    names = {n.name for n in buda_db.BDB(out).all_nets()}
    # The 10 base buses are present with palette-cycled widths (bus 7→4, 8→6, 9→8).
    for bi in range(10):
        w = build_hier_demo._WIDTH_PALETTE[bi % 7]
        assert f"top_bus{bi}_w{w}_0" in names, f"missing base bus {bi}"
    assert "top_bus7_w4_3" in names       # last bit of the 8th bus (width 4)


def test_instances_per_cell_spec(tmp_path):
    # Per-cell instance counts: dict (by cell name) and positional list.
    bd = build_hier_demo
    assert bd._normalize_instances(3, ["a", "b"]) == {"a": 3, "b": 3}
    assert bd._normalize_instances([1, 4], ["a", "b"]) == {"a": 1, "b": 4}
    # Unlisted cells in a dict fall back to the default (2).
    assert bd._normalize_instances({"a": 5}, ["a", "b"]) == {"a": 5, "b": 2}
    # CLI value parsing.
    assert bd._parse_instances("3") == 3
    assert bd._parse_instances("2,3,1") == [2, 3, 1]
    assert bd._parse_instances("dnuts1=3,channel_stress=1") == \
        {"dnuts1": 3, "channel_stress": 1}

    # End-to-end: a positional spec yields the right per-cell instance counts.
    out = str(tmp_path / "percell.bdb")
    bd.build(out, _CELLS, seed=1, n_instances=[1, 4, 2])  # 1+4+2 = 7 instances
    db = buda_db.BDB(out)
    insts = [c.name for c in db.all_components() if c.depth == 1]
    assert len(insts) == 7
    assert sum("i_dnuts2_" in n for n in insts) == 4
    assert sum("i_chan_" in n for n in insts) == 2
    assert sum("i_dnuts1_" in n for n in insts) == 1


@pytest.mark.parametrize("spec", [2, 3, [1, 4, 2], {"channel_stress": 1}])
def test_every_instance_has_min_three_buses(tmp_path, spec):
    # The core guarantee: every depth-1 instance is wired to ≥3 top buses,
    # regardless of the (possibly per-cell) instance count.
    out = str(tmp_path / "cov.bdb")
    build_hier_demo.build(out, _CELLS, seed=1, n_instances=spec, cell_nets=False)
    db = buda_db.BDB(out)
    cov = _top_bus_coverage(db)
    assert cov, "expected depth-1 instances"
    assert min(cov.values()) >= 3, f"under-covered instances: {cov}"


def test_single_instance_builds_without_crash(tmp_path):
    # With one cell × one instance there is no cross-instance pair; the bus
    # selection must fall back to intra-instance receivers (no empty-choice raise).
    out = str(tmp_path / "single.bdb")
    build_hier_demo.build(out, [os.path.join(_ROOT, "flow", "dnuts1.buda")],
                          seed=1, n_instances=1, n_buses=3)
    db = buda_db.BDB(out)
    assert len([c for c in db.all_components() if c.depth == 1]) == 1
    names = {n.name for n in db.all_nets()}
    assert "top_bus0_w4_0" in names
    # (With a single instance the buses are intra-instance, so they cross no
    # instance boundary and carry no depth-1 interface pin — the ≥3 coverage
    # guarantee is meaningful only once there is more than one instance.)


def test_one_block_single_instance_no_crash(tmp_path):
    # Regression (Codex #82): a one-block cell with a single instance leaves the
    # whole design with one (inst, block) endpoint, so no driver+receiver pair
    # exists.  The builder must skip top buses gracefully, not raise IndexError.
    cell = tmp_path / "solo.buda"
    cell.write_text("add_block solo 0 0 100 100\n")
    out = str(tmp_path / "solo.bdb")
    build_hier_demo.build(out, [str(cell)], seed=1, n_instances=1)
    db = buda_db.BDB(out)
    assert len([c for c in db.all_components() if c.depth == 1]) == 1
    # No top buses could be formed; cell has no internal nets either.
    assert not any(n.name.startswith("top_bus") for n in db.all_nets())


def test_build_hier_demo_seed_is_deterministic(tmp_path):
    a = str(tmp_path / "a.bdb")
    b = str(tmp_path / "b.bdb")
    build_hier_demo.build(a, _CELLS, seed=7)
    build_hier_demo.build(b, _CELLS, seed=7)

    def _conn(path):
        db = buda_db.BDB(path)
        cid = {c.id: c.name for c in db.all_components()}
        out = set()
        for p in db.all_pins():
            out.add((p.net_id, cid[p.comp_id], p.pin_name, p.dir))
        # net_id is stable across identical builds (same insert order).
        return out

    assert _conn(a) == _conn(b)


# ── --layout stacked: on-grid vertical stacking ─────────────────────────────

def _depth1(db):
    return {c.name.split("/")[-1]: c for c in db.all_components() if c.depth == 1}


def test_stacked_layout_columns_and_grid(tmp_path):
    """Each cell type gets its own COLUMN (shared x), its instances stacked
    vertically on a pitch that is a multiple of --grid.  Both together are what
    make the instances phase-aligned as placed."""
    path = str(tmp_path / "stacked.bdb")
    build_hier_demo.build(path, _CELLS, seed=1, n_instances=3, n_buses=2,
                          layout="stacked", channel=100.0, grid=48.0)
    db = buda_db.BDB(path)
    comps = _depth1(db)
    by_cell = {}
    for c in comps.values():
        by_cell.setdefault(c.cell, []).append(c)
    assert len(by_cell) == 3
    xs = []
    for cell, insts in by_cell.items():
        insts.sort(key=lambda c: c.y1)
        assert len(insts) == 3
        # one shared x per column
        assert len({round(c.x1, 6) for c in insts}) == 1, cell
        xs.append(insts[0].x1)
        # vertical pitch is uniform AND a multiple of the grid
        pitches = [insts[i + 1].y1 - insts[i].y1 for i in range(len(insts) - 1)]
        assert len(set(round(p, 6) for p in pitches)) == 1, cell
        assert pitches[0] % 48.0 == 0, (cell, pitches[0])
        # ... and leaves at least the requested channel
        assert pitches[0] >= (insts[0].y2 - insts[0].y1) + 100.0
        # every instance origin is on the grid, so all share one track phase
        assert all(c.y1 % 48.0 == 0 for c in insts), cell
    assert len(set(xs)) == 3          # columns are distinct, left to right


def test_stacked_layout_channel_is_a_minimum(tmp_path):
    """The realized gap is the grid round-UP of the requested channel, never
    less than it."""
    path = str(tmp_path / "stacked2.bdb")
    build_hier_demo.build(path, _CELLS[:1], seed=1, n_instances=2, n_buses=0,
                          layout="stacked", channel=10.0, grid=1000.0)
    db = buda_db.BDB(path)
    insts = sorted(_depth1(db).values(), key=lambda c: c.y1)
    pitch = insts[1].y1 - insts[0].y1
    assert pitch % 1000.0 == 0
    assert pitch - (insts[0].y2 - insts[0].y1) >= 10.0


def test_row_layout_is_the_default_and_unchanged(tmp_path):
    """No layout argument = the historical row at y=0."""
    path = str(tmp_path / "row.bdb")
    build_hier_demo.build(path, _CELLS[:2], seed=1, n_instances=2, n_buses=0)
    db = buda_db.BDB(path)
    assert all(c.y1 == 0.0 for c in _depth1(db).values())


def test_column_align_top_flushes_without_breaking_the_grid(tmp_path):
    """--column-align top shifts each column up as a RIGID block: the tops go
    flush and every intra-column pitch is untouched, so --grid alignment
    survives.  (Shifting only the topmost instances would break it — the gap
    to the tallest column is not generally a multiple of the grid.)"""
    path = str(tmp_path / "flush.bdb")
    build_hier_demo.build(path, _CELLS, seed=1, n_instances=3, n_buses=2,
                          layout="stacked", channel=100.0, grid=48.0,
                          column_align="top")
    db = buda_db.BDB(path)
    by_cell = {}
    for c in _depth1(db).values():
        by_cell.setdefault(c.cell, []).append(c)
    tops = set()
    for cell, insts in by_cell.items():
        insts.sort(key=lambda c: c.y1)
        tops.add(round(insts[-1].y2, 6))
        pitches = [insts[i + 1].y1 - insts[i].y1 for i in range(len(insts) - 1)]
        assert len(set(round(p, 6) for p in pitches)) == 1, cell
        assert pitches[0] % 48.0 == 0, (cell, pitches[0])   # grid survives
    assert len(tops) == 1, tops                             # all columns flush


def test_column_align_center_is_symmetric_and_on_grid(tmp_path):
    """--column-align center splits each column's slack equally above and
    below — the placement half of mirror symmetry — again as a rigid shift, so
    the grid survives."""
    path = str(tmp_path / "centred.bdb")
    build_hier_demo.build(path, _CELLS, seed=1, n_instances=3, n_buses=2,
                          layout="stacked", channel=100.0, grid=48.0,
                          column_align="center")
    db = buda_db.BDB(path)
    comps = db.all_components()
    H = [c for c in comps if c.depth == 0][0].y2
    by_cell = {}
    for c in _depth1(db).values():
        by_cell.setdefault(c.cell, []).append(c)
    for cell, insts in by_cell.items():
        insts.sort(key=lambda c: c.y1)
        assert abs(insts[0].y1 - (H - insts[-1].y2)) < 1e-6, cell   # symmetric
        pitches = [insts[i + 1].y1 - insts[i].y1 for i in range(len(insts) - 1)]
        assert pitches[0] % 48.0 == 0, (cell, pitches[0])           # grid kept


def test_mirror_upper_reflects_every_block(tmp_path):
    """--mirror-upper flips the instances above the centreline, so the whole
    leaf-block set maps exactly onto its own reflection."""
    path = str(tmp_path / "mirror.bdb")
    build_hier_demo.build(path, _CELLS, seed=1, n_instances=2, n_buses=2,
                          layout="stacked", channel=100.0, grid=48.0,
                          column_align="center", mirror_upper=True)
    db = buda_db.BDB(path)
    comps = db.all_components()
    H = [c for c in comps if c.depth == 0][0].y2
    leaves = [c for c in comps if c.depth == 2]
    assert leaves
    S = {(round(c.x1, 3), round(c.y1, 3), round(c.x2, 3), round(c.y2, 3))
         for c in leaves}
    M = {(x1, round(H - y2, 3), x2, round(H - y1, 3)) for x1, y1, x2, y2 in S}
    assert S == M


def test_column_align_is_bottom_by_default(tmp_path):
    """Without it, columns are bottom-aligned at y=0 as before."""
    path = str(tmp_path / "noflush.bdb")
    build_hier_demo.build(path, _CELLS, seed=1, n_instances=2, n_buses=0,
                          layout="stacked", channel=100.0, grid=48.0)
    db = buda_db.BDB(path)
    assert min(c.y1 for c in _depth1(db).values()) == 0.0
    by_cell = {}
    for c in _depth1(db).values():
        by_cell.setdefault(c.cell, []).append(c)
    assert all(min(i.y1 for i in v) == 0.0 for v in by_cell.values())


def test_mirror_upper_refuses_a_centreline_straddler(tmp_path):
    """Codex #568: an odd instance count in a centred column puts the middle
    occurrence exactly on the centreline, where it cannot mirror onto itself —
    the layout would silently miss the contents-included guarantee.  Refuse."""
    with pytest.raises(SystemExit) as exc:
        build_hier_demo.build(str(tmp_path / "odd.bdb"), _CELLS[:2], seed=1,
                              n_instances=3, n_buses=1, layout="stacked",
                              channel=100.0, grid=48.0,
                              column_align="center", mirror_upper=True)
    msg = str(exc.value)
    assert "straddle" in msg and "EVEN instance count" in msg


def test_import_leaves_sigpipe_disposition_alone():
    """Importing this tool must NOT flip the process-wide SIGPIPE disposition.

    The CLI legitimately restores the shell default (die silently on a closed
    pipe — see the atomicity test below), but doing it at IMPORT time leaked
    SIG_DFL into every process that imported the module.  Under pytest-xdist
    each worker imports every test module at collection, so every worker ran
    the whole suite with SIG_DFL — and the qor sweep's crash-recovery test,
    whose broken pool makes the executor's queue-feeder thread take an EPIPE,
    silently killed its whole worker ("[gwN] node down") about every other
    full-suite run.  CPython's startup default (SIGPIPE ignored, EPIPE is an
    exception) must survive the import; only main() may change it.
    """
    import signal
    import importlib
    if not hasattr(signal, "SIGPIPE"):
        pytest.skip("no SIGPIPE on this platform (Windows) — the disposition "
                    "under test does not exist (measured, run 25)")
    assert signal.getsignal(signal.SIGPIPE) is not signal.SIG_DFL, (
        "SIGPIPE already SIG_DFL before re-import — another import leaked it")
    before = signal.getsignal(signal.SIGPIPE)
    importlib.reload(build_hier_demo)
    assert signal.getsignal(signal.SIGPIPE) == before, (
        "importing build_hier_demo changed the SIGPIPE disposition")


def test_build_is_atomic_under_a_closed_pipe(tmp_path):
    """A build killed partway must leave NO output BDB.

    Regression for a silent-correctness trap: this tool prints one line per
    bus, so `... | head -N` closed the pipe, SIGPIPE killed the build
    mid-write, and the half-finished BDB — missing nets, pins and every
    busterm — got serialized into a checked-in fixture.  It read as a valid
    design and routed fast and clean because most of the work was absent.
    """
    import subprocess
    out = tmp_path / "trunc.bdb"
    root = build_hier_demo._ROOT
    # -u is REQUIRED, not incidental: block-buffered stdout would hold the ~40
    # bus lines until exit, `head` would never close the pipe early, and the
    # test would fail without ever exercising SIGPIPE — passing or failing on
    # whether PYTHONUNBUFFERED happens to be set (bin/bb exports it; a bare
    # pytest run does not).  Codex #568.
    p1 = subprocess.Popen(
        [sys.executable, "-u",
         os.path.join(root, "tools", "build_hier_demo.py"),
         str(out), "--cells", "dnuts1,dnuts2", "--instances", "2",
         "--buses", "40"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=root,
        env={**os.environ, "PYTHONUNBUFFERED": "1"})
    p2 = subprocess.Popen(["head", "-3"], stdin=p1.stdout,
                          stdout=subprocess.DEVNULL)
    p1.stdout.close()
    p2.wait()
    p1.wait()
    assert not out.exists(), "a truncated build must not leave an output BDB"


def test_build_leaves_no_part_file_on_success(tmp_path):
    out = tmp_path / "ok.bdb"
    build_hier_demo.build(str(out), _CELLS[:2], seed=1, n_instances=2,
                          n_buses=2)
    assert out.exists()
    assert not (tmp_path / "ok.bdb.part").exists()
    db = buda_db.BDB(str(out))
    assert db.all_nets() and db.all_pins()      # fully written
