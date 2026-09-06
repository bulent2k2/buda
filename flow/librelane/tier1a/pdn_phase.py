#!/usr/bin/env python3
"""The PDN-phase check of the hierarchical arm: run it AFTER the blocks are
hardened and BEFORE the top (docs/internal/librelane_hier_flow.md §8 step 4
and §9, phase 1).

    pdn_phase.py <top config.json> [<cell.lef> ...] [--json out.json]

A macro is fed by the top's power straps, and where its own power PINS sit
under a strap of the OTHER net on the SAME layer, pdngen clips the strap
rather than short the two -- the strap then never reaches the pins it was to
feed and nothing before IR-drop signoff says so (`[PSM-0069] Check
connectivity failed`, measured on the phase-0 toy).  The pin positions are a
property of the HARDENED cell, so this reads them from the block's final
LEF (the VPWR/VGND pin rectangles) and the top's PDN config (pitch, offset,
width, spacing, layers, core margins), places every macro instance the way
`Odb.ManualMacroPlacement` will, and reports:

  * every (instance, pin rect, strap) COLLISION -- a pin under a strap of
    the other net on the same layer, minimum spacing included;
  * every (instance, net) with NO connection -- no strap of that net either
    overlaps a same-layer pin or crosses a pin on the other layer with room
    for a via (a strap that misses a pin by 0.66 um is the second way the
    toy failed);
  * per instance, and for the whole placement, the SMALLEST x-shift (and
    y-shift for the horizontal straps) that clears it, the whole-placement
    one restated as the PDN_VOFFSET/PDN_HOFFSET that would do the same.

PASS looks like `PASS: <n> instances, <m> power-pin rects, 0 collisions,
every instance connected on VPWR and VGND` and exit 0.  Any collision or
unconnected net is a FAIL (exit 1) with every offender listed; an input of
a shape this did not expect -- a config without DIE_AREA, a LEF without the
macro, a power pin drawn as a POLYGON, an instance with no location -- is
exit 2, because a check that guessed would be worse than none.

What is ASSUMED rather than read (sky130A, LibreLane 3.0.11; every value is
overridable from the config, and the report names each default it used):
PDN_VERTICAL_LAYER met4 / PDN_HORIZONTAL_LAYER met5; strap width 1.6 and
spacing 1.7 on both; PDN_VPITCH 153.6 / PDN_VOFFSET 16.32 / PDN_HPITCH
153.18 / PDN_HOFFSET 16.65; the core is the die inset by LEFT/RIGHT_MARGIN_MULT
12 site widths (0.46) and BOTTOM/TOP_MARGIN_MULT 4 site heights (2.72) --
`scripts/openroad/floorplan.tcl`, the 5.52 um core origin the toy measured;
strap CENTRES sit at core origin + offset + k*pitch with the VPWR strap
first and VGND one width-plus-spacing after it (`add_pdn_stripe -offset
... -starts_with POWER`, `scripts/openroad/common/pdn_cfg.tcl`; the toy's
VPWR strap at 34.72-36.32 for offset 0, pitch 30, core 5.52 is that rule);
minimum same-layer spacing 0.3 on met4 and 1.6 on met5; a via needs a
1.4 x 1.4 overlap (sky130 via4: 0.8 cut + 2 x 0.31 enclosure).  The
deprecated FP_PDN_* spellings are accepted like LibreLane accepts them.
"""
import argparse
import json
import math
import os
import re
import sys

SKY130 = {
    "PDN_VERTICAL_LAYER": "met4", "PDN_HORIZONTAL_LAYER": "met5",
    "PDN_VWIDTH": 1.6, "PDN_HWIDTH": 1.6, "PDN_VSPACING": 1.7, "PDN_HSPACING": 1.7,
    "PDN_VPITCH": 153.6, "PDN_HPITCH": 153.18, "PDN_VOFFSET": 16.32, "PDN_HOFFSET": 16.65,
    "LEFT_MARGIN_MULT": 12, "RIGHT_MARGIN_MULT": 12, "BOTTOM_MARGIN_MULT": 4, "TOP_MARGIN_MULT": 4,
    "VDD_NET": "VPWR", "GND_NET": "VGND",
    "FP_MACRO_HORIZONTAL_HALO": 10.0, "FP_MACRO_VERTICAL_HALO": 10.0,
}
SITE_W, SITE_H = 0.46, 2.72          # sky130_fd_sc_hd unithd
MIN_SPACING = {"met4": 0.3, "met5": 1.6}
VIA_MIN = 1.4
GRID = 0.005                          # manufacturing grid, um
EPS = 1e-6

