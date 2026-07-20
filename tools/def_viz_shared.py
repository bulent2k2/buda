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

"""
Shared data model, persistence, and canvas drawing utilities for def_viz prototypes.
No tkinter imports — pure data + matplotlib.
"""

import os
import re
import sys

import matplotlib.patches as mpatches

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from def_cluster import parse_def, parse_lef
from group_tree import GroupTree, lighten_color

# Try to import the fast C++ BDB module
try:
    import buda as _buda_mod
    _BDB_AVAILABLE = True
except ImportError:
    _buda_mod = None
    _BDB_AVAILABLE = False


def bdb_is_fresh(def_path: str) -> bool:
    """Return True if a up-to-date .bdb already exists for def_path (no LEF needed)."""
    if not _BDB_AVAILABLE:
        return False
    db_path = _buda_mod.BDB.db_path(def_path)
    return (os.path.exists(db_path) and
            os.path.getmtime(db_path) >= os.path.getmtime(def_path))

# ── Palette ───────────────────────────────────────────────────────────────────
_C_DRIVER   = '#FF8C66'
_C_RECEIVER = '#66AAFF'
_C_BOTH     = '#CC88FF'
_C_EDGE     = '#444444'
_C_DIE_FILL = '#f6f6f6'
_C_DIE_EDGE = '#999999'


# ── Parsing ───────────────────────────────────────────────────────────────────

def infer_cell_sizes_from_def(def_path: str) -> dict:
    """Return {cell_type: (w_um, h_um)} by finding minimum placement spacing per type.

    Works for macros-only DEFs where the same cell type is placed on a regular grid.
    For a cell type with only one instance, falls back to 0.5 × 0.5 µm.
    """
    by_type: dict = {}
    units = 1000
    comp_re = re.compile(
        r'-\s+\S+\s+(\S+)\s+\+\s+(?:PLACED|FIXED)\s+\(\s*(\d+)\s+(\d+)\s*\)')
    with open(def_path) as f:
        in_comp = False
        for line in f:
            s = line.strip()
            if 'UNITS DISTANCE MICRONS' in s:
                m = re.search(r'MICRONS\s+(\d+)', s)
                if m:
                    units = int(m.group(1))
            elif s.startswith('COMPONENTS'):
                in_comp = True
            elif 'END COMPONENTS' in s:
                break
            elif in_comp:
                m = comp_re.search(s)
                if m:
                    cell = m.group(1)
                    by_type.setdefault(cell, []).append(
                        (int(m.group(2)), int(m.group(3))))
    sizes = {}
    for cell, positions in by_type.items():
        xs = sorted(set(p[0] for p in positions))
        ys = sorted(set(p[1] for p in positions))
        dxs = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        dys = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
        w = min(dxs) / units if dxs else 0.5
        h = min(dys) / units if dys else 0.5
        sizes[cell] = (w, h)
    return sizes


def _synthetic_lef(cell_sizes: dict) -> str:
    """Build a minimal LEF string from a {cell: (w, h)} dict."""
    lines = ['VERSION 5.8 ;']
    for cell, (w, h) in cell_sizes.items():
        lines += [f'MACRO {cell}', f'  SIZE {w:.4f} BY {h:.4f} ;', f'END {cell}']
    lines.append('END LIBRARY')
    return '\n'.join(lines)


def parse_cell_sizes(lef_path: str) -> dict:
    with open(lef_path) as f:
        content = f.read()
    sizes = {}
    for m in re.finditer(
            r'MACRO\s+(\S+).*?SIZE\s+([\d.]+)\s+BY\s+([\d.]+)\s*;',
            content, re.DOTALL):
        sizes[m.group(1)] = (float(m.group(2)), float(m.group(3)))
    return sizes


# ── Pure data state ───────────────────────────────────────────────────────────

