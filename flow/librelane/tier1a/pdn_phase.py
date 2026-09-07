#!/usr/bin/env python3
"""The PDN-phase check of the hierarchical arm: run it AFTER the blocks are
hardened and BEFORE the top (docs/internal/librelane_hier_flow.md §8 step 4
and §9, phase 1).

    pdn_phase.py <top config.json> [<cell.lef> ...] [--json out.json]

ADVISORY: a PREDICTION of what pdngen builds and of what OpenROAD's PSM
check then finds unconnected, not the verdict.  The verdict is that PSM
check (`[PSM-0069] Check connectivity failed`) at the end of the top run,
the LAST step of the run; `pdn_connect.py` on the DEF pdngen wrote is the
post-mortem, and the two share one network code (`pdn_connect.net_components`),
so a disagreement between prediction and post-mortem points at the
prediction's INPUT -- a LEF, a config value, a pdngen rule this does not
model -- rather than at a second definition of "connected".  The previous
model counted every same-layer meeting as a defect and was wrong in the way
librelane_hier_flow.md §7.2 records; acting on its FAIL by editing
PDN_HOFFSET once turned a working plan into a PSM-0069 failure (§11 item 8).

What pdngen does (OpenROAD src/pdn/src, read rather than assumed):

  1. STRAPS.  `Straps::makeStraps` places the top's straps at core origin +
     offset + k*pitch (see `straps_along`; not simply "every k the pitch
     allows").
  2. CUTS.  `Shape::cut` removes a strap wherever it meets an obstruction on
     its own layer, across the strap's full width.  A macro's power PIN is
     such an obstruction, grown by the layer's spacing ACROSS the strap and
     by the macro grid's halo ALONG it (`InstanceGrid::getInstanceObstructions`
     -> `applyHalo`, which applies the halo on the layer's wire axis only),
     and the violation subtracted is that grown once more by the STRAP's
     spacing on every side (`getRectWithLargestObstructionHalo`) -- two
     spacings across, halo plus one spacing along; a same-net pin is spared
     only when the strap CONTAINS it plus its spacing across the strap's
     width, which a block pin wider than the strap never is -- so a
     same-layer meeting is a TRIM whatever the two nets are, never a
     connection, and never a defect either: it is how pdngen keeps straps
     off macro pins.  A macro's OBS is an obstruction too, bloated by
     spacing and halo; on a PDN layer it is normally the block's OWN power
     grid (Magic's LEF draws the actual metal, and `final/lef/<cell>.lef` is
     Magic's), the same rectangles as its pins, so it is not counted twice.
     FOREIGN metal on a PDN layer -- signal routing pushed up there -- is the
     dangerous kind: it removes every strap it covers and no phase clears it,
     so it is reported with the remedy (cap the block below that layer,
     `RT_MAX_LAYER`).
  3. VIAS.  `Grid::getIntersections` makes a via wherever a same-net shape on
     the connect pair's lower layer overlaps one on the upper layer -- the
     top's straps AND the macro's own pins, which `getInstancePins` injects
     (`add_pdn_connect -grid macro -layers {met4 met5}`, LibreLane's macro
     grid) -- when the overlap can hold one (1.4 x 1.4 on sky130 via4).
  4. TRIM.  `PdnGen::trimShapes` (run unless PDN_SKIPTRIM, which harm.py
     sets for BLOCKS only, so a top trims) removes every strap fragment with
     fewer connections than `Shape::isRemovable` requires -- 2, or 1 on a pin
     layer, and PDN_ENABLE_PINS (default on) makes both connect layers pin
     layers -- and shrinks the rest to the extent of their vias.  So a
     via-less fragment (a stub between two cuts; the strap on the core edge
     whose crossings are narrower than a via) is GONE before signoff.

PSM then fails a net whose remaining shapes are not one connected set.  In
these terms the check reports, per net:

  * every TRIM (informational: which pin or OBS cuts which strap, where);
  * every STRANDED terminal -- a macro power pin NONE of whose rectangles is
    on its net's main grid (a LEF PIN is one terminal however many RECTs it
    is drawn as; a pin rectangle off the grid whose terminal is fed by
    another of its rectangles is not a failure -- measured, the phase-0 toy
    passes PSM with two such VGND rectangles);
  * every FLOATING fragment -- a strap piece that survives trim on a
    component off the main grid: the "N unconnected shapes" PSM counts;
  * the smallest whole-placement shift (x, y, or a searched pair when
    neither axis alone does it) after which nothing is predicted to fail,
    restated as the PDN_VOFFSET/PDN_HOFFSET that would do the same -- offered
    only after being verified.

PASS looks like `PASS: <n> instances, <m> power-pin rects, <t> trims in <i>
instances, every terminal on its net's grid, no surviving fragment off it`
and exit 0; a stranded terminal or a floating fragment is a FAIL (exit 1)
with every offender listed; an input of a shape this did not expect -- a
config without DIE_AREA, a LEF without the macro, a power pin or an
obstruction drawn as a POLYGON, an instance with no location -- is exit 2,
because a check that guessed would be worse than none.

The strap positions are pdngen's own loop (`Straps::makeStraps`), which is
not simply "every k the pitch allows": see `straps_along`.

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
    # pdngen's macro grid takes its OWN halo (`define_pdn_grid -macro -halo
    # "$PDN_HORIZONTAL_HALO $PDN_VERTICAL_HALO"`), not the floorplan's; both
    # default to 10 in LibreLane, but they are different knobs
    "PDN_HORIZONTAL_HALO": 10.0, "PDN_VERTICAL_HALO": 10.0,
    # what pdngen's trim removes (`PdnGen::trimShapes`): a strap with fewer
    # connections (vias) than the minimum -- 2, or 1 on a PIN layer, and
    # PDN_ENABLE_PINS (default on) makes both connect layers pin layers.
    # PDN_SKIPTRIM (default off) skips the pass; harm.py sets it for BLOCKS
    # only, so a top runs with trim
    "PDN_ENABLE_PINS": True, "PDN_SKIPTRIM": False,
}
OWN_METAL_TOL = 1.0        # how far an OBS rect may exceed the pin it covers
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
              "FP_MACRO_VERTICAL_HALO", "PDN_HORIZONTAL_HALO", "PDN_VERTICAL_HALO"):
        g[k] = float(g[k])
    for k in ("PDN_ENABLE_PINS", "PDN_SKIPTRIM"):
        v = g[k]
        g[k] = v.strip().lower() in ("1", "true", "yes", "on") if isinstance(v, str) else bool(v)
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
def straps_along(lo, hi, offset, pitch, width, spacing, vdd, gnd, die_lo=None, die_hi=None):
    """The strap intervals along one axis: [{net,k,lo,hi,c}], pdngen's own loop
    (`Straps::makeStraps`, OpenROAD src/pdn/src/straps.cpp).

    Each PERIOD starts at lo + offset + k*pitch and each NET in turn takes the
    running position, advancing it by width+spacing -- VPWR first
    (`-starts_with POWER`), VGND after it.  Three details are pdngen's and each
    of them decides a strap at the far core edge:

      * the period loop runs while `pos <= pos_end`, so a period whose centre
        lands exactly ON the core edge is still generated;
      * each net's strap is dropped, and the WHOLE loop stops, once that
        strap's own centre passes the end or its near edge reaches it -- so
        the last period can be a lone VPWR with no VGND partner;
      * a strap whose rectangle leaves the DIE is skipped (that one alone does
        not stop the loop).
    """
    out, k = [], 0
    pos = lo + offset
    while pos <= hi + EPS:
        group_pos = pos
        for net in (vdd, gnd):
            s_lo = group_pos - width / 2
            if s_lo >= hi - EPS or group_pos > hi + EPS:
                return out                      # pdngen returns, it does not continue
            s_hi = s_lo + width
            group_pos += width + spacing
            if (die_lo is not None and s_lo < die_lo - EPS) or \
               (die_hi is not None and s_hi > die_hi + EPS):
                continue                        # outside the die
            out.append({"net": net, "k": k, "c": (s_lo + s_hi) / 2, "lo": s_lo, "hi": s_hi})
        k += 1
        pos += pitch
    return out


def strap_centres(lo, hi, offset, pitch, width=0.0, spacing=0.0, die_lo=None, die_hi=None):
    """The (k, centre) of every VPWR strap `straps_along` emits -- the periods
    that survive pdngen's loop, which is not every k the pitch would allow."""
    return [(s["k"], s["c"]) for s in
            straps_along(lo, hi, offset, pitch, width, spacing, "VPWR", "VGND", die_lo, die_hi)
            if s["net"] == "VPWR"]


