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

"""The geometry of a FIRST hierarchical floorplan — what `auto_floorplan`
computes, kept free of the compiled extension (the `slot_groups.py` rule)
so the sizing and packing rules are testable on their own and readable
without a build.

Three questions, answered bottom-up over the cell tree a netlist declares:

  1. HOW BIG is a leaf?  Two floors, the larger wins.  The FACE RULE: a
     bus has to land on a face, so every face must host the bits of the
     pins spread over it at the stack's bit pitch (`tpu_lib.tcl`'s lesson —
     a PE narrower than its own psum stranded 672 of 832 bits, and widening
     the channel made it worse).  The AREA MODEL: what the PDK says the
     cell's logic or memory occupies (`parse_pdk`); without it a megabyte
     of cache is the size of its pins.
  2. WHERE do a container's children go?  A packing of the children — the
     slicing enumeration scored on area AND the wirelength of the nets
     among them (`slice_pack`, a beam over Stockmeyer's shape curves), a
     grid (`grid_pack`), or rows legalized from the C++ annealer's
     positions (`row_pack`) — and the container's size follows.
  3. The DIE: the roots, packed the same way, plus a margin.

Everything is INTEGER (a block bbox is one: `Rect` and the Hanan lines),
and a packing is one walk per container so a cell's declared size and
where its children land cannot disagree (`soc_lib.tcl`'s `_pack_geom`
rule).
"""
from __future__ import annotations

import fnmatch
import math
from dataclasses import dataclass, field

# ── the PDK ───────────────────────────────────────────────────────────────


@dataclass
class LeafRule:
    glob: str
    kind: str                    # logic | sram | macro
    gates_per_bit: float = 0.0
    gates_fixed: float = 0.0
    bits: float = 0.0
    w: float = 0.0
    h: float = 0.0
    aspect: float = 1.0


@dataclass
class Pdk:
    """The mock PDK's area model (`flow/mockpdk/mock.pdk`): a few scalars
    and an ordered list of leaf rules, first glob to match a cell wins."""
    unit_um: float = 1.0
    bit_pitch: float = 4.0
    stdcell_area: float = 1.0
    util: float = 0.6
    sram_bit_area: float = 0.05
    sram_periph: float = 1.3
    pad: int = 24
    facepad: int = -1          # -1 = 2 x pad; the light face's floor
    reticle_w: float = 0.0        # microns; 0 = none stated
    reticle_h: float = 0.0
    rules: list = field(default_factory=list)
    path: str = ""

    def rule_for(self, cell: str):
        for r in self.rules:
            if fnmatch.fnmatchcase(cell, r.glob):
                return r
        return None


_SCALARS = {
    "unit_um": float, "bit_pitch": float, "stdcell_area": float,
    "util": float, "sram_bit_area": float, "sram_periph": float,
    "pad": int, "facepad": int, "reticle_w": float, "reticle_h": float,
}


def parse_pdk(text: str, path: str = "") -> Pdk:
    """Read the `key value...` area model.  Loud on anything it does not
    understand: a mistyped key would otherwise size every block from the
    default it silently left in place."""
    pdk = Pdk(path=path)
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        toks = line.split()
        key = toks[0]
        where = f"{path or '<pdk>'}:{lineno}"
        if key in _SCALARS:
            if len(toks) != 2:
                raise ValueError(f"{where}: `{key}` takes one value")
            try:
                v = _SCALARS[key](float(toks[1]))
            except ValueError:
                raise ValueError(f"{where}: `{key}` wants a number, got "
                                 f"'{toks[1]}'") from None
            if v < 0 or (key in ("bit_pitch", "stdcell_area", "util",
                                 "unit_um") and v <= 0):
                raise ValueError(f"{where}: `{key}` must be positive")
            setattr(pdk, key, v)
            continue
        if key != "leaf":
            raise ValueError(f"{where}: unknown key '{key}' (expected one "
                             f"of {', '.join(_SCALARS)}, leaf)")
        if len(toks) < 3:
            raise ValueError(f"{where}: `leaf <glob> <logic|sram|macro> "
                             f"...`")
        rule = LeafRule(glob=toks[1], kind=toks[2])
        if rule.kind not in ("logic", "sram", "macro"):
            raise ValueError(f"{where}: leaf kind must be logic, sram or "
                             f"macro (got '{rule.kind}')")
        opts = toks[3:]
        if len(opts) % 2:
            raise ValueError(f"{where}: leaf options are key value pairs")
        allowed = {"logic": ("gates_per_bit", "gates_fixed", "aspect"),
                   "sram": ("bits", "aspect"),
                   "macro": ("w", "h")}[rule.kind]
        for k, v in zip(opts[::2], opts[1::2]):
            if k not in allowed:
                raise ValueError(f"{where}: leaf {rule.kind} takes "
                                 f"{', '.join(allowed)}; not '{k}'")
            try:
                fv = float(v)
            except ValueError:
                raise ValueError(f"{where}: `{k}` wants a number, got "
                                 f"'{v}'") from None
            if fv < 0 or (k in ("aspect", "w", "h", "bits") and fv <= 0):
                raise ValueError(f"{where}: `{k}` must be positive")
            setattr(rule, k, fv)
        if rule.kind == "macro" and (rule.w <= 0 or rule.h <= 0):
            raise ValueError(f"{where}: leaf macro needs w and h")
        if rule.kind == "sram" and rule.bits <= 0:
            raise ValueError(f"{where}: leaf sram needs bits")
        if rule.aspect < 1.0:
            # an aspect names the long side over the short one; 1/A and A
            # are the same shape, so a value below one is a typo
            raise ValueError(f"{where}: aspect must be >= 1 (the long side "
                             f"over the short one)")
        pdk.rules.append(rule)
    return pdk


