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

"""The `hanan_loci` generation knob (wishlist-topo "Nominal-WL comparability",
piece (a)): ALSO sample n-pin trunk loci ON the in-bbox Hanan lines, not just
at channel midpoints, so a block-edge-aligned trunk can nominal at the
geometric WL floor.

Fixture = the b44 block layout (flow/big_data_test/b44.buda): the WL-optimal
V-trunk locus is x=1200 — io_pad_tl's right edge, a Hanan LINE — which the
midpoint-only sampling (700/1950/2830/3810) structurally misses, so the best
default 2-seg TRUNK_V carries a +500 nominal overshoot (4010 vs the 3510
floor = 1760 H + 1750 V between io_pad_tl's and blk_07's nearest corners).
"""

import io
import os
from contextlib import redirect_stdout

import pytest
from pytest_bdd import scenarios, given, when, then

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

B44_SETUP = [
    f"source {os.path.join(REPO, 'flow', 'tracks', 'tracks4top.buda')}",
    "add_block blk_07 2960 9750 4660 10250",
    "add_block blk_23 200 10830 2700 11830",
    "add_block io_pad_tl 200 12000 1200 12800",
    "add_bus bus_060[52] blk_23.p blk_07.p,io_pad_tl.p",
    "run_bundler",
]

WL_FLOOR = 3510  # |1200-2960| + |12000-10250| between the two far blocks


def _gen(gen_cmd):
    from buda_cli import BudaSession
    s = BudaSession()
    for line in B44_SETUP + [gen_cmd]:
        with redirect_stdout(io.StringIO()):
            s.do_command(line)
    return s.bundles[0].input.candidates


def test_hanan_loci_emits_edge_aligned_trunk_at_wl_floor():
    cands = _gen("generate_topologies hanan_loci")
    at_1200 = [t for t in cands
               if "TRUNK_V" in t.type and t.trunk_location == 1200]
    assert at_1200, (
        "no TRUNK_V candidate at the edge-aligned locus x=1200; loci: "
        + str(sorted({t.trunk_location for t in cands if "TRUNK_V" in t.type})))
    assert any(t.estimated_wirelength == WL_FLOOR for t in at_1200), (
        [(t.type, t.estimated_wirelength) for t in at_1200])


def test_hanan_loci_is_opt_in_default_pool_unchanged():
    """Default generation must not change: the knob renumbers the WL-sorted
    candidate pool that checked-in flows pin by index, so it is opt-in."""
    base = _gen("generate_topologies")
    assert not any("TRUNK_V" in t.type and t.trunk_location == 1200
                   for t in base)
    best_plain_v = min(t.estimated_wirelength for t in base
                       if t.type.startswith("TRUNK_V@"))
    with_knob = _gen("generate_topologies hanan_loci")
    best_knob_v = min(t.estimated_wirelength for t in with_knob
                      if t.type.startswith("TRUNK_V@"))
    assert best_knob_v == WL_FLOOR
    assert best_knob_v <= best_plain_v
    # Superset property: every default candidate locus is still sampled.
    assert len(with_knob) >= len(base)


# ---------------------------------------------------------------------------
# pytest-bdd binding for features/hanan_trunk_loci.feature (the @landed home
# of the former future_directions "trunk loci ON Hanan lines" scenario).
# ---------------------------------------------------------------------------

scenarios('features/hanan_trunk_loci.feature')


@pytest.fixture
def ctx():
    return {}


@given("a multicast bundle whose WL-optimal trunk position lies ON a Hanan line")
def _given_b44_layout(ctx):
    ctx['layout'] = B44_SETUP


@when("generate_topologies samples trunk loci with the hanan_loci knob")
def _when_gen_with_knob(ctx):
    ctx['candidates'] = _gen("generate_topologies hanan_loci")


@when("generate_topologies samples trunk loci without the hanan_loci knob")
def _when_gen_without_knob(ctx):
    ctx['candidates'] = _gen("generate_topologies")


@then("a candidate at the Hanan-line locus exists with its nominal at the geometric floor")
def _then_floor_candidate(ctx):
    assert any("TRUNK_V" in t.type and t.trunk_location == 1200
               and t.estimated_wirelength == WL_FLOOR
               for t in ctx['candidates'])


@then("no candidate is emitted at the Hanan-line locus")
def _then_no_edge_candidate(ctx):
    assert not any("TRUNK_V" in t.type and t.trunk_location == 1200
                   for t in ctx['candidates'])