def top_straps(top, dx=0.0, dy=0.0):
    """(vertical straps as x-intervals, horizontal straps as y-intervals) on
    the top's core, the core shifted by (-dx,-dy) so a placement shift of
    (dx,dy) reads as the straps moving the other way."""
    g, c, d = top["g"], top["core"], top["die"]
    v = straps_along(c[0] - dx, c[2] - dx, g["PDN_VOFFSET"], g["PDN_VPITCH"], g["PDN_VWIDTH"],
                     g["PDN_VSPACING"], g["VDD_NET"], g["GND_NET"], d[0] - dx, d[2] - dx)
    h = straps_along(c[1] - dy, c[3] - dy, g["PDN_HOFFSET"], g["PDN_HPITCH"], g["PDN_HWIDTH"],
                     g["PDN_HSPACING"], g["VDD_NET"], g["GND_NET"], d[1] - dy, d[3] - dy)
    return v, h


# ── the LEF ───────────────────────────────────────────────────────────────
def read_lef(path):
    """{macro: {size:(w,h), origin:(x,y), class:str, pins:{pin:{use:str,
    rects:[(layer,x1,y1,x2,y2)]}}, obs:[(layer,x1,y1,x2,y2)]}} -- the
    MACRO/SIZE/PIN/PORT/LAYER/RECT/OBS subset; a POLYGON inside a power pin is
    refused (exit 2) rather than approximated, and a RECT whose layer has not
    been named is an error.

    The OBS block matters as much as the pins: LibreLane writes each hardened
    block's abstract LEF with `write_abstract_lef -bloat_occupied_layers`
    (`OPENROAD_LEF_BLOAT_OCCUPIED_LAYERS`, default True), which emits a
    WHOLE-BLOCK cover rectangle on every layer the block drew anything on --
    its own PDN straps included -- and pdngen cuts the top's straps against it
    (`InstanceGrid::getInstanceObstructions` bloats it by the macro halo,
    `Shape::cut` subtracts it).  A strap that is cut over the macro cannot
    feed it, so a checker that read only the pins would pass a design whose
    macros no strap reaches."""
    macros, cur, pin, layer, in_port, in_obs = {}, None, None, None, False, False
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
            layer, in_obs = None, False
        elif t[0] == "USE" and pin is not None and len(t) >= 2:
            pin["use"] = t[1]
        elif t[0] == "PORT":
            in_port = True
            layer = None
        elif t[0] == "OBS":
            in_obs, pin, in_port, layer = True, None, False, None
        elif t[0] == "LAYER" and len(t) >= 2:
            layer = t[1]
        elif t[0] == "RECT" and ((pin is not None and in_port) or in_obs):
            nums = [x for x in t[1:] if re.fullmatch(r"-?\d+(\.\d+)?", x)]
            where = "OBS" if in_obs else f"PIN of MACRO {name}"
            if layer is None or len(nums) < 4:
                raise InputShape(f"{path}: RECT before a LAYER, or short, in {where} of MACRO {name}: {line}")
            x1, y1, x2, y2 = (float(v) for v in nums[:4])
            r = (layer, min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            (cur["obs"] if in_obs else pin["rects"]).append(r)
        elif t[0] == "POLYGON" and in_obs:
            raise InputShape(f"{path}: MACRO {name} draws an OBS as a POLYGON; this check reads "
                             f"RECTs only -- an unread obstruction would pass straps pdngen cuts")
        elif t[0] == "POLYGON" and pin is not None and in_port:
            if (pin["use"] or "").upper() in ("POWER", "GROUND"):
                raise InputShape(f"{path}: MACRO {name} draws a power pin as a POLYGON; this check "
                                 f"reads RECTs only -- write the LEF with rectangles or extend the reader")
        elif t[0] == "END":
            if len(t) == 1 or t[1] == "PORT":
                if in_obs and (len(t) == 1):
                    in_obs, layer = False, None
                else:
                    in_port = False
            elif pin is not None and len(t) >= 2 and t[1] in cur["pins"]:
                pin = None
            elif len(t) >= 2 and t[1] in macros:
                cur, in_obs = None, False
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


def instance_obstructions(inst, macro, top, spacing, rects):
    """The instance's LEF OBS rectangles in TOP coordinates, each classified as
    the block's OWN power metal or as FOREIGN, and bloated the way pdngen
    bloats an obstruction (`InstanceGrid::getInstanceObstructions`: by the
    layer's spacing and by the macro grid's halo, the union of the two).

    The classification is the whole point, and it is the rule the first real
    run taught.  A hardened block's `final/lef/<cell>.lef` is MAGIC's LEF, so
    its OBS is the block's actual metal -- and on the PDN layers that metal IS
    the block's own power grid, the same rectangles as its power pins.  The
    phase search clears those when it clears the pins, because they are the
    same metal, so an obstruction that coincides with a pin says nothing new.
    What is dangerous is obstruction on a PDN layer that is NOT the block's own
    power metal -- signal routing pushed up onto met4 by a pin layout, say,
    which sits wherever the router put it and which no phase search can have
    cleared.  That is the shape that made pdngen drop straps and fail IR-drop
    signoff on the phase-0 toy.  So the cut is modelled for foreign metal and
    the own-power rectangles are reported as what they are."""
    g = top["g"]
    hx = max(g["PDN_HORIZONTAL_HALO"], 0.0)
    hy = max(g["PDN_VERTICAL_HALO"], 0.0)
    w, h = macro["size"]
    own = {}
    for (net, layer, x1, y1, x2, y2, _p) in rects:
        own.setdefault(layer, []).append((x1, y1, x2, y2))
    out = []
    for (layer, x1, y1, x2, y2) in macro.get("obs", []):
        r = orient_rect((x1, y1, x2, y2), inst["orient"], w, h)
        rx1, ry1 = r[0] + inst["x"], r[1] + inst["y"]
        rx2, ry2 = r[2] + inst["x"], r[3] + inst["y"]
        t = OWN_METAL_TOL
        is_own = any(px1 - t <= rx1 and py1 - t <= ry1 and rx2 <= px2 + t and ry2 <= py2 + t
                     for (px1, py1, px2, py2) in own.get(layer, []))
        bx, by = max(spacing.get(layer, 0.0), hx), max(spacing.get(layer, 0.0), hy)
        out.append({"layer": layer, "own_power_metal": is_own,
                    "rect": [rx1, ry1, rx2, ry2],
                    "bloated": [rx1 - bx, ry1 - by, rx2 + bx, ry2 + by]})
    return out


# ── the grid pdngen would WRITE ──────────────────────────────────────────
# Three rules, each read out of OpenROAD's src/pdn/src and each one the old
# model lacked (#895).  (1) `Shape::cut`: a strap meeting an obstruction on
# its own layer is CUT there, across its full width -- a clip is a trim, not a
# verdict, and the strap keeps the rest of its length.  (2) `InstanceGrid::
# getInstanceObstructions` + `applyHalo(..., is_horizontal, is_vertical)`: a
# macro PIN is an obstruction grown by the macro grid's halo ALONG its
# layer's direction and by the layer's spacing ACROSS it.  (3) `Grid::
# getIntersections` + `getInstancePins`: a via exists wherever a same-net
# shape on the connect pair's lower layer overlaps one on its upper layer,
# and the macro's own pins are shapes -- so connectivity is CROSS-layer, a
# pin can be fed by a strap on the other layer or by its own net's pin on
# the other layer, and whether it IS fed is a property of the whole net's
# metal, which `pdn_connect.net_components` already answers for a written
# DEF and here answers for the predicted one.
def strap_rects(top, vstraps, hstraps):
    """Every strap as a rectangle on its layer: a vertical strap spans the
    core's height, a horizontal one its width."""
    g, c = top["g"], top["core"]
    lv, lh = g["PDN_VERTICAL_LAYER"], g["PDN_HORIZONTAL_LAYER"]
    out = [{"net": s["net"], "layer": lv, "k": s["k"], "vertical": True,
            "rect": [s["lo"], c[1], s["hi"], c[3]]} for s in vstraps]
    out += [{"net": s["net"], "layer": lh, "k": s["k"], "vertical": False,
             "rect": [c[0], s["lo"], c[2], s["hi"]]} for s in hstraps]
    return out


def pin_cutter(top, spacing, net, layer, x1, y1, x2, y2):
    """A macro pin as pdngen makes it an obstruction to the top's straps:
    (cut_lo, cut_hi, keep_lo, keep_hi, along_lo, along_hi).

    `InstanceGrid::getInstanceObstructions` turns the pin into a shape whose
    rect is the pin bloated by the layer's spacing (`generateObstruction`)
    and then by the macro grid's HALO along the layer's wire axis only
    (`applyHalo(rect, halo, true, is_horizontal, is_vertical)`).  `Shape::cut`
    then (a) spares a same-net obstruction only when the strap's own rect
    contains THAT rect across the strap -- so the pin plus ONE spacing is
    what has to fit (`keep`); (b) takes the violation as that rect grown by
    the STRAP's obstruction halo, its own spacing, on every side
    (`getRectWithLargestObstructionHalo`), and subtracts it from the strap --
    so a strap is cut when the pin comes within TWO spacings of it (`cut`,
    the pin's and the strap's; one layer, one MIN_SPACING here for both) and
    loses the pin's extent plus the halo plus one spacing along (`along`).
    The old reading had one spacing everywhere, which no run here
    discriminated (x=20 cuts by 0.5 um either way); the source does."""
    g = top["g"]
    sp = spacing.get(layer, 0.0)
    if layer == g["PDN_VERTICAL_LAYER"]:
        h = max(g["PDN_VERTICAL_HALO"], 0.0)
        return x1 - 2 * sp, x2 + 2 * sp, x1 - sp, x2 + sp, y1 - h - sp, y2 + h + sp
    h = max(g["PDN_HORIZONTAL_HALO"], 0.0)
    return y1 - 2 * sp, y2 + 2 * sp, y1 - sp, y2 + sp, x1 - h - sp, x2 + h + sp


def _subtract(intervals, lo, hi):
    out = []
    for a, b in intervals:
        if hi <= a + EPS or lo >= b - EPS:
            out.append((a, b))
            continue
        if lo > a + EPS:
            out.append((a, lo))
        if hi < b - EPS:
            out.append((hi, b))
    return out


def trim_straps(top, straps, rects_by, obs_by, spacing):
    """(fragments, trims): each strap after every cut pdngen would make in it.

    A cutter on the strap's layer whose ACROSS extent reaches the strap --
    a pin per `pin_cutter`, a foreign OBS bloated as `instance_obstructions`
    has it and again by the strap's own spacing (`Shape::cut` grows every
    violation by the strap's obstruction halo) -- removes its ALONG extent
    from the strap, across the strap's full width.  A same-net PIN is spared
    only when the strap CONTAINS it plus one spacing across its width, which
    a block pin wider than the strap never is, so a same-layer meeting is a
    trim whatever the two nets are.  The block's own-power OBS is the same
    metal as its pins and is not counted twice."""
    frags, trims = [], []
    for s in straps:
        x1, y1, x2, y2 = s["rect"]
        vert = s["vertical"]
        sp = spacing.get(s["layer"], 0.0)
        along = [(y1, y2)] if vert else [(x1, x2)]
        s_lo, s_hi = (x1, x2) if vert else (y1, y2)
        for inst_name, rects in rects_by.items():
            for (net, layer, px1, py1, px2, py2, pname) in rects:
                if layer != s["layer"]:
                    continue
                c_lo, c_hi, k_lo, k_hi, a_lo, a_hi = pin_cutter(top, spacing, net, layer, px1, py1, px2, py2)
                if overlap(c_lo, c_hi, s_lo, s_hi) <= EPS:
                    continue
                if net == s["net"] and s_lo <= k_lo + EPS and s_hi >= k_hi - EPS:
                    continue                    # contained across the strap: spared
                before = along
                along = _subtract(along, a_lo, a_hi)
                if along != before:
                    trims.append({"instance": inst_name, "pin": pname, "net": net, "layer": layer,
                                  "strap_net": s["net"], "strap_k": s["k"],
                                  "strap": [round(s_lo, 3), round(s_hi, 3)],
                                  "along": [round(a_lo, 3), round(a_hi, 3)],
                                  "axis": "x" if vert else "y"})
            for ob in obs_by.get(inst_name, ()):
                if ob["layer"] != s["layer"] or ob["own_power_metal"]:
                    continue
                ox1, oy1, ox2, oy2 = ob["bloated"]
                ox1, oy1, ox2, oy2 = ox1 - sp, oy1 - sp, ox2 + sp, oy2 + sp
                c_lo, c_hi, a_lo, a_hi = (ox1, ox2, oy1, oy2) if vert else (oy1, oy2, ox1, ox2)
                if overlap(c_lo, c_hi, s_lo, s_hi) <= EPS:
                    continue
                before = along
                along = _subtract(along, a_lo, a_hi)
                if along != before:
                    trims.append({"instance": inst_name, "pin": None, "net": None, "layer": s["layer"],
                                  "strap_net": s["net"], "strap_k": s["k"],
                                  "strap": [round(s_lo, 3), round(s_hi, 3)],
                                  "along": [round(a_lo, 3), round(a_hi, 3)],
                                  "axis": "x" if vert else "y", "obs": True})
        for a, b in along:
            if b - a > EPS:
                frags.append({"net": s["net"], "layer": s["layer"], "k": s["k"],
                              "rect": [x1, a, x2, b] if vert else [a, y1, b, y2]})
    return frags, trims


def min_connections(top):
    """How many vias a strap fragment needs to survive `PdnGen::trimShapes`
    (`Shape::isRemovable`): 2, or 1 on a pin layer -- and LibreLane's
    `PDN_ENABLE_PINS` (default on) declares both connect layers pin layers.
    None when the top skips trim (`PDN_SKIPTRIM`): everything survives."""
    g = top["g"]
    if g["PDN_SKIPTRIM"]:
        return None
    return 1 if g["PDN_ENABLE_PINS"] else 2


def predicted_network(top, frags, rects_by, via_min, min_conns=None):
    """The vias pdngen would make, the trim it would apply, and the
    components that leaves, per net.

    Shapes are the cut straps AND every macro power pin on either connect
    layer (`getInstancePins` injects them).  A via is a same-net cross-layer
    overlap of at least `via_min` on both axes (`Grid::getIntersections`),
    counted on the UNTRIMMED fragments the way `updateVias` runs before
    `trimShapes`.  Trim then removes every fragment with fewer vias than
    `min_conns` (None = trim skipped), and on a NON-pin layer (min_conns 2)
    shrinks a survivor to the extent of its vias -- on a pin layer
    `trimShapes` leaves a survivor's shape alone -- after which
    `cleanupVias` drops the vias that lost a shape.  Only THEN is the net
    partitioned (Codex #900: partitioning first let a one-via fragment that
    trim removes bridge a terminal into the grid), by
    `pdn_connect.net_components` -- the same code that reads the written
    DEF, so the prediction and the post-mortem cannot disagree about what
    "one piece of metal" means.  Returns (components-by-net, terminals-by-
    net, trim-by-net) where trim-by-net[net] = {"vias": {frag: n},
    "removed": [frag ...], "kept": [(frag, rect) ...] in the order the
    component member indices count them (fragments first, then pins)}."""
    from pdn_connect import net_components, _BinIndex    # lazy: pdn_connect imports THIS module
    g = top["g"]
    lv, lh = g["PDN_VERTICAL_LAYER"], g["PDN_HORIZONTAL_LAYER"]
    nets = (g["VDD_NET"], g["GND_NET"])
    pins = {n: [] for n in nets}
    terms = {n: [] for n in nets}
    for inst_name, rects in rects_by.items():
        per = {}
        for (net, layer, x1, y1, x2, y2, pname) in rects:
            if layer not in (lv, lh) or net not in pins:
                continue
            pins[net].append((layer, x1, y1, x2, y2))
            per.setdefault((net, pname), []).append((layer, x1, y1, x2, y2))
        for (net, pname), rs in per.items():
            terms[net].append({"name": f"{inst_name}.{pname}", "rects": rs})
    snets, trim = {}, {}
    for net in nets:
        fidx = [i for i, f in enumerate(frags) if f["net"] == net]
        rects = [(frags[i]["layer"], *frags[i]["rect"]) for i in fidx] + pins[net]
        nfr = len(fidx)
        # the vias, on everything: (index_a, index_b, overlap rect)
        low = [(i, r) for i, r in enumerate(rects) if r[0] == lv]
        high = [(i, r) for i, r in enumerate(rects) if r[0] == lh]
        vias = []
        if low and high:
            hrects = [r for _i, r in high]
            idx = _BinIndex(hrects, keyfn=lambda r: r[1:])
            for (ia, (_l, ax1, ay1, ax2, ay2)) in low:
                for j in idx.near(ax1 if idx.axis == 0 else ay1, ax2 if idx.axis == 0 else ay2):
                    ib, (_b, bx1, by1, bx2, by2) = high[j]
                    ox, oy = overlap(ax1, ax2, bx1, bx2), overlap(ay1, ay2, by1, by2)
                    if min(ox, oy) < via_min - EPS:
                        continue
                    vias.append((ia, ib, (max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2))))
        count = {}
        for ia, ib, _r in vias:
            for i in (ia, ib):
                if i < nfr:
                    count[i] = count.get(i, 0) + 1
        removed = set()
        kept_rect = {}
        for p in range(nfr):
            n = count.get(p, 0)
            if min_conns is not None and n < min_conns:
                removed.add(p)
                continue
            r = rects[p]
            if min_conns is not None and min_conns >= 2:
                areas = [vr for ia, ib, vr in vias if p in (ia, ib)]
                # a survivor is shrunk to its vias' extent unless they are one
                # stack (`effectively_vias_stack`: every via area the same)
                if len({tuple(round(v, 6) for v in a) for a in areas}) > 1:
                    r = (r[0], min(a[0] for a in areas), min(a[1] for a in areas),
                         max(a[2] for a in areas), max(a[3] for a in areas))
            kept_rect[p] = r
        order = [p for p in range(nfr) if p not in removed]
        pos = {p: k for k, p in enumerate(order)}
        new_rects = [kept_rect[p] for p in order] + pins[net]
        remap = lambda i: pos[i] if i < nfr else i - nfr + len(order)
        new_vias = [(lv, (vr[0] + vr[2]) / 2, (vr[1] + vr[3]) / 2, "predicted")
                    for ia, ib, vr in vias if ia not in removed and ib not in removed]
        snets[net] = {"rects": new_rects, "vias": new_vias, "pins": []}
        trim[net] = {"vias": {fidx[p]: count.get(p, 0) for p in range(nfr)},
                     "removed": [fidx[p] for p in sorted(removed)],
                     "kept": [(fidx[p], kept_rect[p]) for p in order]}
    return net_components(snets, terms, (lv, lh), with_members=True), terms, trim


