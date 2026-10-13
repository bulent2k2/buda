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

A fix governs a BLOCK / instance (`fix_pin B ...`), only some of its pins
(`fix_pin B.tx ...`, `fix_pin B.d* ...`), or EVERY instance of a BDB cell
(`fix_pin cell:K ...`, `fix_pin cell:K.tx ...`), flat or hierarchical.  A block /
instance fix states its faces in the placed (die) frame; a cell fix states them
in the cell's own frame and each instance reads it turned by its orientation
(`xf_fix`).

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
    resync_cell_fixes(session)      # projected instances re-read their (now narrowed) cell fix


def _apply(session, block, fix, fp=None):
    if is_cell_key(block):
        return            # a cell-level fix is applied per INSTANCE, as blocks are projected
    faces = _eff_faces(session, fix)
    (session.fp if fp is None else fp).set_pin_fix(
        block, max(faces, 0) if faces >= 0 else fix["faces"],
        fix["rect"], fix["has_window"], fix["lo"], fix["hi"])


CELL = "cell:"


def is_cell_key(key):
    return key.startswith(CELL)


def cell_of(key):
    return key[len(CELL):]


def _cell_dims(session, cell):
    """(w, h) of a BDB cell, or None."""
    bdb = getattr(session, "bdb", None)
    if bdb is None:
        return None
    for c in bdb.all_cells():
        if c.name == cell:
            return int(round(c.width)), int(round(c.height))
    return None


def _cell_scratch(session, cell):
    """(floorplan, block name) holding ONLY the cell's own footprint at the
    origin, so a cell-level fix can be judged (rect range, admits a landing)
    before any instance exists.  None when the BDB does not know the cell."""
    import buda
    dims = _cell_dims(session, cell)
    if dims is None:
        return None
    fp = buda.Floorplan()
    rects = [tuple(int(round(v)) for v in r) for r in session.bdb.cell_rects(cell)]
    if len(rects) >= 2:
        fp.add_block_rects("C", rects)
    else:
        fp.add_block("C", 0, 0, dims[0], dims[1])
    return fp, "C"


def xf_fix(fix, orient, cw, ch):
    """A cell-local fix as seen on an instance placed with `orient` (cw x ch =
    the cell's own box).  Faces turn with the cell; a window along a face is
    re-read on the face it lands on, reversed when the transform reverses the
    along-axis; and `order` flips with that reversal, because 'bit 0 at the low
    coordinate' is a statement in the PLACED frame while the cell's pins keep
    their bit order.  A window or an `order` is only defined for faces on ONE
    axis (checked at declaration): a mixed set reads its window on a different
    axis per face, so no single transformed window exists."""
    import orient_rect
    swap, rx, ry = orient_rect.ORIENT_MAPS.get(orient, (0, 0, 0))
    vec = {1: (0, 1), 2: (0, -1), 4: (1, 0), 8: (-1, 0)}

    def vmap(v):
        x, y = v
        if swap:
            x, y = y, x
        if rx:
            x = -x
        if ry:
            y = -y
        return x, y
    bit_of = {v: b for b, v in vec.items()}
    out = dict(fix)
    out["faces"] = 0
    for b, v in vec.items():
        if fix["faces"] & b:
            out["faces"] |= bit_of[vmap(v)]
    if fix["has_window"] or fix.get("order"):
        fb = [b for b in vec if fix["faces"] & b]
        # `order` is a statement about the along-axis too; faces=0 is both axes
        horiz_face = bool(fb) and all(b in (4, 8) for b in fb)
        vert_face = bool(fb) and all(b in (1, 2) for b in fb)
        if not (horiz_face or vert_face):
            return out        # refused at declaration; defensive
        lo, hi = fix["lo"], fix["hi"]
        if not fix["has_window"]:
            lo, hi = 0, 1

        def pt(t):
            p = (0, t) if horiz_face else (t, 0)
            x, y = p
            bw, bh = cw, ch
            if swap:
                x, y, bw, bh = y, x, ch, cw
            if rx:
                x = bw - x
            if ry:
                y = bh - y
            return x, y
        a, b = pt(lo), pt(hi)
        new_vert = bool(out["faces"] & 12)        # east/west faces fill the y axis
        ca, cb = (a[1], b[1]) if new_vert else (a[0], b[0])
        reversed_ = cb < ca
        if fix["has_window"]:
            out["lo"], out["hi"] = min(ca, cb), max(ca, cb)
        if fix.get("order") and reversed_:
            out["order"] = "rindex" if fix["order"] == "index" else "index"
    return out


