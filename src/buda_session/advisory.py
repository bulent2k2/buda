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

"""The advisory writer (Phase 4 of docs/internal/lefdef_interface_plan.md).

Until now the only export was `export_gds`, and a GDS rectangle carries no
net identity a P&R tool can adopt — which is what made the tool's output "a
picture, not a constraint".

Two artifacts, and the split between them is the whole design:

**4a — the corridor manifest, and it LEADS.**  Per bundle: the nets, the
layer, and the rectangle the plan reserved.  This is the POSITIVE intent —
"route these nets here" — which is the thing BUDA actually computed and the
thing DEF has no way to say.  It ships as data (JSON/CSV) plus a worked Tcl
guide script, because that is the form a router can consume.

**4b — DEF blockages, and they are NEGATIVE ONLY.**  The obvious move —
emitting each corridor as a DEF `BLOCKAGES` rectangle — is exactly backwards:
a blockage tells the router to STAY OUT of that region, so it would forbid
the very routing the plan is asking for.  DEF has no "reserve this for these
nets".  What it can honestly carry is:

  * hard blockages for regions that must stay clear (BUDA's own keepouts),
    which is what a blockage means; and
  * `+ PARTIAL <maxDensity>` PLACEMENT blockages over the corridors — a
    limit on how densely cells may be placed under a planned bus.

That second one is narrower than it first looks, and the narrowing is the
honest part: `PARTIAL` is a PLACEMENT-blockage option in DEF 5.8, not a
layer routing-blockage one, so DEF has **no** way to say "leave routing room
here".  What it can say is "do not pack cells under this", which helps pin
access but is not the reservation.  The routing intent lives in the manifest
and nowhere else — which is the whole reason 4a leads.
"""
import csv
import json
import os

import buda_diag
from .util import ensure_parent_dir


def _seg_net_names(w, all_names):
    """Per-segment net names for one wrapper.

    A tapered fan-in topology (`Topology::seg_bits`) puts only a SUBSET of the
    bundle's bits on each branch — NUTS even sizes the segment from that
    subset.  Naming the whole bundle on every corridor would therefore direct
    nets into branches they never traverse, which defeats the one guarantee
    the manifest exists to make (Codex P1 on #648).  Untapered segments keep
    the full list, which is what an untapered topology means."""
    try:
        sel = w.plan.selected_topology_index
        topo = w.input.candidates[sel]
        bits = dict(topo.seg_bits)
    except Exception:
        return {}
    out = {}
    for seg_idx, idxs in bits.items():
        names = [all_names[i] for i in idxs if 0 <= i < len(all_names)]
        if names:
            out[seg_idx] = sorted(names)
    return out


def _corridors(session, margin):
    """Placed bus segments as reserved rectangles, grouped by bundle.

    A TrackSegment is a span along the routing direction at a track position
    across it; the corridor is that swept rectangle, grown by `margin` on
    every side.  Only PLACED segments count — an unplaced one reserves
    nothing, and emitting it would advertise a corridor the plan never made.
    """
    nr = getattr(session, "nuts_result", None)
    if nr is None:
        return {}
    by_bundle = {}
    for s in nr.segments:
        if not getattr(s, "placed", True):
            continue
        half = s.width / 2.0
        if s.horiz:
            x1, x2 = s.span_lo, s.span_hi
            y1, y2 = s.track_position - half, s.track_position + half
        else:
            y1, y2 = s.span_lo, s.span_hi
            x1, x2 = s.track_position - half, s.track_position + half
        by_bundle.setdefault(s.bundle_id, []).append({
            "seg": s.seg_idx,
            "layer": int(s.layer),
            "x1": round(min(x1, x2) - margin, 6),
            "y1": round(min(y1, y2) - margin, 6),
            "x2": round(max(x1, x2) + margin, 6),
            "y2": round(max(y1, y2) + margin, 6),
        })
    return by_bundle


def _bundle_nets(session):
    """bundle id -> (all net names, {seg_idx: names} for tapered segments).

    `input.original_bundle` is the accessor the rest of the session uses
    (`_bundle_label`, `_bids_by_net_prefix`); `input.bundle` exists but does
    not carry the names, which is how the first version of this emitted a
    manifest with every `nets` list empty — an artifact whose entire value is
    net identity, shipped without any."""
    out = {}
    for w in getattr(session, "bundles", []) or []:
        try:
            b = w.input.original_bundle
            names = list(b.get_net_names())
        except Exception:
            continue
        out[b.id] = (names, _seg_net_names(w, names))
    return out


