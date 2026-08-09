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
  * `+ PARTIAL <maxDensity>` over the corridors — "do not fill this region
    past X%, a bus is planned through it".  That is a real constraint, in
    the direction the plan intends, and expressible.

So the manifest carries the intent and the DEF carries the room to honour it.
"""
import csv
import json
import os


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
    """bundle id -> net names.

    `input.original_bundle` is the accessor the rest of the session uses
    (`_bundle_label`, `_bids_by_net_prefix`); `input.bundle` exists but does
    not carry the names, which is how the first version of this emitted a
    manifest with every `nets` list empty — an artifact whose entire value is
    net identity, shipped without any."""
    out = {}
    for w in getattr(session, "bundles", []) or []:
        try:
            b = w.input.original_bundle
            out[b.id] = list(b.get_net_names())
        except Exception:
            continue
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
        rows = sorted(corr[bid], key=lambda r: (r["seg"], r["layer"]))
        for r in rows:
            r["layer_name"] = layer_names.get(r["layer"], f"L{r['layer']}")
        bundles.append({
            "bundle": bid,
            "nets": sorted(nets.get(bid, [])),
            "n_nets": len(nets.get(bid, [])),
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
            joined = " ".join(b["nets"])
            for c in b["corridors"]:
                w.writerow([b["bundle"], c["seg"], c["layer"], c["layer_name"],
                            c["x1"], c["y1"], c["x2"], c["y2"],
                            b["n_nets"], joined])


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
        nets = " ".join(b["nets"])
        for c in b["corridors"]:
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
    units = 1000
    if getattr(session, "bdb", None) is not None:
        try:
            units = int(session.bdb.units()) or 1000
        except Exception:
            units = 1000
    lu_per_um = 1.0
    if getattr(session, "bdb", None) is not None:
        try:
            lu_per_um = float(session.bdb.import_scale()) or 1.0
        except Exception:
            lu_per_um = 1.0

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
        out.append(f"  - LAYER {lname} + PARTIAL {max_density:g} "
                   f"RECT ( {x1} {y1} ) ( {x2} {y2} ) ;")
    out += ["END BLOCKAGES", "END DESIGN", ""]
    with open(path, "w") as f:
        f.write("\n".join(out))
    return len(hard), len(soft)


class AdvisoryMixin:
    """`emit_guides` / `export_def_blockages` — see the module docstring."""

    def _emit_guides(self, path, margin=0.0, tcl=None, csv_path=None):
        m = build_manifest(self, margin)
        if not m["bundles"]:
            print("Warning: emit_guides has no placed bus segments — run "
                  "run_nuts first (an unplaced plan reserves nothing, and "
                  "emitting it would advertise corridors that do not exist)")
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
