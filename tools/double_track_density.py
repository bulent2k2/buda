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
    tools/double_track_density.py --audit FILE        # check invariants (CI guard)

`--audit` deliberately does NOT answer "has this been doubled?".  That is not a
property of a file: a fixture with 16 signals per period could be natively 16
or a doubled 8, and nothing records which.  What it audits is the invariant
whose violation actually causes damage — every width, spacing and origin
BINARY-EXACT, and every slot list well-formed — plus a per-layer report of
period / signal count / density so a reviewer can see the shape at a glance.
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


def audit(path):
    """Verify the invariants that matter, and report the shape.  Returns 0 when
    clean, 1 on any violation (so it works as a CI guard).

    NOT "has this been doubled?" -- see the module docstring for why that
    question has no answer from the file alone."""
    print(f"=== {path} ===")
    bad = 0
    for lineno, line in enumerate(open(path), 1):
        m = PAT.match(line.rstrip("\n"))
        if not m:
            continue
        head, lid, origin, rest, _ = m.groups()
        if not rest.strip():
            continue
        try:
            slots = parse_slots(rest)
        except ValueError as e:
            print(f"   L{lid} (line {lineno}): MALFORMED — {e}")
            bad += 1
            continue
        offenders = [f"{t} {w}/{s}" for t, w, s in slots
                     if not (exact(w) and exact(s))]
        try:
            org_ok = exact(F(origin))
        except (ValueError, ZeroDivisionError):
            org_ok = False
        if not org_ok:
            offenders.append(f"origin {origin}")
        p, n = period(slots), n_signals(slots)
        status = "ok" if not offenders else "NOT BINARY-EXACT: " + ", ".join(offenders)
        print(f"   L{lid}: period={fmt(p)} signals={n} "
              f"density={float(signal_metal(slots) / p):.4f}  {status}")
        if offenders:
            bad += 1
    return 1 if bad else 0


def process(path, dry_run=False):
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
    ap.add_argument("--audit", action="store_true",
                    help="verify binary-exactness + well-formedness; exit "
                         "non-zero on a violation (CI guard)")
    a = ap.parse_args(argv)
    rc = 0
    # The transform is NOT idempotent, and the fixtures contain symlinks
    # (flow/big_data_test/big2/tracks4top.buda -> ../../tracks/tracks4top.buda).
    # Dedupe by real path or a listed symlink silently doubles its target TWICE.
    seen = set()
    for p in a.files:
        real = os.path.realpath(p)
        if real in seen:
            # relpath, absolute on failure: on Windows a path on another DRIVE
            # (C: temp vs D: checkout) makes os.path.relpath raise ValueError,
            # and a display nicety must not crash the tool (measured on the
            # windows-2022 doc-validation, run 8).
            try:
                disp = os.path.relpath(real)
            except ValueError:
                disp = real
            print(f"=== {p} ===\n   skipped: same file as an earlier argument "
                  f"({disp})")
            continue
        seen.add(real)
        rc |= audit(real) if a.audit else process(real, a.dry_run)
    return rc


if __name__ == "__main__":
    sys.exit(main())
