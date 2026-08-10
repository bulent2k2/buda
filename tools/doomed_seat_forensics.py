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
  FREE_SIBLING         adequate sibling whose window is also FREE (enough
                       tracks unclaimed right now).  This is a STATIC verdict
                       and NOT a claim that the re-seat heal misbehaved: the
                       heal proposes the move and then measures it, and a
                       re-seat perturbs the whole abstract solve, so a
                       statically-free sibling can still cost more bits than
                       it saves.  Measured example (chip3a_bottomup b61 seg0,
                       2026-08-04): sibling M5 has 26 span-clear tracks and 21
                       free for 16 bits, the heal DOES propose it, and the
                       re-solve lands unplaced 1658 -> 1672 / overlaps
                       297 -> 300, so the componentwise accept correctly
                       rejects (bisecting to the seat alone reproduces it).
                       Treat FREE_SIBLING as "worth a look", not "a bug".
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
    """Source one flow end-to-end and return the session.

    `visualize` is skipped (headless) but `exit` TERMINATES, exactly as the
    CLI does: a flow that exits before the physical end of the file (e.g. a
    healer experiment parked below the terminator) must be audited at its
    REAL endpoint, not with its unreachable tail executed (Codex P2)."""
    absf = os.path.join(_ROOT, flow)
    cwd = os.getcwd()
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.script_path = absf
    try:
        os.chdir(os.path.dirname(absf))
        for raw in open(absf):
            line = raw.strip()
            if not line or line.startswith(("#", "visualize")):
                continue
            if line == "exit" or line.startswith("exit "):
                break                                  # the flow's real end
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    buda.ostream_redirect():
                try:
                    s.do_command(line)
                except SystemExit as e:
                    # A nested `source`d script may exit; 0/None is normal.
                    if e.code not in (0, None):
                        raise
                    break
    finally:
        os.chdir(cwd)
    return s