def apply_cell_fix(session, fp, name, comp):
    """Project the CELL-level fix of `comp`'s cell onto the block `name` of the
    frame `fp`, transformed by the instance's orientation.  An instance-level
    declaration (`fix_pin <name>`) wins and is applied by the caller's own
    replay; this is only reached when there is none."""
    key = CELL + comp.cell
    fix = session._pin_fixes.get(key)
    if fix is None:
        return
    dims = _cell_dims(session, comp.cell)
    if dims is None:
        return
    cw, ch = dims
    bw = int(round(comp.x2 - comp.x1))
    bh = int(round(comp.y2 - comp.y1))
    import orient_rect
    orient = comp.orient if comp.orient in orient_rect.ORIENT_MAPS else "N"
    swap = orient_rect.ORIENT_MAPS[orient][0]
    want = (ch, cw) if swap else (cw, ch)
    if (bw, bh) != want and cw != ch:
        said = session.__dict__.setdefault("_cellfix_rot_said", set())
        if name not in said:
            said.add(name)
            print(f"Warning: [PinFix] instance '{comp.name}' of cell '{comp.cell}' "
                  f"is {bw}x{bh} but orientation '{orient}' of a {cw}x{ch} cell "
                  f"would be {want[0]}x{want[1]} (a container rotated after its "
                  f"children were placed keeps their tokens, BUDA-1918): its "
                  f"orientation cannot be read, so the cell-level fix is NOT "
                  f"applied to it — declare `fix_pin {comp.name} ...` instead")
        return
    tfix = xf_fix(fix, orient, cw, ch)
    if tfix.get("layers") and _eff_faces(session, tfix) < 0:
        print(f"Warning: [PinFix] instance '{comp.name}' ({orient}): layer "
              f"{','.join(tfix['layers'])} cannot reach the face(s) "
              f"'{_faces_text(tfix['faces'])}' its cell-level fix turns into; "
              f"the fix is NOT applied to it")
        return
    _apply(session, name, tfix, fp)
    if fp is session.fp:
        session.__dict__.setdefault("_cellfix_applied", set()).add(name)


def resync_cell_fixes(session):
    """Bring the session floorplan's blocks in line with the fixes now in force.
    A cell-level fix is applied as each component is PROJECTED; one declared,
    replaced or removed afterwards (or an instance override removed, which must
    give the cell's fix back) would otherwise leave the blocks already
    projected on the old answer, and the generator and audits would read it."""
    if getattr(session, "bdb", None) is None:
        return
    applied = session.__dict__.setdefault("_cellfix_applied", set())
    if not applied and not any(is_cell_key(k) for k in session._pin_fixes):
        return
    fp = session.fp
    for name in list(applied):
        if fp.has_block(name) and name not in session._pin_fixes:
            fp.clear_pin_fix(name)
    applied.clear()
    for comp in _comps(session).values():
        nm = comp.name
        if (CELL + comp.cell) in session._pin_fixes and nm not in session._pin_fixes \
                and fp.has_block(nm):
            apply_cell_fix(session, fp, nm, comp)
    for nm, fx in session._pin_fixes.items():
        if not is_cell_key(nm) and fp.has_block(nm):
            _apply(session, nm, fx)


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


def _match_sel(sels, pin_name):
    """The selector of ONE owner that governs `pin_name`: exact pin > bus >
    longest prefix (None = the owner has no selector for it)."""
    from bus_names import bus_name
    if not sels:
        return None
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
    return best[1] if best is not None else None


def _comps(session):
    """name -> BDB component, rebuilt once per command (components move)."""
    bdb = getattr(session, "bdb", None)
    if bdb is None:
        return {}
    seq = getattr(session, "_cmd_seq", 0)
    c = getattr(session, "_pf_comp_cache", None)
    if c is None or c[0] != seq or c[1] is not bdb:
        c = (seq, bdb, {x.name: x for x in bdb.all_components()})
        session._pf_comp_cache = c
    return c[2]


def _placed(session, fx, comp):
    """A cell-level fix as it reads on one INSTANCE (turned by its orientation),
    or None when the instance's orientation cannot be read (see
    apply_cell_fix) or the turned fix is unsatisfiable by its layers."""
    import orient_rect
    dims = _cell_dims(session, comp.cell)
    if dims is None:
        return None
    cw, ch = dims
    bw = int(round(comp.x2 - comp.x1))
    bh = int(round(comp.y2 - comp.y1))
    orient = comp.orient if comp.orient in orient_rect.ORIENT_MAPS else "N"
    swap = orient_rect.ORIENT_MAPS[orient][0]
    want = (ch, cw) if swap else (cw, ch)
    if (bw, bh) != want and cw != ch:
        said = session.__dict__.setdefault("_cellfix_rot_said", set())
        if comp.name not in said:
            said.add(comp.name)
            print(f"Warning: [PinFix] instance '{comp.name}' of cell '{comp.cell}' "
                  f"is {bw}x{bh} but orientation '{orient}' of a {cw}x{ch} cell "
                  f"would be {want[0]}x{want[1]}: its orientation cannot be read "
                  f"(BUDA-1918), so the cell-level fix is NOT applied to it")
        return None
    t = xf_fix(fx, orient, cw, ch)
    if t.get("layers") and _eff_faces(session, t) < 0:
        return None
    return t


