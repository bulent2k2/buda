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

"""set_planner_param kSegs — the segment-count penalty (opt-in, default 0).

The kWL term scores  estimated_wirelength + kSegs * n_segments, demoting
many-segment trees whose junction vias (per bit) and realization DOF the
WL estimate omits.  The b61 geometry (flow/big_data_test/b61.buda): the
WL-cheapest candidate is a 10-segment TRUNK_H+MST at estWL 15940, but a
5-segment TRUNK_V+MST (estWL 17480, +9.7%) realizes only +2.2% detailed
WL with 56% fewer per-bit vias, and a 3-segment V+H+stub shape exists at
estWL 21110.  Corpus (kSegs=500): big2 unplaced 108->0 / overlaps 4->0 at
+1.8% detWL; bigHalf unplaced 593->214, overlaps 3->1, detWL -3.1%.
"""
import contextlib
import io

import pytest

import buda_cli

pytestmark = pytest.mark.mid


@pytest.fixture(autouse=True)
def _no_ksegs_env(monkeypatch):
    """These tests assert the knob's OWN defaults — shield them from the
    BUDA_KSEGS_REL experiment env (the default-flip study hook)."""
    monkeypatch.delenv("BUDA_KSEGS_REL", raising=False)

# The b61 repro geometry (flow/big_data_test/b61.buda) inline.
_B61 = (
    "def_layer 2 M2 H 25",
    "def_layer 3 M3 V 25",
    "def_layer 4 M4 H TOP 44.44",
    "def_layer 5 M5 V TOP 50.00",
    "def_layer 6 M6 H TOP 44.44",
    "def_layer 7 M7 V TOP 50.00",
    "add_block blk_01 10680 2310 13580 3310",
    "add_block blk_22 11380 4970 13580 6870",
    "add_block blk_24 9250 7990 10950 9090",
    "add_block blk_29 11580 3780 13580 4680",
    "add_block blk_31 200 7990 2700 9190",
    "add_block blk_32 4870 9430 7470 10630",
    "add_bus bus_034[16] blk_32.p blk_01.p,blk_29.p,blk_22.p,blk_24.p,blk_31.p",
    "run_bundler",
    "generate_topologies",
)


def _plan(ksegs):
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in _B61:
            s.do_command(c)
        if ksegs:
            s.do_command(f"set_planner_param kSegs {ksegs}")
        s.do_command("run_planner")
    w = s.bundles[0]
    t = w.input.candidates[w.plan.selected_topology_index]
    return t, s


def test_ksegs_param_recognized(capsys):
    s = buda_cli.BudaSession()
    s.no_viz = True
    for c in ("def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10",
              "add_block a 0 0 10 10", "add_block b 20 0 30 10",
              "add_net n a.o b.i", "run_bundler", "generate_topologies",
              "set_planner_param kSegs 500", "run_planner"):
        s.do_command(c)
    assert "unknown param" not in capsys.readouterr().out


def test_ksegs_demotes_many_segment_trees():
    """Default: the WL-cheapest many-segment TRUNK+MST wins.  kSegs=500:
    the planner pays ~500 WL-units per segment and picks a topology with
    STRICTLY fewer segments at slightly higher estimated WL; the penalty
    is monotone (more kSegs never selects more segments)."""
    t0, _ = _plan(0)
    t500, _ = _plan(500)
    t2400, _ = _plan(2400)
    assert len(t0.segments) >= 9          # the WL-cheapest is a big tree
    assert len(t500.segments) < len(t0.segments)
    assert len(t500.segments) <= 6        # the compact-tree region
    assert t500.estimated_wirelength >= t0.estimated_wirelength
    # Monotone: cranking the penalty further never adds segments back.
    assert len(t2400.segments) <= len(t500.segments)
    # And at the high end the minimal 3-segment V+H+stub shape wins.
    assert len(t2400.segments) == 3


def test_ksegs_rel_scales_with_design_hpwl(monkeypatch):
    """kSegsRel prices each segment as a FRACTION of the design's
    max-possible HPWL (grid W+H ~ 21.7k on the b61 geometry), so 0.02
    lands in the 5-seg sweet spot like absolute ~434 — and the env hook
    BUDA_KSEGS_REL supplies the same as an experiment default, with an
    explicit set_planner_param overriding it."""
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in _B61:
            s.do_command(c)
        s.do_command("set_planner_param kSegsRel 0.02")
        s.do_command("run_planner")
    w = s.bundles[0]
    t = w.input.candidates[w.plan.selected_topology_index]
    assert len(t.segments) == 5           # the compact-tree sweet spot

    # Env default drives the same selection with no explicit param...
    monkeypatch.setenv("BUDA_KSEGS_REL", "0.02")
    s2 = buda_cli.BudaSession()
    s2.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in _B61:
            s2.do_command(c)
        s2.do_command("run_planner")
    w2 = s2.bundles[0]
    t2 = w2.input.candidates[w2.plan.selected_topology_index]
    assert len(t2.segments) == 5
    # ...and an explicit 0 overrides the env (back to the WL-cheapest).
    s3 = buda_cli.BudaSession()
    s3.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        for c in _B61:
            s3.do_command(c)
        s3.do_command("set_planner_param kSegsRel 0")
        s3.do_command("run_planner")
    w3 = s3.bundles[0]
    assert w3.plan.selected_topology_index == 0


