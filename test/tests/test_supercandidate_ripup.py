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

"""ripup_reroute honors a super-candidate group pin.

A group pin (`select_topology <b> group:<N>`) restricts the planner to a
nominal-locus family.  ripup must never move a group-pinned bundle OUTSIDE that
family — that would silently break the user's pin.  The planner's `pinned_group`
precedence already enforces this on every trial's replan; `_rr_candidate_order`
additionally restricts the trial set to the family so no out-of-family index is
even tried.
"""
import contextlib
import io
import sys
from pathlib import Path

import buda_cli  # noqa: E402
sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

_SETUP = [
    "def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
    "def_layer 4 M4 H 50", "def_layer 5 M5 V 50",
    "add_block A 8000 10000 9000 11000",
    "add_block B 2000 3000 3000 4000",
    "add_block C 0 20000 500 20500",
    "add_bus dbus[52] A.p B.p,C.p",
    "run_bundler", "generate_topologies",
]


def _session(extra=()):
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in list(_SETUP) + list(extra):
            s.do_command(c)
    return s


def _family(s, w):
    fp = s._make_topo_fp_resolver()(w)
    return next(g for g in s._loci_groups(w, fp) if len(g) > 1)


def test_ripup_candidate_order_restricted_to_family():
    s = _session()
    w = s.bundles[0]
    fam = _family(s, w)
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"select_topology 1 group:{fam[0] + 1}")
    order = s._rr_candidate_order(w, w.plan.selected_topology_index, 'a')
    assert order, "family has >1 member, so there are alternates"
    assert set(order) <= set(fam)                          # never leaves the family
    assert w.plan.selected_topology_index not in order     # old index excluded


def test_group_pin_suppresses_edge_flips():
    # A flip mutates an MST candidate's geometry IN PLACE, escaping the family,
    # so ripup offers no flips for a group-pinned bundle (Codex).  Force an MST
    # candidate as the selection so the guard — not the "non-MST" early-out — is
    # what returns [].
    s = _session()
    w = s.bundles[0]
    fam = _family(s, w)
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"select_topology 1 group:{fam[0] + 1}")
    mst = next((i for i, c in enumerate(w.input.candidates)
                if "MST" in c.type), None)
    if mst is not None:
        w.plan.selected_topology_index = mst
    assert s._rr_flip_edges(w, 'a') == []


def test_ripup_candidate_order_unpinned_uses_full_pool():
    # Sanity: WITHOUT a group pin the alternate pool is the normal (larger,
    # non-family) set — the restriction is scoped to group-pinned bundles.
    s = _session()
    w = s.bundles[0]
    fam = _family(s, w)
    order = s._rr_candidate_order(w, 0, 'a')
    assert len(order) > len(fam)                            # not family-restricted


def test_ripup_preserves_group_pin_and_stays_in_family():
    # End-to-end: a group pin survives run_planner + run_nuts + ripup_reroute,
    # and the selection never leaves the family (ripup is a no-op here — the
    # point is the invariant + that ripup does not crash or clear the pin).
    s = _session()
    w = s.bundles[0]
    fam = _family(s, w)
    with contextlib.redirect_stdout(io.StringIO()):
        for c in (f"select_topology 1 group:{fam[0] + 1}",
                  "run_planner", "run_nuts", "ripup_reroute"):
            s.do_command(c)
    assert set(w.input.pinned_group) == set(fam)            # family preserved
    assert w.plan.selected_topology_index in fam            # stayed in family


def test_negotiate_replan_keeps_group_pinned_bundle_in_family():
    # The OTHER healer: negotiate_congestion re-plans overlap bundles via
    # `replan_bundle_ripup` after clearing topology_pinned but LEAVING
    # pinned_group.  This drives that exact replan directly — the single-bundle
    # fixture has no NUTS overlap, so `negotiate_congestion` itself would return
    # at its `metric already 0` guard without reaching the replan (Codex) — and
    # asserts the C++ pinned_group precedence keeps the re-selection inside the
    # family (negotiation can never move a group-pinned bundle out of it).
    s = _session()
    w = s.bundles[0]
    fam = _family(s, w)
    with contextlib.redirect_stdout(io.StringIO()):
        for c in (f"select_topology 1 group:{fam[0] + 1}",
                  "run_planner", "run_nuts"):
            s.do_command(c)
    bid = w.input.original_bundle.id
    # Mimic _negotiate_iteration's unpin-then-replan on the group-pinned bundle.
    w.input.topology_pinned = False
    w.input.pinned_seg_layers = []
    with contextlib.redirect_stdout(io.StringIO()):
        asns = s.planner.replan_bundle_ripup(s.bundles, bid)
    moved = [a for a in asns if a.bundle_id == bid]
    assert moved, "replan should return an assignment for the target bundle"
    assert moved[0].topo_index in fam                       # re-selected in family
    assert set(w.input.pinned_group) == set(fam)            # pin untouched
