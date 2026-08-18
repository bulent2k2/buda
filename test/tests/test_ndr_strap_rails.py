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

"""A DEF power strap is a RAIL, and one predicate answers for both kinds.

`specialnets_scope.md` §5(b).  On an imported design every routing layer is
ALL-SIGNAL: a LEF states how wide a wire is and says nothing about which
tracks a power grid takes, so the synthesized pattern carries no rails and
NDR's credit / bond machinery had nothing to bite on.  The rails are in the
DEF's `SPECIALNETS` and nowhere else.

Two things had to become true.  The identity had to REACH the predicates —
it was on the Floorplan's KeepoutZone already, but `DetailedNUTSEngine` holds
only a RoutingGridStack, so the grid was the only possible route.  And the
lookup had to be ONE function (opens_ndr R4): crediting at DNUTS and the R9
audit of the placed result must not be free to disagree, and they were —
only the identity half was shared, the geometry half existed twice.
"""
import buda


def _grid(is_horizontal=False, pitch=0.28, width=0.14):
    """One ALL-SIGNAL layer, which is what an imported design has."""
    st = buda.RoutingGridStack()
    slot = buda.TrackSlot(type="SIGNAL", label="", width=width,
                          space_after=pitch - width)
    st.define_layer(6, buda.TrackPattern(origin=0.0, slots=[slot]),
                    is_horizontal)
    return st


def _spec(net="VDD", credit=True):
    s = buda.NdrSpec()
    s.shield_mode = 1          # per-bus shield: an end to credit
    s.shield_net = net
    s.credit_shields = credit
    return s


# ── a strap is a rail; obstruction is not ──────────────────────────────────

def test_an_identified_strap_becomes_a_rail():
    """The enabling fact.  A SPECIALNETS strap is metal belonging to a net —
    exactly what a PreRoutedSegment represents — so it is reported as one, at
    its own centre, width and span."""
    st = _grid()
    st.add_keepout(6, 100, 0, 110, 1000, "VDD")
    rails = st.preroutes(6, 0, 400, 0, 1000)
    assert len(rails) == 1, rails
    r = rails[0]
    assert r.label == "VDD" and r.slot_type == "POWER"
    assert r.track_position == 105.0 and r.width == 10.0
    assert (r.span_lo, r.span_hi) == (0.0, 1000.0)
    assert r.is_strap


def test_anonymous_obstruction_is_not_a_rail():
    """THE invariant.  A macro's OBS, a LAYER blockage and a hand-declared
    add_keepout all block, but nothing can be said about whose metal they
    are.  If one ever surfaced as a rail, a predicate asking "is there a VDD
    rail here" would start being answered by block footprints."""
    st = _grid()
    st.add_keepout(6, 300, 0, 310, 1000)          # no net
    assert st.preroutes(6, 0, 400, 0, 1000) == []


def test_a_strap_still_blocks():
    """Becoming a rail must not stop it being an obstacle: it is the same
    metal, and a signal still cannot use it."""
    st = _grid()
    st.add_keepout(6, 100, 0, 110, 1000, "VDD")
    g = st.get_layer_grid(6)
    assert g.count_signal_tracks_in(500.0, 100.0, 110.0) == 0


# ── the shared lookup ──────────────────────────────────────────────────────

def test_a_strap_beside_the_run_credits():
    """R5a's question, answered off a strap: identity matches, and the metal
    runs the length of the segment.

    The edge ABUTS the strap's near face (100), which is what "immediately
    adjacent" means on an all-signal layer: the tracks between the edge and
    the rail centre all fall inside the strap itself, and a track under
    occupied metal is not one a bit could have taken."""
    st = _grid()
    st.add_keepout(6, 100, 0, 110, 1000, "VDD")
    g = st.get_layer_grid(6)
    rail = buda.ndr_credit_rail(g, _spec(), 100.0, 1, 20.0, 0.0, 1000.0)
    assert rail is not None and rail.label == "VDD" and rail.is_strap


def test_a_foreign_net_does_not_credit():
    """Identity is the point: a GROUND strap cannot satisfy a rule asking for
    VDD, however well placed it is."""
    st = _grid()
    st.add_keepout(6, 100, 0, 110, 1000, "VSS")
    g = st.get_layer_grid(6)
    assert buda.ndr_credit_rail(g, _spec("VDD"), 100.0, 1, 20.0,
                                0.0, 1000.0) is None


def test_a_strap_that_does_not_span_the_run_does_not_credit():
    """Absent metal where it matters.  A rail covering half the segment
    shields half of it, which is not what the rule asked for."""
    st = _grid()
    st.add_keepout(6, 100, 0, 110, 400, "VDD")     # run needs 0..1000
    g = st.get_layer_grid(6)
    # allow_gap isolates the COVERAGE rule from the adjacency one: with the
    # strap short along the run, the tracks beside it are no longer under
    # metal, so without this the answer would be right for the wrong reason.
    assert buda.ndr_credit_rail(g, _spec(), 100.0, 1, 20.0,
                                0.0, 1000.0, True) is None


def test_a_rail_beyond_a_usable_signal_track_is_not_adjacent():
    """`immediately` is load-bearing: a free SIGNAL track between the run and
    the rail is a track a bit could have taken, so the rail is not flanking
    the shield that would have been emitted."""
    st = _grid()
    st.add_keepout(6, 100, 0, 110, 1000, "VDD")
    g = st.get_layer_grid(6)
    # Look from far away, so whole signal tracks intervene.
    assert buda.ndr_credit_rail(g, _spec(), 60.0, 1, 60.0,
                                0.0, 1000.0) is None
    # …and `allow_gap` is the culled-run escape hatch that forgives exactly
    # that, since a culled outer bit leaves an empty slot behind.
    assert buda.ndr_credit_rail(g, _spec(), 60.0, 1, 60.0,
                                0.0, 1000.0, True) is not None


def test_an_uncredited_rule_never_credits():
    """The opt-in is enforced in the predicate, not only at its call sites,
    so no caller can route around a rule that did not ask for crediting."""
    st = _grid()
    st.add_keepout(6, 100, 0, 110, 1000, "VDD")
    g = st.get_layer_grid(6)
    assert buda.ndr_credit_rail(g, _spec(credit=False), 100.0, 1, 20.0,
                                0.0, 1000.0) is None


def test_no_strap_no_rail():
    """An all-signal layer with no straps is the pre-PDN state: nothing to
    find, and the predicate says so rather than inventing a rail."""
    g = _grid().get_layer_grid(6)
    assert buda.ndr_credit_rail(g, _spec(), 100.0, 1, 20.0,
                                0.0, 1000.0) is None
