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

"""Planner per-rect LOW obstruction on multi-rect blocks (open 3).

teg_multirect_status.md open 3 — planner split-brain: cut CAPACITY is carved
per rect (`Floorplan::low_layer_keepouts` blocks each rect of a multi-rect
block individually, so the notch between rects is routable ground), but
`CongestionPlanner::low_seg_obstructed` judged the UNION bbox from
`get_all_blocks()`.  A LOW segment lying in a routable notch was therefore
priced 9999 ("crossing the cell") and escalated to TOP — a QoR distortion on
exactly the designs the multi-rect feature exists for.

The fix gives the predicate a per-rect twin of the block cache
(`leaf_rects_cache_`): every sub-question — endpoint pin-access containment,
wholly-inside-one-cell, mid-span crossing — is answered against the
individual rects for a multi-rect block, while a single-rect block
contributes its one rect in the same order, so single-rect designs judge
byte-identically (fast tier + corpus guard that).

The end-to-end vehicle is flow/teg_notch_low.buda: A→B is a short vertical
hop inside the L-block's notch (and inside its union bbox), with TOP span_min
making LOW the honest choice.  Measured before the fix the hop escalates to
M5 (both endpoints "inside the cell" per the union); after, it routes on M3
with clean nuts+dnuts audits.
"""
import re
import subprocess
import sys
from pathlib import Path

import buda
from subprocess_env import buda_env

_ROOT = Path(__file__).parents[2]


# ── unit level: the predicate itself ─────────────────────────────────────────

def _seg(x1, y1, x2, y2):
    s = buda.Segment()
    s.start = buda.Point(x1, y1)
    s.end = buda.Point(x2, y2)
    return s


def _planner_with_L():
    """L-shaped thru block (tall arm + base; notch x=100-400, y=100-400) plus
    two small leaf blocks, LOW M2/M3 + TOP M4/M5."""
    fp = buda.Floorplan()
    fp.add_block_rects("L", [(0, 0, 100, 400), (0, 0, 400, 100)])
    fp.add_block("A", 150, 150, 250, 250)   # inside the notch (open ground)
    fp.add_block("B", 150, 300, 250, 400)   # inside the notch, above A
    ls = buda.LayerStack()
    ls.add_layer(2, "M2", buda.LayerDir.HORIZONTAL, buda.LayerType.LOW)
    ls.add_layer(3, "M3", buda.LayerDir.VERTICAL, buda.LayerType.LOW)
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL, buda.LayerType.TOP)
    pl = buda.CongestionPlanner(fp, ls)
    pl.build_congestion_map()   # builds the leaf-rect cache
    return fp, ls, pl


def test_notch_segment_not_obstructed_on_low():
    """A LOW V-segment through the notch — mid-span inside L's union bbox but
    over NO rect — is routable (the union-bbox bug judged it obstructed)."""
    _, _, pl = _planner_with_L()
    # From A's top face into B: whole hop within the union bbox of L.
    assert not pl.low_seg_obstructed(_seg(200, 250, 200, 300), 3)
    # Wholly in the notch with no endpoint cell at all.
    assert not pl.low_seg_obstructed(_seg(300, 260, 300, 290), 3)
    # Horizontal notch run on LOW M2.
    assert not pl.low_seg_obstructed(_seg(260, 275, 390, 275), 2)


def test_segment_through_rect_is_obstructed_on_low():
    """A LOW segment actually crossing (or wholly inside) one of L's rects is
    obstructed — the per-rect fix must not weaken the real cases."""
    _, _, pl = _planner_with_L()
    # V-segment crossing the base rect (y 0..100) mid-span at x=300.
    assert pl.low_seg_obstructed(_seg(300, -100, 300, 250), 3)
    # V-segment wholly inside the tall arm (x 0..100).
    assert pl.low_seg_obstructed(_seg(50, 150, 50, 300), 3)
    # H-segment crossing the tall arm mid-span at y=250.
    assert pl.low_seg_obstructed(_seg(-100, 250, 300, 250), 2)


def test_top_layer_never_obstructed():
    _, _, pl = _planner_with_L()
    assert not pl.low_seg_obstructed(_seg(50, 150, 50, 300), 5)
    assert not pl.low_seg_obstructed(_seg(-100, 250, 300, 250), 4)


def test_single_rect_judgment_unchanged():
    """Single-rect blocks judge exactly as before (the cache contributes the
    same one rect): union == rect, so the historical verdicts hold."""
    fp = buda.Floorplan()
    fp.add_block("C", 0, 0, 200, 200)
    fp.add_block("D", 300, 0, 500, 200)
    ls = buda.LayerStack()
    ls.add_layer(3, "M3", buda.LayerDir.VERTICAL, buda.LayerType.LOW)
    ls.add_layer(4, "M4", buda.LayerDir.HORIZONTAL, buda.LayerType.LOW)
    ls.add_layer(5, "M5", buda.LayerDir.VERTICAL, buda.LayerType.TOP)
    ls.add_layer(6, "M6", buda.LayerDir.HORIZONTAL, buda.LayerType.TOP)
    pl = buda.CongestionPlanner(fp, ls)
    pl.build_congestion_map()
    # Wholly inside C: obstructed.
    assert pl.low_seg_obstructed(_seg(50, 50, 50, 150), 3)
    # Crossing C mid-span: obstructed.
    assert pl.low_seg_obstructed(_seg(-50, 100, 250, 100), 4)
    # In the open channel between C and D: fine.
    assert not pl.low_seg_obstructed(_seg(250, 50, 250, 150), 3)
    # Endpoint pin-access tail into C, far end in the open channel: fine.
    assert not pl.low_seg_obstructed(_seg(150, 100, 250, 100), 4)


# ── end to end: the escalation is gone ───────────────────────────────────────

def test_teg_notch_low_flow_routes_notch_on_low():
    """flow/teg_notch_low.buda: the notch hop routes on LOW M3 (pre-fix it
    escalated to M5) and every audit is clean."""
    flow = _ROOT / "flow" / "teg_notch_low.buda"
    env = buda_env(_ROOT)
    r = subprocess.run(
        [sys.executable, str(_ROOT / "src" / "buda_cli.py"), "--no-viz", str(flow)],
        capture_output=True, text=True, env=env,
    )
    log_path = flow.parent / "log" / f"{flow.stem}_flow.log"
    out = r.stderr + "\n" + (log_path.read_text() if log_path.exists() else "")
    assert r.returncode == 0, f"non-zero exit {r.returncode}\n{out}"
    m = re.search(r"\[Planner\] Bundle 1 .*I_V\s+\[V→(M\d)\]", out)
    assert m, f"planner selection line not found\n{out}"
    assert m.group(1) == "M3", (
        f"notch hop landed on {m.group(1)} — the union-bbox escalation is "
        f"back (open 3 regressed)\n{out}")
    assert out.count("Success: no violations found.") == 2, out
    # The detailed metal is entirely on M3 (report_wl per-layer breakdown).
    assert re.search(r"by layer: M3=\d+", out), out