def build_manifest(session, margin=0.0):
    """The corridor manifest as plain data — the primary artifact.

    Deterministic: bundles in id order, corridors in segment order, so two
    runs of the same design produce the same bytes and a diff means a real
    change.  (The same discipline `gds_io` uses for its export.)
    """
    layer_names = {}
    for name, lid in getattr(session, "_layer_name_map", {}).items():
        layer_names[lid] = name
    corr = _corridors(session, margin)
    nets = _bundle_nets(session)
    bundles = []
    for bid in sorted(corr):
        all_names, per_seg = nets.get(bid, ([], {}))
        rows = sorted(corr[bid], key=lambda r: (r["seg"], r["layer"]))
        for r in rows:
            r["layer_name"] = layer_names.get(r["layer"], f"L{r['layer']}")
            # The nets THIS corridor carries — the whole bundle on an
            # untapered segment, the branch's own bits on a tapered one.
            seg_names = per_seg.get(r["seg"])
            r["nets"] = seg_names if seg_names is not None else sorted(all_names)
            r["n_nets"] = len(r["nets"])
            r["tapered"] = seg_names is not None
        bundles.append({
            "bundle": bid,
            "nets": sorted(all_names),
            "n_nets": len(all_names),
            "corridors": rows,
        })
    return {
        "tool": "buda",
        "artifact": "corridor_manifest",
        "version": 1,
        "margin": margin,
        "units": "layout units — see docs/internal/engine_units.md",
        "note": ("Corridors are the POSITIVE intent: route these nets here. "
                 "They are deliberately NOT emitted as DEF blockages, which "
                 "would tell the router to avoid them."),
        "n_bundles": len(bundles),
        "bundles": bundles,
    }


def write_manifest_json(manifest, path):
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=False)
        f.write("\n")