def evaluate(top, straps, rects_by, obs_by, spacing, via_min):
    """The whole prediction for one placement: the trims, the network, and
    what PSM would find wrong with it.

    pdngen builds the straps, cuts them (`trim_straps`), makes the vias and
    TRIMS (`predicted_network`): every strap fragment with fewer connections
    than `min_connections` is removed before signoff -- a via-less stub
    between two cuts, a strap at the core edge whose crossings are narrower
    than a via.  What `[PSM-0069]` then fails on is a net whose REMAINING
    shapes are not one connected set: a macro terminal none of whose
    rectangles is on the net's main grid (`stranded`), or a surviving
    fragment on a component off the main grid (`floating`, the "N
    unconnected shapes" PSM counts).  A pin rectangle off the grid whose
    TERMINAL is fed by another of its rectangles is neither -- measured, the
    phase-0 toy passes PSM with two such VGND pin rectangles (a LEF PIN is
    one terminal, however many RECTs it is drawn as).  `failures` is the
    union; a placement passes iff it is empty."""
    frags, trims = trim_straps(top, straps, rects_by, obs_by, spacing)
    min_conns = min_connections(top)
    conn, terms, trim = predicted_network(top, frags, rects_by, via_min, min_conns)
    stranded, floating, trimmed, off_grid_pins = [], [], [], []
    for net, c in conn.items():
        comps = c["components"]
        kept = trim[net]["kept"]
        for p in trim[net]["removed"]:
            f = frags[p]
            trimmed.append({"net": net, "layer": f["layer"], "k": f["k"],
                            "rect": [round(v, 3) for v in f["rect"]], "vias": trim[net]["vias"][p]})
        if not comps:
            continue
        main = comps[0]
        main_frags = sum(1 for i in main["members"] if i < len(kept))
        # a net with no strap left has no grid to be on: every terminal is stranded
        no_grid = main_frags == 0
        fed = set() if no_grid else set(main["terminals"])
        seen_stranded = set()
        for comp in comps[1:] if not no_grid else comps:
            fr = [i for i in comp["members"] if i < len(kept)]
            lost = [t for t in comp["terminals"] if t not in fed and t not in seen_stranded]
            seen_stranded.update(lost)
            for t in lost:
                inst, _, pin = t.rpartition(".")
                stranded.append({"instance": inst, "net": net, "pin": pin, "component": comp["id"],
                                 "component_shapes": comp["shapes"], "component_span": comp["span"],
                                 "surviving_fragments": len(fr)})
            if not fr and not lost:
                off_grid_pins.append({"net": net, "component": comp["id"], "shapes": comp["shapes"],
                                      "terminals": comp["terminals"]})
                continue
            for i in fr:
                p, rect = kept[i]
                f = frags[p]
                floating.append({"net": net, "layer": f["layer"], "k": f["k"],
                                 "rect": [round(v, 3) for v in rect[1:]],
                                 "vias": trim[net]["vias"][p], "component": comp["id"],
                                 "terminals": comp["terminals"]})
        # terminals the strap grid never reaches at all (no component holds them)
        held = {t for comp in comps for t in comp["terminals"]}
        for t in terms[net]:
            if t["name"] not in held and t["name"] not in seen_stranded:
                inst, _, pin = t["name"].rpartition(".")
                seen_stranded.add(t["name"])
                stranded.append({"instance": inst, "net": net, "pin": pin, "component": None,
                                 "component_shapes": 0, "component_span": 0.0, "surviving_fragments": 0})
    failures = ([{"kind": "stranded", **x} for x in stranded]
                + [{"kind": "floating", **x} for x in floating])
    return {"frags": frags, "trims": trims, "network": conn, "stranded": stranded,
            "floating": floating, "trimmed": trimmed, "off_grid_pins": off_grid_pins,
            "min_connections": min_conns, "failures": failures}


