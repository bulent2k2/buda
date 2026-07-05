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

"""Render a single pinned topology with its NUTS and DetailedNUTS results.

Drives a .buda flow through setup + bundling + topology generation, pins one
bundle to a chosen candidate, then runs the planner, abstract NUTS and detailed
NUTS — printing `dump_topologies <bundle> --conn` and rendering a three-panel
image:

  TOPOLOGY              NUTS                       DETAILED NUTS
  abstract segments,    abstract bus tracks at     per-bit wires on concrete
  slide ranges, and     placed track positions     signal tracks
  busterm vs containment

Usage:
  tools/render.py <flow.buda> --bundle <id> --topo <id> [--out PREFIX] [--zoom] [--no-dnuts]

  <id>       bundle hint (the first arg you'd pass to select_topology / dump_topologies)
  --topo     1-based candidate index (as shown by dump_topologies)
  --out      output image path (default: <flow stem>_b<bundle>_t<topo>.png)
  --zoom     fit the view to the bundle's routing extent instead of the whole die
  --no-dnuts render only TOPOLOGY + NUTS (skip DetailedNUTS) — no track pattern
             (def_track_pattern) needed in the flow, and a cleaner 2-panel image

Example:
  PYTHONPATH=build:tools tools/render.py flow/big_data_test/big2/b34_bus_028.buda \
      --bundle 1 --topo 1 --out /tmp/b34.png --zoom
"""
import argparse
import contextlib
import io
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (os.path.join(_ROOT, "src"), os.path.join(_ROOT, "build")):
    if p not in sys.path:
        sys.path.insert(0, p)

import buda          # noqa: E402
import buda_cli      # noqa: E402

# Commands re-run by this tool (we pin first, then place ourselves) or that open
# a GUI — skipped while replaying the flow's setup.
_SKIP = {
    "run_planner", "run_nuts", "run_nuts_on_layer", "run_detailed_nuts",
    "check_connectivity", "visualize", "visualize_topologies",
    "select_topology", "select_topologies", "dump_topologies", "report_overhead",
    "exit",
}

# Distinct colours per metal layer id (falls back to a cycle for unknown ids).
_PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b",
            "#17becf", "#bcbd22", "#e377c2", "#7f7f7f"]


