#!/usr/bin/env python3
"""The PDN-phase check of the hierarchical arm: run it AFTER the blocks are
hardened and BEFORE the top (docs/internal/librelane_hier_flow.md §8 step 4
and §9, phase 1).

    pdn_phase.py <top config.json> [<cell.lef> ...] [--json out.json]

A macro is fed by the top's power straps, and pdngen never SHORTS a strap to
a macro's power pin -- it CUTS the strap.  Two things cut it, and neither is
visible before IR-drop signoff (`[PSM-0069] Check connectivity failed`,
measured on the phase-0 toy), which is the LAST step of the run:

  * a same-layer MEETING: where a strap comes within spacing of any power pin
    of the macro, `Shape::cut` (OpenROAD src/pdn/src/shape.cpp) removes the
    strap over the macro.  The same-net exception spares it only when the
    strap CONTAINS the pin across its width, which a 2 um block pin inside a
    1.6 um top strap never is -- so a same-layer overlap is a clip whatever
    the two nets are, and never a connection;
  * the macro's own OBSTRUCTION: LibreLane writes each hardened block's
    abstract LEF with `write_abstract_lef -bloat_occupied_layers`
    (`OPENROAD_LEF_BLOAT_OCCUPIED_LAYERS`, default True), a WHOLE-BLOCK cover
    rectangle per occupied layer, which pdngen bloats by the macro halo
    (`InstanceGrid::getInstanceObstructions`) and subtracts -- so EVERY strap
    on that layer is gone over the macro and its halo.  Which layers are
    covered is read from the file rather than assumed: the writer
    (`lefout::getObstructions`) collects special wires with no filter for the
    power nets, which would obstruct the PDN layers on every block, while the
    first real run saw a met4 OBS only on the block that ROUTES on met4.  That
    is exactly why this reads the LEF instead of predicting it.

What is left to feed the macro is the CROSS-layer crossing that the macro
grid's `add_pdn_connect -layers {met4 met5}` turns into vias -- on a strap
that survived.  So this reads the hardened block's final LEF (its VPWR/VGND
pin rectangles AND its OBS blocks) and the top's PDN config (pitch, offset,
width, spacing, layers, core margins, halo), places every macro instance the
way `Odb.ManualMacroPlacement` will, and reports:

  * every (instance, pin rect, strap) CLIP -- a strap this macro's pin cuts
    (suppressed where the macro's OBS has already removed that strap: pdngen
    got there first);
  * every layer a macro leaves OBSTRUCTED, since no strap on it can feed that
    macro -- and a macro that obstructs BOTH PDN layers is unreachable, whose
    remedy is named (harden the block with `PDN_MULTILAYER` false, LibreLane's
    own setting for a macro meant for integration);
  * every (instance, net) with NO connection -- no surviving strap of that net
    on the other layer crosses a pin of that net with room for a via (a strap
    that misses a pin by 0.66 um is the second way the toy failed);
  * per instance, and for the whole placement, the SMALLEST x-shift (and
    y-shift for the horizontal straps) that clears it, the whole-placement
    one restated as the PDN_VOFFSET/PDN_HOFFSET that would do the same.

PASS looks like `PASS: <n> instances, <m> power-pin rects, 0 clips, every
instance connected on VPWR and VGND` and exit 0.  Any clip or unconnected net
is a FAIL (exit 1) with every offender listed; an input of a shape this did
not expect -- a config without DIE_AREA, a LEF without the macro, a power pin
or an obstruction drawn as a POLYGON, an instance with no location -- is
exit 2, because a check that guessed would be worse than none.

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


def instance_obstructions(inst, macro, top, spacing):
    """The instance's LEF OBS rectangles in TOP coordinates, bloated the way
    pdngen bloats them (`InstanceGrid::getInstanceObstructions`): by the
    layer's minimum spacing and by the macro halo, the union of the two."""
    g = top["g"]
    hx, hy = g["FP_MACRO_HORIZONTAL_HALO"], g["FP_MACRO_VERTICAL_HALO"]
    w, h = macro["size"]
    out = []
    for (layer, x1, y1, x2, y2) in macro.get("obs", []):
        r = orient_rect((x1, y1, x2, y2), inst["orient"], w, h)
        bx, by = max(spacing.get(layer, 0.0), hx), max(spacing.get(layer, 0.0), hy)
        out.append((layer, r[0] + inst["x"] - bx, r[1] + inst["y"] - by,
                    r[2] + inst["x"] + bx, r[3] + inst["y"] + by))
    return out