def shifted(rects, dx, dy):
    return [(n, l, x1 + dx, y1 + dy, x2 + dx, y2 + dy, p) for (n, l, x1, y1, x2, y2, p) in rects]


def shifted_obs(obs, dx, dy):
    out = []
    for ob in obs:
        o = dict(ob)
        o["rect"] = [ob["rect"][0] + dx, ob["rect"][1] + dy, ob["rect"][2] + dx, ob["rect"][3] + dy]
        o["bloated"] = [ob["bloated"][0] + dx, ob["bloated"][1] + dy,
                        ob["bloated"][2] + dx, ob["bloated"][3] + dy]
        out.append(o)
    return out


def clean_at(top, straps, rects_by, obs_by, spacing, via_min, dx, dy):
    """No predicted PSM failure after every macro moves by (dx, dy) --
    equivalently, after the straps move by (-dx, -dy), which is what a
    PDN_*OFFSET change does.  The verdict is global by nature: which
    fragment a strap breaks into depends on every macro it passes, so a
    per-instance shift has no well-defined question to ask; the remedy
    offered is the one shift of the whole set."""
    r_by = {n: shifted(r, dx, dy) for n, r in rects_by.items()}
    o_by = {n: shifted_obs(o, dx, dy) for n, o in obs_by.items()}
    return not evaluate(top, straps, r_by, o_by, spacing, via_min)["failures"]


