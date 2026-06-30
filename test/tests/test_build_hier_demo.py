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
        os.path.join(flow, "dnuts1.buda"), os.path.join(flow, "dnuts2.buda")]
    # An explicit .buda extension is not doubled.
    assert bd._resolve_cells(["dnuts1.buda"], None) == [os.path.join(flow, "dnuts1.buda")]
    # The defaults resolve to the historical flow/*.buda set.
    assert bd._resolve_cells(bd._DEFAULT_CELLS, None) == [
        os.path.join(flow, f) for f in
        ("dnuts1.buda", "dnuts2.buda", "channel_stress.buda")]
    # --path directory is used for bare names.
    assert bd._resolve_cells(["foo", "bar"], "/tmp/cells") == [
        "/tmp/cells/foo.buda", "/tmp/cells/bar.buda"]
    # A directory-qualified entry stays relative to the repo root (back-compat).
    assert bd._resolve_cells(["flow/two.buda"], None) == [os.path.join(ROOT, "flow/two.buda")]
    assert bd._resolve_cells(["flow/two"], None) == [os.path.join(ROOT, "flow/two.buda")]
    # An absolute path is used as-is (extension still inferred), --path ignored.
    assert bd._resolve_cells(["/abs/foo"], None) == ["/abs/foo.buda"]
    assert bd._resolve_cells(["/abs/foo.buda"], "/ignored") == ["/abs/foo.buda"]


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


def test_export_flow_covers_full_hierarchy(tmp_path):
    # Export Flow must report the design's full depth and load every level.
    import floorplanner_commands as fpc
    bdb = str(tmp_path / "full.bdb")
    build_hier_demo.build(bdb, _CELLS, seed=1)        # chip(0) → inst(1) → leaf(2)
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


def test_build_hier_demo_hierarchy_and_buses(tmp_path):
    out = str(tmp_path / "hier.bdb")
    build_hier_demo.build(out, _CELLS, seed=1)

    db = buda_db.BDB(out)
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


def test_cell_internal_nets_replicated_per_instance(tmp_path):
    out = str(tmp_path / "cell.bdb")
    build_hier_demo.build(out, _CELLS, seed=1)
    names = {n.name for n in buda_db.BDB(out).all_nets()}
    # Same cell net exists once per instance, with the instance-path prefix.
    assert "chip/i_dnuts1_0/n11_0" in names
    assert "chip/i_dnuts1_1/n11_0" in names
    assert "chip/i_dnuts2_0/b1_0" in names
    assert "chip/i_dnuts2_1/b1_0" in names
    # channel_stress contributes its sourced nets too.
    assert any(n.startswith("chip/i_chan_0/") for n in names)


def test_cell_internal_nets_are_intra_instance(tmp_path):
    out = str(tmp_path / "intra.bdb")
    build_hier_demo.build(out, _CELLS, seed=1)
    db = buda_db.BDB(out)
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