def _resolve(session, block, pin_name, local=False):
    """The effective fix for ONE pin of an owner (None = unfixed), as it reads
    in the placed frame.  `block` is the owner PATH (a flat block name, or a
    hierarchical instance path); `local` additionally lets the owner's last
    path segment match, because a cell-local frame names its children that way.

    Owner specificity first (an instance takes over what it names), then pin
    specificity inside the owner:
        instance selectors > instance block-level >
        cell selectors > cell block-level      (cell fixes turned by the
                                                instance's orientation)
    and within one owner exact pin > bus > longest prefix."""
    sels = _sel_store(session)
    fixes = session._pin_fixes
    keys = [block]
    if local and "/" in block:
        keys.append(block.rsplit("/", 1)[-1])
    for k in keys:                 # each owner COMPLETELY (selector, then block-level) before the next
        hit = _match_sel(sels.get(k), pin_name)
        if hit is not None:
            return hit, (k, _fixkey(hit))
        if k in fixes:
            return fixes[k], (k, _fixkey(fixes[k]))
    if any(is_cell_key(k) for k in fixes) or any(is_cell_key(k) for k in sels):
        comp = _comps(session).get(block)
        if comp is not None:
            ck = CELL + comp.cell
            hit = _match_sel(sels.get(ck), pin_name)
            if hit is None:
                hit = fixes.get(ck)
            if hit is not None:
                return _placed(session, hit, comp), (ck, _fixkey(hit))
    return None, None


def resolve_fix(session, block, pin_name, local=False):
    """The effective fix for ONE pin (see `_resolve`), as placed."""
    return _resolve(session, block, pin_name, local)[0]


def _owner_has_fixes(session, path, local):
    """True when ANY fix declaration could govern some pin of this owner (its
    own, or its cell's).  An UNFIXED pin of such an owner is still a distinct
    landing from a fixed one — two nets with different signatures there cannot
    share a bundle — so the signature keeps its None."""
    sels = _sel_store(session)
    fixes = session._pin_fixes
    keys = [path] + ([path.rsplit("/", 1)[-1]] if local and "/" in path else [])
    if any(k in fixes or k in sels for k in keys):
        return True
    if any(is_cell_key(k) for k in fixes) or any(is_cell_key(k) for k in sels):
        comp = _comps(session).get(path)
        if comp is not None:
            ck = CELL + comp.cell
            return ck in fixes or ck in sels
    return False


def frame_block(path, local):
    """The name the bundle's frame gives the block at `path`."""
    return path.rsplit("/", 1)[-1] if local and "/" in path else path


def _bdb_net_index(session):
    """net -> (driver pin, [receiver pins]) as 'path.pin' strings over LEAF
    pins, for nets no `add_net` declared (an imported design).  Ancestor
    interface pins are skipped: a fix names a leaf block's pin."""
    seq = getattr(session, "_cmd_seq", 0)
    c = getattr(session, "_pf_netpin_cache", None)
    bdb = getattr(session, "bdb", None)
    if bdb is None:
        return {}
    if c is not None and c[0] == seq and c[1] is bdb:
        return c[2]
    comps = {x.id: x for x in bdb.all_components()}
    names = {n.id: n.name for n in bdb.all_nets()}
    idx = {}
    for pr in bdb.all_pins():
        cp = comps.get(pr.comp_id)
        nn = names.get(pr.net_id)
        if cp is None or nn is None or not cp.is_leaf:
            continue
        idx.setdefault(nn, []).append((f"{cp.name}.{pr.pin_name}", pr.dir))
    out = {}
    for nn, pins in idx.items():
        drv = next((p for p, d in pins if d == "OUTPUT"), pins[0][0])
        out[nn] = (drv, [p for p, _d in pins if p != drv])
    session._pf_netpin_cache = (seq, bdb, out)
    return out


def _net_pins_of(session, net):
    hit = getattr(session, "_net_pins", {}).get(net)
    if hit is not None:
        return hit
    if getattr(session, "bdb", None) is not None and (
            session._pin_fixes or _sel_store(session)):
        return _bdb_net_index(session).get(net)
    return None


def _any_fixes(session):
    return bool(session._pin_fixes or _sel_store(session))


def net_fix_signature(session, net, local=False, names_local=None, ident=False):
    """((frame block, fixkey), ...) over the blocks this net touches that
    resolve to any fix — two nets with different signatures cannot share a
    bundle.  `local` is how names are RESOLVED (a local name may match a
    declaration); `names_local` is how the bundle's frame NAMES its blocks
    (an expanded instance resolves like its template but lives in the
    absolute frame)."""
    pins = _net_pins_of(session, net)
    if pins is None or not _any_fixes(session):
        return ()
    nl = local if names_local is None else names_local
    drv, rcvs = pins
    sig = {}
    for pin in [drv] + list(rcvs):
        path, _, name = pin.rpartition(".")
        if _owner_has_fixes(session, path, local):
            fx, src = _resolve(session, path, name, local)
            sig[frame_block(path, nl)] = (src if ident else _fixkey(fx)) if fx is not None else None
    return tuple(sorted(sig.items(), key=lambda kv: kv[0]))


