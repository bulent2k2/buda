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
    return " ".join(parts)


def _apply(session, block, fix):
    session.fp.set_pin_fix(block, fix["faces"], fix["rect"], fix["has_window"],
                           fix["lo"], fix["hi"])


def _sel_store(session):
    st = getattr(session, "_pin_fix_sel", None)
    if st is None:
        st = session._pin_fix_sel = {}
    return st


def _fixkey(fix):
    return None if fix is None else (fix["faces"], fix["rect"], fix["has_window"],
                                     fix["lo"], fix["hi"])


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
                out[blk] = key
    return out


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
        if sels.get(block, {}).pop(sel, None) is not None:
            if not sels[block]:
                del sels[block]
            print(f"[PinFix] {block}.{sel[1]}{'*' if sel[0] == 'prefix' else ''}: "
                  f"pin fix removed")
        else:
            print(f"[PinFix] {target}: no such pin fix declared")
        return
    if sel is None and len(args) == 2 and args[1].lower() == "off":
        if block in fixes:
            del fixes[block]
            session.fp.clear_pin_fix(block)
            print(f"[PinFix] {block}: fix removed — every face admits landings again")
        else:
            print(f"[PinFix] {block}: no fix declared")
        return

    faces, rect, window = 0, -1, None
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
        elif tok in ("layer", "order"):
            print(f"Error: fix_pin: '{tok}' is not supported yet"); return
        else:
            print(f"Error: fix_pin: unknown token '{args[i]}'"); return
        i += 1

    fp = session.fp
    known = fp.has_block(block)
    if known and rect >= _rect_count(fp, block):
        print(f"Error: fix_pin {block}: rect {rect + 1} out of range "
              f"(block has {_rect_count(fp, block)} rect(s))"); return
    fix = {"faces": faces, "rect": rect, "has_window": window is not None,
           "lo": window[0] if window else 0, "hi": window[1] if window else 0}
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
    for sel, sfx in list(sels.items()):
        bad = None
        if sfx["rect"] >= _rect_count(fp, block):
            bad = f"rect {sfx['rect'] + 1} out of range"
        elif fp is session.fp and not _strips_with(session, block, sfx):
            bad = f"'{_declared(sfx)}' admits no landing on this block"
        if bad:
            del sels[sel]
            print(f"Error: fix_pin {block}.{sel[1]}{'*' if sel[0] == 'prefix' else ''}: "
                  f"{bad}; the fix, declared before the block, is dropped")
    if not sels:
        _sel_store(session).pop(block, None)
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


def cmd_fix_pin(session, cmd, args, cmd_line):
    _cmd_fix_pin(session, cmd, args, cmd_line)
    persist(session)


def restore(session):
    """open_bdb: fixes the BDB holds come back under the typed-wins contract (a
    declaration typed in this session, before or after the open, outranks the
    stored one); a previous BDB's restored entries drop first.  Entries typed
    BEFORE the open had no BDB to write to, so they are stored now."""
    if getattr(session, "bdb", None) is None:
        return 0
    sels = _sel_store(session)
    fixes = session._pin_fixes
    for b in list(getattr(session, "_pin_fix_restored_blocks", ())):
        if fixes.pop(b, None) is not None:
            session.fp.clear_pin_fix(b)
    for b, kind, key in list(getattr(session, "_pin_fix_restored_sels", ())):
        sels.get(b, {}).pop((kind, key), None)
        if b in sels and not sels[b]:
            del sels[b]
    session._pin_fix_restored_blocks, session._pin_fix_restored_sels = set(), set()
    raw = session.bdb.meta_get(_META, "")
    n = 0
    if raw:
        try:
            snap = json.loads(raw)
            for b, fx in snap.get("blocks", {}).items():
                if b not in fixes:
                    fixes[b] = fx
                    _apply(session, b, fx)
                    session._pin_fix_restored_blocks.add(b)
                    n += 1
            for b, kind, key, fx in snap.get("selectors", []):
                if (kind, key) not in sels.get(b, {}):
                    sels.setdefault(b, {})[(kind, key)] = fx
                    session._pin_fix_restored_sels.add((b, kind, key))
                    n += 1
        except (ValueError, KeyError, TypeError) as e:
            print(f"Warning: stored pin fixes unreadable ({e}); ignored")
    for b in set(session._pin_fix_restored_blocks) | {k[0] for k in session._pin_fix_restored_sels}:
        revalidate(session, b)
    if n:
        print(f"[PinFix] restored {n} persisted fix declaration(s)")
    persist(session)
    return n


def restamp_bundles(session):
    """After load_pipeline: restored candidates carry no per-bundle map (it is
    not stored per candidate); stamp each from the restored selectors."""
    if not _sel_store(session):
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
