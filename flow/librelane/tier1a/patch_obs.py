#!/usr/bin/env python3
"""Magic's LEF with ONE layer's OBS replaced by OpenROAD's bloated cover --
the targeted form of #896's fix.

    patch_obs.py <magic.lef> <openroad.lef> <layer> <out.lef>

WHY NOT THE WHOLE ABSTRACT.  Reading `<cell>.openroad.lef` at the top does
close the notch (measured: `wbuf_cell`'s met2 hole at local 91.38-91.44,
14.97-15.29 is covered by a single 4.67,0 - 91.47,60 rect), but it also
adds blanket OBS on met4 and met5, where Magic's LEF has NONE -- and those
are the top's PDN layers.  pdngen drops every strap that crosses an
obstruction, so the macros lose their supply: measured 125,800 power grid
violations at N=8, the flow stopping at `Checker.PowerGridViolations`
before routing mattered.  That is §7.2's own rule ("FOREIGN metal on a PDN
layer is the dangerous kind") reproduced by the proposed fix.

So the fix has to be layer-scoped: take the bloated cover for the layer
whose abstract has the hole and keep Magic's everywhere else.  What it may
still cost is met2 routing INSIDE the macro, which the top used; the pins
stay reachable (a PIN is not an OBS) but the approach is narrower.  That is
the measurement.
"""
import re
import sys


def obs_rects(text, cell, layer):
    body = re.search(rf"MACRO {re.escape(cell)}(.*?)END {re.escape(cell)}", text, re.S)
    if not body:
        sys.exit(f"patch_obs: no MACRO {cell}")
    m = re.search(r"\n(\s*)OBS(.*?)\n\s*END\s*\n", body.group(1), re.S)
    if not m:
        sys.exit(f"patch_obs: {cell} has no OBS block")
    out, cur = [], None
    for ln in m.group(2).splitlines():
        lm = re.match(r"\s*LAYER\s+(\S+)", ln)
        if lm:
            cur = lm.group(1).rstrip(";")
            continue
        rm = re.match(r"\s*RECT\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)", ln)
        if rm and cur == layer:
            out.append(tuple(rm.groups()))
    return out


def main(argv):
    if len(argv) != 5:
        sys.exit(__doc__.strip().splitlines()[2].strip())
    magic_p, or_p, layer, out_p = argv[1:]
    magic, ortext = open(magic_p).read(), open(or_p).read()
    cell = re.search(r"MACRO\s+(\S+)", magic).group(1)
    want = obs_rects(ortext, cell, layer)
    if not want:
        sys.exit(f"patch_obs: the abstract declares no {layer} OBS for {cell} — "
                 f"nothing to substitute")
    have = obs_rects(magic, cell, layer)
    # Rewrite the OBS block: drop every RECT under `layer`, put the
    # abstract's in their place, leave every other layer byte for byte.
    lines, out, cur, done = magic.splitlines(True), [], None, False
    in_obs = False
    for ln in lines:
        if re.match(r"\s*OBS\b", ln):
            in_obs = True
            out.append(ln)
            continue
        if in_obs and re.match(r"\s*END\s*$", ln):
            in_obs = False
            out.append(ln)
            continue
        lm = re.match(r"\s*LAYER\s+(\S+)", ln)
        if in_obs and lm:
            cur = lm.group(1).rstrip(";")
            out.append(ln)
            if cur == layer and not done:
                for r in want:
                    out.append(f"      RECT {r[0]} {r[1]} {r[2]} {r[3]} ;\n")
                done = True
            continue
        if in_obs and cur == layer and re.match(r"\s*RECT\b", ln):
            continue                      # replaced above
        out.append(ln)
    if not done:
        sys.exit(f"patch_obs: {cell}'s OBS has no {layer} LAYER clause to replace")
    open(out_p, "w").write("".join(out))
    print(f"patch_obs: {cell} {layer} OBS {len(have)} rect(s) -> {len(want)} "
          f"from the abstract; every other layer unchanged -> {out_p}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
