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

"""Super-candidate grouping in the TopologyExplorer.

The default-on Hanan loci produce many near-identical candidates differing only
in trunk perp.  With a `groups_fn` (the session's `_loci_groups`), the explorer
can step family-to-family ('G') and group-pin the current family ('S', the GUI
twin of `select_topology group:`), shrinking what the user pages through without
touching the engine candidate list.
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")   # headless; no window
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
import buda_cli  # noqa: E402
import buda_viz  # noqa: E402

# A 3-block fan-out bus with several Hanan-line trunk loci → real families
# (same fixture as test_supercandidate.py).
_SETUP = [
    "def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
    "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
    "add_block A 8000 10000 9000 11000",
    "add_block B 2000 3000 3000 4000",
    "add_block C 0 20000 500 20500",
    "add_bus dbus[52] A.p B.p,C.p",
    "run_bundler", "generate_topologies",
]


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in _SETUP:
        s.do_command(c)
    return s


def _explorer(s, with_groups=True):
    return buda_viz.TopologyExplorer(
        s.fp, s.bundles, layer_stack=s.layers,
        fp_resolver=s._make_topo_fp_resolver(),
        groups_fn=(s._loci_groups if with_groups else None))


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    import matplotlib.pyplot as plt
    plt.close('all')


def test_explorer_exposes_families():
    s = _session()
    exp = _explorer(s)
    exp.bidx, exp.idx = 0, 0
    groups = exp._explorer_groups()
    assert groups is not None
    assert 1 < len(groups) < len(exp.topos)          # collapsed, nothing dropped
    assert any(len(g) > 1 for g in groups)
    flat = sorted(i for g in groups for i in g)
    assert flat == list(range(len(exp.topos)))       # every candidate covered


def test_family_step_jumps_family_to_family():
    s = _session()
    exp = _explorer(s)
    exp.bidx, exp.idx = 0, 0
    groups = exp._explorer_groups()
    reps = [g[0] for g in groups]
    exp._toggle_group_step()                          # snaps idx to a family rep
    assert exp._group_step is True
    assert exp.idx in reps
    start = reps.index(exp.idx)
    exp._step_topo(+1)
    assert exp.idx == reps[(start + 1) % len(reps)]   # next family's rep
    exp._step_topo(-1)
    assert exp.idx == reps[start]                     # back


def test_group_pin_current_sets_pinned_group():
    s = _session()
    exp = _explorer(s)
    exp.bidx = 0
    fam = next(g for g in exp._explorer_groups() if len(g) > 1)
    exp.idx = fam[1]                                  # a NON-representative member
    exp._group_pin_current()
    w = s.bundles[0]
    assert list(w.input.pinned_group) == fam
    assert w.input.topology_pinned is False
    assert w.plan.selected_topology_index == fam[0]   # snaps to the rep


def test_x_clears_group_pin():
    s = _session()
    exp = _explorer(s)
    exp.bidx = 0
    fam = next(g for g in exp._explorer_groups() if len(g) > 1)
    exp.idx = fam[0]
    exp._group_pin_current()
    assert list(s.bundles[0].input.pinned_group) != []
    exp._deselect_current()                           # the 'x' key
    assert list(s.bundles[0].input.pinned_group) == []


def test_group_pin_singleton_falls_back_to_single_pin():
    # 'S' on a candidate that is its own family is never a silent no-op — it
    # single-pins instead.
    s = _session()
    exp = _explorer(s)
    exp.bidx = 0
    singleton = next(g[0] for g in exp._explorer_groups() if len(g) == 1)
    exp.idx = singleton
    exp._group_pin_current()
    w = s.bundles[0]
    assert list(w.input.pinned_group) == []           # no group pin
    assert w.input.topology_pinned is True            # single pin applied


def test_grouping_off_without_groups_fn():
    s = _session()
    exp = _explorer(s, with_groups=False)
    exp.bidx, exp.idx = 0, 0
    assert exp._explorer_groups() is None
    assert exp._group_of(3) == [3]                    # every candidate its own
    exp._toggle_group_step()
    assert exp._group_step is False                   # no-op without families