def load_pdk(path: str) -> Pdk:
    with open(path, encoding="utf-8") as f:
        return parse_pdk(f.read(), path)


# ── leaf sizing ───────────────────────────────────────────────────────────


def face_bits(port_bits: dict, faces: int = 4) -> int:
    """The heaviest face when the cell's PINS (bits per port) are spread
    over `faces` equal faces heaviest-first onto the lightest — the
    `soc_lib.tcl` FACES rule, read per pin because a pin is ONE place on
    one face and what has to fit there is every bit landing on it."""
    loads = [0] * max(1, faces)
    for b in sorted(port_bits.values(), reverse=True):
        i = loads.index(min(loads))
        loads[i] += b
    return max(loads)


def face_floor(port_bits: dict, bit_pitch: float, pad: int) -> int:
    """The least a leaf's side may be: the heaviest face's bits at the bit
    pitch, plus the pad.  A cell with no pins at all still gets the pad
    (a block has to exist to be placed)."""
    return int(math.ceil(face_bits(port_bits) * bit_pitch)) + int(pad)


def face_bits_pair(loads) -> tuple:
    """(heavy, light) in BITS.  The loads are spread over four faces
    heaviest-first onto the lightest; `heavy` is the heaviest face.
    `light` is the largest SINGLE load the two lighter faces received —
    not their sum: a light face must be able to host its biggest bundle,
    and what else the spread put there may as well ride on the heavy
    faces, which are longer than their own load whenever the block is a
    container.  Summing them sized a row of PEs 312 tall for eight 24-bit
    outputs that physically leave one face together (measured on the TPU
    netlist: the third face read 72 bits where `tpu_lib.tcl` reads 8)."""
    faces = [[0, []] for _ in range(4)]
    for b in sorted(loads, reverse=True):
        i = min(range(4), key=lambda k: faces[k][0])
        faces[i][0] += b
        faces[i][1].append(b)
    faces.sort(key=lambda f: -f[0])
    light = max([b for f in faces[2:] for b in f[1]] or [0])
    return faces[0][0], light


def light_floor(pad: int, facepad: int = -1) -> int:
    """The floor the LIGHT face keeps whatever its bundles ask: the PDK's
    `facepad` when it states one, else two pads (room for a pad at each
    end of a face no bus lands on).  Codex P2 on #973: `facepad` was
    parsed and documented and read by nothing, the floor hard-coded to
    `2 * pad` in two places."""
    return int(facepad) if facepad is not None and facepad >= 0 else 2 * int(pad)


def face_pair(loads, bit_pitch: float, pad: int, facepad: int = -1) -> tuple:
    """(heavy, light): the two floors a block's sides must meet when its
    BUNDLE loads — bits grouped by the far endpoint they go to and the
    direction they go in, which is what the bundler lands on one face —
    are spread over four faces heaviest-first, the two heaviest on one
    OPPOSITE pair.  The side that pair constrains must host the heavier
    of the two; the other side the heavier of the remaining two.  This is
    `tpu_lib.tcl`'s PEW/PEH rule read off a netlist: a PE's north/south
    faces carry psum + weight (32 bits -> 152), its east/west the
    activation (8 -> 56).  Which physical axis is which is the placer's
    business; a block may be used either way round."""
    hb, lb = face_bits_pair(loads)
    heavy = int(math.ceil(hb * bit_pitch)) + int(pad)
    light = int(math.ceil(lb * bit_pitch)) + int(pad)
    return heavy, max(light, light_floor(pad, facepad))


@dataclass
class LeafSize:
    cell: str
    w: int
    h: int
    floor: int            # the face floor
    area_side: int        # the side the area model asked for (0 = none)
    binding: str          # face | area | macro
    rule: str             # the matching glob, or ''


