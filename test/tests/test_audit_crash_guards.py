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
