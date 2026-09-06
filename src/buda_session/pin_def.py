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

"""The pin-DEF writer — BUDA's block-side handoff (`emit_pin_def`).

The "block pins at exact positions" row of docs/internal/librelane_hier_flow.md
§5: for one block (a flat block, or every instance of one CELL in a hier
session) write a DEF 5.8 whose `PINS` section puts each of the block's pins
where the routed bit-wire of that pin's net MEETS the block face, on the
bit-wire's layer.  LibreLane's `Odb.ApplyDEFTemplate` (`FP_DEF_TEMPLATE`)
copies the die area and those pin locations into the block's own hardening,
matched by name — so the block is hardened with its pins exactly where the
top-level plan needs them, and the bus between two blocks is straight by
construction.  `flow/librelane/phase0/reg32/gen_pins_def.py` is the same
file written by hand; this replaces it.

Three facts phase 0 measured decide the shape (§8 step 3):

* OpenROAD RE-CENTRES every pin it writes back: the template's rectangle
  `( 0 -150 ) ( 2000 150 ) + PLACED ( 0 8500 )` comes out as `( -1000 -150 )
  ( 1000 150 ) + PLACED ( 1000 8500 )` — the same metal, a different origin.
  So the writer emits the rectangle SYMMETRIC about the PLACED point (what the
  tool will write back) and the verifier (`tools/pin_def_verify.py`) compares
  ABSOLUTE rectangles, never origins.
* A pin sits on a signal TRACK of its layer or the template is refused.  A
  pin placed where a DetailedNUTS bit-wire meets the face is on a track by
  construction; the abstract fallback (no `run_detailed_nuts`) is NOT, and
  says so.
* Pin names are written PLAIN (`d[0]`), NOT in the escaped spelling
  OpenROAD uses when it WRITES a DEF.  Measured on the phase-0 toy: odb reads
  a template `d\\[16\\]` back as the name `d[16\\]` (it consumes the leading
  escape and keeps the trailing one), so all 66 pins were reported "not found
  in design layout" and `ApplyDEFTemplate` exited 2.  `def_escape` is kept for
  the `escaped_names` opt-in, which is there for the day a tool wants the
  other spelling — nothing here needs it.

TEMPLATE SEMANTICS for a cell: a cell is hardened ONCE and placed N times, so
every instance must agree on where each pin is in CELL-LOCAL coordinates.  A
pin routed on two instances at different local positions is a hard error
naming both; a pin routed on only some instances takes its position from
those (u0's `q` and u1's `d` on the phase-0 toy each come from one instance);
a pin routed nowhere — `clk`/`rst`, which reach the block on no bus — is
SPREAD evenly on the `unrouted` edge on that edge's pin layer's tracks.  Only
orientation `N` is accepted for now: the cell-local transform of a rotated
or mirrored instance is `orient_rect.py`'s, and until it is wired in here a
wrong transform would place every pin of that instance on the wrong face
with nothing saying so.
"""
import re

import buda
import buda_diag
from .util import ensure_parent_dir

_FACES = ("N", "S", "E", "W")


def def_escape(name):
    """`d[0]` -> `d\\[0\\]`: the DEF spelling OpenROAD reads and writes for a
    bus bit (measure/def_wires.py: a prefix match on the plain name found 0
    of 32 routed bus nets)."""
    return name.replace("[", "\\[").replace("]", "\\]")