def net_fix_conflicts(session, net, local=False):
    """[(block, [pin names])] where ONE net lands on a block through pins whose
    fixes differ.  A net is a single bundle member, so it cannot be split to
    honour both landings."""
    pins = _net_pins_of(session, net)
    if pins is None or not _any_fixes(session):
        return []
    drv, rcvs = pins
    seen = {}           # block -> {fixkey: [pin names]}
    for pin in [drv] + list(rcvs):
        path, _, name = pin.rpartition(".")
        if _owner_has_fixes(session, path, local):
            key = _fixkey(resolve_fix(session, path, name, local))
            seen.setdefault(frame_block(path, local), {}).setdefault(key, []).append(name)
    return [(b, [n for names in d.values() for n in names])
            for b, d in seen.items() if len(d) > 1]


def _local_name_decl(session):
    """A fix declared on a child's LOCAL name (`fix_pin pb_i south`): a cell-local
    frame matches it by name, but the absolute frame the expanded instances live
    in names the same block `proc_i/pb_i` and holds no such declaration."""
    if getattr(session, "bdb", None) is None:
        return False
    keys = [k for k in list(session._pin_fixes) + list(_sel_store(session))
            if "/" not in k and not is_cell_key(k)]
    if not keys:
        return False
    # decided from the hierarchy, not from what the session floorplan lacks: a
    # top-level block may share a nested child's local name
    nested = {n.rsplit("/", 1)[-1] for n in _comps(session) if "/" in n}
    return any(k in nested for k in keys)


def bundle_fix_map(session, net_names, local=False, names_local=None, always=False):
    """The EFFECTIVE fix map for one bundle ({block: (faces, rect, has_window,
    lo, hi)}), or None when no per-pin selector exists (the floorplan's own
    block-level fixes then apply unchanged — the byte-identical path) unless
    `always` asks for the map regardless."""
    if not always and not any(True for _ in _sel_store(session)):
        return None
    out = {}
    for net in net_names:
        for blk, key in net_fix_signature(session, net, local, names_local):
            if key is not None and blk not in out:
                out[blk] = _geom_key(session, key)
    return out


def _expanded_ids(session):
    """Bundle ids of the per-instance wrappers `run_planner hier` expanded."""
    seq = getattr(session, "_cmd_seq", 0)
    c = getattr(session, "_pf_exp_cache", None)
    em = getattr(session, "_hier_expansion_map", None) or {}
    if c is None or c[0] != seq or c[1] is not em:
        ids = {x.input.original_bundle.id for ws in em.values() for x in ws}
        c = (seq, em, ids)
        session._pf_exp_cache = c
    return c[2]


def wrapper_local(session, w):
    """True when the wrapper's CURRENT geometry is in a cell-local frame (a
    template, before expansion) — its blocks carry the child's local name.  An
    expanded instance, a cross-level / depth bundle and a flat bundle are all
    in an absolute frame with full block names."""
    b = w.input.original_bundle
    return bool(b.cell_context) and b.id not in _expanded_ids(session)


def wrapper_pins(session, w):
    """[(pin string, owner path, pin name, frame block, fix)] for every pin of
    every net of a bundle that resolves to a fix.  Names RESOLVE the way the
    bundle was born (a cell-level template and its expanded instances accept a
    child's local name) and are FRAMED the way its geometry is now."""
    resolve_local = bool(w.input.original_bundle.cell_context)
    frame_local = wrapper_local(session, w)
    out = []
    for net in w.input.original_bundle.get_net_names():
        pins = _net_pins_of(session, net)
        if pins is None:
            continue
        for pin in [pins[0]] + list(pins[1]):
            path, _, name = pin.rpartition(".")
            fx = resolve_fix(session, path, name, resolve_local)
            if fx is not None:
                out.append((pin, path, name, frame_block(path, frame_local), fx))
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
    # A name accepts both spellings here (full path / the child's local name):
    # a layer set needs no geometry, only WHICH pins the bundle lands on.
    for pin, _path, _name, _blk, fix in wrapper_pins(session, w):
        if not fix.get("layers"):
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
    fp = session.fp
    for pin, _path, _name, block, fix in wrapper_pins(session, w):
        if not fix.get("layers") or not fp.has_block(block):
            continue
        ok = {session._layer_name_map.get(n) for n in fix["layers"]}
        for (x1, y1, x2, y2, _f) in _strips_in(session, fp, block, fix):
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


