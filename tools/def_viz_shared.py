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

# ── Palette ───────────────────────────────────────────────────────────────────
_C_DRIVER   = '#FF8C66'
_C_RECEIVER = '#66AAFF'
_C_BOTH     = '#CC88FF'
_C_EDGE     = '#444444'
_C_DIE_FILL = '#f6f6f6'
_C_DIE_EDGE = '#999999'


# ── Parsing ───────────────────────────────────────────────────────────────────

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

    def load(self, def_path: str, lef_path: str) -> str:
        """Parse DEF+LEF, rebuild indexes, auto-load group sidecar. Returns status string."""
        units, die, components, nets_raw = parse_def(def_path)
        lef_pins   = parse_lef(lef_path)
        cell_sizes = parse_cell_sizes(lef_path)

        self.def_path  = def_path
        self.units     = units
        self.die       = die
        self.inst_info = {}
        self.net_insts = {}
        self.inst_nets = {}
        self.inst_roles= {}

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
                    'x1': x1, 'y1': y1, 'x2': x1 + w, 'y2': y1 + h, 'cell': cell
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

        self.groups = GroupTree()
        sc = GroupTree.sidecar_path(def_path)
        if os.path.exists(sc):
            try:
                self.groups.load(sc)
            except Exception:
                pass

        return (f'{len(self.all_nets)} nets · {len(self.inst_info)} instances · '
                f'die {die[0]:.1f}×{die[1]:.1f} µm')

    def save_groups(self):
        if self.def_path:
            self.groups.save(GroupTree.sidecar_path(self.def_path))

    def visible_insts(self, selected_nets: set) -> list:
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
            linewidth=0, facecolor='#bbbbbb', alpha=0.18, zorder=1))


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