class DefVizData:
    """All parsed DEF/LEF state + group tree. No UI dependencies."""

    def __init__(self):
        self.def_path   = ''
        self.units      = 1000
        self.die        = (0.0, 0.0)
        self.inst_info  = {}   # inst -> {x1,y1,x2,y2,cell}
        self.net_insts  = {}   # net_name -> set(inst_names)
        self.inst_nets  = {}   # inst_name -> set(net_names)
        self.inst_roles = {}   # (net_name, inst_name) -> 'driver'|'receiver'
        self.all_nets   = []   # sorted list
        self.groups     = GroupTree()
        self.bdb        = None  # BDB object when available

    def load(self, def_path: str, lef_path: str) -> str:
        """Parse DEF+LEF, rebuild indexes, auto-load group sidecar. Returns status string."""
        self.def_path   = def_path
        self.inst_info  = {}
        self.net_insts  = {}
        self.inst_nets  = {}
        self.inst_roles = {}
        self.bdb        = None

        if _BDB_AVAILABLE:
            self._load_via_bdb(def_path, lef_path)
        else:
            self._load_via_python(def_path, lef_path)

        self.groups = GroupTree()
        sc = GroupTree.sidecar_path(def_path)
        if os.path.exists(sc):
            try:
                self.groups.load(sc)
            except Exception:
                pass

        dw, dh = self.die
        return (f'{len(self.all_nets)} nets · {len(self.inst_info)} instances · '
                f'die {dw:.1f}×{dh:.1f} µm')

    def _load_via_bdb(self, def_path: str, lef_path: str):
        """Fast load using the BDB C++ module. Reuses existing .bdb if newer than .def."""
        import os as _os, tempfile
        db_path = _buda_mod.BDB.db_path(def_path)
        reuse = (
            _os.path.exists(db_path) and
            _os.path.getmtime(db_path) >= _os.path.getmtime(def_path)
        )
        db = _buda_mod.BDB(db_path)
        if not reuse:
            effective_lef = lef_path
            tmp_lef = None
            if not effective_lef or not _os.path.exists(effective_lef):
                sizes = infer_cell_sizes_from_def(def_path)
                if sizes:
                    tmp_lef = tempfile.NamedTemporaryFile(
                        mode='w', suffix='.lef', delete=False)
                    tmp_lef.write(_synthetic_lef(sizes))
                    tmp_lef.close()
                    effective_lef = tmp_lef.name
                    print(f'[def_viz] no LEF — inferred sizes for: '
                          f'{", ".join(sizes)}')
                else:
                    effective_lef = ''
            try:
                db.import_def_lef(def_path, effective_lef)
                db.compute_all()
            finally:
                if tmp_lef is not None:
                    _os.unlink(tmp_lef.name)
        self.bdb   = db
        self.units = db.units()
        self.die   = (db.die_w(), db.die_h())
        self._build_maps_from_bdb(db)

    def load_bdb(self, bdb_path: str) -> str:
        """Load directly from an existing .bdb file (no DEF/LEF needed)."""
        if not _BDB_AVAILABLE:
            raise RuntimeError('buda module not built — cannot load .bdb directly')
        self.def_path   = bdb_path
        self.inst_info  = {}
        self.net_insts  = {}
        self.inst_nets  = {}
        self.inst_roles = {}
        self.bdb        = None

        db = _buda_mod.BDB(bdb_path)
        self.bdb   = db
        self.units = db.units()
        self.die   = (db.die_w(), db.die_h())

        self._build_maps_from_bdb(db)

        self.groups = GroupTree()
        sc = GroupTree.sidecar_path(bdb_path)
        if os.path.exists(sc):
            try: self.groups.load(sc)
            except Exception: pass

        dw, dh = self.die
        return (f'{len(self.all_nets)} nets · {len(self.inst_info)} instances · '
                f'die {dw:.1f}×{dh:.1f} µm  [from BDB]')

    def _build_maps_from_bdb(self, db):
        """Populate inst_info, net_insts, inst_nets, inst_roles from an open BDB."""
        comps = db.all_components()

        # Bottom-up bbox propagation: intermediate hierarchy nodes from import_verilog
        # get placeholder coords (-1). Compute their bbox from placed leaf descendants.
        children = {}  # id -> [child_id]
        for c in comps:
            if c.parent_id is not None:
                children.setdefault(c.parent_id, []).append(c.id)
        computed = {}  # id -> (x1, y1, x2, y2)
        max_depth = max((c.depth for c in comps), default=0)
        for d in range(max_depth, -1, -1):
            for c in comps:
                if c.depth != d:
                    continue
                # x1 >= 0 is placed; the import_verilog placeholder sentinel is
                # exactly -1 (audit T3-02: `> 0` dropped a leaf legitimately at
                # the die's left edge, x==0, which has no children to rebuild a
                # bbox from and vanished from inst_info).
                if c.x1 >= 0:
                    computed[c.id] = (c.x1, c.y1, c.x2, c.y2)
                else:
                    kids = [computed[cid] for cid in children.get(c.id, []) if cid in computed]
                    if kids:
                        computed[c.id] = (min(b[0] for b in kids), min(b[1] for b in kids),
                                          max(b[2] for b in kids), max(b[3] for b in kids))

        for c in comps:
            bbox = computed.get(c.id)
            if bbox is None:
                continue
            self.inst_info[c.name] = {
                'x1': bbox[0], 'y1': bbox[1], 'x2': bbox[2], 'y2': bbox[3],
                'cell': c.cell, 'depth': c.depth,
            }

        net_id_name  = {n.id: n.name for n in db.all_nets()}
        comp_id_name = {c.id: c.name for c in comps}
        for p in db.all_pins():
            net_name  = net_id_name.get(p.net_id)
            inst_name = comp_id_name.get(p.comp_id)
            if net_name is None or inst_name is None:
                continue
            self.net_insts.setdefault(net_name, set()).add(inst_name)
            self.inst_nets.setdefault(inst_name, set()).add(net_name)
            role = 'driver' if p.dir == 'OUTPUT' else 'receiver'
            key = (net_name, inst_name)
            if key not in self.inst_roles or role == 'driver':
                self.inst_roles[key] = role
        self.all_nets = sorted(net_id_name.values())

    def _load_via_python(self, def_path: str, lef_path: str):
        """Fallback Python-only parser."""
        units, die, components, nets_raw = parse_def(def_path)
        if lef_path and os.path.exists(lef_path):
            lef_pins   = parse_lef(lef_path)
            cell_sizes = parse_cell_sizes(lef_path)
        else:
            lef_pins   = {}
            cell_sizes = infer_cell_sizes_from_def(def_path)
            if cell_sizes:
                print(f'[def_viz] no LEF — inferred sizes for: '
                      f'{", ".join(cell_sizes)}')

        self.units = units
        self.die   = die

        for net_name, conns in nets_raw.items():
            inst_set = set()
            for inst, pin in conns:
                if inst not in components:
                    continue
                cell, dx, dy, _ = components[inst]
                x1 = dx / units
                y1 = dy / units
                w, h = cell_sizes.get(cell, (0.5, 0.5))
                self.inst_info[inst] = {
                    'x1': x1, 'y1': y1, 'x2': x1 + w, 'y2': y1 + h, 'cell': cell, 'depth': 0
                }
                inst_set.add(inst)
                pin_dir = 'UNKNOWN'
                if cell in lef_pins and pin in lef_pins[cell]:
                    pin_dir = lef_pins[cell][pin][2]
                role = 'driver' if pin_dir == 'OUTPUT' else 'receiver'
                key = (net_name, inst)
                if key not in self.inst_roles or role == 'driver':
                    self.inst_roles[key] = role
                self.inst_nets.setdefault(inst, set()).add(net_name)
            self.net_insts[net_name] = inst_set

        self.all_nets = sorted(nets_raw.keys())

    def save_groups(self):
        if self.def_path:
            self.groups.save(GroupTree.sidecar_path(self.def_path))

    def visible_insts(self, selected_nets: set) -> list:
        if not selected_nets or not self.all_nets:
            return sorted(self.inst_info.keys())
        result = set()
        for net in selected_nets:
            result |= self.net_insts.get(net, set())
        return sorted(result)

    def inst_color(self, inst: str, selected_nets: set) -> str:
        roles = {self.inst_roles.get((n, inst))
                 for n in selected_nets if (n, inst) in self.inst_roles}
        if 'driver' in roles and 'receiver' in roles:
            return _C_BOTH
        if 'driver' in roles:
            return _C_DRIVER
        return _C_RECEIVER

    def hpwl_distribution(self) -> list:
        """Sorted list of (net_name, hpwl) for all nets with known positions."""
        if self.bdb is not None:
            import sqlite3
            db_path = _buda_mod.BDB.db_path(self.def_path)
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                'SELECT n.name, np.hpwl FROM net_props np '
                'JOIN net n ON n.id=np.net_id WHERE np.hpwl IS NOT NULL'
            ).fetchall()
            conn.close()
            return sorted(rows, key=lambda r: r[1])
        pairs = []
        for net in self.all_nets:
            h = self._net_hpwl(net)
            if h > 0:
                pairs.append((net, h))
        return sorted(pairs, key=lambda r: r[1])

    def _net_hpwl(self, net: str) -> float:
        placed = [self.inst_info[i]
                  for i in self.net_insts.get(net, set()) if i in self.inst_info]
        if len(placed) < 2:
            return 0.0
        return (max(p['x2'] for p in placed) - min(p['x1'] for p in placed) +
                max(p['y2'] for p in placed) - min(p['y1'] for p in placed))

    def group_nets(self, lo: float, hi: float, name: str = None):
        """Create a net group for nets with HPWL in [lo, hi] µm."""
        if self.bdb is not None:
            nets = self.bdb.nets_by_hpwl(lo, hi)
        else:
            nets = [n for n in self.all_nets if lo <= self._net_hpwl(n) <= hi]
        if not nets:
            return None
        g = self.groups.new_group(name or f'HPWL {lo:.1f}–{hi:.1f}µm ({len(nets)} nets)')
        for net in nets:
            self.groups.add_member(g.id, 'net', net)
        return g

    def group_insts(self, xl: float, yl: float, xh: float, yh: float, name: str = None):
        """Create an inst group for instances overlapping [xl, yl, xh, yh]."""
        if self.bdb is not None:
            insts = self.bdb.comps_in_rect(xl, yl, xh, yh)
        else:
            insts = [i for i, info in self.inst_info.items()
                     if info['x1'] < xh and info['x2'] > xl
                     and info['y1'] < yh and info['y2'] > yl]
        if not insts:
            return None
        g = self.groups.new_group(name or f'rect ({len(insts)} insts)')
        for inst in insts:
            self.groups.add_member(g.id, 'inst', inst)
        return g

    def group_common_nets(self, gid1: str, gid2: str, name: str = None):
        """Create a net group for nets connecting instances of gid1 and gid2."""
        insts1 = self.groups.all_insts(gid1)
        insts2 = self.groups.all_insts(gid2)
        common = [n for n, ni in self.net_insts.items()
                  if ni & insts1 and ni & insts2]
        if not common:
            return None
        g1n = getattr(self.groups.get(gid1), 'name', '?')
        g2n = getattr(self.groups.get(gid2), 'name', '?')
        g = self.groups.new_group(name or f'{g1n}↔{g2n} ({len(common)} nets)')
        for net in common:
            self.groups.add_member(g.id, 'net', net)
        return g

    def group_highlight(self, gid: str) -> tuple[set, set]:
        """
        Return (direct_insts, net_reached_insts) for a selected group.

        direct_insts     — all inst members (transitive through sub-groups)
        net_reached_insts — instances reachable via net members, minus direct_insts
        """
        direct = self.groups.all_insts(gid)
        net_reached = set()
        for net in self.groups.all_nets(gid):
            net_reached |= self.net_insts.get(net, set())
        net_reached -= direct
        return direct, net_reached

    def available_depths(self) -> list:
        """Sorted list of distinct depth values present in inst_info."""
        depths = {info['depth'] for info in self.inst_info.values() if 'depth' in info}
        return sorted(depths)

    def insts_at_depth(self, depth: int) -> list:
        """Sorted list of instance names at the given hierarchy depth."""
        return sorted(inst for inst, info in self.inst_info.items()
                      if info.get('depth') == depth)


