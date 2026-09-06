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

"""`emit_block_size` — how big a block has to be (LibreLane phase 1).

The §5 row this closes reads "`DIE_AREA` + `FP_SIZING absolute` | rule in
`flow/tcl/tpu_lib.tcl` (face-capacity aware); a command, NEW", and the
first F-vs-H pair says why it matters: at N = 4 arm H's die is **8.66x**
arm F's, and that gap is not the cost of hierarchy — it is the emitter
padding each cell to its bus faces (`PEPAD`) while the flat arm derives
its die from CELL AREA at 46 % utilization.  So a hierarchical block is
sized by whichever of TWO demands is larger, and the useful output is
WHICH ONE BINDS:

  * **AREA** — the logic has to fit: `area / utilization`, shaped by an
    aspect ratio.  This is the only demand a flat flow has, and it is the
    one `tpu_lib.tcl` never applies.
  * **FACE** — a bus has to LAND: every bit that reaches a face needs its
    own signal track there, so the face must be at least
    `eff_bus_width(bits)` long on the layer the bits arrive on (BUDA's own
    width model, the same one the planner charges with, so the size and
    the route agree by construction).  West/east faces constrain the
    block's HEIGHT, north/south its WIDTH.

The face demand is read off the ROUTED PLAN — the same landings
`emit_pin_def` writes pins at, through the same `_plan_block` — so a
design routed with a different bundling or a different stack re-sizes
itself, and a block whose bus does not fit is reported at SIZING rather
than as stranded bits six stages later (which is how `tpu_lib.tcl`'s own
comment says the rule was learned: PEW 60 against a 24-bit psum stranded
672 of 832 bits, and widening the CHANNEL made it worse).

What it writes is a JSON fragment — `DIE_AREA`, `FP_SIZING absolute` and
the derivation — because that is what a LibreLane config takes and what a
harness can diff.  It never writes a config: which keys a block's config
carries is the flow's business, not the sizer's.
"""
import json
import math

from .pin_def import (_block_pins, _layer_names, _net_of, _plan_block,
                      _min_signal_width, _wires)
from .util import ensure_parent_dir

_FACE_AXIS = {"W": "h", "E": "h", "N": "w", "S": "w"}


def _face_demand(session, faces, margin):
    """{face: (bits, layer id, extent needed)} — the bits landing on each
    face need one signal track each on the layer they arrive on, so the
    face must span `eff_bus_width(bits)` plus a margin at each end.

    A face carrying bits on SEVERAL layers is charged the SUM of each
    layer's demand: two layers' tracks are independent, but the pins share
    the one face, so the extents add."""
    out = {}
    for face, per_layer in faces.items():
        total, worst = 2.0 * margin, None
        for lid, bits in sorted(per_layer.items()):
            w = session.layers.eff_bus_width(bits, 0.0, lid)
            if w <= 0:
                # No pattern on the layer: fall back to the minimum wire
                # width doubled (wire + space), which is what an unpatterned
                # layer's dilution assumes.
                w = 2.0 * (_min_signal_width(session, lid) or 1.0) * bits
            total += w
            if worst is None or bits > worst[1]:
                worst = (lid, bits)
        out[face] = (sum(per_layer.values()), worst[0] if worst else None,
                     total)
    return out


