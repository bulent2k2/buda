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

"""Fixed-pin commands — fix_pin / dump_pin_fixes (docs/internal/fixed_pin_design.md).

A block's pins are not always free to land anywhere on its boundary: a hard
macro's pins sit where its layout put them.  `fix_pin` states which landings a
block still admits — faces, one declared rect, a window along the face — and
the generator, NUTS, DetailedNUTS and the audit all read that as the block's
LANDING geometry (`Floorplan.landing_rects`).  What the block BLOCKS
(`obstruction_rects`) never changes: a fix narrows where a wire may attach, it
does not make the macro's body routable.

A fix governs a BLOCK (`fix_pin B ...`), or only some of its pins (`fix_pin
B.tx ...`, `fix_pin B.d* ...`) in a FLAT flow; faces are stated in the placed
(die) frame.  Cell-level fixes with the orientation transform, layers, bit
order and persistence are the next changes.

Selector precedence, most specific first: exact pin name > bus name (`tx`
selects `tx[3]` / `tx_3`) > longest `prefix*` > the block-level fix.  Two nets
landing on one block under DIFFERENT effective fixes cannot share a bundle
(a bundle lands on one face), so the bundler splits them, loudly.
"""
import json
import re

# BUDA-1922 (no candidate lands on a fixed pin) is emitted by the topology
# generator itself, where the pool is judged; this module declares the fix.

_FACE_BITS = {"north": 1, "south": 2, "east": 4, "west": 8}
_FACE_ALIAS = {"n": "north", "s": "south", "e": "east", "w": "west",
               "h": "h", "v": "v"}
_FACE_NAMES = ((1, "north"), (2, "south"), (4, "east"), (8, "west"))


def _faces_text(mask):
    if not mask:
        return "all"
    return ",".join(n for b, n in _FACE_NAMES if mask & b)


def _rect_count(fp, block):
    return max(1, len(fp.get_block_rects(block)))


def _declared(fix):
    """The fix as the user typed it (what dump_pin_fixes prints)."""
    parts = [_faces_text(fix["faces"])]
    if fix["rect"] >= 0:
        parts.append(f"rect {fix['rect'] + 1}")
    if fix["has_window"]:
        parts.append(f"at {fix['lo']}..{fix['hi']}")
    if fix.get("layers"):
        parts.append("layer " + ",".join(fix["layers"]))
    if fix.get("order"):
        parts.append("order " + fix["order"])
    return " ".join(parts)


def _layer_axes(session, names):
    """({'H','V'} reachable by these layer names, unknown names).  A layer not
    declared yet has no direction to judge."""
    import buda
    axes, unknown = set(), []
    for n in names:
        lid = session._layer_name_map.get(n)
        if lid is None:
            unknown.append(n)
        elif session.layers.get_layer_dir(lid) == buda.LayerDir.HORIZONTAL:
            axes.add("H")
        else:
            axes.add("V")
    return axes, unknown


def _eff_faces(session, fix):
    """The face mask the geometry sees: the declared faces narrowed to the ones
    a named layer can REACH.  An east or west face is landed on by a horizontal
    stub, a north or south face by a vertical one, so `layer M5` (H) leaves
    only east/west.  0 stays 'every face'; -1 = nothing is left."""
    names = fix.get("layers") or []
    mask = fix["faces"]
    if not names:
        return mask
    axes, unknown = _layer_axes(session, names)
    if unknown or not axes:
        return mask                  # layers not declared yet: judged later
    reach = 0
    if "H" in axes:
        reach |= _FACE_BITS["east"] | _FACE_BITS["west"]
    if "V" in axes:
        reach |= _FACE_BITS["north"] | _FACE_BITS["south"]
    eff = (mask or 15) & reach
    if not eff:
        return -1
    return 0 if eff == 15 else eff


def reapply_layer_fixes(session):
    """A fix restored (or typed) before its layer was declared was stamped with
    its un-narrowed faces; once a layer becomes known its direction can narrow
    them, so re-stamp every block-level fix that names layers.  A fix whose
    layers can now be seen to reach none of its faces is REFUSED the way a
    typed one is: reported and dropped, never restored un-narrowed."""
    dropped = False
    for block, fix in list(session._pin_fixes.items()):
        if not fix.get("layers"):
            continue
        if _eff_faces(session, fix) < 0:
            del session._pin_fixes[block]
            session.fp.clear_pin_fix(block)     # stored by name, block or not
            print(f"Error: fix_pin {block}: layer {','.join(fix['layers'])} cannot "
                  f"reach the face(s) '{_faces_text(fix['faces'])}'; the fix is "
                  f"dropped")
            dropped = True
        else:
            _apply(session, block, fix)         # likewise stamped before the block exists
    for block, d in list(_sel_store(session).items()):
        for key, fix in list(d.items()):
            if fix.get("layers") and _eff_faces(session, fix) < 0:
                del d[key]
                print(f"Error: fix_pin {block}.{key[1]}: layer "
                      f"{','.join(fix['layers'])} cannot reach the face(s) "
                      f"'{_faces_text(fix['faces'])}'; the fix is dropped")
                dropped = True
        if not d:
            del _sel_store(session)[block]
    if dropped:
        persist(session)


def _apply(session, block, fix):
    faces = _eff_faces(session, fix)
    session.fp.set_pin_fix(block, max(faces, 0) if faces >= 0 else fix["faces"],
                           fix["rect"], fix["has_window"], fix["lo"], fix["hi"])


