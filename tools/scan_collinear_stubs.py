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

"""Classify COLLINEAR STUB pairs in generated topologies.

Two legs of a candidate can leave one point along the same axis with the
shorter lying entirely inside the longer.  The interesting question is not
whether that happens — it does — but whether the shorter leg is REMOVABLE:

    REDUNDANT    the long leg already serves the short leg's block, so the
                 short one adds no coverage.  Two ways:
                   SAME_BLOCK  both legs tap the same block
                   CROSSES     the long leg crosses that block's INTERIOR
    LOAD_BEARING the long leg does NOT serve it — most often because it
                 merely GRAZES the block's edge (the two blocks share an
                 x1/y1, so the stubs are collinear by coincidence of
                 placement).  Removing the short leg would open that block.

The distinction is the whole point.  "Collinear" is a geometric observation;
"redundant" is a claim about the block contract, and BUDA's coverage rule is
INTERIOR overlap, not abutment — a wire running along a block's edge does not
cover it, and `Topology::pass_through_count` agrees.

Reports per flow, and a corpus-wide roll-up.  Read-only: nothing is mutated,
no flow is re-planned, and unselected candidates are inspected exactly as
generated.

Usage (from the repo root):
    PYTHONPATH=build:src:tools python3 tools/scan_collinear_stubs.py [flow ...]

With no arguments it scans a default set spanning flat and hier, small and
chip-scale.
"""
import contextlib
import io
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_FLOWS = [
    # the two MST vehicles (all-redundant, by construction)
    "flow/mst_shared_leg_prefix.buda",
    "flow/mst_shared_leg_suffix.buda",
    # small flat
    "flow/four_blocks.buda",
    "flow/channel_stress.buda",          # a NEGATIVE: no pairs at all
    "demo/comprehensive_demo.buda",
    # MULTI-RECT / TEG blocks, so the rects-not-bbox coverage path is actually
    # exercised rather than merely written (Codex #741)
    "flow/lShape1.buda",
    "flow/teg1.buda",
    # hier
    "flow/rnr/mix.buda",
    "flow/rnr/mix2.buda",
    "flow/rnr/mix2_topdown_refine.buda",
    # large flat
    "flow/big_data_test/big.buda",
    "flow/big_data_test/bigHalf.buda",
    "flow/big_data_test/big2/big2.buda",
]


def _ray(anchor, far):
    """A leg seen as a ray from `anchor`: (is_horizontal, sign, length).

    None when it is not axis-aligned or has no length.  Anchoring on a POINT
    rather than on `seg.start` is what lets this see a pair however the two
    legs are oriented — `compute_mst` emits `u < v` and trunk stubs are built
    outward, so which end is shared is not a property the shape cares about.
    """
    h = anchor.y == far.y
    v = anchor.x == far.x
    if h == v:
        return None
    d = (far.x - anchor.x) if h else (far.y - anchor.y)
    if d == 0:
        return None
    return h, d > 0, abs(d)


def contained_pairs(topo):
    """(short_idx, long_idx, shared_point) for every collinear containment."""
    out = set()
    segs = topo.segments
    for i, a in enumerate(segs):
        for j, b in enumerate(segs):
            if i == j:
                continue
            for pa, qa in ((a.start, a.end), (a.end, a.start)):
                ra = _ray(pa, qa)
                if ra is None:
                    continue
                for pb, qb in ((b.start, b.end), (b.end, b.start)):
                    if (pa.x, pa.y) != (pb.x, pb.y):
                        continue
                    rb = _ray(pb, qb)
                    if rb is None or rb[0] != ra[0] or rb[1] != ra[1]:
                        continue
                    if ra[2] < rb[2]:
                        out.add((i, j, (pa.x, pa.y)))
    return sorted(out)


def _tapped_block(topo, seg_idx, shared_pt):
    """The block this leg taps at its FAR end (the end away from the shared
    point).  None when that end is a junction rather than a landing."""
    eps = topo.seg_busterms.get(seg_idx)
    if eps is None:
        return None
    g = topo.segments[seg_idx]
    start_is_shared = (g.start.x, g.start.y) == shared_pt
    bt = eps[1] if start_is_shared else eps[0]
    return bt.block_name if bt else None


def _crosses_interior(seg, r):
    """Does `seg` cross the rect's INTERIOR (not merely graze an edge)?

    Matches BUDA's coverage rule: abutment does not cover.  Strict inequality
    on the perpendicular axis is what separates "runs along the left edge"
    from "runs through the middle".

    `r` is one RECT, never a union bbox — see `_covers_block`.
    """
    horiz = seg.start.y == seg.end.y
    perp = seg.start.y if horiz else seg.start.x
    lo, hi = sorted((seg.start.x, seg.end.x) if horiz
                    else (seg.start.y, seg.end.y))
    p_lo, p_hi = (r.y1, r.y2) if horiz else (r.x1, r.x2)
    a_lo, a_hi = (r.x1, r.x2) if horiz else (r.y1, r.y2)
    return p_lo < perp < p_hi and lo < a_hi and hi > a_lo


