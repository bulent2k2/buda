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
"""Scan a bundle's candidate topologies for DANGLING segments and categorize
them, so an expert can see which candidate *shapes* the dangling geometry
comes from.

Flagged segments fall into three categories:

  A. TRULY-dangling stub  — a single connection that is NOT a block tap
     (a wire whose other end connects to nothing).  DetailedNUTS silently
     shrinks it, so NUTS-level viz never flags it (mirrors set_drop_dangling's
     _topo_dangling_reason, src/buda_session/edit.py).
  B. UNBOUNDED slide window, EXPECTED — the +/-2^30 no-clamp sentinel on an
     OOB detour trunk, open on the side pointing AWAY from the bundle's block
     bbox.  This is by design (the trunk detours outside and slides outward);
     set_drop_dangling's clamp mode bounds it.
  C. UNBOUNDED slide window, OTHER — every unbounded window NOT explained by
     (B): open on BOTH sides (fully unclamped), on a NON-trunk segment (a stub
     / MST leg / connector that should be bounded by its connections), on a
     NON-OOB candidate, or an OOB trunk open the INWARD way.  These are the
     ones to investigate.

The sentinel is LOOSE (1e8, not exactly 2^30) because ConnTopology's two
unbounded markers are asymmetric (INT_MIN/2 = -2^30 vs INT_MAX/2 = 2^30-1); a
`>= 1<<30` test misses every high-side-only window by one.

Usage:
    python3 tools/scan_dangling.py <flow.buda> <bundle_hint>

The flow is executed (its own generate_topologies[_for_bundle] populates the
pool); then every candidate of the first bundle whose first net name starts
with <bundle_hint> is inspected.
"""
import argparse
import contextlib
import io
import os
import sys
# (stdlib)
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT / "build"), str(_ROOT / "src"), str(_ROOT / "tools")]

import buda        # noqa: E402
import buda_cli    # noqa: E402

# Matches BudaSession._DANGLING_SENTINEL (src/buda_session/edit.py).  Must be
# LOOSE, not exactly 2^30: ConnTopology's unbounded markers are asymmetric —
# perp_lo = INT_MIN/2 = -1073741824 (|.| = 2^30) but perp_hi = INT_MAX/2 =
# 1073741823 (= 2^30 - 1).  A `>= 1<<30` test catches the low sentinel and
# MISSES the high one by one, so a high-side-only unbounded window (e.g. bus_005
# cand 30/33, slide=[.., 1073741823]) went unreported.  1e8 is far above any
# real slide coordinate and catches both.
_SENTINEL = 100_000_000


def _bundle_bbox(topo, fp):
    """(xlo, xhi, ylo, yhi) over this candidate's OWN blocks
    (connected_block_names).  OOB-ness is relative to the BUNDLE's extent, not
    the whole floorplan, so an OOB detour trunk sits just outside THIS box."""
    xs, ys = [], []
    for bn in topo.connected_block_names:
        try:
            if not fp.has_block(bn):
                continue
            r = fp.get_block_bounds(bn)
            xs += [r.x1, r.x2]; ys += [r.y1, r.y2]
        except Exception:                          # pragma: no cover
            pass
    if not xs:
        return (0, 0, 0, 0)
    return (min(xs), max(xs), min(ys), max(ys))


def _spine_idx(topo):
    """Index of the trunk spine — the longest segment in the trunk orientation
    at trunk_pos (H trunk → horizontal at y=trunk_location, V → vertical at x).
    -1 if the candidate has no such spine (e.g. a plain MST)."""
    tp = topo.trunk_location
    is_h = "TRUNK_H" in topo.type or "BITRUNK_H" in topo.type
    best, sp = -1, -1
    for j, sg in enumerate(topo.segments):
        h = sg.start.y == sg.end.y
        if is_h and (not h or sg.start.y != tp):
            continue
        if (not is_h) and (h or sg.start.x != tp):
            continue
        ln = abs(sg.end.x - sg.start.x) if is_h else abs(sg.end.y - sg.start.y)
        if ln > best:
            best, sp = ln, j
    return sp


