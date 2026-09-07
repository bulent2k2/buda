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

The count that decides the exit is per TERMINAL, not per rectangle: a LEF
`PIN` is one node and its several `RECT`s are alternative access shapes
connected inside the macro, so a via on any one of them feeds it and pdngen
viaing one and not another is the normal case.  The per-rectangle verdicts
below stay as the diagnostics -- which layer, which access shape, what it was
missing -- and the report prints both tables.

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

Then a question a per-pin audit structurally cannot ask: **is each net one
piece of metal?**  A terminal can have its via and still be dead, because the
metal it vias onto is a fragment cut off from the grid -- what `PSM-0069`
reports, and what defeated this script's first version: on the study's own
N=8 run the FAILING DEF and the passing one both audited "208 terminals
connected, 0 floating" (#893).  `net_components()` partitions each net's own
shapes (same-layer rectangles that touch are one piece; a via joins every
shape of that net covering its point), calls the largest by area the grid,
and reports the rest as fragments -- naming, for each, the terminals it
strands.  A fragment carrying terminals fails the run; one carrying none is
floating stub metal, reported and not fatal.

`PSM-0040`/`PSM-0069` and the `*-grid-errors.rpt` remain the verdict.  This
localises a failure to pins or away from them, which is the part that costs a
person an afternoon.

Exit 0 when every power terminal is connected and no fragment strands one; 1
when any floats (`--allow-floating N`) or is stranded (`--allow-stranded N`);
2 on input the reader will not guess at.
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
                if abs(y1 - y2) > EPS and abs(x1 - x2) > EPS:
                    census["diagonal"] = census.get("diagonal", 0) + 1
                    continue
                # DEF's default extension for SPECIAL wiring is half the width
                # at the ENDS as well as across the run, so the rectangle grows
                # by `half` on all four sides -- the same expansion the engine's
                # own consumer applies (src/bdb.cpp, the special_wires ->
                # keepouts pass).  Stopping at the centreline endpoints loses
                # real metal exactly where it matters here: a strap ending at
                # or just beside a macro pin would read as `no-partner`, or
                # would seat a via this reader then calls impossible.
                d["rects"].append((layer, min(x1, x2) - half, min(y1, y2) - half,
                                   max(x1, x2) + half, max(y1, y2) + half))
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
            if not (x1 - EPS <= vx <= x2 + EPS and y1 - EPS <= vy <= y2 + EPS):
                continue
            # The via must belong to the pair being audited.  A DEF carries
            # every connect statement's vias, and a met3/met4 via whose point
            # happens to fall inside a met5 pin says nothing about that pin --
            # accepting it would report a floating pin as connected, which is
            # the one direction this audit must never fail in.  pdngen writes a
            # via on the LOWER of the two layers it joins, so a via of this
            # pair is written on one of the two, and one of another pair is
            # not.  (Residual: a via joining the pin's layer UPWARD out of the
            # pair is still accepted -- it is a real connection to real grid
            # metal, just not through this statement, and the DEF names only
            # the writer's layer, so nothing here can tell the two apart.)
            if vl not in (layer, other[layer]):
                continue
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


# ── is the NETWORK whole? (the question a terminal rollup cannot ask) ─────
class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        p = self.p
        while p[a] != a:
            p[a] = p[p[a]]
            a = p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


