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

"""Every segment-index-keyed map on `Topology` must be re-keyed by BOTH helpers.

`Topology` carries a growing family of annotations keyed by segment index —
`seg_busterms`, `seg_conns`, `seg_bits`, `seg_busterm_bits`.  Two helpers
renumber segments and so must renumber all of them together:

    prepend_segment   (topology.h)   front-insert, shift every key up by one
    erase_segment     (topology.cpp) remove, shift keys above it down by one

Miss one and the maps desync: an entry answers for the wrong segment.  That is
particularly quiet for `seg_busterm_bits`, because `seg_busterm_serves_bit`
resolves the tap ordinal against `seg_busterms` and then reads the tap
membership — so a shifted `seg_busterms` with an unshifted `seg_busterm_bits`
makes DetailedNUTS retract a bit off a real block face while `check_dnuts`,
reading the same predicate, agrees with it.  Both stages sharing one predicate
is what makes them consistent; it is also what stops either from noticing.

This is a SOURCE-level guard rather than a behavioural one on purpose.
`prepend_segment`'s only caller front-inserts during generation, before
`derive_fanin_seg_bits` has written anything, so today no input can reach the
bug — there is nothing to assert at runtime, and the project has no C++ unit
harness to reach the helper directly.  What can be checked is the invariant the
next map will need: that adding a `seg_*` map to `Topology` and forgetting one
of the two helpers fails here rather than in a routed design.
"""
import re
from pathlib import Path

import pytest

_SRC = Path(__file__).parents[2] / "src"


def _topology_struct():
    text = (_SRC / "topology.h").read_text()
    start = text.index("struct Topology {")
    # Balance braces from the opening one so nested types cannot end it early.
    depth, i = 0, text.index("{", start)
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[start:j]
    raise AssertionError("unterminated struct Topology")


def _body(text, signature):
    """The function body following `signature`, by brace balance."""
    start = text.index(signature)
    depth, i = 0, text.index("{", start)
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j]
    raise AssertionError(f"unterminated body for {signature}")


def _index_keyed_maps():
    """Declared `std::map` members of Topology whose name starts with `seg_`.

    The prefix IS the discipline: `bridge_segments` is keyed by block name and
    is deliberately excluded, while every `seg_*` map is keyed by segment index
    (alone, or paired with an endpoint/ordinal).
    """
    decls = re.findall(r"std::map<[^;]*?>\s+(seg_\w+)\s*;", _topology_struct(),
                       re.S)
    return sorted(set(decls))


def test_the_index_keyed_family_is_what_we_think_it_is():
    """Guard the guard: if this list changes, the two tests below start
    covering something new — which is the point — but a silently EMPTY list
    would make them vacuous."""
    maps = _index_keyed_maps()
    assert maps == ["seg_bits", "seg_busterm_bits", "seg_busterms", "seg_conns"], maps


@pytest.mark.parametrize("member", _index_keyed_maps())
def test_prepend_segment_rekeys_every_index_keyed_map(member):
    body = _body((_SRC / "topology.h").read_text(),
                 "inline void prepend_segment(")
    assert member in body, (
        f"prepend_segment does not re-key Topology::{member}. A front-insert "
        f"shifts every segment index by one, so an untouched {member} answers "
        f"for the segment below its key.")


@pytest.mark.parametrize("member", _index_keyed_maps())
def test_erase_segment_rekeys_every_index_keyed_map(member):
    body = _body((_SRC / "topology.cpp").read_text(),
                 "void erase_segment(Topology& topo, int idx)")
    assert member in body, (
        f"erase_segment does not re-key Topology::{member}. Removing a segment "
        f"compacts the indices above it, so an untouched {member} answers for "
        f"the segment above its key.")