# ── Canvas drawing helpers ────────────────────────────────────────────────────

def draw_die(ax, die):
    dw, dh = die
    if dw > 0 and dh > 0:
        ax.add_patch(mpatches.Rectangle(
            (0, 0), dw, dh,
            linewidth=1.2, edgecolor=_C_DIE_EDGE, facecolor=_C_DIE_FILL, zorder=0))


def draw_bg_instances(ax, inst_info: dict):
    for info in inst_info.values():
        ax.add_patch(mpatches.Rectangle(
            (info['x1'], info['y1']),
            info['x2'] - info['x1'], info['y2'] - info['y1'],
            linewidth=0.5, edgecolor='#dddddd', facecolor='#bbbbbb', alpha=0.08, zorder=1))


def draw_selected_instances(ax, visible: list, data: DefVizData,
                             selected_nets: set,
                             inst_highlight: dict = None) -> dict:
    """
    Draw colored patches for visible instances. Returns {patch: inst_name}.

    inst_highlight: optional {inst_name: color} that overrides the default
    driver/receiver coloring for specific instances (used for group display).
    """
    patch_inst = {}
    for inst in visible:
        info = data.inst_info.get(inst)
        if info is None:
            continue
        if inst_highlight and inst in inst_highlight:
            color = inst_highlight[inst]
        else:
            color = data.inst_color(inst, selected_nets)
        patch = mpatches.Rectangle(
            (info['x1'], info['y1']),
            info['x2'] - info['x1'], info['y2'] - info['y1'],
            linewidth=0.8, edgecolor=_C_EDGE,
            facecolor=color,
            alpha=0.85, zorder=2, picker=True)
        ax.add_patch(patch)
        patch_inst[patch] = inst
    return patch_inst


