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

"""The unified 2-pin / n-pin post-emission filter pipeline
(`TopologyGenerator::finalize_candidates`: annotate → sort → keepout cull →
pinch → coverage fill; wishlist-topo "Unify the 2-pin vs n-pin filter
ordering").

Before the unification only `generate_2pin` ran the post-emission keepout
cull; `generate_npin` pre-gated trunk LOCI only, so a candidate whose STUB
(or MST/BITRUNK edge) was fully keepout-blocked on all same-direction layers
survived to the planner.  Such a candidate routes "clean" through abstract
NUTS (0 overlaps / 0 violations — the keepout carves band capacity, but a
face-clamped stub charges no band) and then strands every bit of the blocked
tap at DetailedNUTS: measured on the 3-block scenario below, main pinned to
`TRUNK_H_OOB@y250` = 0/0 at NUTS -> 4/4 bits unplaced.  It is also reachable
without a pin: stage-a `ripup_reroute` optimizes NUTS overlaps only, and a
dead stub's band is empty.
"""
import io
import contextlib

import buda
import buda_cli

# Keepout that fully covers B's upward stub corridor on ALL V layers.
_KO = (150, 100, 350, 400)


def _scenario_session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    patn = ("POWER 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 "
            "GROUND 2 1 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5 SIGNAL 1 0.5")
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ("def_layer 4 M4 H TOP 50", "def_layer 5 M5 V TOP 50",
                  f"def_track_pattern 4 0 {patn}", f"def_track_pattern 5 0 {patn}",
                  "add_block A 0 0 100 100", "add_block B 200 0 300 100",
                  "add_block C 400 0 500 100",
                  f"add_keepout {' '.join(map(str, _KO))} M5",
                  "add_net n0 A.p B.p,C.p", "add_net n1 A.p B.p,C.p",
                  "add_net n2 A.p B.p,C.p", "add_net n3 A.p B.p,C.p",
                  "run_bundler", "generate_topologies"):
            s.do_command(c)
    return s


def _dead_v_segs(cands):
    """(cand_idx, type, seg_idx) for every V segment fully inside the keepout
    (= unroutable on every V layer)."""
    x1, y1, x2, y2 = _KO
    out = []
    for i, c in enumerate(cands):
        for si, g in enumerate(c.segments):
            if g.start.x != g.end.x:
                continue                     # not a V segment
            lo, hi = sorted((g.start.y, g.end.y))
            if x1 <= g.start.x <= x2 and lo >= y1 and hi <= y2:
                out.append((i, c.type, si))
    return out


def test_npin_keepout_cull_drops_dead_stub_candidates():
    """An n-pin candidate whose stub is fully keepout-blocked on all V layers
    must not survive generation (pre-unification `TRUNK_H_OOB@y250` did, with
    its B stub dead inside the zone)."""
    s = _scenario_session()
    cands = list(s.bundles[0].input.candidates)
    assert cands, "scenario produced no candidates at all"
    dead = _dead_v_segs(cands)
    assert not dead, f"keepout-dead segments survived the unified cull: {dead}"
    # The clean through-the-row trunk and the below-detours remain routable.
    types = {c.type for c in cands}
    assert any(t.startswith("TRUNK_H@") for t in types), sorted(types)


def test_npin_scenario_routes_fully_clean():
    """End-to-end with the planner's own choice: every bit places (the dead
    candidates that could strand B's taps are no longer in the pool)."""
    s = _scenario_session()
    with contextlib.redirect_stdout(io.StringIO()):
        for c in ("run_planner 3", "run_nuts", "run_detailed_nuts"):
            s.do_command(c)
    assert s.nuts_result.num_overlaps == 0
    assert s.detailed_result.num_unplaced == 0


def test_fully_blocked_npin_bundle_surfaces_as_unrouted():
    """When EVERY candidate has a keepout-dead segment the cull empties the
    list and the bundle surfaces as UNROUTED (the zero-candidate warning) —
    the 2-pin cull's long-standing semantics, now shared.  That is the honest
    outcome: pre-unification the bundle routed phantom wires that abstract
    NUTS reported clean and DetailedNUTS then stranded."""
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 100, 100)
    fp.add_block("B", 200, 0, 300, 100)
    fp.add_block("C", 400, 0, 500, 100)
    # Blanket keepouts on BOTH directions' only layers around the whole row:
    # every trunk locus and every stub is blocked.
    fp.add_keepout_zone(-100, -100, 600, 500, [4, 5])
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    cands = g.generate_candidates("A", ["B", "C"])
    assert cands == [], [c.type for c in cands]
