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

"""BUDA_TOPO_SORT=segs — the segments-first candidate-order EXPERIMENT toggle.

Sorts each bundle's candidate pool by ascending segment count (wirelength
second) instead of pure wirelength.  Measured verdict (b61 + big2/bigHalf/
b44, see flow/big_data_test/b61.buda): the order does NOT change the
planner's cost-driven selection; it relabels indices — breaking
index-pinned scripts (b44's `select_topology 1 10` lands on a different
shape) — and steers ripup's index-window alternates slightly WORSE on
big2 (+1.1% detWL vs wirelength-ordered windows).  kSegs is the effective
lever; the toggle stays for experimentation, default off.
"""
import contextlib
import io

import pytest

import buda_cli

from test_planner_ksegs import _B61

pytestmark = pytest.mark.mid


def _gen(monkeypatch, mode):
    if mode:
        monkeypatch.setenv("BUDA_TOPO_SORT", mode)
    else:
        monkeypatch.delenv("BUDA_TOPO_SORT", raising=False)
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in _B61:
            s.do_command(c)
    return [ (len(t.segments), t.estimated_wirelength)
             for t in s.bundles[0].input.candidates ]


def test_default_order_is_wirelength(monkeypatch):
    pool = _gen(monkeypatch, None)
    wls = [wl for _, wl in pool]
    assert wls == sorted(wls)
    # And the WL-cheapest head is a many-segment tree (the b61 signature).
    assert pool[0][0] >= 9


def test_segs_mode_orders_by_segment_count(monkeypatch):
    pool = _gen(monkeypatch, "segs")
    keys = [(n, wl) for n, wl in pool]
    assert keys == sorted(keys)
    # Rank 1 is now the minimal 3-segment V+H+stub shape.
    assert pool[0][0] == 3


def test_segs_mode_survives_candidate_accretion(monkeypatch):
    """Codex #326: generate_more_topologies re-sorts the merged pool via the
    session-side helper — it must use the same mode-aware key, or the pool
    silently reverts to wirelength order after accretion."""
    monkeypatch.setenv("BUDA_TOPO_SORT", "segs")
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in _B61:
            s.do_command(c)
        s.do_command("generate_more_topologies bus_034 multi_trunk")
    pool = [(len(t.segments), t.estimated_wirelength)
            for t in s.bundles[0].input.candidates]
    assert pool == sorted(pool)           # still segments-first after merge
    assert pool[0][0] == 3
