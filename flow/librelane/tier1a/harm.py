#!/usr/bin/env python3
"""The writer behind `harm.sh N`: from the emitted n<N>/ set (tpu_rtl.v,
tpu.def, tpu.lef) write n<N>/h/, the hierarchical arm WITHOUT BUDA -- arm H
of docs/internal/librelane_hier_flow.md §7.2.  `harm.sh` is the entry point;
this file is the logic, so the tests can also import its pieces.

    harm.py <n_dir> [--halo UM] [--out DIR]

What it writes, and the rule behind each piece:

  h/<cell>/src/<cell>.v, h/<cell>/config.json   one per LEAF CELL TYPE
      Every MACRO in tpu.lef is a leaf cell (pe_cell, feed_cell, wbuf_cell,
      acc_cell).  Its module text is CUT OUT of tpu_rtl.v into its own file
      rather than handing LibreLane the whole netlist (a block run must
      synthesize its own module and nothing else).  The config hardens it
      on a FIXED die exactly the cell's LEF SIZE (FP_SIZING absolute), so
      the hardened macro has the footprint the DEF placed; pins by
      LibreLane's own placer (no template -- that is the pin-DEF writer's
      job); CLOCK_PORT clk; the block-level settings of
      flow/librelane/phase0/reg32/config.json (PDN at pitch 30 / offset 5 /
      width 2 on met4+met5, RT_MAX_LAYER met4, density 50) plus gen.sh's
      signoff toggles so the arm's DRC column means what the flat arm's does.

  h/top/src/tpu_top_h.v, h/top/config.json
      tpu_rtl.v with the four leaf module bodies REMOVED entirely --
      row_cell and tpu_top stay soft and instantiate the leaves, which
      synthesis then reads as black boxes from each macro's hardened
      netlist (`MACROS.<cell>.nl`; LibreLane's VerilogStep adds every
      macro's NETLIST view to the blackbox list).  A leaf module left in
      the file as an empty shell would be synthesized and flattened away,
      so the whole module goes.  DESIGN_NAME tpu_top, FP_SIZING absolute
      with DIE_AREA = the DEF's DIEAREA, and one MACROS entry per leaf cell
      whose `instances` map EVERY COMPONENTS entry of tpu.def to its
      location/orientation, views at ../<cell>/runs/h/final/{gds,lef,nl,spef}.

      INSTANCE NAMES: the DEF (DIVIDERCHAR "/") writes the hierarchical
      path `row_0/pe_0`; the top LibreLane synthesizes is FLATTENED by
      Yosys (SYNTH_HIERARCHY_MODE flatten, the default), whose separator
      is a DOT, so OpenROAD's instance is `row_0.pe_0` (DEF-escaped in the
      written netlist, plain in the database, which is what
      ManualMacroPlacement matches after escape_verilog_name).  The rule
      is `name.replace('/', '.')`, pinned by the test; a top-level instance
      (`feed_0`) is unchanged.  ManualMacroPlacement exits 1 on a declared
      instance the netlist lacks, so a wrong rule fails at that step, not
      later.

      PLACEMENT SHIFT: the emitter places `feed_*` at x = X0 - EDGEW -
      EDGEGAP = -140, OUTSIDE the DEF's own DIEAREA; a macro outside the
      die cannot be floorplanned.  Rather than refuse the whole set, every
      instance is translated by ONE (dx, dy) = (max(0, halo - min x),
      max(0, halo - min y)) -- the smallest shift that puts every macro
      halo inside the die, the leftmost halo touching the die edge as the
      toy's does -- and the die is then checked to hold the shifted
      placement plus halos (else exit 1).  A DEF whose placement already
      fits is NOT moved (dx = dy = 0).  `placement.json` records both
      coordinates for every instance and the rule.

  h/top/pdn_plan.json, and the PDN_* values in top/config.json
      Chosen so that with the DEF's placement the array's regular pitch
      lands every macro at ONE phase against the top's straps -- the toy's
      `10 = 160 mod 30` rule generalized (§8 step 4):
      * PDN_VPITCH = PPX / k, PPX the pitch of the pe_cell columns (every
        instance's x must be congruent mod it, checked), the smallest k
        whose pitch is at most the PDK default (153.6) and admits a phase
        in which (a) no strap of one net lies within spacing of a
        PREDICTED block pin of the other net on met4, (b) some strap pair
        of the period crosses the block's met5 pins (that crossing is
        what connects the macro), and (c) every standard-cell row fragment
        the macro halos leave -- the channels between columns, the edge
        slivers -- is crossed by a full VPWR+VGND pair, which is the
        second way the toy failed (PSM-0069 on a sliver no strap reached).
        PDN_VOFFSET is the phase with the largest clearance.  With the
        emitter's defaults that is 100 = PPX/2: one pair over each macro
        between its own straps, one in each 48 um channel.
      * PDN_HPITCH = RPY / k, RPY the pe_cell row pitch, and PDN_HOFFSET
        the offset at which no met5 strap sits within spacing of any
        macro's predicted met5 pins (the rows are NOT all at one phase --
        wbuf, acc and the pipe rows sit at their own -- so the offset is
        searched over every instance; a macro needs no met5 crossing
        because the vertical straps feed it).
      The block pins are PREDICTED from the block config this same script
      writes (core margin 12 sites x 4 rows = 5.52 x 10.88, straps at
      core + 5 + 30k, width 2, spacing 1.7 -- the toy's measured 10.52 /
      14.22) and written as h/predicted_lef/<cell>.lef so `pdn_phase.py`
      can dry-run before any hardening; the REAL check is the same script
      on the hardened LEFs, after step 7a and before 7c of §8.

Everything derived is checked against the shape it expects and exit 1
names what did not match: a DEF component whose cell tpu.lef lacks, a leaf
module missing from tpu_rtl.v, an unplaced component, a polygon DIEAREA,
an orientation other than N (the emitter writes N; other orientations are
handled by the shared transform but were never run), instance x's not
congruent mod the chosen pitch, no feasible phase, a fragment no strap
reaches, a predicted-LEF dry run that fails its own check.
"""
import argparse
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdn_phase as pp  # noqa: E402