def _run_flow(flow_path, bundle, topo, no_dnuts=False):
    """Replay the flow's setup, pin (bundle, topo), then plan + NUTS (+ DNUTS)."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with open(flow_path) as f:
        raw = f.readlines()
    cwd = os.getcwd()
    os.chdir(os.path.dirname(os.path.abspath(flow_path)) or ".")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            for line in raw:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                if line.split()[0] in _SKIP:
                    continue
                s.do_command(line)
            # Pin the requested candidate, then place.
            s.do_command(f"select_topology {bundle} {topo}")
            s.do_command("run_planner")
            s.do_command("run_nuts")
            if not no_dnuts:
                s.do_command("run_detailed_nuts")
    finally:
        os.chdir(cwd)
    return s


def _dump_conn(s, w):
    """Capture `dump_topologies <net-hint> --conn` for one bundle.

    select_topology keys off the numeric bundle id, but dump_topologies filters
    by net-name prefix — so hint with the bundle's first net name.
    """
    nets = w.input.original_bundle.get_net_names()
    hint = nets[0] if nets else ""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(f"dump_topologies {hint} --conn".strip())
    return buf.getvalue()


def _find_wrapper(s, bundle):
    """Resolve the bundle hint to its wrapper (by id, else by 1-based position)."""
    for w in s.bundles:
        if str(w.input.original_bundle.id) == str(bundle):
            return w
    try:
        return s.bundles[int(bundle) - 1]
    except (ValueError, IndexError):
        return s.bundles[0] if s.bundles else None


def _layer_color(lid):
    return _PALETTE[lid % len(_PALETTE)]


def _layer_name(s, lid):
    try:
        names = s._make_layer_names()
        if lid in names:
            return names[lid]
    except Exception:
        pass
    return f"L{lid}"


def _spans_block(horiz, along_lo, along_hi, perp, rect):
    if horiz:
        return rect.y1 <= perp <= rect.y2 and along_lo <= rect.x2 and along_hi >= rect.x1
    return rect.x1 <= perp <= rect.x2 and along_lo <= rect.y2 and along_hi >= rect.y1


def _draw_blocks(ax, blocks, highlight):
    for name, r in blocks:
        fc, ec = ("#ffd27f", "#d08a00") if name in highlight else ("#eef1f5", "#9aa6b2")
        ax.add_patch(Rectangle((r.x1, r.y1), r.x2 - r.x1, r.y2 - r.y1,
                               fc=fc, ec=ec, lw=1.4, alpha=0.7, zorder=1))
        ax.text((r.x1 + r.x2) / 2, (r.y1 + r.y2) / 2, name, ha="center", va="center",
                fontsize=7, color="#555", zorder=2, clip_on=True)


def _draw_topology(ax, s, w, blocks):
    """Abstract topology: segments at nominal positions, slide bands, conn kind."""
    topo = w.input.candidates[w.plan.selected_topology_index]
    ct = buda.ConnTopology()
    ct.build(topo, s.fp)
    segs = ct.segs()
    busterm = set()
    for seg in segs:
        for c in seg.conns:
            if c.kind == buda.SegConnKind.BUSTERM:
                busterm.add(c.block_name)
    contained = set()
    for seg in segs:
        for name, r in blocks:
            if name not in busterm and _spans_block(seg.horiz, seg.along_lo,
                                                     seg.along_hi, seg.perp_pos, r):
                contained.add(name)
    _draw_blocks(ax, blocks, contained)
    for i, seg in enumerate(segs):
        col = _layer_color(seg.layer_id)
        if seg.horiz:
            y = seg.perp_pos
            ax.add_patch(Rectangle((seg.along_lo, seg.perp_lo), seg.along_hi - seg.along_lo,
                                   seg.perp_hi - seg.perp_lo, fc=col, ec="none", alpha=0.13, zorder=3))
            ax.plot([seg.along_lo, seg.along_hi], [y, y], color=col, lw=3, zorder=5,
                    solid_capstyle="round")
            ax.text((seg.along_lo + seg.along_hi) / 2, y, f"s{i}", fontsize=7,
                    ha="center", va="bottom", color=col, zorder=6)
        else:
            x = seg.perp_pos
            ax.add_patch(Rectangle((seg.perp_lo, seg.along_lo), seg.perp_hi - seg.perp_lo,
                                   seg.along_hi - seg.along_lo, fc=col, ec="none", alpha=0.13, zorder=3))
            ax.plot([x, x], [seg.along_lo, seg.along_hi], color=col, lw=3, zorder=5,
                    solid_capstyle="round")
            ax.text(x, (seg.along_lo + seg.along_hi) / 2, f"s{i}", fontsize=7,
                    ha="left", va="center", color=col, zorder=6)
    ax.set_title(f"TOPOLOGY — {topo.type}\n{len(topo.segments)} segs · wl={topo.estimated_wirelength}"
                 "\norange = containment · faint band = slide range", fontsize=9)
    return contained


def _involved_blocks(s, w, blocks):
    """Blocks the bundle connects to: busterm (driver/receiver) + containment."""
    topo = w.input.candidates[w.plan.selected_topology_index]
    ct = buda.ConnTopology()
    ct.build(topo, s.fp)
    names = set()
    for seg in ct.segs():
        for c in seg.conns:
            if c.kind == buda.SegConnKind.BUSTERM:
                names.add(c.block_name)
    bt = set(names)
    for seg in ct.segs():
        for name, r in blocks:
            if name not in bt and _spans_block(seg.horiz, seg.along_lo,
                                                seg.along_hi, seg.perp_pos, r):
                names.add(name)
    return [(n, r) for n, r in blocks if n in names]


def _draw_nuts(ax, s, bundle_id, blocks, highlight):
    _draw_blocks(ax, blocks, highlight)
    segs = [g for g in s.nuts_result.segments if g.bundle_id == bundle_id]
    for g in segs:
        col = _layer_color(g.layer)
        if g.horiz:
            y = g.track_position
            ax.add_patch(Rectangle((g.span_lo, g.interval_lo), g.span_hi - g.span_lo,
                                   g.interval_hi - g.interval_lo, fc=col, ec="none", alpha=0.12, zorder=3))
            ax.plot([g.span_lo, g.span_hi], [y, y], color=col, lw=4.5, zorder=5, solid_capstyle="round")
            ax.text((g.span_lo + g.span_hi) / 2, y, f"s{g.seg_idx} {_layer_name(s, g.layer)}",
                    fontsize=7, ha="center", va="bottom", color=col, zorder=6)
        else:
            x = g.track_position
            ax.add_patch(Rectangle((g.interval_lo, g.span_lo), g.interval_hi - g.interval_lo,
                                   g.span_hi - g.span_lo, fc=col, ec="none", alpha=0.12, zorder=3))
            ax.plot([x, x], [g.span_lo, g.span_hi], color=col, lw=4.5, zorder=5, solid_capstyle="round")
            ax.text(x, (g.span_lo + g.span_hi) / 2, f"s{g.seg_idx} {_layer_name(s, g.layer)}",
                    fontsize=7, ha="left", va="center", color=col, zorder=6, rotation=90)
    nr = s.nuts_result
    ax.set_title(f"NUTS (abstract bus tracks)\n{len(segs)} segs · {nr.num_overlaps} overlaps · "
                 f"{nr.num_violations} violations\nthick = bus centre · band = Hanan interval", fontsize=9)


def _draw_dnuts(ax, s, bundle_id, blocks, highlight):
    _draw_blocks(ax, blocks, highlight)
    horiz_of = {g.seg_idx: g.horiz for g in s.nuts_result.segments if g.bundle_id == bundle_id}
    nets = [ns for ns in s.detailed_result.net_segments if ns.bundle_id == bundle_id]
    for ns in nets:
        col = _layer_color(ns.layer)
        if horiz_of.get(ns.seg_idx, True):
            y = ns.track_position
            ax.plot([ns.span_lo, ns.span_hi], [y, y], color=col, lw=1.4, zorder=5)
        else:
            x = ns.track_position
            ax.plot([x, x], [ns.span_lo, ns.span_hi], color=col, lw=1.4, zorder=5)
    ax.set_title(f"DetailedNUTS (per-bit wires)\n{len(nets)} bit-wires · "
                 f"{s.detailed_result.num_unplaced} unplaced\neach thin line = one bit on a signal track",
                 fontsize=9)


def _balance(x0, x1, y0, y1, max_aspect=3.0):
    """Pad the thinner axis so the (equal-aspect) panel isn't a razor strip."""
    w, h = x1 - x0, y1 - y0
    if w > max_aspect * h:
        pad = (w / max_aspect - h) / 2
        y0, y1 = y0 - pad, y1 + pad
    elif h > max_aspect * w:
        pad = (h / max_aspect - w) / 2
        x0, x1 = x0 - pad, x1 + pad
    return x0, x1, y0, y1


