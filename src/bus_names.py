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

"""Which bus a net name belongs to — the ONE place that decides.

BUDA has two ways to get a bus, and they do not look alike:

  * **declared** — `add_bus p[4]` in a `.buda` script expands to `p_0 … p_3`
    (and `add_net` may use `p_b0`): an UNDERSCORE, an optional letter tag,
    and a decimal index;
  * **imported** — a design read from LEF/DEF/Verilog keeps the netlist's
    own names, `p[0] … p[3]`: BRACKETS.

Three call sites needed this and each carried its own copy of the same
regex, which knew only the declared spelling.  On an imported design every
bit therefore looked like a bus of its own, and each site failed in its own
way:

  * the visualizer's design-stats line reported `1230 buses · 1230 nets`
    for `flow/rv` — a design with 49 buses.  A bus count equal to the net
    count is not a count, and that line exists to be read at a glance;
  * the per-bundle log summary never compressed, printing
    `dbg[0], dbg[10], dbg[11], …` where it exists to print `dbg[0:31]`;
  * `set_max_bundle_bits` lost the structure it promises to preserve — its
    splitter keeps "bits of one bus together", and with every bit its own
    group there was nothing to keep together, so a bus could be cut down
    the middle with no diagnostic.

The bracket rule is `derive_bus_bit` (`src/bdb.cpp`) written out, so this
agrees with `net_props.bus_name`/`bit_index` by construction: an all-digit
non-empty index, and a base that is not itself empty.  Reading `net_props`
would be the single source of truth, but two of the three callers have no
BDB handle and threading one in for a display rule is not worth the
coupling — sharing the rule is.

Standalone and dependency-free (like `slot_groups.py`), so `tools/`, the
viz and the session layer can all import it without pulling in the
compiled extension.
"""
import re

# The declared form: '<bus>_<tag><idx>' — tag is an optional letter run, so
# 'bus_077_b00' is bus 'bus_077', tag 'b', index 0.
_DECLARED = re.compile(r'^(.*?)_([A-Za-z]*)(\d+)$')

# The tag reserved for the imported (bracket) form.  It cannot collide with a
# declared tag, which is letters only.
BRACKET_TAG = '[]'

# `bus_key`'s first element: which KIND of thing the key names.  A scalar gets
# its own namespace rather than sharing the declared form's empty tag, because
# a two-part (tag, bus) key collides — scalar `p` against declared bus `p_0`,
# both ('', 'p') — and so does the (bus, tag) order it replaced, in the
# obscure direction: scalar `b` against declared bus `_b0`.  A key that merges
# two distinct buses reports one too few and contradicts the rule that a
# scalar is its own bus (Codex P2 on #704).
_BUS, _SCALAR = 'bus', 'net'


def parse_bus_bit(net_name):
    """`(bus, tag, index)` for a net that belongs to a bus, else `None`.

    `tag` distinguishes the two spellings so two buses that differ only in
    how they are written never merge: declared `p_0` gives `('p', '', 0)`
    and imported `p[0]` gives `('p', '[]', 0)`.
    """
    if len(net_name) >= 4 and net_name.endswith(']'):
        br = net_name.rfind('[')
        if br > 0:
            inner = net_name[br + 1:-1]
            # `isdigit()` is true for non-ASCII digits, which `int()` accepts
            # but the C++ rule (`std::isdigit`) does not — keep them apart.
            if inner and all('0' <= c <= '9' for c in inner):
                return net_name[:br], BRACKET_TAG, int(inner)
    m = _DECLARED.match(net_name)
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    return None


def bus_key(net_name):
    """A hashable bus identity, for counting or grouping.

    `(kind, name, tag)`: the KIND separates a scalar from a bus that happens
    to share its name, and the TAG separates the two bus spellings, so no two
    distinct things can ever share a key.  A net in neither bus form is its
    own bus, which is what a scalar signal is.
    """
    parsed = parse_bus_bit(net_name)
    if parsed:
        return (_BUS, parsed[0], parsed[1])
    return (_SCALAR, net_name, '')


def bus_name(net_name):
    """The bus's NAME, or the net name when it is not part of a bus.

    Distinct from `bus_key`: two differently-spelled buses can share a name
    (`p_0` and `p[0]` are both bus `p`), so this is for display and for
    grouping where the spelling does not matter.
    """
    parsed = parse_bus_bit(net_name)
    return parsed[0] if parsed else net_name