# flow/librelane/phase0/reg32/config.json, the block-level settings, verbatim
BLOCK_SETTINGS = {
    "FP_CORE_UTIL": 45,
    "PL_TARGET_DENSITY_PCT": 50,
    "RT_MAX_LAYER": "met4",
    "PDN_VOFFSET": 5,
    "PDN_HOFFSET": 5,
    "PDN_VWIDTH": 2,
    "PDN_HWIDTH": 2,
    "PDN_VPITCH": 30,
    "PDN_HPITCH": 30,
    "PDN_SKIPTRIM": True,
}
BLOCK_SPACING = 1.7          # PDN_VSPACING/HSPACING the block inherits (sky130A)
# gen.sh's signoff toggles: the H arm's DRC column must mean what F's does
SIGNOFF = {"GRT_ALLOW_CONGESTION": False, "RUN_KLAYOUT_XOR": False,
           "RUN_MAGIC_DRC": False, "RUN_KLAYOUT_DRC": True}
CLOCK_PERIOD = 20            # gen.sh's flat arm; the H arm is compared against it
TOP_DENSITY = 35             # the two_reg32 toy: a top whose cells are CTS buffers and taps
TOP_STRAP_W, TOP_STRAP_SP = pp.SKY130["PDN_VWIDTH"], pp.SKY130["PDN_VSPACING"]
PDK_VPITCH, PDK_HPITCH = pp.SKY130["PDN_VPITCH"], pp.SKY130["PDN_HPITCH"]
MIN_PITCH = 10.0
# ROUGH cell areas (um^2) fitted to §7.1's Yosys totals at N=2/4/8 -- advisory
# only, for the utilization warning; the real number is the block's stat.json.
ROUGH_AREA = {"pe_cell": 3900.0, "acc_cell": 750.0, "feed_cell": 250.0, "wbuf_cell": 250.0}
ORIENTS_RUN = ("N",)


class Shape(Exception):
    """An input of a shape the writer did not expect (exit 1, named)."""


# ── readers ───────────────────────────────────────────────────────────────
def read_def(path):
    """design, divider, dbu, die (um), components [{name, cell, x, y, orient}] --
    the subset the emitter writes; every deviation from it is refused."""
    txt = open(path).read()
    m = re.search(r'DIVIDERCHAR\s+"(.)"', txt)
    divider = m.group(1) if m else "/"
    m = re.search(r"UNITS\s+DISTANCE\s+MICRONS\s+(\d+)", txt)
    if not m:
        raise Shape(f"{path}: no UNITS DISTANCE MICRONS")
    dbu = int(m.group(1))
    m = re.search(r"DESIGN\s+(\S+)\s*;", txt)
    design = m.group(1) if m else None
    m = re.search(r"DIEAREA\s+((?:\(\s*-?\d+\s+-?\d+\s*\)\s*)+);", txt)
    if not m:
        raise Shape(f"{path}: no DIEAREA")
    pts = re.findall(r"\(\s*(-?\d+)\s+(-?\d+)\s*\)", m.group(1))
    if len(pts) != 2:
        raise Shape(f"{path}: DIEAREA with {len(pts)} points -- a rectangle (2 points) was expected")
    (x0, y0), (x1, y1) = ((int(a) / dbu, int(b) / dbu) for a, b in pts)
    die = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    m = re.search(r"COMPONENTS\s+(\d+)\s*;(.*?)END\s+COMPONENTS", txt, re.S)
    if not m:
        raise Shape(f"{path}: no COMPONENTS section")
    declared = int(m.group(1))
    comps = []
    for entry in re.findall(r"-\s+(.*?);", m.group(2), re.S):
        t = entry.split()
        if len(t) < 2:
            raise Shape(f"{path}: malformed component entry: {entry!r}")
        name, cell = t[0], t[1]
        pm = re.search(r"\+\s+(PLACED|FIXED|COVER)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\S+)", entry)
        if not pm:
            raise Shape(f"{path}: component {name} is not PLACED/FIXED with a point and orientation -- an "
                        f"unplaced macro has nowhere to go: {entry!r}")
        comps.append({"name": name, "cell": cell, "x": int(pm.group(2)) / dbu, "y": int(pm.group(3)) / dbu,
                      "orient": pm.group(4)})
    if len(comps) != declared:
        raise Shape(f"{path}: COMPONENTS declares {declared}, {len(comps)} read")
    return {"design": design, "divider": divider, "dbu": dbu, "die": die, "comps": comps}


def split_modules(rtl):
    """{module: text} for every `module NAME` ... `endmodule` at line start,
    and the list of (start, end) line spans in file order."""
    lines = rtl.splitlines(keepends=True)
    mods, spans, cur, start = {}, [], None, None
    for i, ln in enumerate(lines):
        s = ln.strip()
        m = re.match(r"module\s+([A-Za-z_][\w$]*)", s)
        if m and cur is None:
            cur, start = m.group(1), i
        elif s == "endmodule" and cur is not None:
            if cur in mods:
                raise Shape(f"module {cur} defined twice")
            mods[cur] = "".join(lines[start:i + 1])
            spans.append((cur, start, i + 1))
            cur = None
    if cur is not None:
        raise Shape(f"module {cur} has no endmodule")
    return mods, spans, lines


# ── geometry ──────────────────────────────────────────────────────────────
def gcd_um(values):
    """gcd of a list of lengths in um, at 0.001 resolution."""
    g = 0
    for v in values:
        g = math.gcd(g, int(round(v * 1000)))
    return g / 1000.0


