#!/usr/bin/env python3
"""What pdngen ACTUALLY connected, read off its own output DEF.

    pdn_connect.py <pdn.def> [<cell.lef> ...] [--layers met4,met5] [--json out.json]

`pdn_phase.py` PREDICTS, from the config and the block LEFs, before the top
runs.  This one is its post-mortem twin: it reads the DEF pdngen WROTE and
reports, per macro power pin, whether pdngen put a via on it -- and, where it
did not, whether the pin had anything to connect to.  Nothing here is
modelled.  Every strap cut, every obstruction subtraction, every halo has
already happened by the time this metal is in the file, so a reader of the
output needs no theory of any of them; that is the whole reason to run it.

WHAT PDNGEN'S CONNECTION MECHANISM IS (OpenROAD src/pdn/src, read 2026-09-07,
because the premise everything here rested on turned out to be untested):

  * `Grid::getIntersections` (grid.cpp:573) emits a via wherever a same-net
    shape on an `add_pdn_connect` pair's LOWER layer OVERLAPS a same-net shape
    on its UPPER layer.  That is the entire rule.  It is a CROSS-LAYER
    relation; "a strap of the same net running over the pin on the pin's own
    layer" plays no part in it, which is why a macro pin with no strap above
    it on its own layer connects perfectly well.
  * `InstanceGrid::getInstancePins` (grid.cpp:1609) injects the macro's own
    pins as fixed shapes on their own layers, and `InstanceGrid::
    getIntersections` (:1654) merges them into the search set -- so a macro
    pin participates as an ordinary shape, and a macro's met4 pin can be the
    partner that connects its met5 pin.
  * `Grid::makeVias` (:827) pulls into the macro's search area every shape
    from every OTHER grid.  The macro grid connects using the CORE grid's
    straps.
  * LibreLane's macro grid draws no metal at all: `pdn_cfg.tcl:185` is
    `define_pdn_grid -macro -default -name macro -starts_with POWER -halo ...`
    followed by exactly one `add_pdn_connect -grid macro -layers
    "$PDN_VERTICAL_LAYER $PDN_HORIZONTAL_LAYER"`, and no `add_pdn_stripe`.

So the pair passed as `--layers` IS the connect statement, and the question
this script asks of every power pin on one of those two layers is the
question pdngen asks: is there a same-net shape on the OTHER one overlapping
it, and did a via land there.

Four verdicts per pin rectangle:

  connected        a via of the pin's net sits on the pin.  Ground truth --
                   pdngen says so in its own output.
  no-partner       no via, and no same-net shape on the other connect layer
                   overlaps the pin far enough to seat a via.  The pin has
                   nothing to reach; a geometry (pitch/offset/size) story.
  partner-no-via   no via, but the crossing IS there.  Something removed the
                   via after the intersection was found -- an obstruction on
                   an intermediate layer, or a spacing/enclosure refusal.
                   This is the residual that needs the pdngen log, not more
                   geometry.
  via-no-partner   a via with no crossing this script can find.  IMPOSSIBLE
                   under the rule above, so a non-zero count means the READER
                   is wrong (a missed shape, a width misread, the wrong layer
                   pair) and the run's other verdicts cannot be trusted.  It
                   is reported first and fails the check on its own.

Exit 0 when every power pin of every macro is connected, 1 when any floats
(`--allow-floating N` raises the bar to N), 2 on input the reader will not
guess at.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdn_phase import InputShape, orient_rect, read_lef  # noqa: E402

EPS = 1e-6
VIA_MIN = 1.4          # um; the same floor pdn_phase.py predicts with

_POWER_USE = ("POWER", "GROUND")


# ── DEF ───────────────────────────────────────────────────────────────────
def _section(text, name):
    m = re.search(r"^%s\s+\d+\s*;(.*?)^END %s" % (name, name), text, re.S | re.M)
    if m:
        return m.group(1)
    # COMPONENTS/SPECIALNETS carry a count; a section written without one is
    # still legal DEF, so fall back rather than reporting an empty design.
    m = re.search(r"^%s\b[^;]*;(.*?)^END %s" % (name, name), text, re.S | re.M)
    return m.group(1) if m else None


def unescape(name):
    """DEF escapes a name's special characters; `mid\\[0\\]` is net `mid[0]`."""
    return re.sub(r"\\(.)", r"\1", name)