def _extent(blocks, focus, segs_xy, zoom):
    """View extent.  --zoom frames the bundle's driver/receiver+containment blocks
    (`focus`) fully, plus its wires; otherwise the whole die."""
    if zoom and focus:
        xs = [r.x1 for _, r in focus] + [r.x2 for _, r in focus] + [p[0] for p in segs_xy]
        ys = [r.y1 for _, r in focus] + [r.y2 for _, r in focus] + [p[1] for p in segs_xy]
        mx = max(60, (max(xs) - min(xs)) * 0.06)
        my = max(60, (max(ys) - min(ys)) * 0.06)
        return _balance(min(xs) - mx, max(xs) + mx, min(ys) - my, max(ys) + my)
    xs = [r.x1 for _, r in blocks] + [r.x2 for _, r in blocks]
    ys = [r.y1 for _, r in blocks] + [r.y2 for _, r in blocks]
    m = max(50, (max(xs) - min(xs)) * 0.04)
    return (min(xs) - m, max(xs) + m, min(ys) - m, max(ys) + m)


def main():
    ap = argparse.ArgumentParser(description="Render a pinned topology + NUTS + DetailedNUTS.")
    ap.add_argument("flow", help="path to the .buda flow")
    ap.add_argument("--bundle", required=True, help="bundle hint (select_topology first arg)")
    ap.add_argument("--topo", required=True, help="1-based candidate index to pin")
    ap.add_argument("--out", default=None, help="output PNG path")
    ap.add_argument("--zoom", action="store_true", help="fit view to the bundle's routing extent")
    ap.add_argument("--no-dnuts", action="store_true",
                    help="render only TOPOLOGY + NUTS (skip DetailedNUTS; no track pattern needed)")
    args = ap.parse_args()

    s = _run_flow(args.flow, args.bundle, args.topo, no_dnuts=args.no_dnuts)
    w = _find_wrapper(s, args.bundle)
    if w is None:
        sys.exit("error: no bundles produced by the flow")
    print(_dump_conn(s, w))

    bid = w.input.original_bundle.id
    blocks = list(s.fp.get_all_blocks())

    n_panels = 2 if args.no_dnuts else 3
    fig, axes = plt.subplots(1, n_panels, figsize=(7 * n_panels, 7))
    contained = _draw_topology(axes[0], s, w, blocks)
    _draw_nuts(axes[1], s, bid, blocks, contained)
    if not args.no_dnuts:
        _draw_dnuts(axes[2], s, bid, blocks, contained)

    # Shared view extent (zoom to the bundle's NUTS segments when --zoom).
    segs_xy = []
    for g in s.nuts_result.segments:
        if g.bundle_id != bid:
            continue
        if g.horiz:
            segs_xy += [(g.span_lo, g.track_position), (g.span_hi, g.track_position)]
        else:
            segs_xy += [(g.track_position, g.span_lo), (g.track_position, g.span_hi)]
    focus = _involved_blocks(s, w, blocks)
    x0, x1, y0, y1 = _extent(blocks, focus, segs_xy, args.zoom)
    for ax in axes:
        ax.set_aspect("equal")
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.invert_yaxis()
        ax.grid(True, ls=":", alpha=0.4)

    topo_type = w.input.candidates[w.plan.selected_topology_index].type
    fig.suptitle(f"{os.path.basename(args.flow)} · bundle {bid} · topo {args.topo} ({topo_type})",
                 fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    out = args.out or f"{os.path.splitext(os.path.basename(args.flow))[0]}_b{args.bundle}_t{args.topo}.png"
    fig.savefig(out, dpi=115)
    print(f"[render] wrote {out}")


if __name__ == "__main__":
    main()