def _sel_store(session):
    st = getattr(session, "_pin_fix_sel", None)
    if st is None:
        st = session._pin_fix_sel = {}
    return st


def _fixkey(fix):
    return None if fix is None else (fix["faces"], fix["rect"], fix["has_window"],
                                     fix["lo"], fix["hi"],
                                     tuple(sorted(fix.get("layers") or ())),
                                     fix.get("order") or "")


def resolve_fix(session, block, pin_name):
    """The effective fix for ONE pin of a block (None = unfixed): exact pin >
    bus > longest prefix > the block-level fix."""
    from bus_names import bus_name
    sels = _sel_store(session).get(block)
    if sels:
        hit = sels.get(("name", pin_name))
        if hit is not None:
            return hit
        hit = sels.get(("name", bus_name(pin_name)))
        if hit is not None:
            return hit
        best = None
        for (kind, key), fx in sels.items():
            if kind == "prefix" and pin_name.startswith(key):
                if best is None or len(key) > len(best[0]):
                    best = (key, fx)
        if best is not None:
            return best[1]
    return session._pin_fixes.get(block)


def _net_pins_of(session, net):
    return getattr(session, "_net_pins", {}).get(net)


def net_fix_signature(session, net):
    """((block, fixkey), ...) over the blocks this net touches that carry any
    fix declaration — two nets with different signatures cannot share a bundle."""
    pins = _net_pins_of(session, net)
    if pins is None:
        return ()
    drv, rcvs = pins
    sig = {}
    for pin in [drv] + list(rcvs):
        block, _, name = pin.rpartition(".")
        if block in session._pin_fixes or block in _sel_store(session):
            sig[block] = _fixkey(resolve_fix(session, block, name))
    return tuple(sorted(sig.items(), key=lambda kv: kv[0]))


def net_fix_conflicts(session, net):
    """[(block, [pin names])] where ONE net lands on a block through pins whose
    fixes differ.  A net is a single bundle member, so it cannot be split to
    honour both landings."""
    pins = _net_pins_of(session, net)
    if pins is None:
        return []
    drv, rcvs = pins
    seen = {}           # block -> {fixkey: [pin names]}
    for pin in [drv] + list(rcvs):
        block, _, name = pin.rpartition(".")
        if block in session._pin_fixes or block in _sel_store(session):
            key = _fixkey(resolve_fix(session, block, name))
            seen.setdefault(block, {}).setdefault(key, []).append(name)
    return [(b, [n for names in d.values() for n in names])
            for b, d in seen.items() if len(d) > 1]


def bundle_fix_map(session, net_names):
    """The EFFECTIVE fix map for one bundle ({block: (faces, rect, has_window,
    lo, hi)}), or None when no per-pin selector exists (the floorplan's own
    block-level fixes then apply unchanged — the byte-identical path)."""
    if not _sel_store(session):
        return None
    out = {}
    for net in net_names:
        for blk, key in net_fix_signature(session, net):
            if key is not None and blk not in out:
                out[blk] = _geom_key(session, key)
    return out


def _geom_key(session, key):
    """The 5 fields the C++ map reads, with the faces the layers narrow them to
    (the signature key keeps the DECLARED faces plus layers/order, so two fixes
    differing only in those still split a bundle)."""
    faces, rect, has_window, lo, hi, layers = key[:6]
    eff = _eff_faces(session, {"faces": faces, "layers": list(layers)})
    return (max(eff, 0) if eff >= 0 else faces, rect, has_window, lo, hi)


def has_layer_fixes(session):
    return (any(f.get("layers") for f in session._pin_fixes.values())
            or any(f.get("layers") for d in _sel_store(session).values()
                   for f in d.values()))


def _fix_axes(session, fix):
    """Axes ('H'/'V') a fix's landing stub may take: the layers named, each in
    its own direction, limited to the faces the fix still admits."""
    import buda
    faces = _eff_faces(session, fix) or 15
    out = {}
    for n in fix["layers"]:
        lid = session._layer_name_map.get(n)
        if lid is None:
            continue
        horiz = session.layers.get_layer_dir(lid) == buda.LayerDir.HORIZONTAL
        if faces & ((_FACE_BITS["east"] | _FACE_BITS["west"]) if horiz
                    else (_FACE_BITS["north"] | _FACE_BITS["south"])):
            out.setdefault("H" if horiz else "V", set()).add(lid)
    return out