def write_manifest_csv(manifest, path):
    """One row per corridor rectangle — the form a script can grep."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["bundle", "seg", "layer", "layer_name",
                    "x1", "y1", "x2", "y2", "n_nets", "nets"])
        for b in manifest["bundles"]:
            for c in b["corridors"]:
                w.writerow([b["bundle"], c["seg"], c["layer"], c["layer_name"],
                            c["x1"], c["y1"], c["x2"], c["y2"],
                            c["n_nets"], " ".join(c["nets"])])


def write_guide_tcl(manifest, path):
    """A worked `create_route_guide`-style script.

    Deliberately a WORKED EXAMPLE and not a claim of portability: every P&R
    tool spells this differently, and the manifest above is the artifact
    meant to be consumed programmatically.  What this file demonstrates is
    that the manifest carries enough to write one — net names, a layer, and
    a rectangle — which is precisely what a GDS rectangle does not.
    """
    lines = [
        "# BUDA corridor guides — generated, do not edit.",
        "#",
        "# A worked example in create_route_guide form.  Coordinates are in",
        "# the design's layout units (docs/internal/engine_units.md); if your",
        "# tool wants microns and the design was imported at a DBU scale,",
        "# divide by that scale here.",
        "#",
        f"# {manifest['n_bundles']} bundle(s), margin {manifest['margin']}.",
        "",
    ]
    for b in manifest["bundles"]:
        lines.append(f"# bundle {b['bundle']} — {b['n_nets']} net(s)")
        for c in b["corridors"]:
            # The corridor's OWN nets: a tapered branch carries a subset, and
            # guiding the whole bundle down it would be a wrong instruction,
            # not merely a loose one.
            nets = " ".join(c["nets"])
            lines.append(
                f"create_route_guide -net_list {{{nets}}} "
                f"-layer {c['layer_name']} "
                f"-rect {{{c['x1']} {c['y1']} {c['x2']} {c['y2']}}}")
        lines.append("")
    with open(path, "w") as f:
        f.write("\n".join(lines))


def write_def_blockages(session, manifest, path, design="buda_advisory",
                        max_density=None):
    """4b — the DEF, carrying only what DEF can honestly say.

    NOT the corridors as blockages: that would forbid the routing the plan
    asks for.  What goes in is
      * the design's real keepouts, as hard blockages (that IS what a
        blockage means), and
      * optionally `+ PARTIAL <maxDensity>` over each corridor — "leave room
        here", which is the negative shadow of the positive intent and the
        only part of it DEF can express.

    Deterministic ordering throughout, like `gds_io`'s export, so a diff
    between two runs means a real change.
    """
    units, lu_per_um = dbu_scale(session)

    def dbu(v):
        # layout units -> µm -> DEF database units.
        return int(round(v / lu_per_um * units))

    names = {lid: n for n, lid in getattr(session, "_layer_name_map", {}).items()}
    hard = []
    for z in (session.fp.get_keepout_zones() if session.fp else []):
        for lid in sorted(z.layer_ids):
            hard.append((names.get(lid, f"L{lid}"),
                         dbu(z.bbox.x1), dbu(z.bbox.y1),
                         dbu(z.bbox.x2), dbu(z.bbox.y2)))
    hard.sort()

    soft = []
    if max_density is not None:
        for b in manifest["bundles"]:
            for c in b["corridors"]:
                soft.append((c["layer_name"], dbu(c["x1"]), dbu(c["y1"]),
                             dbu(c["x2"]), dbu(c["y2"])))
        soft.sort()

    die_w = die_h = 0
    if getattr(session, "bdb", None) is not None:
        try:
            die_w, die_h = dbu(session.bdb.die_w()), dbu(session.bdb.die_h())
        except Exception:
            pass

    out = ["VERSION 5.8 ;",
           f"DESIGN {design} ;",
           f"UNITS DISTANCE MICRONS {units} ;"]
    if die_w and die_h:
        out.append(f"DIEAREA ( 0 0 ) ( {die_w} {die_h} ) ;")
    out.append(f"BLOCKAGES {len(hard) + len(soft)} ;")
    for lname, x1, y1, x2, y2 in hard:
        out.append(f"  - LAYER {lname} RECT ( {x1} {y1} ) ( {x2} {y2} ) ;")
    for lname, x1, y1, x2, y2 in soft:
        # `PARTIAL maxDensity` is a PLACEMENT-blockage option in the DEF 5.8
        # grammar, NOT a layer routing-blockage one.  Writing it on a `LAYER`
        # blockage produces a file a standards-compliant tool may reject or
        # ignore — and our own permissive reader accepts it, which is exactly
        # why the round-trip test did not expose it (Codex P1 on #648).
        #
        # So the honest emission is a PLACEMENT blockage, and the honest
        # CLAIM is narrower than "reserve this corridor": it limits placement
        # density under the planned bus.  DEF has no routing-density concept
        # at all, so the routing intent lives in the manifest and nowhere
        # else.  The layer is carried as a comment because a PLACEMENT
        # blockage has no layer field.
        out.append(f"  # corridor on {lname}")
        out.append(f"  - PLACEMENT + PARTIAL {max_density:g} "
                   f"RECT ( {x1} {y1} ) ( {x2} {y2} ) ;")
    out += ["END BLOCKAGES", "END DESIGN", ""]
    ensure_parent_dir(path)                 # create flow/def/out/ etc. if absent
    with open(path, "w") as f:
        f.write("\n".join(out))
    return len(hard), len(soft)




# ── the OpenROAD guide file ──────────────────────────────────────────────
#
# The manifest above is BUDA's own data.  What a router READS is a guide
# file — the ISPD-contest form TritonRoute's `read_guides` takes:
#
#     <net>
#     (
#     x1 y1 x2 y2 <layer>
#     )
#
# and phase 0 of docs/internal/librelane_hier_flow.md measured four things
# about it that this writer is built around (all 2026-09-05, §8 step 5):
#
#   * A guide is a set of GCELLS, not rectangles: a box that covers no whole
#     gcell has no index and stops the router (`DRT-0229 genGuides_split
#     split_indices is empty`).  So every box here is gcell-aligned, and the
#     gcell comes from the DEF's own `GCELLGRID` when the design was
#     imported from one, else from `gcell <um>`.
#   * Two boxes on adjacent layers connect only where they SHARE a gcell
#     (`DRT-0218 Guide is not connected to design` otherwise).  A stub and
#     its trunk meet at a point; the box for each is derived with the same
#     rule — the gcell CONTAINING each endpoint, floor at both ends — so the
#     junction gcell is in both by construction, and a via self-check says
#     so (or names the one that is not).
#   * Net names are DEF-escaped in a routed database (`mid\[0\]`), and that
#     is how OpenROAD's own writer spells them; `plain_names` turns it off.
#   * The router must reach the PINS: a guide that stops short of a pin is
#     "not connected to design".  Where the design carries pin positions (a
#     BDB imported from a DEF+LEF), each free end of a net's wire — an end
#     that is not a via, i.e. a block landing — is joined to the net's
#     nearest pin by a strip of gcells, on the wire's layer and on each
#     `terminal` layer (the pin's layer, which BUDA does not model).
#
# Per-bit boxes come from detailed NUTS when it ran (one gcell row per bit
# — the corridor at track resolution); with only abstract NUTS every net of
# a corridor gets the corridor's gcells (the taper rule of the manifest
# applies: a branch names its own bits).

import math


def dbu_scale(session):
    """(units, lu_per_um): DEF database units per micron, and the design's
    layout units per micron (`set_import_scale`); (1000, 1.0) with no BDB."""
    units, lu_per_um = 1000, 1.0
    bdb = getattr(session, "bdb", None)
    if bdb is not None:
        try:
            units = int(bdb.units()) or 1000
        except Exception:
            units = 1000
        try:
            lu_per_um = float(bdb.import_scale()) or 1.0
        except Exception:
            lu_per_um = 1.0
    return units, lu_per_um


def escape_def_name(name):
    """`mid[0]` -> `mid\\[0\\]`, the spelling a routed OpenROAD database and
    its `write_guides` use for a non-port net."""
    return name.replace("[", "\\[").replace("]", "\\]")


def gcell_from_def(path):
    """The DEF's GCELLGRID as a GcellGrid in DBU, or None when it has none.

    Per axis the entry with the MOST gcells is the grid: a DEF routinely
    carries a second one-or-two-cell entry at the die edge (a partial gcell
    closing the die), which is not the period the router indexes by."""
    import buda
    d = buda.read_def(path)
    best = {}
    for g in d.gcellgrid:
        ax = str(g.dir).lower()
        if ax not in ("x", "y") or g.step <= 0 or g.count <= 0:
            continue
        if ax not in best or g.count > best[ax][2]:
            best[ax] = (int(round(g.start)), int(round(g.step)), int(g.count))
    if "x" not in best or "y" not in best:
        return None
    return GcellGrid(best["x"][0], best["x"][1], best["y"][0], best["y"][1],
                     units=int(d.units) or 1000, source="DEF GCELLGRID")


class GcellGrid:
    """A gcell grid in DBU: per axis an origin and a period.

    `cover(lo, hi, axis)` is THE alignment rule: the gcells containing every
    point of [lo, hi], floor at both ends, as a DBU interval.  A point is in
    exactly one gcell under this rule, so two wires meeting at a point are
    put in the same gcell whichever of them asks — which is what makes a
    junction connected in the router's eyes (DRT-0218)."""

    def __init__(self, x_start, x_step, y_start, y_step, units=1000, source="gcell"):
        if x_step <= 0 or y_step <= 0:
            raise ValueError("gcell period must be positive")
        self.start = {"x": int(x_start), "y": int(y_start)}
        self.step = {"x": int(x_step), "y": int(y_step)}
        self.units = units
        self.source = source

    def index(self, v, axis):
        return math.floor((v - self.start[axis]) / self.step[axis])

    def cover(self, lo, hi, axis):
        if hi < lo:
            lo, hi = hi, lo
        i0, i1 = self.index(lo, axis), self.index(hi, axis)
        st, sp = self.start[axis], self.step[axis]
        return st + i0 * sp, st + (i1 + 1) * sp

    def cell(self, x, y):
        """The one gcell containing (x, y), as (x1, y1, x2, y2)."""
        x1, x2 = self.cover(x, x, "x")
        y1, y2 = self.cover(y, y, "y")
        return x1, y1, x2, y2

    def describe(self):
        um = lambda v: f"{v / self.units:g}"
        return (f"{um(self.step['x'])} x {um(self.step['y'])} um from "
                f"({um(self.start['x'])}, {um(self.start['y'])}) [{self.source}]")


