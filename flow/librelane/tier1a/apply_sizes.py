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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harm                                  # noqa: E402  (same directory)
import pdn_phase as pp                       # noqa: E402

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


def usable_core(w, h):
    """The core area a `w x h` block actually offers, from `harm.py`'s own
    `block_core` and row snapping — NOT the nominal die.

    This is the distinction that made the first cut of this tool wrong
    (Codex #890): `emit_block_size` divides the cell area by a utilization
    to get a DIE, while the placer measures utilization against the CORE,
    which is the die minus LibreLane's margins and rounded down to whole
    standard-cell rows.  On a small block the two differ enormously — a
    128 x 67 PE is 8576 um2 of die and 5090 of core."""
    c = harm.block_core(w, h)
    rows = int(math.floor((c[3] - c[1]) / pp.SITE_H + 1e-9))
    return max(0.0, (c[2] - c[0]) * rows * pp.SITE_H)


def clears_bar(cell, w, h):
    """(ok, utilization, core) against the bar `harm.py` predicts a refusal
    at: `PL_TARGET_DENSITY_PCT` with its own advisory margin.  A cell whose
    area is unknown clears by default — there is nothing to check it with."""
    area, _how = harm.cell_area_estimate(cell)
    core = usable_core(w, h)
    if area is None:
        return True, None, core
    if core <= 0:
        return False, math.inf, core
    util = 100.0 * area / core
    bar = harm.BLOCK_SETTINGS["PL_TARGET_DENSITY_PCT"]
    return util * harm.ADVICE_MARGIN <= bar, util, core


def best_aspect(cell, face_w, face_h, n, edgew, edgeh, hi=600):
    """The PE shape that minimises the ARRAY's die, subject to BOTH floors.

    `emit_block_size` shapes a block by its faces' ratio, which is right for
    one block and not for an array: the emitter spends `PPX = PEW + CHAN`
    once per COLUMN, so a wide PE costs N times its width and only once its
    height.  That is the free variable.  What is NOT free is the placer's
    bar — the block has to hold its own cells in its CORE — so the search
    is constrained by `clears_bar` rather than by nominal area, which is
    what the first cut got wrong (Codex #890: 128 x 67 is 117 % utilised
    and `harm.py` predicts GPL-0301 on it).

    Returns (w, h) or None when nothing inside `hi` clears."""
    best = None
    w0 = max(1, int(math.ceil(face_w)))
    h0 = max(1, int(math.ceil(face_h)))
    for w in range(w0, hi + 1):
        for h in range(h0, hi + 1):
            ok, _u, _c = clears_bar(cell, w, h)
            if not ok:
                continue
            dw, dh, _px, _py = die(n, w, h, edgew, edgeh)
            if best is None or dw * dh < best[0]:
                best = (dw * dh, w, h)
            break            # h is monotone in core: the first that fits is best
    return (best[1], best[2]) if best else None


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
        d = json.load(open(os.path.join(a.sizes, "pe_cell.json"))).get("derivation", {})
        fn = d.get("face_needs", {})
        got = best_aspect("pe_cell", fn.get("w", 0.0), fn.get("h", 0.0),
                          a.n, edgew, edgeh)
        if got is None:
            sys.exit("apply_sizes: no PE size within 600 um clears the "
                     "placer's bar — check harm.cell_area_estimate('pe_cell')")
        pew, peh = got
        aspect_note = (f"PE reshaped {rule_pew} x {rule_peh} -> {pew} x {peh} "
                       f"to minimise the ARRAY's die, subject to the placer's "
                       f"bar on its own core")
    if a.optimize_aspect:
        # The edge knob has to clear too, or the recipe still cannot harden:
        # grow it (keeping it one size for all three, as the emitter demands)
        # to the smallest that holds every edge cell's own core.
        grown = False
        while any(not clears_bar(c, edgew, edgeh)[0]
                  for c in EDGE_CELLS if c in sizes) and edgeh < 600:
            edgeh += 1
            grown = True
        if grown:
            aspect_note += (f"; edge grown to {edgew} x {edgeh} for the same "
                            f"reason")
    # BOTH paths are checked: a size the placer refuses is not a size, and
    # the rule's own die is measured against the DIE while the placer
    # measures the CORE (Codex #890).
    checks = []
    for cell, (cw, ch) in (("pe_cell", (pew, peh)),) + tuple(
            (c, (edgew, edgeh)) for c in EDGE_CELLS if c in sizes):
        ok, util, core = clears_bar(cell, cw, ch)
        checks.append((cell, cw, ch, ok, util, core))

    args = f"-PEW {pew} -PEH {peh} -EDGEW {edgew} -EDGEH {edgeh}"
    if a.args:
        print(args)
        return 0

    w, h, ppx, ppy = die(a.n, pew, peh, edgew, edgeh)
    result = {"n": a.n, "gen_args": args, "aspect_note": aspect_note,
              "checks": [{"cell": c, "w": w, "h": h, "clears": ok,
                          "utilization_pct": u, "core_um2": round(cr, 1)}
                         for c, w, h, ok, u, cr in checks],
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
    for cell, cw, ch, ok, util, core in checks:
        if util is None:
            continue
        verdict = "ok" if ok else "REFUSED by the placer"
        print(f"  {cell:<10} {cw} x {ch}: {core:.0f} um2 of core, "
              f"{util:.0f} % utilised -- {verdict}")
    bad = [c for c in checks if not c[3]]
    if bad:
        print(f"apply_sizes: WARNING: {len(bad)} cell(s) above the placer's "
              f"bar (PL_TARGET_DENSITY_PCT "
              f"{harm.BLOCK_SETTINGS['PL_TARGET_DENSITY_PCT']} with harm.py's "
              f"{harm.ADVICE_MARGIN}x margin) -- `--optimize-aspect` picks a "
              f"size that clears it, or give the cell more area")
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