def draw_group_boxes(ax, groups: GroupTree, inst_info: dict,
                     highlight_id: str = None) -> dict:
    """
    Draw labeled bounding boxes for all groups that have inst members.
    Boxes are only drawn when the group (transitively) contains at least one
    placed instance — groups of pure nets or empty groups are skipped.
    Returns {patch: group_id}.
    """
    patch_group = {}

    def _draw(g, depth):
        insts  = groups.all_insts(g.id)
        placed = [inst_info[i] for i in insts if i in inst_info]
        if not placed:
            return   # no inst members — skip box (also handles pure-net groups)
        x1 = min(p['x1'] for p in placed)
        y1 = min(p['y1'] for p in placed)
        x2 = max(p['x2'] for p in placed)
        y2 = max(p['y2'] for p in placed)
        pad    = depth * 0.3
        lw     = 2.8 if g.id == highlight_id else 1.6
        ls     = '-'  if g.id == highlight_id else '--'
        falpha = max(0.10 - depth * 0.025, 0.03)
        patch  = mpatches.Rectangle(
            (x1 - pad, y1 - pad), (x2 - x1) + 2 * pad, (y2 - y1) + 2 * pad,
            linewidth=lw, edgecolor=g.color, facecolor=g.color,
            linestyle=ls, alpha=falpha, zorder=4 + depth, picker=True)
        ax.add_patch(patch)
        patch_group[patch] = g.id
        ax.text(x1 - pad, y2 + pad, g.name,
                fontsize=8, color=g.color, fontweight='bold',
                va='bottom', clip_on=True, zorder=5 + depth)
        for child in groups.children(g.id):
            _draw(child, depth + 1)

    for root_group in groups.roots():
        _draw(root_group, 0)
    return patch_group


def fit_view(ax, visible: list, inst_info: dict, die=None, full_die=False):
    if full_die and die and die[0] > 0:
        m = max(die) * 0.03
        ax.set_xlim(-m, die[0] + m)
        ax.set_ylim(-m, die[1] + m)
        return
    placed = [inst_info[i] for i in visible if i in inst_info]
    if not placed:
        if die and die[0] > 0:
            m = max(die) * 0.05
            ax.set_xlim(-m, die[0] + m)
            ax.set_ylim(-m, die[1] + m)
        return
    x1 = min(p['x1'] for p in placed)
    y1 = min(p['y1'] for p in placed)
    x2 = max(p['x2'] for p in placed)
    y2 = max(p['y2'] for p in placed)
    pw = max(x2 - x1, 1.0) * 0.12
    ph = max(y2 - y1, 1.0) * 0.12
    ax.set_xlim(x1 - pw, x2 + pw)
    ax.set_ylim(y1 - ph, y2 + ph)