def segment_orders(session, w, topo, fp=None):
    """{segment index: {'index'|'rindex'}} — the bit order each segment is held
    to by the fixed pins it ENDS on (an end inside an admitted landing strip).
    `fp` is the frame the topology's geometry lives in (the session floorplan
    for anything already absolute; a generator's cell-local frame for a
    template that is still being generated)."""
    out = {}
    if not has_order_fixes(session):
        return out
    fp = session.fp if fp is None else fp
    segs = list(topo.segments)
    strips = {}
    for pin, path, name, block, fix in wrapper_pins(session, w):
        if not fix.get("order") or not fp.has_block(block):
            continue
        key = (block, _fixkey(fix))
        if key not in strips:
            strips[key] = (fix["order"], _strips_in(session, fp, block, fix))
        order, rects = strips[key]
        for i, sg in enumerate(segs):
            for p in (sg.start, sg.end):
                if any(x1 <= p.x <= x2 and y1 <= p.y <= y2
                       for (x1, y1, x2, y2, _f) in rects):
                    out.setdefault(i, set()).add(order)
    return out


def filter_order_conflicts(session, w, cands, fp=None):
    """A straight segment cannot reverse its bits, so a candidate whose one
    segment joins two landings of OPPOSITE order is dropped (a bend may differ:
    each leg takes its landing's order).  Decided per candidate, at generation,
    because only a candidate knows which landings share a segment.  When every
    candidate conflicts the pool is kept and the conflict is reported — the bus
    must never strand."""
    if not has_order_fixes(session) or not cands:
        return cands
    keep = [c for c in cands
            if not any(len(o) > 1 for o in segment_orders(session, w, c, fp).values())]
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


def set_gen_fixes(session, tg, w, local=None):
    """Point a generator at the bundle's effective fixes (no-op without selectors).
    The map is keyed by the names the bundle's FRAME gives its blocks, so a
    cell-local template reads the children's local names."""
    if local is None:
        local = wrapper_local(session, w)
    m = bundle_fix_map(session, w.input.original_bundle.get_net_names(), local)
    if m is None:
        tg.clear_block_fixes()
    else:
        tg.set_block_fixes(m)


def stamp_expanded(session, w, orient=None):
    """An expanded instance wrapper: re-resolve its fix map in the ABSOLUTE
    frame (full block names, the instance's own pins and orientations).  A
    no-op without per-pin selectors, where the floorplan's own fixes apply."""
    b = w.input.original_bundle
    if orient and orient != "N":
        # The template's routing is turned with this occurrence.  A CELL fix turns
        # with its instances (xf_fix); an instance or local-name fix states its
        # faces in the placed frame, so one turned occurrence cannot share the
        # template's solution with the others.
        bad = set()
        for net in b.get_net_names():
            for pin in (lambda p: [p[0]] + list(p[1]))(_net_pins_of(session, net) or ("", [])):
                if not pin:
                    continue
                path, _, name = pin.rpartition(".")
                fx, src = _resolve(session, path, name, bool(b.cell_context))
                if fx is not None and src and not is_cell_key(src[0]):
                    bad.add(src[0])
        if bad:
            import sys
            inst = b.instances[0] if b.instances else "?"
            print(f"Error: pin fix(es) on {', '.join(sorted(bad))} govern a cell-level "
                  f"bundle class whose occurrence {inst} is turned ({orient}) relative "
                  f"to the template: an instance or local-name fix states its faces in "
                  f"the placed frame, so it cannot ride a template solved once and "
                  f"turned.  Declare the fix on the CELL (`fix_pin cell:<name> ...`), "
                  f"which turns with each instance.")
            sys.exit(1)
    m = bundle_fix_map(session, b.get_net_names(), bool(b.cell_context), False,
                       always=_local_name_decl(session))
    if m is None:
        return
    cands = list(w.input.candidates)         # copies: stamp, then assign back
    for c in cands:
        c.block_fixes = m
    w.input.candidates = cands


def _net_fix_map(session, net, local=False, ident=False):
    return {blk: key for blk, key in net_fix_signature(session, net, local, None, ident)}


def _partition_idx(session, nets, local=False, ident=False):
    """Greedy partition of a bundle's nets into parts whose per-block fixes do
    not CONFLICT: [(merged {block: key}, [net indices])].  Nets landing on
    different fixed blocks share a part (one map can hold both); only
    differing resolutions at a block they share split."""
    parts = []
    for i, n in enumerate(nets):
        m = _net_fix_map(session, n, local, ident)
        for merged, members in parts:
            if all(merged.get(b, k) == k for b, k in m.items()):
                merged.update(m)
                members.append(i)
                break
        else:
            parts.append((dict(m), [i]))
    return parts


def _partition_by_fix(session, nets, local=False):
    return [(g, [nets[i] for i in idxs])
            for g, idxs in _partition_idx(session, nets, local)]