def _natural_key(name):
    """Sort `d[2]` before `d[10]` and `clk` before `d[0]` — the order a human
    reads a pin list in, and a STABLE one, so two runs write the same file."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


# ── what the session knows ────────────────────────────────────────────────

def _layer_names(session):
    return {lid: n for n, lid in getattr(session, "_layer_name_map", {}).items()}


def _layer_is_h(session, lid):
    try:
        return session.layers.get_layer_dir(lid) == buda.LayerDir.HORIZONTAL
    except Exception:
        return None


def _min_signal_width(session, lid):
    """The layer's minimum SIGNAL slot width from its track pattern — the
    width a wire on that layer really is — or None with no pattern."""
    g = getattr(session, "routing_grid", None)
    if g is None or not g.has_layer(lid):
        return None
    ws = [s.width for s in g.get_layer_grid(lid).global_pattern().slots
          if s.type == "SIGNAL" and s.width > 0]
    return min(ws) if ws else None


def _signal_tracks(session, lid, lo, hi):
    """SIGNAL track centres of `lid`'s GLOBAL pattern in [lo, hi] —
    deliberately the raw pattern and not `signal_tracks_in`, whose keepout
    cull would remove every track under the block itself (a leaf footprint
    is a LOW-layer keepout), which is exactly where an edge pin sits."""
    g = getattr(session, "routing_grid", None)
    if g is None or not g.has_layer(lid):
        return []
    pat = g.get_layer_grid(lid).global_pattern()
    return [pos for pos, slot in pat.tracks_in_range(lo, hi)
            if slot.type == "SIGNAL"]


def _wires(session):
    """Every placed bit-wire as (bundle_id, bit, layer, horiz, lo, hi, perp,
    width), from DetailedNUTS when it ran, else fanned out of the abstract
    bus segments (WARNING: the bus position, not a track).  None with
    neither."""
    dr = getattr(session, "detailed_result", None)
    if dr is not None:
        out = []
        for ns in dr.net_segments:
            if getattr(ns, "is_shield", False):
                continue
            h = _layer_is_h(session, ns.layer)
            if h is None:
                continue
            lo, hi = sorted((ns.span_lo, ns.span_hi))
            out.append((ns.bundle_id, ns.bit_index, ns.layer, h, lo, hi,
                        ns.track_position, ns.width))
        return out, "detailed"
    nr = getattr(session, "nuts_result", None)
    if nr is None:
        return None, None
    buda_diag.emit("BUDA-1711",
                   "emit_pin_def: no run_detailed_nuts result — pins are "
                   "placed from the ABSTRACT bus-segment positions (run_nuts), "
                   "which are not on signal tracks; run run_detailed_nuts "
                   "first for a template the block's router can honour")
    wr = {w.input.original_bundle.id: w for w in session.bundles or []}
    out = []
    for ts in nr.segments:
        if not getattr(ts, "placed", True):
            continue
        w = wr.get(ts.bundle_id)
        if w is None:
            continue
        nbits = len(w.input.original_bundle.get_net_names())
        bits = list(range(nbits))
        try:
            sb = dict(w.input.candidates[w.plan.selected_topology_index].seg_bits)
            if ts.seg_idx in sb and 0 < len(sb[ts.seg_idx]) < nbits:
                bits = sorted(sb[ts.seg_idx])
        except Exception:
            pass
        n = max(1, len(bits))
        lo, hi = sorted((ts.span_lo, ts.span_hi))
        pitch = ts.width / n
        for i, bit in enumerate(bits):
            perp = ts.track_position - ts.width / 2.0 + (i + 0.5) * pitch
            out.append((ts.bundle_id, bit, ts.layer, bool(ts.horiz), lo, hi,
                        perp, pitch))
    return out, "abstract"


def _net_of(session):
    """(bundle id, bit) -> net name, over the session's (expanded) bundles."""
    names = {}
    for w in getattr(session, "bundles", []) or []:
        try:
            b = w.input.original_bundle
            names[b.id] = list(b.get_net_names())
        except Exception:
            continue

    def get(bid, bit):
        ns = names.get(bid)
        if ns is None or not 0 <= bit < len(ns):
            return None
        return ns[bit]
    return get


def _block_pins(session, block, comp=None):
    """The pins the netlist puts ON this block: {pin name: (net, dir)}.

    Flat flow: every net whose driver or receiver instance is the block, the
    pin NAMED BY ITS NET (a flat `add_bus d[8] a.o b.i` gives every bit the
    same pin `o`, so the net is the only name that identifies a bit).
    Hier / BDB: the component's own pin rows, whose names are the cell's
    port names and whose direction the LEF stated."""
    out = {}
    eps = getattr(session, "_net_endpoints", {}) or {}
    for net, (drv, rcvs) in eps.items():
        is_drv, is_rcv = drv == block, block in rcvs
        if is_drv and is_rcv:
            out[net] = (net, "INOUT")
        elif is_drv:
            out[net] = (net, "OUTPUT")
        elif is_rcv:
            out[net] = (net, "INPUT")
    if out or comp is None:
        return out
    net_name = {n.id: n.name for n in session.bdb.all_nets()}
    for p in session.bdb.pins_by_comp(comp.id):
        d = (p.dir or "").upper()
        if d not in ("INPUT", "OUTPUT", "INOUT"):
            d = "INOUT"
        out[p.pin_name] = (net_name.get(p.net_id, p.pin_name), d)
    return out