def read_units(text):
    m = re.search(r"^\s*UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;", text, re.M)
    if not m:
        raise InputShape("DEF has no UNITS DISTANCE MICRONS; every coordinate "
                         "below would be in unknown units")
    return float(m.group(1))


_COMP = re.compile(
    r"^\s*-\s+(\S+)\s+(\S+)(.*?);", re.S | re.M)
_PLACED = re.compile(r"\+\s*(?:PLACED|FIXED|COVER)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*(\w+)")
_UNPLACED = re.compile(r"\+\s*UNPLACED\b")


def read_components(text, dbu):
    """[{name, cell, x, y, orient}] in MICRONS; UNPLACED entries are dropped
    and counted by the caller through `unplaced`."""
    body = _section(text, "COMPONENTS")
    if body is None:
        raise InputShape("DEF has no COMPONENTS section")
    out, unplaced = [], []
    for m in _COMP.finditer(body):
        name, cell, rest = unescape(m.group(1)), m.group(2), m.group(3)
        p = _PLACED.search(rest)
        if p is None:
            if _UNPLACED.search(rest):
                unplaced.append(name)
            continue
        out.append({"name": name, "cell": cell,
                    "x": int(p.group(1)) / dbu, "y": int(p.group(2)) / dbu,
                    "orient": p.group(3)})
    return out, unplaced


_SNET_ENTRY = re.compile(r"^\s*-\s+(\S+)(.*?)(?=^\s*-\s+\S|\Z)", re.S | re.M)
_CONN = re.compile(r"\(\s*(\S+)\s+(\S+)\s*\)")
_TOKEN = re.compile(r"\(\s*([^()]*?)\s*\)|(\S+)")
_WIRE_START = re.compile(r"\+\s*(?:ROUTED|FIXED|COVER)\b|\bNEW\b")


def _paths(entry, dbu, census):
    """[(layer, width_um, [(x,y), ...])] for each `+ ROUTED`/`NEW` wire of one
    SPECIALNETS entry, coordinates in microns.

    A `+ SHAPE <type>` or `+ STYLE <n>` sits between the width and the first
    point, so the point walk has to skip it (reading it as a point is how a
    reader loses every stripe a PDN generator writes).  A path that ends in a
    via name after a SINGLE point is a via PLACEMENT, not wire, and is
    returned by `_vias`; anything else the reader cannot shape is censused by
    what defeated it rather than silently dropped."""
    out = []
    for stmt in re.split(_WIRE_START, entry)[1:]:
        toks = [(g1, g2) for g1, g2 in _TOKEN.findall(stmt)]
        i, layer, width = 0, None, None
        pts = []
        while i < len(toks):
            paren, word = toks[i]
            if word:
                if word == "+":
                    if pts:
                        break                  # the entry's tail (+ USE, + PROPERTY)
                    i += 1
                    continue
                if word in ("SHAPE", "STYLE", "MASK"):
                    i += 2                     # the clause and its value
                    continue
                if layer is None:
                    layer = word
                    if i + 1 < len(toks) and toks[i + 1][1] is not None \
                            and re.fullmatch(r"-?\d+", toks[i + 1][1]):
                        width = int(toks[i + 1][1]) / dbu
                        i += 1
                    i += 1
                    continue
                break                          # a via name (or the entry's tail)
            else:
                nums = paren.split()
                if len(nums) < 2:
                    census["short_point"] = census.get("short_point", 0) + 1
                    i += 1
                    continue
                prev = pts[-1] if pts else None
                try:
                    x = prev[0] if nums[0] == "*" else int(nums[0]) / dbu
                    y = prev[1] if nums[1] == "*" else int(nums[1]) / dbu
                except (TypeError, ValueError):
                    census["bad_point"] = census.get("bad_point", 0) + 1
                    i += 1
                    continue
                pts.append((x, y))
                i += 1
                continue
        if layer is None:
            census["no_layer"] = census.get("no_layer", 0) + 1
            continue
        if len(pts) >= 2:
            if width is None:
                census["no_width"] = census.get("no_width", 0) + 1
                continue
            out.append((layer, width, pts))
        elif len(pts) == 1:
            pass                               # a via placement; see _vias
        else:
            census["no_points"] = census.get("no_points", 0) + 1
    return out