def shift_candidates(rects_list, obs_list, top, vstraps, hstraps, spacing, via_min, axis):
    """The dx (axis x) or dy (axis y) values at which some constraint changes
    state -- a pin edge meeting a strap edge plus spacing, or a crossing
    reaching via size -- for the given instances' rects.  The smallest
    clearing shift is one of these (or 0), so they are what gets tried."""
    g = top["g"]
    lv, lh = g["PDN_VERTICAL_LAYER"], g["PDN_HORIZONTAL_LAYER"]
    same_layer, cross_layer, straps = (lv, lh, vstraps) if axis == "x" else (lh, lv, hstraps)
    cands = {0.0}
    for obs in obs_list:
        for ob in obs:
            if ob["own_power_metal"]:
                continue
            layer = ob["layer"]
            x1, y1, x2, y2 = ob["bloated"]
            lo, hi = (x1, x2) if axis == "x" else (y1, y2)
            if layer not in (same_layer, cross_layer):
                continue
            if layer == same_layer:                  # plus the strap's own spacing, as the cut reads it
                sp = spacing.get(layer, 0.0)
                lo, hi = lo - sp, hi + sp
            for s in (straps if layer == same_layer else
                      (hstraps if straps is vstraps else vstraps)):
                cands.add(s["lo"] - hi)
                cands.add(s["hi"] - lo)
    for rects in rects_list:
        for (net, layer, x1, y1, x2, y2, _) in rects:
            lo, hi = (x1, x2) if axis == "x" else (y1, y2)
            if layer == same_layer:
                # every same-layer meeting is a trim, whatever the nets, so
                # both nets' straps contribute the same CLEARING candidates:
                # the pin edge two spacings from the strap edge (`pin_cutter`)
                sp = 2 * spacing.get(layer, 0.0)
                for s in straps:
                    cands.add(s["lo"] - sp - hi)
                    cands.add(s["hi"] + sp - lo)
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