def grid_pitch(insts, cell, axis):
    xs = sorted({round(i[axis], 3) for i in insts if i["cell"] == cell})
    if len(xs) < 2:
        raise Shape(f"{cell}: only {len(xs)} distinct {axis} -- no array pitch to derive the PDN from "
                    f"(N >= 2 has two columns and two rows)")
    return gcd_um([b - a for a, b in zip(xs, xs[1:])])


def block_core(w, h):
    c = pp.SKY130
    return [c["LEFT_MARGIN_MULT"] * pp.SITE_W, c["BOTTOM_MARGIN_MULT"] * pp.SITE_H,
            w - c["RIGHT_MARGIN_MULT"] * pp.SITE_W, h - c["TOP_MARGIN_MULT"] * pp.SITE_H]


def predicted_pins(w, h):
    """The block's own PDN straps as they will come back as VPWR/VGND pins
    of its LEF (block-local rects), from BLOCK_SETTINGS: met4 straps run the
    core height at x = 5.52 + 5 + 30k (VGND 3.7 after), met5 straps run the
    ROW extent at y = 10.88 + 5 + 30k -- the toy's 10.52/14.22 and its met5
    pin ending at 74.06 = 5.52 + 149 sites."""
    c = block_core(w, h)
    rows_hi = c[0] + math.floor((c[2] - c[0]) / pp.SITE_W + 1e-9) * pp.SITE_W
    B = BLOCK_SETTINGS
    pins = {"VPWR": [], "VGND": []}
    for k, cx in pp.strap_centres(c[0], c[2], B["PDN_VOFFSET"], B["PDN_VPITCH"]):
        for net, cc in (("VPWR", cx), ("VGND", cx + B["PDN_VWIDTH"] + BLOCK_SPACING)):
            pins[net].append(("met4", cc - B["PDN_VWIDTH"] / 2, c[1], cc + B["PDN_VWIDTH"] / 2, c[3]))
    for k, cy in pp.strap_centres(c[1], c[3], B["PDN_HOFFSET"], B["PDN_HPITCH"]):
        for net, cc in (("VPWR", cy), ("VGND", cy + B["PDN_HWIDTH"] + BLOCK_SPACING)):
            pins[net].append(("met5", c[0], cc - B["PDN_HWIDTH"] / 2, rows_hi, cc + B["PDN_HWIDTH"] / 2))
    return pins, (c[0], rows_hi)


def write_predicted_lef(path, cell, w, h, pins):
    L = [f"# {cell}.lef -- PREDICTED power pins (flow/librelane/tier1a/harm.py), for a pdn_phase.py dry run;",
         "# the hardened block's final LEF is the real one.  Do not hand this to LibreLane.",
         "VERSION 5.8 ;", 'BUSBITCHARS "[]" ;', 'DIVIDERCHAR "/" ;', "UNITS", "  DATABASE MICRONS 1000 ;",
         "END UNITS", "", f"MACRO {cell}", "  CLASS BLOCK ;", "  ORIGIN 0 0 ;", f"  SIZE {w:g} BY {h:g} ;"]
    for net, use in (("VPWR", "POWER"), ("VGND", "GROUND")):
        L += [f"  PIN {net}", "    DIRECTION INOUT ;", f"    USE {use} ;", "    PORT"]
        for (layer, x1, y1, x2, y2) in pins[net]:
            L += [f"      LAYER {layer} ;", f"      RECT {x1:.3f} {y1:.3f} {x2:.3f} {y2:.3f} ;"]
        L += ["    END", f"  END {net}"]
    L += [f"END {cell}", "", "END LIBRARY", ""]
    with open(path, "w") as f:
        f.write("\n".join(L))


def row_fragments(insts, sizes, core, halo):
    """The standard-cell row fragments the macro halos leave: for each
    elementary y-band of the core, the x-intervals of [core] minus the
    union of the macro-plus-halo footprints reaching that band.  Returns
    [(y_lo, y_hi, x_lo, x_hi)] with width >= 0 (the caller sorts slivers)."""
    hx, hy = halo
    boxes = []
    for i in insts:
        w, h = pp.placed_size(i["orient"], *sizes[i["cell"]])
        boxes.append((i["x"] - hx, i["y"] - hy, i["x"] + w + hx, i["y"] + h + hy))
    ys = {core[1], core[3]}
    for b in boxes:
        for v in (b[1], b[3]):
            if core[1] < v < core[3]:
                ys.add(v)
    ys = sorted(ys)
    frags = []
    for ylo, yhi in zip(ys, ys[1:]):
        if yhi - ylo <= 1e-9:
            continue
        blocked = sorted((max(core[0], b[0]), min(core[2], b[2])) for b in boxes
                         if b[1] < yhi - 1e-9 and b[3] > ylo + 1e-9 and b[2] > core[0] and b[0] < core[2])
        x = core[0]
        for lo, hi in blocked:
            if lo > x:
                frags.append((ylo, yhi, x, lo))
            x = max(x, hi)
        if core[2] > x:
            frags.append((ylo, yhi, x, core[2]))
    return frags


def pair_fits(frag_lo, frag_hi, centres):
    """Some VPWR strap centre X in `centres` has its full pair
    [X - w/2, X + w + sp + w/2] inside [frag_lo, frag_hi]."""
    lo_off, hi_off = TOP_STRAP_W / 2, TOP_STRAP_W + TOP_STRAP_SP + TOP_STRAP_W / 2
    return any(X - lo_off >= frag_lo - 1e-9 and X + hi_off <= frag_hi + 1e-9 for X in centres)


def gap(a_lo, a_hi, b_lo, b_hi):
    return max(b_lo - a_hi, a_lo - b_hi)