def size_leaf(pdk: Pdk | None, cell: str, loads, bit_pitch: float | None = None,
              pad: int | None = None, total_bits: int | None = None,
              faces: tuple | None = None) -> LeafSize:
    """A leaf's size: the face pair (`face_pair` over the cell's bundle
    `loads`) grown to the PDK's area — uniformly, so the face-implied
    aspect is kept, unless the rule names an aspect — and never below
    the faces: the area model may only make a block BIGGER than its pins
    need.  `w` carries the heavy face, `h` the light one."""
    bp = float(bit_pitch if bit_pitch is not None else
               (pdk.bit_pitch if pdk else 4.0))
    pd = int(pad if pad is not None else (pdk.pad if pdk else 24))
    fp = light_floor(pd, pdk.facepad if pdk else -1)
    loads = list(loads)
    if faces is not None:
        # the heavy/light face bits already taken (the max over a cell's
        # instances, whose loads group differently at each occurrence)
        heavy = int(math.ceil(faces[0] * bp)) + pd
        light = max(int(math.ceil(faces[1] * bp)) + pd, fp)
    else:
        heavy, light = face_pair(loads, bp, pd, fp)
    floor = heavy
    rule = pdk.rule_for(cell) if pdk else None
    if rule is None:
        return LeafSize(cell, heavy, light, floor, 0, "face", "")
    if rule.kind == "macro":
        # a stated size is in microns like every PDK length (the logic and
        # sram areas scale by unit_um^2 below); Codex P1 on #973: it was
        # taken as layout units, 1000x short under `unit_um 1000`
        w = max(int(math.ceil(rule.w * pdk.unit_um)), heavy)
        h = max(int(math.ceil(rule.h * pdk.unit_um)), light)
        return LeafSize(cell, w, h, floor, 0, "macro", rule.glob)
    if rule.kind == "sram":
        area = rule.bits * pdk.sram_bit_area * pdk.sram_periph
    else:
        bits = total_bits if total_bits is not None else sum(loads)
        area = (rule.gates_fixed + rule.gates_per_bit * bits) * \
            pdk.stdcell_area / pdk.util
    area *= pdk.unit_um * pdk.unit_um
    side = int(math.ceil(math.sqrt(max(area, 0.0))))
    w, h = heavy, light
    if rule.aspect > 1.0 + 1e-9:
        long_ = int(math.ceil(math.sqrt(area * rule.aspect)))
        short = int(math.ceil(area / long_)) if long_ > 0 else 0
        w, h = max(long_, heavy), max(short, light)
    elif w * h < area:
        # area-bound: as square as the faces allow (a memory macro with
        # one port pair is not a 3:1 sliver because its light face is
        # empty), each side still floored by its face
        w = max(heavy, side)
        h = max(light, int(math.ceil(area / w)))
    binding = "area" if w * h > heavy * light else "face"
    return LeafSize(cell, w, h, floor, side, binding, rule.glob)


# ── wirelength ────────────────────────────────────────────────────────────


def member(m):
    """A net member is `name` (the pin at the block's centre) or
    `(name, dx, dy)` (the pin `dx, dy` inside the block — where the child
    that carries a container's port actually sits, resolved bottom-up, so
    a parent's placement sees where a bus LEAVES a block rather than its
    middle: on the TPU the eight psum outputs of a row leave at eight
    different columns, and centred they pulled every accumulator into one
    clump)."""
    if isinstance(m, tuple):
        return m
    return (m, None, None)


def aggregate_nets(endpoint_sets) -> list:
    """Nets as (weight, members): every net whose members are the same set
    is ONE weighted net, so a 32-bit bus between two children scores
    thirty-two times once rather than thirty-two times.  Members are
    `(name, dx, dy)` (see `member`); a net on fewer than two distinct
    blocks is dropped."""
    acc = {}
    for members in endpoint_sets:
        key = tuple(sorted(set(member(m) for m in members),
                           key=lambda t: (t[0], t[1] or 0, t[2] or 0)))
        if len({t[0] for t in key}) < 2:
            continue
        acc[key] = acc.get(key, 0) + 1
    return [(w, list(k)) for k, w in sorted(acc.items())]


def pin_xy(m, pos: dict, sizes: dict):
    """A member's pin position in the packing: its block's origin plus the
    pin offset, the centre when none is given."""
    name, dx, dy = member(m)
    x, y = pos[name]
    w, h = sizes[name]
    return (x + (dx if dx is not None else w / 2.0),
            y + (dy if dy is not None else h / 2.0))


def hpwl(nets, pos: dict, sizes: dict) -> float:
    """Σ weight × half-perimeter of the members' pins (members outside this
    packing are ignored)."""
    total = 0.0
    for w, members in nets:
        pts = [pin_xy(m, pos, sizes) for m in members if member(m)[0] in pos]
        if len({member(m)[0] for m in members if member(m)[0] in pos}) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        total += w * ((max(xs) - min(xs)) + (max(ys) - min(ys)))
    return total


# ── packers ───────────────────────────────────────────────────────────────
# Every packer takes `items` = [(name, w, h)] in a fixed order and returns
# Packing(W, H, {name: (x, y)}) with the children INSIDE the margin —
# (M, M) is the least origin — so the container's declared size and where
# its children land are one computation.


@dataclass
class Packing:
    w: int
    h: int
    pos: dict
    method: str = ""
    hpwl: float = 0.0


def _ceil_to(v: float, q: int) -> int:
    v = int(math.ceil(v - 1e-9))
    if q <= 1:
        return v
    return int(math.ceil(v / q)) * q