class _Rect:
    """The 4-tuple `get_block_rects` returns, given a rect's attribute names."""
    __slots__ = ("x1", "y1", "x2", "y2")

    def __init__(self, t):
        self.x1, self.y1, self.x2, self.y2 = t


def _covers_block(seg, fp, name):
    """Does `seg` cross any REAL rectangle of the block?

    A multi-rect / TEG block's `get_block_bounds()` is only the union bbox, so
    it spans notches and gaps that hold no block material.  A leg through such
    a gap crosses the BBOX while covering nothing — which would score a
    load-bearing stub as removable, the one direction that could inflate the
    redundant count.  So test the rects and fall back to the bounds only when
    a block has none, exactly as the production coverage paths do.
    """
    rects = fp.get_block_rects(name)
    if not rects:
        return _crosses_interior(seg, fp.get_block_bounds(name))
    return any(_crosses_interior(seg, _Rect(t)) for t in rects)


def classify(topo, pair, fp):
    """REDUNDANT:<why> or LOAD_BEARING:<why> for one containment pair."""
    si, li, pt = pair
    b_short = _tapped_block(topo, si, pt)
    b_long = _tapped_block(topo, li, pt)

    if b_short is None:
        return "LOAD_BEARING:short_leg_taps_nothing"
    if b_short == b_long:
        return "REDUNDANT:same_block"
    if not fp.has_block(b_short):
        return "LOAD_BEARING:block_not_in_frame"
    if _covers_block(topo.segments[li], fp, b_short):
        return "REDUNDANT:long_leg_crosses_it"
    return "LOAD_BEARING:long_leg_only_grazes"


def solve(flow):
    import buda_cli
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        try:
            s.do_command(f"source {flow}")
        except SystemExit:
            pass
    return s


def scan(flow):
    s = solve(flow)
    tally = Counter()
    examples = {}
    n_cand = n_carrier = 0

    # Each wrapper is classified against the floorplan ITS candidates were
    # generated in, not the session's.  A pre-expansion cell-local template
    # and a cross-level bundle live in their own frames, so `s.fp` would
    # report their taps as blocks-not-in-frame — or, worse, silently test a
    # same-named block from another frame.  This is the resolver
    # `dump_topologies` uses, so the scan reads geometry the same way
    # topology inspection does (Codex #741).
    topo_fp = s._make_topo_fp_resolver()

    for w in s.bundles:
        bid = w.input.original_bundle.id
        sel = w.plan.selected_topology_index if w.plan else -1
        fp = topo_fp(w)
        for idx, t in enumerate(w.input.candidates):
            n_cand += 1
            pairs = contained_pairs(t)
            if not pairs:
                continue
            n_carrier += 1
            for p in pairs:
                verdict = classify(t, p, fp)
                tally[verdict] += 1
                if idx == sel:
                    # Cross-tabbed, not just counted: a pair in a SELECTED
                    # candidate only costs metal if it is also REDUNDANT, and
                    # that is the one number that says whether any of this
                    # reaches a routed design.
                    tally["SELECTED+" + verdict.split(":")[0]] += 1
                    examples.setdefault("SELECTED+" + verdict,
                                        (bid, idx + 1, t.type, p))
                examples.setdefault(verdict, (bid, idx + 1, t.type, p))
    return tally, examples, n_cand, n_carrier


def main():
    flows = sys.argv[1:] or DEFAULT_FLOWS
    total = Counter()
    print(f"{'flow':<42} {'cands':>6} {'carriers':>9}  verdicts")
    print("-" * 108)
    for f in flows:
        path = ROOT / f
        if not path.exists():
            print(f"{f:<42}    -- not found --")
            continue
        tally, examples, n_cand, n_carrier = scan(path)
        total.update(tally)
        detail = ", ".join(f"{k}={v}" for k, v in sorted(tally.items())) or "none"
        print(f"{f:<42} {n_cand:>6} {n_carrier:>9}  {detail}")
        for k, ex in sorted(examples.items()):
            print(f"{'':<42} {'':>16}  e.g. {k}: bundle {ex[0]} topo {ex[1]} "
                  f"{ex[2]} segs{ex[3][:2]}")

    print("\n" + "=" * 108)
    print("CORPUS ROLL-UP")
    red = sum(v for k, v in total.items() if k.startswith("REDUNDANT"))
    load = sum(v for k, v in total.items() if k.startswith("LOAD_BEARING"))
    for k, v in sorted(total.items()):
        print(f"  {k:<44} {v:>6}")
    print(f"\n  removable (REDUNDANT)     {red:>6}")
    print(f"  NOT removable (LOAD_BEARING) {load:>6}")
    if red + load:
        print(f"  redundant share: {100.0 * red / (red + load):.1f}%")


if __name__ == "__main__":
    main()