MAX_SHIFT_TRIALS = 400          # single-axis trials, and the pair search's budget
MAX_TRIM_LINES = 40             # TRIM lines on the terminal; every one is in --json


def axis_candidates(top, rects_by, obs_by, spacing, via_min, axis, limit, vstraps, hstraps):
    insts = list(rects_by)
    cands = shift_candidates([rects_by[i] for i in insts], [obs_by[i] for i in insts],
                             top, vstraps, hstraps, spacing, via_min, axis)
    return [d for d in cands if abs(d) <= limit + EPS]


def smallest_shift(top, straps, rects_by, obs_by, spacing, via_min, axis, limit, vstraps, hstraps):
    """The smallest |shift| along `axis` alone (within +-limit) after which
    nothing is predicted to fail; None when no candidate within the limit
    does it.  Candidates are the offsets at which some pin edge meets some
    strap edge plus spacing, or a crossing reaches via size -- the smallest
    clearing shift is one of those or 0."""
    for d in axis_candidates(top, rects_by, obs_by, spacing, via_min, axis, limit, vstraps, hstraps)[:MAX_SHIFT_TRIALS]:
        dx, dy = (d, 0.0) if axis == "x" else (0.0, d)
        if clean_at(top, straps, rects_by, obs_by, spacing, via_min, dx, dy):
            return d
    return None


def smallest_pair(top, straps, rects_by, obs_by, spacing, via_min, xlimit, ylimit, vstraps, hstraps):
    """A (dx, dy) that clears a placement failing on BOTH axes, where neither
    axis alone can: the candidates of each axis, smallest first, tried as
    pairs by increasing |dx|+|dy| within the trial budget.  None when the
    budget finds nothing -- a placement or pitch change is then the remedy."""
    xs = axis_candidates(top, rects_by, obs_by, spacing, via_min, "x", xlimit, vstraps, hstraps)
    ys = axis_candidates(top, rects_by, obs_by, spacing, via_min, "y", ylimit, vstraps, hstraps)
    n = max(1, int(math.sqrt(MAX_SHIFT_TRIALS)))
    pairs = sorted(((dx, dy) for dx in xs[:n] for dy in ys[:n]), key=lambda p: (abs(p[0]) + abs(p[1]), p))
    for dx, dy in pairs[:MAX_SHIFT_TRIALS]:
        if clean_at(top, straps, rects_by, obs_by, spacing, via_min, dx, dy):
            return dx, dy
    return None


