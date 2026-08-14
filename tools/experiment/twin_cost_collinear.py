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

"""E1 — could trimming the redundant stub ever flip the selection?

Read-only, and DECISIVE in one direction.  The planner scores a candidate as

    total = seg_cost + wl_term
          = max_over_segments(SegCost.total) + kWL * wl_est

so removing the redundant stub S can only help through three channels:
  (1) wl_est drops by len(S)                       -> wl_term drops by kWL*len(S)
  (2) if S was the argmax, seg_cost falls to the runner-up
  (3) S's own band charge disappears from the candidate's overlay, which can
      lower the cong term of the segments it overlapped (the DOUBLE CHARGE)

Channel (3) is the one that cannot be read off a single scoring pass — so
instead of estimating it, BOUND it.  Congestion cost is non-negative, so even
if trimming drove every remaining segment's cong term to zero:

    cost_after >= kWL*(wl_est - len(S)) + ksegs*(n-1) + max_over_remaining(total - cong)

That is a floor no amount of double-charge relief can go under.  If the floor
still exceeds the winner's cost, trimming PROVABLY cannot flip that candidate,
and the hypothesis is refuted for it with no mutation and no re-planning.

Only the candidates whose floor dips below the winner need the real
trim-and-rescore experiment.

The WL term's components are SEPARATED by a per-bundle least-squares fit
(`_fit_wl_term`) rather than assumed.  Taking `kWL` as `wl_term/wl_est` folds
the segment-count penalty into the WL rate, and scaling that by `(wl - L)` then
removes a penalty proportional to the trimmed LENGTH instead of to the one
segment actually dropped — which overstates the post-trim cost for a short stub,
so the "floor" stops being a lower bound and a reachable cost can be reported as
unreachable (Codex #745; it moved 2 of 150 candidates across the line).  A
bundle whose books the fit cannot reproduce is reported UNVERIFIABLE rather than
scored on a formula that does not hold for it.
"""
import contextlib
import io
import sys
from collections import Counter
from pathlib import Path

BUDA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BUDA / "tools"))

from scan_collinear_stubs import classify, contained_pairs  # noqa: E402

