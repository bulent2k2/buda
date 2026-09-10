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

"""A DEF orientation applied to an OFFSET, in one place.

DEF gives a `PIN`'s `PORT` geometry relative to the pin's own origin and
transforms it by the pin's orientation.  That offset is anchored at a POINT
rather than normalized inside an extent, so it may be negative on either
axis -- which is what separates this from `src/orient_rect.py`, whose rects
sit inside a cell's `w x h` box and keep their lower-left.  (One expression
covers both: over a box `S` is `(w-x, h-y)`, about a point `(-x, -y)`, the
same thing at `w = h = 0`, which is how the C++ side gets away with a single
function -- `def_orient_xf` in `bdb.cpp`, the twin of this table.)

**These are DEF's tokens, not BDB's.**  The two conventions agree on the
pure rotations and differ on the two DIRECTION-PRESERVING flips: DEF mirrors
about Y, so `FN` is `(-x, y)`, while BDB mirrors about X and its `FN` is
`(x, -y)` (and `FS` the same the other way).  They AGREE on the two
axis-SWAPPING flips -- `FW` is the plain transpose `(y, x)` under both and
`FE` is `(h-y, w-x)` under both.  This said "all four" until it was measured
(`test/tests/test_def_orient_twins.py`); the example it gave was `FN`, one of
the two that do differ.  `def_orient_to_bdb` in `bdb.cpp` permutes all four
flip TOKENS, which for `FE`/`FW` is a claim about the stored token rather
than about these transforms -- see that test's note.  A token read straight
out of a DEF file belongs here; a `component.orient` read back out of a BDB
belongs in `src/orient_rect.py`.

The twins whose agreement with this table is MEASURED rather than argued
(#912 is why): `def_orient_xf` in `src/bdb.cpp`, and `_ORIENT` in
`flow/librelane/tier1a/pdn_phase.py` -- the latter a stdlib-only script that
must run inside a LibreLane run tree, so it cannot import this.

Lives in `tools/` rather than `src/` because both callers are here and one of
them (`pin_def_verify.py`) is run as a bare script from an arbitrary
directory inside a LibreLane run tree, with nothing on `PYTHONPATH` -- so
what it can import is its own directory and no more.

The UNKNOWN-token policy is deliberately the CALLER's and not this module's:
`pin_def_verify` REFUSES one (a verifier that silently mis-places a
rectangle reports a pin unmoved when it has no idea), while
`def_viz_shared`'s loader falls back to the identity, matching the reader's
own `else` branch so the picture cannot depend on which loader ran.
"""

# offset (x, y) -> transformed offset, per DEF orientation token.
DEF_ORIENT_POINT = {
    "N":  lambda x, y: (x, y),
    "S":  lambda x, y: (-x, -y),
    "FN": lambda x, y: (-x, y),       # mirror Y
    "FS": lambda x, y: (x, -y),       # mirror X
    "W":  lambda x, y: (-y, x),       # CCW 90
    "E":  lambda x, y: (y, -x),       # CW 90
    "FW": lambda x, y: (y, x),
    "FE": lambda x, y: (-y, -x),
}
