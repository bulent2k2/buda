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
"""Show, per bundle segment, where the NOMINAL junction label disagrees with the
PLACED geometry -- and what that costs at the bit level.

The disagreement this exists to make visible:

  `is_endpoint` is decided ONCE, at generation, from nominal coordinates
  (topology_analysis.cpp: at_pos == along_lo || at_pos == along_hi).  NUTS then
  moves the ends -- tighten_spans_to_reach contracts a span back to its
  outermost junction -- and nothing re-derives the label.  DetailedNUTS reads
  that stale label as its ONLY gate for per-bit snapping (detailed_nuts.cpp):
  an ENDPOINT conn pulls each bit's end onto its own partner bit's track, a
  MID-span conn only asks the span to keep covering.  So a junction that was
  interior at nominal and is the segment's very end after placement leaves
  every bit stretched to one shared abstract end.

Read the table's VERDICT column:

  ok          label and placed geometry agree
  STALE MID   labelled mid, but the junction IS the placed end -> bits do NOT
              snap; this is the defect
  STALE END   labelled endpoint, but placement moved the end past it (the
              mirror; the busterm-face pass usually hides this one)

Usage:
    python3 tools/show_stale_endpoints.py <flow.buda> [<bundle_hint>]

    # the worked example (bundle 67 / bus_005, candidate 18):
    python3 tools/show_stale_endpoints.py \\
        flow/big_data_test/bigHalf_sel_bundle_only.buda bus_005
"""
import argparse
import contextlib
import io
import os
import sys

sys.path[:0] = ["build", "src", "tools"]
import buda          # noqa: E402
import buda_cli      # noqa: E402

TOL = 1e-6


def run_flow(path):
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.script_path = os.path.abspath(path)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            s.do_command(f"source {os.path.abspath(path)}")
        except SystemExit:      # a flow ending in `exit 0`
            pass
    return s


def pick_bundle(session, hint):
    for w in session.bundles:
        b = w.input.original_bundle
        if hint is None:
            if w.plan and w.input.candidates:
                return w
        elif str(b.id) == hint or (b.net_names and b.net_names[0].startswith(hint)):
            return w
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("flow")
    ap.add_argument("hint", nargs="?", default=None)
    args = ap.parse_args()

    s = run_flow(args.flow)
    w = pick_bundle(s, args.hint)
    if w is None:
        sys.exit(f"no bundle matching {args.hint!r} in {args.flow}")
    bid = w.input.original_bundle.id
    topo = w.input.candidates[w.plan.selected_topology_index]

    ct = buda.ConnTopology()
    ct.build(topo, s.fp)
    segs = ct.segs()

    placed = {t.seg_idx: t for t in s.nuts_result.segments if t.bundle_id == bid}

    print(f"\nbundle {bid}   selected topo {w.plan.selected_topology_index + 1}"
          f"  {topo.type}   nominal WL {topo.estimated_wirelength}")
    print("\n  NOMINAL (what the label was derived from) vs PLACED (what NUTS built)\n")
    hdr = (f"  {'seg':<5}{'nominal along':<18}{'placed span':<18}{'partner':<9}"
           f"{'junction at':<13}{'label':<7}{'verdict'}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    stale = []
    for si, cs in enumerate(segs):
        ts = placed.get(si)
        if ts is None or not ts.placed:
            continue
        lo, hi = min(ts.span_lo, ts.span_hi), max(ts.span_lo, ts.span_hi)
        nom = f"[{cs.along_lo},{cs.along_hi}]"
        plc = f"[{lo:.0f},{hi:.0f}]"
        first = True
        for c in cs.conns:
            if c.kind != buda.SegConnKind.SEG:
                continue
            p = placed.get(c.seg_idx)
            if p is None or not p.placed:
                continue
            pos = p.track_position
            at_end = abs(pos - lo) <= TOL or abs(pos - hi) <= TOL
            label = "end" if c.is_endpoint else "mid"
            if at_end and not c.is_endpoint:
                verdict = "STALE MID  <-- bits will NOT snap"
                stale.append((si, c.seg_idx, pos))
            elif not at_end and c.is_endpoint:
                verdict = "STALE END"
            else:
                verdict = "ok"
            print(f"  {('seg'+str(si)) if first else '':<5}"
                  f"{nom if first else '':<18}{plc if first else '':<18}"
                  f"{'seg'+str(c.seg_idx):<9}{pos:<13.1f}{label:<7}{verdict}")
            first = False

    if not stale:
        # A STALE END may still be listed above; it is the benign mirror (the
        # busterm-face and pass-through passes re-extend that end).  Only a
        # STALE MID suppresses per-bit snapping, so only that one is "the bug".
        print("\n  no STALE MID on this bundle -- every junction that defines a")
        print("  placed end is labelled as one, so each bit snaps to its own via.")
        return

    print("\n  The contradiction, spelled out:")
    for si, pj, pos in stale:
        ts = placed[si]
        lo, hi = min(ts.span_lo, ts.span_hi), max(ts.span_lo, ts.span_hi)
        end = "lo" if abs(pos - lo) <= TOL else "hi"
        print(f"    seg{si}'s placed span {end} end is {pos:.0f}; its junction with "
              f"seg{pj} is AT {pos:.0f}.")
        print(f"    Geometrically that IS the endpoint.  The label still says "
              f"'mid', because at NOMINAL")
        print(f"    seg{si} ran [{segs[si].along_lo},{segs[si].along_hi}] and "
              f"{pos:.0f} was interior to it.")

    dn = getattr(s, "detailed_result", None)
    if dn is None:
        print("\n  (run the flow through run_detailed_nuts to see the bit-level cost)")
        return

    # The VERDICT is the engine's own -- check_dnuts is what `check_design`
    # runs, so this tool cannot report a different number from the flow.
    nbits = len(w.input.original_bundle.net_names)
    res = buda.check_dnuts(ct, dn, topo, s.fp, s.layers, bid, nbits)
    antennas = [v for v in res.violations
                if v.kind == buda.ViolationKind.ANTENNA]

    print("\n  What check_design says about it (engine verdict, not this tool's):\n")
    for si, pj, _ in stale:
        here = [v for v in antennas if v.seg_idx == si]
        print(f"    seg{si}: {len(here)} ANTENNA violation(s)")
        for v in here[:3]:
            print(f"       {v.message}")
        if len(here) > 3:
            print(f"       ... {len(here) - 3} more")

    print("\n  Why -- each bit's wire end against its OWN via:\n")
    for si, pj, shared_end in stale:
        mine = {n.bit_index: n for n in dn.net_segments
                if n.bundle_id == bid and n.seg_idx == si}
        theirs = {n.bit_index: n for n in dn.net_segments
                  if n.bundle_id == bid and n.seg_idx == pj}
        shown = 0
        for b in sorted(mine):
            if b not in theirs or shown >= 5:
                continue
            n = mine[b]
            own = theirs[b].track_position          # where THIS bit's via is
            lo, hi = min(n.span_lo, n.span_hi), max(n.span_lo, n.span_hi)
            end = hi if abs(hi - own) <= abs(lo - own) else lo
            if abs(end - own) <= TOL:
                continue
            print(f"       bit {b:<3} wire ends at {end:.1f}, its via with "
                  f"seg{pj} is at {own:.1f}  ({abs(end - own):.1f} past it)")
            shown += 1
        print(f"       (they all stop at {shared_end:.0f} -- the segment's ABSTRACT end --"
              f" instead of following their own via)\n")

    print("  Each of those bits would end exactly at its own via if the label were")
    print("  re-derived from the placed span.  That is the whole fix.")


if __name__ == "__main__":
    main()