def grid_pack(items, gap: int, margin: int, cols: int = 0,
              snap: tuple = (1, 1), align_low: bool = False) -> Packing:
    """A roughly square grid (`ceil(sqrt(n))` columns, row-major), column
    widths and row heights the max of what lands in them, each child
    centred in its cell — `soc_lib.tcl`'s `_pack_geom`.  `cols` forces the
    column count (a systolic ROW wants n columns).  Under `snap` every
    origin is a multiple of the period (children left/bottom aligned, the
    pitch rounded up), so every instance of the cell sees the same track
    phase wherever its parent puts it — if the parent snaps too."""
    n = len(items)
    if n == 0:
        return Packing(2 * margin, 2 * margin, {}, "grid")
    nc = cols if cols > 0 else int(math.ceil(math.sqrt(n)))
    nc = max(1, min(nc, n))
    nr = int(math.ceil(n / nc))
    sx, sy = max(1, int(snap[0])), max(1, int(snap[1]))
    cw = [0] * nc
    rh = [0] * nr
    for i, (_name, w, h) in enumerate(items):
        c, r = i % nc, i // nc
        cw[c] = max(cw[c], w)
        rh[r] = max(rh[r], h)
    if sx > 1 or sy > 1:
        cw = [_ceil_to(v, sx) for v in cw]
        rh = [_ceil_to(v, sy) for v in rh]
        gx, gy, mx, my = _ceil_to(gap, sx), _ceil_to(gap, sy), \
            _ceil_to(margin, sx), _ceil_to(margin, sy)
        align_low = True
    else:
        gx = gy = gap
        mx = my = margin
    xoff = []
    x = mx
    for c in range(nc):
        xoff.append(x)
        x += cw[c] + gx
    yoff = []
    y = my
    for r in range(nr):
        yoff.append(y)
        y += rh[r] + gy
    W = x - gx + mx
    H = y - gy + my
    pos = {}
    for i, (name, w, h) in enumerate(items):
        c, r = i % nc, i // nc
        dx = 0 if align_low else (cw[c] - w) // 2
        dy = 0 if align_low else (rh[r] - h) // 2
        pos[name] = (xoff[c] + dx, yoff[r] + dy)
    return Packing(int(W), int(H), pos, "grid")


def compact_pack(items, origins: dict, gap: int, margin: int,
                 snap: tuple = (1, 1), kind: dict | None = None) -> Packing:
    """A set of positions (the annealer's) LEGALIZED and COMPACTED with
    their relative order kept: every child grown by the gap, overlaps
    shoved apart left to right, then the whole set compacted down and
    left along the constraint graph those positions define (a block moves
    down to the top of whatever it overlaps in x, left to the right edge
    of whatever it overlaps in y, in order) — overlap-free by
    construction and as tight as the order allows.

    Then ALIGNED: instances of one cell (`kind`: name -> cell) that stack
    — overlap in x — take one x, the rightmost of the group, kept only
    where the shift lands on nothing (the compaction had left the rows of
    the TPU alternating by the width of the feeders beside them, so every
    straight psum bus between two rows jogged through a 16-unit gap);
    the stack is compacted down again afterwards, never left."""
    n = len(items)
    if n == 0:
        return Packing(2 * margin, 2 * margin, {}, "compact")
    sx, sy = max(1, int(snap[0])), max(1, int(snap[1]))
    snapping = sx > 1 or sy > 1
    gx, gy, mx, my = (_ceil_to(gap, sx), _ceil_to(gap, sy),
                      _ceil_to(margin, sx), _ceil_to(margin, sy)) \
        if snapping else (gap, gap, margin, margin)
    bw = {nm: (_ceil_to(w, sx) if snapping else w) + gx for nm, w, _h in items}
    bh = {nm: (_ceil_to(h, sy) if snapping else h) + gy for nm, _w, h in items}
    x = {nm: float(origins[nm][0]) for nm, _w, _h in items}
    y = {nm: float(origins[nm][1]) for nm, _w, _h in items}

    def yov(a, b):
        return y[a] < y[b] + bh[b] and y[b] < y[a] + bh[a]

    def xov(a, b):
        return x[a] < x[b] + bw[b] and x[b] < x[a] + bw[a]

    # shove apart along x in the annealer's x order
    done = []
    for nm in sorted(x, key=lambda k: (x[k], y[k], k)):
        lim = x[nm]
        for o in done:
            if yov(nm, o):
                lim = max(lim, x[o] + bw[o])
        x[nm] = lim
        done.append(nm)
    for _pass in range(3):
        done = []
        for nm in sorted(y, key=lambda k: (y[k], x[k], k)):
            lim = 0.0
            for o in done:
                if xov(nm, o):
                    lim = max(lim, y[o] + bh[o])
            y[nm] = lim
            done.append(nm)
        done = []
        for nm in sorted(x, key=lambda k: (x[k], y[k], k)):
            lim = 0.0
            for o in done:
                if yov(nm, o):
                    lim = max(lim, x[o] + bw[o])
            x[nm] = lim
            done.append(nm)
    if kind:
        by_cell = {}
        for nm, _w, _h in items:
            by_cell.setdefault(kind.get(nm), []).append(nm)
        for cell, names in by_cell.items():
            if cell is None or len(names) < 2:
                continue
            # union the instances that overlap in x into stacks
            parent = {nm: nm for nm in names}

            def find(a):
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a

            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    if xov(a, b):
                        parent[find(a)] = find(b)
            stacks = {}
            for nm in names:
                stacks.setdefault(find(nm), []).append(nm)
            for members in stacks.values():
                if len(members) < 2:
                    continue
                target = max(x[nm] for nm in members)
                # each instance on its own: the one with a block to its
                # right stays, the rest still line up
                for nm in sorted(members, key=lambda k: (y[k], k)):
                    if x[nm] == target:
                        continue
                    old = x[nm]
                    x[nm] = target
                    if any(xov(nm, o) and yov(nm, o) for o in x if o != nm):
                        x[nm] = old
        done = []
        for nm in sorted(y, key=lambda k: (y[k], x[k], k)):
            lim = 0.0
            for o in done:
                if xov(nm, o):
                    lim = max(lim, y[o] + bh[o])
            y[nm] = lim
            done.append(nm)
    pos = {}
    W = H = 0
    for nm, _w, _h in items:
        px = _ceil_to(x[nm], sx) if snapping else int(round(x[nm]))
        py = _ceil_to(y[nm], sy) if snapping else int(round(y[nm]))
        pos[nm] = (px + mx, py + my)
        W = max(W, px + bw[nm] - gx)
        H = max(H, py + bh[nm] - gy)
    return Packing(int(W + 2 * mx), int(H + 2 * my), pos, "compact")


