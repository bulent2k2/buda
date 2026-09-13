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

"""Render a WHOLE design headlessly: floorplan, NUTS, DetailedNUTS.

The per-design sibling of `tools/render.py`, which renders ONE bundle's pinned
candidate.  This one runs a `.buda` flow to its end and writes one PNG per
stage the flow reached, plus a JSON of the measured shape so a caption can
quote numbers that came from the same session as the picture:

  <prefix>_fp.png      floorplan at EVERY hierarchy level (BDB flows: containers
                       as depth-shaded outlines, leaves coloured by cell type;
                       flat flows: the session floorplan's blocks)
  <prefix>_nuts.png    abstract bus tracks, one line per placed bus segment
  <prefix>_dnuts.png   per-bit wires on concrete signal tracks
  <prefix>_meta.json   die, component/leaf/bundle/bit-wire counts, abstract and
                       detailed wirelength, overlaps, unplaced, audit verdicts

Layer colours are the viewer's own (`viz_common._LAYER_COLOR`), so a picture
here reads like the GUI.  The detailed wirelength is the sum of every placed
bit-wire's span — the same quantity `report_wirelength` prints, which is what
`test_render_design.py` pins them against.

Usage:
  tools/render_design.py <flow.buda> [--out PREFIX] [--title TEXT] [--dpi N]
                                     [--label-depth N]

  --out          output prefix (default: the flow's stem, in the current dir)
  --title        caption prefix on every panel (default: the flow's basename)
  --dpi          raster resolution (default 150)
  --label-depth  label containers at this depth and above (default 1)

`visualize`, `visualize_topologies` and `exit` lines are skipped; every other
command runs, so `check_design` verdicts land in the JSON.  A flow that stops
before `run_nuts` / `run_detailed_nuts` gets only the panels its state supports
(said on stdout, never a blank picture).

Example:
  PYTHONPATH=build:src tools/render_design.py flow/soc_mid.buda \
      --out /tmp/soc --title "soc.tcl 32"
"""
import argparse
import contextlib
import io
import json
import os
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                       # noqa: E402
from matplotlib.lines import Line2D                   # noqa: E402
from matplotlib.patches import Patch, Rectangle       # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (os.path.join(_ROOT, "src"), os.path.join(_ROOT, "build")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import buda_cli                                       # noqa: E402
try:
    from viz_common import _LAYER_COLOR as LAYER_COLOR  # the viewer's palette
except ImportError:                                   # pragma: no cover
    LAYER_COLOR = {}

# Lines that open a window or end the session; everything else runs.
_SKIP = {"visualize", "visualize_topologies", "exit"}
_CELL_PALETTE = plt.get_cmap("tab20").colors


def run_flow(path):
    """Run every command of the flow (minus _SKIP) in one session.

    The flow's directory is the CWD while it runs, as `tools/render.py` does:
    a line fed to `do_command` has no script context, so relative paths in
    `source` / `open_bdb` lines resolve the way they would under the CLI.
    """
    s = buda_cli.BudaSession()
    s.no_viz = True
    with open(path) as f:
        lines = f.read().splitlines()
    cwd = os.getcwd()
    os.chdir(os.path.dirname(os.path.abspath(path)) or ".")
    log = io.StringIO()
    t0 = time.time()
    try:
        with contextlib.redirect_stdout(log):
            for ln in lines:
                st = ln.strip()
                if not st or st.startswith("#") or st.split()[0] in _SKIP:
                    continue
                s.do_command(ln)
    finally:
        os.chdir(cwd)
    return s, log.getvalue(), time.time() - t0


class _Comp:
    """One drawable component: BDB row or a flat-floorplan block."""
    __slots__ = ("name", "cell", "depth", "x1", "y1", "x2", "y2", "is_leaf")

    def __init__(self, name, cell, depth, x1, y1, x2, y2, is_leaf):
        self.name, self.cell, self.depth = name, cell, depth
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.is_leaf = is_leaf


def components(s):
    """Every placed component of the design, from the BDB when one is open
    (all levels), else from the flat floorplan (one level, no cell types)."""
    if s.bdb is not None:
        out = []
        for c in s.bdb.all_components():
            if c.x2 <= c.x1 or c.y2 <= c.y1:       # unplaced: -1,-1,-1,-1
                continue
            out.append(_Comp(c.name, c.cell, c.depth, c.x1, c.y1, c.x2, c.y2,
                             bool(c.is_leaf)))
        if out:
            return out, True
    out = [_Comp(name, "block", 0, r.x1, r.y1, r.x2, r.y2, True)
           for name, r in s.fp.get_all_blocks()]
    return out, False


