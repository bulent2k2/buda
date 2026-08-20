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

"""How many bits does a CROSSING vouch for, all by itself?

`detect_bit_antennas` asks whether a bit's metal runs past its outermost
ATTACHMENT, and it accepts three kinds:

    via       the bit meets another segment's same-numbered bit   ELECTRICAL
    tap       the segment lands on a block face                   ELECTRICAL
    crossing  the bit's wire merely flies OVER a connected block   GEOMETRIC

The first two are connections.  The third is not: over-the-cell routing passes
above a block's footprint and keeps going, and `seg_attachment` says as much in
passing — a pass-through joint "carries no conn record".

antenna_repros entry 3 frames the question this leaves open: should a crossing
vouch for a bit AT ALL?  Two ways of getting a FALSE crossing credit are closed
(entry 3's own vehicle showed a bus wider than the block it crosses is not a
placeable state; entry 4 moved the test from the nominal segment to the bit).
Nobody asked whether a TRUE crossing should confer credit in the first place.

The question has no answer from the code, because the tree contains no
statement of what a crossing is supposed to vouch for.  What it does have is a
population: the bits whose ONLY attachment is a crossing.  For those, and only
those, the credit is load-bearing — remove it and they become findings.  If the
set is empty the question is academic and can be recorded as such; if it is
not, its members are what to look at.

    sole=crossing   no via, no served tap, >=1 crossing   <- THE POPULATION
    sole=tap        no via, >=1 served tap, no crossing      (for scale)
    sole=via        >=1 via, nothing else                    (for scale)
    mixed           more than one kind
    none            nothing vouches — already reportable

Tap membership is asked of the ENGINE (`buda.seg_busterm_serves_bit`), not
re-derived: its tapered/untapered fallbacks are subtle (a MISSING key serves
every bit, a PRESENT-but-empty one serves none) and re-implementing that in
Python is how the phantom-charge work produced four wrong answers and a
withdrawn table (Codex #754).  The crossing test is plain geometry and matches
verify.cpp's: the bit's own track_position against the block's perpendicular
extent, its own placed span against the along one.

Read-only: nothing is mutated and no flow is re-planned.

Usage (from the repo root):
    PYTHONPATH=build:src:tools python3 tools/scan_crossing_credit.py [flow ...]
"""
import contextlib
import io
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_FLOWS = [
    # the entry-3 vehicle itself (ends dirty on purpose)
    "flow/antenna_wide_bus_passthru.buda",
    # the tapered sibling entry 3's header points at
    "flow/antenna_taper_passthru.buda",
    # small flat, then real designs
    "flow/four_blocks.buda",
    "flow/rnr/mix.buda",
    "flow/rnr/mix2_topdown_refine.buda",
    "flow/big_data_test/big.buda",
    "flow/big_data_test/big2/big2.buda",
    "flow/rv/soc_conv_div.buda",
]


def solve(flow):
    """Run a flow, returning its session — or raising if the flow FAILED.

    Only a clean `exit 0` continues.  A half-populated session would report
    small counts as an ordinary row, and "nothing was measured" would read as
    "nothing is there" — the failure mode this kind of tool must not have.
    """
    import buda_cli
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        try:
            s.do_command(f"source {flow}")
        except SystemExit as e:
            if e.code not in (0, None):
                raise RuntimeError(f"{flow} exited {e.code}") from e
    return s