def bundle_layer_mask(session, w):
    """(label, [layer ids]) a bundle's pin-fix layers allow, or (None, None).

    The landing stub is the segment `layer` constrains, but the planner masks a
    BUNDLE's layers, not one segment's.  So per axis: if some fix on the bundle
    names layers of that axis, the bundle takes the INTERSECTION of those sets
    on that axis (every landing must be satisfiable by the one layer a straight
    run takes); an axis nobody constrains keeps all its layers, so the other leg
    of an L or Z still routes.  The cost is that a bundle's OTHER same-axis
    segments follow the same layers — the per-segment set is the later,
    finer form.  An axis whose fixes share no layer is a hard error naming
    both pins: the bus has two landings no single layer serves."""
    import buda
    if not has_layer_fixes(session):
        return (None, None, {})
    per_axis = {"H": [], "V": []}
    for net in w.input.original_bundle.get_net_names():
        pins = _net_pins_of(session, net)
        if pins is None:
            continue
        drv, rcvs = pins
        for pin in [drv] + list(rcvs):
            block, _, name = pin.rpartition(".")
            fix = resolve_fix(session, block, name)
            if fix is None or not fix.get("layers"):
                continue
            for ax, lids in _fix_axes(session, fix).items():
                per_axis[ax].append((pin, frozenset(lids)))
    if not per_axis["H"] and not per_axis["V"]:
        return (None, None, {})
    allowed, constrained = [], {}
    for ax, dirn in (("H", buda.LayerDir.HORIZONTAL), ("V", buda.LayerDir.VERTICAL)):
        every = list(session.layers.get_layer_ids_by_dir(dirn))
        if not per_axis[ax]:
            allowed += every
            continue
        common = set.intersection(*[set(l) for _p, l in per_axis[ax]])
        constrained[ax] = common
        if not common:
            names = {lid: n for n, lid in session._layer_name_map.items()}
            shown = "; ".join(f"{p} -> {','.join(names.get(l, str(l)) for l in sorted(ls))}"
                              for p, ls in dict(per_axis[ax]).items())
            print(f"Error: fix_pin: bundle {w.input.original_bundle.id} lands on "
                  f"pins whose {ax} layers share none ({shown}); one bus cannot "
                  f"take two layers — give the pins a common layer, or keep "
                  f"their nets apart (set_bundling)")
            import sys
            sys.exit(1)
        allowed += sorted(common)
    return ("fix_pin layer", sorted(set(allowed)), constrained)


class _PinFixViolation:
    """Duck-typed audit row, like reports._FidelityViolation."""
    class _Kind:
        name = "PIN_FIX"
    kind = _Kind()
    seg_idx2 = -1
    bit_index = -1

    def __init__(self, block, seg, message):
        self.block_name, self.seg_idx, self.message = block, seg, message


def audit_pin_fix(session, w, topo):
    """PIN_FIX: a segment ending on a fixed pin's landing strip on a layer
    outside the fix's set.  Defence in depth for the bundle mask: the planner
    may have ended up outside it (BEST_EFFORT) or a pin took an unmasked
    route.  Needs a plan; reads the assigned layers.  Also reports a straight
    segment joining two pins of opposite `order` — the generator's gate drops
    those, but a hand-edited USER candidate arrives after it."""
    out = []
    for i, orders in sorted(segment_orders(session, w, topo).items()):
        if len(orders) > 1:
            out.append(_PinFixViolation("", i, (
                f"PIN_FIX: segment {i} joins fixed pins of opposite order "
                f"(index and rindex) — a straight wire cannot reverse its bits")))
    if not has_layer_fixes(session):
        return out
    layers = list(w.plan.seg_layers)
    segs = list(topo.segments)
    if len(layers) != len(segs):
        return out
    names = {lid: n for n, lid in session._layer_name_map.items()}
    seen = set()
    for net in w.input.original_bundle.get_net_names():
        pins = _net_pins_of(session, net)
        if pins is None:
            continue
        for pin in [pins[0]] + list(pins[1]):
            block, _, name = pin.rpartition(".")
            fix = resolve_fix(session, block, name)
            if fix is None or not fix.get("layers") or not session.fp.has_block(block):
                continue
            ok = {session._layer_name_map.get(n) for n in fix["layers"]}
            for (x1, y1, x2, y2, _f) in _strips_with(session, block, fix):
                for i, sg in enumerate(segs):
                    for p in (sg.start, sg.end):
                        if x1 <= p.x <= x2 and y1 <= p.y <= y2 and layers[i] not in ok:
                            key = (pin, i)
                            if key in seen:
                                continue
                            seen.add(key)
                            out.append(_PinFixViolation(block, i, (
                                f"PIN_FIX: segment {i} lands on {pin} on "
                                f"{names.get(layers[i], layers[i])}, outside the "
                                f"fixed layer(s) {','.join(fix['layers'])}")))
    return out


def has_order_fixes(session):
    return (any(f.get("order") for f in session._pin_fixes.values())
            or any(f.get("order") for d in _sel_store(session).values()
                   for f in d.values()))


def segment_orders(session, w, topo):
    """{segment index: {'index'|'rindex'}} — the bit order each segment is held
    to by the fixed pins it ENDS on (an end inside an admitted landing strip)."""
    out = {}
    if not has_order_fixes(session):
        return out
    segs = list(topo.segments)
    strips = {}
    for net in w.input.original_bundle.get_net_names():
        pins = _net_pins_of(session, net)
        if pins is None:
            continue
        for pin in [pins[0]] + list(pins[1]):
            block, _, name = pin.rpartition(".")
            fix = resolve_fix(session, block, name)
            if fix is None or not fix.get("order") or not session.fp.has_block(block):
                continue
            key = (block, _fixkey(fix))
            if key not in strips:
                strips[key] = (fix["order"], _strips_with(session, block, fix))
            order, rects = strips[key]
            for i, sg in enumerate(segs):
                for p in (sg.start, sg.end):
                    if any(x1 <= p.x <= x2 and y1 <= p.y <= y2
                           for (x1, y1, x2, y2, _f) in rects):
                        out.setdefault(i, set()).add(order)
    return out