def _scan_segs(topo, fp):
    """Return [(seg_idx, kind, note)] for flagged segments; kind in
    {'truly-dangling', 'unbounded-expected', 'unbounded-other'}.

    An OOB detour trunk is unbounded on ONE side BY DESIGN — its slide window is
    open on the side pointing AWAY from the design (the trunk detours outside the
    block bbox and slides outward freely).  That case is 'unbounded-expected'.
    Every other unbounded window is 'unbounded-other' and worth investigating:
    a window open on BOTH sides (fully unclamped), an unbounded window on a
    NON-trunk segment (a stub / MST leg / connector that should be bounded by its
    connections), an unbounded window on a NON-OOB candidate, or an OOB trunk
    open on the INWARD side (toward the design, not away)."""
    out = []
    try:
        ct = buda.ConnTopology()
        ct.build(topo, fp)
    except Exception as exc:                       # pragma: no cover
        return [(-1, "build-failed", str(exc))]
    sp = _spine_idx(topo)
    is_oob = "_OOB" in topo.type
    is_h = "TRUNK_H" in topo.type or "BITRUNK_H" in topo.type
    xlo, xhi, ylo, yhi = _bundle_bbox(topo, fp)
    plo, phi = (ylo, yhi) if is_h else (xlo, xhi)   # bundle extent on the perp axis
    for j, cs in enumerate(ct.segs()):
        lo_u = abs(cs.perp_lo) >= _SENTINEL
        hi_u = abs(cs.perp_hi) >= _SENTINEL
        if lo_u or hi_u:
            sides = "both" if lo_u and hi_u else ("lo" if lo_u else "hi")
            expected = False
            if is_oob and j == sp and sides != "both":
                tp = topo.trunk_location
                outward = "lo" if tp < plo else ("hi" if tp > phi else None)
                expected = outward is not None and sides == outward
            if expected:
                out.append((j, "unbounded-expected",
                            f"OOB trunk open {sides} (outward, by design)"))
            else:
                why = []
                if sides == "both":
                    why.append("both-sided (fully unclamped)")
                if j != sp:
                    why.append("non-trunk segment")
                if not is_oob:
                    why.append("non-OOB candidate")
                if is_oob and j == sp and sides in ("lo", "hi"):
                    why.append(f"OOB trunk open {sides} INWARD")
                out.append((j, "unbounded-other", "; ".join(why) or sides))
            continue
        blk = sum(1 for c in cs.conns if c.block_name)
        if len(cs.conns) == 1 and blk == 0:
            out.append((j, "truly-dangling", "single non-block connection"))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("flow", help="path to a .buda flow that generates candidates")
    ap.add_argument("hint", help="bundle hint (net-name prefix, e.g. bus_005)")
    args = ap.parse_args()

    flow = Path(args.flow).resolve()
    if not flow.exists():
        sys.exit(f"no such flow: {flow}")

    sess = buda_cli.BudaSession()
    sess.no_viz = True
    # Execute the flow from its own directory so its relative `source` lines
    # resolve, swallowing its chatter.
    cwd = os.getcwd()
    try:
        os.chdir(flow.parent)
        with contextlib.redirect_stdout(io.StringIO()):
            sess.do_command(f"source {flow}")
    finally:
        os.chdir(cwd)

    w = next((ww for ww in sess.bundles
              if ww.input.original_bundle.get_net_names()[0].startswith(args.hint)),
             None)
    if w is None:
        sys.exit(f"no bundle whose first net starts with '{args.hint}'")

    bid = w.input.original_bundle.id
    fp = sess.fp
    cands = list(w.input.candidates)
    print(f"{args.hint} = bundle {bid}: {len(cands)} candidates\n")

    rows = []
    for i, c in enumerate(cands):
        d = _scan_segs(c, fp)
        rows.append((i, c.type, len(c.segments), d))

    print(f"{'idx':>4} {'type':<24} {'nseg':>4}  flagged segments")
    print("-" * 90)
    _abbr = {"truly-dangling": "dangling", "unbounded-expected": "unb-ok",
             "unbounded-other": "unb-OTHER"}
    for i, typ, nseg, d in rows:
        tag = ("  <== " + ", ".join(f"seg{j}:{_abbr.get(k, k)}" for j, k, _ in d)
               ) if d else ""
        print(f"{i + 1:>4} {typ:<24} {nseg:>4}{tag}")

    def has(d, kind):
        return any(k == kind for _, k, _ in d)

    truly = [(i, t) for i, t, _, d in rows if has(d, "truly-dangling")]
    unb_ok = [(i, t) for i, t, _, d in rows if has(d, "unbounded-expected")]
    # "other" is the interesting bucket — keep the per-segment reasons
    other = [(i, t, [(j, note) for j, k, note in d if k == "unbounded-other"])
             for i, t, _, d in rows if has(d, "unbounded-other")]

    print("\n=== A: truly-dangling stub (single non-block connection) ===")
    print("    the real dangling wire — DetailedNUTS silently shrinks it")
    for i, t in truly:
        flag = "MST" if "MST" in t else "NON-MST !!"
        print(f"    idx {i + 1:>3}  {t:<24} {flag}")
    print(f"    -> {len(truly)} candidate(s)")

    print("\n=== B: unbounded slide window — EXPECTED (OOB detour trunk) ===")
    print("    open on the outward side by design; set_drop_dangling clamp bounds it")
    for i, t in unb_ok:
        print(f"    idx {i + 1:>3}  {t}")
    print(f"    -> {len(unb_ok)} candidate(s)")

    print("\n=== C: unbounded slide window — OTHER (investigate) ===")
    print("    NOT a by-design OOB trunk opening: both-sided / non-trunk segment /")
    print("    non-OOB candidate / OOB trunk open the wrong way")
    for i, t, segs in other:
        detail = "; ".join(f"seg{j}: {note}" for j, note in segs)
        print(f"    idx {i + 1:>3}  {t:<24} {detail}")
    print(f"    -> {len(other)} candidate(s)")

    print("\n=== verdict ===")
    if truly:
        kinds_ = "all TRUNK_*+MST" if all("MST" in t for _, t in truly) else "MIXED"
        print(f"  {len(truly)} truly-dangling stub candidate(s) ({kinds_}).")
    else:
        print("  No truly-dangling stubs.")
    print(f"  {len(unb_ok)} by-design OOB-trunk openings (expected).")
    if other:
        print(f"  {len(other)} OTHER unbounded window(s) — these are the ones to "
              f"investigate (not explained by OOB detour geometry).")
    else:
        print("  No unexpected unbounded windows.")


if __name__ == "__main__":
    main()