def _vias(entry, dbu, census):
    """[(layer, x, y, via_name)] -- a one-point path ending in a via name.
    That is how pdngen writes a via, and it is the only place the DEF says
    outright that two layers were joined here."""
    out = []
    for stmt in re.split(_WIRE_START, entry)[1:]:
        toks = _TOKEN.findall(stmt)
        layer, pts, tail = None, [], None
        for paren, word in toks:
            if word:
                if word == "+":
                    if pts:
                        break                  # the entry's tail, not a via name
                    continue
                if word in ("SHAPE", "STYLE", "MASK"):
                    continue
                if layer is None:
                    layer = word
                elif re.fullmatch(r"-?\d+", word):
                    continue
                elif pts:
                    tail = word
                    break
            else:
                nums = paren.split()
                if len(nums) >= 2:
                    prev = pts[-1] if pts else None
                    try:
                        x = prev[0] if nums[0] == "*" else int(nums[0]) / dbu
                        y = prev[1] if nums[1] == "*" else int(nums[1]) / dbu
                    except (TypeError, ValueError):
                        continue
                    pts.append((x, y))
        if layer is not None and len(pts) == 1 and tail:
            out.append((layer, pts[0][0], pts[0][1], tail))
        elif layer is not None and len(pts) > 1 and tail:
            census["via_mid_path"] = census.get("via_mid_path", 0) + 1
    return out


def read_specialnets(text, dbu):
    """{net: {"rects": [(layer,x1,y1,x2,y2)], "vias": [(layer,x,y,name)],
    "pins": [pin names connected via `( * PIN )`]}}, plus a census of what the
    reader could not shape."""
    body = _section(text, "SPECIALNETS")
    if body is None:
        return {}, {"no_section": 1}
    nets, census = {}, {}
    for m in _SNET_ENTRY.finditer(body):
        net = unescape(m.group(1))
        entry = m.group(2)
        head = entry.split("+", 1)[0]
        pins = [p for inst, p in _CONN.findall(head) if inst == "*"]
        d = nets.setdefault(net, {"rects": [], "vias": [], "pins": []})
        d["pins"].extend(pins)
        for (layer, width, pts) in _paths(entry, dbu, census):
            half = width / 2.0
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                if abs(y1 - y2) <= EPS:        # horizontal run
                    d["rects"].append((layer, min(x1, x2), y1 - half, max(x1, x2), y1 + half))
                elif abs(x1 - x2) <= EPS:      # vertical run
                    d["rects"].append((layer, x1 - half, min(y1, y2), x1 + half, max(y1, y2)))
                else:
                    census["diagonal"] = census.get("diagonal", 0) + 1
        d["vias"].extend(_vias(entry, dbu, census))
    return nets, census


# ── the audit ─────────────────────────────────────────────────────────────
def _ov(a1, a2, b1, b2):
    return min(a2, b2) - max(a1, b1)


def pin_nets(snets):
    """{pin name: net} from the DEF's own `( * PIN )` connections -- the DEF
    says which net each macro power pin is on, so the mapping is read rather
    than named on the command line.  A power pin no SPECIALNETS entry claims
    is reported; guessing its net would invent a connection to audit."""
    out = {}
    for net, d in snets.items():
        for p in d["pins"]:
            if out.get(p, net) != net:
                raise InputShape(f"DEF connects pin {p} to both {out[p]} and {net}")
            out[p] = net
    return out


def instance_pins(inst, macro, netof):
    """[(net, layer, x1, y1, x2, y2, pin)] in top microns, power pins only."""
    w, h = macro["size"]
    out, unclaimed = [], set()
    for pname, pin in macro["pins"].items():
        if (pin["use"] or "").upper() not in _POWER_USE:
            continue
        net = netof.get(pname)
        if net is None:
            unclaimed.add(pname)
            continue
        for (layer, x1, y1, x2, y2) in pin["rects"]:
            r = orient_rect((x1, y1, x2, y2), inst["orient"], w, h)
            out.append((net, layer, r[0] + inst["x"], r[1] + inst["y"],
                        r[2] + inst["x"], r[3] + inst["y"], pname))
    return out, unclaimed


def shapes_by_layer(snets, layers):
    """{(net, layer): [(x1,y1,x2,y2)]} for the two connect layers only -- built
    once, because the per-pin scan would otherwise walk every stripe of the net
    (a real PDN has thousands) for every pin of every macro."""
    idx = {}
    for net, d in snets.items():
        for (layer, x1, y1, x2, y2) in d["rects"]:
            if layer in layers:
                idx.setdefault((net, layer), []).append((x1, y1, x2, y2))
    return idx