def _pareto(entries, keep: int, lam: float, a_tot: float, wl_scale: float):
    """Thin a subset's entries to `keep`, ranked on the one COST the whole
    search uses: bounding area over the children's own area, plus λ times
    the internal wirelength in units of (bits × a block side) — so a net
    stretched by one block side costs as much as λ × 100 % more area.
    Pareto-dominated (w, h, hpwl) entries go first, so the kept set spans
    the shape curve rather than one corner of it."""
    def cost(e):
        return e[0] * e[1] / a_tot + lam * e[4] / wl_scale
    entries.sort(key=lambda e: (cost(e), e[0], e[1]))
    out = []
    for e in entries:
        dominated = False
        for o in out:
            if o[0] <= e[0] and o[1] <= e[1] and o[4] <= e[4]:
                dominated = True
                break
        if not dominated:
            out.append(e)
        if len(out) >= keep:
            break
    # The two EXTREME shapes ride along whatever their cost: the widest
    # (a line of the children) and the tallest.  A container's own face
    # may demand exactly that shape (a row of PEs carrying 256 bits across
    # its north face), and the floors are judged only at the full set — a
    # beam ranked on squareness alone had thrown the line away at the
    # first merge, and the row came back padded to 38 % utilisation.
    if entries:
        for ext in (max(entries, key=lambda e: (e[0], -e[1])),
                    max(entries, key=lambda e: (e[1], -e[0]))):
            if not any(o[0] == ext[0] and o[1] == ext[1] and o[4] == ext[4]
                       for o in out):
                out.append(ext)
    return out


