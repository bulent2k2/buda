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

"""WHETHER a BDB component has a placement — the one place that decides.

A component row carries a bbox and no flag, so "is this placed?" is a
question about the NUMBERS, and the readers had four different answers to
it:

  * `_add_blocks_from_bdb` (the BDB -> session Floorplan projection):
    `x1 < 0 or y1 < 0` — ANY negative coordinate;
  * `_build_bdb_floorplan` / `_build_cell_local_floorplan` (the frames hier
    generation builds): `x1 >= 0`;
  * `_floorplan_for_hbundle`'s cross-level case: no test at all, so an
    unplaced component entered the frame as a block at (-1,-1,-1,-1).

Generation validates a candidate in ITS frame and `load_pipeline` validates
it in the session Floorplan, so any disagreement between those answers is a
resume failure by construction — which is how it surfaced (`flow/ariane133`,
`load_pipeline expanded`: "block(s) i_cache_subsystem ... are not in the
current Floorplan", on a checkpoint whose build session had reported
success).

The sentinel is `-1,-1,-1,-1` — what `import_verilog` writes for a component
the netlist knows and no file places, and what `import_def_lef` writes for an
`UNPLACED` component.  A NEGATIVE COORDINATE IS NOT THE SENTINEL: a DEF `PIN`
straddles the die edge (ariane133's 495 die ports sit at x1 = -70), and
`derive_container_bboxes`' margin can push a derived container box below the
origin (ariane133's whole `i_cache_subsystem` lands at y1 = -240).  Reading
those as "unplaced" silently drops real geometry — 496 of 504 depth-0
components on that design, the top-level cache container among them.

The Floorplanner learned this the expensive way first (audit T2-03, in
`tools/floorplanner_commands.py`: the same `x1 < 0 or y1 < 0` dropped
legitimately-negative blocks on reopen, losing them from the saved design).
Its rule is the one written down here, so the GUI and the routing session
cannot drift apart again.

A DEGENERATE box counts as unplaced too, and that is not a second rule
bolted on: a zero-area block hosts no routing and gives a topology generator
nothing to tap, so admitting one produces exactly the phantom the sentinel
check exists to prevent.  It also subsumes the sentinel (`-1 <= -1`); the
sentinel clause is kept explicit because it is the case a reader needs
named.

Standalone and dependency-free, for the reason `slot_groups.py` and
`bus_names.py` are: `tools/` reads component rows too, and a copy in each
layer is how the four answers happened.
"""


def bbox_is_placed(x1, y1, x2, y2) -> bool:
    """True iff this bbox is a real extent rather than a no-placement marker.

    Takes the four numbers, so a caller holding a Floorplanner block, a DEF
    rect or a database row can ask without first shaping it into a component.
    """
    if x1 == -1 and y1 == -1:
        return False            # the no-placement sentinel, named explicitly
    return x2 > x1 and y2 > y1  # a degenerate box is not an extent


def is_placed(c) -> bool:
    """True iff component row `c` has a placement (see `bbox_is_placed`)."""
    return bbox_is_placed(c.x1, c.y1, c.x2, c.y2)


def is_unplaced(c) -> bool:
    """The negation, for call sites that read better that way."""
    return not is_placed(c)