def audit_instance(inst, pins, snets, layers, via_min, straps=None, vias=None):
    """One verdict dict per power-pin rectangle on a connect layer."""
    lo_layer, hi_layer = layers
    other = {lo_layer: hi_layer, hi_layer: lo_layer}
    if straps is None:
        straps = shapes_by_layer(snets, set(layers))
    if vias is None:
        vias = {n: d["vias"] for n, d in snets.items()}
    own = {}
    for (net, layer, x1, y1, x2, y2, _p) in pins:
        own.setdefault((net, layer), []).append((x1, y1, x2, y2))

    out = []
    for (net, layer, x1, y1, x2, y2, pname) in pins:
        if layer not in other:
            continue
        via = None
        for (vl, vx, vy, vname) in vias.get(net, ()):
            if x1 - EPS <= vx <= x2 + EPS and y1 - EPS <= vy <= y2 + EPS:
                via = {"layer": vl, "x": round(vx, 4), "y": round(vy, 4), "via": vname}
                break
        partner = None
        cands = [("strap", r) for r in straps.get((net, other[layer]), ())]
        cands += [("pin", r) for r in own.get((net, other[layer]), ())]
        for kind, (ox1, oy1, ox2, oy2) in cands:
            ox, oy = _ov(x1, x2, ox1, ox2), _ov(y1, y2, oy1, oy2)
            if min(ox, oy) >= via_min - EPS:
                partner = {"kind": kind, "layer": other[layer],
                           "rect": [round(v, 4) for v in (ox1, oy1, ox2, oy2)],
                           "overlap": [round(ox, 4), round(oy, 4)]}
                break
        if via is not None:
            verdict = "connected" if partner else "via-no-partner"
        else:
            verdict = "partner-no-via" if partner else "no-partner"
        out.append({"instance": inst["name"], "cell": inst["cell"], "pin": pname,
                    "net": net, "layer": layer,
                    "rect": [round(v, 4) for v in (x1, y1, x2, y2)],
                    "verdict": verdict, "via": via, "partner": partner})
    return out


def run_audit(def_text, lefs, layers, via_min=VIA_MIN):
    dbu = read_units(def_text)
    comps, unplaced = read_components(def_text, dbu)
    snets, census = read_specialnets(def_text, dbu)
    netof = pin_nets(snets)

    straps = shapes_by_layer(snets, set(layers))
    vias = {n: d["vias"] for n, d in snets.items()}
    findings, unclaimed, missing_lef, macros = [], set(), {}, 0
    for inst in comps:
        macro = lefs.get(inst["cell"])
        if macro is None:
            missing_lef[inst["cell"]] = missing_lef.get(inst["cell"], 0) + 1
            continue
        pins, unc = instance_pins(inst, macro, netof)
        unclaimed |= unc
        if not pins:
            continue
        macros += 1
        findings.extend(audit_instance(inst, pins, snets, layers, via_min, straps, vias))

    if not findings:
        # The shape this whole script exists to prevent: a clean-looking pass
        # over nothing.  Name which of the four ways it happened.
        why = []
        if missing_lef and not macros:
            why.append("no placed component's cell has a LEF here ("
                       + ", ".join(sorted(missing_lef)) + ")")
        if not any((pin["use"] or "").upper() in _POWER_USE
                   for m in lefs.values() for pin in m["pins"].values()):
            why.append("no LEF pin carries USE POWER or USE GROUND")
        if unclaimed:
            why.append("no SPECIALNETS entry claims " + ", ".join(sorted(unclaimed))
                       + " -- the DEF must connect them with `( * <pin> )`")
        if macros:
            why.append(f"no power pin lies on {layers[0]} or {layers[1]} "
                       f"(--layers names the add_pdn_connect pair)")
        raise InputShape("audited 0 power-pin rectangles: "
                         + ("; ".join(why) if why else "no macro pin geometry matched"))

    tally = {}
    for f in findings:
        key = (f["cell"], f["net"], f["layer"], f["verdict"])
        tally[key] = tally.get(key, 0) + 1
    rows = [{"cell": c, "net": n, "layer": l, "verdict": v, "count": k}
            for (c, n, l, v), k in sorted(tally.items())]
    counts = {}
    for f in findings:
        counts[f["verdict"]] = counts.get(f["verdict"], 0) + 1
    floating = counts.get("no-partner", 0) + counts.get("partner-no-via", 0)
    return {"dbu": dbu, "layers": list(layers), "via_min": via_min,
            "macros": macros, "pins": len(findings), "counts": counts,
            "floating": floating, "rows": rows, "findings": findings,
            "nets": sorted(snets), "unplaced": unplaced,
            "unclaimed_power_pins": sorted(unclaimed),
            "missing_lef": missing_lef, "unread": census}


