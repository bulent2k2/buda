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

"""Legacy BITRUNK anchoring gate (filter_unanchored_bitrunk).

A legacy BITRUNK_H/BITRUNK_V whose endpoint blocks are ALL untapped covers every
block by a free-sliding trunk graze: it passes check_topo at nominal but opens
every bit at DetailedNUTS (bigHalf bus_038 — a 0-overlap/0-unplaced route that is
electrically open at every endpoint).  The generation gate drops such a
fully-degenerate candidate when a clean alternative survives, so the planner
falls to an anchored shape and the bundle routes cleanly.
"""
import contextlib
import io

import pytest

import buda
import buda_cli


def _tapped(topo, fp):
    """The set of blocks a candidate anchors via a busterm tap (a perpendicular
    stub landing on the block face — the `explicitly_connected` set the per-bit
    DNUTS audit exempts)."""
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return {c.block_name for cs in ct.segs() for c in cs.conns
            if c.kind == buda.SegConnKind.BUSTERM}


# ---------------------------------------------------------------------------
# Unit: a SURVIVING legacy ladder must be anchored (the gate's invariant).
# ---------------------------------------------------------------------------

# Two receiver x-columns (x~200 / x~500) at three y-rows: BITRUNK_H reaches them
# with real stubs (an anchored ladder that survives the gate).
_TWO_COL = {
    "S":  (0, 0, 60, 60),
    "A0": (200, 100, 260, 160), "A1": (200, 300, 260, 360), "A2": (200, 500, 260, 560),
    "B0": (500, 100, 560, 160), "B1": (500, 300, 560, 360), "B2": (500, 500, 560, 560),
}
_TWO_COL_DSTS = ["A0", "A1", "A2", "B0", "B1", "B2"]


def _gen(blocks, driver, dsts, multi_trunk=False):
    fp = buda.Floorplan()
    for n, (x1, y1, x2, y2) in blocks.items():
        fp.add_block(n, x1, y1, x2, y2)
    g = buda.TopologyGenerator(fp)
    g.set_layer_ids(4, 5)
    if multi_trunk:
        g.set_multi_trunk(True)
    return fp, g.generate_candidates(driver, dsts)


def test_surviving_legacy_bitrunk_is_anchored():
    # Every legacy BITRUNK_H/BITRUNK_V that survives generation must anchor at
    # least one endpoint (non-empty busterm-tap set) — a fully-unanchored ladder
    # is the bus_038 failure and must have been dropped.  This layout keeps an
    # anchored BITRUNK_H, so the check is non-vacuous.
    fp, cands = _gen(_TWO_COL, "S", _TWO_COL_DSTS, multi_trunk=True)
    ladders = [c for c in cands if c.type in ("BITRUNK_H", "BITRUNK_V")]
    assert ladders, "expected at least one surviving legacy BITRUNK ladder"
    for c in ladders:
        assert _tapped(c, fp), \
            f"a fully-unanchored {c.type} survived the gate (no busterm tap)"


# ---------------------------------------------------------------------------
# End-to-end: bus_038's degenerate BITRUNK_H is dropped and it routes clean.
# ---------------------------------------------------------------------------

def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    return s


def _run(session, *cmds):
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        for c in cmds:
            session.do_command(c)


def _check_design_out(session):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        session.do_command("check_design")
    return buf.getvalue()


# bus_038 in tc3a_flat_5x is a 56-bit fan-out (blk_04 -> blk_30, blk_26,
# io_pad_bl) whose only legacy BITRUNK_H "covers" all four endpoints by a
# free-sliding H-trunk graze (tapped={}); before the gate it routed to 168 =
# 3x56 per-bit BUSTERM_OPENs at DNUTS with 0 NUTS overlaps / 0 unplaced bits.
_REPRO_CMDS = (
    "source ../tracks/tracks4top.buda",
    "source tc3a_flat_5x.buda",
    "run_bundler",
    "generate_topologies_for_bundle bus_038 no_hanan_loci",
)


@pytest.mark.mid
def test_bus038_routes_clean_after_gate(monkeypatch):
    monkeypatch.chdir("flow/big_data_test")
    s = _session()
    _run(s, *_REPRO_CMDS, "run_planner", "run_nuts", "run_detailed_nuts")
    assert s.detailed_result is not None
    assert s.detailed_result.num_unplaced == 0          # placed...
    out = _check_design_out(s)
    assert "no violations" in out, out                  # ...AND electrically complete


@pytest.mark.mid
def test_bus038_degenerate_bitrunk_h_dropped(monkeypatch):
    # bus_038's single BITRUNK_H is fully-degenerate (tapped={}); the gate must
    # remove it, so no BITRUNK_H survives in the pool, and any BITRUNK that DID
    # survive would have to be anchored.
    monkeypatch.chdir("flow/big_data_test")
    s = _session()
    _run(s, *_REPRO_CMDS)
    w = next(b for b in s.bundles
             if b.input.original_bundle.get_net_names()[0].startswith("bus_038"))
    fp = s._make_topo_fp_resolver()(w)
    assert not any(c.type == "BITRUNK_H" for c in w.input.candidates), \
        "bus_038's degenerate BITRUNK_H must be dropped by the gate"
    for c in w.input.candidates:
        if c.type in ("BITRUNK_H", "BITRUNK_V"):
            assert _tapped(c, fp), f"a fully-unanchored {c.type} survived the gate"