def eval_instance(inst, rects, obs, top, vstraps, hstraps, spacing, via_min):
    """(clips, connected-by-net, connections, obstructed-layers) for one placed
    instance; `rects` and `obs` already in top coordinates.

    pdngen never SHORTS two nets -- it CUTS.  Where a macro's power pin or its
    OBS meets a top strap on the same layer, `Shape::cut` removes the strap
    over the macro, and the same-net exception spares it only when the strap
    CONTAINS the pin across its width (a 2 um block pin never fits inside a
    1.6 um top strap, and an obstruction copied into a grid carries no net at
    all).  So a same-layer meeting is a CLIP whatever the nets are, never a
    connection, and what actually feeds a macro is the CROSS-layer crossing
    that `add_pdn_connect -layers {met4 met5}` vias -- on a strap that is
    still there to cross it.
    """
    g, core = top["g"], top["core"]
    lv, lh = g["PDN_VERTICAL_LAYER"], g["PDN_HORIZONTAL_LAYER"]
    clips, connections = [], []
    connected = {g["VDD_NET"]: False, g["GND_NET"]: False}
    cut = {lv: set(), lh: set()}
    obs_cut = {lv: set(), lh: set()}
    obstructed = {}

    # (a) the straps this macro removes over itself, and why
    for (layer, ox1, oy1, ox2, oy2) in obs:
        if layer not in cut:
            continue
        straps, lo, hi = (vstraps, ox1, ox2) if layer == lv else (hstraps, oy1, oy2)
        hit = [j for j, s in enumerate(straps) if overlap(s["lo"], s["hi"], lo, hi) > EPS]
        cut[layer].update(hit)
        obs_cut[layer].update(hit)
        if hit:
            obstructed[layer] = {"rect": [round(v, 3) for v in (ox1, oy1, ox2, oy2)], "straps": len(hit)}
    for (net, layer, x1, y1, x2, y2, pname) in rects:
        if layer not in cut:
            continue
        if layer == lv and overlap(y1, y2, core[1], core[3]) <= 0:
            continue
        if layer == lh and overlap(x1, x2, core[0], core[2]) <= 0:
            continue
        sp = spacing.get(layer, 0.0)
        straps, lo, hi = (vstraps, x1, x2) if layer == lv else (hstraps, y1, y2)
        for j, s in enumerate(straps):
            ov = overlap(lo, hi, s["lo"], s["hi"])
            if ov > -sp + EPS:
                cut[layer].add(j)
                if j in obs_cut[layer]:
                    continue          # the OBS already removed this strap here
                clips.append({"instance": inst["name"], "pin": pname, "net": net, "layer": layer,
                              "rect": [x1, y1, x2, y2], "strap_net": s["net"], "strap_k": s["k"],
                              "strap": [s["lo"], s["hi"]], "overlap": round(ov, 4),
                              "axis": "x" if layer == lv else "y"})

    # (b) what still feeds it: a SURVIVING strap of the same net on the other layer
    for (net, layer, x1, y1, x2, y2, pname) in rects:
        if layer == lv:
            other, straps = lh, hstraps
        elif layer == lh:
            other, straps = lv, vstraps
        else:
            continue
        for j, s in enumerate(straps):
            if s["net"] != net or j in cut[other]:
                continue
            if other == lh:      # horizontal strap: spans the core in x
                ovx = min(x2, core[2]) - max(x1, core[0])
                ovy = overlap(y1, y2, s["lo"], s["hi"])
            else:                # vertical strap: spans the core in y
                ovx = overlap(x1, x2, s["lo"], s["hi"])
                ovy = min(y2, core[3]) - max(y1, core[1])
            if min(ovx, ovy) >= via_min - EPS:
                connected[net] = True
                connections.append((inst["name"], pname, layer, "crossed by", s["net"], s["k"], other))
    return clips, connected, connections, obstructed


def shifted(rects, dx, dy):
    return [(n, l, x1 + dx, y1 + dy, x2 + dx, y2 + dy, p) for (n, l, x1, y1, x2, y2, p) in rects]


