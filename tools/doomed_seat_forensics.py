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
Per-seat FORENSICS for supply-doomed TOP seats — the build-decision instrument
for the class-level TRACK negotiation open (issue #536's last residual; see
docs/internal/wishlist-healer.md "Class-level TRACK negotiation").

`check_design`'s doomed-seat census (`_report_doomed_seats`, PR #548) is the
COARSE detector: it names every seat whose assigned layer cannot supply its
member bits, and separates TOP from LOW.  It stops there — it only ever
evaluates each unlocked wrapper's OWN assigned layer, so it cannot tell a seat
whose sibling layer is adequate-but-occupied (negotiable) from one that is
genuinely layer-starved (not negotiable by any mechanism).

This tool makes that distinction, per seat:

  BLOCKED_BY_LOCKED    a same-direction TOP sibling has span-clear supply >=
                       member bits, and `hier.locked` bottom-up copies hold
                       tracks in the seat's window there.  THE shape the
                       class-level track negotiation exists to reach: a
                       top-level bus and locked template copies contending
                       for the design's only viable window.
  BLOCKED_BY_UNLOCKED  adequate sibling, held only by unlocked bundles —
                       ordinary ripup / re-seat territory, not a class move.
  FREE_SIBLING         adequate sibling with an EMPTY window: the TOP re-seat
                       heal should already have taken it (investigate).
  LAYER_STARVED        no same-direction TOP sibling has adequate supply —
                       genuinely doomed; no negotiation mechanism helps.

Supply is measured with the session's own `_seg_admission_pool` (the exact
DNUTS `place_by_layer` admission arithmetic) evaluated against each sibling
layer's grid over the SAME seat (span x slide) — i.e. "what would this seat's
supply be on that layer", the question the TOP re-seat heal asks.

Usage:
  tools/doomed_seat_forensics.py                     # whole qor_corpus CORPUS
  tools/doomed_seat_forensics.py flow/rnr/mix.buda   # specific flows

