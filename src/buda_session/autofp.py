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

"""`auto_floorplan` — a FIRST hierarchical floorplan from a netlist and a
PDK, so the hier routing flow has geometry to run on before anyone has
drawn a block.

The missing piece of a Verilog-in front end: `import_verilog` gives the
cell tree, the instances and every per-bit pin, and NO geometry (every
component unplaced, every cell 0 x 0); `derive_container_bboxes` sizes a
container from children that were placed by a DEF.  With neither a DEF
nor a floorplan there was nothing to route.  This fills that in, bottom-up
over the cell tree the netlist declares:

  * a LEAF is sized from two floors (`hier_floorplan.size_leaf`): the
    FACE RULE — every face hosts the bits of the pins spread over it at
    the stack's bit pitch, the lesson `tpu_lib.tcl` paid for — and the
    PDK's AREA MODEL (`flow/mockpdk/mock.pdk`: logic by gates per bit of
    port, memory by bits), the larger winning per axis;
  * a CONTAINER's children are PLACED — one packing per cell TYPE, so every
    instance is congruent by construction (what solve-once-copy needs) —
    and its size follows from the packing plus a margin: a slicing beam
    scored on area and the wirelength of the nets among the children
    (`slice_pack`) for a cell with few children, a grid for many, and the
    C++ annealer (`PlacementOptimizer`, the Floorplanner's own engine)
    refining the grid under a MEASURED accept (its rows-legalized result
    is kept only when it scores lower than the grid did);
  * the ROOTS are packed the same way and the die set around them.

Then every instance is STAMPED top-down (one batched write), childless
components marked leaves (a ports-only module is a block, which is what
`import_def_lef` + `import_verilog` conclude for the same module), the
`cell_children` template rows written so the Floorplanner GUI and
`add_inst` read the same offsets, and `FloorplannerEngine.validate()`
audits the result.

What it is NOT: a placer that reads the outside of a block.  The nets a
container's children share with the WORLD (its ports) do not steer the
bottom-up packing — the top-down half of the loop is the route itself,
read back through `report_layer_demand` and the audits, and handed down
as `grow` / `gap` / `cols` on the next call.  The first floorplan is a
starting point the flow measures, not an answer.
"""
from __future__ import annotations

import math
import os
import re

import hier_floorplan as hf
from comp_placement import is_placed


_SLICE_MAX = 8          # 3^n merges — past this the grid + annealer path


