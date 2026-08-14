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

"""E0 — is `SELECTED+REDUNDANT == 0` surprising at all?

The #741 scan counted PAIRS and observed zero in any selected candidate.  That
number means nothing without a base rate: if a bundle carries 50 candidates,
any given candidate is selected ~2% of the time, so a handful of
redundant-carrying candidates would be expected to yield zero selections by
chance alone.

Two null models, both Poisson-binomial (each bundle is one independent trial
whose success probability is the share of its pool that carries redundancy):

  NULL A  selection is uniform over the bundle's whole pool.
          expected = sum_b (n_red_b / n_b)

  NULL B  selection is uniform over the candidates of the SAME TYPE as the
          one actually selected.  This is the confound control: if redundancy
          concentrates in MST/TRUNK+MST hybrids and hybrids rarely win for
          structural reasons (more segments, more junctions), then redundancy
          is a passenger, not a cause.  Observed-0 being consistent with NULL B
          while far below NULL A would REFUTE the cost hypothesis.

Read-only: nothing is mutated, no flow is re-planned.
"""
import contextlib
import io
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]
BUDA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BUDA / "tools"))

from scan_collinear_stubs import classify, contained_pairs  # noqa: E402

FLOWS = [
    "flow/mst_shared_leg_prefix.buda",
    "flow/mst_shared_leg_suffix.buda",
    "flow/four_blocks.buda",
    "flow/channel_stress.buda",
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


def candidate_rows(flow):
    """One row per candidate: (flow, bid, idx, type, selected, n_red, n_load)."""
    s = solve(flow)
    topo_fp = s._make_topo_fp_resolver()
    rows = []
    for w in s.bundles:
        bid = w.input.original_bundle.id
        sel = w.plan.selected_topology_index if w.plan else -1
        fp = topo_fp(w)
        for idx, t in enumerate(w.input.candidates):
            n_red = n_load = 0
            for p in contained_pairs(t):
                if classify(t, p, fp).startswith("REDUNDANT"):
                    n_red += 1
                else:
                    n_load += 1
            # The type string carries the trunk LOCUS ("TRUNK_H+MST@y2692"),
            # which makes every candidate its own singleton type and destroys
            # the stratification.  The CLASS is the part before the '@'.
            cls = t.type.split("@")[0]
            rows.append((flow, bid, idx, cls, idx == sel, n_red, n_load))
    return rows


def poisson_binomial(ps):
    """(mean, sd) of a sum of independent Bernoulli(p_i)."""
    mean = sum(ps)
    var = sum(p * (1.0 - p) for p in ps)
    return mean, math.sqrt(var)


def main():
    flows = sys.argv[1:] or FLOWS
    all_rows = []
    for f in flows:
        path = BUDA / f
        if not path.exists():
            print(f"-- not found: {f}")
            continue
        rows = candidate_rows(path)
        all_rows.extend(rows)
        n_red_cand = sum(1 for r in rows if r[5] > 0)
        print(f"{f:<42} cands={len(rows):>6}  redundant-carrying={n_red_cand:>5}")

    print("\n" + "=" * 78)

    # group by (flow, bundle)
    by_bundle = defaultdict(list)
    for r in all_rows:
        by_bundle[(r[0], r[1])].append(r)

    observed = 0
    ps_a, ps_b = [], []
    type_tab = defaultdict(lambda: Counter())

    for (_f, _b), rows in by_bundle.items():
        sel = [r for r in rows if r[4]]
        if not sel:
            continue                       # bundle never planned
        sel = sel[0]
        n = len(rows)
        n_red = sum(1 for r in rows if r[5] > 0)
        if sel[5] > 0:
            observed += 1
        ps_a.append(n_red / n)

        # NULL B: restrict the pool to the winner's own type
        same = [r for r in rows if r[3] == sel[3]]
        n_red_same = sum(1 for r in same if r[5] > 0)
        ps_b.append(n_red_same / len(same))

        for r in rows:
            key = r[3]
            type_tab[key]["cands"] += 1
            if r[5] > 0:
                type_tab[key]["red"] += 1
            if r[4]:
                type_tab[key]["sel"] += 1
                if r[5] > 0:
                    type_tab[key]["sel_red"] += 1

    ma, sa = poisson_binomial(ps_a)
    mb, sb = poisson_binomial(ps_b)
    print(f"bundles with a selection : {len(ps_a)}")
    print(f"OBSERVED selected+redundant : {observed}")
    print(f"NULL A (uniform in pool)    : expected {ma:.2f} +/- {sa:.2f}"
          f"   z = {(observed - ma) / sa if sa else float('nan'):+.2f}")
    print(f"NULL B (uniform within the winner's TYPE):"
          f" expected {mb:.2f} +/- {sb:.2f}"
          f"   z = {(observed - mb) / sb if sb else float('nan'):+.2f}")

    print("\nBY CANDIDATE TYPE (selection rate, redundant vs clean)")
    print(f"  {'type':<28} {'cands':>6} {'red':>6} {'sel':>5} {'sel_red':>8}"
          f" {'P(sel|red)':>11} {'P(sel|clean)':>13}")
    for k in sorted(type_tab, key=lambda k: -type_tab[k]["cands"]):
        c = type_tab[k]
        red, clean = c["red"], c["cands"] - c["red"]
        p_red = c["sel_red"] / red if red else float("nan")
        p_clean = (c["sel"] - c["sel_red"]) / clean if clean else float("nan")
        print(f"  {k:<28} {c['cands']:>6} {red:>6} {c['sel']:>5} "
              f"{c['sel_red']:>8} {p_red:>11.4f} {p_clean:>13.4f}")


if __name__ == "__main__":
    main()