def run_check(top, lefs, spacing=None, via_min=VIA_MIN):
    spacing = dict(MIN_SPACING, **(spacing or {}))
    g = top["g"]
    lv, lh = g["PDN_VERTICAL_LAYER"], g["PDN_HORIZONTAL_LAYER"]
    vstraps, hstraps = top_straps(top)
    straps = strap_rects(top, vstraps, hstraps)
    for inst in top["instances"]:
        if inst["cell"] not in lefs:
            raise InputShape(f"no LEF defines MACRO {inst['cell']} (instance {inst['name']}); LEFs read: "
                             f"{sorted(lefs)}")
    rects_by, obs_by, n_rects, sealed, own_power_by = {}, {}, 0, [], {}
    for inst in top["instances"]:
        macro = lefs[inst["cell"]]
        rects = instance_rects(inst, macro, top)
        if not rects:
            raise InputShape(f"MACRO {inst['cell']}: no {g['VDD_NET']}/{g['GND_NET']} pin rectangles in its "
                             f"LEF -- a macro with no power pins cannot be fed; is this the FINAL lef?")
        obs = instance_obstructions(inst, macro, top, spacing, rects)
        rects_by[inst["name"]], obs_by[inst["name"]] = rects, obs
        n_rects += len(rects)
        foreign = {ob["layer"] for ob in obs if not ob["own_power_metal"]}
        if lv in foreign and lh in foreign:
            sealed.append({"instance": inst["name"], "cell": inst["cell"]})
        own = {}
        for ob in obs:
            if ob["own_power_metal"]:
                own[ob["layer"]] = own.get(ob["layer"], 0) + 1
        own_power_by[inst["name"]] = own
    ev = evaluate(top, straps, rects_by, obs_by, spacing, via_min)
    stranded = ev["stranded"]
    unconnected = sorted({(s["instance"], s["net"]) for s in stranded})
    unconnected = [{"instance": i, "net": n} for i, n in unconnected]
    per_inst = []
    for inst in top["instances"]:
        n = inst["name"]
        con = {net: not any(s["instance"] == n and s["net"] == net for s in stranded)
               for net in (g["VDD_NET"], g["GND_NET"])}
        foreign = sorted({ob["layer"] for ob in obs_by[n] if not ob["own_power_metal"]})
        per_inst.append({"instance": n, "cell": inst["cell"],
                         "trims": sum(1 for t in ev["trims"] if t["instance"] == n),
                         "connected": con, "obstructed": foreign, "own_power_obs": own_power_by[n]})
    net_rows = {}
    for net, c in ev["network"].items():
        comps = c["components"]
        net_rows[net] = {"straps": sum(1 for st in straps if st["net"] == net),
                         "fragments": sum(1 for f in ev["frags"] if f["net"] == net),
                         "components": len(comps),
                         "main_shapes": comps[0]["shapes"] if comps else 0,
                         "terminals": len(ev_terms(ev, net)),
                         "terminals_on_grid": len(ev_terms(ev, net)) - sum(1 for s in stranded if s["net"] == net),
                         "trimmed_away": sum(1 for f in ev["trimmed"] if f["net"] == net),
                         "floating": sum(1 for f in ev["floating"] if f["net"] == net),
                         "bridged_by": c["bridged_by"]}
    result = {"config": top["path"], "instances": len(top["instances"]), "pin_rects": n_rects,
              "trims": ev["trims"], "unconnected": unconnected, "stranded": stranded,
              "floating": ev["floating"], "trimmed_away": ev["trimmed"], "off_grid_pins": ev["off_grid_pins"],
              "min_connections": ev["min_connections"], "failures": ev["failures"],
              "network": net_rows, "per_instance": per_inst, "sealed": sealed,
              "used_defaults": top["used_defaults"], "vstraps": len(vstraps), "hstraps": len(hstraps),
              "pass": not ev["failures"]}
    if not result["pass"]:
        args = (top, straps, rects_by, obs_by, spacing, via_min)
        xl, yl = g["PDN_VPITCH"] / 2, g["PDN_HPITCH"] / 2
        dx = smallest_shift(*args, "x", xl, vstraps, hstraps)
        dy = smallest_shift(*args, "y", yl, vstraps, hstraps)
        result["global_dx"], result["global_dy"] = dx, dy
        # one axis alone when either does it (the smaller move); a PAIR only
        # when neither does -- and then a searched one, since a design failing
        # on both axes passes neither single search (Codex #885)
        singles = [(abs(d), (d, 0.0) if ax == "x" else (0.0, d))
                   for ax, d in (("x", dx), ("y", dy)) if d is not None]
        pair = min(singles)[1] if singles else smallest_pair(*args, xl, yl, vstraps, hstraps)
        result["global_shift"] = list(pair) if pair else None
        if pair:
            result["voffset_for_shift"] = round((g["PDN_VOFFSET"] - pair[0]) % g["PDN_VPITCH"], 3)
            result["hoffset_for_shift"] = round((g["PDN_HOFFSET"] - pair[1]) % g["PDN_HPITCH"], 3)
        # the offered shift is VERIFIED before it is offered (Codex #885)
        result["global_clean_at_shift"] = bool(pair) and clean_at(*args, *pair)
    return result


def ev_terms(ev, net):
    """The terminals of `net` the evaluation saw: the union of every
    component's terminal list plus the ones no component holds."""
    names = {t for c in ev["network"][net]["components"] for t in c["terminals"]}
    names |= {f"{s['instance']}.{s['pin']}" for s in ev["stranded"] if s["net"] == net}
    return names


ADVISORY = (
    "  ADVISORY: a PREDICTION of pdngen's own steps -- cut, via, trim -- and of what PSM then finds\n"
    "  unconnected, not the verdict.  The verdict is OpenROAD's own PSM check at the end of the top\n"
    "  run; pdn_connect.py on the DEF pdngen wrote is the post-mortem, and it uses the same network\n"
    "  code, so where the two disagree the prediction's INPUT is what to look at.  A TRIM is not a\n"
    "  defect (it is how pdngen keeps straps off macro pins); a via-less fragment is trimmed away,\n"
    "  not stranded.  Editing PDN_*OFFSET on the old check's say-so is what produced PSM-0069 once."
)


