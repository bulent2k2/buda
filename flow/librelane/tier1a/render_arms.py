#!/usr/bin/env python3
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

"""The three N = 8 arms (F flat, H hierarchical, H+B hierarchical with BUDA)
drawn side by side at ONE scale from their signoff DEFs, with a hierarchical
arm FLATTENED: every hardened block's own final DEF is placed back into the
top at its FIXED location, so a picture -- or a wire total -- covers every
cell and every wire the arm actually holds, not the top level alone.

    render_arms.py placement [--out PREFIX]              std cells by kind + block/row outlines
    render_arms.py routing  --layers met2,met3 [--out PREFIX] [--lw W]   signal wires on those layers
    render_arms.py wire                                  wire per layer, top / blocks / flattened, per arm
    render_arms.py utilization                           logic-cell area over die (and core) area, per arm

`placement` writes one PNG per arm plus `<prefix>_same_scale.png`; `routing`
writes `<prefix>_<layers>.png`.  Fill, tap, decap and antenna-diode cells are
left out of the placement pictures and the utilization numerator (they carry
no logic and the 130k decaps of the flat arm would cover its whole die); the
`wire` and `routing` modes read the DEF `NETS` section only -- the power
grid is in `SPECIALNETS` and is the same fixed pattern in every arm.

What the numbers are pinned against: the top-level wire this reads adds up
to the `route__wirelength` LibreLane reports for each arm within 20 um
(934,813 / 803,887 / 300,691 um against 934,831 / 803,897 / 300,704), and the
logic + tap area it sums for F is LibreLane's `design__instance__area__stdcell`
within 0.02 % (461,374 vs 461,459 um^2), so `utilization` reproduces the
46.3 % of docs/internal/librelane_hier_flow.md's N = 8 table when tap cells
are counted and 44.5 % of the core without them.

Which runs are the arms is a table at the top (`ARMS`), relative to this
directory: F = `n8/runs/flat`, H = `n8/h` (top run `h`), H+B = `hb2/n8/h`
(top run `hb`, the pre-notch-fix arm every figure in the results table comes
from).  Standard-cell sizes come from the PDK's `sky130_fd_sc_hd.lef`, found
under `$PDK_ROOT` or the `ciel` cache the LibreLane recipe installs into.
"""
import argparse
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# arm -> (kind, top DEF, block run dir pattern).  A block's final DEF is
# <blocks>/<cell>/runs/h/final/def/<cell>.def -- harm.sh's layout.
ARMS = {
    "F":   ("flat", "n8/runs/flat/final/def/tpu_top.def", None),
    "H":   ("hier", "n8/h/top/runs/h/final/def/tpu_top.def", "n8/h"),
    "H+B": ("hier", "hb2/n8/h/top/runs/hb/final/def/tpu_top.def", "hb2/n8/h"),
}
TITLE = {"F": "F: flat, no hierarchy",
         "H": "H: hierarchical, no BUDA",
         "H+B": "H+B: hierarchical, with BUDA"}
LAYERS = ["li1", "met1", "met2", "met3", "met4", "met5"]
SKIP = re.compile(r"__(fill|decap|tapvpwrvgnd|diode)")      # no logic in these
TAP = re.compile(r"__tapvpwrvgnd")


def find_lef():
    """An explicit $PDK_ROOT wins outright; the ciel cache (its newest version)
    is consulted only when there is none -- a cache appended AFTER the
    configured PDK would silently outrank it (Codex on #951)."""
    root = os.environ.get("PDK_ROOT")
    if root:
        hit = glob.glob(os.path.join(root, "sky130A", "libs.ref", "sky130_fd_sc_hd", "lef",
                                     "sky130_fd_sc_hd.lef"))
        if hit:
            return hit[0]
        sys.exit(f"render_arms: PDK_ROOT={root} holds no sky130A/libs.ref/sky130_fd_sc_hd/lef/"
                 "sky130_fd_sc_hd.lef")
    cands = sorted(glob.glob(os.path.expanduser(
        "~/.ciel/ciel/sky130/versions/*/sky130A/libs.ref/sky130_fd_sc_hd/lef/sky130_fd_sc_hd.lef")))
    if not cands:
        sys.exit("render_arms: no sky130_fd_sc_hd.lef under $PDK_ROOT or ~/.ciel -- install the PDK "
                 "(docs/internal/librelane_hier_flow.md recipe 1) or set PDK_ROOT")
    return cands[-1]


def lef_sizes(path):
    """MACRO name -> (w, h) in um."""
    sizes, cur = {}, None
    for line in open(path):
        m = re.match(r"MACRO (\S+)", line)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r"\s*SIZE ([\d.]+) BY ([\d.]+)", line)
        if m and cur:
            sizes[cur] = (float(m.group(1)), float(m.group(2)))
    return sizes


