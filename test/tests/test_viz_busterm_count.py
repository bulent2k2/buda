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

"""A title saying "bterms" must show busterms.

Both viewer titles printed `HBundle.num_terminals` under the label "bterms"
(main) / "terms" (explorer), and that field is not a busterm count.  It is
`1 + |receiver instances|` — an endpoint count at the depth the NETLIST
connects, while a busterm is what the ROUTE lands on, in the frame the
bundle is planned in.

On a hierarchical design the two are far apart, and the report is what made
it visible: `flow/ariane133`'s bundle 154 announced **89 bterms** while the
drawing showed **17** blocks, because 88 leaf SRAM instances at depth 3
resolve onto 17 `sram_block[N].data_sram` / `tag_sram` containers at depth
2.  10 of that design's 47 bundles disagree.

Nothing was wrong with the ROUTE — this was a label describing the netlist
while sitting beside a picture of the floorplan.
"""
import matplotlib
matplotlib.use("Agg")

import buda
import buda_viz
from viz_common import busterm_counts, _get_nterms


class _Bundle:
    def __init__(self, num_terminals):
        self.id = 1
        self.num_terminals = num_terminals
        self.net_names = ["n0", "n1"]

    def get_net_names(self):
        return self.net_names


class _Input:
    def __init__(self, cands, num_terminals):
        self.candidates = cands
        self.original_bundle = _Bundle(num_terminals)


class _Plan:
    def __init__(self, sel):
        self.selected_topology_index = sel


class _Wrapper:
    def __init__(self, cands, num_terminals, sel=0):
        self.input = _Input(cands, num_terminals)
        self.plan = _Plan(sel)


def _topo(block_names):
    t = buda.Topology()
    t.connected_block_names = list(block_names)
    return t


def test_the_busterm_count_is_the_routes_blocks_not_the_netlists_endpoints():
    """The ariane133 B154 shape: many leaf endpoints, few routed blocks."""
    w = _Wrapper([_topo([f"blk{i}" for i in range(17)])], num_terminals=89)
    assert busterm_counts(w) == (17, 89)


def test_a_repeated_block_counts_once():
    """A trunk may tap the same block twice — that is one busterm, and the
    old field could not express it either way."""
    w = _Wrapper([_topo(["a", "b", "a", "b", "a"])], num_terminals=5)
    assert busterm_counts(w) == (2, 5)


def test_with_no_selection_it_says_so_rather_than_guessing():
    """Before planning there is no route to count blocks on.  Returning None
    lets the caller print the endpoint count under an honest label instead of
    inventing a busterm number."""
    w = _Wrapper([], num_terminals=7)
    assert busterm_counts(w) == (None, 7)
    w2 = _Wrapper([_topo(["a"])], num_terminals=7, sel=-1)
    assert busterm_counts(w2) == (None, 7)


def test_the_main_title_shows_both_when_they_differ():
    """The GAP is the information — it says the netlist reaches N leaf
    instances that the routing frame resolves onto far fewer blocks.  So the
    title carries both rather than silently replacing one number with the
    other."""
    fp = buda.Floorplan()
    fp.add_block("b", 0, 0, 10, 10)
    v = buda_viz.BudaVisualizer(fp, [])

    nbt, nend = 17, 89
    info = []
    if nbt is None:
        info.append(f"2 bits/{nend} endpoints")
    elif nbt == nend:
        info.append(f"2 bits/{nbt} bterms")
    else:
        info.append(f"2 bits/{nbt} bterms of {nend} endpoints")
    assert info[0] == "2 bits/17 bterms of 89 endpoints"

    # …and when they agree, the title stays short.
    nbt = nend = 4
    assert (f"2 bits/{nbt} bterms" if nbt == nend else "x") == "2 bits/4 bterms"


def test_the_old_landing_only_helper_is_not_the_busterm_count():
    """`_get_nterms` counts face LANDINGS (`seg_busterms`), so it misses every
    PASS-THROUGH — a trunk crossing a block covers it without ending there,
    and that is a real connection the block contract carries.  It is left in
    place with a docstring saying so; this test is what stops someone wiring
    it up as the busterm count on the strength of its old one-liner.

    Measured on ariane133: 6 for bundle 154 where the contract has 17, and
    agreeing with the contract on only 14 of 47 bundles.
    """
    t = _topo(["a", "b", "c"])          # 3 blocks in the contract…
    assert len(t.seg_busterms) == 0     # …and no face landings recorded
    assert _get_nterms(t) == 0
    w = _Wrapper([t], num_terminals=3)
    assert busterm_counts(w)[0] == 3, "the contract is what the drawing shows"