def _pin_positions(session):
    """net name -> [(x, y)] in layout units, from the BDB's pin table; {}
    without a BDB.  Pins of an UNPLACED component (bbox -1) are skipped —
    their position is unknown, not (-1, -1)."""
    bdb = getattr(session, "bdb", None)
    if bdb is None:
        return {}
    try:
        net_name = {n.id: n.name for n in bdb.all_nets()}
        out = {}
        for c in bdb.all_components():
            x1, x2 = getattr(c, "x1", 0), getattr(c, "x2", 0)
            if x1 < 0 and x2 < 0:
                continue
            for pin in bdb.pins_by_comp(c.id):
                nm = net_name.get(pin.net_id)
                if nm is None:
                    continue
                if pin.px < 0 and pin.py < 0:
                    # The pin's OWN unknown-position sentinel: a placed
                    # component whose LEF lacks this pin still gets the
                    # connection row, at (-1, -1) (Codex #880).
                    continue
                out.setdefault(nm, []).append((float(pin.px), float(pin.py)))
        return out
    except Exception:
        return {}


_EPS = 1e-6


def build_guides(session, grid, terminal_layers=(), escape=True):
    """(guides, report): guides = {net: [(x1, y1, x2, y2, layer_name)]} in
    DBU, gcell-aligned; report = counts and the via self-check.

    Detailed NUTS ran: one box per bit-wire (its span's gcells on its track's
    gcell row) — the corridor at track resolution — plus a strip from every
    FREE END (an end that is not a via of that bit: a block landing) to the
    net's nearest pin on the wire's layer and each `terminal` layer.  Only
    abstract NUTS: every net a corridor names gets the corridor's gcells,
    and pin gcells on the `terminal` layers alone.
    """
    import buda
    units, lu_per_um = dbu_scale(session)

    def dbu(v):
        return int(round(v / lu_per_um * units))

    names_by_id = {lid: n for n, lid in getattr(session, "_layer_name_map", {}).items()}

    def lname(lid):
        return names_by_id.get(int(lid), f"L{int(lid)}")

    h_layers = set(session.layers.get_layer_ids_by_dir(buda.LayerDir.HORIZONTAL))
    guides = {}
    rep = {"source": "none", "wires": 0, "vias": 0, "vias_unshared": [],
           "terminals": 0, "pin_strips": 0, "shields": 0, "unnamed": 0}

    def add(net, box, lid):
        guides.setdefault(net, []).append((*box, lname(lid)))

    def wire_box(horiz, lo, hi, track):
        if horiz:
            x1, x2 = grid.cover(lo, hi, "x")
            y1, y2 = grid.cover(track, track, "y")
        else:
            y1, y2 = grid.cover(lo, hi, "y")
            x1, x2 = grid.cover(track, track, "x")
        return x1, y1, x2, y2

    pins = _pin_positions(session)
    free_ends = []                         # (net, layer id, x, y) in layout units
    dr = getattr(session, "detailed_result", None)
    if dr is not None and len(dr.net_segments):
        rep["source"] = "detailed"
        bid_names = {w.input.original_bundle.id:
                     list(w.input.original_bundle.get_net_names())
                     for w in session.bundles}
        vias_at = {}
        for v in dr.net_vias:
            for seg in (v.from_seg, v.to_seg):
                vias_at.setdefault((v.bundle_id, v.bit_index, seg), []).append((v.x, v.y))
        for ns in dr.net_segments:
            if ns.is_shield:
                rep["shields"] += 1
                continue
            names = bid_names.get(ns.bundle_id, [])
            if not 0 <= ns.bit_index < len(names):
                rep["unnamed"] += 1
                continue
            net = names[ns.bit_index]
            horiz = int(ns.layer) in h_layers
            lo, hi = sorted((ns.span_lo, ns.span_hi))
            add(net, wire_box(horiz, dbu(lo), dbu(hi), dbu(ns.track_position)), ns.layer)
            rep["wires"] += 1
            t = ns.track_position
            ends = [(lo, t), (hi, t)] if horiz else [(t, lo), (t, hi)]
            vs = vias_at.get((ns.bundle_id, ns.bit_index, ns.seg_idx), [])
            for x, y in ends:
                if not any(abs(x - vx) <= _EPS and abs(y - vy) <= _EPS for vx, vy in vs):
                    free_ends.append((net, int(ns.layer), x, y))
        for v in dr.net_vias:
            names = bid_names.get(v.bundle_id, [])
            if not 0 <= v.bit_index < len(names):
                continue
            net = names[v.bit_index]
            cx1, cy1, cx2, cy2 = grid.cell(dbu(v.x), dbu(v.y))
            for lid in (v.from_layer, v.to_layer):
                ln = lname(lid)
                if not any(b[4] == ln and b[0] <= cx1 and cx2 <= b[2]
                           and b[1] <= cy1 and cy2 <= b[3] for b in guides.get(net, [])):
                    rep["vias_unshared"].append((net, ln, v.x, v.y))
            rep["vias"] += 1
    else:
        corr = _corridors(session, 0.0)
        if corr:
            rep["source"] = "abstract"
        nets = _bundle_nets(session)
        for bid in sorted(corr):
            all_names, per_seg = nets.get(bid, ([], {}))
            for r in corr[bid]:
                seg_names = per_seg.get(r["seg"])
                seg_names = seg_names if seg_names is not None else sorted(all_names)
                x1, x2 = grid.cover(dbu(r["x1"]), dbu(r["x2"]), "x")
                y1, y2 = grid.cover(dbu(r["y1"]), dbu(r["y2"]), "y")
                for net in seg_names:
                    add(net, (x1, y1, x2, y2), r["layer"])
                rep["wires"] += 1

    term_ids = [int(l) for l in terminal_layers]
    stack = sorted(int(l) for l in names_by_id)      # the declared layers, bottom to top

    def between(a, b):
        """Every declared layer from a to b inclusive: a strip must be on
        each of them, since adjacent-layer boxes connect only where they
        share a gcell (Codex #880 — met5 to met3 needs met4 too)."""
        lo, hi = min(a, b), max(a, b)
        return [l for l in stack if lo <= l <= hi] or [a, b]

    def strip_layers(lid):
        out = {lid}
        for t in term_ids:
            out.update(between(lid, t))
        return sorted(out)

    if rep["source"] == "detailed":
        for net, lid, x, y in free_ends:
            gx1, gy1, gx2, gy2 = grid.cell(dbu(x), dbu(y))
            near = pins.get(net)
            if near:
                px, py = min(near, key=lambda p: abs(p[0] - x) + abs(p[1] - y))
                px1, py1, px2, py2 = grid.cell(dbu(px), dbu(py))
                strip = (min(gx1, px1), min(gy1, py1), max(gx2, px2), max(gy2, py2))
                rep["pin_strips"] += 1
            else:
                strip = (gx1, gy1, gx2, gy2)
            for l in strip_layers(lid):
                add(net, strip, l)
                rep["terminals"] += 1
    else:
        # Abstract corridors know no free ends; the corridor itself is
        # joined to each pin: from the pin's gcell to the gcell of the
        # nearest point of the net's nearest box, on that box's layer, the
        # terminal layers and everything between (a lone pin gcell was two
        # disconnected regions, Codex #880).
        ids_by_name = {n: l for l, n in names_by_id.items()}
        for net in list(guides):
            for px, py in pins.get(net, []):
                pxd, pyd = dbu(px), dbu(py)
                px1, py1, px2, py2 = grid.cell(pxd, pyd)
                best = None
                for b in guides[net]:
                    cx = min(max(pxd, b[0]), b[2] - 1)   # the box's point nearest the pin
                    cy = min(max(pyd, b[1]), b[3] - 1)
                    d = abs(cx - pxd) + abs(cy - pyd)
                    if best is None or d < best[0]:
                        best = (d, cx, cy, ids_by_name.get(b[4], stack[0] if stack else 0))
                if best is None:
                    continue
                _d, cx, cy, lid = best
                bx1, by1, bx2, by2 = grid.cell(cx, cy)
                strip = (min(bx1, px1), min(by1, py1), max(bx2, px2), max(by2, py2))
                for l in strip_layers(lid):
                    add(net, strip, l)
                    rep["terminals"] += 1
                rep["pin_strips"] += 1

    guides = {net: merge_boxes(bx) for net, bx in guides.items()}
    rep["nets"] = len(guides)
    rep["boxes"] = sum(len(b) for b in guides.values())
    rep["escape"] = escape
    rep["disconnected"] = [net for net in sorted(guides)
                           if guide_components(guides[net], grid, stack, names_by_id) > 1]
    return guides, rep


