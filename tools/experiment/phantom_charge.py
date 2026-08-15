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

  P2 MAGNITUDE.  For the instances that survive P0 and P1: how much demand does
     the phantom put on which band, and does that band have the room?  The harm
     is discrete — a band that looks full pushes somebody else's candidate out
     of the STRICT tier — so the reported figures are the ones that decide it:
     whether the band overflows, whether the phantom is what makes it overflow,
     and how much of the remaining headroom the phantom eats.

P2's arithmetic is the ENGINE's, read through `CongestionPlanner::
committed_charges()` — the `charge_log_` record `commit_plan` keeps so a rip-up
can subtract exactly what it added.  Nothing here re-derives a band.

That is deliberate.  A first cut did re-derive, mapping each duplicate onto the
cut/band it charges, and got it wrong in four separate ways (Codex #754): it
picked the grid of the WRONG AXIS (`for_each_cut_` passes `is_vcut = is_h`, so
an H segment's bands index `y_grid_` and a V segment's `x_grid_`, the opposite
of the obvious reading), it never filtered cuts by DIRECTION, it used an
inclusive band test where `find_band` is half-open, and it read the topology's
nominal perp while `commit_plan` charges through `plan.seg_perp` and may SPREAD
one segment over several weighted bands (`band_span_charge`).  Those numbers
were withdrawn.  Under the greedy `band_span_charge` modes no reimplementation
could have been right at all — they read live occupancy that has moved on by
the time anyone asks — which is why the fix was to ask the engine rather than to
patch the Python.

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


def band_phantoms(planner, groups):
    """Per-band duplicated demand, from the engine's own charge record.

    `groups` maps bundle_id -> list of segment-index sets, each set holding
    segments that are mutually coincident AND that NUTS seated on one track:
    same layer, same orientation, same PLACED track position.

    A band is affected when TWO OR MORE segments of one group charged it.  Two
    facts together make merging them sound, and each supplies one axis: the
    shared TRACK fixes the position across the span, and the shared CUT fixes
    it along the span (a cut lies at one coordinate, so every segment charging
    it reaches that coordinate).  Same layer, same track, same place along the
    wire — one piece of metal, whatever the members' individual extents.  It is
    also why the grouping needs no reachability argument, and why two disjoint
    duplicates that happen to share a track stay harmless: they share no cut.

    The phantom is `sum - max`: one wire pays once, at the LARGEST of the
    recorded amounts.  Conservative by construction, and it handles a triple
    stack correctly where summing pairwise minima would count it three times.

    Returns [(cut_index, band, phantom, cap, usage, layer, bundles)], one row
    per affected band.
    """
    # (bundle, segment) -> [(cut, band, amount)], straight from charge_log_.
    by_seg = {}
    for c in planner.committed_charges():
        by_seg.setdefault((c.bundle_id, c.seg_idx), []).append(
            (c.cut_index, c.band, c.amount))

    cuts = planner.get_cuts()
    per_band = {}
    for bid, sets in groups.items():
        for members in sets:
            amounts = {}                      # (cut, band) -> [amount, ...]
            for si in members:
                for (ci, b, amt) in by_seg.get((bid, si), ()):
                    amounts.setdefault((ci, b), []).append(amt)
            for key, vals in amounts.items():
                if len(vals) < 2:
                    continue
                ph, bids = per_band.get(key, (0.0, set()))
                per_band[key] = (ph + sum(vals) - max(vals), bids | {bid})

    return [(ci, b, ph, cuts[ci].cap(b), cuts[ci].usage(b),
             cuts[ci].layer_id, sorted(bids))
            for (ci, b), (ph, bids) in sorted(per_band.items())]


def run(flow, p0, totals, bands):
    s = solve(flow)
    nr = getattr(s, "nuts_result", None)
    if nr is None:
        return
    placed = {(ts.bundle_id, ts.seg_idx): ts.track_position
              for ts in nr.segments if ts.placed}

    groups = {}
    for w in s.bundles:
        if not w.plan or w.plan.selected_topology_index < 0:
            continue
        t = w.input.candidates[w.plan.selected_topology_index]
        seg_layers = list(w.plan.seg_layers)
        bid = w.input.original_bundle.id
        totals["bundles"] += 1
        totals["segments"] += len(t.segments)

        # Keyed by the coincidence class itself, so a triple stack forms ONE
        # group rather than three overlapping pairs.
        #
        # The key is the PLACED track, not the topology's nominal perp.  Being
        # one wire is a placed fact, and the two can disagree: two duplicate
        # pairs at the same nominal perp that NUTS seats on DIFFERENT tracks
        # are two wires, and a nominal key would merge all four into one class
        # and report three phantom charges where the truth is two (Codex #762).
        # Keying on the track cannot make that mistake, and it is the honest
        # statement of the merge rule anyway.
        classes = {}
        for (i, j, _lo, _hi, o, _perp, layer) in coincident_pairs(t, seg_layers):
            totals["pairs"] += 1
            pi, pj = placed.get((bid, i)), placed.get((bid, j))
            if pi is None or pj is None:
                p0["one or both unplaced"] += 1
                continue
            if abs(pi - pj) >= 1e-9:
                p0["placed APART (charge correct)"] += 1
                continue
            p0["co-placed (phantom)"] += 1
            # Only a CO-PLACED pair reaches P2: where NUTS put the twins on
            # different tracks the metal really is two wires and charging twice
            # was right, so folding it in would manufacture a phantom.
            classes.setdefault((o, layer, round(pi, 6)), set()).update((i, j))
        # EXTEND rather than assign: `charge_log_` keys on original_bundle.id,
        # so if two wrappers ever shared one, assigning would silently drop a
        # class whose charges are still in the record.
        if classes:
            groups.setdefault(bid, []).extend(classes.values())

    planner = getattr(s, "planner", None)
    if planner is None or not groups:
        return
    if not planner.charge_log_active():
        # band_span_charge 0 charges a single band and is its own inverse, so
        # it keeps no record.  Say so rather than report an empty result.
        totals["unrecorded_flows"] += 1
        return
    name = Path(flow).name
    bands.extend((name,) + row for row in band_phantoms(planner, groups))


def report_bands(bands, unrecorded):
    print("\nP2 — how much demand, on which band, with how much room?")
    if unrecorded:
        print(f"  {unrecorded} flow(s) ran with band_span_charge 0, which keeps"
              " no charge record;\n  their instances are NOT in the rows below.")
    if not bands:
        print("  no affected bands (nothing co-placed reached the committed"
              " field)")
        return

    print(f"  affected bands                    {len(bands):>6}")
    # Overflow is the hard STRICT constraint, so it gets counted first: a band
    # the phantom ALONE pushes over is the discrete harm the whole question is
    # about.
    over = [r for r in bands if r[5] > r[4]]
    caused = [r for r in over if r[5] - r[3] <= r[4]]
    print(f"  ... over capacity                  {len(over):>6}")
    print(f"  ... over capacity BECAUSE of it    {len(caused):>6}")

    print(f"\n  {'flow':<26} {'cut':>4} {'band':>4} {'M':>3} {'phantom':>9}"
          f" {'cap':>9} {'usage':>9}  fill%   eaten  bundle")
    for (flow, ci, b, ph, cap, use, lid, bids) in bands:
        # Of the room that would have existed without the phantom, how much
        # does it take?  Undefined when the band is already over.
        free = cap - use
        eaten = ("    n/a" if free < 0 else
                 f"{100.0 * ph / max(ph + free, 1e-12):>6.1f}%")
        print(f"  {flow:<26} {ci:>4} {b:>4} {lid:>3} {ph:>9.2f} {cap:>9.2f}"
              f" {use:>9.2f} {100.0 * use / max(cap, 1e-12):>5.1f}% {eaten}"
              f"  {','.join(str(x) for x in bids)}")


def main():
    flows = sys.argv[1:] or FLOWS
    p0, totals, bands = Counter(), Counter(), []
    for f in flows:
        p = BUDA / f
        if not p.exists():
            print(f"-- not found: {f}")
            continue
        run(p, p0, totals, bands)
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

    report_bands(bands, totals["unrecorded_flows"])
    print("\n  These are FINAL committed states.  The planner is greedy and\n"
          "  widest-first, so a band could have been tight mid-run and relaxed\n"
          "  by the end; a clean table here is not 'never mattered'.")


if __name__ == "__main__":
    main()
