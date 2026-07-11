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

"""ripup_reroute QoR-measured class re-rank (wishlist-planner "Selection
basis" lever 2).

Candidates are WL-sorted (annotate_and_sort), and `_rr_candidate_order`
historically drew its trial pool from `range(min(n, 8))` — the 8 cheapest
estimates only.  On fan-out trunk bundles the pool runs to 35-45 candidates
with the higher-estimate classes (OOB trunks, two-level BITRUNK trees) at
indices 8-40, so no measured contention could ever promote one: the trial
that would test it was structurally unreachable.

The re-rank keeps the legacy first-8 pool FIRST (farness-ranked, exactly as
before — whenever a cheap alternate improves, the first-improving scan
commits the same move and routes are unchanged; the golden corpora guard
that) and APPENDS the top-8 farness-ranked candidates from beyond the
first-8 window.  The expensive classes are reachable exactly when every
cheap alternate fails, and acceptance stays QoR-gated by the measured
(opens, overlaps) metric.

These tests pin the pool-composition contract directly on a real 39-candidate
fan-out bundle, with `_rr_contention_centres` stubbed so the contention input
is deterministic.
"""
import io
import contextlib

import buda_cli
from buda_session.util import _RR_MAX_CANDIDATES_PER_BUNDLE as _CAP


def _fanout_session():
    """Driver + a column of 6 receivers: the trunk generator emits ~39
    candidates (straight trunk, MSTs, TRUNK+MST hybrids, per-row trunks, OOB
    detours), comfortably beyond the first-8 window."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = ["def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
            "add_block SRC 0 800 200 1000"]
    rcv = []
    for k in range(6):
        y = 200 + k * 300
        cmds.append(f"add_block R{k} 2600 {y} 2800 {y+150}")
        rcv.append(f"R{k}.p")
    cmds += [f"add_bus d[8] SRC.p {','.join(rcv)}",
             "run_bundler", "generate_topologies"]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    w = s.bundles[0]
    assert len(w.input.candidates) > 2 * _CAP, \
        "fixture must have candidates beyond the appended-extras window"
    return s, w


def _farness(cand, sites):
    """Reference reimplementation of the ordering key (kept in lockstep with
    _rr_candidate_order): min over sites of the candidate's closest
    same-orientation segment distance."""
    worst = None
    for horiz, centre in sites:
        best = None
        for seg in cand.segments:
            seg_h = (seg.start.y == seg.end.y)
            if seg_h != horiz:
                continue
            perp = seg.start.y if seg_h else seg.start.x
            d = abs(perp - centre)
            if best is None or d < best:
                best = d
        if best is not None and (worst is None or best < worst):
            worst = best
    return worst if worst is not None else 0.0


def test_no_contention_keeps_legacy_first_n_pool():
    """With no measured contention sites there is nothing to rank by and no
    evidence justifying extra trials: the pool stays the legacy first-N."""
    s, w = _fanout_session()
    s._rr_contention_centres = lambda stage, bid: []
    order = s._rr_candidate_order(w, 0, 'a')
    assert order == list(range(1, _CAP))


def test_contention_appends_beyond_cap_extras():
    """Under contention the legacy pool still comes FIRST (same members,
    farness-ranked) and up to N extras from beyond the cap are appended —
    the previously unreachable candidates."""
    s, w = _fanout_session()
    sites = [(True, 900.0)]     # horizontal contention near the trunk row
    s._rr_contention_centres = lambda stage, bid: sites
    old = 0
    order = s._rr_candidate_order(w, old, 'a')
    legacy = order[:_CAP - 1]
    extras = order[_CAP - 1:]
    # legacy pool: exactly the first-8 window minus the current selection
    assert sorted(legacy) == [i for i in range(_CAP) if i != old]
    # extras: only beyond-cap indices, capped, and previously unreachable
    assert extras, "expected appended extras from beyond the first-8 window"
    assert len(extras) <= _CAP
    assert all(i >= _CAP for i in extras)
    # both halves are farness-ranked (descending, ties by index — stable sort)
    cands = w.input.candidates
    for part in (legacy, extras):
        keys = [_farness(cands[i], sites) for i in part]
        assert keys == sorted(keys, reverse=True), (part, keys)
    # the farthest beyond-cap candidate leads the extras
    best = max((i for i in range(_CAP, len(cands)) if i != old),
               key=lambda i: (_farness(cands[i], sites), -i))
    assert extras[0] == best


def test_current_selection_excluded_from_extras():
    """A contender currently pinned beyond the cap must not re-trial itself."""
    s, w = _fanout_session()
    sites = [(True, 900.0)]
    s._rr_contention_centres = lambda stage, bid: sites
    old = _CAP + 3               # current selection sits in the extras window
    order = s._rr_candidate_order(w, old, 'a')
    assert old not in order
    assert sorted(order[:_CAP]) == list(range(_CAP))   # full legacy window
