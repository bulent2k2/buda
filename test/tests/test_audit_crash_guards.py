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

"""Crash-guard regressions from the 2026-07 whole-codebase audit.

Each test drives a formerly-unguarded entry point with the degenerate input
that used to be undefined behavior (empty-vector indexing, size_t underflow)
and asserts the fail-LOUD behavior that replaced it.
"""
import pytest

import buda


def test_add_block_rects_rejects_empty_list():
    # Audit C4-04: Rect u = norm_rects[0] on an empty input was UB.
    fp = buda.Floorplan()
    with pytest.raises(Exception, match="must not be empty"):
        fp.add_block_rects("blk", [])


def test_add_net_pins_empty_path_endpoint_does_not_underflow():
    # Audit C6-02: an endpoint like ".p" (empty instance path) made the
    # ancestor loop start at size_t(-1) and index an empty vector. It must
    # now complete without crashing (the leaf pin insert itself is handled
    # by _add_pin_by_path; there are simply no ancestors to add).
    db = buda.BDB(":memory:")
    nid = db.add_net_pins("n1", ".p", ["a/b.q"])
    assert isinstance(nid, int)
    # Same guard on the undirected and inout variants (identical loops).
    nid2 = db.add_net_pins_undirected("n2", [".x", "c.y"])
    assert isinstance(nid2, int)


def test_reference_holding_ctors_keep_parent_alive():
    # Audit C7-01/02/03: BustermGen(BDB&), HierarchicalBundler(BDB&) and
    # TopologyGenerator(const Floorplan&) store C++ references but had no
    # py::keep_alive — dropping the Python parent left the child dangling
    # (a temporary parent dangled IMMEDIATELY). With keep_alive the
    # temporary-parent pattern is safe by construction.
    import gc
    import buda_db

    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 300, 0, 400, 100)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    del fp
    gc.collect()
    cands = g.generate_candidates("A", ["B"])   # dangled pre-fix
    assert cands, "generator must still see the kept-alive floorplan"

    db = buda.BDB(":memory:")
    db.add_cell("c", 10, 10)
    gen = buda_db.BustermGen(db)
    del db
    gc.collect()
    gen.derive(0)                               # dangled pre-fix

    db2 = buda.BDB(":memory:")
    hb = buda.HierarchicalBundler(db2)
    del db2
    gc.collect()
    hb.run(1)                                   # dangled pre-fix