def guide_components(boxes, grid, stack, names_by_id):
    """How many connected sets of gcells a net's guide is, under the
    router's rule: two gcells connect when they are side by side on one
    layer, or the SAME gcell on two ADJACENT layers of the stack.  One is a
    guide the router accepts; more is DRT-0218 waiting to happen — every
    finding on #880 was a case of it, so the writer checks the predicate
    itself rather than each cause separately."""
    ids_by_name = {n: l for l, n in names_by_id.items()}
    rank = {l: i for i, l in enumerate(stack)}
    cells = set()
    for x1, y1, x2, y2, ln in boxes:
        r = rank.get(ids_by_name.get(ln, -1))
        if r is None:
            continue
        i0, i1 = grid.index(x1, "x"), grid.index(x2 - 1, "x")
        j0, j1 = grid.index(y1, "y"), grid.index(y2 - 1, "y")
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                cells.add((i, j, r))
    parent = {c: c for c in cells}

    def find(c):
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    for (i, j, r) in cells:
        for n in ((i + 1, j, r), (i, j + 1, r), (i, j, r + 1)):
            if n in cells:
                a, b = find((i, j, r)), find(n)
                if a != b:
                    parent[a] = b
    return len({find(c) for c in cells})


def merge_boxes(boxes):
    """Union same-layer boxes that are contained, or abut/overlap along one
    axis with the other axis identical — fewer, equivalent boxes.
    Deterministic (sorted at every step)."""
    boxes = sorted(set(boxes))
    changed = True
    while changed:
        changed, out = False, []
        for b in boxes:
            for i, o in enumerate(out):
                if o[4] != b[4]:
                    continue
                if o[0] <= b[0] and o[1] <= b[1] and b[2] <= o[2] and b[3] <= o[3]:
                    changed = True                       # contained
                    break
                if b[0] <= o[0] and b[1] <= o[1] and o[2] <= b[2] and o[3] <= b[3]:
                    out[i], changed = b, True            # contains
                    break
                if o[1] == b[1] and o[3] == b[3] and not (b[0] > o[2] or b[2] < o[0]):
                    out[i] = (min(o[0], b[0]), o[1], max(o[2], b[2]), o[3], o[4])
                    changed = True
                    break
                if o[0] == b[0] and o[2] == b[2] and not (b[1] > o[3] or b[3] < o[1]):
                    out[i] = (o[0], min(o[1], b[1]), o[2], max(o[3], b[3]), o[4])
                    changed = True
                    break
            else:
                out.append(b)
        boxes = sorted(set(out))
    return boxes


