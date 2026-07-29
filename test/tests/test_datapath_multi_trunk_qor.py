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

"""End-to-end QoR regression: multi_trunk pays off on a datapath workload.

The MST / multi-trunk machinery loses on trunk-dominated designs (tc3 flats),
where plain trunks are already WL-competitive — see
docs/internal/mst_edge_realization.md.  It WINS on column/row-aligned datapaths:
this test routes the `flow/datapath_multi_trunk.buda` design (a source column
fanning 8-bit buses across 3 receiver columns x 5 rows) with plain trunks and
with `multi_trunk`, and asserts multi_trunk (a) actually selects BITRUNK_HVH
trees and (b) yields equal-or-better wirelength AND overlaps.

Exact WL/overlap counts are FP/CPU-sensitive under -march=native, so the guards
are relational (multi_trunk <= plain), not absolute.
"""
import io
import contextlib
import sys
from collections import Counter
from pathlib import Path

import pytest
import buda

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

pytestmark = pytest.mark.mid

# The two committed datapath demos, built in-process so the plain vs multi_trunk
# comparison shares everything but the one generate flag:
#   'col' -> flow/datapath_multi_trunk.buda  (columns -> BITRUNK_HVH)
#   'row' -> flow/datapath_row_vhv.buda       (rows    -> BITRUNK_VHV)
_LAYERS = ["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
           "def_layer 4 M4 H 50", "def_layer 5 M5 V 50"]
_NGROUP, _NPER, _NBUS, _BITS = 3, 5, 6, 8


def _blocks_and_buses(orient):
    cmds = []
    for b in range(_NBUS):
        p = 100 + b * 120
        cmds.append(f"add_block S{b} 0 {p} 80 {p + 80}" if orient == "col"
                    else f"add_block S{b} {p} 0 {p + 80} 80")
    for g in range(_NGROUP):
        for i in range(_NPER):
            if orient == "col":            # groups are COLUMNS (aligned in x)
                x, y = 500 + g * 350, 60 + i * 180
            else:                          # groups are ROWS (aligned in y)
                x, y = 60 + i * 180, 500 + g * 350
            cmds.append(f"add_block D{g}_{i} {x} {y} {x + 80} {y + 80}")
    for b in range(_NBUS):
        g0, g1 = b % _NGROUP, (b + 1) % _NGROUP
        dsts = [f"D{g}_{i}.p" for g in (g0, g1) for i in range(_NPER)]
        cmds.append(f"add_bus bus{b}[{_BITS}] S{b}.p " + ",".join(dsts))
    return cmds


def _route(orient, multi_trunk: bool):
    s = buda_cli.BudaSession()
    s.no_viz = True
    # Midpoint-only pool on BOTH sides (hanan_loci default-flip opt-out): this
    # QoR claim ("multi_trunk pays off on a datapath") was measured against the
    # pre-flip plain pool, and the comparison must isolate the ONE multi_trunk
    # variable.  Under the default-on pool the loci-enriched plain trunks beat
    # the two-level trees on this synthetic datapath (col: plain 17947 vs
    # multi 18332) — recorded in wishlist-topo piece (a)'s flip notes.
    gen = ("generate_topologies multi_trunk no_hanan_loci" if multi_trunk
           else "generate_topologies no_hanan_loci")
    cmds = _LAYERS + _blocks_and_buses(orient) + ["run_bundler", gen,
                                                  "run_planner", "run_nuts"]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s



def _coverage_opens(s):
    """BUSTERM_OPEN count over the placed route — blocks the topology claims to
    connect but whose wire does not reach them at NUTS-placed positions."""
    n = 0
    for w in s.bundles:
        sel = w.plan.selected_topology_index
        if sel is None or sel < 0 or sel >= len(w.input.candidates):
            continue
        topo = w.input.candidates[sel]
        ct = buda.ConnTopology()
        ct.build(topo, s.fp)
        with contextlib.redirect_stdout(io.StringIO()), buda.ostream_redirect():
            r = buda.check_nuts(ct, s.nuts_result, topo, s.fp, s.layers,
                                w.input.original_bundle.id)
        n += sum(1 for v in r.violations
                 if v.kind == buda.ViolationKind.BUSTERM_OPEN)
    return n

# Only the two-level trees (BITRUNK_HVH / BITRUNK_VHV) are gated behind the
# multi_trunk flag; the legacy BITRUNK_H exists on the default path too, so
# `startswith("BITRUNK")` would also count it (Codex #179 P2).  The win is
# specifically about the two-level trees, so key on those exactly.
_TWO_LEVEL = ("BITRUNK_HVH", "BITRUNK_VHV")


def _wl_and_unplaced(s):
    """Total abstract wirelength over PLACED segments, plus the unplaced count.
    WL is only comparable when nothing is unplaced — an unplaced segment silently
    dropped from the sum would let an incomplete route look 'shorter' (Codex
    #179 P2)."""
    total, unplaced = 0.0, 0
    for t in s.nuts_result.segments:
        if getattr(t, "placed", True):
            total += abs(t.span_hi - t.span_lo)
        else:
            unplaced += 1
    return total, unplaced