def slice_pack(items, gap: int, margin: int, nets=(), keep: int = 5,
               wl_weight: float = 0.5, aspect_cap: float = 2.0,
               snap: tuple = (1, 1), floors: tuple = (),
               ext: dict | None = None) -> Packing:
    """The best SLICING floorplan of the children, by a beam over every
    way of cutting the set in two (H: side by side, V: stacked),
    recursively — Stockmeyer's shape-curve composition, with each subset
    keeping `keep` arrangements ranked on area AND the wirelength of the
    nets whose members all lie inside it (`_pareto`).  The container's
    aspect is held to `aspect_cap`.  3^n merges, so the caller sends
    at most ~8 children here and the rest to `grid_pack`/`row_pack`.

    Each subtree is centred across its cut (a short child does not sit on
    its parent's face); under `snap` it is low-aligned instead and every
    dimension rounded up to the period, which keeps every origin on it.

    `ext` ({child: bits}) is the BOUNDARY PULL — the bits each child
    exchanges with the world outside the container, through its ports.
    Where those ports will sit is the level above's business, but a child
    carrying them belongs at an EDGE, not between its siblings: the pull
    charges each such child's bits times its pin's distance to the nearest
    edge of the packing it is in (at every merge, the subset's own edge —
    a child on the edge of its group tends to end on the edge of the
    whole), in the same wirelength units as the nets.  Measured on the
    SoC: without it the L3 controller, carrying four 32-bit links out of
    its slice, was packed between the bank rows and every link crossed a
    bank."""
    n = len(items)
    if n == 0:
        return Packing(2 * margin, 2 * margin, {}, "slice")
    sx, sy = max(1, int(snap[0])), max(1, int(snap[1]))
    snapping = sx > 1 or sy > 1
    gx, gy, mx, my = (_ceil_to(gap, sx), _ceil_to(gap, sy),
                      _ceil_to(margin, sx), _ceil_to(margin, sy)) \
        if snapping else (gap, gap, margin, margin)
    names = [it[0] for it in items]
    size = {}
    for nm, w, h in items:
        size[nm] = (_ceil_to(w, sx), _ceil_to(h, sy)) if snapping else (w, h)
    a_tot = float(sum(w * h for w, h in size.values())) or 1.0
    total_w = float(sum(w for w, _m in nets)) or 1.0
    wl_scale = total_w * math.sqrt(a_tot / n)
    idx = {nm: i for i, nm in enumerate(names)}
    # nets by the mask of their members; a net scores once its members are
    # all inside one subset, at the merge that first unites them
    net_mask = []
    for w, members in nets:
        m = 0
        norm = []
        for mem in members:
            nm0, dx, dy = member(mem)
            if nm0 in idx:
                m |= 1 << idx[nm0]
                norm.append((nm0,
                             size[nm0][0] / 2.0 if dx is None else dx,
                             size[nm0][1] / 2.0 if dy is None else dy))
        if m and (m & (m - 1)):
            net_mask.append((m, w, norm))
    full = (1 << n) - 1
    ext = {k: v for k, v in (ext or {}).items() if k in size and v > 0}

    def pull(pos, w, h):
        tot = 0.0
        for nm, bits in ext.items():
            if nm not in pos:
                continue
            cx = pos[nm][0] + size[nm][0] / 2.0
            cy = pos[nm][1] + size[nm][1] / 2.0
            tot += bits * max(0.0, min(cx, w - cx, cy, h - cy))
        return tot

    # entry: (w, h, pos{name: (x, y)}, hpwl_internal, hpwl_scored)
    F = {}
    for i, nm in enumerate(names):
        w, h = size[nm]
        F[1 << i] = [(w, h, {nm: (0, 0)}, 0.0, 0.0)]
    for m in range(1, full + 1):
        if m in F:
            continue
        low = m & -m
        spanning = [(w, mem) for nm_, w, mem in net_mask
                    if (nm_ & m) == nm_]
        cand = []
        a = (m - 1) & m
        while a > 0:
            if a & low:
                b = m ^ a
                for pa in F[a]:
                    for pb in F[b]:
                        wa, ha, posa, hpa, _sa = pa
                        wb, hb, posb, hpb, _sb = pb
                        # H: a left of b
                        w = wa + gx + wb
                        h = max(ha, hb)
                        pos = {}
                        dya = 0 if snapping else (h - ha) // 2
                        dyb = 0 if snapping else (h - hb) // 2
                        for nm, (x, y) in posa.items():
                            pos[nm] = (x, y + dya)
                        for nm, (x, y) in posb.items():
                            pos[nm] = (x + wa + gx, y + dyb)
                        hp = hpa + hpb + _span_hpwl(spanning, pos, size, a, b, idx)
                        cand.append((w, h, pos, hp, hp + pull(pos, w, h)))
                        # V: a below b
                        w = max(wa, wb)
                        h = ha + gy + hb
                        pos = {}
                        dxa = 0 if snapping else (w - wa) // 2
                        dxb = 0 if snapping else (w - wb) // 2
                        for nm, (x, y) in posa.items():
                            pos[nm] = (x + dxa, y)
                        for nm, (x, y) in posb.items():
                            pos[nm] = (x + dxb, y + ha + gy)
                        hp = hpa + hpb + _span_hpwl(spanning, pos, size, a, b, idx)
                        cand.append((w, h, pos, hp, hp + pull(pos, w, h)))
            a = (a - 1) & m
        if m == full:
            # The container's own faces rank the PADDED shape (a packing
            # that already spans its heavy face beats one padded out to
            # it), and the ASPECT CAP is a preference, not a filter: the
            # squarest-enough packing is taken only when it costs at most
            # 15 % more than the best of all — a row of PEs whose north
            # face carries 256 bits IS a 13:1 line, and holding it to 2:1
            # padded it to 57 % utilisation (measured on the TPU netlist).
            def padded(e):
                return padded_dims(e[0] + 2 * mx, e[1] + 2 * my, floors, snap) \
                    if floors else (e[0] + 2 * mx, e[1] + 2 * my)
            scored = [(padded(e)[0] - 2 * mx, padded(e)[1] - 2 * my,
                       e[2], e[3], e[4], e[0], e[1]) for e in cand]
            ranked = _pareto(scored, max(keep, len(scored)), wl_weight,
                             a_tot, wl_scale)
            best_cost = ranked[0][0] * ranked[0][1] / a_tot + \
                wl_weight * ranked[0][4] / wl_scale
            pick = ranked[0]
            if aspect_cap > 0:
                for e in ranked:
                    W, H = e[0] + 2 * mx, e[1] + 2 * my
                    c = e[0] * e[1] / a_tot + wl_weight * e[4] / wl_scale
                    if c > 1.15 * best_cost:
                        break
                    if max(W, H) <= aspect_cap * min(W, H):
                        pick = e
                        break
            F[m] = [(pick[5], pick[6], pick[2], pick[3], pick[4])]
            continue
        F[m] = _pareto(cand, keep, wl_weight, a_tot, wl_scale)
    w, h, pos, hp, _sc = F[full][0]
    out = {nm: (x + mx, y + my) for nm, (x, y) in pos.items()}
    return Packing(int(w + 2 * mx), int(h + 2 * my), out, "slice", hp)