def filter_order_conflicts(session, w, cands):
    """A straight segment cannot reverse its bits, so a candidate whose one
    segment joins two landings of OPPOSITE order is dropped (a bend may differ:
    each leg takes its landing's order).  Decided per candidate, at generation,
    because only a candidate knows which landings share a segment.  When every
    candidate conflicts the pool is kept and the conflict is reported — the bus
    must never strand."""
    if not has_order_fixes(session) or not cands:
        return cands
    keep = [c for c in cands
            if not any(len(o) > 1 for o in segment_orders(session, w, c).values())]
    if keep and len(keep) == len(cands):
        return cands
    bid = w.input.original_bundle.id
    if not keep:
        print(f"Warning: [PinFix] every candidate of bundle {bid} joins two fixed "
              f"pins of opposite order on one straight segment; the pool is kept "
              f"— give the pins the same order, or move a block so a bend fits")
        return cands
    print(f"[PinFix] bundle {bid}: dropped {len(cands) - len(keep)} candidate(s) "
          f"that join fixed pins of opposite order on one straight segment "
          f"(a straight wire cannot reverse its bits)")
    return keep


def apply_orders(session, bus_segs):
    """Hand each landing segment its fixed bit order (BusSegment.bit_order is
    per segment).  `index` lays bit 0 at the lowest coordinate along the face —
    the perpendicular axis of the landing wire — which is LO_HI; `rindex` is
    HI_LO.  Segments no order fix touches keep the run's own order."""
    if not has_order_fixes(session):
        return bus_segs
    by_bid = {w.input.original_bundle.id: w for w in session.bundles}
    cache = {}
    for bs in bus_segs:
        w = by_bid.get(bs.bundle_id)
        if w is None or w.plan.selected_topology_index < 0:
            continue
        if bs.bundle_id not in cache:
            topo = w.input.candidates[w.plan.selected_topology_index]
            cache[bs.bundle_id] = segment_orders(session, w, topo)
        orders = cache[bs.bundle_id].get(bs.seg_idx)
        if orders and len(orders) == 1:
            bs.bit_order = "LO_HI" if next(iter(orders)) == "index" else "HI_LO"
    return bus_segs


def set_gen_fixes(session, tg, w):
    """Point a generator at the bundle's effective fixes (no-op without selectors)."""
    m = bundle_fix_map(session, w.input.original_bundle.get_net_names())
    if m is None:
        tg.clear_block_fixes()
    else:
        tg.set_block_fixes(m)


def _net_fix_map(session, net):
    return {blk: key for blk, key in net_fix_signature(session, net)}


def _partition_by_fix(session, nets):
    """Greedy partition of a bundle's nets into parts whose per-block fixes do
    not CONFLICT.  Nets landing on different fixed blocks share a part (one map
    can hold both); only differing resolutions at a block they share split."""
    parts = []          # [(merged {block: key}, [nets])]
    for n in nets:
        m = _net_fix_map(session, n)
        for merged, members in parts:
            if all(merged.get(b, k) == k for b, k in m.items()):
                merged.update(m)
                members.append(n)
                break
        else:
            parts.append((dict(m), [n]))
    return parts


def split_bundles_by_fix(session, raw_bundles):
    """Fix-uniform split: a bundle whose nets resolve to DIFFERENT fixes at one
    block cannot land on one face — split into conflict-free parts, LOUDLY.
    No selectors declared = input returned untouched."""
    if not _sel_store(session):
        return raw_bundles
    import buda
    import sys
    for b in raw_bundles:
        for n in b.get_net_names():
            bad = net_fix_conflicts(session, n)
            if bad:
                blk, pins = bad[0]
                print(f"Error: net '{n}' reaches block '{blk}' through pins "
                      f"{', '.join(pins)} whose pin fixes differ — one net "
                      f"cannot land on two faces; give those pins the same "
                      f"fix or split the net")
                sys.exit(1)
    out = []
    next_id = max((b.id for b in raw_bundles), default=0)
    for b in raw_bundles:
        nets = list(b.get_net_names())
        parts = _partition_by_fix(session, nets)
        if len(parts) <= 1:
            out.append(b)
            continue
        sizes = "+".join(str(len(m)) for _g, m in parts)
        print(f"[PinFix] split bundle {b.id} ({len(nets)} bits) into "
              f"{len(parts)} fix-uniform part(s) ({sizes}): its nets land on "
              f"one fixed block under different pin fixes, and one bundle lands "
              f"on one face")
        for k, (_g, part) in enumerate(parts, start=1):
            nb = buda.HBundle()
            if k == 1:
                nb.id = b.id
            else:
                next_id += 1
                nb.id = next_id
            nb.net_names = part
            nb.reason = f"{b.reason}|PINFIX:{k}"
            nb.num_terminals = b.num_terminals
            out.append(nb)
    return out


def _strips_with(session, block, fix):
    """Strips `fix` would admit on `block`, without leaving it applied."""
    fp = session.fp
    prev = session._pin_fixes.get(block)
    _apply(session, block, fix)
    try:
        return fp.pin_fix_strips(block)
    finally:
        if prev is None:
            fp.clear_pin_fix(block)
        else:
            _apply(session, block, prev)