def classify(session):
    """Per placed bit-wire: which attachment kinds vouch for it."""
    import buda
    out = Counter()
    detailed = getattr(session, "detailed_result", None)
    if detailed is None:
        return out

    vias = set()
    for v in detailed.net_vias:
        for si in (v.from_seg, v.to_seg):
            if si >= 0:
                vias.add((v.bundle_id, si, v.bit_index))

    topos, fps = {}, {}
    for w in session.bundles:
        if not w.plan or w.plan.selected_topology_index < 0:
            continue
        bid = w.input.original_bundle.id
        topos[bid] = w.input.candidates[w.plan.selected_topology_index]
        fps[bid] = getattr(w, "fp", None) or session.fp

    for ns in detailed.net_segments:
        if getattr(ns, "is_shield", False):
            continue
        topo = topos.get(ns.bundle_id)
        if topo is None or ns.seg_idx >= len(topo.segments):
            continue
        sg = topo.segments[ns.seg_idx]
        horiz = sg.start.y == sg.end.y
        fp = fps.get(ns.bundle_id) or session.fp

        has_via = (ns.bundle_id, ns.seg_idx, ns.bit_index) in vias

        # The blocks THIS SEGMENT LANDS ON.  Served-ness alone is not a tap:
        # `seg_busterm_serves_bit` answers "does this tap serve this bit",
        # which is vacuously true where there is no tap at all.
        eps = topo.seg_busterms.get(ns.seg_idx) or (None, None)
        tapped = {e.block_name for e in eps if e is not None}
        has_tap = any(
            buda.seg_busterm_serves_bit(topo, ns.seg_idx, b, ns.bit_index)
            for b in tapped)

        s_lo, s_hi = min(ns.span_lo, ns.span_hi), max(ns.span_lo, ns.span_hi)
        # A CROSSING is a block the segment MERELY crosses — `seg_attachment`'s
        # own word.  A tapped block must be excluded: the wire lands on its
        # face, so the span touches the block edge and an overlap test alone
        # would report every tap as a crossing too, making the two kinds
        # indistinguishable and `sole=crossing` unreachable by construction.
        has_cross = False
        for bname in topo.connected_block_names:
            if bname in tapped:
                continue
            try:
                rects = fp.get_block_rects(bname) or [fp.get_block_bounds(bname)]
            except Exception:
                continue
            for r in rects:
                p_lo, p_hi = (r.y1, r.y2) if horiz else (r.x1, r.x2)
                if not (p_lo <= ns.track_position <= p_hi):
                    continue
                b_lo, b_hi = (r.x1, r.x2) if horiz else (r.y1, r.y2)
                if max(s_lo, b_lo) <= min(s_hi, b_hi):
                    has_cross = True
                    break
            if has_cross:
                break

        kinds = [k for k, on in (("via", has_via), ("tap", has_tap),
                                 ("crossing", has_cross)) if on]
        if not kinds:
            out["none"] += 1
        elif len(kinds) > 1:
            out["mixed"] += 1
        else:
            out[f"sole={kinds[0]}"] += 1
        out["bits"] += 1
    return out


def main():
    flows = sys.argv[1:] or DEFAULT_FLOWS
    cols = ["bits", "sole=crossing", "sole=tap", "sole=via", "mixed", "none"]
    print(f"{'flow':<40}" + "".join(f"{c:>15}" for c in cols))
    print("-" * (40 + 15 * len(cols)))
    tot, failed = Counter(), []
    for flow in flows:
        name = flow[5:] if flow.startswith("flow/") else flow
        try:
            c = classify(solve(str(ROOT / flow)))
        except RuntimeError as e:
            print(f"{name:<40}{'FAILED — not counted':>30}  ({e})")
            failed.append(name)
            continue
        print(f"{name:<40}" + "".join(f"{c[k]:>15,}" for k in cols))
        tot.update(c)
    print("-" * (40 + 15 * len(cols)))
    print(f"{'total':<40}" + "".join(f"{tot[k]:>15,}" for k in cols))
    if failed:
        print(f"\n{len(failed)} flow(s) did NOT run: {', '.join(failed)}.\n"
              "The totals cover the rest only — no conclusion about what is\n"
              "absent can rest on them.")
        return 1
    print()
    if tot["sole=crossing"] == 0:
        print("sole=crossing is 0: no bit anywhere here depends on a crossing to\n"
              "vouch for it, so the open question is ACADEMIC on this set —\n"
              "changing the rule would change no verdict.")
    else:
        print(f"sole=crossing is {tot['sole=crossing']:,}: that many bits are\n"
              "held clear of an ANTENNA finding by a crossing ALONE.  Those are\n"
              "the population to inspect — for each, the credit is load-bearing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