def _span_hpwl(spanning, pos, size, a, b, idx):
    """HPWL of the nets that straddle the two halves just merged — the
    ones whose members are not all in `a` nor all in `b`."""
    total = 0.0
    # members are normalized (name, dx, dy) tuples here — the hot loop
    for w, members in spanning:
        ma = mb = False
        for nm, _dx, _dy in members:
            bit = 1 << idx[nm]
            if bit & a:
                ma = True
            elif bit & b:
                mb = True
        if not (ma and mb):
            continue
        xs = [pos[nm][0] + dx for nm, dx, _dy in members]
        ys = [pos[nm][1] + dy for nm, _dx, dy in members]
        total += w * ((max(xs) - min(xs)) + (max(ys) - min(ys)))
    return total


def padded_dims(w: int, h: int, floors: tuple, snap: tuple = (1, 1)) -> tuple:
    """The container's dims once its own faces are honoured: the heavy
    floor on whichever axis needs the less padding, the light one on the
    other.  (w, h) when the floors are met either way round."""
    if not floors:
        return w, h
    a, b = floors
    sx, sy = max(1, int(snap[0])), max(1, int(snap[1]))
    c1 = (max(w, _ceil_to(a, sx)), max(h, _ceil_to(b, sy)))
    c2 = (max(w, _ceil_to(b, sx)), max(h, _ceil_to(a, sy)))
    return min((c1, c2), key=lambda d: (d[0] * d[1], d))