def _refuse_conflicting_nets(session, nets, local):
    import sys
    for n in nets:
        bad = net_fix_conflicts(session, n, local)
        if bad:
            blk, pins = bad[0]
            print(f"Error: net '{n}' reaches block '{blk}' through pins "
                  f"{', '.join(pins)} whose pin fixes differ — one net "
                  f"cannot land on two faces; give those pins the same "
                  f"fix or split the net")
            sys.exit(1)


def split_bundles_by_fix(session, raw_bundles):
    """Fix-uniform split: a bundle whose nets resolve to DIFFERENT fixes at one
    block cannot land on one face — split into conflict-free parts, LOUDLY.
    No selectors declared = input returned untouched."""
    if not _sel_store(session):
        return raw_bundles
    import buda
    for b in raw_bundles:
        _refuse_conflicting_nets(session, b.get_net_names(), False)
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


def split_hbundles_by_fix(session, raw_bundles):
    """Hier twin of `split_bundles_by_fix`, applied to TEMPLATE bundles BEFORE
    per-instance expansion (the NDR rule-class split's position and shape): a
    cell-local template's REPLICAS split in lockstep with it, every HBundle
    hier field is preserved per part, per-net fan-in arrays split in the same
    net partition, and a class the fixes govern INCONSISTENTLY across its
    occurrences is a hard error — a template is solved once and copied, so every
    occurrence must resolve to the same fixes on the same bits.  Names are read
    in the bundle's own frame (a cell-local template: the children's local
    names).  The occurrence-congruence check runs for ANY applicable fix, not
    only selectors: a block-level instance override on one occurrence of a
    replicated template is just as unable to ride a template solved once.  No
    fixes declared = input returned untouched."""
    if not _any_fixes(session):
        return raw_bundles
    import sys
    from buda_cmds.bundling_cmds import _fanin_core
    for b in raw_bundles:
        _refuse_conflicting_nets(session, b.get_net_names(), bool(b.cell_context))
    counter = [max((b.id for b in raw_bundles), default=0)]

    def _new_id():
        counter[0] += 1
        return counter[0]

    def _classes(b):
        """[(sorted {block: fixkey}, [net indices])]: what each part lands on,
        so occurrences are congruent only when they resolve to the same
        DECLARATIONS as well as the same bits.  The declaration, not its placed
        result: a cell-level fix legitimately reads as different faces on a
        turned occurrence, which the expansion's transform accounts for."""
        return [(tuple(sorted(g.items(), key=lambda kv: kv[0])), idxs) for g, idxs in
                _partition_idx(session, list(b.get_net_names()),
                               bool(b.cell_context), ident=True)]

    def _split_one(b, classes):
        classes = [idxs for _g, idxs in classes]
        if len(classes) <= 1:
            return [b]
        nets = list(b.get_net_names())
        drvs = list(b.net_drivers)
        rcvs = [list(r) for r in b.net_receivers]
        fanin = bool(drvs) and len(drvs) == len(nets)
        sizes = "+".join(str(len(i)) for i in classes)
        print(f"[PinFix] split hbundle {b.id} ({len(nets)} bits) into "
              f"{len(classes)} fix-uniform part(s) ({sizes}): its nets land on "
              f"one fixed block under different pin fixes, and one bundle lands "
              f"on one face")
        parts = []
        for k, idxs in enumerate(classes, start=1):
            nb = session._clone_hbundle_with_id(b, b.id if k == 1 else _new_id())
            nb.net_names = [nets[i] for i in idxs]
            suffix = f"|PINFIX:{k}"
            if fanin:
                pd = [drvs[i] for i in idxs]
                pr = [rcvs[i] for i in idxs]
                nb.net_drivers = pd
                nb.net_receivers = pr
                core, uniq_drv, root, extra_rcv = _fanin_core(pd, pr)
                nb.reason = (core if core else b.reason) + suffix
                if core and b.cell_context and b.entry_busterm_ids:
                    nb.entry_busterm_ids = ["bt:" + d for d in uniq_drv]
                    nb.exit_busterm_ids = (["bt:" + root]
                                           + ["bt:" + r for r in extra_rcv])
            else:
                nb.reason = b.reason + suffix
            parts.append(nb)
        return parts

    cell_ids = {b.id for b in raw_bundles if b.cell_context}

    def _is_replica(b):
        return bool(b.cell_context and b.parent_id in cell_ids and b.instances)

    replicas_of = {}
    for b in raw_bundles:
        if _is_replica(b):
            replicas_of.setdefault(b.parent_id, []).append(b)
    out = []
    for b in raw_bundles:
        if _is_replica(b):
            continue
        reps = replicas_of.get(b.id, ())
        tcls = _classes(b)
        for rb in reps:
            rcls = _classes(rb)
            if rcls != tcls:
                print(f"Error: pin fixes govern cell-level bundle class {b.id} "
                      f"INCONSISTENTLY across its occurrences: the template "
                      f"partitions as {[len(i) for _g, i in tcls]} but occurrence "
                      f"bundle {rb.id} ({rb.instances[0] if rb.instances else '?'}) "
                      f"as {[len(i) for _g, i in rcls]} — a cell-local template is "
                      f"solved once and copied, so every occurrence must resolve "
                      f"to the same fixes on the same bits.  Declare the fix on "
                      f"the CELL (`fix_pin cell:<name>.<pin>`) or on the child's "
                      f"local name, not on one instance's path.")
                sys.exit(1)
        tparts = _split_one(b, tcls)
        out.extend(tparts)
        for rb in reps:
            rparts = _split_one(rb, _classes(rb))
            for tp, rp in zip(tparts, rparts):
                rp.parent_id = tp.id
            out.extend(rparts)
    return out