def die_extent(s, cs):
    w = h = 0.0
    if s.bdb is not None:
        w, h = s.bdb.die_w(), s.bdb.die_h()
    if w <= 0 or h <= 0:
        w = max(c.x2 for c in cs)
        h = max(c.y2 for c in cs)
    return w, h


def _new_fig(w, h, title):
    fw = 13.0
    fh = max(6.0, fw * (h / w) + 1.2)
    fig, ax = plt.subplots(figsize=(fw, fh))
    ax.set_aspect("equal")
    ax.set_xlim(-w * 0.01, w * 1.01)
    ax.set_ylim(-h * 0.01, h * 1.01)
    ax.add_patch(Rectangle((0, 0), w, h, fc="white", ec="#222", lw=1.2, zorder=0))
    ax.set_title(title, fontsize=12, loc="left")
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)
    return fig, ax


def _side_legend(ax, handles, title):
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0),
              fontsize=8, frameon=False, title=title, title_fontsize=8)


def draw_floorplan(ax, cs, label_depth):
    maxd = max(c.depth for c in cs)
    leaves = [c for c in cs if c.is_leaf]
    conts = [c for c in cs if not c.is_leaf]
    cells = sorted({c.cell for c in leaves})
    col = {cell: _CELL_PALETTE[i % len(_CELL_PALETTE)] for i, cell in enumerate(cells)}
    for c in sorted(conts, key=lambda c: c.depth):
        shade = 0.97 - 0.05 * (maxd - c.depth)
        ax.add_patch(Rectangle((c.x1, c.y1), c.x2 - c.x1, c.y2 - c.y1,
                               fc=(shade, shade, shade), ec="#444",
                               lw=max(0.6, 1.6 - 0.3 * c.depth), zorder=1 + c.depth))
        if c.depth <= label_depth:
            ax.text(c.x1 + (c.x2 - c.x1) * 0.01, c.y2 - (c.y2 - c.y1) * 0.01,
                    c.name.split("/")[-1], fontsize=7, ha="left", va="top",
                    color="#333", zorder=20, clip_on=True)
    for c in leaves:
        ax.add_patch(Rectangle((c.x1, c.y1), c.x2 - c.x1, c.y2 - c.y1,
                               fc=col[c.cell], ec="#222", lw=0.4, alpha=0.85,
                               zorder=2 + c.depth))
        if not conts and len(leaves) <= 80:     # flat design: name the blocks
            ax.text((c.x1 + c.x2) / 2, (c.y1 + c.y2) / 2, c.name, fontsize=7,
                    ha="center", va="center", color="#222", zorder=20, clip_on=True)
    handles = [Patch(fc=col[k], ec="#222", label=k) for k in cells]
    if conts:
        handles.append(Patch(fc="#eee", ec="#444", label="container (depth-shaded)"))
    _side_legend(ax, handles, "leaf cell")
    return maxd, cells


def draw_blocks_faint(ax, cs):
    for c in cs:
        if c.is_leaf:
            ax.add_patch(Rectangle((c.x1, c.y1), c.x2 - c.x1, c.y2 - c.y1,
                                   fc="#f2f2f2", ec="#bbb", lw=0.4, zorder=1))
        elif c.depth <= 1:
            ax.add_patch(Rectangle((c.x1, c.y1), c.x2 - c.x1, c.y2 - c.y1,
                                   fc="none", ec="#999", lw=0.8, ls=":", zorder=1))


def _layer_legend(ax, names, used):
    hs = [Line2D([0], [0], color=LAYER_COLOR.get(l, "#000"), lw=3,
                 label=f"{names.get(l, 'L%d' % l)} ({'H' if h else 'V'})")
          for l, h in sorted(used.items())]
    _side_legend(ax, hs, "layer")


def draw_nuts(ax, s, names):
    used, wl = {}, 0.0
    for g in s.nuts_result.segments:
        c = LAYER_COLOR.get(g.layer, "#000")
        used[g.layer] = g.horiz
        wl += abs(g.span_hi - g.span_lo)
        if g.horiz:
            ax.plot([g.span_lo, g.span_hi], [g.track_position] * 2, color=c,
                    lw=1.1, alpha=0.85, zorder=5, solid_capstyle="butt")
        else:
            ax.plot([g.track_position] * 2, [g.span_lo, g.span_hi], color=c,
                    lw=1.1, alpha=0.85, zorder=5, solid_capstyle="butt")
    _layer_legend(ax, names, used)
    return wl