def vertical_clearance(voffset, pitch, cells, refs, core_x0):
    """(min clearance, connected-for-every-cell) of vertical straps whose VPWR
    centres sit at x = core_x0 + voffset + k*pitch, seen by each cell type at
    its own block-local phase (core_x0 + voffset - refs[cell]) mod pitch:
    clearance against the cell's predicted met4 pins of the other net
    (spacing counted), connection = some pair inside its met5 pin x-span."""
    sp4 = pp.MIN_SPACING["met4"]
    w2, gnd_off = TOP_STRAP_W / 2, TOP_STRAP_W + TOP_STRAP_SP
    clearance = math.inf
    for cell, (w, h, pins, rows) in cells.items():
        phase = (core_x0 + voffset - refs[cell]) % pitch
        connected = False
        j = math.floor((-pp.SKY130["FP_MACRO_HORIZONTAL_HALO"] - phase) / pitch) - 1
        while True:
            X = phase + j * pitch
            j += 1
            if X < -w2 - gnd_off - 1:
                continue
            if X - w2 > w + 1:
                break
            straps = (("VPWR", X - w2, X + w2), ("VGND", X + gnd_off - w2, X + gnd_off + w2))
            for net, slo, shi in straps:
                other = "VGND" if net == "VPWR" else "VPWR"
                for (layer, x1, y1, x2, y2) in pins[other]:
                    if layer == "met4":
                        clearance = min(clearance, gap(slo, shi, x1, x2) - sp4)
            if X - w2 >= rows[0] - 1e-9 and X + gnd_off + w2 <= rows[1] + 1e-9:
                connected = True
        if not connected:
            return -math.inf, False
    return clearance, True


def cell_phase_refs(insts, pitch):
    """{cell: smallest x of its instances}; every instance of a cell must sit
    at that cell's phase mod `pitch` (a cell hardened once has ONE pin
    pattern, so two phases would need two checks and one of them fails)."""
    refs, bad = {}, []
    for i in insts:
        refs.setdefault(i["cell"], i["x"])
        refs[i["cell"]] = min(refs[i["cell"]], i["x"])
    for i in insts:
        r = (i["x"] - refs[i["cell"]]) % pitch
        if min(r, pitch - r) > 1e-3:
            bad.append(f"{i['name']} ({i['cell']} at x={i['x']}, {r:.3f} off its cell's phase)")
    return refs, bad


def choose_vertical(insts, sizes, cells, core, halo, ppx):
    """PDN_VPITCH/VOFFSET by the rule in the module docstring.  Returns the
    plan dict; raises Shape when no pitch/offset satisfies the three tests."""
    frags = row_fragments(insts, sizes, core, halo)
    rows = [f for f in frags if f[3] - f[2] >= pp.SITE_W - 1e-9]
    slivers = [f for f in frags if f[3] - f[2] < pp.SITE_W - 1e-9]
    tried = []
    for pass_ in ("<= PDK default", "any"):
        k = 1
        while ppx / k >= MIN_PITCH:
            pitch = round(ppx / k, 3)
            k += 1
            if pass_ == "<= PDK default" and pitch > PDK_VPITCH + 1e-9:
                tried.append((pitch, "skipped: above the PDK default %g" % PDK_VPITCH))
                continue
            if abs(pitch * (k - 1) - ppx) > 1e-6:
                continue  # not an exact divisor at 0.001
            refs, bad = cell_phase_refs(insts, pitch)
            if bad:
                raise Shape(f"instances of one cell at two x phases mod PDN_VPITCH {pitch}: " + "; ".join(bad[:6]))
            phases = {c: round((core[0] - refs[c]) % pitch, 3) for c in refs}

            def score(voff):
                cl, ok = vertical_clearance(voff, pitch, cells, refs, core[0])
                if not ok or cl < 0:
                    return None
                centres = [c for _, c in pp.strap_centres(core[0], core[2], voff, pitch)]
                if not all(pair_fits(f[2], f[3], centres) for f in rows):
                    return None
                return cl

            best, step = None, 0.05
            for i in range(int(round(pitch / step))):
                s_ = score(i * step)
                if s_ is not None and (best is None or s_ > best[0]):
                    best = (s_, i * step)
            if best is not None:  # refine around the coarse best
                for v in [best[1] + i * pp.GRID for i in range(-10, 11)]:
                    s_ = score(v % pitch)
                    if s_ is not None and s_ > best[0]:
                        best = (s_, v % pitch)
            if best is None:
                tried.append((pitch, "no offset clears the predicted met4 pins of every cell, connects every cell "
                                     "AND puts a strap pair in every row fragment"))
                continue
            voff = round(round(best[1] / pp.GRID) * pp.GRID, 3) % pitch
            cl, _ = vertical_clearance(voff, pitch, cells, refs, core[0])
            return {"PDN_VPITCH": pitch, "PDN_VOFFSET": voff, "k": k - 1, "ppx": ppx,
                    "cell_x_ref": refs, "cell_phase_at_offset_0": phases,
                    "phase_block_local": {c: round((core[0] + voff - refs[c]) % pitch, 3) for c in refs},
                    "clearance_met4": round(cl, 3), "tried": tried, "row_fragments": len(rows),
                    "sub_site_slivers": [[round(v, 3) for v in f] for f in slivers],
                    "rule": pass_}
    raise Shape("no PDN_VPITCH = PPX/k admits an offset: " + "; ".join(f"{p}: {why}" for p, why in tried))