def _natural_key(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _port_base(pin_name: str) -> str:
    return re.sub(r"\[\d+\]$", "", pin_name)


class AutoFloorplanMixin:

    def _auto_floorplan(self, pdk_path=None, gap=16, margin=16, place="auto",
                        seed=1, keep=5, wl_weight=0.5, snap=(1, 1),
                        bit_pitch=None, pad=None, aspect_cap=2.0,
                        grow=None, cols=None, sa_iter=0, top_margin=None,
                        util=None, fixed=()):
        """Size and place the whole cell tree of the open BDB.  Returns the
        report dict (`cells`, `die`, `issues`) or None after printing an
        error.  See the module docstring for the rules."""
        import buda
        if self.bdb is None:
            print("Error: auto_floorplan needs an open BDB (open_bdb, then "
                  "import_verilog or add_cell/add_inst)")
            return None
        pdk = None
        if pdk_path:
            if not os.path.isfile(pdk_path):
                print(f"Error: auto_floorplan: PDK file not found: {pdk_path}")
                return None
            try:
                pdk = hf.load_pdk(pdk_path)
            except ValueError as e:
                print(f"Error: auto_floorplan: {e}")
                return None
            if util is not None:
                pdk.util = util
        grow = dict(grow or {})
        cols = dict(cols or {})
        fixed = set(fixed or ())
        if top_margin is None:
            top_margin = margin

        comps = self.bdb.all_components()
        if not comps:
            print("Error: auto_floorplan: the BDB holds no components")
            return None
        by_id = {c.id: c for c in comps}
        kids = {}
        for c in comps:
            kids.setdefault(c.parent_id, []).append(c)
        roots = kids.get(-1, [])
        insts = {}
        for c in comps:
            insts.setdefault(c.cell, []).append(c)
        local = {c.id: c.name.rsplit("/", 1)[-1] for c in comps}

        # ── the templates: one child list per cell, identical at every
        # instance (a netlist's modules are; a hand-built BDB may not be).
        template = {}
        ref = {}
        for cell, lst in insts.items():
            lst.sort(key=lambda c: c.id)
            ref[cell] = lst[0]
            want = sorted((local[k.id], k.cell) for k in kids.get(lst[0].id, []))
            for other in lst[1:]:
                got = sorted((local[k.id], k.cell)
                             for k in kids.get(other.id, []))
                if got != want:
                    print(f"Error: auto_floorplan: cell '{cell}' is not a "
                          f"template — instance '{lst[0].name}' holds "
                          f"{[n for n, _c in want]} and '{other.name}' "
                          f"{[n for n, _c in got]}; a cell is placed once "
                          f"and stamped at every occurrence, so its "
                          f"children must agree")
                    return None
            template[cell] = sorted(want, key=lambda nc: _natural_key(nc[0]))
        for cell in {r.name for r in self.bdb.all_cells()}:
            template.setdefault(cell, [])

        # ── pins: the BUNDLE loads of every component — its pins grouped by
        # the far endpoint they reach at its own level (the sibling the net
        # goes to, or `^` for the world outside the parent) and the
        # direction they go in, which is what the bundler lands on ONE face
        # — and bits per port for the report.  The face floors of a cell are
        # the max over its instances (an edge instance groups differently).
        pins_by_comp = {}
        for p in self.bdb.all_pins():
            pins_by_comp.setdefault(p.comp_id, []).append(p)
        cell_pin_dir = {(r.cell, r.pin_name): r.dir
                        for r in self.bdb.all_cell_pins()}
        net_comps = {}
        for cid, plist in pins_by_comp.items():
            for p in plist:
                net_comps.setdefault(p.net_id, set()).add(cid)
        parent_of = {c.id: c.parent_id for c in comps}

        def at_level(oid, parent):
            """`oid`'s ancestor that is a child of `parent`, else None."""
            x = oid
            while x is not None and x != -1:
                if parent_of.get(x, -1) == parent:
                    return x
                x = parent_of.get(x)
            return None

        port_bits = {}
        face_bits = {}
        ext_bits = {}          # comp id -> bits through its parent's ports
        for cell, lst in insts.items():
            acc = {}
            hb = lb = 0
            for c in lst:
                cnt = {}
                groups = {}
                ext_n = 0
                for p in pins_by_comp.get(c.id, []):
                    b = _port_base(p.pin_name)
                    cnt[b] = cnt.get(b, 0) + 1
                    d = p.dir if p.dir not in ("", "UNKNOWN") else \
                        cell_pin_dir.get((c.cell, p.pin_name), "UNKNOWN")
                    far = []
                    for oid in net_comps.get(p.net_id, ()):
                        if oid == c.id:
                            continue
                        a = at_level(oid, c.parent_id)
                        far.append(local[a] if a is not None and a != c.id
                                   else "^")
                    key = (tuple(sorted(set(far))), d)
                    groups[key] = groups.get(key, 0) + 1
                    if "^" in far:
                        ext_n += 1
                ext_bits[c.id] = ext_n
                for b, n in cnt.items():
                    acc[b] = max(acc.get(b, 0), n)
                h_, l_ = hf.face_bits_pair(groups.values())
                hb, lb = max(hb, h_), max(lb, l_)
            port_bits[cell] = acc
            face_bits[cell] = (hb, lb)

        # where a cell's PORT sits inside it: a leaf's at its centre, a
        # container's where the child carrying it sits (resolved after the
        # container is packed), so the level above places against the
        # point a bus actually leaves the block
        portpos = {}

        def pin_at(k):
            """(dx, dy) of pin row `k` inside the component `k.comp_id`."""
            c = by_id[k.comp_id]
            w, h = size[c.cell]
            return portpos.get(c.cell, {}).get(_port_base(k.pin_name),
                                                (w / 2.0, h / 2.0))

        def nets_among(children):
            """(weight, members) over the nets whose pins land on >= 2 of
            `children` ({comp_id: member name}), each member the pin's
            position inside its block."""
            ends = {}
            for cid, nm in children.items():
                for p in pins_by_comp.get(cid, []):
                    dx, dy = pin_at(p)
                    ends.setdefault(p.net_id, set()).add((nm, dx, dy))
            return hf.aggregate_nets(ends.values())

        def resolve_ports(cell, r, packing):
            """The reference instance's own pins, each at the child that
            carries its net (the mean, for a port fanning out inside)."""
            own = {}
            child_of = {k.id: k for k in kids.get(r.id, [])}
            for p in pins_by_comp.get(r.id, []):
                pts = []
                for q_cid in net_comps.get(p.net_id, ()):
                    k = child_of.get(q_cid)
                    if k is None:
                        continue
                    for q in pins_by_comp.get(k.id, []):
                        if q.net_id != p.net_id:
                            continue
                        dx, dy = pin_at(q)
                        x, y = packing.pos[local[k.id]]
                        pts.append((x + dx, y + dy))
                if pts:
                    base = _port_base(p.pin_name)
                    acc = own.setdefault(base, [0.0, 0.0, 0])
                    acc[0] += sum(pt[0] for pt in pts)
                    acc[1] += sum(pt[1] for pt in pts)
                    acc[2] += len(pts)
            portpos[cell] = {b: (v[0] / v[2], v[1] / v[2])
                             for b, v in own.items()}

        # ── levels, leaves first.
        memo = {}

        def level(cell, stack=()):
            if cell in memo:
                return memo[cell]
            if cell in stack:
                raise ValueError(f"cell '{cell}' contains itself")
            ch = template.get(cell, [])
            lv = 1 if not ch else 1 + max(level(cc, stack + (cell,))
                                          for _n, cc in ch)
            memo[cell] = lv
            return lv

        try:
            for cell in template:
                level(cell)
        except ValueError as e:
            print(f"Error: auto_floorplan: {e}")
            return None
        # a cell with no instance (the top module of a netlist) is nothing
        # to size or place
        order = sorted((c for c in template if insts.get(c)),
                       key=lambda c: (memo[c], c))

        bp = bit_pitch if bit_pitch is not None else \
            (pdk.bit_pitch if pdk else None)
        if bp is None:
            # the stack's own per-bit channel when a pattern is declared,
            # else the mock default — said, since it sizes every face
            from buda_session.util import min_bit_pitch
            bp = min_bit_pitch(self, no_pattern=0.0) or 4.0
            top = [self.layers.eff_bus_width(1, 0.0, lid)
                   for d in (buda.LayerDir.HORIZONTAL, buda.LayerDir.VERTICAL)
                   for lid in self.layers.get_layer_ids_by_dir(d)
                   if self.layers.is_top(lid)]
            top = [p for p in top if p > 0]
            if top:
                bp = max(top)      # the coarsest TOP layer is what a bus lands on
        pd = pad if pad is not None else (pdk.pad if pdk else 24)

        # FIXED cells keep the geometry the BDB already holds — the cell's
        # size from the `cell` table and its children's offsets from the
        # `cell_children` template rows (what `resize_cell` +
        # `add_inst_to_cell` declare) — so a placement the author computed
        # by a rule of its own (a systolic array's rows, feeders and tail,
        # `tpu_lib.tcl`'s) survives the pass untouched and is stamped like
        # every other template.  Every instance's children must be in the
        # rows, and every size positive; said otherwise.
        cell_size = {r.name: (r.width, r.height) for r in self.bdb.all_cells()}
        fixed_pos = {}
        if fixed:
            trows = {}
            for cell in fixed:
                if cell not in template:
                    print(f"Error: auto_floorplan: fixed cell '{cell}' is not "
                          f"in the design")
                    return None
                w, h = cell_size.get(cell, (0.0, 0.0))
                if w <= 0 or h <= 0:
                    print(f"Error: auto_floorplan: fixed cell '{cell}' has no "
                          f"size (resize_cell first)")
                    return None
                if template[cell]:
                    try:
                        rows_ = self.bdb.cell_children_of(cell)
                    except AttributeError:
                        rows_ = None
                    if rows_ is None:
                        rows_ = self._cell_children_rows(cell)
                    have = {nm: (x, y) for nm, _cc, x, y in rows_}
                    missing = [nm for nm, _cc in template[cell] if nm not in have]
                    if missing:
                        print(f"Error: auto_floorplan: fixed cell '{cell}' has "
                              f"no template offset for "
                              f"{', '.join(missing[:6])}"
                              f"{', …' if len(missing) > 6 else ''} "
                              f"(add_inst_to_cell first)")
                        return None
                    fixed_pos[cell] = {nm: (int(round(have[nm][0])),
                                            int(round(have[nm][1])))
                                       for nm, _cc in template[cell]}
                trows[cell] = (int(round(w)), int(round(h)))
            cell_size.update(trows)

        size = {}
        rows = {}
        tpos = {}
        for cell in order:
            ch = template[cell]
            f = float(grow.get(cell, 1.0))
            fb = face_bits.get(cell, (0, 0))
            if cell in fixed:
                size[cell] = cell_size[cell]
                if ch:
                    tpos[cell] = fixed_pos[cell]
                    items = [(nm, size[cc][0], size[cc][1]) for nm, cc in ch]
                    pk = hf.Packing(size[cell][0], size[cell][1],
                                    fixed_pos[cell], "fixed")
                    resolve_ports(cell, ref[cell], pk)
                    bad = [nm for nm, cc in ch
                           if fixed_pos[cell][nm][0] + size[cc][0] > size[cell][0]
                           or fixed_pos[cell][nm][1] + size[cc][1] > size[cell][1]]
                    if bad:
                        print(f"[AutoFP] WARNING: fixed cell '{cell}': "
                              f"{', '.join(bad[:4])} reach outside the cell")
                    r_ = ref[cell]
                    children = {k.id: local[k.id] for k in kids.get(r_.id, [])}
                    nets = nets_among(children)
                    rows[cell] = dict(cell=cell, level=memo[cell],
                                      kind="container",
                                      n_inst=len(insts.get(cell, [])),
                                      n_kids=len(ch), w=size[cell][0],
                                      h=size[cell][1], how="fixed",
                                      note=f"faces {fb[0]}/{fb[1]} bits",
                                      util=hf.utilization(pk, items),
                                      hpwl=hf.packing_hpwl(pk, items, nets),
                                      bits=port_bits.get(cell, {}))
                else:
                    rows[cell] = dict(cell=cell, level=1, kind="leaf",
                                      n_inst=len(insts.get(cell, [])),
                                      n_kids=0, w=size[cell][0],
                                      h=size[cell][1], how="fixed",
                                      note=f"faces {fb[0]}/{fb[1]} bits",
                                      util=None, hpwl=None,
                                      bits=port_bits.get(cell, {}))
                continue
            if not ch:
                ls = hf.size_leaf(pdk, cell, [], bp, pd,
                                  total_bits=sum(port_bits.get(cell, {}).values()),
                                  faces=fb)
                w, h = int(math.ceil(ls.w * f)), int(math.ceil(ls.h * f))
                if snap != (1, 1):
                    w, h = hf._ceil_to(w, snap[0]), hf._ceil_to(h, snap[1])
                size[cell] = (w, h)
                rows[cell] = dict(cell=cell, level=1, kind="leaf",
                                  n_inst=len(insts.get(cell, [])), n_kids=0,
                                  w=w, h=h, how=ls.binding,
                                  note=(f"faces {fb[0]}/{fb[1]} bits, area "
                                        f"side {ls.area_side}, rule "
                                        f"'{ls.rule or '-'}'"),
                                  util=None, hpwl=None, bits=port_bits.get(cell, {}))
                continue
            items = [(nm, size[cc][0], size[cc][1]) for nm, cc in ch]
            r = ref[cell]
            children = {k.id: local[k.id] for k in kids.get(r.id, [])}
            nets = nets_among(children)
            ext = {local[k.id]: ext_bits.get(k.id, 0)
                   for k in kids.get(r.id, [])}
            g, m = int(round(gap * f)), int(round(margin * f))
            floors = hf.face_pair([fb[0], fb[0], fb[1], fb[1]], bp, pd)
            packing = self._pack_children(cell, items, nets, g, m, place,
                                          seed, keep, wl_weight, snap,
                                          aspect_cap, cols.get(cell, 0),
                                          sa_iter, floors,
                                          {nm: cc for nm, cc in ch}, ext)
            size[cell] = (packing.w, packing.h)
            tpos[cell] = packing.pos
            resolve_ports(cell, r, packing)
            rows[cell] = dict(cell=cell, level=memo[cell], kind="container",
                              n_inst=len(insts.get(cell, [])),
                              n_kids=len(ch), w=packing.w, h=packing.h,
                              how=packing.method,
                              note=f"faces {fb[0]}/{fb[1]} bits",
                              util=hf.utilization(packing, items),
                              hpwl=hf.packing_hpwl(packing, items, nets),
                              bits=port_bits.get(cell, {}))

        # ── the top: the roots, and the die around them.
        roots.sort(key=lambda c: _natural_key(c.name))
        items = [(c.name, size[c.cell][0], size[c.cell][1]) for c in roots]
        nets = nets_among({c.id: c.name for c in roots})
        top = self._pack_children("<top>", items, nets, gap, top_margin,
                                  place, seed, keep, wl_weight, snap,
                                  aspect_cap, cols.get("<top>", 0), sa_iter,
                                  (), {c.name: c.cell for c in roots})
        die_w, die_h = top.w, top.h

        # ── write it: cell sizes, template rows, every instance's box.
        for cell, (w, h) in size.items():
            if cell in fixed:
                continue
            self.bdb.resize_cell(cell, float(w), float(h))
        for cell, ch in template.items():
            if cell in fixed or cell not in tpos:
                continue
            for nm, cc in ch:
                x, y = tpos[cell][nm]
                self.bdb.add_inst_to_cell(cell, nm, cc, float(x), float(y))
        boxes = []
        n_leaf = 0
        # Every stamped instance is UPRIGHT: a cell is placed once and its
        # children's offsets are the template's, so the box written here
        # is the cell's own w x h at the instance's origin, which is the
        # geometry of an `N` instance.  A rotated token left on the row
        # (`E`/`W`/`FE`/`FW` from a DEF import -- resize_cell swaps those
        # instances' sides) would then describe a box this write has just
        # replaced (Codex P2 on #973), so the token is reset with the box,
        # and how many turned is said.
        turned = [c.name for c in comps if (c.orient or "N") != "N"]

        def stamp(c, x, y):
            w, h = size[c.cell]
            boxes.append((c.name, float(x), float(y), float(x + w), float(y + h)))
            for k in kids.get(c.id, []):
                lx, ly = tpos[c.cell][local[k.id]]
                stamp(k, x + lx, y + ly)

        for c in roots:
            x, y = top.pos[c.name]
            stamp(c, x, y)
        self.bdb.set_comp_bboxes(boxes)
        stamped = {b[0] for b in boxes}
        turned = [nm for nm in turned if nm in stamped]
        if turned:
            self.bdb.set_comp_orients([(nm, "N") for nm in turned])
            print(f"[auto_floorplan] {len(turned)} rotated instance(s) placed "
                  f"upright (orient reset to N): {', '.join(turned[:6])}"
                  + (" ..." if len(turned) > 6 else ""))
        for c in comps:
            if not kids.get(c.id) and not c.is_leaf:
                self.bdb.set_comp_is_leaf(c.name, True)
                n_leaf += 1
        self.bdb.set_die(float(die_w), float(die_h))
        if hasattr(self, "_die_w"):
            self._die_w, self._die_h = float(die_w), float(die_h)

        issues = self._validate_placement()
        self._print_autofp_report(pdk, bp, pd, gap, margin, rows, order,
                                  top, items, die_w, die_h, n_leaf, issues,
                                  snap)
        out = dict(cells={c: dict(rows[c]) for c in rows},
                   die=(die_w, die_h), top=dict(top.pos),
                   top_method=top.method, issues=issues,
                   bit_pitch=bp, pad=pd)
        self._autofp_last = out
        return out

    def _cell_children_rows(self, cell):
        """[(inst_name, child_cell, x, y)] of a cell's template rows.  The
        BDB binds the parent/child EDGES only, so the rows are read through
        a scratch copy with sqlite3 — a read, never a write."""
        import os
        import sqlite3
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".bdb")
        os.close(fd)
        try:
            self.bdb.save_copy(path)
            con = sqlite3.connect(path)
            try:
                cur = con.execute("SELECT inst_name, child_cell, x, y FROM "
                                  "cell_children WHERE parent_cell=? "
                                  "ORDER BY inst_name", (cell,))
                return [(r[0], r[1], float(r[2]), float(r[3])) for r in cur]
            finally:
                con.close()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    # ── one container's packing ───────────────────────────────────────────

    def _pack_children(self, cell, items, nets, gap, margin, place, seed,
                       keep, wl_weight, snap, aspect_cap, force_cols, sa_iter,
                       floors, kind=None, ext=None):
        import buda
        n = len(items)

        def cost(p):
            return hf.packing_cost(p, items, nets, margin, wl_weight,
                                   floors, snap)

        method = place
        if method == "auto":
            method = "grid" if force_cols else "slice"
        if force_cols and method == "slice":
            method = "grid"
        if method == "slice":
            # the beam over every slicing of up to _SLICE_MAX children;
            # beyond that, clustered by connectivity into groups of that
            # many, each sliced, recursively
            p = hf.cluster_pack(items, nets, gap, margin, keep=keep,
                                wl_weight=wl_weight, aspect_cap=aspect_cap,
                                snap=snap, floors=floors,
                                max_group=_SLICE_MAX, ext=ext)
            return hf.pad_packing(p, floors, snap)
        grid = hf.best_grid(items, gap, margin, nets, wl_weight, floors,
                            snap, force_cols)
        if method == "grid" or n < 2:
            return hf.pad_packing(grid, floors, snap)
        # The annealer: the Floorplanner's own engine (`PlacementOptimizer`),
        # every child bloated by the gap (the GUI's bloat), nets pinned where
        # the ports sit, inside a die a third larger than the grid so it may
        # permute and not only jiggle.  Its cost is `w_wl * hpwl + w_area *
        # bbox/die + w_ovlp * overlap` with the temperature starting at
        # `t_init` — so the weights SCALE each term to order one (a raw
        # wirelength delta in the thousands against t_init 1.0 is a greedy
        # descent from a random start, which is what the first cut measured:
        # rows shifted, accumulators scattered) and the cooling rate is set
        # so the run spans t_init..t_min rather than freezing by iteration
        # two thousand.  Its positions are then legalized and compacted
        # (`compact_pack`) and KEPT only when they score lower.
        die_w = (grid.w - 2 * margin) * 1.33 + gap
        die_h = (grid.h - 2 * margin) * 1.33 + gap
        a_tot = float(sum(w * h for _n, w, h in items)) or 1.0
        total_w = float(sum(w for w, _m in nets)) or 1.0
        wl_scale = total_w * math.sqrt(a_tot / max(1, n))
        opt = buda.PlacementOptimizer(die_w, die_h, 1.0)
        for nm, w, h in items:
            x, y = grid.pos[nm]
            opt.add_block_ex(nm, float(w + gap), float(h + gap),
                             float(x - margin), float(y - margin), 0.0, 0.0,
                             False, False)
        size = {nm: (w, h) for nm, w, h in items}
        for w, members in nets:
            pins = []
            for m in members:
                nm, dx, dy = hf.member(m)
                if nm not in size:
                    continue
                dx = size[nm][0] / 2.0 if dx is None else dx
                dy = size[nm][1] / 2.0 if dy is None else dy
                pins.append((nm, (dx + gap / 2.0, dy + gap / 2.0)))
            if len(pins) >= 2:
                for _k in range(max(1, int(round(w)))):
                    opt.add_net(pins)
        iters = sa_iter if sa_iter > 0 else min(60000, max(10000, 2000 * n))
        t_init, t_min = 1.0, 1e-4
        alpha = (t_min / t_init) ** (1.0 / iters)
        res = opt.run_sa(max_iter=iters, t_init=t_init, t_min=t_min,
                         alpha=alpha, w_wl=wl_weight / wl_scale, w_area=1.0,
                         w_ovlp=20.0 / a_tot, seed=seed)
        origins = {pb.name: (pb.x, pb.y) for pb in res.placements}
        rows_p = hf.compact_pack(items, origins, gap, margin, snap=snap,
                                 kind=kind)
        cg, cr = cost(grid), cost(rows_p)
        if cr < cg:
            rows_p.method = f"sa+compact ({cr:.3f} < grid {cg:.3f})"
            return hf.pad_packing(rows_p, floors, snap)
        grid.method = f"grid ({cg:.3f} <= sa {cr:.3f})"
        return hf.pad_packing(grid, floors, snap)

    # ── the report ────────────────────────────────────────────────────────

    def _print_autofp_report(self, pdk, bp, pd, gap, margin, rows, order,
                             top, top_items, die_w, die_h, n_leaf, issues,
                             snap):
        src = pdk.path if pdk else "none (face rule only)"
        print(f"=== auto_floorplan: PDK {src}; bit pitch {bp:g}, pad {pd}, "
              f"gap {gap}, margin {margin}"
              + (f", snap {snap[0]}x{snap[1]}" if snap != (1, 1) else "")
              + " ===")
        w_cell = max(len(c) for c in rows) if rows else 4
        print(f"  {'cell':<{w_cell}}  lvl  inst  kids  {'size':>11}  "
              f"{'how':<28} {'util':>5}  {'hpwl':>9}")
        for c in order:
            r = rows[c]
            util = f"{100 * r['util']:4.0f}%" if r["util"] is not None else "    -"
            hp = f"{r['hpwl']:9.0f}" if r["hpwl"] is not None else "        -"
            how = r["how"] if r["kind"] == "container" else \
                f"{r['how']} ({r['note']})"
            print(f"  {c:<{w_cell}}  {r['level']:>3}  {r['n_inst']:>4}  "
                  f"{r['n_kids']:>4}  {r['w']:>5}x{r['h']:<5}  {how:<28} "
                  f"{util}  {hp}")
        a = sum(w * h for _n, w, h in top_items)
        util = a / (die_w * die_h) if die_w * die_h > 0 else 0.0
        print(f"  die {die_w}x{die_h}: {len(top_items)} top-level block(s) "
              f"by {top.method}, utilization {util:.3f}; {n_leaf} childless "
              f"component(s) marked leaf")
        kinds = {}
        for k, _a, _b, _m in issues:
            kinds[k] = kinds.get(k, 0) + 1
        if issues:
            print(f"  WARNING: placement audit: " + ", ".join(
                f"{n} {k}" for k, n in sorted(kinds.items())))
            for k, a_, b_, m in issues[:8]:
                print(f"    {k}: {a_} {b_} {m}".rstrip())
        else:
            print("  placement audit: clean (no overlap, nothing outside the die)")