_COMP = re.compile(r"^\s*- (\S+) (\S+) .*?\+ (?:PLACED|FIXED) \( (-?\d+) (-?\d+) \) (\S+)")


def parse_def(path):
    """(die [x1 y1 x2 y2] um, components [(name, cell, x um, y um, orient)], core [x1 y1 x2 y2] um).
    The core is the extent of the ROW statements (None when the DEF has none)."""
    units, die, comps, inc = 1000, None, [], False
    xs, ys = [], []
    for line in open(path):
        if line.startswith("UNITS"):
            units = int(line.split()[3])
        elif line.startswith("DIEAREA"):
            die = [int(x) for x in re.findall(r"-?\d+", line)]
        elif line.startswith("ROW "):
            m = re.match(r"ROW \S+ \S+ (-?\d+) (-?\d+) \S+ DO (\d+) BY (\d+) STEP (\d+) (\d+)", line)
            if m:
                x, y, nx, ny, sx, sy = map(int, m.groups())
                xs += [x, x + nx * sx]
                ys += [y, y + (ny * sy if ny > 1 else 2720)]     # a one-row ROW is one site tall
        elif line.startswith("COMPONENTS"):
            inc = True
        elif line.startswith("END COMPONENTS"):
            break
        elif inc:
            m = _COMP.match(line)
            if m:
                n, c, x, y, o = m.groups()
                comps.append((n, c, int(x) / units, int(y) / units, o))
    core = [min(xs) / units, min(ys) / units, max(xs) / units, max(ys) / units] if xs else None
    return [d / units for d in die], comps, core


_STMT = re.compile(r"(?:ROUTED|NEW) (li1|met\d)((?: \( [-\d*]+ [-\d*]+(?: -?\d+)? \))+)")
_PT = re.compile(r"\( ([-\d*]+) ([-\d*]+)(?: -?\d+)? \)")


def routes(path, dx=0.0, dy=0.0, keep=LAYERS):
    """layer -> [((x1,y1),(x2,y2)) um] over the DEF's NETS section (SPECIALNETS
    excluded).  A `*` coordinate repeats the previous point's, as DEF defines
    it; a one-point statement is a via and adds no wire."""
    segs = {l: [] for l in keep}
    units, innets = 1000, False
    for line in open(path):
        if line.startswith("UNITS"):
            units = int(line.split()[3])
        if line.startswith("NETS"):
            innets = True
            continue
        if line.startswith("END NETS"):
            break
        if not innets:
            continue
        m = _STMT.search(line)
        if not m or m.group(1) not in segs:
            continue
        px = py = None
        pts = []
        for x, y in _PT.findall(m.group(2)):
            px = px if x == "*" else int(x)
            py = py if y == "*" else int(y)
            pts.append((px / units + dx, py / units + dy))
        for a, b in zip(pts, pts[1:]):
            if a != b:
                segs[m.group(1)].append((a, b))
    return segs


def length(segs):
    return {l: sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in v) for l, v in segs.items()}


def kind(cell):
    c = cell.split("__")[-1]
    if re.match(r"(df|dl[xrc]|sdf|edf)", c):      # flops and latches; dlygate/dlymetal are delay buffers
        return "ff"
    if re.match(r"(buf|clkbuf|clkdly|dlygate|dlymetal|clkinv|probe)", c):
        return "buf"
    return "comb"