def horizontal_clearance(offset, pitch, insts, cells, core):
    """min clearance of met5 straps at y = core_y0 + offset + k*pitch against
    every instance's predicted met5 pins (spacing counted); also whether
    every instance's met4 pins get crossed too (a bonus, reported)."""
    sp5 = pp.MIN_SPACING["met5"]
    w2, gnd_off = TOP_STRAP_W / 2, TOP_STRAP_W + TOP_STRAP_SP
    clearance = math.inf
    crossed_all = True
    centres = [c for _, c in pp.strap_centres(core[1], core[3], offset, pitch)]
    for i in insts:
        w, h, pins, _ = cells[i["cell"]]
        crossed = False
        for Y in centres:
            y = Y - i["y"]
            if y + gnd_off + w2 < -1 or y - w2 > h + 1:
                continue
            for net, slo, shi in (("VPWR", y - w2, y + w2), ("VGND", y + gnd_off - w2, y + gnd_off + w2)):
                other = "VGND" if net == "VPWR" else "VPWR"
                for (layer, x1, y1, x2, y2) in pins[other]:
                    if layer == "met5":
                        clearance = min(clearance, gap(slo, shi, y1, y2) - sp5)
                for (layer, x1, y1, x2, y2) in pins[net]:
                    if layer == "met4" and min(shi, y2) - max(slo, y1) >= pp.VIA_MIN - 1e-9:
                        crossed = True
        crossed_all = crossed_all and crossed
    return clearance, crossed_all


def choose_horizontal(insts, cells, core, rpy):
    tried = []
    for pass_ in ("<= PDK default", "any"):
        k = 1
        while rpy / k >= MIN_PITCH:
            pitch = round(rpy / k, 3)
            k += 1
            if pass_ == "<= PDK default" and pitch > PDK_HPITCH + 1e-9:
                tried.append((pitch, "skipped: above the PDK default %g" % PDK_HPITCH))
                continue
            if abs(pitch * (k - 1) - rpy) > 1e-6:
                continue
            best, step = None, 0.05
            for i in range(int(round(pitch / step))):
                off = i * step
                cl, _ = horizontal_clearance(off, pitch, insts, cells, core)
                if cl >= 0 and (best is None or cl > best[0]):
                    best = (cl, off)
            if best is None:
                tried.append((pitch, "every offset puts a met5 strap within spacing of some macro's predicted met5 pins"))
                continue
            for off in [best[1] + i * pp.GRID for i in range(-10, 11)]:
                cl, _ = horizontal_clearance(off % pitch, pitch, insts, cells, core)
                if cl >= 0 and cl > best[0]:
                    best = (cl, off % pitch)
            hoff = round(round(best[1] / pp.GRID) * pp.GRID, 3)
            cl, crossed = horizontal_clearance(hoff, pitch, insts, cells, core)
            # inf = no met5 strap comes near any macro's met5 pins at all (the
            # straps run in the channels between macro rows): nothing to clear
            return {"PDN_HPITCH": pitch, "PDN_HOFFSET": hoff, "k": k - 1, "rpy": rpy,
                    "clearance_met5": None if cl == math.inf else round(cl, 3),
                    "met5_straps_over_macros": cl != math.inf,
                    "met4_pins_also_crossed_everywhere": crossed,
                    "tried": tried, "rule": pass_}
    raise Shape("no PDN_HPITCH = RPY/k admits an offset: " + "; ".join(f"{p}: {why}" for p, why in tried))


# ── the writer ────────────────────────────────────────────────────────────
def views(cell):
    base = f"dir::../{cell}/runs/h/final"
    return {"gds": [f"{base}/gds/{cell}.gds"], "lef": [f"{base}/lef/{cell}.lef"],
            "nl": [f"{base}/nl/{cell}.nl.v"],
            "spef": {f"{c}_*": f"{base}/spef/{c}/{cell}.{c}.spef" for c in ("nom", "min", "max")}}


def top_instance_name(def_name, divider):
    return def_name.replace(divider, ".")


def utilization_advice(cell, w, h):
    c = block_core(w, h)
    rows = int(math.floor((c[3] - c[1]) / pp.SITE_H + 1e-9))
    core_area = (c[2] - c[0]) * rows * pp.SITE_H
    area = ROUGH_AREA.get(cell)
    if area is None:
        return f"{cell}: {w:g} x {h:g}, {rows} rows, {core_area:.0f} um^2 of core"
    util = 100.0 * area / core_area if core_area > 0 else math.inf
    line = (f"{cell}: {w:g} x {h:g}, {rows} rows = {core_area:.0f} um^2 of core; ~{area:.0f} um^2 of cells "
            f"(ROUGH, from §7.1's totals) = ~{util:.0f} % utilization")
    if util > BLOCK_SETTINGS["PL_TARGET_DENSITY_PCT"]:
        pad = 0
        while pad < 400:
            pad += 8
            c2 = block_core(w + pad, h + pad)
            r2 = int(math.floor((c2[3] - c2[1]) / pp.SITE_H + 1e-9))
            if 100.0 * area / ((c2[2] - c2[0]) * r2 * pp.SITE_H) <= BLOCK_SETTINGS["PL_TARGET_DENSITY_PCT"] - 10:
                break
        line += (f" -- ABOVE PL_TARGET_DENSITY_PCT {BLOCK_SETTINGS['PL_TARGET_DENSITY_PCT']}: expect "
                 f"OpenROAD.GlobalPlacement to refuse; regenerate the set with `gen.sh N -PEPAD "
                 f"{24 + pad}` (the emitter pads PEW and PEH by PEPAD, default 24; DEF and LEF "
                 f"scale together and harm.sh reads whatever it emitted)")
    return line


