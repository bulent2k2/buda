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

"""`Topology::seg_busterm_bits` says which bits each BUSTERM tap serves.

The map answers one question — "does this tap belong to this bit?" — for the
placer and both audits, through `seg_busterm_serves_bit`.  Its reading of a
MISSING key is what these tests are about.

Absence used to do double duty: "this topology is untapered" and "the walk
never reached this tap", and the second reading handed the tap back to every
bit riding the segment.  The derivation now SEEDS an entry for every tap on a
segment it gave bits to, so within a tapered topology absence no longer occurs
and an empty list is a recorded verdict ("serves no bit") rather than a gap.

Worth stating plainly, because it bounds what these tests can claim: the
second reading was never observed to fire.  Instrumenting the derivation to
print every tap the walk left unmarked found ZERO across the 41-flow corpus
and every taper vehicle in the tree, and the shape needed to produce one —
a block tapped by two segments where the group routing to it walks the other
one — resisted synthesis, because a group whose receiver is unreachable takes
the `mark_all` fallback instead, which marks every tap.  So the change is a
convention made explicit, measured inert, not a repair with a witness.
"""
from pathlib import Path

import pytest

import buda
import buda_cli

_ROOT = Path(__file__).parents[2]
_TAPER = _ROOT / "flow/antenna_taper_passthru.buda"

pytestmark = pytest.mark.mid


@pytest.fixture(scope="module")
def taper():
    """flow/antenna_taper_passthru: DIVERGENT folds three buses off one driver
    into one per-bit tapered fan-out tree — a trunk whose taps serve different
    bits, which is exactly what the map exists to describe."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.do_command(f"source {_TAPER}")
    return s


def _selected(session):
    for w in session.bundles:
        topo = w.input.candidates[w.plan.selected_topology_index]
        if topo.seg_busterm_bits:
            return topo
    pytest.fail("no tapered topology in the session")


def test_the_map_is_exposed_and_keyed_by_segment_and_tap_ordinal(taper):
    """The binding itself: a taper question is nearly always 'which bits does
    this tap belong to?', and answering it from Python otherwise means
    inferring it from the spans the placer already produced."""
    topo = _selected(taper)
    for key, bits in topo.seg_busterm_bits.items():
        si, ordinal = key
        assert 0 <= si < len(topo.segments), key
        assert ordinal in (0, 1), key
        assert list(bits) == sorted(set(bits)), (key, list(bits))


def test_every_tap_on_a_bit_carrying_segment_is_recorded(taper):
    """THE invariant the seeding establishes: within a tapered topology, a tap
    on a segment the walk gave bits to always has an entry.

    That is what lets an EMPTY entry mean "serves no bit" — if absence could
    still mean "the walk skipped it", an empty list would be indistinguishable
    from a gap and could not be read as a verdict.
    """
    topo = _selected(taper)
    for si, bits in topo.seg_bits.items():
        if not list(bits):
            continue
        eps = topo.seg_busterms.get(si)
        if eps is None:
            continue
        for ordinal, ep in enumerate(eps):
            if ep is None:
                continue
            assert (si, ordinal) in topo.seg_busterm_bits, (
                f"seg {si} tap {ordinal} to '{ep.block_name}' has no entry — "
                f"absence would be read as 'serves every bit'")


def test_no_entry_names_a_tap_the_topology_does_not_have(taper):
    """The map must assert only things that are true of this topology: no
    entry for a segment with no busterm annotation, and none for an ordinal
    that holds no tap."""
    topo = _selected(taper)
    for (si, ordinal), _bits in topo.seg_busterm_bits.items():
        eps = topo.seg_busterms.get(si)
        assert eps is not None, f"entry for seg {si}, which taps nothing"
        assert eps[ordinal] is not None, \
            f"entry for seg {si} ordinal {ordinal}, which holds no tap"


def test_a_taps_bits_are_a_subset_of_the_bits_riding_its_segment(taper):
    """A tap cannot serve a bit that does not ride the segment carrying it —
    the relationship between the two maps, which the placer relies on when it
    retracts a bit past a tap that is not the bit's."""
    topo = _selected(taper)
    for (si, _ord), bits in topo.seg_busterm_bits.items():
        riding = set(topo.seg_bits.get(si, []))
        if not riding:
            continue
        assert set(bits) <= riding, (si, sorted(bits), sorted(riding))
