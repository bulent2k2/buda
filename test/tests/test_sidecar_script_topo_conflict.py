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

"""Sidecar vs script topology conflict: the script wins, and the sidecar's layer
overrides apply only if they belong to the script-chosen topology.

Regression: with generate_topologies restoring the sidecar baseline, a later
select_topology to a *different* topology left the sidecar's seg_layers stale on
the wrapper, and the planner truncated them onto the new (shorter) topology.
_apply_selections now clears overrides that don't match the chosen topology.
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402


def _session(tmp_path, sidecar, *extra):
    p = tmp_path / "t.buda"
    p.write_text("")
    (tmp_path / "t.json").write_text(json.dumps(sidecar))
    s = buda_cli.BudaSession(); s.no_viz = True
    s.script_path = str(p)            # _sidecar_path() -> t.json
    for c in (
        "def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10",
        "def_layer 6 M6 H TOP 10", "def_layer 7 M7 V TOP 10",
        "add_block b1 0 0 100 100", "add_block b2 200 200 300 300",
        "add_bus v[4] b1.o b2.i", "add_bus w[4] b2.i b1.o",
        "run_bundler strict", "generate_topologies",   # calls _apply_selections (baseline)
        *extra,
    ):
        s.do_command(c)
    return s


def _sidecar(idx, layers):
    # type/wl deliberately won't match -> falls back to topo_index_hint=idx.
    return {"selections": [{
        "bundle_hint": "v_0", "bundle_id": 1,
        "topo_type": "_unused_", "topo_wl": -1, "topo_index_hint": idx,
        "note": "", "selected_at": "x", "seg_layers": layers,
    }]}


def test_script_topo_overrides_sidecar_and_clears_mismatched_layers(tmp_path):
    s = _session(tmp_path, _sidecar(3, [5, 6, 7]),
                 "select_topology 1 2",   # pick a DIFFERENT topo than the sidecar (idx 3)
                 "run_planner")
    w = s.bundles[0]
    cands = w.input.candidates
    if not (len(cands) > 3 and len(cands[3].segments) == 3 and len(cands[1].segments) == 2):
        import pytest; pytest.skip("topology generation differs from expected fixture")
    assert w.plan.selected_topology_index == 1            # script wins (topo 2)
    assert list(w.input.pinned_seg_layers) == []          # sidecar's topo-4 layers NOT leaked
    # planner auto-assigns topo 2's layers (not a truncation of [5,6,7])
    assert len(w.plan.seg_layers) == 2


def test_script_topo_matching_sidecar_keeps_layers(tmp_path):
    s = _session(tmp_path, _sidecar(3, [5, 6, 7]),
                 "select_topology 1 4",   # SAME topo the sidecar pinned (idx 3)
                 "run_planner")
    w = s.bundles[0]
    cands = w.input.candidates
    if not (len(cands) > 3 and len(cands[3].segments) == 3):
        import pytest; pytest.skip("topology generation differs from expected fixture")
    assert w.plan.selected_topology_index == 3            # same topo, script + sidecar agree
    assert list(w.input.pinned_seg_layers) == [5, 6, 7]   # sidecar layer overrides applied
