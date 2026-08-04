#!/usr/bin/env python3
"""Double a track fixture's signal density, exactly.

Each `SIGNAL w s` slot becomes TWO `SIGNAL w/2 s/2` slots.  The pair occupies
`2*(w/2 + s/2) == w + s` — precisely the footprint the single slot had — so:

  * the unit PERIOD is preserved on every layer, by construction.  Every
    placement grid derived from it (`align_bottom_up`, the chip vehicles'
    504 = LCM(...), every checked-in floorplan) stays valid;
  * the signal METAL per period is preserved (2 * w/2 == w), so the layer's
    density is unchanged and its `def_layer` overhead needs no edit;
  * BINARY-EXACTNESS is preserved: halving a binary-exact value is binary-exact.
    This matters — `tracks_in_range` walks a period at a time with
    `pos += width + space_after`, so accumulated rounding decides whether a
    track sitting exactly ON a window edge falls inside it.  A fixture built
    from non-representable values breaks template alignment with an off-by-one
    at window edges (see flow/chip/ReadMe.md).

Rails (POWER/GROUND/CLOCK/SHIELD) are untouched, including their `space_after`,
so a fixture's own structure — unequal rail widths, asymmetric gaps, an odd
signal count per group — survives unchanged.  This is deliberately NOT the
symmetric rewrite the chip stack uses: symmetry only buys anything for a
MIRRORED placement, and none of these fixtures has one.

Usage:
    tools/double_track_density.py FILE [FILE ...]     # rewrite in place
    tools/double_track_density.py --dry-run FILE      # print, do not write
    tools/double_track_density.py --check FILE        # verify already doubled
"""
import argparse
import os
import re
import sys
from fractions import Fraction as F

RAILS = ("POWER", "GROUND", "CLOCK", "SHIELD", "VDD", "VSS", "GND", "CLK")
PAT = re.compile(r"^(\s*def_track_pattern\s+(\S+)\s+(\S+)\s+)(.*?)(\s*)$")


def fmt(x):
    f = float(x)
    return str(int(f)) if f == int(f) else f"{f:g}"


def parse_slots(rest):
    tok = rest.split()
    if len(tok) % 3:
        raise ValueError(f"slot list is not a multiple of 3: {rest!r}")
    return [(tok[i], F(tok[i + 1]), F(tok[i + 2])) for i in range(0, len(tok), 3)]


def period(slots):
    return sum(w + s for _, w, s in slots)


def signal_metal(slots):
    return sum(w for t, w, _ in slots if t.upper() not in RAILS)


def n_signals(slots):
    return sum(1 for t, _, _ in slots if t.upper() not in RAILS)


def double(slots):
    """Each signal slot -> two half-width, half-spaced slots."""
    out = []
    for t, w, s in slots:
        if t.upper() in RAILS:
            out.append((t, w, s))
        else:
            out.extend([(t, w / 2, s / 2), (t, w / 2, s / 2)])
    return out


def exact(x, denom=65536):
    return (x * denom).denominator == 1


def transform_line(line):
    """-> (new_line, note) or (line, None) if it is not a pattern line."""
    m = PAT.match(line.rstrip("\n"))
    if not m:
        return line, None
    head, lid, _origin, rest, _tail = m.groups()
    if not rest.strip():
        return line, None
    old = parse_slots(rest)
    new = double(old)

    # invariants, asserted rather than assumed
    assert period(new) == period(old), f"L{lid}: period {period(old)} -> {period(new)}"
    assert signal_metal(new) == signal_metal(old), f"L{lid}: metal changed"
    assert n_signals(new) == 2 * n_signals(old), f"L{lid}: signal count"
    for _, w, s in new:
        assert exact(w) and exact(s), f"L{lid}: {w}/{s} not binary-exact"

    body = "  ".join(f"{t} {fmt(w)} {fmt(s)}" for t, w, s in new)
    note = (f"L{lid}: {n_signals(old)} -> {n_signals(new)} signals, "
            f"period {fmt(period(old))} held, metal {fmt(signal_metal(old))} held")
    return head + body + "\n", note


def process(path, dry_run=False, check=False):
    with open(path) as fh:
        lines = fh.readlines()
    out, notes, changed = [], [], False
    for line in lines:
        new, note = transform_line(line)
        out.append(new)
        if note:
            notes.append(note)
            if new != line:
                changed = True
    if check:
        return 0 if not changed else 1
    print(f"=== {path} ===")
    for n in notes:
        print(f"   {n}")
    if dry_run:
        for line in out:
            if line.lstrip().startswith("def_track_pattern"):
                print("   " + line.rstrip())
    elif changed:
        with open(path, "w") as fh:
            fh.writelines(out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--dry-run", action="store_true", help="print, do not write")
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if a file would still change")
    a = ap.parse_args(argv)
    rc = 0
    # The transform is NOT idempotent, and the fixtures contain symlinks
    # (flow/big_data_test/big2/tracks4top.buda -> ../../tracks/tracks4top.buda).
    # Dedupe by real path or a listed symlink silently doubles its target TWICE.
    seen = set()
    for p in a.files:
        real = os.path.realpath(p)
        if real in seen:
            print(f"=== {p} ===\n   skipped: same file as an earlier argument "
                  f"({os.path.relpath(real)})")
            continue
        seen.add(real)
        rc |= process(real, a.dry_run, a.check)
    return rc


if __name__ == "__main__":
    sys.exit(main())