def draw_dnuts(ax, s, names):
    horiz = {(g.bundle_id, g.seg_idx): g.horiz for g in s.nuts_result.segments}
    used, wl = {}, 0.0
    for ns in s.detailed_result.net_segments:
        c = LAYER_COLOR.get(ns.layer, "#000")
        h = horiz.get((ns.bundle_id, ns.seg_idx), True)
        used[ns.layer] = h
        wl += abs(ns.span_hi - ns.span_lo)
        if h:
            ax.plot([ns.span_lo, ns.span_hi], [ns.track_position] * 2, color=c,
                    lw=0.35, alpha=0.8, zorder=5)
        else:
            ax.plot([ns.track_position] * 2, [ns.span_lo, ns.span_hi], color=c,
                    lw=0.35, alpha=0.8, zorder=5)
    _layer_legend(ax, names, used)
    return wl


def render(flow, prefix, title=None, dpi=150, label_depth=1):
    """Run the flow and write the panels + JSON.  Returns the metadata dict."""
    title = title or os.path.basename(flow)
    s, log, secs = run_flow(flow)
    cs, hier = components(s)
    if not cs:
        sys.exit("render_design: the flow placed no blocks — nothing to draw")
    w, h = die_extent(s, cs)
    names = s._make_layer_names()
    written = []

    fig, ax = _new_fig(w, h, f"{title} — floorplan" + (", every level" if hier else ""))
    maxd, cells = draw_floorplan(ax, cs, label_depth)
    fig.savefig(prefix + "_fp.png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    written.append(prefix + "_fp.png")

    n_bund = len(s.bundles)
    awl = dwl = None
    if s.nuts_result is not None:
        fig, ax = _new_fig(w, h, f"{title} — NUTS: abstract bus tracks ({n_bund} bundles)")
        draw_blocks_faint(ax, cs)
        awl = draw_nuts(ax, s, names)
        fig.savefig(prefix + "_nuts.png", dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        written.append(prefix + "_nuts.png")
    else:
        print("render_design: no run_nuts in the flow — NUTS panel skipped")

    n_bits = 0
    if s.detailed_result is not None and s.nuts_result is not None:
        n_bits = len(s.detailed_result.net_segments)
        fig, ax = _new_fig(w, h, f"{title} — DetailedNUTS: per-bit wires ({n_bits} bit-wires)")
        draw_blocks_faint(ax, cs)
        dwl = draw_dnuts(ax, s, names)
        fig.savefig(prefix + "_dnuts.png", dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        written.append(prefix + "_dnuts.png")
    else:
        print("render_design: no run_detailed_nuts in the flow — DNUTS panel skipped")

    verdicts = [ln.strip() for ln in log.splitlines()
                if "Success: no violations" in ln or "violation(s)" in ln]
    meta = dict(
        flow=os.path.basename(flow), hierarchical=hier, die=[w, h],
        components=len(cs), leaves=sum(1 for c in cs if c.is_leaf),
        max_depth=maxd, leaf_cells=cells, bundles=n_bund, bit_wires=n_bits,
        abstract_wl=awl, detailed_wl=dwl,
        overlaps=(s.nuts_result.num_overlaps if s.nuts_result is not None else None),
        unplaced=(s.detailed_result.num_unplaced if s.detailed_result is not None else None),
        verdicts=verdicts, seconds=round(secs, 1), panels=written)
    with open(prefix + "_meta.json", "w") as f:
        json.dump(meta, f, indent=1)
    return meta


def main():
    ap = argparse.ArgumentParser(description="Render a whole design: floorplan, NUTS, DetailedNUTS.")
    ap.add_argument("flow", help="path to the .buda flow")
    ap.add_argument("--out", default=None, help="output prefix (default: flow stem)")
    ap.add_argument("--title", default=None, help="caption prefix (default: flow basename)")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--label-depth", type=int, default=1,
                    help="label containers at this depth and above")
    a = ap.parse_args()
    prefix = a.out or os.path.splitext(os.path.basename(a.flow))[0]
    meta = render(a.flow, prefix, a.title, a.dpi, a.label_depth)
    print(json.dumps(meta, indent=1))
    for p in meta["panels"]:
        print(f"[render_design] wrote {p}")


if __name__ == "__main__":
    main()