def test_ksegs_taper_honest_weight_keeps_fanin_tree():
    """Audit G3a: the penalty charges each segment for its MEMBER-BIT share
    (seg_bit_count/nbits — the per-bit average path length), not raw
    n_segments.  A CONVERGENT fan-in tree (two 8-bit driver stubs onto one
    trunk: 3 segments, w_segs = 2.0) keeps winning at kSegs=100 where
    raw-nseg pricing would flip to the 2-seg TRUNK_V:
      old: 800 + 3*100 = 1100  >  850 + 2*100 = 1050  (tree loses)
      new: 800 + 2*100 = 1000  <  1050                (tree wins)"""
    s = buda_cli.BudaSession()
    s.no_viz = True
    cmds = ["def_layer 4 M4 H TOP 10", "def_layer 5 M5 V TOP 10",
            "add_block d1 0 0 100 100", "add_block d2 0 400 100 500",
            "add_block sink 600 200 700 300"]
    for i in range(8):
        cmds += [f"add_net n{i} d1.o{i} sink.i{i}",
                 f"add_net m{i} d2.p{i} sink.j{i}"]
    cmds += ["run_bundler CONVERGENT", "set_planner_param kSegs 100",
             "generate_topologies"]
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    w = s.bundles[0]
    # Restrict the pool to a CONTROLLED pair (robust to generation churn):
    #   A: tapered 3-seg TRUNK_H, WL 800 (two 8-bit driver stubs -> w=2.0)
    #   B: untapered 2-seg TRUNK_V, WL 850
    # kSegs=100 arithmetic: old (raw nseg): A 800+300 > B 850+200 -> B wins;
    # new (member-bit share):  A 800+200 < B 850+200 -> A wins.
    pool = list(w.input.candidates)
    a = next(c for c in pool if c.type.startswith("TRUNK_H")
             and len(c.segments) == 3 and c.estimated_wirelength == 800)
    b = next(c for c in pool if c.type.startswith("TRUNK_V")
             and len(c.segments) == 2 and c.estimated_wirelength == 850)
    w.input.candidates = [a, b]
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command("run_planner")       # derives seg_bits, then scores
    t = w.input.candidates[w.plan.selected_topology_index]
    assert t.seg_bits, "fan-in taper must be derived before planning"
    assert t.type.startswith("TRUNK_H") and len(t.segments) == 3
    assert t.estimated_wirelength == 800
    nbits = len(w.input.original_bundle.get_net_names())
    w_segs = sum((len(t.seg_bits.get(si, [])) or nbits)
                 for si in range(len(t.segments))) / nbits
    assert w_segs == 2.0                  # each driver stub carries 8/16 bits


def test_ksegs_env_default_stands_down_for_multi_trunk(monkeypatch, capsys):
    """Audit G3b intent hierarchy: explicit set_planner_param > the
    multi_trunk generation opt-in > the env default.  A design whose pools
    carry the gated two-level trees (they exist only under `multi_trunk`)
    suppresses the ENV-DEFAULT penalty entirely — the measured greedy
    coupling degrades such flows even with the trees themselves exempt —
    while an explicit kSegsRel still applies (with the trees exempt)."""
    from test_datapath_multi_trunk_qor import _route, _selected_types

    def vhv(s):
        return sum(v for k, v in _selected_types(s).items()
                   if k == "BITRUNK_VHV")

    monkeypatch.delenv("BUDA_KSEGS_REL", raising=False)
    base = _route("row", True)                    # kSegs fully off
    monkeypatch.setenv("BUDA_KSEGS_REL", "0.02")
    capsys.readouterr()
    env = _route("row", True)                     # env default → suppressed
    # Byte-identical selections to the kSegs-0 run, and the note printed.
    assert _selected_types(env) == _selected_types(base)
    assert vhv(env) == vhv(base) >= 2
    # Explicit kSegsRel on the same design DOES apply (no suppression):
    # non-tree candidates are penalized, the gated trees stay exempt.
    monkeypatch.delenv("BUDA_KSEGS_REL", raising=False)
    s = buda_cli.BudaSession()
    s.no_viz = True
    import test_datapath_multi_trunk_qor as dp
    cmds = (dp._LAYERS + dp._blocks_and_buses("row")
            + ["run_bundler", "set_planner_param kSegsRel 0.02",
               "generate_topologies multi_trunk", "run_planner"])
    with contextlib.redirect_stdout(io.StringIO()):
        for c in cmds:
            s.do_command(c)
    assert _selected_types(s) != _selected_types(base)   # penalty did act


def test_ksegs_env_default_stands_down_for_kpeak(monkeypatch):
    """Audit G4: a segment penalty is a DETOUR penalty — it overwhelms
    kPeak's sub-capacity steering (the U-detour off a loaded band costs 2
    extra segments).  kPeak is an explicit routability opt-in, so the env
    default stands down for it (same hierarchy as multi_trunk); explicit
    kSegs/kSegsRel alongside kPeak is the user's own calibration and still
    applies."""
    from test_planner_kpeak import _route

    monkeypatch.setenv("BUDA_KSEGS_REL", "0.02")
    s, sel = _route(0.2)
    # The env default stood down: the probe still takes the kPeak detour.
    assert sel["probe_0"].startswith("U_"), sel
    # And an explicit kSegsRel alongside kPeak DOES apply: the penalty
    # out-prices the steering and the probe goes straight — the calibration
    # the user owns when setting both.
    monkeypatch.delenv("BUDA_KSEGS_REL", raising=False)
    s2, sel2 = _route(0.2, extra=("set_planner_param kSegsRel 0.02",))
    assert sel2["probe_0"].startswith("I_"), sel2


def test_ksegs_default_off_keeps_selection():
    """kSegs defaults to 0 — an un-set knob must not change the planner's
    choice (the WL-cheapest candidate keeps winning)."""
    t0, s = _plan(0)
    sel = s.bundles[0].plan.selected_topology_index
    assert sel == 0                       # pool is WL-sorted; cheapest wins
    assert t0.estimated_wirelength == min(
        c.estimated_wirelength for c in s.bundles[0].input.candidates)