def _cmd_fix_pin(session, cmd, args, cmd_line):
    # fix_pin <block>[.*] [north|south|east|west|H|V ...] [rect <n>] [at <lo>..<hi>]
    # fix_pin <block> off
    if not args:
        print("Error: usage: fix_pin <block> [north|south|east|west|H|V ...] "
              "[rect <n>] [at <lo>..<hi>]   |   fix_pin <block> off")
        return
    target = args[0]
    sel = None      # (kind, key) for a per-pin selector, else None (block-level)
    # Block names may themselves contain a period (`u.core`, `IO.PAD`), so the
    # exact declared name wins; only an explicit trailing `.*` is stripped, and
    # a suffix is read as a per-pin selector only when its prefix IS a known block.
    if session.fp.has_block(target):
        block = target
    elif target.endswith(".*"):
        block = target[:-2]
    elif "." in target and session.fp.has_block(target.rsplit(".", 1)[0]):
        block, _, sel_text = target.rpartition(".")
        sel = (("prefix", sel_text[:-1]) if sel_text.endswith("*")
               else ("name", sel_text))
    else:
        block = target      # not declared yet (or a dotted name declared later)
    fixes = session._pin_fixes
    sels = _sel_store(session)
    if sel is not None and len(args) == 2 and args[1].lower() == "off":
        _tombstone(session, block, sel)
        if sels.get(block, {}).pop(sel, None) is not None:
            if not sels[block]:
                del sels[block]
            print(f"[PinFix] {block}.{sel[1]}{'*' if sel[0] == 'prefix' else ''}: "
                  f"pin fix removed")
        else:
            print(f"[PinFix] {target}: no such pin fix declared")
        return
    if sel is None and len(args) == 2 and args[1].lower() == "off":
        _tombstone(session, block, None)
        if block in fixes:
            del fixes[block]
            session.fp.clear_pin_fix(block)
            print(f"[PinFix] {block}: fix removed — every face admits landings again")
        else:
            print(f"[PinFix] {block}: no fix declared")
        return

    faces, rect, window, layers, order = 0, -1, None, [], ""
    i = 1
    while i < len(args):
        tok = args[i].lower()
        tok = _FACE_ALIAS.get(tok, tok)
        if tok in _FACE_BITS:
            faces |= _FACE_BITS[tok]
        elif tok == "h":
            faces |= _FACE_BITS["east"] | _FACE_BITS["west"]
        elif tok == "v":
            faces |= _FACE_BITS["north"] | _FACE_BITS["south"]
        elif tok == "rect":
            if i + 1 >= len(args) or not args[i + 1].isdigit() or int(args[i + 1]) < 1:
                print("Error: fix_pin: 'rect' takes a 1-based rect number"); return
            rect = int(args[i + 1]) - 1
            i += 1
        elif tok == "at":
            m = (re.fullmatch(r"(-?\d+)\.\.(-?\d+)", args[i + 1]) if i + 1 < len(args) else None)
            if not m:
                print("Error: fix_pin: 'at' takes a window lo..hi along the face "
                      "(block-local coordinates)"); return
            window = (int(m.group(1)), int(m.group(2)))
            if window[0] >= window[1]:
                print(f"Error: fix_pin: window {window[0]}..{window[1]} is empty"); return
            i += 1
        elif tok == "layer":
            if i + 1 >= len(args):
                print("Error: fix_pin: 'layer' takes a layer name or a comma list "
                      "(M5 or M3,M5)"); return
            layers = [n.strip() for n in args[i + 1].split(",") if n.strip()]
            if not layers:
                print("Error: fix_pin: 'layer' names no layer"); return
            bad = [n for n in layers if n not in session._layer_name_map]
            if bad:
                print(f"Error: fix_pin: unknown layer {', '.join(bad)} (declare it "
                      f"with def_layer first; declared: "
                      f"{', '.join(sorted(session._layer_name_map)) or 'none'})")
                return
            layers = sorted(set(layers), key=layers.index)
            i += 1
        elif tok == "order":
            if i + 1 >= len(args) or args[i + 1].lower() not in ("index", "rindex"):
                print("Error: fix_pin: 'order' takes index (bit 0 at the lowest "
                      "coordinate along the face) or rindex (reversed)"); return
            order = args[i + 1].lower()
            i += 1
        else:
            print(f"Error: fix_pin: unknown token '{args[i]}'"); return
        i += 1

    fp = session.fp
    known = fp.has_block(block)
    if known and rect >= _rect_count(fp, block):
        print(f"Error: fix_pin {block}: rect {rect + 1} out of range "
              f"(block has {_rect_count(fp, block)} rect(s))"); return
    fix = {"faces": faces, "rect": rect, "has_window": window is not None,
           "lo": window[0] if window else 0, "hi": window[1] if window else 0,
           "layers": layers, "order": order}
    if layers and _eff_faces(session, fix) < 0:
        axes, _u = _layer_axes(session, layers)
        print(f"Error: fix_pin {target}: layer {','.join(layers)} "
              f"({'/'.join(sorted(axes))}) cannot reach the face(s) "
              f"'{_faces_text(faces)}': a horizontal layer lands on an east or "
              f"west face, a vertical one on a north or south face; the previous "
              f"setting is kept")
        return
    if sel is not None:
        if getattr(session, "bdb", None) is not None and not known:
            print(f"Error: fix_pin {target}: a per-pin fix needs a flat-flow block "
                  f"declared first; hierarchical designs take block-level fixes only")
            return
        if known and not _strips_with(session, block, fix):
            print(f"Error: fix_pin {target}: '{_declared(fix)}' admits no landing "
                  f"on this block (a face interior to its rects, or a window "
                  f"outside the face); the previous setting is kept")
            return
        sels.setdefault(block, {})[sel] = fix
        shown = f"{block}.{sel[1]}{'*' if sel[0] == 'prefix' else ''}"
        print(f"[PinFix] {shown}: {_declared(fix)} (pin selector)"
              + ("" if known else " (block not declared yet)"))
        return
    prev = fixes.get(block)
    fixes[block] = fix
    _apply(session, block, fix)
    if known and not fp.pin_fix_strips(block):
        # Nothing admissible: a fix that admits no landing at all strands the
        # bus, which is never what was meant.  Refuse and put the old one back.
        if prev is None:
            del fixes[block]
            fp.clear_pin_fix(block)
        else:
            fixes[block] = prev
            _apply(session, block, prev)
        print(f"Error: fix_pin {block}: '{_declared(fix)}' admits no landing "
              f"on this block (a face interior to its rects, or a window "
              f"outside the face); the previous setting is kept")
        return
    n = len(fp.pin_fix_strips(block)) if known else 0
    print(f"[PinFix] {block}: {_declared(fix)}"
          + (f" -> {n} landing strip(s)" if known else " (block not declared yet)"))