# ── the geometry ──────────────────────────────────────────────────────────

def _face_hit(wire, bx, eps=1e-6):
    """Where a bit-wire MEETS the block: (face, along, perp, on_face) or None.

    A wire that ends at or inside the block from outside reaches the face it
    crossed; one that crosses the whole block is a pass-through over the cell
    and no pin; one wholly inside or wholly outside is neither."""
    _bid, _bit, _lid, horiz, lo, hi, perp, _w = wire
    x1, y1, x2, y2 = bx
    if horiz:
        if not (y1 - eps <= perp <= y2 + eps):
            return None
        starts_out_w, ends_out_e = lo < x1 - eps, hi > x2 + eps
        if starts_out_w and not ends_out_e and hi >= x1 - eps:
            return ("W", x1, perp, abs(hi - x1) <= eps)
        if ends_out_e and not starts_out_w and lo <= x2 + eps:
            return ("E", x2, perp, abs(lo - x2) <= eps)
        return None
    if not (x1 - eps <= perp <= x2 + eps):
        return None
    starts_out_s, ends_out_n = lo < y1 - eps, hi > y2 + eps
    if starts_out_s and not ends_out_n and hi >= y1 - eps:
        return ("S", y1, perp, abs(hi - y1) <= eps)
    if ends_out_n and not starts_out_s and lo <= y2 + eps:
        return ("N", y2, perp, abs(lo - y2) <= eps)
    return None


class _Pin:
    __slots__ = ("name", "net", "dir", "layer", "face", "cx", "cy", "width",
                 "planned", "source")

    def __init__(self, name, net, dirn, layer, face, cx, cy, width, planned,
                 source):
        self.name, self.net, self.dir = name, net, dirn
        self.layer, self.face, self.cx, self.cy = layer, face, cx, cy
        self.width, self.planned, self.source = width, planned, source


def _plan_block(session, block, bx, pins_on_block, wires, net_of, depth,
                min_w):
    """Pins of ONE block in BLOCK-LOCAL coordinates: {pin: _Pin} for every
    pin a routed bit-wire reaches, plus the names of pins whose net is on
    a routed bundle but reached the face by no wire."""
    by_net = {}
    for name, (net, d) in pins_on_block.items():
        by_net.setdefault(net, []).append(name)
    cands = {}      # pin name -> list of (rank, hit, wire)
    routed_nets = set()
    for wire in wires:
        net = net_of(wire[0], wire[1])
        if net is None or net not in by_net:
            continue
        routed_nets.add(net)
        hit = _face_hit(wire, bx)
        if hit is None:
            continue
        face, along, perp, on_face = hit
        rank = (0 if on_face else 1, wire[2], perp)
        for name in by_net[net]:
            cands.setdefault(name, []).append((rank, hit, wire))
    out, notes = {}, []
    x1, y1, x2, y2 = bx
    for name in sorted(cands, key=_natural_key):
        cs = sorted(cands[name], key=lambda c: c[0])
        if len({(c[1][0], c[1][2], c[2][2]) for c in cs}) > 1:
            notes.append(f"{block}.{name}: {len(cs)} bit-wires meet the "
                         f"block; the one ending on its face wins")
        _rank, (face, along, perp, _on), wire = cs[0]
        lid = wire[2]
        w = min_w(lid) or wire[7]
        # The rectangle's CENTRE: `depth` into the block from the face, on
        # the wire's track — symmetric about the point, as OpenROAD writes it.
        if face == "W":
            cx, cy = along + depth / 2.0, perp
        elif face == "E":
            cx, cy = along - depth / 2.0, perp
        elif face == "S":
            cx, cy = perp, along + depth / 2.0
        else:
            cx, cy = perp, along - depth / 2.0
        net, d = pins_on_block[name]
        out[name] = _Pin(name, net, d, lid, face, cx - x1, cy - y1, w, True,
                         block)
    missed = sorted((n for net in routed_nets for n in by_net[net]
                     if n not in out), key=_natural_key)
    return out, missed, notes