def shifted_obs(obs, dx, dy):
    return [(l, x1 + dx, y1 + dy, x2 + dx, y2 + dy) for (l, x1, y1, x2, y2) in obs]


def clean_at(inst, rects, obs, top, vstraps, hstraps, spacing, via_min, dx, dy):
    clips, con, _, _ = eval_instance(inst, shifted(rects, dx, dy), shifted_obs(obs, dx, dy),
                                     top, vstraps, hstraps, spacing, via_min)
    return not clips and all(con.values())


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
        for (layer, x1, y1, x2, y2) in obs:
            lo, hi = (x1, x2) if axis == "x" else (y1, y2)
            if layer not in (same_layer, cross_layer):
                continue
            for s in (straps if layer == same_layer else
                      (hstraps if straps is vstraps else vstraps)):
                cands.add(s["lo"] - hi)
                cands.add(s["hi"] - lo)
    for rects in rects_list:
        for (net, layer, x1, y1, x2, y2, _) in rects:
            lo, hi = (x1, x2) if axis == "x" else (y1, y2)
            if layer == same_layer:
                # every same-layer meeting is a clip now, whatever the nets, so
                # both nets' straps contribute the same CLEARING candidates
                sp = spacing.get(layer, 0.0)
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


def smallest_shift(insts, rects_by, obs_by, top, vstraps, hstraps, spacing, via_min, axis, limit):
    """The smallest |shift| along `axis` (within +-limit) after which EVERY
    instance in `insts` is collision-free on both layers and connected on
    both nets; None when no candidate within the limit does it."""
    cands = shift_candidates([rects_by[i["name"]] for i in insts], [obs_by[i["name"]] for i in insts],
                             top, vstraps, hstraps, spacing, via_min, axis)
    for d in cands:
        if abs(d) > limit + EPS:
            break
        dx, dy = (d, 0.0) if axis == "x" else (0.0, d)
        if all(clean_at(i, rects_by[i["name"]], obs_by[i["name"]], top, vstraps, hstraps,
                        spacing, via_min, dx, dy) for i in insts):
            return d
    return None


def run_check(top, lefs, spacing=None, via_min=VIA_MIN):
    spacing = dict(MIN_SPACING, **(spacing or {}))
    g = top["g"]
    lv, lh = g["PDN_VERTICAL_LAYER"], g["PDN_HORIZONTAL_LAYER"]
    vstraps, hstraps = top_straps(top)
    for inst in top["instances"]:
        if inst["cell"] not in lefs:
            raise InputShape(f"no LEF defines MACRO {inst['cell']} (instance {inst['name']}); LEFs read: "
                             f"{sorted(lefs)}")
    rects_by, obs_by, per_inst, all_clips, unconnected, n_rects = {}, {}, [], [], [], 0
    sealed = []
    for inst in top["instances"]:
        macro = lefs[inst["cell"]]
        rects = instance_rects(inst, macro, top)
        if not rects:
            raise InputShape(f"MACRO {inst['cell']}: no {g['VDD_NET']}/{g['GND_NET']} pin rectangles in its "
                             f"LEF -- a macro with no power pins cannot be fed; is this the FINAL lef?")
        obs = instance_obstructions(inst, macro, top, spacing)
        rects_by[inst["name"]], obs_by[inst["name"]] = rects, obs
        n_rects += len(rects)
        clips, con, cons, obstructed = eval_instance(inst, rects, obs, top, vstraps, hstraps, spacing, via_min)
        all_clips.extend(clips)
        for net, ok in con.items():
            if not ok:
                unconnected.append({"instance": inst["name"], "net": net})
        if lv in obstructed and lh in obstructed:
            sealed.append({"instance": inst["name"], "cell": inst["cell"]})
        row = {"instance": inst["name"], "cell": inst["cell"], "clips": len(clips),
               "connected": con, "connections": len(cons),
               "obstructed": sorted(obstructed)}
        if clips or not all(con.values()):
            row["dx"] = smallest_shift([inst], rects_by, obs_by, top, vstraps, hstraps, spacing, via_min,
                                       "x", g["PDN_VPITCH"] / 2)
            row["dy"] = smallest_shift([inst], rects_by, obs_by, top, vstraps, hstraps, spacing, via_min,
                                       "y", g["PDN_HPITCH"] / 2)
        per_inst.append(row)
    result = {"config": top["path"], "instances": len(top["instances"]), "pin_rects": n_rects,
              "clips": all_clips, "unconnected": unconnected, "per_instance": per_inst,
              "sealed": sealed, "used_defaults": top["used_defaults"],
              "vstraps": len(vstraps), "hstraps": len(hstraps),
              "pass": not all_clips and not unconnected}
    if not result["pass"]:
        insts = top["instances"]
        dx = smallest_shift(insts, rects_by, obs_by, top, vstraps, hstraps, spacing, via_min, "x",
                            g["PDN_VPITCH"] / 2)
        dy = smallest_shift(insts, rects_by, obs_by, top, vstraps, hstraps, spacing, via_min, "y",
                            g["PDN_HPITCH"] / 2)
        result["global_dx"], result["global_dy"] = dx, dy
        if dx is not None:
            result["voffset_for_dx"] = round((g["PDN_VOFFSET"] - dx) % g["PDN_VPITCH"], 3)
        if dy is not None:
            result["hoffset_for_dy"] = round((g["PDN_HOFFSET"] - dy) % g["PDN_HPITCH"], 3)
    return result