class Arm:
    """One arm, flattened: top-level cells/wires plus every block's, placed."""

    def __init__(self, name, sizes, layers=LAYERS):
        kindof, top, blocks = ARMS[name]
        self.name, self.sizes = name, sizes
        top = os.path.join(HERE, top)
        if not os.path.exists(top):
            sys.exit(f"render_arms: {name}: no {top} -- run the arm first "
                     "(docs/internal/librelane_hier_flow.md recipe 7)")
        self.die, comps, self.core = parse_def(top)
        self.top_segs = routes(top, keep=layers)
        self.top_cells = [c for c in comps if c[1].startswith("sky130")]
        self.blocks, self.block_segs, self.block_cells = [], {l: [] for l in layers}, []
        self.block_defs = {}
        cache = {}
        for n, c, x, y, o in comps:
            if c.startswith("sky130"):
                continue
            assert o == "N", f"{n}: orientation {o} not handled (every emitted block is N)"
            if c not in cache:
                bp = os.path.join(HERE, blocks, c, "runs", "h", "final", "def", f"{c}.def")
                bdie, bcomps, _ = parse_def(bp)
                cache[c] = (bdie, bcomps, routes(bp, keep=layers))
                self.block_defs[c] = bp
            bdie, bcomps, bsegs = cache[c]
            ox, oy = x - bdie[0], y - bdie[1]
            for l, v in bsegs.items():
                self.block_segs[l].extend(((a[0] + ox, a[1] + oy), (b[0] + ox, b[1] + oy)) for a, b in v)
            self.block_cells.extend((bn, bc, bx + ox, by + oy, bo) for bn, bc, bx, by, bo in bcomps
                                    if bc.startswith("sky130"))
            self.blocks.append((n, c, x, y, bdie[2] - bdie[0], bdie[3] - bdie[1]))

    @property
    def cells(self):
        return self.top_cells + self.block_cells

    def segs(self):
        return {l: self.top_segs[l] + self.block_segs[l] for l in self.top_segs}

    def rows(self):
        """Logical row of PEs -> bbox, from the `row_<r>.pe_<c>` instance names."""
        rows = {}
        for n, c, x, y, w, h in self.blocks:
            if "." in n:
                r = n.split(".")[0]
                x0, y0, x1, y1 = rows.get(r, (x, y, x + w, y + h))
                rows[r] = (min(x0, x), min(y0, y), max(x1, x + w), max(y1, y + h))
        return rows

    def die_area(self):
        return (self.die[2] - self.die[0]) * (self.die[3] - self.die[1])

    def cell_area(self, include_tap=False):
        a = 0.0
        for _, c, _, _, _ in self.cells:
            if SKIP.search(c) and not (include_tap and TAP.search(c)):
                continue
            w, h = self.sizes[c]
            a += w * h
        return a


# ── drawing ──────────────────────────────────────────────────────────────

def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


CELL_COL = {"ff": "#1f5fbf", "comb": "#e8871e", "buf": "#3aa65a"}
CELL_LAB = {"ff": "flip-flops", "comb": "logic gates", "buf": "buffers / clock / delay"}
LAYER_COL = {"li1": "#999999", "met1": "#7f3fbf", "met2": "#1f77b4", "met3": "#2ca02c",
             "met4": "#d62728", "met5": "#ff9f1a"}


def _frame(ax, arm, block_ec, block_lw):
    from matplotlib.patches import Rectangle
    d = arm.die
    ax.add_patch(Rectangle((d[0], d[1]), d[2] - d[0], d[3] - d[1], fill=False, ec="black",
                           lw=1.2, zorder=5))
    for _, _, x, y, w, h in arm.blocks:
        ax.add_patch(Rectangle((x, y), w, h, fill=False, ec=block_ec, lw=block_lw, zorder=4))
    ax.set_xlim(d[0] - 20, d[2] + 20)
    ax.set_ylim(d[1] - 20, d[3] + 20)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)