def self_cross(lefs, layers, via_min=VIA_MIN):
    """Per macro and per power pin: do the pin's OWN rectangles on the two
    connect layers cross each other far enough to seat a via?

    This needs no DEF and no placement, because it is a property of the CELL.
    It matters because `InstanceGrid::getInstancePins` injects a macro's pins
    into the same shape set the straps are in, so a pin that crosses its own
    net's pin on the other connect layer is connected by that crossing alone --
    with no strap over it anywhere.  A macro whose VPWR pins cross and whose
    VGND pins do not will connect one net and float the other on any phase,
    which no strap-only prediction can see and no offset search can fix."""
    lo, hi = layers
    out = []
    for cell in sorted(lefs):
        macro = lefs[cell]
        for pname in sorted(macro["pins"]):
            pin = macro["pins"][pname]
            if (pin["use"] or "").upper() not in _POWER_USE:
                continue
            los = [r for r in pin["rects"] if r[0] == lo]
            his = [r for r in pin["rects"] if r[0] == hi]
            best, where = 0.0, None
            for (_a, ax1, ay1, ax2, ay2) in los:
                for (_b, bx1, by1, bx2, by2) in his:
                    m = min(_ov(ax1, ax2, bx1, bx2), _ov(ay1, ay2, by1, by2))
                    if m > best:
                        best, where = m, [round(max(ax1, bx1), 4), round(max(ay1, by1), 4),
                                          round(min(ax2, bx2), 4), round(min(ay2, by2), 4)]
            out.append({"cell": cell, "pin": pname,
                        "rects": {lo: len(los), hi: len(his)},
                        "overlap": round(best, 4), "at": where,
                        "self_crossed": best >= via_min - EPS})
    return {"layers": list(layers), "via_min": via_min, "pins": out}


def report_self(res, out=sys.stdout):
    lo, hi = res["layers"]
    out.write(f"pdn_connect --self-cross: does a power pin cross its own net on "
              f"the other connect layer?\n"
              f"  pair {lo}/{hi}, via floor {res['via_min']} um.  A pin that does is "
              f"connected by that\n  crossing alone, whatever the straps do "
              f"(InstanceGrid::getInstancePins).\n\n")
    out.write(f"  cell / pin              {lo:>6} {hi:>6}  overlap  self-crossed\n")
    for r in res["pins"]:
        out.write(f"  {r['cell']:<15} {r['pin']:<6} {r['rects'][lo]:>6} {r['rects'][hi]:>6} "
                  f"{r['overlap']:>8}  {'yes' if r['self_crossed'] else 'NO'}\n")
    split = {}
    for r in res["pins"]:
        split.setdefault(r["cell"], set()).add(r["self_crossed"])
    mixed = [c for c, v in split.items() if v == {True, False}]
    if mixed:
        out.write("\n  SPLIT: " + ", ".join(sorted(mixed)) + " -- one power net self-crosses "
                  "and the other does not.\n  That asymmetry is per-CELL and survives every "
                  "PDN offset, so a floating\n  net on exactly one cell is explained here "
                  "before any phase search is run.\n")