DEPRECATED = {k: "FP_" + k for k in SKY130 if k.startswith("PDN_")}


class InputShape(Exception):
    """An input of a shape the check did not expect (exit 2)."""


# ── the config ────────────────────────────────────────────────────────────
def cfg_value(cfg, key, used_defaults):
    """The config's value for `key` (or its deprecated FP_ spelling), else the
    sky130A default, recorded so the report can say which it assumed."""
    if key in cfg:
        return cfg[key]
    alt = DEPRECATED.get(key)
    if alt and alt in cfg:
        return cfg[alt]
    used_defaults.append(key)
    return SKY130[key]


def resolve_path(p, base):
    if isinstance(p, str) and p.startswith("dir::"):
        return os.path.normpath(os.path.join(base, p[len("dir::"):]))
    return p


def read_top_config(path):
    cfg = json.load(open(path))
    base = os.path.dirname(os.path.abspath(path))
    used = []
    if cfg.get("FP_SIZING") != "absolute" or "DIE_AREA" not in cfg:
        raise InputShape(f"{path}: FP_SIZING absolute + DIE_AREA required -- the strap grid is "
                         f"anchored on the core of a FIXED die, so a relative-sized top has no "
                         f"phase to check")
    die = [float(v) for v in cfg["DIE_AREA"]]
    if len(die) != 4 or die[2] <= die[0] or die[3] <= die[1]:
        raise InputShape(f"{path}: DIE_AREA must be [x0 y0 x1 y1] with x1>x0, y1>y0: {cfg['DIE_AREA']}")
    g = {k: cfg_value(cfg, k, used) for k in SKY130}
    for k in ("PDN_VWIDTH", "PDN_HWIDTH", "PDN_VSPACING", "PDN_HSPACING", "PDN_VPITCH", "PDN_HPITCH",
              "PDN_VOFFSET", "PDN_HOFFSET", "LEFT_MARGIN_MULT", "RIGHT_MARGIN_MULT",
              "BOTTOM_MARGIN_MULT", "TOP_MARGIN_MULT", "FP_MACRO_HORIZONTAL_HALO",
              "FP_MACRO_VERTICAL_HALO"):
        g[k] = float(g[k])
    if g["PDN_VPITCH"] <= 0 or g["PDN_HPITCH"] <= 0:
        raise InputShape(f"{path}: PDN pitches must be positive")
    if "CORE_AREA" in cfg:
        core = [float(v) for v in cfg["CORE_AREA"]]
    else:
        core = [die[0] + g["LEFT_MARGIN_MULT"] * SITE_W, die[1] + g["BOTTOM_MARGIN_MULT"] * SITE_H,
                die[2] - g["RIGHT_MARGIN_MULT"] * SITE_W, die[3] - g["TOP_MARGIN_MULT"] * SITE_H]
    macros = cfg.get("MACROS")
    if not macros:
        raise InputShape(f"{path}: no MACROS entry -- nothing to check")
    insts, lef_paths = [], {}
    for cell, m in macros.items():
        for name, d in (m.get("instances") or {}).items():
            loc = d.get("location")
            if loc is None or d.get("orientation") is None:
                raise InputShape(f"{path}: MACROS.{cell}.instances.{name} needs location AND "
                                 f"orientation (ManualMacroPlacement places nothing without them)")
            insts.append({"name": name, "cell": cell, "x": float(loc[0]), "y": float(loc[1]),
                          "orient": d["orientation"]})
        lef_paths[cell] = [resolve_path(p, base) for p in (m.get("lef") or [])]
    if not insts:
        raise InputShape(f"{path}: MACROS names no instances")
    return {"path": path, "cfg": cfg, "die": die, "core": core, "g": g, "used_defaults": used,
            "instances": insts, "lef_paths": lef_paths}


