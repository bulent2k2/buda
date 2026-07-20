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

"""Hier-aware set_max_bundle_bits split.

The balanced bundle-bit-bound split used to be flat-only; run_hier_bundler
now splits over-limit TEMPLATE bundles BEFORE per-instance expansion, so the
split propagates identically to every instance through the template↔replica
linkage.  All HBundle hier metadata is preserved on every part, and the
per-net-aligned fan-in arrays (net_drivers / net_receivers) are split in the
same net partition (with each part re-scoped to the leaves its bits touch).
"""
import contextlib
import io

import pytest

import buda
import buda_cli


def _quiet(s, *cmds):
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)


def _cell_local_bdb(width):
    """One cell type (two leaves joined by a `width`-bit bus) instantiated
    twice — the canonical template↔replica scenario."""
    db = buda.BDB(":memory:")
    db.add_cell("core_cell", 300, 220)
    db.add_cell("pipe_cell", 110, 80)
    db.add_inst_to_cell("core_cell", "a_i", "pipe_cell", 20, 60)
    db.add_inst_to_cell("core_cell", "b_i", "pipe_cell", 175, 60)
    db.add_inst("core_i1", "core_cell", "", 0, 0)
    db.add_inst("core_i2", "core_cell", "", 0, 400)
    for i in range(width):
        db.add_net_pins(f"c1_bus_{i}", "core_i1/a_i.out", ["core_i1/b_i.in"])
        db.add_net_pins(f"c2_bus_{i}", "core_i2/a_i.out", ["core_i2/b_i.in"])
    buda.BustermGen(db).derive(1)
    return db


def _fanin_bdb(width):
    """Two driver blocks fanning into one shared sink (a same-level fan-in
    bundle under CONVERGENT)."""
    db = buda.BDB(":memory:")
    db.add_cell("d_cell", 60, 60)
    db.add_cell("s_cell", 80, 200)
    db.add_inst("da_i", "d_cell", "", 0, 0)
    db.add_inst("db_i", "d_cell", "", 0, 300)
    db.add_inst("snk_i", "s_cell", "", 500, 120)
    for i in range(width):
        db.add_net_pins(f"fa_{i}", "da_i.out", ["snk_i.in"])
        db.add_net_pins(f"fb_{i}", "db_i.out", ["snk_i.in"])
    buda.BustermGen(db).derive(1)
    return db


# ── the template split ────────────────────────────────────────────────────────

def test_static_split_preserves_template_metadata():
    """A wide cell-local template splits into balanced parts, each keeping the
    cell_context + instances so it replicates to every occurrence."""
    s = buda_cli.BudaSession(); s.no_viz = True
    s.bdb = _cell_local_bdb(12)
    _quiet(s, "set_max_bundle_bits 5")
    _quiet(s, "run_hier_bundler")
    template_parts = [w.input.original_bundle for w in s.bundles
                      if list(w.input.original_bundle.instances)
                      == ["core_i1", "core_i2"]]
    # ceil(12/5)=3 parts, balanced 4+4+4.
    assert len(template_parts) == 3
    assert sorted(len(b.get_net_names()) for b in template_parts) == [4, 4, 4]
    for b in template_parts:
        assert b.cell_context == "core_cell"
        assert list(b.instances) == ["core_i1", "core_i2"]
        assert "|SPLIT:" in b.reason
    # No bit is lost across the parts.
    allnets = set()
    for b in template_parts:
        allnets.update(b.get_net_names())
    assert allnets == {f"c1_bus_{i}" for i in range(12)}


def test_no_split_when_under_limit_is_identity():
    s = buda_cli.BudaSession(); s.no_viz = True
    s.bdb = _cell_local_bdb(4)
    _quiet(s, "set_max_bundle_bits 8")
    _quiet(s, "run_hier_bundler")
    assert all("|SPLIT:" not in w.input.original_bundle.reason
               for w in s.bundles)


def test_split_off_clears_bound():
    s = buda_cli.BudaSession(); s.no_viz = True
    s.bdb = _cell_local_bdb(12)
    _quiet(s, "set_max_bundle_bits 4", "set_max_bundle_bits off")
    _quiet(s, "run_hier_bundler")
    assert all("|SPLIT:" not in w.input.original_bundle.reason
               for w in s.bundles)


# ── auto (busterm-edge) cap on hier bundles ───────────────────────────────────