def _is_instance_owner(session, name):
    """`name` is a BDB instance path, or the local name of one (the name a
    cell-local frame gives a child): a hierarchical pin selector's owner."""
    if getattr(session, "bdb", None) is None:
        return False
    comps = _comps(session)
    if name in comps:
        return True
    return "/" not in name and any(c.rsplit("/", 1)[-1] == name for c in comps)


def _frame_of(session, block):
    """(floorplan, block name) a fix on `block` is judged against: the session
    floorplan for a block, the cell's own footprint for a `cell:` key.
    (None, None) when there is nothing to judge against yet."""
    if is_cell_key(block):
        scratch = _cell_scratch(session, cell_of(block))
        return scratch if scratch else (None, None)
    return session.fp, block


def _strips_in(session, fp, name, fix):
    """Strips `fix` would admit on block `name` of frame `fp`, leaving whatever
    the frame held there (a cell-level fix applied at projection included)
    exactly as it was."""
    had = fp.has_pin_fix(name)
    prev = fp.pin_fix(name) if had else None
    _apply(session, name, fix, fp)
    try:
        return fp.pin_fix_strips(name)
    finally:
        if had:
            fp.set_pin_fix(name, *prev)
        else:
            fp.clear_pin_fix(name)


def _strips_with(session, block, fix):
    """Strips `fix` would admit on `block` (a session-floorplan block, or a
    `cell:` key judged on the cell's own footprint)."""
    fp, name = _frame_of(session, block)
    if fp is None:
        return []
    return _strips_in(session, fp, name, fix)


def _cmd_fix_pin(session, cmd, args, cmd_line):
    # fix_pin <block>[.*] [north|south|east|west|H|V ...] [rect <n>] [at <lo>..<hi>]
    # fix_pin <block> off
    if not args:
        print("Error: usage: fix_pin <block> [north|south|east|west|H|V ...] "
              "[rect <n>] [at <lo>..<hi>]   |   fix_pin <block> off")
        return
    target = args[0]
    sel = None      # (kind, key) for a per-pin selector, else None (block-level)
    # `cell:<name>` governs EVERY instance of a BDB cell, transformed by each
    # instance's orientation; anything else names one block / instance.  Block
    # names may themselves contain a period (`u.core`, `IO.PAD`), so the exact
    # declared name wins; only an explicit trailing `.*` is stripped, and a
    # suffix is read as a per-pin selector only when its prefix IS a known block
    # (or, for `cell:`, a known cell).
    if target.startswith(CELL):
        body = target[len(CELL):]
        if _cell_dims(session, body) is not None:
            block = CELL + body
        elif body.endswith(".*"):
            block = CELL + body[:-2]
        elif "." in body and _cell_dims(session, body.rsplit(".", 1)[0]) is not None:
            cname, _, sel_text = body.rpartition(".")
            block = CELL + cname
            sel = (("prefix", sel_text[:-1]) if sel_text.endswith("*")
                   else ("name", sel_text))
        else:
            block = CELL + body
        if _cell_dims(session, cell_of(block)) is None:
            print(f"Error: fix_pin {target}: no BDB cell named '{cell_of(block)}' "
                  f"(a cell-level fix needs open_bdb and add_cell / import first)")
            return
    elif session.fp.has_block(target):
        block = target
    elif target.endswith(".*"):
        block = target[:-2]
    elif "." in target and (session.fp.has_block(target.rsplit(".", 1)[0])
                            or _is_instance_owner(session, target.rsplit(".", 1)[0])):
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

    cellfix = is_cell_key(block)
    fp, fname = _frame_of(session, block)
    if fp is None:
        fp, fname = session.fp, block
    known = fp.has_block(fname)
    if known and rect >= _rect_count(fp, fname):
        print(f"Error: fix_pin {block}: rect {rect + 1} out of range "
              f"(block has {_rect_count(fp, fname)} rect(s))"); return
    if cellfix and (window is not None or order):
        # A window / order is stated along ONE face axis; a cell-level fix is
        # re-read per instance orientation, and a mixed face set reads its
        # window on a different axis per face, so there is no single answer.
        if not (faces and (all(f in (4, 8) for f in _FACE_BITS.values() if faces & f)
                           or all(f in (1, 2) for f in _FACE_BITS.values() if faces & f))):
            print(f"Error: fix_pin {block}: 'at' and 'order' on a cell-level fix "
                  f"need faces on ONE axis (H, V, or a single face): the window "
                  f"is read along the face, and an instance turned 90 degrees "
                  f"reads a mixed set along a different axis per face")
            return
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
        if not known and not cellfix and not _is_instance_owner(session, block):
            print(f"Error: fix_pin {target}: a per-pin fix needs its block declared "
                  f"first (add_block, or an instance of the open BDB)")
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
    if cellfix:
        # judged on the cell's own footprint (a scratch frame): `_apply` skips
        # a cell key, so apply it there for the strip count
        _apply(session, fname, fix, fp)
    if known and not fp.pin_fix_strips(fname):
        # Nothing admissible: a fix that admits no landing at all strands the
        # bus, which is never what was meant.  Refuse and put the old one back.
        if prev is None:
            del fixes[block]
            fp.clear_pin_fix(fname)
        else:
            fixes[block] = prev
            _apply(session, block, prev)
        print(f"Error: fix_pin {block}: '{_declared(fix)}' admits no landing "
              f"on this block (a face interior to its rects, or a window "
              f"outside the face); the previous setting is kept")
        return
    n = len(fp.pin_fix_strips(fname)) if known else 0
    print(f"[PinFix] {block}: {_declared(fix)}"
          + (f" -> {n} landing strip(s)" if known else " (block not declared yet)")
          + (" (every instance, turned with its orientation)" if cellfix else ""))


