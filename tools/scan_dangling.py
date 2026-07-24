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

A "dangling" ConnSeg is one of two kinds (mirrors set_drop_dangling's
predicates, see src/buda_session/edit.py::_topo_dangling_reason):

  A. TRULY-dangling stub  — a single connection that is NOT a block tap
     (a wire whose other end connects to nothing).  DetailedNUTS silently
     shrinks it, so NUTS-level viz never flags it; this is the real bug.
  B. UNBOUNDED slide window — the +/-2^30 no-clamp sentinel on an
     OOB-detour trunk.  set_drop_dangling's clamp mode bounds these.

Usage:
    python3 tools/scan_dangling.py <flow.buda> <bundle_hint>

The flow is executed (its own generate_topologies[_for_bundle] populates the
pool); then every candidate of the first bundle whose first net name starts
with <bundle_hint> is inspected.  Prints a per-candidate table plus a
per-category breakdown of MST vs non-MST.
"""
import argparse
import contextlib
import io
import os
import sys
from collections import Counter
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


def _dangling_segs(topo, fp):
    """Return [(seg_idx, kind)] for dangling segments; kind in
    {'truly-dangling', 'unbounded-window'}."""
    out = []
    try:
        ct = buda.ConnTopology()
        ct.build(topo, fp)
    except Exception as exc:                       # pragma: no cover
        return [(-1, f"build-failed:{exc}")]
    for j, cs in enumerate(ct.segs()):
        if abs(cs.perp_lo) >= _SENTINEL or abs(cs.perp_hi) >= _SENTINEL:
            out.append((j, "unbounded-window"))
            continue
        blk = sum(1 for c in cs.conns if c.block_name)
        if len(cs.conns) == 1 and blk == 0:
            out.append((j, "truly-dangling"))
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
    by_type = Counter()
    for i, c in enumerate(cands):
        d = _dangling_segs(c, fp)
        rows.append((i, c.type, len(c.segments), d))
        by_type[c.type] += 1

    print(f"{'idx':>4} {'type':<24} {'nseg':>4}  dangling")
    print("-" * 78)
    for i, typ, nseg, d in rows:
        tag = ("  <== " + ", ".join(f"seg{j}:{k}" for j, k in d)) if d else ""
        print(f"{i + 1:>4} {typ:<24} {nseg:>4}{tag}")

    def kinds(d):
        return {k for _, k in d}

    truly = [(i, t) for i, t, _, d in rows if "truly-dangling" in kinds(d)]
    unb = [(i, t) for i, t, _, d in rows if "unbounded-window" in kinds(d)]

    print("\n=== A: truly-dangling stub (single non-block connection) ===")
    print("    the real dangling wire — DetailedNUTS silently shrinks it")
    for i, t in truly:
        flag = "MST" if "MST" in t else "NON-MST !!"
        print(f"    idx {i + 1:>3}  {t:<24} {flag}")
    print(f"    -> {len(truly)} candidate(s): "
          f"{sum('MST' in t for _, t in truly)} MST, "
          f"{sum('MST' not in t for _, t in truly)} non-MST")

    print("\n=== B: unbounded (+/-2^30) slide window ===")
    print("    OOB-detour trunk geometry; set_drop_dangling clamp bounds these")
    for i, t in unb:
        flag = "MST" if "MST" in t else "NON-MST (pure OOB)"
        print(f"    idx {i + 1:>3}  {t:<24} {flag}")
    print(f"    -> {len(unb)} candidate(s): "
          f"{sum('MST' in t for _, t in unb)} MST, "
          f"{sum('MST' not in t for _, t in unb)} non-MST")

    print("\n=== verdict ===")
    if truly and all("MST" in t for _, t in truly):
        print("  Every truly-dangling stub is a TRUNK_*+MST hybrid "
              "(the MST-relay artifact).")
    elif truly:
        print("  Some truly-dangling stubs are NOT MST types:")
        for i, t in truly:
            if "MST" not in t:
                print(f"    idx {i + 1}: {t}")
    else:
        print("  No truly-dangling stubs.")
    if unb and any("MST" not in t for _, t in unb):
        print("  Unbounded windows also occur on pure OOB trunks "
              "(TRUNK_*_OOB), so they are a separate class from the MST stubs.")


if __name__ == "__main__":
    main()