Validated against the hand-established b61 case (mix2_fast_on_aligned_sql):
reproduces M6 pool=12 vs need=16, sibling M4 supply=17, locked holders
b152/b170/b174 plus unlocked b18.
"""

import argparse
import contextlib
import io
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "build"), os.path.join(_ROOT, "src"),
           os.path.join(_ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import buda            # noqa: E402  (after sys.path setup)
import buda_cli        # noqa: E402
import qor_corpus as qc  # noqa: E402


def run_flow(flow):
    """Source one flow end-to-end (viz/exit stripped) and return the session."""
    absf = os.path.join(_ROOT, flow)
    cwd = os.getcwd()
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.script_path = absf
    try:
        os.chdir(os.path.dirname(absf))
        for raw in open(absf):
            line = raw.strip()
            if not line or line.startswith(("#", "visualize", "exit")):
                continue
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    buda.ostream_redirect():
                try:
                    s.do_command(line)
                except SystemExit as e:
                    # An intentional `exit 0` ends a flow normally.
                    if e.code not in (0, None):
                        raise
    finally:
        os.chdir(cwd)
    return s


def _window_holders(s, layer, seg, locked_ids):
    """Placed segments on `layer` that actually consume the seat's window:
    span overlapping the seat's span AND track inside its slide window.
    Returns (locked_bundle_ids, unlocked_bundle_ids)."""
    lo, hi = seg.interval_lo, seg.interval_hi
    locked, unlocked = set(), set()
    for t in s.nuts_result.segments:
        if t.layer != layer or not t.placed or t is seg:
            continue
        if t.span_hi < seg.span_lo or t.span_lo > seg.span_hi:
            continue                                   # no span overlap
        if not (lo <= t.track_position <= hi):
            continue                                   # outside the window
        (locked if t.bundle_id in locked_ids else unlocked).add(t.bundle_id)
    return sorted(locked), sorted(unlocked)


def audit(s, flow):
    """Classify every supply-doomed TOP seat in a solved session."""
    if getattr(s, "nuts_result", None) is None or \
            getattr(s, "routing_grid", None) is None:
        return []
    locked_ids = {w.input.original_bundle.id
                  for w in s.bundles if w.hier.locked}
    # The census scope: locked copies are excluded (their bits arrive via the
    # template-copy path, which never runs per-instance admission).
    wmap = {w.input.original_bundle.id: w
            for w in s.bundles if not w.hier.locked}
    lnames = s._make_layer_names()
    rows = []
    for seg in s.nuts_result.segments:
        if not seg.placed or not s.routing_grid.has_layer(seg.layer):
            continue
        w = wmap.get(seg.bundle_id)
        if w is None or not w.plan.seg_layers:
            continue
        sel = w.plan.selected_topology_index
        if sel < 0 or sel >= len(w.input.candidates):
            continue
        need = s._seg_member_bits(w, sel, seg.seg_idx)
        pool = s._seg_admission_pool(
            seg, s.routing_grid.get_layer_grid(seg.layer), need)
        if pool >= need:
            continue                       # seat is adequately supplied
        if not s.layers.is_top(seg.layer):
            continue                       # LOW = the escalation family's market

        # What would this seat's supply be on each same-direction TOP sibling?
        sibs = []
        for lid in s.layers.get_layer_ids_by_dir(
                s.layers.get_layer_dir(seg.layer)):
            if lid == seg.layer or not s.routing_grid.has_layer(lid):
                continue
            if not s.layers.is_top(lid):
                continue
            sibs.append((lid, s._seg_admission_pool(
                seg, s.routing_grid.get_layer_grid(lid), need)))

        adequate = [(lid, sp) for lid, sp in sibs if sp >= need]
        holders = None
        if not adequate:
            verdict = "LAYER_STARVED"
        else:
            # Report the sibling with the strongest locked-copy claim: that is
            # the one a class-level track negotiation would have to move.
            best = max(
                ((lid, sp) + _window_holders(s, lid, seg, locked_ids)
                 for lid, sp in adequate),
                key=lambda r: (len(r[2]), r[1]))
            lid, sp, lk, un = best
            verdict = ("BLOCKED_BY_LOCKED" if lk else
                       "BLOCKED_BY_UNLOCKED" if un else "FREE_SIBLING")
            holders = dict(layer=lnames.get(lid, lid), supply=sp,
                           locked=lk, unlocked=un)
        rows.append(dict(flow=flow, bid=seg.bundle_id, seg=seg.seg_idx,
                         layer=lnames.get(seg.layer, seg.layer),
                         need=need, pool=pool, verdict=verdict,
                         sibs=[(lnames.get(l, l), p) for l, p in sibs],
                         holders=holders))
    return rows


def main():
    ap = argparse.ArgumentParser(
        description="Classify supply-doomed TOP seats: negotiable "
                    "(locked copies hold an adequate sibling) vs "
                    "genuinely layer-starved.")
    ap.add_argument("flows", nargs="*",
                    help="flows to audit (default: the qor_corpus CORPUS)")
    args = ap.parse_args()

    flows = args.flows or list(qc.CORPUS)
    rows = []
    for flow in flows:
        try:
            rows_f = audit(run_flow(flow), flow)
        except Exception as e:                     # noqa: BLE001 — record, continue
            print(f"{flow}: ERR {type(e).__name__}: {str(e)[:90]}", flush=True)
            continue
        tag = flow.replace("flow/", "")
        if not rows_f:
            print(f"{tag:40} no doomed TOP seats", flush=True)
        for d in rows_f:
            print(f"{tag:40} b{d['bid']} seg{d['seg']} on {d['layer']} "
                  f"need={d['need']} pool={d['pool']}  {d['verdict']}",
                  flush=True)
            print(f"{'':40}   siblings={d['sibs']}", flush=True)
            if d["holders"]:
                h = d["holders"]
                print(f"{'':40}   sibling {h['layer']} supply={h['supply']} "
                      f"locked={h['locked']} unlocked={h['unlocked']}",
                      flush=True)
        rows.extend(rows_f)

    print("\n=== SUMMARY ===")
    for verdict, n in Counter(d["verdict"] for d in rows).most_common():
        print(f"  {verdict:20} {n}")
    negotiable = [d for d in rows if d["verdict"] == "BLOCKED_BY_LOCKED"]
    vehicles = sorted({d["flow"] for d in negotiable})
    print(f"  BLOCKED_BY_LOCKED on {len(vehicles)} vehicle(s): {vehicles}")
    print(f"  bits at stake (all-or-nothing strand): "
          f"{sum(d['need'] for d in negotiable)}")


if __name__ == "__main__":
    main()