def revalidate(session, block, fp=None):
    """A fix declared BEFORE its block exists could not be checked then.  Check it
    when the block arrives — on the session floorplan (`add_block`) or on a
    derived hier frame as each BDB component is projected: a rect out of range,
    or a fix that admits no landing, is refused now (and dropped everywhere —
    there is no previous setting to keep), not discovered later as a stranded
    bus."""
    fixes = session._pin_fixes
    fix = fixes.get(block)
    fp = session.fp if fp is None else fp
    if not fp.has_block(block):
        return
    sels = _sel_store(session).get(block, {})
    dropped = False
    for sel, sfx in list(sels.items()):
        bad = None
        if sfx["rect"] >= _rect_count(fp, block):
            bad = f"rect {sfx['rect'] + 1} out of range"
        elif fp is session.fp and not _strips_with(session, block, sfx):
            bad = f"'{_declared(sfx)}' admits no landing on this block"
        if bad:
            del sels[sel]
            dropped = True
            print(f"Error: fix_pin {block}.{sel[1]}{'*' if sel[0] == 'prefix' else ''}: "
                  f"{bad}; the fix, declared before the block, is dropped")
    if not sels:
        _sel_store(session).pop(block, None)
    if dropped:
        persist(session)      # a delayed drop must not be restored again next time
    if fix is None:
        return
    problem = None
    if fix["rect"] >= _rect_count(fp, block):
        problem = (f"rect {fix['rect'] + 1} out of range (block has "
                   f"{_rect_count(fp, block)} rect(s))")
    elif not fp.pin_fix_strips(block):
        problem = (f"'{_declared(fix)}' admits no landing on this block (a face "
                   f"interior to its rects, or a window outside the face)")
    if problem:
        del fixes[block]
        fp.clear_pin_fix(block)
        if fp is not session.fp:
            session.fp.clear_pin_fix(block)
        print(f"Error: fix_pin {block}: {problem}; the fix, declared before the "
              f"block, is dropped")
        persist(session)


def cmd_dump_pin_fixes(session, cmd, args, cmd_line):
    fixes = session._pin_fixes
    sels = _sel_store(session)
    if not fixes and not sels:
        print("[PinFix] no fixed pins declared")
        return
    for block in sorted(sels):
        for (kind, key), fx in sorted(sels[block].items()):
            star = "*" if kind == "prefix" else ""
            print(f"  fix_pin {block}.{key}{star} {_declared(fx)}   (pin selector)")
    if not fixes:
        return
    print(f"[PinFix] {len(fixes)} fixed block(s):")
    for block in sorted(fixes):
        fix = fixes[block]
        print(f"  fix_pin {block} {_declared(fix)}")
        if session.fp.has_block(block):
            for (x1, y1, x2, y2, face) in session.fp.pin_fix_strips(block):
                name = {1: "north", 2: "south", 4: "east", 8: "west"}.get(face, "?")
                print(f"    {name:5s} strip [{x1},{y1} .. {x2},{y2}]")


_META = "pin_fixes"
_META_GEN = "pin_fixes_gen"


def _snapshot(session):
    sels = [[b, kind, key, fx] for b, d in sorted(_sel_store(session).items())
            for (kind, key), fx in sorted(d.items())]
    return {"blocks": {b: fx for b, fx in sorted(session._pin_fixes.items())},
            "selectors": sels}


def persist(session):
    """ONE meta row (`pin_fixes`, JSON), like the layer reserves: a fix is a
    small declaration with a selector list, which no existing table holds."""
    if getattr(session, "bdb", None) is None:
        return
    snap = _snapshot(session)
    session.bdb.meta_set(_META, json.dumps(snap, sort_keys=True)
                         if snap["blocks"] or snap["selectors"] else "")


def _forget_overridden(session):
    """A restored entry the user has since replaced (a new dict) or removed is
    TYPED state now: it must not be dropped as 'restored' when another BDB is
    opened (typed wins)."""
    rb = getattr(session, "_pin_fix_restored_blocks", None)
    if rb:
        for b in [b for b, fx in rb.items() if session._pin_fixes.get(b) is not fx]:
            del rb[b]
    rs = getattr(session, "_pin_fix_restored_sels", None)
    if rs:
        sels = _sel_store(session)
        for k in [k for k, fx in rs.items()
                  if sels.get(k[0], {}).get((k[1], k[2])) is not fx]:
            del rs[k]