class _BinIndex:
    """Bins rectangles along their NARROW axis so a point or box query touches
    a handful of candidates instead of all of them.

    A PDN is thousands of long thin stripes, so the obvious index shapes fail
    in opposite ways: a uniform 2-D grid puts one die-crossing strap in
    thousands of cells, and a scan is quadratic.  Binning on the axis the
    rectangles are THIN along gives each one a bin or two.  Measured on a
    6120-rect / 103k-via synthetic PDN: 25.9 s scanning, 0.4 s indexed.
    """

    def __init__(self, rects, keyfn=lambda r: r, nbins=4096):
        self.rects = rects
        self.axis = 0
        if rects:
            wx = sorted(keyfn(r)[2] - keyfn(r)[0] for r in rects)
            wy = sorted(keyfn(r)[3] - keyfn(r)[1] for r in rects)
            self.axis = 0 if wx[len(wx) // 2] <= wy[len(wy) // 2] else 1
        a = self.axis
        lo = min((keyfn(r)[a] for r in rects), default=0.0)
        hi = max((keyfn(r)[a + 2] for r in rects), default=0.0)
        self.lo = lo
        self.n = max(1, min(nbins, len(rects) or 1))
        self.w = max((hi - lo) / self.n, 1e-9)
        self.bins = [[] for _ in range(self.n + 1)]
        for i, r in enumerate(rects):
            k = keyfn(r)
            for b in range(self._bin(k[a]), self._bin(k[a + 2]) + 1):
                self.bins[b].append(i)

    def _bin(self, v):
        return max(0, min(self.n, int((v - self.lo) / self.w)))

    def near(self, lo, hi):
        """Indices whose bin range meets [lo, hi] on the index axis."""
        seen = set()
        for b in range(self._bin(lo), self._bin(hi) + 1):
            seen.update(self.bins[b])
        return seen

    def at(self, x, y):
        return self.near(x if self.axis == 0 else y, x if self.axis == 0 else y)


def _touch(a, b):
    """Two rectangles are electrically one piece of metal when they overlap OR
    ABUT.  Touching must count: pdngen writes a strap as path segments that
    meet end to end, so a strict-overlap rule would fragment every strap at
    every bend and report a whole grid as rubble."""
    return (min(a[2], b[2]) >= max(a[0], b[0]) - EPS
            and min(a[3], b[3]) >= max(a[1], b[1]) - EPS)


def net_components(snets, terminals_by_net=None, layers=None, with_members=False):
    """Partition each power net's DEF metal into ELECTRICAL components.

    A macro power TERMINAL can have its via and still be dead, because the
    metal it vias onto is a fragment isolated from the rest of the grid.
    That is what `PSM-0069` reports and what the per-pin audit above
    structurally cannot see -- measured on this study's own N=8 run, where
    the failing DEF and the passing one both audited "208 terminals
    connected, 0 floating" (#893).

    Union-find over the net's own shapes: same-layer rectangles that touch
    are one piece, and a via placement joins every rectangle of that net
    containing its point, on any layer.  The via rule is deliberately
    generous -- the DEF names only the layer the via was WRITTEN on, so
    asking which two layers it joins is guesswork, while "everything of this
    net at this point is now one node" is what a via stack does.  Being
    generous means this pass can only UNDER-report fragmentation, never
    invent it, which is the right direction for something that fails a run.

    `layers` is the audited `add_pdn_connect` pair and filters TERMINAL
    ATTACHMENT only, never the union-find: a met3/met4 via legitimately joins
    that net's met3 and met4 metal, so it belongs in the network, but it does
    nothing for a met5 pin whose footprint it happens to sit inside -- and
    crediting it would attach a healthy terminal to a met3 stub and report
    the stub as stranding it.  `audit_instance()` already makes exactly this
    distinction for the per-pin verdict.

    Deliberately NOT joined: two fragments both landing on one macro's pin.
    A hard macro's internal PDN really does connect them, but PSM cannot
    traverse an abstract LEF and does not credit it -- and agreeing with the
    verdict matters more here than being physically complete.  Where it
    happens it is reported (`bridged_by`) rather than silently applied.
    """
    out = {}
    for net, d in snets.items():
        rects = list(d["rects"])
        if not rects:
            continue
        uf = _UF(len(rects))
        # same-layer touch, by an x-sweep: a PDN's stripes are long on one
        # axis and narrow on the other, so the active list stays short.  The
        # worst case is still quadratic; a report that took too long would be
        # a better problem than the silence it replaces.
        bylayer = {}
        for i, r in enumerate(rects):
            bylayer.setdefault(r[0], []).append(i)
        for idxs in bylayer.values():
            idxs.sort(key=lambda i: rects[i][1])
            active = []
            for i in idxs:
                x1 = rects[i][1]
                active = [j for j in active if rects[j][3] >= x1 - EPS]
                bi = rects[i][1:]
                for j in active:
                    if _touch(bi, rects[j][1:]):
                        uf.union(i, j)
                active.append(i)
        idx = _BinIndex(rects, keyfn=lambda r: r[1:])

        def covering(vx, vy):
            return [i for i in idx.at(vx, vy)
                    if rects[i][1] - EPS <= vx <= rects[i][3] + EPS
                    and rects[i][2] - EPS <= vy <= rects[i][4] + EPS]

        # a via joins every shape of this net covering its point
        for (_vl, vx, vy, _vn) in d["vias"]:
            hit = covering(vx, vy)
            for j in hit[1:]:
                uf.union(hit[0], j)

        comps = {}
        for i, r in enumerate(rects):
            comps.setdefault(uf.find(i), []).append(i)
        rows = []
        for k, members in comps.items():
            area = sum((rects[i][3] - rects[i][1]) * (rects[i][4] - rects[i][2]) for i in members)
            # NOT `layers`: that is the parameter, and shadowing it here made
            # the pair filter below test this dict's keys instead
            per_layer = {}
            for i in members:
                per_layer[rects[i][0]] = per_layer.get(rects[i][0], 0) + 1
            xs = [rects[i][1] for i in members] + [rects[i][3] for i in members]
            ys = [rects[i][2] for i in members] + [rects[i][4] for i in members]
            rows.append({"root": k, "shapes": len(members), "area": round(area, 3),
                         # the rect indices (input order) only on request: a
                         # caller predicting the grid needs them to say WHICH
                         # fragment is off it; the JSON report does not
                         **({"members": sorted(members)} if with_members else {}),
                         "layers": dict(sorted(per_layer.items())),
                         "bbox": [round(min(xs), 3), round(min(ys), 3),
                                  round(max(xs), 3), round(max(ys), 3)],
                         "span": round(max(max(xs) - min(xs), max(ys) - min(ys)), 3),
                         "terminals": []})
        rows.sort(key=lambda c: (-c["area"], -c["shapes"]))
        index = {c["root"]: n for n, c in enumerate(rows)}

        # which macro terminals sit on which component -- the fragments that
        # carry none are floating stubs, the ones that carry some strand pins
        vidx = _BinIndex(d["vias"], keyfn=lambda v: (v[1], v[2], v[1], v[2]))
        bridged = []
        for t in (terminals_by_net or {}).get(net, ()):
            on = set()
            for (tl, x1, y1, x2, y2) in t["rects"]:
                # (a) the pin's own metal touching a strap on the SAME layer.
                # The layer test is not a detail: without it a met5 pin
                # "touches" every met4 strap whose footprint it crosses, and
                # crossing metal on two layers is not connected -- that is
                # what the via is for.
                for i in idx.near(x1 if idx.axis == 0 else y1,
                                  x2 if idx.axis == 0 else y2):
                    if rects[i][0] == tl and _touch((x1, y1, x2, y2), rects[i][1:]):
                        on.add(index[uf.find(i)])
                # (b) a via inside the pin, landing in a shape of the net --
                # the normal case, and the only one for a pin whose partner
                # is on the other layer
                for vi in vidx.near(x1 if vidx.axis == 0 else y1,
                                    x2 if vidx.axis == 0 else y2):
                    (vl, vx, vy, _vn) = d["vias"][vi]
                    if layers is not None and vl not in layers:
                        continue                 # another pair's via; see above
                    if not (x1 - EPS <= vx <= x2 + EPS and y1 - EPS <= vy <= y2 + EPS):
                        continue
                    for i in covering(vx, vy):
                        on.add(index[uf.find(i)])
            for c in sorted(on):
                rows[c]["terminals"].append(t["name"])
            if len(on) > 1:
                bridged.append({"terminal": t["name"], "components": sorted(on)})

        for n, c in enumerate(rows):
            c["id"] = n
            c.pop("root")
        # UNIQUE terminals: a terminal on two fragments is one stranded
        # terminal, and `--allow-stranded` is a terminal threshold, so summing
        # the per-fragment lists would reject a design with exactly one.
        stranded = len({t for c in rows[1:] for t in c["terminals"]})
        out[net] = {"components": rows, "fragments": max(0, len(rows) - 1),
                    "stranded_terminals": stranded, "bridged_by": bridged}
    return out


def terminal_rollup(findings):
    """One verdict per (instance, net, pin) TERMINAL, from the per-rectangle
    findings.

    A LEF `PIN` is ONE terminal; its several `RECT`s (and `PORT`s) are
    alternative access shapes for the same node, connected inside the macro.
    So a via on any one of them feeds the terminal and the others are not
    independently floating -- counting rectangles would fail a macro pdngen
    connected perfectly well, merely because it did not via every access
    shape, which is the normal case.  The per-rectangle findings stay: they
    are the diagnostics (which layer, which access shape, what it was missing).
    Only the count that decides the exit moves up to the terminal.

    A rect carrying a via counts as connected even when its verdict is
    `via-no-partner`: the metal IS joined there, and what that verdict says is
    that the READER cannot explain it.  That claim is not diluted -- it keeps
    its own per-rect count and fails the run on its own."""
    order, groups = [], {}
    for f in findings:
        k = (f["instance"], f["net"], f["pin"])
        if k not in groups:
            order.append(k)
            groups[k] = []
        groups[k].append(f)
    out = []
    for k in order:
        fs = groups[k]
        vias = sum(1 for f in fs if f["via"] is not None)
        if vias:
            verdict = "connected"
        elif any(f["partner"] for f in fs):
            verdict = "partner-no-via"
        else:
            verdict = "no-partner"
        out.append({"instance": k[0], "cell": fs[0]["cell"], "net": k[1], "pin": k[2],
                    "verdict": verdict, "rects": len(fs), "vias": vias,
                    "layers": sorted({f["layer"] for f in fs})})
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

    terminals = terminal_rollup(findings)
    # the network question, from the same shapes the per-pin audit just used
    tbn = {}
    for t in terminals:
        tbn.setdefault(t["net"], []).append(
            {"name": f"{t['instance']}.{t['pin']}",
             "rects": [tuple([f["layer"]] + f["rect"]) for f in findings
                       if f["instance"] == t["instance"] and f["net"] == t["net"]
                       and f["pin"] == t["pin"]]})
    nets_conn = net_components(snets, tbn, layers)
    fragments = sum(v["fragments"] for v in nets_conn.values())
    stranded = sum(v["stranded_terminals"] for v in nets_conn.values())
    tcounts = {}
    for t in terminals:
        tcounts[t["verdict"]] = tcounts.get(t["verdict"], 0) + 1
    ttally = {}
    for t in terminals:
        ttally[(t["cell"], t["net"], t["verdict"])] = \
            ttally.get((t["cell"], t["net"], t["verdict"]), 0) + 1
    terminal_rows = [{"cell": c, "net": n, "verdict": v, "count": k}
                     for (c, n, v), k in sorted(ttally.items())]
    floating = len(terminals) - tcounts.get("connected", 0)
    return {"dbu": dbu, "layers": list(layers), "via_min": via_min,
            "macros": macros, "pins": len(findings), "counts": counts,
            "terminals": terminals, "terminal_counts": tcounts,
            "connectivity": nets_conn, "fragments": fragments,
            "stranded_terminals": stranded,
            "terminal_rows": terminal_rows, "n_terminals": len(terminals),
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
      f"{res['n_terminals']} power terminal(s) over {res['pins']} rect(s)\n")
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
    # The TERMINAL table is the verdict; the rectangle table below it is the
    # diagnostic.  A LEF pin's several rects are access shapes for one node,
    # so a via on any of them feeds it -- counting rects would fail a macro
    # pdngen connected, for not viaing every access shape.
    w("\n  terminals (a LEF pin is one node; any via on it feeds it)\n")
    w("  cell / net                         verdict          count\n")
    for r in res["terminal_rows"]:
        w(f"  {r['cell']:<16} {r['net']:<13} {r['verdict']:<16} {r['count']:>6}\n")
    w("\n  access rectangles (diagnostic: which layer, which shape)\n")
    w("  cell / net / layer                 verdict          count\n")
    for r in res["rows"]:
        w(f"  {r['cell']:<16} {r['net']:<6} {r['layer']:<6} {r['verdict']:<16} {r['count']:>6}\n")
    floating_terms = {(t["instance"], t["net"], t["pin"]) for t in res["terminals"]
                      if t["verdict"] != "connected"}
    bad_findings = [f for f in res["findings"]
                    if f["verdict"] == "via-no-partner"
                    or (f["instance"], f["net"], f["pin"]) in floating_terms]
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
    # the network question.  A terminal with its via can still be dead when
    # the metal it vias onto is cut off from the grid -- what PSM-0069
    # reports, and what the tables above structurally cannot show.
    w("\n  network (is each net one piece of metal?)\n")
    for net in sorted(res["connectivity"]):
        c = res["connectivity"][net]
        comps = c["components"]
        main = comps[0]
        w(f"  {net:<6} {len(comps)} component(s); main {main['shapes']} shape(s) "
          f"{main['layers']} span {main['span']}\n")
        for frag in comps[1:1 + limit]:
            who = (f", STRANDING {len(frag['terminals'])} terminal(s): "
                   + ", ".join(frag["terminals"][:4])
                   + (" …" if len(frag["terminals"]) > 4 else "")
                   if frag["terminals"] else ", no terminal on it")
            w(f"    fragment {frag['id']}: {frag['shapes']} shape(s) {frag['layers']} "
              f"span {frag['span']} at {frag['bbox']}{who}\n")
        if len(comps) - 1 > limit:
            w(f"    ... and {len(comps) - 1 - limit} more fragment(s) (--json for all)\n")
        for b in c["bridged_by"][:3]:
            w(f"    NOTE {b['terminal']} lands on components {b['components']} — a hard "
              f"macro's own PDN joins them, PSM does not credit that\n")
    if res["fragments"]:
        w(f"\n  {res['fragments']} fragment(s) off the main network, stranding "
          f"{res['stranded_terminals']} terminal(s).  PSM-0040/PSM-0069 and the\n"
          f"  *-grid-errors.rpt are the verdict; this localises it.\n")

    w(f"\n  {res['terminal_counts'].get('connected', 0)} terminal(s) connected, "
      f"{res['floating']} floating\n")


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
                    help="pass with up to N floating power terminals (0)")
    ap.add_argument("--allow-stranded", type=int, default=0, metavar="N",
                    help="pass with up to N terminals on a fragment off the main network (0)")
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
    if res["stranded_terminals"] > a.allow_stranded:
        return 1
    return 0 if res["floating"] <= a.allow_floating else 1


if __name__ == "__main__":
    sys.exit(main())
