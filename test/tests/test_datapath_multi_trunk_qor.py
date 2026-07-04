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

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
import buda_cli  # noqa: E402

pytestmark = pytest.mark.mid

# The flow/datapath_multi_trunk.buda geometry, built in-process so the plain vs
# multi_trunk comparison shares everything but the one generate flag.
_LAYERS = ["def_layer 6 M6 H TOP 50", "def_layer 7 M7 V TOP 50",
           "def_layer 4 M4 H 50", "def_layer 5 M5 V 50"]
_NCOLS, _NROWS, _NBUS, _BITS = 3, 5, 6, 8


def _blocks_and_buses():
    cmds = []
    for b in range(_NBUS):
        y = 100 + b * 120
        cmds.append(f"add_block S{b} 0 {y} 80 {y + 80}")
    for c in range(_NCOLS):
        x = 500 + c * 350
        for r in range(_NROWS):
            y = 60 + r * 180
            cmds.append(f"add_block D{c}_{r} {x} {y} {x + 80} {y + 80}")
    for b in range(_NBUS):
        c0, c1 = b % _NCOLS, (b + 1) % _NCOLS
        dsts = [f"D{c}_{r}.p" for c in (c0, c1) for r in range(_NROWS)]
        cmds.append(f"add_bus bus{b}[{_BITS}] S{b}.p " + ",".join(dsts))
    return cmds


def _route(multi_trunk: bool):
    s = buda_cli.BudaSession()
    s.no_viz = True
    gen = "generate_topologies multi_trunk" if multi_trunk else "generate_topologies"
    cmds = _LAYERS + _blocks_and_buses() + ["run_bundler", gen,
                                            "run_planner", "run_nuts"]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    return s


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


def test_multi_trunk_selects_bitrunk_and_improves_qor():
    plain = _route(False)
    multi = _route(True)

    sel = _selected_types(multi)
    n_two_level = sum(v for k, v in sel.items() if k in _TWO_LEVEL)
    assert n_two_level >= 3, (
        f"expected multi_trunk to select several two-level BITRUNK_HVH/VHV trees "
        f"on this datapath, got {dict(sel)}"
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

    # The win: equal-or-better wirelength AND overlaps.
    assert multi_wl <= plain_wl + 1e-6, (
        f"multi_trunk WL {multi_wl} worse than plain {plain_wl}"
    )
    assert multi.nuts_result.num_overlaps <= plain.nuts_result.num_overlaps, (
        f"multi_trunk overlaps {multi.nuts_result.num_overlaps} worse than "
        f"plain {plain.nuts_result.num_overlaps}"
    )