def _track_claims(s, layer, seg, locked_ids):
    """Which of the sibling layer's SIGNAL tracks in this seat's window are
    actually claimed, and by whom.

    Measures the EXACT per-bit reservations DetailedNUTS makes (`net_segments`
    carry one placed track per bit) rather than testing a bus's single
    abstract `track_position` — a wide bus reserves a RANGE of tracks, so the
    point test both misses claims that enter the window and credits holders
    whose bits do not (Codex P1).  Falls back to the abstract anchor only when
    the flow never ran detailed NUTS.

    Returns (tracks, claimed_by_locked, claimed_by_unlocked, locked_ids_seen,
    unlocked_ids_seen) where the two `claimed_*` are SETS OF TRACK POSITIONS."""
    g = s.routing_grid.get_layer_grid(layer)
    tracks = [p for p, _slot in g.signal_tracks_in_span(
        seg.span_lo, seg.span_hi, seg.interval_lo, seg.interval_hi)]
    if not tracks:
        return [], set(), set(), set(), set()
    tol = 1e-6

    def _nearest(pos):
        best = min(tracks, key=lambda t: abs(t - pos))
        return best if abs(best - pos) <= max(tol, 0.5) else None

    dr = getattr(s, "detailed_result", None)
    claims = []          # (bundle_id, track_position)
    if dr is not None and dr.net_segments:
        for ns in dr.net_segments:
            if ns.layer != layer:
                continue
            if ns.bundle_id == seg.bundle_id and ns.seg_idx == seg.seg_idx:
                continue                               # the seat itself
            if ns.span_hi < seg.span_lo or ns.span_lo > seg.span_hi:
                continue
            t = _nearest(ns.track_position)
            if t is not None:
                claims.append((ns.bundle_id, t))
    else:                                              # pre-DNUTS fallback
        for t_seg in s.nuts_result.segments:
            if t_seg.layer != layer or not t_seg.placed or t_seg is seg:
                continue
            if t_seg.span_hi < seg.span_lo or t_seg.span_lo > seg.span_hi:
                continue
            half = t_seg.width / 2.0
            for t in tracks:
                if (t_seg.track_position - half) <= t <= \
                        (t_seg.track_position + half):
                    claims.append((t_seg.bundle_id, t))

    lk_tracks, un_tracks, lk_ids, un_ids = set(), set(), set(), set()
    for bid, t in claims:
        if bid in locked_ids:
            lk_tracks.add(t); lk_ids.add(bid)
        else:
            un_tracks.add(t); un_ids.add(bid)
    return tracks, lk_tracks, un_tracks, sorted(lk_ids), sorted(un_ids)


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
        # The engine admits on bus_seg_min_demand, so a GOVERNED segment's
        # requirement is its group DEMAND (bits + guards + shields), not its
        # bit count — measuring it in bits under-reports how doomed the seat
        # is and mis-ranks the sibling-layer search below.  Identity for
        # every ungoverned segment.
        need = s._seg_admission_need(w, sel, seg.seg_idx)
        bits = s._seg_member_bits(w, sel, seg.seg_idx)   # what strands
        pool = s._seg_admission_pool(
            seg, s.routing_grid.get_layer_grid(seg.layer), need)
        if pool >= need:
            continue                       # seat is adequately supplied
        if not s.layers.is_top(seg.layer):
            continue                       # LOW = the escalation family's market
        # A user-forced per-segment layer is inviolable — the re-seat heal
        # refuses to move it, so no sibling is a legal destination and the
        # seat is not a negotiation candidate at all (Codex P2).
        pins = list(w.input.pinned_seg_layers) if w.input.pinned_seg_layers else []
        if seg.seg_idx < len(pins) and pins[seg.seg_idx] >= 0:
            rows.append(dict(flow=flow, bid=seg.bundle_id, seg=seg.seg_idx,
                             layer=lnames.get(seg.layer, seg.layer),
                             need=need, bits=bits, pool=pool, verdict="USER_PINNED",
                             sibs=[], holders=None))
            continue

        # Candidate siblings: EXACTLY the re-seat heal's legal pool
        # (nutsflow.py `_final_reseat_heal`) — same-direction TOP layers with
        # a grid, intersected with `allowed_layers`, minus fractionally
        # leased layers (a re-seat there would spend past the lease).
        want_dir = (buda.LayerDir.HORIZONTAL if seg.horiz
                    else buda.LayerDir.VERTICAL)
        pool_ids = [l for l in s.layers.get_layer_ids_by_dir(want_dir)
                    if s.layers.is_top(l) and l != seg.layer
                    and s.routing_grid.has_layer(l)]
        if w.input.allowed_layers:
            pool_ids = [l for l in pool_ids if l in w.input.allowed_layers]
        if w.input.layer_shares:
            leased = {lid for lid, _sh in w.input.layer_shares}
            pool_ids = [l for l in pool_ids if l not in leased]

        sibs = [(lid, s._seg_admission_pool(
            seg, s.routing_grid.get_layer_grid(lid), need)) for lid in pool_ids]
        adequate = [(lid, sp) for lid, sp in sibs if sp >= need]

        holders, verdict = None, "LAYER_STARVED"
        best = None
        for lid, sp in adequate:
            tracks, lk_t, un_t, lk_ids, un_ids = _track_claims(
                s, lid, seg, locked_ids)
            # Room available NOW, and room if every locked copy vacated.
            free_now = len([t for t in tracks if t not in lk_t and t not in un_t])
            free_if_released = len([t for t in tracks if t not in un_t])
            if free_now >= need:
                v = "FREE_SIBLING"
            elif lk_t and free_if_released >= need:
                # Releasing the locked copies genuinely creates room: the
                # only case a class-level track negotiation can convert.
                v = "BLOCKED_BY_LOCKED"
            else:
                # Unlocked reservations alone still prevent placement, so
                # moving locked copies would NOT free enough capacity.
                v = "BLOCKED_BY_UNLOCKED"
            rank = {"BLOCKED_BY_LOCKED": 3, "FREE_SIBLING": 2,
                    "BLOCKED_BY_UNLOCKED": 1}[v]
            cand = (rank, sp, lid, v, dict(
                layer=lnames.get(lid, lid), supply=sp, tracks=len(tracks),
                free_now=free_now, free_if_locked_released=free_if_released,
                locked=lk_ids, unlocked=un_ids))
            if best is None or cand[:2] > best[:2]:
                best = cand
        if best is not None:
            _r, _sp, _lid, verdict, holders = best

        rows.append(dict(flow=flow, bid=seg.bundle_id, seg=seg.seg_idx,
                         layer=lnames.get(seg.layer, seg.layer),
                         need=need, bits=bits, pool=pool, verdict=verdict,
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
                      f"tracks={h['tracks']} free_now={h['free_now']} "
                      f"free_if_locked_released={h['free_if_locked_released']} "
                      f"locked={h['locked']} unlocked={h['unlocked']}",
                      flush=True)
        rows.extend(rows_f)

    print("\n=== SUMMARY ===")
    for verdict, n in Counter(d["verdict"] for d in rows).most_common():
        print(f"  {verdict:20} {n}")
    negotiable = [d for d in rows if d["verdict"] == "BLOCKED_BY_LOCKED"]
    vehicles = sorted({d["flow"] for d in negotiable})
    print(f"  BLOCKED_BY_LOCKED on {len(vehicles)} vehicle(s): {vehicles}")
    # BITS, not need: a governed seat's `need` is demand slots (bits +
    # guards + shields), and what strands is the bit count.
    print(f"  bits at stake (all-or-nothing strand): "
          f"{sum(d.get('bits', d['need']) for d in negotiable)}")


if __name__ == "__main__":
    main()