def test_auto_split_binds_on_cell_local_busterm_edge():
    """The AUTO cap resolves a cell-local leaf name to a congruent instance's
    child footprint (pipe_cell 110x80 → 80-unit edge) and splits accordingly."""
    s = buda_cli.BudaSession(); s.no_viz = True
    s.bdb = _cell_local_bdb(60)
    _quiet(s, "def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10",
           "def_track_pattern 4 0 SIGNAL 2 2", "def_track_pattern 5 0 SIGNAL 2 2")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        s.do_command("set_max_bundle_bits auto")
        s.do_command("run_hier_bundler")
    log = out.getvalue()
    assert "busterm edge" in log       # AUTO cap was the binding constraint
    template_parts = [w.input.original_bundle for w in s.bundles
                      if list(w.input.original_bundle.instances)
                      == ["core_i1", "core_i2"]]
    assert len(template_parts) > 1     # 60 bits did not fit one 80-unit edge


# ── fan-in split alignment ────────────────────────────────────────────────────

def test_fanin_split_keeps_per_net_endpoints_aligned():
    s = buda_cli.BudaSession(); s.no_viz = True
    s.bdb = _fanin_bdb(6)
    _quiet(s, "set_max_bundle_bits 4", "run_hier_bundler CONVERGENT")
    for w in s.bundles:
        b = w.input.original_bundle
        nets = list(b.get_net_names())
        drvs = list(b.net_drivers)
        assert len(drvs) == len(nets)                       # aligned
        # every fa_* net is driven by da_i, every fb_* by db_i
        for n, d in zip(nets, drvs):
            assert d == ("da_i" if n.startswith("fa_") else "db_i")


def test_fanin_split_rescopes_degenerate_part():
    """A part that ends up with a single driver re-scopes its FANIN reason to
    just that leaf (no spurious 0-bit stub to the absent driver)."""
    s = buda_cli.BudaSession(); s.no_viz = True
    s.bdb = _fanin_bdb(6)
    _quiet(s, "set_max_bundle_bits 4", "run_hier_bundler CONVERGENT")
    for w in s.bundles:
        b = w.input.original_bundle
        drivers = set(b.net_drivers)
        assert b.reason.startswith("FANIN:")
        leaves = b.reason.split("|FROM:")[1].split("|SPLIT:")[0]
        leaf_set = {x for x in leaves.split(",") if x}
        assert leaf_set == drivers          # reason lists exactly this part's drivers


# ── end-to-end: the split routes clean AND propagates to instances ────────────

@pytest.mark.mid
def test_split_propagates_to_instances_and_routes_clean():
    s = buda_cli.BudaSession(); s.no_viz = True
    s.bdb = _cell_local_bdb(12)
    _quiet(s,
           "def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10",
           "set_min_stub_length 2", "add_blocks_from_bdb 0",
           "add_blocks_from_bdb 1 skip",
           "set_max_bundle_bits 5", "run_hier_bundler",
           "generate_hier_topologies", "set_track_pitch 2.0",
           "run_planner hier 3", "run_nuts")
    # After expansion each instance sees the same 3-part split.
    per_inst = {}
    for w in s.bundles:
        b = w.input.original_bundle
        if b.instances and "|SPLIT:" in b.reason:
            per_inst.setdefault(list(b.instances)[0], []).append(b)
    assert set(per_inst) == {"core_i1", "core_i2"}
    for parts in per_inst.values():
        assert sorted(len(b.get_net_names()) for b in parts) == [4, 4, 4]
    assert s.nuts_result.num_overlaps == 0
    check = io.StringIO()
    with contextlib.redirect_stdout(check):
        s.do_command("check_design nuts")
    assert "Success" in check.getvalue()


@pytest.mark.mid
def test_fanin_split_routes_clean():
    s = buda_cli.BudaSession(); s.no_viz = True
    s.bdb = _fanin_bdb(6)
    _quiet(s,
           "def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10",
           "add_blocks_from_bdb 0", "set_max_bundle_bits 4",
           "run_hier_bundler CONVERGENT", "generate_hier_topologies",
           "set_track_pitch 2.0", "run_planner hier 3", "run_nuts")
    assert s.nuts_result.num_overlaps == 0
    check = io.StringIO()
    with contextlib.redirect_stdout(check):
        s.do_command("check_design nuts")
    assert "Success" in check.getvalue()