def _free_tracks(session, lid, extent, taken):
    """BLOCK-FRAME signal track coordinates of `lid` over [0, extent], minus
    `taken` (local coordinates already holding a planned pin on that
    edge+layer).  The block is hardened in its own run with tracks at
    `OFFSET + k*PITCH` from ITS origin, which is the pattern read in its own
    frame — so a spread pin never depends on where the instance sits."""
    pos = _signal_tracks(session, lid, 0.0, extent)
    return [p for p in pos if all(abs(p - t) > 1e-6 for t in taken)]


def _phase(session, lid, origin):
    """(residue, period) of an instance ORIGIN against `lid`'s track period,
    or None when the layer has no pattern.  A top-frame track `t` is a
    block-frame track iff `t - origin` is one, i.e. iff the origin is a
    whole number of periods — the rule `align_bottom_up` nudges congruent
    instances onto.  A residue of 0 means every top-frame track on that
    layer IS a block-frame track."""
    g = getattr(session, "routing_grid", None)
    if g is None or not g.has_layer(lid):
        return None
    period = g.get_layer_grid(lid).global_pattern().unit_pitch()
    if period <= 0:
        return None
    r = origin % period
    if r > period - 1e-6:
        r = 0.0
    return (0.0 if r < 1e-6 else r), period


def _snap_track(session, lid, local, extent):
    """The nearest block-frame SIGNAL track of `lid` to `local` (within the
    block's extent) and the shift to reach it; (local, 0) with no pattern."""
    pos = _signal_tracks(session, lid, 0.0, extent)
    if not pos:
        return local, 0.0
    best = min(pos, key=lambda t: abs(t - local))
    return best, abs(best - local)


# ── the writer ────────────────────────────────────────────────────────────

def _lef_cell_pins(session, target, lef_path):
    """The cell's PIN SET from its LEF: {pin: dir} for every non-power pin.

    This is the set `Odb.ApplyDEFTemplate` matches in `strict` mode, and the
    BDB does not hold it: `import_def_lef` keeps a macro's SIZE and only the
    pins the DEF's nets reach, so a port on no net (`clk` when the top's
    clock is routed by the top's own flow) has no row anywhere.  `USE CLOCK`
    pins are kept — they are signal ports of the block even though the
    importer treats them as pre-routes.  None when no LEF is known."""
    if not lef_path:
        return None
    try:
        lib = buda.read_lef(lef_path)
    except Exception as e:      # noqa: BLE001 — reported, never fatal
        print(f"[PinDEF] note: could not read {lef_path} for the cell's pin "
              f"set: {e}")
        return None
    m = lib.find_macro(target)
    if m is None:
        print(f"[PinDEF] note: {lef_path} declares no MACRO '{target}'; the "
              f"pin set comes from the instances' nets alone")
        return None
    out = {}
    for p in m.pins:
        if (p.use or "").upper() in ("POWER", "GROUND"):
            continue
        d = (p.dir or "").upper()
        out[p.name] = d if d in ("INPUT", "OUTPUT", "INOUT") else "INOUT"
    return out


