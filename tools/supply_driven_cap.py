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
"""The SUPPLY-DRIVEN bundle bit cap experiment — MEASURED AND REJECTED.

Kept as the reproducible record behind
docs/internal/wishlist-bundler.md "Supply-driven bundle bit cap": the loop
does NOT converge, its cost outgrows its benefit, and it degenerates to
`cap 1` on a zero-supply seat.  Re-run it before reviving the idea.

The scoped cap (PR #582) lets one bundle be split without taxing the design,
but choosing N is manual.  The natural next step is to derive N from the
supply the seat actually gets:

    run the flow -> read the doomed-seat census -> for each doomed bundle,
    cap = the largest part that fits its seat -> re-run with those scoped
    caps -> measure.

This is the loop as a TOOL, deliberately: if one iteration does not move the
corpus triple there is no reason to build it into the engine.

The cap for a doomed seat is the best pool available to that segment across
its assigned layer and its same-direction TOP siblings — the most bits that
could actually land — floored at 1.  A bundle with several doomed seats takes
the tightest.

Usage: supply_driven_cap.py <flow.buda> [<flow.buda> ...]
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "build"), os.path.join(_ROOT, "src"),
           os.path.join(_ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import buda                          # noqa: E402
import doomed_seat_forensics as dsf  # noqa: E402
import qor_corpus as qc              # noqa: E402


def derive_caps(s):
    """(prefix -> cap) for every supply-doomed seat in a solved session.

    `prefix` is the bundle's first net name with a trailing `_<idx>` stripped
    — the bus name a `set_max_bundle_bits ... for` scope takes.
    """
    wmap = {w.input.original_bundle.id: w for w in s.bundles
            if not w.hier.locked}
    caps = {}
    detail = []
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
        g = s.routing_grid.get_layer_grid(seg.layer)
        pool = s._seg_admission_pool(seg, g, need)
        if pool >= need:
            continue                      # not doomed
        # Best pool this seat could get, across its own layer and every
        # same-direction sibling.  FREE tracks, not raw supply: b61's M4 has
        # 17 tracks for 16 bits and looks adequate, but 16 of them are taken —
        # which is exactly why the re-seat heal already tried M4 and failed.
        # A cap derived from raw supply would conclude "a sibling fits it" and
        # cap nothing.
        want = (buda.LayerDir.HORIZONTAL if seg.horiz
                else buda.LayerDir.VERTICAL)
        locked_ids = {x.input.original_bundle.id
                      for x in s.bundles if x.hier.locked}
        best = 0
        for lid in s.layers.get_layer_ids_by_dir(want):
            if not s.routing_grid.has_layer(lid):
                continue
            if w.input.allowed_layers and lid not in w.input.allowed_layers:
                continue
            supply = s._seg_admission_pool(
                seg, s.routing_grid.get_layer_grid(lid), need)
            tracks, lk_t, un_t, _lk, _un = dsf._track_claims(
                s, lid, seg, locked_ids)
            # No span-clear tracks at all => nothing can land, whatever the
            # admission pool says.  b61's M2 reports supply 16 purely from the
            # MIDPOINT fallback while zero tracks clear the span, so treating
            # an empty track list as "use the raw supply" made the loop
            # conclude a layer could host the bus and cap nothing.
            free = len([t for t in tracks if t not in lk_t and t not in un_t])
            best = max(best, min(supply, free))
        cap = max(1, best)
        if cap >= need:
            continue                      # some layer can host it whole
        nets = list(w.input.original_bundle.get_net_names())
        if not nets:
            continue
        pfx = nets[0].rsplit("_", 1)[0]
        caps[pfx] = min(caps.get(pfx, cap), cap)
        detail.append((seg.bundle_id, seg.seg_idx, need, pool, cap, pfx))
    return caps, detail


def variant(flow, caps, it):
    """Write a copy of `flow` with the scoped caps declared before bundling.

    One file PER ITERATION (`_sd<it>_<base>`): a single reused path would make
    every pass overwrite the last, so the intermediate flows could not be
    inspected afterwards and the cleanup list would hold the same path
    repeatedly — which crashed the multi-pass run at teardown, after the
    measurements printed (Codex P2 on #588)."""
    src = os.path.join(_ROOT, flow)
    lines = open(src).read().split("\n")
    out = []
    for l in lines:
        if l.strip().startswith("run_hier_bundler") or \
                l.strip().startswith("run_bundler"):
            for p, n in sorted(caps.items()):
                out.append(f"set_max_bundle_bits {n} for {p}")
        out.append(l)
    d, base = os.path.split(src)
    path = os.path.join(d, f"_sd{it}_" + base)
    open(path, "w").write("\n".join(out))
    return os.path.relpath(path, _ROOT)


def _q(r):
    return (f"{r.get('overlaps')} ov / {r.get('unplaced')} unpl / "
            f"{r.get('viol_bundles')} viol   absWL {r.get('abstract_wl')}")


def main():
    args = list(sys.argv[1:])
    iters = 1
    if "--iters" in args:
        i = args.index("--iters")
        iters = int(args[i + 1])
        del args[i:i + 2]

    for flow in args:
        print(f"\n{'=' * 72}\n{flow}\n{'=' * 72}")
        print(f"  iter 0 (baseline): {_q(qc.run_flow(flow))}")
        caps, cur, made = {}, flow, []
        for it in range(1, iters + 1):
            s = dsf.run_flow(cur)
            new, detail = derive_caps(s)
            if not new:
                print(f"  iter {it}: no supply-doomed seats left — converged")
                break
            # Accumulate: a scope already declared keeps the TIGHTER cap, so
            # the loop cannot oscillate a bundle back to a looser bound.
            changed = False
            for p, n in new.items():
                if p not in caps or n < caps[p]:
                    caps[p] = n
                    changed = True
            for bid, si, need, pool, cap, pfx in detail:
                print(f"      b{bid} seg{si}: need={need} pool={pool} "
                      f"-> cap {cap} for {pfx}")
            if not changed:
                print(f"  iter {it}: no cap tightened — fixpoint "
                      f"({len(new)} seat(s) still doomed)")
                break
            cur = variant(flow, caps, it)
            made.append(cur)
            print(f"  iter {it} ({len(caps)} cap(s)): {_q(qc.run_flow(cur))}")
        for f in made:                       # unique by construction now
            fp = os.path.join(_ROOT, f)
            if os.path.exists(fp):           # tolerate a hand-deleted variant
                os.remove(fp)


if __name__ == "__main__":
    main()