def report(top, lefs, res, out=sys.stdout):
    g, core = top["g"], top["core"]
    lv, lh = g["PDN_VERTICAL_LAYER"], g["PDN_HORIZONTAL_LAYER"]
    p = lambda *a: print(*a, file=out)
    p(f"pdn_phase: {top['path']}: die {top['die']}, core {[round(v, 3) for v in core]}")
    p(ADVISORY)
    p(f"  {lv} straps: {res['vstraps']} at x = {core[0]:.3f} + {g['PDN_VOFFSET']} + k*{g['PDN_VPITCH']}"
      f" (VPWR first, VGND +{g['PDN_VWIDTH'] + g['PDN_VSPACING']:.2f}; width {g['PDN_VWIDTH']})")
    p(f"  {lh} straps: {res['hstraps']} at y = {core[1]:.3f} + {g['PDN_HOFFSET']} + k*{g['PDN_HPITCH']}"
      f" (width {g['PDN_HWIDTH']}, VGND +{g['PDN_HWIDTH'] + g['PDN_HSPACING']:.2f})")
    mc = res.get("min_connections")
    if mc is None:
        p("  trim: SKIPPED (PDN_SKIPTRIM) -- every strap fragment survives, via-less ones included")
    else:
        p(f"  trim: a strap fragment with fewer than {mc} via{'s' if mc > 1 else ''} is removed "
          f"(`PdnGen::trimShapes`; PDN_ENABLE_PINS {'on' if g['PDN_ENABLE_PINS'] else 'off'})")
    own = {}
    for r in res["per_instance"]:
        for layer, n in (r.get("own_power_obs") or {}).items():
            own[layer] = own.get(layer, 0) + n
    if own:
        p("  own-power OBS (the block's grid, the same metal as its pins -- the pin rule below governs "
          "it): " + ", ".join(f"{l}: {n} rects" for l, n in sorted(own.items())))
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
        obs_layers = sorted({l for (l, *_r) in m.get("obs", [])})
        p(f"  MACRO {cell}: {m['size'][0]} x {m['size'][1]}, power-pin rects " +
          ", ".join(f"{n}/{l}: {c}" for (n, l), c in sorted(counts.items())) +
          (f"; OBS on {', '.join(obs_layers)}" if obs_layers else "; no OBS"))
    seen = set()
    for r in res["per_instance"]:
        for layer in r["obstructed"]:
            if (r["cell"], layer) in seen:
                continue
            seen.add((r["cell"], layer))
            p(f"OBSTRUCTED {r['cell']} (e.g. {r['instance']}) on {layer}: an OBS rectangle that is NOT "
              f"the block's own power metal, so no phase search cleared it; bloated by the macro grid's "
              f"halo it removes {layer} straps over this macro (`Shape::cut` against "
              f"`InstanceGrid::getInstanceObstructions`).  Signal routing on a PDN layer does this -- "
              f"cap the block below it (RT_MAX_LAYER) or keep that layer for the block's own grid")
    trims = res["trims"]
    for t in trims[:MAX_TRIM_LINES]:
        what = (f"OBS" if t.get("obs") else f"{t['net']} pin {t['pin']}")
        p(f"TRIM {t['instance']} {what} on {t['layer']} cuts {t['strap_net']} strap k={t['strap_k']} "
          f"[{t['strap'][0]:.3f},{t['strap'][1]:.3f}] over {'y' if t['axis'] == 'x' else 'x'} "
          f"[{t['along'][0]:.3f},{t['along'][1]:.3f}]"
          + ("" if t.get("obs") else " (spacing across, halo along)"))
    if len(trims) > MAX_TRIM_LINES:
        p(f"  ... {len(trims) - MAX_TRIM_LINES} more trims (all in --json)")
    for net, row in res["network"].items():
        p(f"  {net}: {row['straps']} straps -> {row['fragments']} fragments after the cuts; main grid "
          f"{row['main_shapes']} shapes; {row['trimmed_away']} via-less fragment(s) trimmed away; "
          f"{row['floating']} surviving off the grid; terminals on the grid "
          f"{row['terminals_on_grid']}/{row['terminals']}"
          + (f"; {len(row['bridged_by'])} terminal(s) bridge two components (not credited)"
             if row["bridged_by"] else ""))
    for s_ in res["stranded"]:
        where = ("no strap of that net reaches any rectangle of it" if s_["component"] is None else
                 f"its component ({s_['component_shapes']} shapes, span {s_['component_span']:.3f}) is off "
                 f"the main grid" + (f", {s_['surviving_fragments']} of its fragments survive trim"
                                     if s_["surviving_fragments"] else ", and every strap fragment on it is "
                                     "via-less and trimmed away"))
        p(f"STRANDED {s_['instance']} {s_['net']} pin {s_['pin']}: {where}")
    for f in res["floating"]:
        p(f"FLOATING {f['net']} {f['layer']} fragment k={f['k']} [{f['rect'][0]:.3f},{f['rect'][1]:.3f},"
          f"{f['rect'][2]:.3f},{f['rect'][3]:.3f}] with {f['vias']} via(s): survives trim on a component "
          f"off the main grid -- a shape PSM reports unconnected"
          + (f" (component carries {', '.join(f['terminals'][:4])})" if f["terminals"] else ""))
    for sd in res["sealed"]:
        p(f"  {sd['instance']} ({sd['cell']}) carries foreign metal on BOTH {lv} and {lh}: nothing can "
          f"reach it. Cap the block below them (`RT_MAX_LAYER`) so those layers hold only its own "
          f"power grid, which the phase search can clear")
    n_trim_inst = len({t["instance"] for t in trims})
    if res["pass"]:
        p(f"PASS: {res['instances']} instances, {res['pin_rects']} power-pin rects, {len(trims)} trims "
          f"in {n_trim_inst} instances, every terminal on its net's grid, no surviving fragment off it "
          f"({len(res['trimmed_away'])} via-less trimmed away)")
        return
    line = (f"FAIL: {res['instances']} instances, {len(trims)} trims in {n_trim_inst} instances, "
            f"{len(res['stranded'])} stranded terminal(s) in {len(res['unconnected'])} instance-net(s), "
            f"{len(res['floating'])} floating fragment(s)")
    sh = res.get("global_shift")
    if sh and res.get("global_clean_at_shift"):
        parts = []
        if sh[0]:
            parts.append(f"dx={sh[0]:+.3f} (PDN_VOFFSET={res['voffset_for_shift']})")
        if sh[1]:
            parts.append(f"dy={sh[1]:+.3f} (PDN_HOFFSET={res['hoffset_for_shift']})")
        line += ("; shifting EVERY macro by " + " and ".join(parts) + " -- equivalently the strap "
                 "offset(s) named -- leaves nothing predicted to fail"
                 + (" (both axes needed: neither alone does)" if sh[0] and sh[1] else ""))
    else:
        line += ("; no shift within half a pitch on either axis, nor a pair within the trial budget, "
                 "clears it -- a placement or pitch change is needed")
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