def emit_pin_def(session, path, target, unrouted="S", unrouted_layer=None,
                 depth_um=None, grid=None, lef_path=None, snap=False,
                 escaped_names=False):
    """Write `path`.  Returns the list of (name, planned) written, or None
    when the command refused — every refusal prints an `Error:` line and
    returns, one convention for the command (disagreeing instances, an
    off-phase origin, a non-N orientation included)."""
    if unrouted not in _FACES:
        print(f"Error: emit_pin_def unrouted edge must be one of "
              f"{'/'.join(_FACES)}, got '{unrouted}'")
        return None
    # ── units: 1 DEF database unit = 1 layout unit, so UNITS is lu_per_um ──
    lu_per_um = 1.0
    bdb = getattr(session, "bdb", None)
    if bdb is not None:
        try:
            if bdb.import_scale_pending():
                print("Error: emit_pin_def: set_import_scale dbu is declared "
                      "but no DEF has been imported to resolve it — import "
                      "the design first, or declare an explicit scale "
                      "(set_import_scale <lu_per_um>)")
                return None
            lu_per_um = float(bdb.import_scale()) or 1.0
        except Exception:
            lu_per_um = 1.0
    units = int(round(lu_per_um))
    if units < 1 or abs(lu_per_um - units) > 1e-9:
        print(f"Error: emit_pin_def: the session's scale is {lu_per_um:g} "
              f"layout units per micron, and a DEF's UNITS DISTANCE MICRONS "
              f"must be a whole number — declare set_import_scale dbu (or an "
              f"integer scale) before importing")
        return None
    if units == 1:
        print("[PinDEF] note: UNITS DISTANCE MICRONS 1 — the nominal-micron "
              "default (no set_import_scale); a LibreLane handoff wants "
              "set_import_scale dbu so coordinates are exact DBU")
    if grid is None:
        # gen_pins_def's 5 DBU is a MANUFACTURING grid (0.005 um at 1000
        # DBU/um).  At the nominal-micron scale a DEF unit IS a micron, so
        # the same 5 would fold neighbouring tracks onto one point; there the
        # coordinates are already whole units and no snap is wanted.
        grid = 5 if units >= 100 else 1
    try:
        grid = int(grid)
    except (TypeError, ValueError):
        grid = 0
    if grid < 1:
        print("Error: emit_pin_def grid must be a positive whole number of "
              "database units")
        return None
    depth = (2.0 if depth_um is None else float(depth_um)) * lu_per_um
    if depth <= 0:
        print("Error: emit_pin_def depth must be positive")
        return None

    # ── what routed ────────────────────────────────────────────────────────
    wires, source = _wires(session)
    if wires is None:
        print("Error: emit_pin_def needs a routed design — run run_nuts and "
              "run_detailed_nuts first (nothing has been placed, so there is "
              "no bit-wire to put a pin under)")
        return None
    net_of = _net_of(session)
    lname = _layer_names(session)
    names_for = lambda lid: lname.get(lid, f"L{lid}")   # noqa: E731

    # ── which block(s): a CELL's instances, or one flat block ──────────────
    fp = session.fp
    instances = []      # (block name, absolute bbox, ComponentRow|None)
    cell_pins = {}      # pin -> dir, from the LEF, cell mode only
    cell_mode = False
    if bdb is not None:
        cells = {c.name: c for c in bdb.all_cells()}
        if target in cells:
            cell_mode = True
            comps = [c for c in bdb.all_components() if c.cell == target]
            bad_orient = [f"{c.name} ({c.orient})" for c in comps
                          if (c.orient or "N") != "N"]
            if bad_orient:
                print(f"Error: emit_pin_def: cell '{target}' has instance(s) "
                      f"not in orientation N — {', '.join(bad_orient)}; the "
                      f"cell-local transform of a rotated or mirrored "
                      f"instance is not wired into the pin writer yet, and a "
                      f"template from the wrong transform would put every "
                      f"pin on the wrong face.  Place the instances N, or "
                      f"emit from a flat block.")
                return None
            for c in comps:
                if not fp.has_block(c.name):
                    continue
                r = fp.get_block_bounds(c.name)
                instances.append((c.name, (r.x1, r.y1, r.x2, r.y2), c))
            unplaced = [c.name for c in comps if not fp.has_block(c.name)]
            if not instances:
                print(f"Error: emit_pin_def: cell '{target}' has no instance "
                      f"in the routing floorplan (instances: "
                      f"{', '.join(c.name for c in comps) or 'none'}) — "
                      f"add_blocks_from_bdb must have loaded them")
                return None
            if unplaced:
                print(f"[PinDEF] note: {len(unplaced)} instance(s) of "
                      f"'{target}' not in the floorplan, skipped: "
                      f"{', '.join(unplaced[:6])}")
            for cp in bdb.all_cell_pins():
                if cp.cell == target:
                    d = (cp.dir or "").upper()
                    cell_pins[cp.pin_name] = (d if d in ("INPUT", "OUTPUT",
                                                         "INOUT") else "INOUT")
            lef_pins = _lef_cell_pins(
                session, target,
                lef_path or getattr(session, "_pin_def_lef_path", None))
            if lef_pins:
                cell_pins.update(lef_pins)
            elif lef_pins is None and not cell_pins:
                print(f"[PinDEF] note: no LEF known for cell '{target}' — "
                      f"the pin set is what the instances' nets reach; pass "
                      f"`lef <file>` so a port on no net (clk, rst) is in "
                      f"the template too")
    if not cell_mode:
        if not fp.has_block(target):
            what = "cell or block" if bdb is not None else "block"
            print(f"Error: emit_pin_def: no {what} named '{target}' in the "
                  f"design")
            return None
        r = fp.get_block_bounds(target)
        # An INSTANCE named as a block: its pins are its BDB pin rows and,
        # with a LEF known, its cell's full port list — the same sources
        # cell mode reads, for one occurrence.
        comp = None
        if bdb is not None:
            comp = next((c for c in bdb.all_components() if c.name == target),
                        None)
            if comp is not None and comp.cell:
                lef_pins = _lef_cell_pins(
                    session, comp.cell,
                    lef_path or getattr(session, "_pin_def_lef_path", None))
                if lef_pins:
                    cell_pins.update(lef_pins)
        instances.append((target, (r.x1, r.y1, r.x2, r.y2), comp))

    # Sizes must agree or the template's DIEAREA describes only one of them.
    sizes = {(bx[2] - bx[0], bx[3] - bx[1]) for _n, bx, _c in instances}
    if len(sizes) > 1:
        print(f"Error: emit_pin_def: instances of '{target}' differ in size: "
              f"{sorted(sizes)} — one template cannot describe them")
        return None
    bw, bh = next(iter(sizes))
    if bw <= 0 or bh <= 0:
        print(f"Error: emit_pin_def: '{target}' has no extent ({bw} x {bh})")
        return None

    # ── per instance, then merge under template semantics ──────────────────
    min_w = lambda lid: _min_signal_width(session, lid)    # noqa: E731
    merged = {}          # pin -> _Pin (block-local, unsnapped)
    all_pins = dict(cell_pins)   # pin -> dir over every source
    missed_all, notes = [], []
    per_inst = []
    for name, bx, comp in instances:
        on_block = _block_pins(session, name, comp)
        for pn, (_net, d) in on_block.items():
            all_pins.setdefault(pn, d)
        planned, missed, nts = _plan_block(session, name, bx, on_block, wires,
                                           net_of, depth, min_w)
        notes += nts
        missed_all += [f"{name}.{m}" for m in missed]
        per_inst.append((name, bx, planned))

    # ── the block's OWN track grid ─────────────────────────────────────────
    # A planned pin sits where the TOP's bit-wire meets the face, on one of
    # the top's tracks.  The block is hardened in its own run with tracks
    # anchored at ITS origin, so a top-frame track is a block-frame track
    # only when the instance origin is a whole number of that layer's track
    # periods (measured on the phase-0 fixture at (10000, 20000) DBU: d[0]
    # 400 DBU off the block-frame met3 track, clk 120 off met2 — pins the
    # block's router cannot reach without a jog, and a template it may
    # refuse as off-track).  Per instance and pin layer the residue is
    # checked; the honest fix is a placement on the period (the same rule
    # `align_bottom_up` implements), and `snap` is the fallback for a
    # placement that cannot move: each pin moves to the nearest block-frame
    # track and the largest shift is reported LOUD.
    off = []
    for name, bx, planned in per_inst:
        for lid in sorted({p.layer for p in planned.values()}):
            h = _layer_is_h(session, lid)
            ph = _phase(session, lid, bx[1] if h else bx[0])
            if ph is None or ph[0] == 0.0:
                continue
            r, period = ph
            off.append((name, lid, "y" if h else "x", r, period))
    n_snapped, max_shift = 0, 0.0
    if off and not snap:
        lines = [f"  {name}: origin {axis} on {names_for(lid)} is {r:g} past "
                 f"a track period of {period:g} — move it by -{r:g} or "
                 f"+{period - r:g}"
                 for name, lid, axis, r, period in off]
        print(f"Error: emit_pin_def: the pins of '{target}' would sit on the "
              f"TOP's track grid, not the block's — the instance origin is "
              f"not a whole number of track periods on the pin layer(s):\n"
              + "\n".join(lines)
              + "\n  remedy: place the instance(s) at an origin that is a "
              "multiple of every pin layer's period (align_bottom_up's "
              "phase rule) and re-route, or pass `snap` to move each pin "
              "to the nearest block-frame track (reported)")
        return None
    if off:
        off_layers = {(name, lid) for name, lid, _a, _r, _p in off}
        for name, bx, planned in per_inst:
            for p in planned.values():
                if (name, p.layer) not in off_layers:
                    continue
                if p.face in ("W", "E"):
                    p.cy, sh = _snap_track(session, p.layer, p.cy, bh)
                else:
                    p.cx, sh = _snap_track(session, p.layer, p.cx, bw)
                if sh > 1e-6:
                    n_snapped += 1
                    max_shift = max(max_shift, sh)
        buda_diag.emit("BUDA-1713",
                       f"emit_pin_def: {n_snapped} pin(s) of '{target}' "
                       f"snapped onto the block-frame tracks, the largest "
                       f"shift {max_shift:g} layout units — the top's "
                       f"bit-wire and the block's pin differ by that much "
                       f"and the top's router will jog to meet it; a "
                       f"placement on the track period removes the shift")

    for name, _bx, planned in per_inst:
        for pn, p in planned.items():
            q = merged.get(pn)
            if q is None:
                merged[pn] = p
                continue
            same = (q.layer == p.layer and q.face == p.face
                    and abs(q.cx - p.cx) < 0.5 and abs(q.cy - p.cy) < 0.5)
            if not same:
                print(f"Error: emit_pin_def: instances of '{target}' "
                      f"disagree on pin '{pn}': {q.source} puts it on "
                      f"{names_for(q.layer)} face {q.face} at local "
                      f"({q.cx:g}, {q.cy:g}), {p.source} on "
                      f"{names_for(p.layer)} face {p.face} at local "
                      f"({p.cx:g}, {p.cy:g}) — a cell is hardened once, so "
                      f"every instance must route the pin to the same place; "
                      f"re-plan (or pin the topology) until they agree")
                return None
    if not all_pins:
        print(f"Error: emit_pin_def: '{target}' has no pins — no net reaches "
              f"it and (in a hier session) its cell declares no port")
        return None
    if missed_all:
        buda_diag.emit("BUDA-1712",
                       f"emit_pin_def: {len(missed_all)} pin(s) whose net is "
                       f"on a routed bundle reached the face of '{target}' "
                       f"by no bit-wire (unplaced bit, or a pass-through "
                       f"over the cell) — spread with the unrouted pins: "
                       f"{', '.join(missed_all[:8])}"
                       + (" …" if len(missed_all) > 8 else ""))

    # ── the unrouted ones, spread on one edge ──────────────────────────────
    unrouted_names = sorted((pn for pn in all_pins if pn not in merged),
                            key=_natural_key)
    spread = []
    if unrouted_names:
        want_h = unrouted in ("W", "E")     # a W/E edge pin is an H wire
        lid = None
        if unrouted_layer:
            lid = getattr(session, "_layer_name_map", {}).get(unrouted_layer)
            if lid is None:
                print(f"Error: emit_pin_def: unknown layer '{unrouted_layer}'")
                return None
            if _layer_is_h(session, lid) != want_h:
                print(f"Error: emit_pin_def: layer '{unrouted_layer}' runs "
                      f"{'horizontally' if not want_h else 'vertically'}; a "
                      f"pin on the {unrouted} edge is a "
                      f"{'horizontal' if want_h else 'vertical'} wire")
                return None
        else:
            used = sorted({p.layer for p in merged.values()
                           if _layer_is_h(session, p.layer) == want_h})
            g = getattr(session, "routing_grid", None)
            patterned = sorted(l for l in lname
                               if _layer_is_h(session, l) == want_h
                               and g is not None and g.has_layer(l))
            any_dir = sorted(l for l in lname
                             if _layer_is_h(session, l) == want_h)
            lid = (used or patterned or any_dir or [None])[0]
            if lid is None:
                print(f"Error: emit_pin_def: no "
                      f"{'horizontal' if want_h else 'vertical'} layer is "
                      f"declared to carry the unrouted pins on edge "
                      f"{unrouted}; declare one (def_layer) or pick another "
                      f"edge")
                return None
        taken = [(p.cx if unrouted in ("S", "N") else p.cy)
                 for p in merged.values()
                 if p.face == unrouted and p.layer == lid]
        pos = _free_tracks(session, lid, bw if unrouted in ("S", "N") else bh,
                           taken)
        n = len(unrouted_names)
        on_tracks = bool(pos)
        if not on_tracks:
            # No pattern on the layer: even spacing, and say it is not on
            # tracks (the template may then be refused as off-track).
            ext = bw if unrouted in ("S", "N") else bh
            pos = [(k + 0.5) * ext / n for k in range(n)]
            print(f"[PinDEF] note: layer {names_for(lid)} has no track "
                  f"pattern — the {n} unrouted pin(s) on edge {unrouted} are "
                  f"evenly spaced, NOT on tracks")
        elif len(pos) < n:
            print(f"Error: emit_pin_def: edge {unrouted} of '{target}' has "
                  f"{len(pos)} free signal track(s) on {names_for(lid)} for "
                  f"{n} unrouted pin(s)")
            return None
        w = min_w(lid) or 2.0 * lu_per_um
        picks = [pos[int((k + 0.5) * len(pos) / n)] for k in range(n)]
        for pn, t in zip(unrouted_names, picks):
            if unrouted == "S":
                cx, cy = t, depth / 2.0
            elif unrouted == "N":
                cx, cy = t, bh - depth / 2.0
            elif unrouted == "W":
                cx, cy = depth / 2.0, t
            else:
                cx, cy = bw - depth / 2.0, t
            spread.append(_Pin(pn, pn, all_pins[pn], lid, unrouted, cx, cy, w,
                               False, "spread"))
        # A PLANNED pin's direction the LEF knows wins over the net's role.
    for p in merged.values():
        if p.name in cell_pins:
            p.dir = cell_pins[p.name]

    # ── snap, write ────────────────────────────────────────────────────────
    def snap(v):
        return int(round(v / grid)) * grid

    moved = 0
    rows = []
    for p in sorted(merged.values(), key=lambda q: _natural_key(q.name)) + spread:
        cx, cy = snap(p.cx), snap(p.cy)
        if abs(cx - p.cx) > 1e-6 or abs(cy - p.cy) > 1e-6:
            moved += 1
        hw, hd = max(snap(p.width / 2.0), grid), max(snap(depth / 2.0), grid)
        along_x = p.face in ("W", "E")      # the wire runs along x
        if along_x:
            x1r, y1r, x2r, y2r = -hd, -hw, hd, hw
        else:
            x1r, y1r, x2r, y2r = -hw, -hd, hw, hd
        rows.append((p, cx, cy, (x1r, y1r, x2r, y2r)))
    if moved:
        print(f"[PinDEF] note: {moved} pin centre(s) were off the {grid}-DBU "
              f"grid and were snapped to it")
    design = target
    out = [
        "VERSION 5.8 ;",
        'DIVIDERCHAR "/" ;',
        'BUSBITCHARS "[]" ;',
        f"DESIGN {design} ;",
        f"UNITS DISTANCE MICRONS {units} ;",
        f"DIEAREA ( 0 0 ) ( {snap(bw)} {snap(bh)} ) ;",
        f"PINS {len(rows)} ;",
    ]
    for p, cx, cy, (x1r, y1r, x2r, y2r) in rows:
        nm = def_escape(p.name) if escaped_names else p.name
        out.append(
            f"  - {nm} + NET {nm} + DIRECTION {p.dir} + USE SIGNAL"
            f" + LAYER {names_for(p.layer)} ( {x1r} {y1r} ) ( {x2r} {y2r} )"
            f" + PLACED ( {cx} {cy} ) N ;")
    out += ["END PINS", "END DESIGN", ""]
    ensure_parent_dir(path)
    with open(path, "w") as f:
        f.write("\n".join(out))
    n_plan, n_spread = len(merged), len(spread)
    faces = {}
    for p in merged.values():
        faces[p.face] = faces.get(p.face, 0) + 1
    face_txt = ", ".join(f"{k} {v}" for k, v in sorted(faces.items()))
    print(f"[PinDEF] {path}: {len(rows)} pin(s) for {design} — {n_plan} from "
          f"the plan ({source}{'; ' + face_txt if face_txt else ''}), "
          f"{n_spread} spread on edge {unrouted}"
          f"{' on ' + names_for(spread[0].layer) if spread else ''}; "
          f"UNITS {units}, die {snap(bw)} x {snap(bh)}, "
          f"{len(instances)} instance(s)"
          + (f"; {n_snapped} snapped, largest shift {max_shift:g}"
             if n_snapped else ""))
    for n in notes[:8]:
        print(f"[PinDEF] note: {n}")
    return [(p.name, p.planned) for p, _cx, _cy, _r in rows]