def emit_block_size(session, path, target, area=None, util=None, aspect=None,
                    margin=0.0, metrics_path=None, inst=None):
    """Write the sizing fragment; returns the dict written, or None on a
    refusal (printed)."""
    if util is None:
        util = 50.0
    if not 0 < util <= 100:
        print(f"Error: emit_block_size util must be a percentage in (0, 100], "
              f"got {util:g}")
        return None
    if aspect is not None and aspect <= 0:
        print(f"Error: emit_block_size aspect must be positive, got {aspect:g}")
        return None
    if margin < 0:
        print("Error: emit_block_size margin must not be negative")
        return None
    if metrics_path is not None:
        try:
            m = json.load(open(metrics_path))
        except (OSError, ValueError) as e:
            print(f"Error: emit_block_size cannot read metrics {metrics_path}: {e}")
            return None
        got = m.get("design__instance__area")
        if got is None:
            print(f"Error: emit_block_size: {metrics_path} has no "
                  f"'design__instance__area' — is it a LibreLane metrics.json?")
            return None
        if area is not None and abs(float(got) - area) > 1e-9:
            print(f"Error: emit_block_size: both `area` ({area:g}) and "
                  f"`metrics` ({float(got):g}) were given and they differ — "
                  f"pass one")
            return None
        area = float(got)

    lname = _layer_names(session)
    names_for = lambda lid: lname.get(lid, f"L{lid}")     # noqa: E731

    # ── the instances whose landings define the faces ──────────────────────
    bdb = getattr(session, "bdb", None)
    fp = session.fp
    instances = []
    if bdb is not None:
        for c in bdb.all_components():
            if c.cell != target or (c.x1 < 0 and c.x2 < 0):
                continue
            if inst is not None and c.name != inst:
                continue
            instances.append((c.name, (float(c.x1), float(c.y1),
                                       float(c.x2), float(c.y2)), c))
    if not instances:
        if inst is not None:
            print(f"Error: emit_block_size: '{inst}' is not a placed instance "
                  f"of cell '{target}'")
            return None
        if not fp.has_block(target):
            print(f"Error: emit_block_size: no cell or block named '{target}' "
                  f"in the design")
            return None
        r = fp.get_block_bounds(target)
        comp = None
        if bdb is not None:
            comp = next((c for c in bdb.all_components() if c.name == target),
                        None)
        instances.append((target, (r.x1, r.y1, r.x2, r.y2), comp))

    sizes = {(bx[2] - bx[0], bx[3] - bx[1]) for _n, bx, _c in instances}
    if len(sizes) > 1:
        print(f"Error: emit_block_size: instances of '{target}' differ in "
              f"size: {sorted(sizes)} — one size cannot describe them")
        return None
    cur_w, cur_h = next(iter(sizes))

    # ── the face demand, from the routed plan ─────────────────────────────
    wires, source = _wires(session)
    if not wires:
        print("Error: emit_block_size has no routed plan to read faces from "
              "— run run_nuts (and preferably run_detailed_nuts) first, or "
              "size by area alone with `faces off`")
        return None
    net_of = _net_of(session)
    faces = {}          # face -> {layer id: bits}
    per_inst = {}
    for name, bx, comp in instances:
        pins_on = _block_pins(session, name, comp)
        planned, _missed, _notes = _plan_block(
            session, name, bx, pins_on, wires, net_of, 0.0,
            lambda lid: _min_signal_width(session, lid))
        seen = {}
        for p in planned.values():
            seen.setdefault(p.face, {})
            seen[p.face][p.layer] = seen[p.face].get(p.layer, 0) + 1
        per_inst[name] = seen
        for face, per_layer in seen.items():
            for lid, n in per_layer.items():
                faces.setdefault(face, {})
                faces[face][lid] = max(faces[face].get(lid, 0), n)
    if not faces:
        print(f"Error: emit_block_size: no routed bit-wire lands on any face "
              f"of '{target}' — nothing constrains its faces")
        return None

    demand = _face_demand(session, faces, margin)
    need_w = max([d[2] for f, d in demand.items() if _FACE_AXIS[f] == "w"],
                 default=0.0)
    need_h = max([d[2] for f, d in demand.items() if _FACE_AXIS[f] == "h"],
                 default=0.0)

    # ── the area demand ───────────────────────────────────────────────────
    area_w = area_h = 0.0
    if area is not None:
        core = area / (util / 100.0)
        r = aspect if aspect is not None else (
            (need_w / need_h) if need_w > 0 and need_h > 0 else 1.0)
        area_w, area_h = math.sqrt(core * r), math.sqrt(core / r)

    w, h = max(need_w, area_w), max(need_h, area_h)
    if w <= 0 or h <= 0:
        print(f"Error: emit_block_size: '{target}' has no demand on one axis "
              f"({w:g} x {h:g}) — give `area` so the logic sizes it")
        return None
    binds_w = "face" if need_w >= area_w else "area"
    binds_h = "face" if need_h >= area_h else "area"

    out = {
        "cell": target,
        "FP_SIZING": "absolute",
        "DIE_AREA": [0, 0, round(w, 3), round(h, 3)],
        "derivation": {
            "binds": {"w": binds_w, "h": binds_h},
            "face": {f: {"bits": d[0], "layer": names_for(d[1]),
                         "needs": round(d[2], 3)}
                     for f, d in sorted(demand.items())},
            "face_needs": {"w": round(need_w, 3), "h": round(need_h, 3)},
            "area": (None if area is None else {
                "instance_area": round(area, 3),
                "utilization_pct": util,
                "aspect": round(w / h, 4),
                "needs": {"w": round(area_w, 3), "h": round(area_h, 3)}}),
            "margin": margin,
            "source": source,
            "instances": sorted(per_inst),
            "current": {"w": round(cur_w, 3), "h": round(cur_h, 3),
                        "area_ratio": (round(cur_w * cur_h / (w * h), 4)
                                       if w * h else None)},
        },
    }
    ensure_parent_dir(path)
    with open(path, "w") as f:
        json.dump(out, f, indent=4, sort_keys=True)
        f.write("\n")

    face_txt = ", ".join(
        f"{f} {d[0]}b on {names_for(d[1])} needs {d[2]:g}"
        for f, d in sorted(demand.items()))
    print(f"[BlockSize] {path}: {target} {w:g} x {h:g} "
          f"(w binds on {binds_w}, h binds on {binds_h}); {source} faces: {face_txt}"
          + (f"; area {area:g} at {util:g}% needs {area_w:g} x {area_h:g}"
             if area is not None else "; no area given — FACE ONLY, so this "
             "is a floor, not a size"))
    ratio = cur_w * cur_h / (w * h) if w * h else 0
    if abs(ratio - 1.0) > 0.005:
        print(f"[BlockSize] the design's current {cur_w:g} x {cur_h:g} is "
              f"{ratio:.2f}x this area")
    if area is None:
        print("[BlockSize] note: no `area`/`metrics` — the logic's own demand "
              "is unknown, so a block sized from this alone may not hold its "
              "cells; harden once at any size and pass its metrics.json")
    return out
