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

"""Structural WL tie-break in annotate_and_sort (wishlist-topo
"Nominal-WL comparability across shape families", piece b).

Candidates are ranked by (wl, nsegs, type): equal-WL candidates order by
ascending SEGMENT count — fewer segments = fewer junctions = tighter
realization risk — with the type string only as the final determinism
anchor.  Previously the tie broke alphabetically ((wl, type), ASCII
'+' < '@'), so on flow/big_data_test/b44.buda a 6-junction TRUNK_H+MST
staircase sorted before its 3-seg tie-siblings, and the planner's
equal-score lowest-index tie-break selected it — realizing at 4510/bit
vs the 3-seg sibling's 3625/bit (pre-pull-clamp numbers).

CAUTION (why these tests pin the exact contract): the sort defines the
1-based candidate indices that `select_topology` pins in checked-in flows
and tests — changing the key again requires a fresh pin audit.
"""
import io
import contextlib

from pytest_bdd import scenarios, given, when, then

import buda
import buda_cli

scenarios('features/structural_tiebreak.feature')


def _b44_session():
    """The b44 repro floorplan: one 52-bit multicast, blk_23 -> {blk_07,
    io_pad_tl} — the fixture whose 3510 WL tie motivated the tie-break."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), buda.ostream_redirect():
        for c in ["source flow/tracks/tracks4top.buda",
                  "add_block blk_07 2960 9750 4660 10250",
                  "add_block blk_23 200 10830 2700 11830",
                  "add_block io_pad_tl 200 12000 1200 12800",
                  "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
                  "run_bundler", "generate_topologies"]:
            s.do_command(c)
    return s


def _key(c):
    # bridge segments (TEG-over) are real wires; annotate_and_sort counts them
    return (c.estimated_wirelength,
            len(c.segments) + len(c.bridge_segments), c.type)


def test_pool_sorted_by_wl_then_nsegs_then_type():
    """The whole pool is ordered by the structural key — equal-WL candidates
    by ascending segment count, type only as the final anchor."""
    s = _b44_session()
    pool = list(s.bundles[0].input.candidates)
    keys = [_key(c) for c in pool]
    assert keys == sorted(keys), keys


def test_equal_wl_tie_orders_fewer_segments_first():
    """The b44 3510 tie group holds both 3-seg plain trunks AND the 6-seg
    TRUNK_H+MST staircase; the staircase must sort LAST in the group even
    though '+' < '@' would put it first alphabetically."""
    s = _b44_session()
    pool = list(s.bundles[0].input.candidates)
    tie = [c for c in pool if c.estimated_wirelength == 3510]
    assert len(tie) >= 2, "fixture drift: the 3510 tie group vanished"
    assert any("MST" in c.type for c in tie), \
        "fixture drift: no MST staircase at the 3510 floor"
    nsegs = [len(c.segments) + len(c.bridge_segments) for c in tie]
    assert nsegs == sorted(nsegs)
    # The first candidate of the pool (= the planner's equal-score default)
    # is a tie member with the MINIMUM segment count, not the staircase.
    assert _key(pool[0])[1] == min(nsegs)
    assert "MST" not in pool[0].type


def test_sort_is_deterministic():
    """Two independent generations produce the identical candidate order
    (by content uid) — the type anchor keeps the sort a total order."""
    a = [buda.topo_uid(c)
         for c in _b44_session().bundles[0].input.candidates]
    b = [buda.topo_uid(c)
         for c in _b44_session().bundles[0].input.candidates]
    assert a == b


# ---------------------------------------------------------------------------
# pytest-bdd bindings for features/structural_tiebreak.feature
# ---------------------------------------------------------------------------

@given("several candidates with equal nominal wirelength", target_fixture="tb")
def _given_tie_pool():
    s = _b44_session()
    pool = list(s.bundles[0].input.candidates)
    tie = [c for c in pool if c.estimated_wirelength == 3510]
    assert len(tie) >= 2, "fixture drift: the 3510 tie group vanished"
    return {"pool": pool, "tie": tie}


@when("candidates are sorted for planning")
def _when_sorted(tb):
    # generation already ends in annotate_and_sort; record the tie order
    tb["tie_keys"] = [_key(c) for c in tb["tie"]]


@then("fewer-segment shapes rank ahead of slide-coupled staircases")
def _then_structural(tb):
    nsegs = [k[1] for k in tb["tie_keys"]]
    assert nsegs == sorted(nsegs)
    # the 6-seg MST staircase sorts last in its tie group, not first
    assert any("MST" in c.type for c in tb["tie"])
    assert "MST" not in tb["tie"][0].type