def _tombstone(session, block, sel):
    """An explicit `off` outranks any BDB opened later, whether or not a fix was
    live when it was typed (typed wins)."""
    if sel is None:
        session.__dict__.setdefault("_pin_fix_removed_blocks", set()).add(block)
    else:
        session.__dict__.setdefault("_pin_fix_removed_sels", set()).add(
            (block, sel[0], sel[1]))


def _keys(session):
    return (set(session._pin_fixes),
            {(b, k, n) for b, d in _sel_store(session).items() for (k, n) in d})


def cmd_fix_pin(session, cmd, args, cmd_line):
    before_b, before_s = _keys(session)
    _cmd_fix_pin(session, cmd, args, cmd_line)
    after_b, after_s = _keys(session)
    # A typed removal outranks any BDB opened later (typed wins), so remember it.
    tb = session.__dict__.setdefault("_pin_fix_removed_blocks", set())
    ts = session.__dict__.setdefault("_pin_fix_removed_sels", set())
    tb |= before_b - after_b
    ts |= before_s - after_s
    tb -= after_b
    ts -= after_s
    _forget_overridden(session)
    persist(session)


def _valid_fix(fx):
    return (isinstance(fx, dict)
            and all(isinstance(fx.get(k), int) and not isinstance(fx.get(k), bool)
                    and -2**31 <= fx[k] < 2**31      # the binding takes native ints
                    for k in ("faces", "rect", "lo", "hi"))
            and isinstance(fx.get("has_window"), bool)
            and 0 <= fx["faces"] <= 15                    # N|S|E|W mask, 0 = every face
            and fx["rect"] >= -1
            and fx.get("order", "") in ("", "index", "rindex")
            and (fx.get("layers", []) == [] or (
                isinstance(fx["layers"], list)
                and all(isinstance(n, str) for n in fx["layers"])))
            and (fx["lo"] < fx["hi"] if fx["has_window"] else True))


def _validate_snapshot(snap):
    """The WHOLE stored snapshot, before any of it touches the session: a
    wrong-shaped record anywhere makes the row unreadable as a unit."""
    if not isinstance(snap, dict):
        raise ValueError("not a JSON object")
    blocks, sels = snap.get("blocks", {}), snap.get("selectors", [])
    if not isinstance(blocks, dict) or not isinstance(sels, list):
        raise ValueError("blocks/selectors have the wrong type")
    for b, fx in blocks.items():
        if not isinstance(b, str) or not _valid_fix(fx):
            raise ValueError(f"malformed fix for block {b!r}")
    for rec in sels:
        if (not isinstance(rec, list) or len(rec) != 4
                or not all(isinstance(x, str) for x in rec[:3])
                or rec[1] not in ("name", "prefix") or not _valid_fix(rec[3])):
            raise ValueError(f"malformed selector record {rec!r}")


def restore(session):
    """open_bdb: fixes the BDB holds come back under the typed-wins contract (a
    declaration typed in this session, before or after the open, outranks the
    stored one); a previous BDB's restored entries drop first.  Entries typed
    BEFORE the open had no BDB to write to, so they are stored now."""
    if getattr(session, "bdb", None) is None:
        return 0
    before = json.loads(json.dumps(_snapshot(session)))
    sels = _sel_store(session)
    fixes = session._pin_fixes
    for b in list(getattr(session, "_pin_fix_restored_blocks", {})):
        if fixes.pop(b, None) is not None:
            session.fp.clear_pin_fix(b)
    for b, kind, key in list(getattr(session, "_pin_fix_restored_sels", {})):
        sels.get(b, {}).pop((kind, key), None)
        if b in sels and not sels[b]:
            del sels[b]
    session._pin_fix_restored_blocks, session._pin_fix_restored_sels = {}, {}
    raw = session.bdb.meta_get(_META, "")
    gen = session.bdb.meta_get(_META_GEN, "")
    try:
        if gen:
            session._pin_fix_ckpt = json.loads(gen)
            if not isinstance(session._pin_fix_ckpt, dict):
                session._pin_fix_ckpt = {"unknown": True}
        elif raw:
            # No generation stamp: what the pipeline was generated under cannot be
            # inferred from mutable current state, so no snapshot can match.
            session._pin_fix_ckpt = {"unknown": True}
        else:
            session._pin_fix_ckpt = {"blocks": {}, "selectors": []}
    except ValueError:
        session._pin_fix_ckpt = {"unknown": True}      # corrupt stamp: unverifiable
    n = 0
    if raw:
        try:
            snap = json.loads(raw)
            _validate_snapshot(snap)
            tb = getattr(session, "_pin_fix_removed_blocks", set())
            ts = getattr(session, "_pin_fix_removed_sels", set())
            for b, fx in snap.get("blocks", {}).items():
                if b not in fixes and b not in tb:
                    fixes[b] = fx
                    _apply(session, b, fx)
                    session._pin_fix_restored_blocks[b] = fx
                    n += 1
            for b, kind, key, fx in snap.get("selectors", []):
                if (kind, key) not in sels.get(b, {}) and (b, kind, key) not in ts:
                    sels.setdefault(b, {})[(kind, key)] = fx
                    session._pin_fix_restored_sels[(b, kind, key)] = fx
                    n += 1
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            print(f"Warning: stored pin fixes unreadable ({e}); ignored")
    for b in set(session._pin_fix_restored_blocks) | {k[0] for k in session._pin_fix_restored_sels}:
        revalidate(session, b)
    if n:
        print(f"[PinFix] restored {n} persisted fix declaration(s)")
    if len(session.bundles) and json.loads(json.dumps(_snapshot(session))) != before:
        print("Error: open_bdb: the restored pin fixes change the ones the live "
              "bundles were generated under; the live pipeline is voided — "
              "re-run run_bundler and the stages after it")
        _void_pipeline(session)
    persist(session)
    return n