def pad_packing(p: Packing, floors: tuple, snap: tuple = (1, 1)) -> Packing:
    """Grow a packing to its face floors, children centred in the room
    that adds (origins kept on the snap grid)."""
    W, H = padded_dims(p.w, p.h, floors, snap)
    if (W, H) == (p.w, p.h):
        return p
    sx, sy = max(1, int(snap[0])), max(1, int(snap[1]))
    dx = ((W - p.w) // 2) // sx * sx
    dy = ((H - p.h) // 2) // sy * sy
    pos = {n: (x + dx, y + dy) for n, (x, y) in p.pos.items()}
    return Packing(W, H, pos, p.method + "+pad", p.hpwl)


def packing_cost(p: Packing, items, nets, margin: int, wl_weight: float,
                 floors: tuple = (), snap: tuple = (1, 1)) -> float:
    """The ONE cost every packer is judged by: the container's area (its
    face floors honoured) over the children's own area, plus λ × the
    nets' half-perimeter wirelength in units of (bits × the mean block
    side) — so a net stretched by one block side costs λ × 100 % more
    area."""
    n = max(1, len(items))
    a_tot = float(sum(w * h for _n, w, h in items)) or 1.0
    total_w = float(sum(w for w, _m in nets)) or 1.0
    wl_scale = total_w * math.sqrt(a_tot / n)
    W, H = padded_dims(p.w, p.h, floors, snap)
    return W * H / a_tot + wl_weight * packing_hpwl(p, items, nets) / wl_scale


def best_grid(items, gap: int, margin: int, nets=(), wl_weight: float = 0.5,
              floors: tuple = (), snap: tuple = (1, 1), cols: int = 0) -> Packing:
    """The grid packing with the best `packing_cost` over every column
    count (or the one `cols` names)."""
    n = len(items)
    counts = [cols] if cols > 0 else range(1, n + 1)
    best = None
    for nc in counts:
        p = grid_pack(items, gap, margin, cols=nc, snap=snap)
        c = packing_cost(p, items, nets, margin, wl_weight, floors, snap)
        if best is None or c < best[0] - 1e-9:
            best = (c, p)
    return best[1]


def cluster_pack(items, nets, gap: int, margin: int, keep: int = 5,
                 wl_weight: float = 0.5, aspect_cap: float = 2.0,
                 snap: tuple = (1, 1), floors: tuple = (), max_group: int = 8,
                 _depth: int = 0, ext: dict | None = None) -> Packing:
    """Many children: CLUSTER them by connectivity into groups of at most
    `max_group`, slice-pack each group, and pack the groups — recursively,
    a group of groups when there are still too many.  Agglomerative:
    the pair of clusters with the most bits between them per member
    merges first (small clusters first, so a chain of equals pairs up
    before it absorbs a stranger), a merge refused once it would exceed
    `max_group`; when nothing connected is left to merge and the count is
    still over the limit, the SMALLEST clusters pair up by area (blocks
    that talk to nothing can sit together).  Every group's pins stay
    resolved to the member that carries them, so the level above places
    a group by where its buses leave it.  Deterministic, no annealer."""
    n = len(items)
    ext = dict(ext or {})
    if n <= max_group:
        return slice_pack(items, gap, margin, nets, keep=keep,
                          wl_weight=wl_weight, aspect_cap=aspect_cap,
                          snap=snap, floors=floors, ext=ext)
    names = [it[0] for it in items]
    size = {nm: (w, h) for nm, w, h in items}
    cl = {nm: [nm] for nm in names}            # cluster id -> members
    of = {nm: nm for nm in names}              # member -> cluster id
    # bits between members
    link = {}
    for w, members in nets:
        ms = sorted({member(m)[0] for m in members if member(m)[0] in size})
        for i, a in enumerate(ms):
            for b in ms[i + 1:]:
                link[(a, b)] = link.get((a, b), 0.0) + w

    def weight(ca, cb):
        tot = 0.0
        for a in cl[ca]:
            for b in cl[cb]:
                tot += link.get((a, b) if a < b else (b, a), 0.0)
        return tot

    while len(cl) > max_group:
        best = None
        ids = sorted(cl)
        for i, ca in enumerate(ids):
            for cb in ids[i + 1:]:
                if len(cl[ca]) + len(cl[cb]) > max_group:
                    continue
                wgt = weight(ca, cb)
                if wgt <= 0:
                    continue
                key = (wgt / (len(cl[ca]) + len(cl[cb])), -len(cl[ca]) - len(cl[cb]), ca, cb)
                if best is None or key > best[0]:
                    best = (key, ca, cb)
        if best is None:
            # nothing connected: the two smallest by area, if they fit
            def area(c):
                return sum(size[m][0] * size[m][1] for m in cl[c])
            cand = sorted(ids, key=lambda c: (area(c), c))
            merged = False
            for i, ca in enumerate(cand):
                for cb in cand[i + 1:]:
                    if len(cl[ca]) + len(cl[cb]) <= max_group:
                        best = (None, ca, cb)
                        merged = True
                        break
                if merged:
                    break
            if not merged:
                break
        _k, ca, cb = best
        cl[ca] = cl[ca] + cl[cb]
        for m in cl[cb]:
            of[m] = ca
        del cl[cb]
    # pack every multi-member cluster (no margin: it is a group, not a cell)
    gitems = []
    gpos = {}
    for cid in sorted(cl):
        mem = cl[cid]
        if len(mem) == 1:
            gitems.append((cid, size[cid][0], size[cid][1]))
            gpos[cid] = {cid: (0, 0)}
            continue
        sub_items = [(m, size[m][0], size[m][1]) for m in mem]
        sub_nets = [(w, [mm for mm in members if member(mm)[0] in cl_set])
                    for w, members in nets
                    for cl_set in ({x for x in mem},)
                    if len({member(mm)[0] for mm in members if member(mm)[0] in cl_set}) >= 2]
        sub = cluster_pack(sub_items, sub_nets, gap, 0, keep, wl_weight,
                           aspect_cap, snap, (), max_group, _depth + 1,
                           {m: ext[m] for m in mem if m in ext})
        gitems.append((cid, sub.w, sub.h))
        gpos[cid] = sub.pos
    # the nets between groups, pins resolved through the member positions
    gnets = []
    for w, members in nets:
        ends = []
        for m in members:
            nm, dx, dy = member(m)
            if nm not in size:
                continue
            cid = of[nm]
            px, py = gpos[cid][nm]
            dx = size[nm][0] / 2.0 if dx is None else dx
            dy = size[nm][1] / 2.0 if dy is None else dy
            ends.append((cid, px + dx, py + dy))
        if len({e[0] for e in ends}) >= 2:
            gnets.append((w, ends))
    gnets = [(sum(w for w, _e in grp), e) for e, grp in
             _group_nets(gnets).items()]
    gext = {}
    for m, bits in ext.items():
        gext[of[m]] = gext.get(of[m], 0) + bits
    top = cluster_pack(gitems, gnets, gap, margin, keep, wl_weight,
                       aspect_cap, snap, floors, max_group, _depth + 1, gext)
    pos = {}
    for cid, (gx_, gy_) in top.pos.items():
        for m, (px, py) in gpos[cid].items():
            pos[m] = (gx_ + px, gy_ + py)
    return Packing(top.w, top.h, pos, f"cluster({len(cl)})" if _depth == 0
                   else top.method, top.hpwl)


def _group_nets(gnets):
    out = {}
    for w, ends in gnets:
        key = tuple(sorted(set(ends)))
        out.setdefault(key, []).append((w, ends))
    return {k: v for k, v in out.items()}


def packing_hpwl(packing: Packing, items, nets) -> float:
    sizes = {nm: (w, h) for nm, w, h in items}
    return hpwl(nets, packing.pos, sizes)


def utilization(packing: Packing, items) -> float:
    a = sum(w * h for _n, w, h in items)
    d = packing.w * packing.h
    return a / d if d > 0 else 0.0