FLOWS = [
    "flow/mst_shared_leg_prefix.buda",
    "flow/mst_shared_leg_suffix.buda",
    "flow/four_blocks.buda",
    "demo/comprehensive_demo.buda",
    "flow/lShape1.buda",
    "flow/teg1.buda",
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


def seg_len(sg):
    return abs(sg.end.x - sg.start.x) + abs(sg.end.y - sg.start.y)


def _fit_wl_term(w, costs):
    """Recover (kWL, kWL*kWLSpread, kWL*ksegs) for one bundle, or None.

    `wl_term = kWL * (wl_est + kWLSpread*(wl_hi-wl_lo) + ksegs*w_segs)` — linear
    in three regressors, so a no-intercept least squares over the bundle's own
    candidates recovers the coefficients without reading (or assuming) the
    knobs.  That matters because `kSegsRel` carries a COMPILED default that
    engages whenever a flow declares `healersAhead`, so "kSegs is probably off"
    is wrong on exactly the flows this measurement cares about.

    Returns None when the fit does not reproduce every candidate's wl_term to
    within a tight tolerance — a per-bit TAPERED candidate charges a fractional
    segment weight this model does not carry, and a bundle whose books we
    cannot reproduce must be reported as unverifiable rather than scored on a
    formula that does not hold for it.
    """
    import numpy as np
    rows, ys = [], []
    for i, t in enumerate(w.input.candidates):
        c = costs.get(i)
        if c is None:
            continue
        env = (t.wl_hi - t.wl_lo) if (t.wl_lo >= 0 and t.wl_hi >= t.wl_lo) else 0.0
        rows.append([float(t.estimated_wirelength), float(env),
                     float(len(t.segments))])
        ys.append(c.wl_term)
    if len(rows) < 4:                      # need more samples than unknowns
        return None
    A = np.array(rows)
    y = np.array(ys)
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = np.abs(A @ coef - y)
    scale = max(1e-9, float(np.abs(y).max()))
    if float(resid.max()) / scale > 1e-6:
        return None
    if coef[0] <= 0 or coef[2] < -1e-9:    # kWL must be positive, ksegs >= 0
        return None
    return float(coef[0]), float(coef[1]), float(max(0.0, coef[2]))


def analyse(flow, rows_out, unverifiable):
    s = solve(flow)
    topo_fp = s._make_topo_fp_resolver()
    for w in s.bundles:
        if not w.plan:
            continue
        sel = w.plan.selected_topology_index
        if sel < 0:
            continue
        fp = topo_fp(w)

        # which candidates carry a REDUNDANT pair, and which segs are removable
        removable = {}
        for idx, t in enumerate(w.input.candidates):
            short_idxs = set()
            for p in contained_pairs(t):
                if classify(t, p, fp).startswith("REDUNDANT"):
                    short_idxs.add(p[0])
            if short_idxs:
                removable[idx] = short_idxs
        if not removable:
            continue

        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            costs, is_real = s._candidate_costs(w)
        if not is_real or sel not in costs:
            continue
        win = costs[sel].total
        coef = _fit_wl_term(w, costs)

        for idx, short_idxs in removable.items():
            c = costs.get(idx)
            if c is None or not getattr(c, "segs", None):
                continue
            t = w.input.candidates[idx]
            wl = t.estimated_wirelength
            if wl <= 0:
                continue
            if coef is None:
                unverifiable.append((w.input.original_bundle.id, idx))
                continue
            a_wl, b_env, c_seg = coef
            trimmed_len = sum(seg_len(t.segments[i]) for i in short_idxs
                              if i < len(t.segments))

            keep = [g for g in c.segs if g.seg_idx not in short_idxs]
            if not keep:
                continue
            # floor: every remaining segment's congestion driven to zero, and
            # the WL term rebuilt from its SEPARATED components.  Dividing
            # wl_term by wl folds the segment-count penalty into the WL rate,
            # and scaling that by (wl - L) then removes a penalty proportional
            # to the trimmed LENGTH instead of the one segment actually
            # dropped — which overstates the post-trim cost for a short stub
            # and would let a floor exceed a cost that is really reachable
            # (Codex #745).  The envelope term is dropped rather than
            # predicted: b_env * env_after >= 0, so omitting it keeps the
            # floor a genuine lower bound.
            floor_seg = max(g.total - g.cong for g in keep)
            n_now = float(len(t.segments))
            floor = (a_wl * (wl - trimmed_len)
                     + c_seg * max(0.0, n_now - len(short_idxs))
                     + floor_seg)

            # the exact analytic saving through channels (1) and (2) only
            seg_now = max(g.total for g in c.segs)
            seg_excl = max(g.total for g in keep)
            delta_12 = (a_wl * trimmed_len + c_seg * len(short_idxs)
                        + (seg_now - seg_excl))

            rows_out.append(dict(
                flow=flow.name, bid=w.input.original_bundle.id, idx=idx,
                cls=t.type.split("@")[0], cost=c.total, win=win,
                gap=c.total - win, floor=floor,
                flip_possible=floor <= win,
                delta_12=delta_12, trimmed_len=trimmed_len,
                feasible=c.feasible, n_short=len(short_idxs),
            ))


def main():
    rows = []
    unverifiable = []
    for f in (sys.argv[1:] or FLOWS):
        p = BUDA / f
        if not p.exists():
            print(f"-- not found: {f}")
            continue
        analyse(p, rows, unverifiable)
        print(f"scanned {f}  (cum rows {len(rows)})")

    print("\n" + "=" * 92)
    print(f"redundant-carrying candidates with a real cost: {len(rows)}")
    print(f"  UNVERIFIABLE (wl_term books not reproducible): {len(unverifiable)}")
    if not rows:
        return

    flip = [r for r in rows if r["flip_possible"]]
    infeas = [r for r in rows if not r["feasible"]]
    print(f"  STRICT-infeasible                  : {len(infeas)}")
    print(f"  flip arithmetically POSSIBLE       : {len(flip)}")
    print(f"  flip PROVABLY IMPOSSIBLE           : {len(rows) - len(flip)}")

    gaps = sorted(r["gap"] for r in rows)
    d12 = sorted(r["delta_12"] for r in rows)

    def pct(v, q):
        return v[min(len(v) - 1, int(q * len(v)))]

    print("\n  cost gap to the winner (candidate - winner):")
    print(f"    min {gaps[0]:.4g}   p25 {pct(gaps,.25):.4g}   median "
          f"{pct(gaps,.5):.4g}   p75 {pct(gaps,.75):.4g}   max {gaps[-1]:.4g}")
    print("  exact saving from channels (1)+(2) (WL + argmax relief):")
    print(f"    min {d12[0]:.4g}   p25 {pct(d12,.25):.4g}   median "
          f"{pct(d12,.5):.4g}   p75 {pct(d12,.75):.4g}   max {d12[-1]:.4g}")

    ratio = sorted(r["delta_12"] / r["gap"] for r in rows if r["gap"] > 0)
    if ratio:
        print("  saving as a FRACTION of the gap it would have to close:")
        print(f"    median {pct(ratio,.5):.4g}   p90 {pct(ratio,.9):.4g}"
              f"   max {ratio[-1]:.4g}")

    print("\n  by class:")
    for k, n in Counter(r["cls"] for r in rows).most_common():
        sub = [r for r in rows if r["cls"] == k]
        print(f"    {k:<20} n={n:<5} flip_possible="
              f"{sum(1 for r in sub if r['flip_possible']):<4}"
              f" median gap={pct(sorted(r['gap'] for r in sub), .5):.4g}")

    if flip:
        print("\n  CANDIDATES WHERE A FLIP IS NOT EXCLUDED (need the real trim):")
        for r in sorted(flip, key=lambda r: r["gap"])[:25]:
            print(f"    {r['flow']:<28} b{r['bid']:<5} topo {r['idx']+1:<4} "
                  f"{r['cls']:<18} gap={r['gap']:.4g} floor={r['floor']:.4g} "
                  f"win={r['win']:.4g} feasible={r['feasible']}")
        print("\n  feasibility of the flip-possible set: "
              + str(Counter(r['feasible'] for r in flip)))
        print("  feasibility of ALL redundant candidates: "
              + str(Counter(r['feasible'] for r in rows)))
        neg=[r for r in rows if r['gap']<0]
        print(f"  NEGATIVE-gap (cheaper than the winner) : {len(neg)}"
              f"  feasible={Counter(r['feasible'] for r in neg)}")


if __name__ == "__main__":
    main()