# ── the straps ────────────────────────────────────────────────────────────
def strap_centres(lo, hi, offset, pitch):
    """VPWR strap centres: lo + offset + k*pitch while < hi (pdngen's loop)."""
    out, k = [], 0
    while True:
        c = lo + offset + k * pitch
        if c >= hi - EPS:
            return out
        out.append((k, c))
        k += 1


def straps_along(lo, hi, offset, pitch, width, spacing, vdd, gnd):
    """The strap intervals along one axis: [{net,k,lo,hi,c}] -- VPWR centred at
    each centre, VGND one width-plus-spacing after it (`-starts_with POWER`)."""
    out = []
    for k, c in strap_centres(lo, hi, offset, pitch):
        out.append({"net": vdd, "k": k, "c": c, "lo": c - width / 2, "hi": c + width / 2})
        cg = c + width + spacing
        out.append({"net": gnd, "k": k, "c": cg, "lo": cg - width / 2, "hi": cg + width / 2})
    return out


def top_straps(top, dx=0.0, dy=0.0):
    """(vertical straps as x-intervals, horizontal straps as y-intervals) on
    the top's core, the core shifted by (-dx,-dy) so a placement shift of
    (dx,dy) reads as the straps moving the other way."""
    g, c = top["g"], top["core"]
    v = straps_along(c[0] - dx, c[2] - dx, g["PDN_VOFFSET"], g["PDN_VPITCH"], g["PDN_VWIDTH"],
                     g["PDN_VSPACING"], g["VDD_NET"], g["GND_NET"])
    h = straps_along(c[1] - dy, c[3] - dy, g["PDN_HOFFSET"], g["PDN_HPITCH"], g["PDN_HWIDTH"],
                     g["PDN_HSPACING"], g["VDD_NET"], g["GND_NET"])
    return v, h


