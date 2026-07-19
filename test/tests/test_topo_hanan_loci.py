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

"""Hanan-line trunk loci (wishlist-topo "Nominal-WL comparability",
piece (a)): sample n-pin trunk loci ON the in-bbox Hanan lines as well as at
channel midpoints, so a block-edge-aligned trunk can nominal at the geometric
WL floor.

DEFAULT-ON since the default flip (allow_hanan_loci_ = true, src/topology.h):
the default pool CONTAINS the Hanan-line loci, `no_hanan_loci` is the
per-command opt-out that restores the midpoint-only pool, and the legacy
`hanan_loci` flag remains accepted as a keep-on no-op.

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


def test_default_pool_emits_edge_aligned_trunk_at_wl_floor():
    """The flipped DEFAULT: plain generation samples the Hanan-line loci, so
    the b44 fixture emits TRUNK_V@x1200 at the 3510 geometric floor with no
    knob at all."""
    cands = _gen("generate_topologies")
    at_1200 = [t for t in cands
               if "TRUNK_V" in t.type and t.trunk_location == 1200]
    assert at_1200, (
        "no TRUNK_V candidate at the edge-aligned locus x=1200; loci: "
        + str(sorted({t.trunk_location for t in cands if "TRUNK_V" in t.type})))
    assert any(t.estimated_wirelength == WL_FLOOR for t in at_1200), (
        [(t.type, t.estimated_wirelength) for t in at_1200])


def test_no_hanan_loci_restores_midpoint_only_pool():
    """`no_hanan_loci` is the per-command opt-out: the pool reverts to
    midpoint-only trunk loci (the pre-flip default), a strict subset of the
    default pool."""
    base = _gen("generate_topologies no_hanan_loci")
    assert not any("TRUNK_V" in t.type and t.trunk_location == 1200
                   for t in base)
    best_plain_v = min(t.estimated_wirelength for t in base
                       if t.type.startswith("TRUNK_V@"))
    default = _gen("generate_topologies")
    best_default_v = min(t.estimated_wirelength for t in default
                         if t.type.startswith("TRUNK_V@"))
    assert best_default_v == WL_FLOOR
    assert best_default_v <= best_plain_v
    # Superset property: every midpoint-only locus is still sampled by the
    # default (loci only ADD candidates).
    assert len(default) >= len(base)


def test_no_hanan_loci_memo_round_trips_bulk_regeneration(tmp_path):
    """The v15 knob-memo opt-out contract (see _record_gen_knob_memo,
    src/buda_cmds/topologies_cmds.py): a pool generated per-bundle with
    `no_hanan_loci` stays midpoint-only after a bulk generate_topologies
    replays memos, and an explicit `hanan_loci` flips the memo polarity
    back."""
    from buda_cli import BudaSession
    s = BudaSession()
    bdb = str(tmp_path / "loci_memo.bdb")
    with redirect_stdout(io.StringIO()):
        for line in [f"open_bdb {bdb}"] + B44_SETUP + ["generate_topologies"]:
            s.do_command(line)

    def has_1200():
        return any("TRUNK_V" in t.type and t.trunk_location == 1200
                   for t in s.bundles[0].input.candidates)

    def memo():
        bid = str(s.bundles[0].input.original_bundle.id)
        return s.bdb.bundle_gen_knobs(bid)

    assert has_1200()                       # default-on pool
    with redirect_stdout(io.StringIO()):
        s.do_command("generate_topologies_for_bundle bus_060 no_hanan_loci")
    assert not has_1200() and memo() == "no_hanan_loci"
    with redirect_stdout(io.StringIO()):
        s.do_command("generate_topologies")  # bulk regen — memo must hold
    assert not has_1200(), "no_hanan_loci memo did not survive bulk regen"
    with redirect_stdout(io.StringIO()):
        s.do_command("generate_topologies_for_bundle bus_060 hanan_loci")
    assert has_1200() and memo() == "hanan_loci"   # polarity flipped back
    with redirect_stdout(io.StringIO()):
        s.do_command("generate_topologies")
    assert has_1200()


def test_pure_opt_out_memo_skips_additive_replay(tmp_path):
    """A memo whose ONLY token is `no_hanan_loci` has nothing to replay
    additively: after the midpoint-only base regen, running the additive
    generator anyway (all non-memo knobs off) would append the PLAIN
    midpoint pool into a knobbed bulk regen — e.g. `generate_topologies
    center_mode` would no longer produce a center-mode-consistent pool for
    that bundle.  The replay must skip the additive pass, so the bulk pool
    equals a fresh (center_mode + no_hanan_loci) generation exactly."""
    import buda
    from buda_cli import BudaSession
    s = BudaSession()
    bdb = str(tmp_path / "loci_memo_cm.bdb")
    with redirect_stdout(io.StringIO()):
        for line in [f"open_bdb {bdb}"] + B44_SETUP + ["generate_topologies"]:
            s.do_command(line)
        s.do_command("generate_topologies_for_bundle bus_060 no_hanan_loci")
        s.do_command("generate_topologies center_mode")   # memo replays here
    got = {buda.topo_uid(c) for c in s.bundles[0].input.candidates}

    ref = BudaSession()
    with redirect_stdout(io.StringIO()):
        for line in B44_SETUP:
            ref.do_command(line)
        ref.do_command("generate_topologies center_mode no_hanan_loci")
    want = {buda.topo_uid(c) for c in ref.bundles[0].input.candidates}
    assert got == want, (
        f"pure no_hanan_loci memo polluted the center_mode bulk pool: "
        f"{len(got)} vs {len(want)} candidates "
        f"(+{len(got - want)} extra, -{len(want - got)} missing)")


def test_legacy_hanan_loci_flag_is_a_keep_on_noop():
    """Backward compatibility: pre-flip scripts (and v15 knob memos) passing
    `hanan_loci` must keep working — the flag is accepted and the pool is
    identical to the plain default-on pool."""
    import buda
    legacy = _gen("generate_topologies hanan_loci")
    default = _gen("generate_topologies")
    assert ([buda.topo_uid(t) for t in legacy]
            == [buda.topo_uid(t) for t in default])


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


@when("generate_topologies samples trunk loci with the default settings")
def _when_gen_default(ctx):
    ctx['candidates'] = _gen("generate_topologies")


@when("generate_topologies samples trunk loci with the no_hanan_loci opt-out")
def _when_gen_opt_out(ctx):
    ctx['candidates'] = _gen("generate_topologies no_hanan_loci")


@then("a candidate at the Hanan-line locus exists with its nominal at the geometric floor")
def _then_floor_candidate(ctx):
    assert any("TRUNK_V" in t.type and t.trunk_location == 1200
               and t.estimated_wirelength == WL_FLOOR
               for t in ctx['candidates'])


@then("no candidate is emitted at the Hanan-line locus")
def _then_no_edge_candidate(ctx):
    assert not any("TRUNK_V" in t.type and t.trunk_location == 1200
                   for t in ctx['candidates'])