def write_guide_file(path, guides, escape=True):
    """The ISPD form; nets in name order, boxes in coordinate order."""
    lines = []
    for net in sorted(guides):
        lines.append(escape_def_name(net) if escape else net)
        lines.append("(")
        for x1, y1, x2, y2, layer in guides[net]:
            lines.append(f"{x1} {y1} {x2} {y2} {layer}")
        lines.append(")")
    with open(path, "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


class AdvisoryMixin:
    """`emit_guides` / `export_def_blockages` — see the module docstring."""

    def _emit_guide_file(self, path, gcell_um=None, terminal=(), escape=True):
        """`emit_guides <file.guide> [gcell <um>] [terminal <layers>] [plain_names]`."""
        units, _lu = dbu_scale(self)
        grid = getattr(self, "_def_gcell", None)
        if gcell_um is not None:
            if gcell_um <= 0:
                print("Error: emit_guides gcell must be a positive length in um")
                return
            step = int(round(gcell_um * units))
            grid = GcellGrid(0, step, 0, step, units=units, source="gcell option")
        if grid is None:
            print("Error: emit_guides needs the router's gcell size for a "
                  ".guide file — a guide is a set of gcells, and a box off "
                  "the gcell grid stops the router (DRT-0229).  Import the "
                  "design from a DEF carrying GCELLGRID, or pass "
                  "`gcell <um>` (the GCELLGRID STEP detailed routing prints).")
            return
        term_ids = []
        for name in terminal:
            lid = self._layer_name_map.get(name)
            if lid is None:
                print(f"Error: emit_guides terminal layer '{name}' is not a "
                      f"declared layer ({', '.join(sorted(self._layer_name_map)) or 'none'})")
                return
            term_ids.append(lid)
        if getattr(self, "nuts_result", None) is None:
            buda_diag.emit("BUDA-1701",
                           "emit_guides has no placed bus segments — run "
                           "run_nuts first (an unplaced plan reserves "
                           "nothing, and emitting it would advertise "
                           "corridors that do not exist)")
        guides, rep = build_guides(self, grid, term_ids, escape)
        ensure_parent_dir(path)
        write_guide_file(path, guides, escape)
        per_layer = {}
        for bx in guides.values():
            for b in bx:
                per_layer[b[4]] = per_layer.get(b[4], 0) + 1
        layers = ", ".join(f"{k} {v}" for k, v in sorted(per_layer.items()))
        print(f"[Advisory] guides -> {path}: {rep['nets']} net(s), {rep['boxes']} "
              f"gcell box(es) [{layers}] from {rep['source']} NUTS "
              f"({rep['wires']} wire(s)); gcell {grid.describe()}; "
              f"{rep['terminals']} terminal box(es), {rep['pin_strips']} joined to a pin"
              + (f", {rep['shields']} shield wire(s) not guided" if rep["shields"] else "")
              + ("" if rep["escape"] else "; names plain"))
        if rep["disconnected"]:
            d = rep["disconnected"]
            print(f"WARNING: emit_guides: {len(d)} net(s) whose guide is not ONE connected "
                  f"set of gcells (side-by-side on a layer, or the same gcell on adjacent "
                  f"layers) — the router will report DRT-0218; first: {d[0]}")
        else:
            print(f"[Advisory] guides: every net's guide is one connected set of gcells")
        if rep["source"] == "detailed":
            bad = rep["vias_unshared"]
            if bad:
                net, ln, x, y = bad[0]
                print(f"WARNING: emit_guides: {len(bad)} via(s) whose gcell is not in "
                      f"the net's boxes on both layers — the router will report the "
                      f"guide as not connected (DRT-0218); first: {net} on {ln} at "
                      f"({x:g}, {y:g})")
            else:
                print(f"[Advisory] guides: every one of {rep['vias']} via(s) sits in a "
                      f"gcell its net holds on both layers")
        elif rep["source"] == "abstract":
            print("[Advisory] guides: from ABSTRACT bus segments — every net of a "
                  "corridor gets the corridor's gcells; run run_detailed_nuts "
                  "first for per-bit boxes"
                  + ("" if term_ids else "; no `terminal` layers, so the pins' own "
                                          "gcells are not added"))
        if rep["unnamed"]:
            print(f"WARNING: emit_guides: {rep['unnamed']} bit-wire(s) with no net name "
                  f"were not guided")

    def _emit_guides(self, path, margin=0.0, tcl=None, csv_path=None):
        m = build_manifest(self, margin)
        if not m["bundles"]:
            buda_diag.emit("BUDA-1701",
                           "emit_guides has no placed bus segments — run "
                           "run_nuts first (an unplaced plan reserves "
                           "nothing, and emitting it would advertise "
                           "corridors that do not exist)")
        for p in (path, csv_path, tcl):     # create flow/def/out/ etc. if absent
            if p:
                ensure_parent_dir(p)
        root, ext = os.path.splitext(path)
        if ext.lower() == ".csv":
            write_manifest_csv(m, path)
        else:
            write_manifest_json(m, path)
        n_corr = sum(len(b["corridors"]) for b in m["bundles"])
        print(f"[Advisory] corridor manifest -> {path} "
              f"({m['n_bundles']} bundle(s), {n_corr} corridor(s), "
              f"margin {margin:g})")
        if csv_path:
            write_manifest_csv(m, csv_path)
            print(f"[Advisory] corridor CSV -> {csv_path}")
        if tcl:
            write_guide_tcl(m, tcl)
            print(f"[Advisory] route guides (worked example) -> {tcl}")
        return m
