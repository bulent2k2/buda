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

    # 7 top buses (70 nets) + cell-internal nets replicated ×2:
    # dnuts1 128 + dnuts2 16 + channel_stress 200 = 344 per set, ×2 = 688.
    assert len(db.all_nets()) == 70 + 688


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
    names = {n.name for n in buda_db.BDB(out).all_nets()}
    assert len(names) == 70, "only the 7 top buses (70 nets)"
    assert not any(n.startswith("chip/i_dnuts1_0/") for n in names)


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
