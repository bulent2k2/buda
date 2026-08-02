#!/usr/bin/env python3
"""Render a stacked-layout placement from a BDB (headless PNG).

Draws the die, the depth-1 instances coloured per cell type, their leaf
blocks, the channels between stacked instances, and each instance origin as
a multiple of the on-grid quantum — the picture that shows at a glance that
`build_hier_demo.py --layout stacked --grid Q` did what it promised.

  python3 tools/render_stack_placement.py <design.bdb> <out.png> [grid]

grid defaults to 504 (flow/chip/chip_stack: the LCM of the M2/M4/M6/M8
pitches, i.e. the horizontal layers a cell may use under
`reserve_top_layers 2`).
"""
import sys
sys.path.insert(0, "build")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import buda_db

GRID = float(sys.argv[3]) if len(sys.argv) > 3 else 504.0
db = buda_db.BDB(sys.argv[1])
comps = db.all_components()
top = [c for c in comps if c.depth == 0][0]
insts = sorted([c for c in comps if c.depth == 1], key=lambda c: (c.x1, c.y1))
leaves = [c for c in comps if c.depth == 2]

W, H = top.x2 - top.x1, top.y2 - top.y1
fig, ax = plt.subplots(figsize=(7.2, 10.0), dpi=200)
fig.patch.set_facecolor("#faf9f7")
ax.set_facecolor("#faf9f7")

CELL_C = {"big2": "#b4622a", "mix2": "#40709c"}

# die
ax.add_patch(Rectangle((0, 0), W, H, fill=False, ec="#2c3844", lw=1.6, zorder=5))

# grid lines at multiples of 504 — faint, to show what "on grid" means
y = 0.0
while y <= H:
    ax.axhline(y, color="#2c3844", lw=0.25, alpha=0.13, zorder=0)
    y += GRID

for inst in insts:
    c = CELL_C.get(inst.cell, "#777")
    ax.add_patch(Rectangle((inst.x1, inst.y1), inst.x2 - inst.x1,
                           inst.y2 - inst.y1, fc=c, ec=c, alpha=0.10,
                           lw=1.8, zorder=2))
    ax.add_patch(Rectangle((inst.x1, inst.y1), inst.x2 - inst.x1,
                           inst.y2 - inst.y1, fill=False, ec=c, lw=1.8,
                           zorder=6))

for lf in leaves:
    par = next(i for i in insts if i.id == lf.parent_id)
    c = CELL_C.get(par.cell, "#777")
    ax.add_patch(Rectangle((lf.x1, lf.y1), lf.x2 - lf.x1, lf.y2 - lf.y1,
                           fc=c, ec=c, alpha=0.42, lw=0.25, zorder=3))

# instance labels + the on-grid arithmetic
for inst in insts:
    c = CELL_C.get(inst.cell, "#777")
    short = inst.name.split("/")[-1]
    k = inst.y1 / GRID
    bbox = dict(boxstyle="round,pad=0.28", fc="#faf9f7", ec=c, lw=0.5,
                alpha=0.94)
    ax.text(inst.x1 + 70, inst.y2 - 300,
            f"{short}\ny = {inst.y1:.0f}"
            + (f" = {k:.0f}×{GRID:.0f}" if k else "  (origin)"),
            color=c, fontsize=6.6, fontweight="bold", zorder=10,
            va="top", bbox=bbox, family="DejaVu Sans", linespacing=1.5)

# channel annotations between stacked instances
for cell in ("big2", "mix2"):
    col = sorted([i for i in insts if i.cell == cell], key=lambda c: c.y1)
    c = CELL_C[cell]
    for a, b in zip(col, col[1:]):
        gap_lo, gap_hi = a.y2, b.y1
        xm = (a.x1 + a.x2) / 2
        ax.annotate("", xy=(xm, gap_hi), xytext=(xm, gap_lo),
                    arrowprops=dict(arrowstyle="<->", color=c, lw=0.8,
                                    shrinkA=0, shrinkB=0), zorder=9)
        ax.text(xm + 90, (gap_lo + gap_hi) / 2, f"channel {gap_hi - gap_lo:.0f}",
                color=c, fontsize=6.0, va="center", zorder=9,
                family="DejaVu Sans")
    pitch = col[1].y1 - col[0].y1
    ax.text((col[0].x1 + col[0].x2) / 2, -420,
            f"{cell}: {len(col)}×  pitch {pitch:.0f} = {pitch/GRID:.0f}×{GRID:.0f}",
            color=c, fontsize=7.0, ha="center", fontweight="bold",
            family="DejaVu Sans")

ax.set_xlim(-500, W + 500)
ax.set_ylim(-900, H + 700)
ax.set_aspect("equal")
ax.axis("off")
ax.set_title("chip_stack — on-grid stacked placement\n"
             f"die {W:.0f} × {H:.0f} · every instance origin a multiple of "
             f"{GRID:.0f}",
             fontsize=9, color="#2c3844", pad=14, family="DejaVu Sans")
fig.tight_layout()
out = sys.argv[2]
fig.savefig(out, bbox_inches="tight", facecolor=fig.get_facecolor())
print("wrote", out)
