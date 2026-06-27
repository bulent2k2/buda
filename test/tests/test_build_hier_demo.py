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

    # 7 buses of widths 4,6,…,16 → 70 nets.
    assert len(db.all_nets()) == 70


def test_build_hier_demo_buses_are_hierarchical(tmp_path):
    out = str(tmp_path / "hier2.bdb")
    build_hier_demo.build(out, _CELLS, seed=1)

    db = buda_db.BDB(out)
    cid2name = {c.id: c.name for c in db.all_components()}
    nets = {n.name: n.id for n in db.all_nets()}
    # Every bus bit-net exists.
    assert "top_bus6_w16_15" in nets

    nid = nets["top_bus0_w4_0"]
    pins = [p for p in db.all_pins() if p.net_id == nid]
    # One driver (OUTPUT) leaf, ≥1 receiver (INPUT) leaf.
    assert any(p.dir == "OUTPUT" for p in pins)
    assert any(p.dir == "INPUT" for p in pins)
    # Interface pins are propagated onto depth-1 instance ancestors
    # (pin_name == net_name on a "chip/i_*" component, no further '/').
    iface = [p for p in pins
             if p.pin_name == "top_bus0_w4_0"
             and cid2name[p.comp_id].count("/") == 1]
    assert iface, "expected interface pins on instance ancestors"


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
