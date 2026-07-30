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

"""
Phantom band reservations (issue #516).

`optimize_topologies` parks every hier wrapper's reserved demand as virtual
band usage up front and releases it at that wrapper's own turn, so an
earlier-planned bundle leaves room inside a cell interior.  A wrapper with
NO candidate topologies is skipped by the plan loop entirely — it never
becomes metal — so parking demand for it charges earlier bundles for
congestion no wire can ever consume.

That is not hypothetical: the hier planner runs top-down, so a depth-1
global plans before every cell-local turn and therefore sees the whole
parked set.  Issue #516's repro generated candidates for one D1 bundle and
left 100 D2 wrappers candidate-less; the sole planned bundle reported
`overflow=346` while NUTS/DNUTS placed it cleanly (0 overlaps, 60/60 bits).

The invariant tested here is the strong one: **a candidate-less wrapper is
completely inert** — planning with it present must produce byte-identical
assignments to planning without it.  That catches both failure directions:

  * parking demand that is never consumed (the #516 phantom charge), and
  * releasing a reservation that was never parked, which drives band usage
    NEGATIVE and hands out capacity that does not exist.

The second is why park and release must stay gated on the same predicate;
a refactor that guards only one of them fails the saturated scenario below.
"""
import pytest
import buda


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _narrow_channel():
    """Two walls with a 4-wide gap, M5 keepouts over both walls.

    Mirrors test_global_congestion's spill fixture: block footprints do not
    reduce TOP-layer capacity, so the keepouts are what make the gap the only
    M5 capacity.  M5 is the TOP V layer, which is exactly where
    apply_reservation parks a region's V demand — so a phantom reservation
    lands on the same bands the real V bundles compete for, while M3 (also V,
    but not the top V layer) stays clear as the spill destination.
    """
    fp = buda.Floorplan()
    fp.add_block("wall_left",  0,  0,  8, 100)
    fp.add_block("wall_right", 12, 0, 20, 100)
    fp.add_keepout_zone(0,  0,  8, 100, [5])
    fp.add_keepout_zone(12, 0, 20, 100, [5])

    ls = buda.LayerStack()
    ls.add_layer(3, "M3", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL,   buda.LayerType.TOP)
    return fp, ls


def _v_bundle(bid, width, x, priority):
    """A real bundle: one full-height V segment through the channel."""
    seg = buda.Segment()
    seg.start = buda.Point(x, 0)
    seg.end   = buda.Point(x, 100)
    seg.layer_hint = 5

    topo = buda.Topology()
    topo.type = "TEST_V"
    topo.segments = [seg]

    b = buda.HBundle()
    b.id = bid

    w = buda.BundleWrapper()
    w.input.original_bundle = b
    w.input.width = width
    w.input.candidates = [topo]
    w.plan.selected_topology_index = 0
    # Higher priority plans FIRST — i.e. while the phantom below is still
    # parked.  That ordering is the whole point: a reservation released at
    # its own turn cannot affect anyone who already planned.
    w.hier.priority = priority
    return w


def _phantom(bid, width=40.0):
    """A candidate-less wrapper holding a reservation over the whole channel.

    This is the shape `_expand_hier_bundles` produces for a cell-local
    instance whose bundle generated no candidates: hier.has_reservation is
    set unconditionally there, independent of the candidate pool.
    """
    b = buda.HBundle()
    b.id = bid

    w = buda.BundleWrapper()
    w.input.original_bundle = b
    w.input.width = width
    w.input.candidates = []          # ← never routes, never becomes metal
    w.hier.has_reservation = True
    w.hier.res_x1, w.hier.res_y1 = 0, 0
    w.hier.res_x2, w.hier.res_y2 = 20, 100
    w.hier.priority = -1.0           # planned last, so it stays parked throughout
    return w


def _plan(fp, ls, wrappers):
    """Run the planner and return {bundle_id: (topo, v_layer, seg_layers)}."""
    planner = buda.CongestionPlanner(fp, ls)
    planner.build_congestion_map()
    with buda.ostream_redirect():
        assignments = planner.optimize_topologies(wrappers, 1)
    return {a.bundle_id: (a.topo_index, a.v_layer_id, list(a.seg_layers))
            for a in assignments}


# ── The inertness invariant ───────────────────────────────────────────────────

def test_candidateless_wrapper_is_inert_when_uncongested():
    """One real bundle with room to spare keeps its layer when a phantom
    reservation blankets the channel.

    Without the fix the parked demand consumes the gap's M5 capacity, the
    real bundle overflows and spills off the top V layer — congestion that
    no wire can ever produce.
    """
    fp, ls = _narrow_channel()
    real = _v_bundle(bid=1, width=3.0, x=10, priority=1.0)
    alone = _plan(fp, ls, [real])

    fp, ls = _narrow_channel()
    real = _v_bundle(bid=1, width=3.0, x=10, priority=1.0)
    with_phantom = _plan(fp, ls, [real, _phantom(bid=99)])

    assert 99 not in with_phantom, \
        "a candidate-less wrapper must not receive an assignment"
    assert with_phantom[1] == alone[1], (
        "a candidate-less wrapper changed a real bundle's plan: "
        f"{alone[1]} alone vs {with_phantom[1]} with the phantom present "
        "(issue #516 — reserved demand parked for a bundle that never routes)"
    )


def test_candidateless_wrapper_is_inert_when_saturated():
    """The negative-usage direction: with the channel genuinely saturated,
    a phantom must not hand out capacity either.

    Two real bundles contend for the same gap, so the planner's decision sits
    right at the capacity boundary and moves if the books are off in EITHER
    direction.  Releasing a reservation that was never parked would credit
    the bands negative and relieve real contention that physically exists.
    """
    fp, ls = _narrow_channel()
    reals = [_v_bundle(bid=1, width=3.0, x=10, priority=2.0),
             _v_bundle(bid=2, width=3.0, x=10, priority=1.0)]
    alone = _plan(fp, ls, reals)

    fp, ls = _narrow_channel()
    reals = [_v_bundle(bid=1, width=3.0, x=10, priority=2.0),
             _v_bundle(bid=2, width=3.0, x=10, priority=1.0)]
    with_phantom = _plan(fp, ls, reals + [_phantom(bid=99)])

    assert with_phantom == alone, (
        "a candidate-less wrapper changed the saturated outcome: "
        f"{alone} vs {with_phantom} — park and release must stay gated on "
        "the same predicate, or band usage goes negative"
    )


@pytest.mark.parametrize("n_phantoms", [1, 5, 20])
def test_phantom_demand_does_not_accumulate(n_phantoms):
    """#516's real shape: ONE planned bundle against MANY candidate-less
    wrappers (100 in the repro).  The phantom charge accumulates per wrapper,
    so the distortion grows with the count — the plan must not.
    """
    fp, ls = _narrow_channel()
    real = _v_bundle(bid=1, width=3.0, x=10, priority=1.0)
    alone = _plan(fp, ls, [real])

    fp, ls = _narrow_channel()
    real = _v_bundle(bid=1, width=3.0, x=10, priority=1.0)
    many = _plan(fp, ls, [real] + [_phantom(bid=100 + k)
                                   for k in range(n_phantoms)])

    assert many[1] == alone[1], (
        f"{n_phantoms} candidate-less wrapper(s) moved a real bundle's plan: "
        f"{alone[1]} -> {many[1]}"
    )