def write_h(n_dir, out_dir, halo):
    n_dir = os.path.abspath(n_dir)
    out_dir = os.path.abspath(out_dir)
    for f in ("tpu_rtl.v", "tpu.def", "tpu.lef"):
        if not os.path.exists(os.path.join(n_dir, f)):
            raise Shape(f"{n_dir}/{f} missing -- run gen.sh N first (it emits the set)")
    D = read_def(os.path.join(n_dir, "tpu.def"))
    L = pp.read_lef(os.path.join(n_dir, "tpu.lef"))
    rtl = open(os.path.join(n_dir, "tpu_rtl.v")).read()
    mods, spans, lines = split_modules(rtl)
    if D["design"] != "tpu_top":
        raise Shape(f"tpu.def: DESIGN {D['design']}, expected tpu_top")
    leaf_cells = list(L)  # every MACRO in tpu.lef is a leaf cell type
    for cell, m in L.items():
        if m["origin"] != (0.0, 0.0):
            raise Shape(f"tpu.lef: MACRO {cell} ORIGIN {m['origin']} -- DIE_AREA assumes an origin at 0 0")
        if cell not in mods:
            raise Shape(f"tpu_rtl.v defines no module {cell}, which tpu.lef declares as a MACRO")
    for c in D["comps"]:
        if c["cell"] not in L:
            raise Shape(f"tpu.def: component {c['name']} is a {c['cell']}, which tpu.lef does not declare")
        if c["orient"] not in ORIENTS_RUN:
            raise Shape(f"tpu.def: {c['name']} orientation {c['orient']}: only {ORIENTS_RUN} have been run "
                        f"through this flow (the transform exists in pdn_phase.py; remove this guard to try)")
    used = {c["cell"] for c in D["comps"]}
    for cell in leaf_cells:
        if cell not in used:
            raise Shape(f"tpu.lef declares MACRO {cell} but tpu.def places no instance of it")
    for keep in ("row_cell", "tpu_top"):
        if keep not in mods:
            raise Shape(f"tpu_rtl.v has no module {keep}")
    sizes = {cell: L[cell]["size"] for cell in leaf_cells}
    hx, hy = halo

    # ── the placement shift (see the docstring) ──
    xs = [c["x"] for c in D["comps"]]
    ys = [c["y"] for c in D["comps"]]
    dx = max(0.0, hx - min(xs) - D["die"][0])
    dy = max(0.0, hy - min(ys) - D["die"][1])
    dx, dy = round(dx, 3), round(dy, 3)
    insts = []
    for c in D["comps"]:
        w, h = pp.placed_size(c["orient"], *sizes[c["cell"]])
        x, y = round(c["x"] + dx, 3), round(c["y"] + dy, 3)
        if x + w + hx > D["die"][2] + 1e-9 or y + h + hy > D["die"][3] + 1e-9 or x - hx < D["die"][0] - 1e-9 \
                or y - hy < D["die"][1] - 1e-9:
            raise Shape(f"tpu.def: {c['name']} at ({x},{y}) + halo {hx}/{hy} does not fit the DEF's DIEAREA "
                        f"{D['die']} even after the ({dx},{dy}) shift -- the die is too small for its own placement")
        insts.append({"def_name": c["name"], "name": top_instance_name(c["name"], D["divider"]), "cell": c["cell"],
                      "def_location": [c["x"], c["y"]], "x": x, "y": y, "orient": c["orient"], "size": [w, h]})
    names = [i["name"] for i in insts]
    if len(set(names)) != len(names):
        raise Shape("instance-name mapping collides: " + ", ".join(sorted({n for n in names if names.count(n) > 1})))

    # ── the PDN plan ──
    top_cfg_stub = {"die": D["die"]}
    core = [D["die"][0] + pp.SKY130["LEFT_MARGIN_MULT"] * pp.SITE_W, D["die"][1] + pp.SKY130["BOTTOM_MARGIN_MULT"] * pp.SITE_H,
            D["die"][2] - pp.SKY130["RIGHT_MARGIN_MULT"] * pp.SITE_W, D["die"][3] - pp.SKY130["TOP_MARGIN_MULT"] * pp.SITE_H]
    cells = {}
    for cell in leaf_cells:
        w, h = sizes[cell]
        pins, rows = predicted_pins(w, h)
        cells[cell] = (w, h, pins, rows)
    # the ARRAY cell sets the pitches: the emitter's pe_cell, else the most
    # numerous cell (at N=2 the six acc_cell/pipe instances outnumber four PEs
    # and their 104 um pipe spacing is not the row pitch)
    array_cell = "pe_cell" if "pe_cell" in leaf_cells else \
        max(leaf_cells, key=lambda c: sum(1 for i in insts if i["cell"] == c))
    ppx = grid_pitch(insts, array_cell, "x")
    rpy = grid_pitch(insts, array_cell, "y")
    vplan = choose_vertical(insts, sizes, cells, core, halo, ppx)
    hplan = choose_horizontal(insts, cells, core, rpy)

    # ── write ──
    os.makedirs(out_dir, exist_ok=True)
    pred_dir = os.path.join(out_dir, "predicted_lef")
    os.makedirs(pred_dir, exist_ok=True)
    advice = []
    for cell in leaf_cells:
        w, h = sizes[cell]
        d = os.path.join(out_dir, cell)
        os.makedirs(os.path.join(d, "src"), exist_ok=True)
        with open(os.path.join(d, "src", f"{cell}.v"), "w") as f:
            f.write(f"// {cell}.v -- module {cell} of tpu_rtl.v, extracted by flow/librelane/tier1a/harm.sh; "
                    f"do not edit.\n`default_nettype none\n\n{mods[cell]}\n`default_nettype wire\n")
        cfg = {"meta": {"version": 2}, "DESIGN_NAME": cell, "VERILOG_FILES": [f"dir::src/{cell}.v"],
               "CLOCK_PORT": "clk", "CLOCK_PERIOD": CLOCK_PERIOD,
               "FP_SIZING": "absolute", "DIE_AREA": [0, 0, w, h]}
        cfg.update(BLOCK_SETTINGS)
        cfg.update(SIGNOFF)
        with open(os.path.join(d, "config.json"), "w") as f:
            json.dump(cfg, f, indent=4)
            f.write("\n")
        write_predicted_lef(os.path.join(pred_dir, f"{cell}.lef"), cell, w, h, cells[cell][2])
        advice.append(utilization_advice(cell, w, h))

    top = os.path.join(out_dir, "top")
    os.makedirs(os.path.join(top, "src"), exist_ok=True)
    drop = {cell for cell in leaf_cells}
    kept, removed = [], []
    i = 0
    span_at = {s: (name, e) for name, s, e in spans}
    while i < len(lines):
        if i in span_at and span_at[i][0] in drop:
            removed.append(span_at[i][0])
            i = span_at[i][1]
            # swallow one blank line that followed the module
            if i < len(lines) and lines[i].strip() == "":
                i += 1
            continue
        kept.append(lines[i])
        i += 1
    header = (f"// tpu_top_h.v -- tpu_rtl.v with the leaf module bodies ({', '.join(removed)}) removed by "
              f"flow/librelane/tier1a/harm.sh; each is a hardened macro (MACROS in config.json) whose netlist "
              f"synthesis reads as a black box.  Do not edit.\n")
    top_v = header + "".join(kept)
    for cell in leaf_cells:
        if re.search(rf"^\s*module\s+{re.escape(cell)}\b", top_v, re.M):
            raise Shape(f"tpu_top_h.v still defines module {cell}")
        if not re.search(rf"^\s*{re.escape(cell)}\s+\w+\s*\(", top_v, re.M):
            raise Shape(f"tpu_top_h.v no longer instantiates {cell}")
    with open(os.path.join(top, "src", "tpu_top_h.v"), "w") as f:
        f.write(top_v)

    macros = {}
    for cell in leaf_cells:
        inst_map = {}
        for i_ in insts:
            if i_["cell"] == cell:
                inst_map[i_["name"]] = {"location": [i_["x"], i_["y"]], "orientation": i_["orient"]}
        macros[cell] = {"instances": inst_map, **views(cell)}
    tcfg = {"meta": {"version": 2}, "DESIGN_NAME": "tpu_top", "VERILOG_FILES": ["dir::src/tpu_top_h.v"],
            "CLOCK_PORT": "clk", "CLOCK_PERIOD": CLOCK_PERIOD,
            "FP_SIZING": "absolute", "DIE_AREA": D["die"],
            "FP_CORE_UTIL": TOP_DENSITY, "PL_TARGET_DENSITY_PCT": TOP_DENSITY,
            "FP_MACRO_HORIZONTAL_HALO": hx, "FP_MACRO_VERTICAL_HALO": hy,
            "PDN_VPITCH": vplan["PDN_VPITCH"], "PDN_VOFFSET": vplan["PDN_VOFFSET"],
            "PDN_HPITCH": hplan["PDN_HPITCH"], "PDN_HOFFSET": hplan["PDN_HOFFSET"],
            "PDN_VWIDTH": TOP_STRAP_W, "PDN_HWIDTH": TOP_STRAP_W,
            "PDN_VSPACING": TOP_STRAP_SP, "PDN_HSPACING": TOP_STRAP_SP,
            "RT_MAX_LAYER": "met5"}
    tcfg.update(SIGNOFF)
    tcfg["MACROS"] = macros
    with open(os.path.join(top, "config.json"), "w") as f:
        json.dump(tcfg, f, indent=4)
        f.write("\n")
    with open(os.path.join(top, "placement.json"), "w") as f:
        json.dump({"design": "tpu_top", "def": os.path.relpath(os.path.join(n_dir, "tpu.def"), top),
                   "dbu": D["dbu"], "die_um": D["die"], "divider": D["divider"],
                   "instance_name_rule": "DEF name with the DIVIDERCHAR replaced by '.' (Yosys flatten)",
                   "shift_um": [dx, dy],
                   "shift_rule": "(max(0, halo_x - min x), max(0, halo_y - min y)): the smallest translation "
                                 "putting every macro halo inside the die; 0 when the DEF's placement already fits",
                   "halo_um": [hx, hy], "instances": insts}, f, indent=1)
        f.write("\n")
    plan = {"array_cell": array_cell, "vertical": vplan, "horizontal": hplan,
            "core_um": [round(v, 3) for v in core],
            "block_pdn": BLOCK_SETTINGS, "block_spacing": BLOCK_SPACING,
            "top_strap": {"width": TOP_STRAP_W, "spacing": TOP_STRAP_SP},
            "predicted_pins": {cell: cells[cell][2] for cell in leaf_cells}}
    with open(os.path.join(top, "pdn_plan.json"), "w") as f:
        json.dump(plan, f, indent=1)
        f.write("\n")

    # ── the dry run: the writer's own prediction must pass its own check ──
    topcfg = pp.read_top_config(os.path.join(top, "config.json"))
    lefs = {}
    for cell in leaf_cells:
        lefs.update(pp.read_lef(os.path.join(pred_dir, f"{cell}.lef")))
    res = pp.run_check(topcfg, lefs)
    if not res["pass"]:
        pp.report(topcfg, lefs, res, sys.stderr)
        raise Shape("the PDN plan fails pdn_phase.py on the PREDICTED pins -- the writer contradicts its checker")

    counts = {cell: sum(1 for i_ in insts if i_["cell"] == cell) for cell in leaf_cells}
    readme = render_readme(n_dir, out_dir, leaf_cells, counts, sizes, D, dx, dy, vplan, hplan, advice)
    with open(os.path.join(out_dir, "README.md"), "w") as f:
        f.write(readme)
    return {"out": out_dir, "cells": leaf_cells, "counts": counts, "shift": [dx, dy], "vplan": vplan, "hplan": hplan,
            "advice": advice, "instances": len(insts)}