def revalidate(session, block, fp=None, fp_name=None):
    """A fix declared BEFORE its block exists could not be checked then.  Check it
    when the block arrives — on the session floorplan (`add_block`) or on a
    derived hier frame as each BDB component is projected: a rect out of range,
    or a fix that admits no landing, is refused now (and dropped everywhere —
    there is no previous setting to keep), not discovered later as a stranded
    bus."""
    fixes = session._pin_fixes
    fix = fixes.get(block)
    if is_cell_key(block) and fp is None:
        # a cell-level declaration is judged on the cell's own footprint
        fp, fp_name = _frame_of(session, block)
        if fp is None:
            return
    fp = session.fp if fp is None else fp
    name = block if fp_name is None else fp_name     # the owner key vs the frame's name for it
    if not fp.has_block(name):
        return
    sels = _sel_store(session).get(block, {})
    dropped = False
    for sel, sfx in list(sels.items()):
        bad = None
        if sfx["rect"] >= _rect_count(fp, name):
            bad = f"rect {sfx['rect'] + 1} out of range"
        elif (fp is session.fp or fp_name is not None) and not _strips_in(session, fp, name, sfx):
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
    if fix["rect"] >= _rect_count(fp, name):
        problem = (f"rect {fix['rect'] + 1} out of range (block has "
                   f"{_rect_count(fp, name)} rect(s))")
    elif not _strips_in(session, fp, name, fix):
        # judged by its OWN temporary application: what the frame holds there now
        # may be another owner's (a rejected override's) fix
        problem = (f"'{_declared(fix)}' admits no landing on this block (a face "
                   f"interior to its rects, or a window outside the face)")
    if problem:
        del fixes[block]
        fp.clear_pin_fix(name)
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
    resync_cell_fixes(session)


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
    resync_cell_fixes(session)      # blocks projected before this open read the restored cell fixes
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
    if not _sel_store(session) and not _local_name_decl(session):
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
        loc = wrapper_local(session, w)
        conflict = any(net_fix_conflicts(session, n, loc) for n in nets)
        if conflict or len(_partition_by_fix(session, nets, loc)) > 1:
            print(f"Error: load_pipeline: restored bundle "
                  f"{w.input.original_bundle.id} no longer fits the current pin "
                  f"fixes (its nets now land on one block under different "
                  f"fixes); the restored pipeline is voided — re-run "
                  f"run_bundler and the stages after it")
            _void_pipeline(session)
            return
    for w in session.bundles:
        m = bundle_fix_map(session, w.input.original_bundle.get_net_names(),
                           bool(w.input.original_bundle.cell_context),
                           wrapper_local(session, w), always=_local_name_decl(session))
        if m is not None:
            cands = list(w.input.candidates)     # copies: stamp, then assign back
            for c in cands:
                c.block_fixes = m
            w.input.candidates = cands


COMMANDS = {
    "fix_pin": cmd_fix_pin,
    "dump_pin_fixes": cmd_dump_pin_fixes,
}
