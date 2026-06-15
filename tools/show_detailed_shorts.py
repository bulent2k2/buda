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

"""
Run a .buda flow and report detailed-NUTS bit-level shorts.

DetailedNUTS only counts unplaced bits, so a cross-net SHORT (two different-net
NetSegments on the same layer sharing a track while their spans overlap) is
invisible in its normal output.  This tool runs the flow in-process and prints,
per (bundle, segment), the detailed track positions and span, then lists any
short pairs.  Use it to see the cross-trunk-layer stub short (e.g. on an old
build) and confirm it is gone after the carry-bound fix.

Usage:
    python3 tools/show_detailed_shorts.py [flow.buda]      # default: flow/xlayer_short.buda
"""
import os
import sys
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
import buda_cli  # noqa: E402  (adds build/ to sys.path and imports the buda module)


def main():
    flow = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_ROOT, "flow", "xlayer_short.buda")
    flow = os.path.abspath(flow)

    sess = buda_cli.BudaSession()
    sess.no_viz = True
    sess.do_command(f"source {flow}")
    if sess.detailed_result is None:
        print("No detailed result — does the flow call run_detailed_nuts?")
        return 1

    ns = sess.detailed_result.net_segments
    by_seg = defaultdict(list)
    for n in ns:
        by_seg[(n.bundle_id, n.seg_idx)].append(n)

    print(f"\n=== detailed NetSegments for {os.path.relpath(flow, _ROOT)} ===")
    for key in sorted(by_seg):
        segs = by_seg[key]
        tracks = sorted(round(s.track_position, 1) for s in segs)
        lo = min(s.span_lo for s in segs)
        hi = max(s.span_hi for s in segs)
        print(f"  B{key[0]}.s{key[1]} L{segs[0].layer}  tracks={tracks}  span=[{lo:.1f},{hi:.1f}]")

    # A short: different nets, same layer, same track, spans overlap OR touch.
    # The span axis is CLOSED on the same track so an end-to-end touch
    # (a.span_hi == b.span_lo) — collinear bit ends butting up — counts.
    shorts = []
    for i in range(len(ns)):
        a = ns[i]
        for j in range(i + 1, len(ns)):
            b = ns[j]
            if a.layer != b.layer or a.bundle_id == b.bundle_id:
                continue
            if abs(a.track_position - b.track_position) < 1e-6 and \
               a.span_lo <= b.span_hi and b.span_lo <= a.span_hi:
                shorts.append((a, b))

    print(f"\n=== detailed shorts: {len(shorts)}  (unplaced bits: {sess.detailed_result.num_unplaced}) ===")
    for a, b in shorts:
        ov_lo, ov_hi = max(a.span_lo, b.span_lo), min(a.span_hi, b.span_hi)
        print(f"  SHORT L{a.layer} track={a.track_position:.1f}: "
              f"B{a.bundle_id}.s{a.seg_idx}.b{a.bit_index} x B{b.bundle_id}.s{b.seg_idx}.b{b.bit_index} "
              f"over span [{ov_lo:.1f},{ov_hi:.1f}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