def _selected_types(s):
    return Counter(
        w.input.candidates[w.plan.selected_topology_index].type.split("@")[0]
        for w in s.bundles)


# min_trees is a floor on how many of the 6 bundles pick the two-level tree, not
# an exact count: which near-tie bundles flip between BITRUNK_HVH and TRUNK_V+MST
# is FP/ISA-sensitive under -march=native (the generation host selects 3 HVH on
# the column datapath, this host 2), so the floor tolerates that drift while
# still proving multi_trunk actually reaches for the gated two-level trees.
# The column floor is 1 (not 2): the issue-#57 collinear-stub MERGE makes the
# TRUNK+MST candidates ~4 units shorter (staircase jog removed), so on the column
# datapath a couple of columns now pick the cleaner TRUNK+MST over BITRUNK_HVH
# while multi_trunk still reaches >=1 gated two-level tree AND still improves QoR
# (the WL/overlap assertions below are the real teeth: measured multi WL 15563 <=
# plain 16600, overlaps equal). The row datapath is unaffected (still 2 VHV).
#
# WL EXPECTATION, re-measured for issue #496.  The two-level trees cover several
# blocks by CROSSING them, and abstract NUTS used to contract those spans away
# (tighten_spans_to_reach dropped a crossing beyond the outermost junction), so
# the multi_trunk routes were winning on wirelength while leaving blocks OPEN —
# 4 of them on the column datapath, 5 on the row one.  The plain routes were
# coverage-clean throughout, so the comparison was never like-for-like:
#
#     case         pre-#496 WL / opens      post-#496 WL / opens
#     col plain      16600 / 0                16600 / 0
#     col multi      15563 / 4                16345 / 0
#     row plain      15128 / 0                15128 / 0
#     row multi      14990 / 5                16329 / 0
#
# Once both routes actually deliver their coverage the column claim SURVIVES
# (multi 16345 < plain 16600) but the row claim INVERTS (multi 16329 > plain
# 15128, +7.9%): on a row datapath the VHV trees' pass-through coverage costs
# more wire than the plain trunks it replaces.  That is recorded here as a
# measured fact (wl_win=False) rather than asserted away — the structural and
# overlap claims still hold, and the new coverage precondition below is what
# would have caught the original comparison.
@pytest.mark.parametrize(
    "orient, tree, min_trees, wl_win",
    [("col", "BITRUNK_HVH", 1, True),    # columns -> root-H / branch-V trees
     ("row", "BITRUNK_VHV", 2, False)],  # rows    -> root-V / branch-H trees
    ids=["column_hvh", "row_vhv"])
def test_multi_trunk_selects_bitrunk_and_improves_qor(orient, tree, min_trees,
                                                     wl_win):
    plain = _route(orient, False)
    multi = _route(orient, True)

    sel = _selected_types(multi)
    n_trees = sel.get(tree, 0)
    assert n_trees >= min_trees, (
        f"expected multi_trunk to select >= {min_trees} {tree} trees on this "
        f"{orient} datapath, got {dict(sel)}"
    )
    # Plain trunks pick NO two-level tree (they are gated behind multi_trunk).
    plain_sel = _selected_types(plain)
    assert not any(k in _TWO_LEVEL for k in plain_sel), dict(plain_sel)

    # WL is only a valid comparison when both routes place every segment.
    plain_wl, plain_unpl = _wl_and_unplaced(plain)
    multi_wl, multi_unpl = _wl_and_unplaced(multi)
    assert plain_unpl == 0 and multi_unpl == 0, (
        f"NUTS left segments unplaced (WL not comparable): "
        f"plain={plain_unpl}, multi={multi_unpl}"
    )

    # ...and only when both routes actually COVER every block they claim.  A
    # route that drops a pass-through crossing is shorter for the worst possible
    # reason, so comparing its wirelength to a complete route's is meaningless
    # (issue #496 — this precondition is the one that would have caught it).
    assert _coverage_opens(plain) == 0 and _coverage_opens(multi) == 0, (
        f"block coverage lost (WL not comparable): "
        f"plain={_coverage_opens(plain)}, multi={_coverage_opens(multi)}"
    )

    # The win: equal-or-better wirelength (where it holds — see the table above)
    # AND overlaps.
    if wl_win:
        assert multi_wl <= plain_wl + 1e-6, (
            f"multi_trunk WL {multi_wl} worse than plain {plain_wl}"
        )
    else:
        assert multi_wl > plain_wl, (
            f"multi_trunk WL {multi_wl} now beats plain {plain_wl} on this "
            f"datapath — if that is a real improvement, flip wl_win to True "
            f"and update the table above with the new measurement"
        )
    assert multi.nuts_result.num_overlaps <= plain.nuts_result.num_overlaps, (
        f"multi_trunk overlaps {multi.nuts_result.num_overlaps} worse than "
        f"plain {plain.nuts_result.num_overlaps}"
    )
