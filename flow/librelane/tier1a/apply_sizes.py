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
"""BUDA's block sizes as emitter knobs — the H+B arm's first half.

    apply_sizes.py <sizes dir> [--n N] [--baseline <n dir>] [--json]

`emit_block_size` (flow/librelane/tier1a/size.buda) says how big each leaf
cell has to be: the larger of its FACE demand and its AREA demand, per
axis.  Arm H sizes them instead with `PEPAD`, a single pad added to every
cell, and §8 step 7d measured what that costs — arm H's die is **8.66x**
arm F's at N = 4, which is a SIZING artifact rather than a cost of
hierarchy, and therefore the headroom arm H+B exists to take back.

Feeding the sizes back is NOT a matter of re-hardening the blocks at a new
size: the emitter derives the PE pitch from `PEW` (`PPX = PEW + CHAN`), the
row from the pitch, and the die from the row, so a block that shrinks moves
every instance and the die with it.  The array has to be RE-EMITTED, and
this is what turns the fragments into the `gen.sh` arguments that do it:

    ./gen.sh 4 $(python3 apply_sizes.py out --n 4 --args)
    ./harm.sh 4

**The emitter has ONE edge size** (`EDGEW`/`EDGEH` govern `feed_cell`,
`wbuf_cell` and `acc_cell` alike), so the edge knob is the MAX over the
three — the smallest size that holds all of them.  That is reported, since
it is where the rule's per-cell answer is coarsened: on the checked-in N=8
set the rule gives feed and wbuf 44.2 x 44.2 and acc 96.0 x 51.1, so the
edge cells go out at acc's size and two of them stay larger than they need.
Making them independent is an emitter change, not a sizing one.

The predicted die is computed with the emitter's own arithmetic (below,
transcribed from flow/tcl/tpu_lib.tcl and pinned against it by a test), so
the saving can be read BEFORE paying for a run — but it is a prediction,
and the H row that matters is the measured one.
"""
import argparse
import glob
import json
import math
import os
import re
import sys

# flow/tcl/tpu_lib.tcl's defaults for everything this does not resize.
CHAN, ROWM, EDGEGAP, X0, Y0 = 48, 12, 48, 60, 120
PIPE, PIPEGAP = 2, 48
EDGE_CELLS = ("feed_cell", "wbuf_cell", "acc_cell")


def die(n, pew, peh, edgew, edgeh, chan=CHAN, rowm=ROWM):
    """The emitter's die, transcribed from `tpu_vehicle::configure`.

    PPX = PEW + CHAN; PPY = PEH; ROWGAP = CHAN (the compact default, which
    is what `gen.sh` uses — `-ALIGN 1` snaps it to the track period and is
    a different experiment)."""
    ppx, ppy = pew + chan, peh
    rw = 2 * rowm + (n - 1) * ppx + pew
    rh = 2 * rowm + peh
    rpy = rh + chan
    w = X0 + rw + edgew + EDGEGAP + X0
    h = (Y0 + n * rpy + EDGEGAP + edgeh
         + (PIPE + 1) * PIPEGAP + PIPE * edgeh + Y0)
    return w, h, ppx, ppy


def best_aspect(area_um2, util_pct, face_w, face_h, n, edgew, edgeh,
                lo=0.05, hi=20.0, steps=4000):
    """The PE shape that minimises the ARRAY's die, not the cell's own.

    `emit_block_size` shapes a block by its faces' ratio, which is the right
    default for ONE block and not what the array wants: the emitter spends
    `PPX = PEW + CHAN` once per column, so a wide PE costs N times its width
    and only once its height.  The cell's AREA and its FACE floors are fixed
    by the rule; the aspect between them is free, and this spends that free
    variable on the die.  Returns (w, h, aspect)."""
    core = area_um2 / (util_pct / 100.0)
    best = None
    for i in range(steps + 1):
        r = lo * (hi / lo) ** (i / steps)
        w = max(math.sqrt(core * r), face_w)
        h = max(math.sqrt(core / r), face_h)
        dw, dh, _px, _py = die(n, int(math.ceil(w)), int(math.ceil(h)),
                               edgew, edgeh)
        if best is None or dw * dh < best[0]:
            best = (dw * dh, int(math.ceil(w)), int(math.ceil(h)), r)
    return best[1], best[2], best[3]


def read_sizes(d):
    """{cell: (w, h, binds, faces)} from the emit_block_size fragments."""
    out = {}
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            j = json.load(open(p))
        except ValueError as e:
            sys.exit(f"apply_sizes: {p}: not JSON ({e})")
        if "DIE_AREA" not in j or "cell" not in j:
            continue                      # not a sizing fragment
        der = j.get("derivation", {})
        out[j["cell"]] = (j["DIE_AREA"][2], j["DIE_AREA"][3],
                          der.get("binds", {}), der.get("face", {}))
    if not out:
        sys.exit(f"apply_sizes: no emit_block_size fragment in {d}\n"
                 f"  remedy: bin/buda --no-viz flow/librelane/tier1a/size.buda")
    return out


