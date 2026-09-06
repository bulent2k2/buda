#!/usr/bin/env python3
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

"""Did the hardened block keep the pins where the template put them?

    pin_def_verify.py template.def final.def

Exit 0 when every pin of the template's `PINS` section appears in the final
DEF with the SAME ABSOLUTE RECTANGLE on the same layer; exit 1 naming each
mismatch otherwise; exit 2 when the final DEF has no `PINS` section (or an
empty one) — a file with no pins cannot confirm anything, and reading it as
"nothing moved" is the false pass this script exists to refuse.

THE RULE (docs/internal/librelane_hier_flow.md §8 step 3, measured
2026-09-05): OpenROAD writes every pin back with its origin at the
rectangle's CENTRE, so the template's `LAYER met3 ( 0 -150 ) ( 2000 150 )
+ PLACED ( 0 8500 )` comes out as `LAYER met3 ( -1000 -150 ) ( 1000 150 ) +
PLACED ( 1000 8500 )` — the same metal, a different origin.  A check on the
`PLACED` point reports all 66 pins of the phase-0 block moved when 66 of 66
rectangles are identical.  So this compares `LAYER` offsets ADDED to the
`PLACED` point, and never the point alone.

Names are compared UNESCAPED (`d\\[0\\]` and `d[0]` are one pin).  A pin
with several `PORT`s or several `LAYER` rectangles contributes each
absolute rectangle; every template rectangle must be present in the final
pin's set (extra final rectangles are reported as notes, not failures — a
router may add access metal, and that is not the pin moving).  Orientation
tokens other than `N` are transformed by the DEF convention before the
offsets are added.  Both files must state the same `UNITS DISTANCE MICRONS`
or the numbers are not comparable, which is reported as a mismatch of the
files rather than of a pin.
"""
import re
import sys

_PINS = re.compile(r"^PINS\s+(\d+)\s*;(.*?)^END PINS", re.S | re.M)
_ENTRY = re.compile(r"^\s*-\s+(\S+)(.*?);", re.S | re.M)
_LAYER = re.compile(r"\+\s*LAYER\s+(\S+)(?:\s+\+\s*MASK\s+\d+)?\s*"
                    r"\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)")
_PLACE = re.compile(r"\+\s*(?:PLACED|FIXED|COVER)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)"
                    r"\s*([A-Z]{1,2})?")
_UNITS = re.compile(r"^UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;", re.M)


def unescape(name):
    return name.replace("\\", "")


def _orient(x, y, o):
    """A pin-local offset under a DEF orientation token."""
    o = o or "N"
    if o == "N":
        return x, y
    if o == "S":
        return -x, -y
    if o == "W":
        return -y, x
    if o == "E":
        return y, -x
    if o == "FN":
        return -x, y
    if o == "FS":
        return x, -y
    if o == "FW":
        return y, x
    if o == "FE":
        return -y, -x
    raise ValueError(f"unknown orientation token {o!r}")


def read_pins(text, what):
    """{pin name: set of (layer, x1, y1, x2, y2) absolute rectangles}.

    Raises ValueError with the reason when the file has no usable PINS
    section — the caller decides that this is the refusal, not a pass."""
    m = _PINS.search(text)
    if not m:
        raise ValueError(f"{what}: no PINS section")
    declared, body = int(m.group(1)), m.group(2)
    pins = {}
    for e in _ENTRY.finditer(body):
        name, rest = unescape(e.group(1)), e.group(2)
        rects = set()
        # A multi-PORT pin repeats LAYER/PLACED per PORT; split there so each
        # rectangle is placed by ITS port's point.
        groups = re.split(r"\+\s*PORT\b", rest)
        for g in groups:
            layers = _LAYER.findall(g)
            place = _PLACE.search(g)
            if not layers:
                continue
            if not place:
                raise ValueError(f"{what}: pin {name!r} has LAYER geometry "
                                 f"but no PLACED/FIXED/COVER point")
            px, py, o = int(place.group(1)), int(place.group(2)), place.group(3)
            for layer, x1, y1, x2, y2 in layers:
                ax1, ay1 = _orient(int(x1), int(y1), o)
                ax2, ay2 = _orient(int(x2), int(y2), o)
                rects.add((layer, px + min(ax1, ax2), py + min(ay1, ay2),
                           px + max(ax1, ax2), py + max(ay1, ay2)))
        pins[name] = rects
    if not pins:
        raise ValueError(f"{what}: PINS section declares {declared} and "
                         f"holds no pin entry")
    return pins


def _units(text):
    m = _UNITS.search(text)
    return int(m.group(1)) if m else None


def compare(template_text, final_text):
    """(mismatches, notes, n_ok, n_template) — the verdict as data."""
    mismatches, notes = [], []
    tu, fu = _units(template_text), _units(final_text)
    if tu is not None and fu is not None and tu != fu:
        mismatches.append(f"UNITS differ: template {tu}, final {fu} — the "
                          f"coordinates are not comparable")
    t = read_pins(template_text, "template")
    f = read_pins(final_text, "final")
    n_ok = 0
    for name in sorted(t, key=lambda n: [int(x) if x.isdigit() else x
                                          for x in re.split(r"(\d+)", n)]):
        want = t[name]
        if not want:
            mismatches.append(f"{name}: the template gives it no rectangle")
            continue
        got = f.get(name)
        if got is None:
            mismatches.append(f"{name}: absent from the final DEF")
            continue
        missing = sorted(want - got)
        if missing:
            wanted = "; ".join(f"{l} ({x1} {y1}) ({x2} {y2})"
                               for l, x1, y1, x2, y2 in missing)
            have = "; ".join(f"{l} ({x1} {y1}) ({x2} {y2})"
                             for l, x1, y1, x2, y2 in sorted(got)) or "nothing"
            mismatches.append(f"{name}: template rect {wanted} not in the "
                              f"final DEF, which has {have}")
            continue
        n_ok += 1
        extra = sorted(got - want)
        if extra:
            notes.append(f"{name}: final DEF adds {len(extra)} rectangle(s) "
                         f"beyond the template's (access metal?)")
    return mismatches, notes, n_ok, len(t)


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[0])
        print("usage: pin_def_verify.py template.def final.def")
        return 2
    tpath, fpath = argv[1], argv[2]
    try:
        with open(tpath) as fh:
            tt = fh.read()
        with open(fpath) as fh:
            ft = fh.read()
    except OSError as e:
        print(f"REFUSED: {e}")
        return 2
    try:
        mismatches, notes, n_ok, n_t = compare(tt, ft)
    except ValueError as e:
        print(f"REFUSED: {e} — a DEF with no pins cannot confirm the template "
              f"was honoured (is this the FINAL DEF of the hardened block?)")
        return 2
    for n in notes:
        print(f"note: {n}")
    if mismatches:
        for m in mismatches:
            print(f"MISMATCH: {m}")
        print(f"FAIL: {len(mismatches)} mismatch(es); {n_ok} of {n_t} template "
              f"pin(s) kept their absolute rectangle")
        return 1
    print(f"PASS: {n_ok} of {n_t} template pin(s) appear in the final DEF "
          f"with an identical absolute rectangle (origins are NOT compared — "
          f"OpenROAD re-centres them)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