def report(res, out=sys.stdout, limit=12):
    w = out.write
    w(f"pdn_connect: connect pair {res['layers'][0]}/{res['layers'][1]}, "
      f"via floor {res['via_min']} um, {res['macros']} macro(s), "
      f"{res['pins']} power-pin rect(s)\n")
    if res["missing_lef"]:
        w("  no LEF (not audited): "
          + ", ".join(f"{c} x{n}" for c, n in sorted(res["missing_lef"].items())) + "\n")
    if res["unplaced"]:
        w(f"  UNPLACED components skipped: {len(res['unplaced'])}\n")
    if res["unclaimed_power_pins"]:
        w("  power pins no SPECIALNETS entry claims: "
          + ", ".join(res["unclaimed_power_pins"]) + "\n")
    if res["unread"]:
        w("  SPECIALNETS the reader could not shape: "
          + ", ".join(f"{k}={v}" for k, v in sorted(res["unread"].items())) + "\n")
    bad = res["counts"].get("via-no-partner", 0)
    if bad:
        w(f"\n  READER FAULT: {bad} via(s) with no crossing this reader can find.\n"
          f"  pdngen vias a same-net cross-layer OVERLAP and nothing else, so a via\n"
          f"  without one means a shape was missed -- treat every verdict below as\n"
          f"  unproven until it is nil.\n")
    w("\n  cell / net / layer                 verdict          count\n")
    for r in res["rows"]:
        w(f"  {r['cell']:<16} {r['net']:<6} {r['layer']:<6} {r['verdict']:<16} {r['count']:>6}\n")
    bad_findings = [f for f in res["findings"] if f["verdict"] != "connected"]
    other_of = {res["layers"][0]: res["layers"][1], res["layers"][1]: res["layers"][0]}
    shown = 0
    for f in bad_findings[:limit]:
        p, o = f["partner"], other_of[f["layer"]]
        if f["verdict"] == "via-no-partner":
            why = (f"via {f['via']['via']} at ({f['via']['x']}, {f['via']['y']}) with NOTHING "
                   f"on {o} overlapping it -- the reader missed a shape")
        elif p:
            why = (f"crossed by a {p['kind']} on {p['layer']} at {p['rect']} "
                   f"(overlap {p['overlap']}) but no via")
        else:
            why = f"nothing on {o} overlaps it by {res['via_min']} um"
        w(f"    {f['instance']}.{f['pin']} ({f['net']}) {f['layer']} {f['rect']}: {why}\n")
        shown += 1
    left = len(bad_findings) - shown
    if left > 0:
        w(f"    ... and {left} more (--json for all)\n")
    w(f"\n  {res['counts'].get('connected', 0)} connected, {res['floating']} floating\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("deffile", metavar="def", nargs="?",
                    help="the DEF pdngen wrote (the PDN stage's output); omit with --self-cross")
    ap.add_argument("lef", nargs="*", help="the hardened block LEFs whose pins to audit")
    ap.add_argument("--layers", default="met4,met5",
                    help="the add_pdn_connect pair, lower,upper (met4,met5)")
    ap.add_argument("--via-min", type=float, default=VIA_MIN,
                    help=f"min overlap in both axes to seat a via (um, {VIA_MIN})")
    ap.add_argument("--allow-floating", type=int, default=0, metavar="N",
                    help="pass with up to N floating power-pin rects (0)")
    ap.add_argument("--self-cross", action="store_true",
                    help="LEFs only, no DEF: report per cell whether each power pin crosses its "
                         "own net on the other connect layer (see self_cross())")
    ap.add_argument("--json", metavar="OUT", help="write every finding as JSON")
    a = ap.parse_args(argv)
    try:
        layers = tuple(s.strip() for s in a.layers.split(",") if s.strip())
        if len(layers) != 2:
            raise InputShape(f"--layers takes exactly two layers, got {a.layers!r}")
        if a.self_cross:
            # every positional is a LEF here: the mode reads no DEF at all, so
            # the first one must not be swallowed as one
            if a.deffile:
                a.lef = [a.deffile] + list(a.lef)
                a.deffile = None
        elif a.deffile is None:
            raise InputShape("no DEF given; pass the PDN stage's output DEF, or --self-cross "
                             "to ask the LEF-only question")
        elif not os.path.exists(a.deffile):
            raise InputShape(f"{a.deffile}: no such DEF -- this reads a pdngen RUN's output, "
                             f"not a prediction; pdn_phase.py is the before-the-run check")
        def_text = None
        if a.deffile:
            with open(a.deffile) as f:
                def_text = f.read()
        lefs = {}
        for pth in a.lef:
            if not os.path.exists(pth):
                raise InputShape(f"{pth}: no such LEF")
            lefs.update(read_lef(pth))
        if not lefs:
            raise InputShape("no LEF given, so no macro pin geometry to audit; pass the "
                             "hardened blocks' LEFs")
        res = (self_cross(lefs, layers, a.via_min) if a.self_cross
               else run_audit(def_text, lefs, layers, a.via_min))
    except InputShape as e:
        print(f"pdn_connect: ERROR: {e}", file=sys.stderr)
        return 2
    (report_self if a.self_cross else report)(res)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=1)
    if a.self_cross:
        return 0                               # a report, not a verdict
    if res["counts"].get("via-no-partner", 0):
        return 1
    return 0 if res["floating"] <= a.allow_floating else 1


if __name__ == "__main__":
    sys.exit(main())