def lef_sizes(path):
    """{cell: (w, h)} from the emitted LEF, for the baseline comparison."""
    out, cell = {}, None
    for ln in open(path):
        m = re.match(r"\s*MACRO\s+(\S+)", ln)
        if m:
            cell = m.group(1)
        m = re.match(r"\s*SIZE\s+([0-9.]+)\s+BY\s+([0-9.]+)", ln)
        if m and cell:
            out[cell] = (float(m.group(1)), float(m.group(2)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sizes", help="the directory of emit_block_size fragments")
    ap.add_argument("--n", type=int, default=8, help="array size the die is predicted for")
    ap.add_argument("--baseline", help="an n<N>/ directory, to compare against its tpu.lef")
    ap.add_argument("--optimize-aspect", action="store_true",
                    help="reshape the PE to minimise the ARRAY's die (same area "
                         "and the same face floors, a different aspect)")
    ap.add_argument("--args", action="store_true", help="print only the gen.sh arguments")
    ap.add_argument("--json", action="store_true", help="print the whole result as JSON")
    a = ap.parse_args(argv)
    if a.n < 2:
        sys.exit("apply_sizes: --n must be at least 2")

    sizes = read_sizes(a.sizes)
    if "pe_cell" not in sizes:
        sys.exit(f"apply_sizes: no pe_cell fragment in {a.sizes} — the PE sets "
                 f"PEW/PEH and the array's pitch, so it cannot be inferred")
    # The emitter's knobs are integers (its geometry is integer arithmetic),
    # and rounding DOWN would hand a bus a face smaller than the rule said
    # it needs — so up, always.
    pew, peh = (int(math.ceil(v)) for v in sizes["pe_cell"][:2])
    edge = [(c, sizes[c][0], sizes[c][1]) for c in EDGE_CELLS if c in sizes]
    if edge:
        edgew = int(math.ceil(max(w for _c, w, _h in edge)))
        edgeh = int(math.ceil(max(h for _c, _w, h in edge)))
    else:
        edgew, edgeh = pew, peh
    rule_pew, rule_peh, aspect_note = pew, peh, None
    if a.optimize_aspect:
        frag = os.path.join(a.sizes, "pe_cell.json")
        d = json.load(open(frag)).get("derivation", {})
        ar = d.get("area")
        if not ar:
            sys.exit("apply_sizes: --optimize-aspect needs pe_cell's AREA "
                     "demand — re-run size.buda with `area` or `metrics`")
        pew, peh, r = best_aspect(ar["instance_area"], ar["utilization_pct"],
                                  d.get("face_needs", {}).get("w", 0.0),
                                  d.get("face_needs", {}).get("h", 0.0),
                                  a.n, edgew, edgeh)
        aspect_note = (f"PE reshaped {rule_pew} x {rule_peh} -> {pew} x {peh} "
                       f"(aspect {r:.2f}, same area and the same face floors) "
                       f"to minimise the ARRAY's die")
    args = f"-PEW {pew} -PEH {peh} -EDGEW {edgew} -EDGEH {edgeh}"
    if a.args:
        print(args)
        return 0

    w, h, ppx, ppy = die(a.n, pew, peh, edgew, edgeh)
    result = {"n": a.n, "gen_args": args, "aspect_note": aspect_note,
              "pe": {"w": pew, "h": peh}, "edge": {"w": edgew, "h": edgeh},
              "predicted_die": {"w": w, "h": h, "mm2": round(w * h / 1e6, 4)},
              "pitch": {"x": ppx, "y": ppy}}

    if a.baseline:
        lef = os.path.join(a.baseline, "tpu.lef")
        if not os.path.isfile(lef):
            sys.exit(f"apply_sizes: no {lef} — is --baseline an emitted n<N>/ dir?")
        base = lef_sizes(lef)
        if "pe_cell" not in base:
            sys.exit(f"apply_sizes: {lef} declares no MACRO pe_cell")
        bw, bh = base["pe_cell"]
        b_edge = [base[c] for c in EDGE_CELLS if c in base]
        be_w = max([e[0] for e in b_edge], default=bw)
        be_h = max([e[1] for e in b_edge], default=bh)
        ow, oh, _px, _py = die(a.n, bw, bh, be_w, be_h)
        result["baseline"] = {"pe": {"w": bw, "h": bh},
                              "edge": {"w": be_w, "h": be_h},
                              "die": {"w": ow, "h": oh,
                                      "mm2": round(ow * oh / 1e6, 4)}}
        result["die_ratio"] = round((w * h) / (ow * oh), 4) if ow * oh else None

    if a.json:
        json.dump(result, sys.stdout, indent=4, sort_keys=True)
        print()
        return 0

    print(f"apply_sizes: N={a.n}  pe_cell {pew} x {peh}, edge {edgew} x {edgeh}")
    for cell, (cw, ch, binds, faces) in sorted(sizes.items()):
        b = f"{binds.get('w', '?')}/{binds.get('h', '?')}"
        note = ""
        if cell in EDGE_CELLS and (math.ceil(cw) < edgew or math.ceil(ch) < edgeh):
            note = (f"  <- emitted at the edge size {edgew} x {edgeh}: the "
                    f"emitter has ONE edge cell size")
        print(f"  {cell:<10} rule {cw:7.1f} x {ch:6.1f}  binds {b}{note}")
    if aspect_note:
        print(f"apply_sizes: {aspect_note}")
    print(f"apply_sizes: predicted die {w} x {h} um = {w * h / 1e6:.3f} mm2 "
          f"(PE pitch {ppx} x {ppy})")
    if a.baseline:
        b = result["baseline"]
        print(f"apply_sizes: baseline    {b['die']['w']} x {b['die']['h']} um = "
              f"{b['die']['mm2']:.3f} mm2 (pe_cell {b['pe']['w']:g} x "
              f"{b['pe']['h']:g}, edge {b['edge']['w']:g} x {b['edge']['h']:g})")
        print(f"apply_sizes: predicted die is {result['die_ratio']:.2f}x the "
              f"baseline's — a PREDICTION from the emitter's own arithmetic; "
              f"the H row that counts is the measured one")
    print(f"apply_sizes: regenerate with  ./gen.sh {a.n} {args}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
