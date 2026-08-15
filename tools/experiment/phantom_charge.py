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

"""Does the same-bundle DOUBLE CHARGE inflate what other bundles see?

`CongestionPlanner::apply_segment` charges every segment independently at
`eff_width + track_pitch`, so two same-bundle segments whose metal COINCIDES —
same layer, collinear, overlapping span — charge the same bands twice.  NUTS
lets same-bundle segments share a track (nuts.cpp: "same-bundle never
conflicts"), so the books can say two buses where the metal is one.

That mischarge cannot demote the candidate carrying it: the candidate's own
score is `max_over_segments(...)`, and a short duplicate is almost never the
argmax (see twin_cost_collinear.py).  Where it could bite is on OTHER bundles —
a committed bundle's charge persists in `cuts_`, and every later bundle reads it
through `cong_cost_segment`, through `kPeak`, and above all through OVERFLOW,
which is a hard STRICT constraint.  Phantom demand can make a band look full and
push somebody else's candidate out of the STRICT tier entirely.

Two questions, cheapest first, and the first can refute the whole thing:

  P0 PREMISE.  "May share a track" is not "does share".  Did NUTS actually
     place the twins at the same `track_position`?
       co-placed    -> one wire charged twice   -> phantom, P1 matters
       placed apart -> two wires charged twice  -> CORRECT, nothing to fix
     The second outcome is not hypothetical (see the measurement below), which
     is why a blanket "dedup same-bundle charge" fix would be wrong.

  P1 CENSUS.  How often does this occur in COMMITTED geometry — the only
     geometry that persists in `cuts_` and that other bundles can read?

Deliberately NOT answered here: HOW MUCH any one instance inflates a specific
band.  A first cut tried, by mapping each duplicate onto the cut/band it charges
— and got it wrong in four separate ways (Codex #754): it picked the grid of the
WRONG AXIS (`for_each_cut_` passes `is_vcut = is_h`, so an H segment's bands
index `y_grid_` and a V segment's `x_grid_`, the opposite of the obvious
reading), it never filtered cuts by DIRECTION, it used an inclusive band test
where `find_band` is half-open, and it read the topology's nominal perp while
`commit_plan` charges through `plan.seg_perp` and may SPREAD one segment over
several weighted bands (`band_span_charge`).

That is the trap this file warns about below: a partial reimplementation of
`for_each_band_w` yields a confident wrong answer.  Quantifying per-band impact
belongs in the engine, which already owns that arithmetic — see the note at the
bottom of tools/experiment/ReadMe.md.  What survives here needs none of it:
both numbers below are counts over geometry, and neither touches a band.

Scoped to SELECTED topologies on purpose.  A duplicate inside a candidate that
then loses is charged into a scoring overlay and dies with the candidate; it
never reaches the committed field.

Read-only: nothing is mutated, no flow is re-planned.

Usage (from the repo root):
    PYTHONPATH=build:src:tools python3 tools/experiment/phantom_charge.py [flow ...]
"""
import contextlib
import io
import sys
from collections import Counter
from pathlib import Path

BUDA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BUDA / "tools"))

FLOWS = [
    "flow/four_blocks.buda",
    "demo/comprehensive_demo.buda",
    "flow/rnr/mix.buda",
    "flow/rnr/mix2.buda",
    "flow/rnr/mix2_topdown_refine.buda",
    "flow/big_data_test/big.buda",
    "flow/big_data_test/bigHalf.buda",
    "flow/big_data_test/big2/big2.buda",
]


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


def orient(sg):
    """'H', 'V', or None for a degenerate/diagonal segment."""
    if sg.start.y == sg.end.y and sg.start.x != sg.end.x:
        return "H"
    if sg.start.x == sg.end.x and sg.start.y != sg.end.y:
        return "V"
    return None


def perp_of(sg, o):
    return sg.start.y if o == "H" else sg.start.x


def span_of(sg, o):
    return sorted((sg.start.x, sg.end.x) if o == "H"
                  else (sg.start.y, sg.end.y))


def coincident_pairs(topo, seg_layers):
    """Segment pairs whose METAL coincides, so the books charge it twice.

    Returns (i, j, overlap_lo, overlap_hi, orient, perp, layer).

    All four conditions are load-bearing:
      - same ASSIGNED layer   (different layers are different metal)
      - same orientation      (a crossing is not a duplicate)
      - same perpendicular    (parallel-but-apart wires each need their own
                               width, so charging both is CORRECT)
      - STRICTLY overlapping spans (segments that merely touch end-to-end
                               share no metal)
    """
    out = []
    segs = topo.segments
    for i in range(len(segs)):
        oi = orient(segs[i])
        if oi is None:
            continue
        li = seg_layers[i] if i < len(seg_layers) else -1
        if li < 0:
            continue
        for j in range(i + 1, len(segs)):
            if orient(segs[j]) != oi:
                continue
            lj = seg_layers[j] if j < len(seg_layers) else -1
            if lj != li:
                continue
            if perp_of(segs[i], oi) != perp_of(segs[j], oi):
                continue
            a_lo, a_hi = span_of(segs[i], oi)
            b_lo, b_hi = span_of(segs[j], oi)
            lo, hi = max(a_lo, b_lo), min(a_hi, b_hi)
            if lo < hi:
                out.append((i, j, lo, hi, oi, perp_of(segs[i], oi), li))
    return out


def run(flow, p0, totals):
    s = solve(flow)
    nr = getattr(s, "nuts_result", None)
    if nr is None:
        return
    placed = {(ts.bundle_id, ts.seg_idx): ts.track_position
              for ts in nr.segments if ts.placed}

    for w in s.bundles:
        if not w.plan or w.plan.selected_topology_index < 0:
            continue
        t = w.input.candidates[w.plan.selected_topology_index]
        seg_layers = list(w.plan.seg_layers)
        bid = w.input.original_bundle.id
        totals["bundles"] += 1
        totals["segments"] += len(t.segments)

        for (i, j, _lo, _hi, _o, _perp, _layer) in coincident_pairs(t, seg_layers):
            totals["pairs"] += 1
            pi, pj = placed.get((bid, i)), placed.get((bid, j))
            if pi is None or pj is None:
                p0["one or both unplaced"] += 1
            elif abs(pi - pj) >= 1e-9:
                p0["placed APART (charge correct)"] += 1
            else:
                p0["co-placed (phantom)"] += 1


def main():
    flows = sys.argv[1:] or FLOWS
    p0, totals = Counter(), Counter()
    for f in flows:
        p = BUDA / f
        if not p.exists():
            print(f"-- not found: {f}")
            continue
        run(p, p0, totals)
        print(f"scanned {f}")

    print("\n" + "=" * 74)
    print("P0 — did NUTS co-place the coincident twins?")
    tot = sum(p0.values())
    if not tot:
        print("  no coincident same-layer collinear pairs in committed geometry")
    for k, v in p0.most_common():
        print(f"  {k:<34} {v:>6}  ({100.0 * v / tot:.1f}%)")

    print("\nP1 — how often, in the geometry other bundles actually read?")
    segs = totals["segments"]
    print(f"  committed bundles                 {totals['bundles']:>6}")
    print(f"  committed segments                {segs:>6}")
    print(f"  coincident pairs                  {totals['pairs']:>6}"
          f"   ({100.0 * totals['pairs'] / max(1, segs):.4f}% of segments)")
    print("\n  Per-band impact is NOT reported: see the module docstring.  It\n"
          "  needs `for_each_band_w`'s own arithmetic, and the four ways a\n"
          "  hand-rolled version got it wrong are recorded there.")


if __name__ == "__main__":
    main()
