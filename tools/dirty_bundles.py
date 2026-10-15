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

"""Which bundles of a routed result are dirty, and why.

Runs a recorded flow (`BUDA_RECORD`, see `tools/tcl2buda.py`) to its end,
then reads the finished session:

  * a bundle is DIRTY when it ends with bits unplaced (detailed NUTS ran) or
    with a track overlap (abstract NUTS);
  * every dirty bundle gets ONE cause, the first that applies:
      doomed     its seat's real signal-track supply (the same census
                 `check_design` prints) is below its bit count, so every bit
                 strands -- a width fact, nothing to do with contention
      contended  its seat could host it but the tracks were taken
                 (DetailedNUTS' "reservation conflict")
      overlap    two bus segments share a track (abstract NUTS)
  * the report groups the dirty bundles by the cell that holds them and the
    layer they sit on, which is where a REPEATED cause shows as one line.

Writes `<prefix>_dirty.png` (the floorplan with each dirty segment drawn in
its cause's colour, beside the counts) and `<prefix>_dirty.json`, and prints
the table.

Usage:
  tools/dirty_bundles.py <flow.buda> [--out PREFIX] [--top N]
"""
import argparse
import collections
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import render_design as rd                              # noqa: E402

plt = rd.plt
CAUSE_COLOR = {"doomed": "#d62728", "contended": "#ff7f0e", "overlap": "#1f77b4"}


def analyse(s):
    """One record per dirty bundle segment: {bundle, seg, cause, layer, ...}."""
    nuts = s.nuts_result
    det = s.detailed_result
    by_id = {}
    for w in s.bundles:
        by_id.setdefault(w.input.original_bundle.id, w)
    tseg = {(t.bundle_id, t.seg_idx): t for t in nuts.segments} if nuts else {}
    placed = collections.Counter()
    if det is not None:
        for n in det.net_segments:
            if not n.is_shield:
                placed[(n.bundle_id, n.seg_idx)] += 1
    doomed = {}
    try:
        for t, bits, tracks, _lock, _b in s._doomed_seats():
            doomed[(t.bundle_id, t.seg_idx)] = (bits, tracks)
    except Exception:                                   # pragma: no cover
        pass
    names = {}
    for (bid, _k), _t in tseg.items():
        w = by_id.get(bid)
        if w is not None:
            names[bid] = len(w.input.original_bundle.net_names)
    recs = []
    for (bid, k), t in tseg.items():
        w = by_id.get(bid)
        if w is None:
            continue
        ob = w.input.original_bundle
        nbits = names[bid]
        lost = nbits - placed[(bid, k)] if det is not None else 0
        cause = None
        if (bid, k) in doomed:
            cause = "doomed"
        elif lost > 0:
            cause = "contended"
        if cause is None:
            continue
        recs.append(dict(
            bundle=bid, seg=k, cause=cause, layer=t.layer, bits=nbits,
            unplaced=lost if det is not None else 0,
            window=[t.interval_lo, t.interval_hi],
            window_width=t.interval_hi - t.interval_lo,
            horiz=bool(t.horiz), pos=t.track_position,
            span=[t.span_lo, t.span_hi], cell=ob.cell_context or "(top)",
            level=ob.level, instances=list(ob.instances)[:2],
            reason=ob.reason, supply=(doomed.get((bid, k)) or (None, None))[1]))
    return recs


def summarize(recs):
    g = collections.defaultdict(lambda: [0, 0, 0])
    for r in recs:
        a = g[(r["cause"], r["cell"], r["layer"], r["bits"])]
        a[0] += 1
        a[1] += r["unplaced"]
    return g


def draw(s, recs, out, title):
    cs, _bdb = rd.components(s)
    bounds, die = rd.die_extent(s, cs)
    fig, axes = plt.subplots(1, 2, figsize=(20, 8.4),
                             gridspec_kw={"width_ratios": [3.1, 1]})
    ax = axes[0]
    rd.draw_blocks_faint(ax, cs)
    ax.add_patch(rd.Rectangle((0, 0), die[0], die[1], fc="white", ec="#222",
                              lw=1.2, zorder=0))
    ax.set_xlim(bounds[0], bounds[2])
    ax.set_ylim(bounds[1], bounds[3])
    ax.set_aspect("equal")
    for r in recs:
        c = CAUSE_COLOR[r["cause"]]
        a, b = r["span"]
        p = r["pos"]
        if r["horiz"]:
            ax.plot([a, b], [p, p], color=c, lw=3.2, solid_capstyle="butt")
        else:
            ax.plot([p, p], [a, b], color=c, lw=3.2, solid_capstyle="butt")
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    n = collections.Counter(r["cause"] for r in recs)
    bits = collections.Counter()
    for r in recs:
        bits[r["cause"]] += r["unplaced"]
    ax.legend(handles=[rd.Line2D([0], [0], color=CAUSE_COLOR[k], lw=3,
                                 label=f"{k}: {n[k]} seg, {bits[k]:,} bits")
                       for k in CAUSE_COLOR if n[k]], loc="lower right")
    # right: unplaced bits by cell, stacked by cause
    ax2 = axes[1]
    cells = collections.defaultdict(collections.Counter)
    for r in recs:
        cells[r["cell"]][r["cause"]] += r["unplaced"] or r["bits"]
    order = sorted(cells, key=lambda c: -sum(cells[c].values()))[:12]
    left = [0] * len(order)
    for cause in CAUSE_COLOR:
        vals = [cells[c][cause] for c in order]
        ax2.barh(range(len(order)), vals, left=left, color=CAUSE_COLOR[cause],
                 label=cause)
        left = [a + b for a, b in zip(left, vals)]
    ax2.set_yticks(range(len(order)))
    ax2.set_yticklabels(order)
    ax2.invert_yaxis()
    ax2.set_xlabel("bits")
    ax2.set_title("by holding cell")
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("flow")
    ap.add_argument("--out", default=None)
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    prefix = a.out or os.path.splitext(os.path.basename(a.flow))[0]
    s, _log, secs = rd.run_flow(a.flow)
    recs = analyse(s)
    det = s.detailed_result
    tot = det.num_unplaced if det is not None else None
    print(f"[dirty_bundles] ran in {secs:.0f}s; {len(recs)} dirty segment(s); "
          f"unplaced total {tot}, attributed "
          f"{sum(r['unplaced'] for r in recs)}")
    g = summarize(recs)
    print(f"{'cause':10s} {'cell':14s} {'layer':>5s} {'bits/bundle':>11s} "
          f"{'segs':>5s} {'unplaced':>9s}")
    for (cause, cell, layer, bits), (n, up, _x) in sorted(
            g.items(), key=lambda kv: -kv[1][1])[:a.top]:
        print(f"{cause:10s} {cell:14s} {layer:>5d} {bits:>11d} {n:>5d} {up:>9d}")
    with open(prefix + "_dirty.json", "w") as f:
        json.dump(recs, f, indent=1)
    draw(s, recs, prefix + "_dirty.png", f"{os.path.basename(prefix)}: dirty bundle segments")
    print(f"[dirty_bundles] wrote {prefix}_dirty.png, {prefix}_dirty.json")


if __name__ == "__main__":
    main()