def report(top, lefs, res, out=sys.stdout):
    g, core = top["g"], top["core"]
    lv, lh = g["PDN_VERTICAL_LAYER"], g["PDN_HORIZONTAL_LAYER"]
    p = lambda *a: print(*a, file=out)
    p(f"pdn_phase: {top['path']}: die {top['die']}, core {[round(v, 3) for v in core]}")
    p(f"  {lv} straps: {res['vstraps']} at x = {core[0]:.3f} + {g['PDN_VOFFSET']} + k*{g['PDN_VPITCH']}"
      f" (VPWR first, VGND +{g['PDN_VWIDTH'] + g['PDN_VSPACING']:.2f}; width {g['PDN_VWIDTH']})")
    p(f"  {lh} straps: {res['hstraps']} at y = {core[1]:.3f} + {g['PDN_HOFFSET']} + k*{g['PDN_HPITCH']}"
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
            p(f"OBSTRUCTED {r['cell']} (e.g. {r['instance']}) on {layer}: the block's own OBS, bloated by "
              f"the macro halo, removes every {layer} strap over it -- pdngen cuts them "
              f"(`Shape::cut` against `InstanceGrid::getInstanceObstructions`), so no {layer} strap "
              f"can feed this macro")
    for c in res["clips"]:
        p(f"CLIP {c['instance']} {c['net']} pin {c['pin']} on {c['layer']} "
          f"[{c['rect'][0]:.3f},{c['rect'][2]:.3f}]x[{c['rect'][1]:.3f},{c['rect'][3]:.3f}] "
          f"cuts {c['strap_net']} strap k={c['strap_k']} [{c['strap'][0]:.3f},{c['strap'][1]:.3f}] "
          f"(overlap {c['overlap']:.3f} um along {c['axis']}; spacing counted)")
    for u in res["unconnected"]:
        p(f"UNCONNECTED {u['instance']} {u['net']}: no surviving strap of that net on the other layer "
          f"crosses a pin of that net with room for a via")
    for sd in res["sealed"]:
        p(f"  {sd['instance']} ({sd['cell']}) obstructs BOTH {lv} and {lh}: nothing can reach it. "
          f"Harden the block with PDN_MULTILAYER false -- LibreLane's own setting for a macro meant "
          f"for integration -- so it draws no {lh}, and the top's {lh} straps stay whole over it")
    bad = [r for r in res["per_instance"] if r["clips"] or not all(r["connected"].values())]
    for r in bad:
        fmt = lambda v: "none within half a pitch" if v is None else f"{v:+.3f} um"
        p(f"  {r['instance']}: smallest x-shift {fmt(r['dx'])}, smallest y-shift {fmt(r['dy'])}")
    if res["pass"]:
        p(f"PASS: {res['instances']} instances, {res['pin_rects']} power-pin rects, 0 clips, "
          f"every instance connected on {g['VDD_NET']} and {g['GND_NET']}")
    else:
        line = (f"FAIL: {res['instances']} instances, {len(res['clips'])} clips in "
                f"{len({c['instance'] for c in res['clips']})} instances, "
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