def _void_pipeline(session):
    """Drop every restored stage together: a stale NUTS/DNUTS result must not
    outlive the wrappers it was solved for."""
    import buda
    session.bundles = buda.BundleWrapperVec()
    session.planner = None
    session.nuts_result = None
    session.detailed_result = None
    session._hier_bundles_orig = []
    if hasattr(session, "_hier_expansion_map"):
        session._hier_expansion_map = {}
    session._planner_is_hier = False


def stamp_generation(session):
    """Bind the fixes in force to the pipeline being persisted: the bundler (the
    stage that reads them) records them beside the bundle rows, so a fix typed
    LATER, which rewrites `pin_fixes` only, still differs from what the stored
    pipeline was generated under.  With no BDB open yet the snapshot is HELD and
    written when the bundles are first persisted (`flush_generation`)."""
    snap = json.loads(json.dumps(_snapshot(session)))
    if getattr(session, "bdb", None) is None:
        session._pin_fix_gen_pending = snap
        return
    session._pin_fix_gen_pending = None
    session.bdb.meta_set(_META_GEN, json.dumps(snap, sort_keys=True))
    session._pin_fix_ckpt = snap            # a same-session reload sees the new generation


def fixes_changed_since_generation(session):
    """True when the fixes in force differ from (or cannot be compared with) the
    ones the stored pipeline was generated under — then hand-edited USER
    candidates the rebundle would keep were built for geometry that no longer
    holds, and must not be carried into the new generation."""
    bdb = getattr(session, "bdb", None)
    if bdb is None:
        return False
    gen = bdb.meta_get(_META_GEN, "")
    now = json.loads(json.dumps(_snapshot(session)))
    try:
        if gen:
            return json.loads(gen) != now
    except ValueError:
        return True
    if (getattr(session, "_pin_fix_ckpt", None) or {}).get("unknown"):
        return True          # restore() found the generation unknowable at open
    # No generation stamp: what any retained candidate was built under is unknown,
    # and the live row is mutable state (restore rewrites it), so it cannot vouch.
    return bool(now["blocks"] or now["selectors"] or bdb.meta_get(_META, ""))


def flush_generation(session):
    """Write a snapshot held from a bundler that ran before `open_bdb`."""
    pend = getattr(session, "_pin_fix_gen_pending", None)
    if pend is None or getattr(session, "bdb", None) is None:
        return
    session._pin_fix_gen_pending = None
    session.bdb.meta_set(_META_GEN, json.dumps(pend, sort_keys=True))
    session._pin_fix_ckpt = pend


def restamp_bundles(session):
    """After load_pipeline: restored candidates carry no per-bundle map (it is
    not stored per candidate); stamp each from the restored selectors.  A
    restored bundle whose nets the CURRENT fixes would repartition (a typed
    selector outranked the checkpoint's) is stale: the whole restored pipeline
    is voided, loudly, rather than routed under one map."""
    import buda
    ckpt = getattr(session, "_pin_fix_ckpt", None)
    now = json.loads(json.dumps(_snapshot(session)))
    if ckpt is not None and now != ckpt and len(session.bundles):
        print("Error: load_pipeline: the pin fixes in force differ from the ones "
              "the checkpoint was generated under; the restored pipeline is "
              "voided — re-run run_bundler and the stages after it")
        _void_pipeline(session)
        return
    if not _sel_store(session):
        return
    if any(_net_pins_of(session, n) is None for w in session.bundles
           for n in w.input.original_bundle.get_net_names()):
        print("Error: load_pipeline: pin selectors are declared but the nets' "
              "endpoints are not (add_net / add_bus before load_pipeline), so "
              "the restored bundles cannot be matched to their fixes; the "
              "restored pipeline is voided")
        _void_pipeline(session)
        return
    for w in session.bundles:
        nets = list(w.input.original_bundle.get_net_names())
        conflict = any(net_fix_conflicts(session, n) for n in nets)
        if conflict or len(_partition_by_fix(session, nets)) > 1:
            print(f"Error: load_pipeline: restored bundle "
                  f"{w.input.original_bundle.id} no longer fits the current pin "
                  f"fixes (its nets now land on one block under different "
                  f"fixes); the restored pipeline is voided — re-run "
                  f"run_bundler and the stages after it")
            _void_pipeline(session)
            return
    for w in session.bundles:
        m = bundle_fix_map(session, w.input.original_bundle.get_net_names())
        if m is not None:
            cands = list(w.input.candidates)     # copies: stamp, then assign back
            for c in cands:
                c.block_fixes = m
            w.input.candidates = cands


COMMANDS = {
    "fix_pin": cmd_fix_pin,
    "dump_pin_fixes": cmd_dump_pin_fixes,
}