def render_readme(n_dir, out_dir, cells, counts, sizes, D, dx, dy, vplan, hplan, advice):
    blocks = " ".join(f"--block ../{c}/runs/h:{counts[c]}" for c in cells)
    harden = "\n".join(f"(cd {c} && librelane --dockerized --run-tag h config.json > h.log 2>&1) &" for c in cells)
    lefs = " ".join(f"../{c}/runs/h/final/lef/{c}.lef" for c in cells)
    plef = " ".join(f"predicted_lef/{c}.lef" for c in cells)
    return f"""# Arm H of tier 1a at this N -- written by `flow/librelane/tier1a/harm.sh`, do not edit

From `{os.path.relpath(n_dir, out_dir)}/` (tpu_rtl.v + tpu.def + tpu.lef).  Blocks: {', '.join(f'{c} x{counts[c]} ({sizes[c][0]:g} x {sizes[c][1]:g})' for c in cells)};
top die {D['die'][2]:g} x {D['die'][3]:g} um, {sum(counts.values())} macro instances, placement shifted by ({dx}, {dy}) um from the DEF
(see top/placement.json for the rule and every instance's both coordinates).
PDN: met4 straps at pitch {vplan['PDN_VPITCH']} = PPX {vplan['ppx']} / {vplan['k']}, offset {vplan['PDN_VOFFSET']} (block-local phase per cell
{vplan['phase_block_local']}, {vplan['clearance_met4']} um clear of the predicted met4 pins, a pair in every one of the {vplan['row_fragments']} row
fragments); met5 straps at pitch {hplan['PDN_HPITCH']} = RPY {hplan['rpy']} / {hplan['k']}, offset {hplan['PDN_HOFFSET']} ({('%s um clear of the predicted met5 pins' % hplan['clearance_met5']) if hplan['met5_straps_over_macros'] else 'no met5 strap over any macro: they run between the macro rows'}).  The reasoning is in top/pdn_plan.json and harm.py's docstring.

Utilization (rough): {'; '.join(advice)}

## 0. Dry-run the PDN check on the PREDICTED pins (no tools needed)

    python3 ../../pdn_phase.py top/config.json {plef}

Pass: `PASS: {sum(counts.values())} instances, ...`.  This only proves the plan agrees with its own prediction.

## 1. Harden the {len(cells)} cells -- independent, so in parallel; record wall AND cpu (§7.3)

    date +%s > blocks.start
{harden}
    wait; date +%s > blocks.end

Pass, per cell: `Flow complete` in `<cell>/h.log`, and `<cell>/runs/h/final/{{gds,lef,nl,spef/nom}}` present
(the paths top/config.json names).  Wall = blocks.end - blocks.start; the cpu-sum comes from runtimes.py below.
If `OpenROAD.GlobalPlacement` refuses on utilization, the die (the emitter's LEF SIZE) is too small for the
RTL: regenerate the whole set with a larger `-PEPAD` (see the utilization line above) and rerun harm.sh.

## 2. The PDN-phase check on the HARDENED pins -- before the top

    python3 ../../pdn_phase.py top/config.json {lefs}

Pass: `PASS: ...`, exit 0.  A COLLISION or UNCONNECTED line names the instance, pin and strap and the smallest
shift that clears it (and the PDN_VOFFSET/PDN_HOFFSET that is equivalent for all macros at once); fix the
offsets in top/config.json and rerun the check, never the top blind (§8 step 4: signoff is the first tool step
that notices, and it is the last step).

## 3. The top

    (cd top && librelane --dockerized --run-tag h config.json)

Pass: `Odb.ManualMacroPlacement` prints `Successfully placed {sum(counts.values())} instances` (a declared instance the
flattened netlist does not have exits 1 there -- that is the `row_0/pe_0` to `row_0.pe_0` name rule failing),
`Flow complete`, `All shapes on net VPWR are connected` (and VGND) from the IR-drop report.

## 4. The row for the table (§7.3: top plus every block, wire per PLACED instance)

    python3 ../../runtimes.py top/runs/h --blocks-from top/config.json
    python3 ../../runtimes.py top/runs/h --blocks-from top/config.json --json >> ../../results.jsonl

`--blocks-from` reads the block run directories and instance counts off the MACROS entry; the explicit form is
`python3 ../../runtimes.py top/runs/h {blocks}`.
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="write the H arm (n<N>/h/) from an emitted n<N>/ set")
    ap.add_argument("n_dir")
    ap.add_argument("--out", help="output directory (default <n_dir>/h)")
    ap.add_argument("--halo", type=float, nargs=2, metavar=("HX", "HY"),
                    default=(pp.SKY130["FP_MACRO_HORIZONTAL_HALO"], pp.SKY130["FP_MACRO_VERTICAL_HALO"]),
                    help="FP_MACRO_HORIZONTAL_HALO / VERTICAL_HALO written to the top and used by the checks")
    a = ap.parse_args(argv)
    try:
        r = write_h(a.n_dir, a.out or os.path.join(a.n_dir, "h"), tuple(a.halo))
    except Shape as e:
        print(f"harm: ERROR: {e}", file=sys.stderr)
        return 1
    v, h = r["vplan"], r["hplan"]
    blocks = ", ".join(f"{c} x{r['counts'][c]}" for c in r["cells"])
    print(f"harm: {r['out']}: {len(r['cells'])} blocks ({blocks}), "
          f"top with {r['instances']} macro instances, placement shift ({r['shift'][0]}, {r['shift'][1]}) um")
    print(f"harm: PDN_VPITCH {v['PDN_VPITCH']} = PPX {v['ppx']}/{v['k']}, PDN_VOFFSET {v['PDN_VOFFSET']} "
          f"(met4 clearance {v['clearance_met4']} um, {v['row_fragments']} row fragments each crossed by a pair); "
          f"PDN_HPITCH {h['PDN_HPITCH']} = RPY {h['rpy']}/{h['k']}, PDN_HOFFSET {h['PDN_HOFFSET']} "
          f"(met5: {'clearance %s um' % h['clearance_met5'] if h['met5_straps_over_macros'] else 'no strap over any macro'})")
    for line in r["advice"]:
        print("harm: " + line)
    print(f"harm: next steps in {r['out']}/README.md (dry-run check, harden the blocks in parallel, "
          f"pdn_phase.py on the hardened LEFs, the top, runtimes.py --blocks-from)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