# ── the LEF ───────────────────────────────────────────────────────────────
def read_lef(path):
    """{macro: {size:(w,h), origin:(x,y), class:str, pins:{pin:{use:str,
    rects:[(layer,x1,y1,x2,y2)]}}}} -- the MACRO/SIZE/PIN/PORT/LAYER/RECT
    subset; a POLYGON inside a power pin is refused (exit 2) rather than
    approximated, and a RECT whose layer has not been named is an error."""
    macros, cur, pin, layer, in_port = {}, None, None, None, False
    with open(path) as f:
        txt = f.read()
    # statements end in ';' except the block openers/closers
    for raw in txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        t = line.rstrip(";").split()
        if not t:
            continue
        if t[0] == "MACRO" and len(t) >= 2:
            cur = {"size": None, "origin": (0.0, 0.0), "class": None, "pins": {}, "obs": []}
            macros[t[1]] = cur
            name = t[1]
        elif cur is None:
            continue
        elif t[0] == "SIZE" and len(t) >= 4:
            cur["size"] = (float(t[1]), float(t[3]))
        elif t[0] == "ORIGIN" and len(t) >= 3:
            cur["origin"] = (float(t[1]), float(t[2]))
        elif t[0] == "CLASS" and len(t) >= 2:
            cur["class"] = " ".join(t[1:])
        elif t[0] == "PIN" and len(t) >= 2:
            pin = {"use": None, "rects": []}
            cur["pins"][t[1]] = pin
            layer = None
        elif t[0] == "USE" and pin is not None and len(t) >= 2:
            pin["use"] = t[1]
        elif t[0] == "PORT":
            in_port = True
            layer = None
        elif t[0] == "LAYER" and len(t) >= 2:
            layer = t[1]
        elif t[0] == "RECT" and (pin is not None and in_port):
            nums = [x for x in t[1:] if re.fullmatch(r"-?\d+(\.\d+)?", x)]
            if layer is None or len(nums) < 4:
                raise InputShape(f"{path}: RECT before a LAYER, or short, in PIN of MACRO {name}: {line}")
            x1, y1, x2, y2 = (float(v) for v in nums[:4])
            pin["rects"].append((layer, min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
        elif t[0] == "POLYGON" and pin is not None and in_port:
            if (pin["use"] or "").upper() in ("POWER", "GROUND"):
                raise InputShape(f"{path}: MACRO {name} draws a power pin as a POLYGON; this check "
                                 f"reads RECTs only -- write the LEF with rectangles or extend the reader")
        elif t[0] == "END":
            if len(t) == 1 or t[1] == "PORT":
                in_port = False
            elif pin is not None and len(t) >= 2 and t[1] in cur["pins"]:
                pin = None
            elif len(t) >= 2 and t[1] in macros:
                cur = None
    for m, d in macros.items():
        if d["size"] is None:
            raise InputShape(f"{path}: MACRO {m} has no SIZE")
    return macros


# ── orientation: cell-local rect -> instance-local, lower-left at (0,0) ──
_ORIENT = {  # (x,y) -> rotated/mirrored point about the origin
    "N": lambda x, y: (x, y), "S": lambda x, y: (-x, -y),
    "W": lambda x, y: (-y, x), "E": lambda x, y: (y, -x),
    "FN": lambda x, y: (-x, y), "FS": lambda x, y: (x, -y),
    "FW": lambda x, y: (y, x), "FE": lambda x, y: (-y, -x),
}


def orient_rect(rect, orient, w, h):
    """A cell-local (x1,y1,x2,y2) in the placed instance's frame: DEF/LEF
    orientation applied about the origin, then translated so the oriented
    cell's lower-left corner is (0,0) -- the point the DEF PLACED gives."""
    if orient not in _ORIENT:
        raise InputShape(f"unknown orientation {orient!r} (N S E W FN FS FE FW)")
    f = _ORIENT[orient]
    cx = [f(x, y)[0] for x in (0, w) for y in (0, h)]
    cy = [f(x, y)[1] for x in (0, w) for y in (0, h)]
    ox, oy = min(cx), min(cy)
    x1, y1, x2, y2 = rect
    p = [f(x1, y1), f(x2, y1), f(x1, y2), f(x2, y2)]
    return (min(q[0] for q in p) - ox, min(q[1] for q in p) - oy,
            max(q[0] for q in p) - ox, max(q[1] for q in p) - oy)


def placed_size(orient, w, h):
    return (h, w) if orient in ("E", "W", "FE", "FW") else (w, h)


# ── the check ─────────────────────────────────────────────────────────────
def overlap(a1, a2, b1, b2):
    return min(a2, b2) - max(a1, b1)


def instance_rects(inst, macro, top):
    """The instance's power-pin rects in TOP coordinates: [(net, layer, x1,y1,x2,y2, pin)]."""
    g = top["g"]
    nets = {g["VDD_NET"]: g["VDD_NET"], g["GND_NET"]: g["GND_NET"]}
    w, h = macro["size"]
    out = []
    for pname, pin in macro["pins"].items():
        use = (pin["use"] or "").upper()
        net = nets.get(pname)
        if net is None:
            if use in ("POWER", "GROUND"):
                raise InputShape(f"MACRO {inst['cell']}: power pin {pname} (USE {use}) is neither "
                                 f"VDD_NET {g['VDD_NET']} nor GND_NET {g['GND_NET']}; name the nets in the config")
            continue
        for (layer, x1, y1, x2, y2) in pin["rects"]:
            r = orient_rect((x1, y1, x2, y2), inst["orient"], w, h)
            out.append((net, layer, r[0] + inst["x"], r[1] + inst["y"], r[2] + inst["x"], r[3] + inst["y"], pname))
    return out


def eval_instance(inst, rects, top, vstraps, hstraps, spacing, via_min):
    """(collisions, connected-by-net, connections) for one placed instance;
    `rects` already in top coordinates (shift applied by the caller)."""
    g, core = top["g"], top["core"]
    lv, lh = g["PDN_VERTICAL_LAYER"], g["PDN_HORIZONTAL_LAYER"]
    collisions, connections = [], []
    connected = {g["VDD_NET"]: False, g["GND_NET"]: False}
    for (net, layer, x1, y1, x2, y2, pname) in rects:
        if layer == lv:
            # vertical straps span the core's height: only a rect inside it meets them
            if overlap(y1, y2, core[1], core[3]) <= 0:
                continue
            sp = spacing.get(layer, 0.0)
            for s in vstraps:
                ov = overlap(x1, x2, s["lo"], s["hi"])
                if s["net"] != net:
                    if ov > -sp + EPS:
                        collisions.append({"instance": inst["name"], "pin": pname, "net": net, "layer": layer,
                                           "rect": [x1, y1, x2, y2], "strap_net": s["net"], "strap_k": s["k"],
                                           "strap": [s["lo"], s["hi"]], "overlap": round(ov, 4), "axis": "x"})
                elif ov > EPS:
                    connected[net] = True
                    connections.append((inst["name"], pname, layer, "overlaps", s["net"], s["k"]))
            for s in hstraps:
                if s["net"] == net and min(overlap(y1, y2, s["lo"], s["hi"]), x2 - x1) >= via_min - EPS:
                    connected[net] = True
                    connections.append((inst["name"], pname, layer, "crossed by", s["net"], s["k"]))
        elif layer == lh:
            if overlap(x1, x2, core[0], core[2]) <= 0:
                continue
            sp = spacing.get(layer, 0.0)
            for s in hstraps:
                ov = overlap(y1, y2, s["lo"], s["hi"])
                if s["net"] != net:
                    if ov > -sp + EPS:
                        collisions.append({"instance": inst["name"], "pin": pname, "net": net, "layer": layer,
                                           "rect": [x1, y1, x2, y2], "strap_net": s["net"], "strap_k": s["k"],
                                           "strap": [s["lo"], s["hi"]], "overlap": round(ov, 4), "axis": "y"})
                elif ov > EPS:
                    connected[net] = True
                    connections.append((inst["name"], pname, layer, "overlaps", s["net"], s["k"]))
            for s in vstraps:
                if s["net"] == net and min(overlap(x1, x2, s["lo"], s["hi"]), y2 - y1) >= via_min - EPS:
                    connected[net] = True
                    connections.append((inst["name"], pname, layer, "crossed by", s["net"], s["k"]))
    return collisions, connected, connections


def shifted(rects, dx, dy):
    return [(n, l, x1 + dx, y1 + dy, x2 + dx, y2 + dy, p) for (n, l, x1, y1, x2, y2, p) in rects]


def clean_at(inst, rects, top, vstraps, hstraps, spacing, via_min, dx, dy):
    col, con, _ = eval_instance(inst, shifted(rects, dx, dy), top, vstraps, hstraps, spacing, via_min)
    return not col and all(con.values())


def shift_candidates(rects_list, top, vstraps, hstraps, spacing, via_min, axis):
    """The dx (axis x) or dy (axis y) values at which some constraint changes
    state -- a pin edge meeting a strap edge plus spacing, or a crossing
    reaching via size -- for the given instances' rects.  The smallest
    clearing shift is one of these (or 0), so they are what gets tried."""
    g = top["g"]
    lv, lh = g["PDN_VERTICAL_LAYER"], g["PDN_HORIZONTAL_LAYER"]
    same_layer, cross_layer, straps = (lv, lh, vstraps) if axis == "x" else (lh, lv, hstraps)
    cands = {0.0}
    for rects in rects_list:
        for (net, layer, x1, y1, x2, y2, _) in rects:
            lo, hi = (x1, x2) if axis == "x" else (y1, y2)
            if layer == same_layer:
                sp = spacing.get(layer, 0.0)
                for s in straps:
                    if s["net"] != net:
                        cands.add(s["lo"] - sp - hi)
                        cands.add(s["hi"] + sp - lo)
                    else:
                        cands.add(s["lo"] - hi + GRID)
                        cands.add(s["hi"] - lo - GRID)
            elif layer == cross_layer:
                for s in straps:
                    if s["net"] == net:
                        cands.add(s["lo"] + via_min - hi)
                        cands.add(s["hi"] - via_min - lo)
    out = set()
    for c in cands:
        q = round(c / GRID) * GRID
        for d in (q - GRID, q, q + GRID):
            out.add(round(d, 3))
    return sorted(out, key=lambda v: (abs(v), v))


def smallest_shift(insts, rects_by, top, vstraps, hstraps, spacing, via_min, axis, limit):
    """The smallest |shift| along `axis` (within +-limit) after which EVERY
    instance in `insts` is collision-free on both layers and connected on
    both nets; None when no candidate within the limit does it."""
    cands = shift_candidates([rects_by[i["name"]] for i in insts], top, vstraps, hstraps, spacing, via_min, axis)
    for d in cands:
        if abs(d) > limit + EPS:
            break
        dx, dy = (d, 0.0) if axis == "x" else (0.0, d)
        if all(clean_at(i, rects_by[i["name"]], top, vstraps, hstraps, spacing, via_min, dx, dy) for i in insts):
            return d
    return None


def run_check(top, lefs, spacing=None, via_min=VIA_MIN):
    spacing = dict(MIN_SPACING, **(spacing or {}))
    g = top["g"]
    vstraps, hstraps = top_straps(top)
    for inst in top["instances"]:
        if inst["cell"] not in lefs:
            raise InputShape(f"no LEF defines MACRO {inst['cell']} (instance {inst['name']}); LEFs read: "
                             f"{sorted(lefs)}")
    rects_by, per_inst, all_col, unconnected, n_rects = {}, [], [], [], 0
    for inst in top["instances"]:
        macro = lefs[inst["cell"]]
        rects = instance_rects(inst, macro, top)
        if not rects:
            raise InputShape(f"MACRO {inst['cell']}: no {g['VDD_NET']}/{g['GND_NET']} pin rectangles in its "
                             f"LEF -- a macro with no power pins cannot be fed; is this the FINAL lef?")
        rects_by[inst["name"]] = rects
        n_rects += len(rects)
        col, con, cons = eval_instance(inst, rects, top, vstraps, hstraps, spacing, via_min)
        all_col.extend(col)
        for net, ok in con.items():
            if not ok:
                unconnected.append({"instance": inst["name"], "net": net})
        row = {"instance": inst["name"], "cell": inst["cell"], "collisions": len(col),
               "connected": con, "connections": len(cons)}
        if col or not all(con.values()):
            row["dx"] = smallest_shift([inst], rects_by, top, vstraps, hstraps, spacing, via_min, "x",
                                       g["PDN_VPITCH"] / 2)
            row["dy"] = smallest_shift([inst], rects_by, top, vstraps, hstraps, spacing, via_min, "y",
                                       g["PDN_HPITCH"] / 2)
        per_inst.append(row)
    result = {"config": top["path"], "instances": len(top["instances"]), "pin_rects": n_rects,
              "collisions": all_col, "unconnected": unconnected, "per_instance": per_inst,
              "used_defaults": top["used_defaults"], "vstraps": len(vstraps), "hstraps": len(hstraps),
              "pass": not all_col and not unconnected}
    if not result["pass"]:
        insts = top["instances"]
        dx = smallest_shift(insts, rects_by, top, vstraps, hstraps, spacing, via_min, "x", g["PDN_VPITCH"] / 2)
        dy = smallest_shift(insts, rects_by, top, vstraps, hstraps, spacing, via_min, "y", g["PDN_HPITCH"] / 2)
        result["global_dx"], result["global_dy"] = dx, dy
        if dx is not None:
            result["voffset_for_dx"] = round((g["PDN_VOFFSET"] - dx) % g["PDN_VPITCH"], 3)
        if dy is not None:
            result["hoffset_for_dy"] = round((g["PDN_HOFFSET"] - dy) % g["PDN_HPITCH"], 3)
    return result


def report(top, lefs, res, out=sys.stdout):
    g, core = top["g"], top["core"]
    p = lambda *a: print(*a, file=out)
    p(f"pdn_phase: {top['path']}: die {top['die']}, core {[round(v, 3) for v in core]}")
    p(f"  {g['PDN_VERTICAL_LAYER']} straps: {res['vstraps']} at x = {core[0]:.3f} + {g['PDN_VOFFSET']} + k*{g['PDN_VPITCH']}"
      f" (VPWR first, VGND +{g['PDN_VWIDTH'] + g['PDN_VSPACING']:.2f}; width {g['PDN_VWIDTH']})")
    p(f"  {g['PDN_HORIZONTAL_LAYER']} straps: {res['hstraps']} at y = {core[1]:.3f} + {g['PDN_HOFFSET']} + k*{g['PDN_HPITCH']}"
      f" (width {g['PDN_HWIDTH']}, VGND +{g['PDN_HWIDTH'] + g['PDN_HSPACING']:.2f})")
    if res["used_defaults"]:
        p("  ASSUMED (not in the config; sky130A/LibreLane 3.0.11 defaults): " +
          ", ".join(f"{k}={SKY130[k]}" for k in res["used_defaults"]))
    for cell in sorted({i["cell"] for i in top["instances"]}):
        m = lefs[cell]
        counts = {}
        for pname, pin in m["pins"].items():
            if pname in (g["VDD_NET"], g["GND_NET"]):
                for (layer, *_r) in pin["rects"]:
                    counts[(pname, layer)] = counts.get((pname, layer), 0) + 1
        p(f"  MACRO {cell}: {m['size'][0]} x {m['size'][1]}, power-pin rects " +
          ", ".join(f"{n}/{l}: {c}" for (n, l), c in sorted(counts.items())))
    for c in res["collisions"]:
        p(f"COLLISION {c['instance']} {c['net']} pin {c['pin']} on {c['layer']} "
          f"[{c['rect'][0]:.3f},{c['rect'][2]:.3f}]x[{c['rect'][1]:.3f},{c['rect'][3]:.3f}] "
          f"under {c['strap_net']} strap k={c['strap_k']} [{c['strap'][0]:.3f},{c['strap'][1]:.3f}] "
          f"(overlap {c['overlap']:.3f} um along {c['axis']}; spacing counted)")
    for u in res["unconnected"]:
        p(f"UNCONNECTED {u['instance']} {u['net']}: no strap of that net overlaps a same-layer pin or "
          f"crosses a pin on the other layer with room for a via")
    bad = [r for r in res["per_instance"] if r["collisions"] or not all(r["connected"].values())]
    for r in bad:
        fmt = lambda v: "none within half a pitch" if v is None else f"{v:+.3f} um"
        p(f"  {r['instance']}: smallest x-shift {fmt(r['dx'])}, smallest y-shift {fmt(r['dy'])}")
    if res["pass"]:
        p(f"PASS: {res['instances']} instances, {res['pin_rects']} power-pin rects, 0 collisions, "
          f"every instance connected on {g['VDD_NET']} and {g['GND_NET']}")
    else:
        line = (f"FAIL: {res['instances']} instances, {len(res['collisions'])} collisions in "
                f"{len({c['instance'] for c in res['collisions']})} instances, "
                f"{len(res['unconnected'])} unconnected instance-nets")
        if res.get("global_dx") is not None:
            line += (f"; shifting EVERY macro by dx={res['global_dx']:+.3f} clears the x-axis "
                     f"(equivalently PDN_VOFFSET={res['voffset_for_dx']})")
        if res.get("global_dy") is not None:
            line += (f"; dy={res['global_dy']:+.3f} the y-axis (PDN_HOFFSET={res['hoffset_for_dy']})")
        if res.get("global_dx") is None and res.get("global_dy") is None:
            line += "; no single shift within half a pitch clears every instance -- see the per-instance shifts"
        p(line)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("config", help="the top's LibreLane config.json (MACROS + PDN_*)")
    ap.add_argument("lef", nargs="*", help="hardened block LEFs; default: the config's MACROS.*.lef paths")
    ap.add_argument("--json", metavar="OUT", help="write the findings as JSON")
    ap.add_argument("--via-min", type=float, default=VIA_MIN, help=f"min overlap for a via (um, {VIA_MIN})")
    ap.add_argument("--spacing", action="append", default=[], metavar="LAYER=UM",
                    help="min same-layer spacing override, e.g. met4=0.3")
    a = ap.parse_args(argv)
    try:
        top = read_top_config(a.config)
        paths = list(a.lef)
        if not paths:
            for cell, ps in top["lef_paths"].items():
                if not ps:
                    raise InputShape(f"{a.config}: MACROS.{cell} has no lef view and none was passed")
                paths.extend(ps)
        lefs = {}
        for pth in paths:
            if not os.path.exists(pth):
                raise InputShape(f"{pth}: no such LEF -- harden the block first (or pass the predicted LEFs "
                                 f"harm.sh wrote for a dry run)")
            for name, m in read_lef(pth).items():
                lefs[name] = m
        spacing = {}
        for s in a.spacing:
            k, _, v = s.partition("=")
            spacing[k] = float(v)
        res = run_check(top, lefs, spacing, a.via_min)
    except InputShape as e:
        print(f"pdn_phase: ERROR: {e}", file=sys.stderr)
        return 2
    report(top, lefs, res)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=1)
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