def draw_placement(ax, arm):
    from matplotlib.collections import PolyCollection
    from matplotlib.patches import Rectangle
    rects = {"ff": [], "comb": [], "buf": []}
    for _, c, x, y, _ in arm.cells:
        if SKIP.search(c):
            continue
        w, h = arm.sizes[c]
        rects[kind(c)].append([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
    n = 0
    for k in ("comb", "buf", "ff"):
        if rects[k]:
            ax.add_collection(PolyCollection(rects[k], facecolors=CELL_COL[k], edgecolors="none",
                                             alpha=0.9, zorder=2))
            n += len(rects[k])
    for x0, y0, x1, y1 in arm.rows().values():
        ax.add_patch(Rectangle((x0 - 6, y0 - 6), x1 - x0 + 12, y1 - y0 + 12, fill=False, ec="#555",
                               lw=0.9, ls=(0, (4, 3)), zorder=4))
    _frame(ax, arm, "#222", 0.6)
    d = arm.die
    ax.set_title(f"{TITLE[arm.name]}\n{d[2]-d[0]:,.0f} × {d[3]-d[1]:,.0f} µm = "
                 f"{arm.die_area()/1e6:.3f} mm²\n{n:,} logic cells", fontsize=11, loc="left")
    return n


def draw_routing(ax, arm, layers, lw):
    from matplotlib.collections import LineCollection
    segs = arm.segs()
    for l in layers:
        if segs[l]:
            ax.add_collection(LineCollection(segs[l], colors=LAYER_COL[l], linewidths=lw, zorder=3))
    _frame(ax, arm, "#bbbbbb", 0.5)
    L = length(segs)
    ax.set_title(TITLE[arm.name] + "\n" + ", ".join(f"{l} {L[l]/1000:,.1f} mm" for l in layers),
                 fontsize=11, loc="left")
    return L


def side_by_side(arms, draw, legend, out):
    """All arms in one figure at ONE um scale."""
    plt = _mpl()
    ws = [a.die[2] - a.die[0] for a in arms]
    hs = [a.die[3] - a.die[1] for a in arms]
    s = 16.0 / (sum(ws) + 400)
    fh = max(hs) * s + 1.8
    fig = plt.figure(figsize=(16.5, fh))
    x = 0.01
    for a, w, h in zip(arms, ws, hs):
        fw = (w + 40) * s / 16.5
        ax = fig.add_axes([x, 0.6 / fh, fw, (h + 40) * s / fh])
        draw(ax, a)
        x += fw + 0.02
    fig.legend(handles=legend, loc="lower left", ncol=len(legend), fontsize=9, frameon=False)
    fig.savefig(out, dpi=200)
    plt.close(fig)
    print(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["placement", "routing", "wire", "utilization"])
    ap.add_argument("--layers", default="met4,met5", help="routing mode: comma list of layers")
    ap.add_argument("--lw", type=float, default=None, help="routing mode: line width (default by density)")
    ap.add_argument("--out", default="arms", help="output prefix")
    ap.add_argument("--arms", default="F,H,H+B")
    a = ap.parse_args()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    sizes = lef_sizes(find_lef())
    names = a.arms.split(",")
    layers = a.layers.split(",") if a.mode == "routing" else LAYERS
    arms = [Arm(n, sizes, layers) for n in names]

    if a.mode == "placement":
        plt = _mpl()
        for arm in arms:
            w, h = arm.die[2] - arm.die[0], arm.die[3] - arm.die[1]
            s = 8.0 / max(w, h)
            fig = plt.figure(figsize=(w * s + 0.6, h * s + 1.6))
            ax = fig.add_axes([0.02, 0.10, 0.96, 0.80])
            draw_placement(ax, arm)
            hs = [Patch(fc=CELL_COL[k], label=CELL_LAB[k]) for k in ("comb", "ff", "buf")]
            if arm.blocks:
                hs += [Line2D([0], [0], color="#222", lw=1.2, label="hardened block"),
                       Line2D([0], [0], color="#555", lw=1.2, ls=(0, (4, 3)), label="row of PEs (logical)")]
            fig.legend(handles=hs, loc="lower left", ncol=3, fontsize=8, frameon=False)
            fn = f"{a.out}_{arm.name.replace('+', 'B').lower()}.png"
            fig.savefig(fn, dpi=200)
            plt.close(fig)
            print(fn)
        leg = [Patch(fc=CELL_COL[k], label=CELL_LAB[k]) for k in ("comb", "ff", "buf")]
        leg += [Line2D([0], [0], color="#222", lw=1.2, label="hardened block (PE, feeder, buffer, accumulator)"),
                Line2D([0], [0], color="#555", lw=1.2, ls=(0, (4, 3)), label="row of 8 PEs (logical grouping)")]
        side_by_side(arms, draw_placement, leg, f"{a.out}_same_scale.png")

    elif a.mode == "routing":
        lw = a.lw if a.lw is not None else (0.35 if set(layers) <= {"met4", "met5"} else 0.15)
        leg = [Line2D([0], [0], color=LAYER_COL[l], lw=2, label=f"{l} signal wires") for l in layers]
        leg.append(Line2D([0], [0], color="#bbbbbb", lw=1, label="hardened block outline"))
        side_by_side(arms, lambda ax, arm: draw_routing(ax, arm, layers, lw), leg,
                     f"{a.out}_{'_'.join(layers)}.png")

    elif a.mode == "wire":
        print(f"{'arm':5s} {'part':6s} {'total':>9s} " + " ".join(f"{l:>9s}" for l in LAYERS) + "   (um)")
        for arm in arms:
            parts = [("top", length(arm.top_segs))]
            if arm.blocks:
                parts += [("blocks", length(arm.block_segs)), ("flat", length(arm.segs()))]
            for label, L in parts:
                print(f"{arm.name:5s} {label:6s} {sum(L.values()):9.0f} " +
                      " ".join(f"{L[l]:9.0f}" for l in LAYERS))

    else:
        print(f"{'arm':5s} {'die mm2':>8s} {'core mm2':>9s} {'logic mm2':>10s} {'logic/die':>10s} "
              f"{'logic/core':>11s} {'+tap/core':>10s} {'blocks/die':>11s}")
        for arm in arms:
            die, logic, lt = arm.die_area(), arm.cell_area(), arm.cell_area(include_tap=True)
            core = ((arm.core[2] - arm.core[0]) * (arm.core[3] - arm.core[1])) if arm.core else float("nan")
            blk = sum(w * h for *_, w, h in arm.blocks)
            print(f"{arm.name:5s} {die/1e6:8.3f} {core/1e6:9.3f} {logic/1e6:10.4f} {100*logic/die:9.1f}% "
                  f"{100*logic/core:10.1f}% {100*lt/core:9.1f}% {100*blk/die:10.1f}%")


if __name__ == "__main__":
    main()
