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

"""
Unit tests for ConnTopology.compute_net_pull().

Covers the two reference flows:

flow/pull1.buda — GOOD behavior to preserve: a floating spine (Z trunk with
    no busterm of its own) pulls its outer stubs toward the far block when the
    stub's nominal position lies OUTSIDE the far stub's anchored slide
    interval.  Sliding to the own-range extreme then lands the stub as close
    as possible to its target.

flow/pull2.buda — regression: when the far stub's anchored interval CONTAINS
    the stub's nominal position, any nonzero pull makes NUTS overshoot (it
    slides the stub to its own range extreme, past the target).  The pull
    must be 0 so the stub stays at its nominal position.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import buda

_ROOT   = Path(__file__).parents[2]
FLOW    = _ROOT / "flow"
CLI     = _ROOT / "src" / "buda_cli.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candidate(fp, src, dst, type_str):
    gen = buda.TopologyGenerator(fp)
    cands = gen.generate_candidates(src, dst)
    for c in cands:
        if c.type == type_str:
            return c
    raise AssertionError(
        f"{type_str} not generated; got: {[c.type for c in cands]}")


def _pulls(topo, fp):
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    return [cs.net_pull for cs in ct.segs()]


def _pull1_fp():
    """Geometry of flow/pull1.buda."""
    fp = buda.Floorplan()
    fp.add_block("bottom", 200, 0, 300, 100)
    fp.add_block("topl",   0,   200, 100, 300)
    fp.add_block("topr",   400, 200, 500, 300)
    fp.set_global_corner_margin(10, 8)
    return fp


def _pull2_fp():
    """Geometry of flow/pull2.buda."""
    fp = buda.Floorplan()
    fp.add_block("left",         0,   0,  400, 500)
    fp.add_block("right_bot_hi", 550, 60, 620, 190)
    return fp


# ---------------------------------------------------------------------------
# pull1 — floating spine pulls stubs toward the far block (preserve)
# ---------------------------------------------------------------------------

def test_pull1_z_stubs_pull_toward_far_block():
    """Z topl->bottom: the block faces are disjoint in y (topl [208,292],
    bottom [8,92]), so each outer stub is pulled toward the far block."""
    fp = _pull1_fp()
    z = _candidate(fp, "topl", "bottom", "Z_HVH@x150@y208")
    seg0, trunk, seg2 = _pulls(z, fp)
    assert seg0 < 0, "topl stub (y=208) must pull DOWN toward bottom [8,92]"
    assert trunk == 0, "floating V trunk between the stubs is balanced"
    assert seg2 > 0, "bottom stub (y=92) must pull UP toward topl [208,292]"


def test_pull1_multicast_stubs_pull_toward_anchored_trunk():
    """TRUNK_V bottom->{topl,topr}: the trunk is anchored at bottom's top
    face (y=100); both branch stubs (y=208) are pulled down toward it."""
    fp = _pull1_fp()
    t = _candidate(fp, "bottom", ["topl", "topr"], "TRUNK_V@x250")
    trunk, stub_l, stub_r = _pulls(t, fp)
    assert trunk == 0
    assert stub_l < 0, "topl stub must pull DOWN toward the bottom anchor"
    assert stub_r < 0, "topr stub must pull DOWN toward the bottom anchor"


# ---------------------------------------------------------------------------
# pull2 — stub already inside the far stub's anchored interval: no pull
# ---------------------------------------------------------------------------

def test_pull2_z_outer_stubs_have_no_pull():
    """Z left->right_bot_hi: the left stub (y=125) lies inside the right
    stub's anchored interval [60,190], and the right stub (y=190) lies inside
    the left stub's interval [0,500].  Neither may be pulled — a nonzero pull
    previously sent the left stub to y~500 and the right stub to y~60,
    stretching the trunk from 65 to ~430 units."""
    fp = _pull2_fp()
    z = _candidate(fp, "left", "right_bot_hi", "Z_HVH@x475@y125")
    seg0, trunk, seg2 = _pulls(z, fp)
    assert seg0 == 0, "left stub is inside the far anchored interval: no pull"
    assert trunk == 0
    assert seg2 == 0, "right stub is inside the far anchored interval: no pull"


def test_pull2_l_stub_outside_far_interval_still_pulls():
    """Same floorplan, L candidate whose H stub (y=210) is ABOVE the
    right_bot_hi face [60,190]: the directional pull must remain."""
    fp = _pull2_fp()
    l = _candidate(fp, "left", "right_bot_hi", "L_HV@x550@y210")
    h_pull, _ = _pulls(l, fp)
    assert h_pull < 0, "stub above the target face must pull DOWN"


# ---------------------------------------------------------------------------
# Multicast trunk: only the endpoint-setting stub pulls toward the tapped block
# ---------------------------------------------------------------------------

def _b4_fp():
    """The five blocks of big2 bus_077 (bundle 4): driver blk_02 + receivers.
    The TRUNK_H@y4887 trunk taps blk_12 at x=6100 (right); the V stubs hang off
    it toward the left."""
    fp = buda.Floorplan()
    for n, r in {
        "blk_02":    (4280, 5350, 5445, 6300),
        "blk_22":    (4870, 2385, 6100, 3365),
        "blk_12":    (4870, 4425, 6100, 5350),
        "blk_29":    (4870, 1425, 6100, 2385),
        "io_pad_tr": (5445, 5350, 6100, 6300),
    }.items():
        fp.add_block(n, *r)
    return fp


def test_multicast_trunk_only_endpoint_stub_pulls():
    """A V stub of a busterm-tapped trunk pulls toward the tap ONLY when it sets
    the trunk's near endpoint — i.e. it is the binding far-extreme or is anchored
    at its busterm-side slide bound.  Interior or freely-sliding stubs must not be
    dragged to their slide bound for no wirelength gain.

    big2 bus_077 / TRUNK_H@y4887 taps blk_12 at x=6100.  Of the four V stubs only
    blk_02's (capped at x=5445 = the trunk's left end) is anchored/binding, so
    only it pulls; blk_29 (interior) and io_pad_tr (co-located but free to slide
    right) and blk_22 (already at the tap) must read 0.  Pre-fix all three of
    blk_02/blk_29/io_pad_tr pulled +1.
    """
    fp = _b4_fp()
    topo = _candidate(fp, "blk_02", ["blk_22", "blk_12", "blk_29", "io_pad_tr"],
                      "TRUNK_H@y4887")
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    segs = list(ct.segs())

    def stub_for(block):
        for cs in segs:
            if any(c.kind == buda.SegConnKind.BUSTERM and c.block_name == block
                   for c in cs.conns):
                return cs
        raise AssertionError(f"no stub taps {block}")

    assert stub_for("blk_02").net_pull > 0, "blk_02's binding/anchored stub must pull"
    for blk in ("blk_29", "io_pad_tr", "blk_22"):
        assert stub_for(blk).net_pull == 0, (
            f"{blk}'s stub does not set the trunk endpoint and must not pull "
            f"(got {stub_for(blk).net_pull})"
        )


def test_direct_trunk_taps_count_as_endpoints():
    """A trunk's direct BUSTERM taps are immovable endpoint-setters; an interior
    stub between a tap and another stub must not pull.

    TRUNK_H@y500 taps L at x=160 and R at x=1050 directly; M's stub is interior
    at x=510 with P's stub further right at x=800.  Pre-fix the endpoint check saw
    only SEG stubs: no SEG lay left of M, so M was judged the binding low endpoint
    and pulled +1 toward R, while the pull toward the left tap was suppressed by P.
    Counting the left tap as a competitor leaves M (interior on both sides) at 0.
    """
    fp = buda.Floorplan()
    fp.add_block("L", 0,    400, 160,  600)   # driver -> direct left tap (x=160)
    fp.add_block("R", 1050, 400, 1250, 600)   # receiver -> direct right tap (x=1050)
    fp.add_block("M", 460,  800, 560,  1000)  # interior V stub at x=510
    fp.add_block("P", 750,  800, 850,  1000)  # V stub at x=800 (right of M)
    topo = _candidate(fp, "L", ["R", "M", "P"], "TRUNK_H@y500")
    ct = buda.ConnTopology()
    ct.build(topo, fp)
    for cs in ct.segs():
        if not cs.horiz and any(c.kind == buda.SegConnKind.BUSTERM
                                and c.block_name == "M" for c in cs.conns):
            assert cs.net_pull == 0, (
                f"interior stub between a direct tap and another stub must not "
                f"pull (got {cs.net_pull})"
            )
            return
    raise AssertionError("M's interior stub not found")


# ---------------------------------------------------------------------------
# End-to-end: flow scripts place stubs sanely (regression for NUTS overshoot)
# ---------------------------------------------------------------------------

def _run_flow(name):
    build_dir = _ROOT / "build"
    tools_dir = _ROOT / "tools"
    ppath = os.environ.get("PYTHONPATH", "")
    env = {**os.environ,
           "PYTHONPATH": f"{build_dir}:{tools_dir}:{ppath}".rstrip(":")}
    r = subprocess.run(
        [sys.executable, str(CLI), "--no-viz", str(FLOW / name)],
        capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"{name} failed:\n{r.stdout}{r.stderr}"
    # Detail (NUTS/planner/…) now lives once in the flow log; the terminal only
    # carries an abstract per-command summary whose headlines would double-count
    # detail lines.  Parse the log (+ stderr for crashes) instead.
    script = FLOW / name
    log_path = script.parent / "log" / f"{script.stem}_flow.log"
    log_text = log_path.read_text() if log_path.exists() else ""
    return r.stderr + "\n" + log_text


def _layer_interval(out, layer):
    m = re.search(
        rf"\[NUTS\] {layer}: total bus width [\d.]+ units, "
        rf"spanning perpendicular interval \[([-\d.]+), ([-\d.]+)\]", out)
    assert m, f"no NUTS interval line for {layer}:\n{out}"
    return float(m.group(1)), float(m.group(2))


def test_pull2_flow_stubs_stay_within_target_face():
    """flow/pull2.buda pins the Z_HVH@x475@y125 topology.  Both outer H
    stubs (layer M6) must be placed within right_bot_hi's face extent
    [60,190].  Before the fix they were placed at y~495 and y~63."""
    out = _run_flow("pull2.buda")
    lo, hi = _layer_interval(out, "M6")
    assert lo >= 60.0 - 1e-6, f"H stubs placed below the target face: lo={lo}"
    assert hi <= 190.0 + 1e-6, f"H stubs placed above the target face: hi={hi}"


def test_pull1_flow_placement_clean_and_compact():
    """flow/pull1.buda: NUTS stays clean and all H segments stay within the
    block faces they connect ([8,92] and [208,292])."""
    out = _run_flow("pull1.buda")
    m = re.search(
        r"\[NUTS\] (\d+) segments placed.*"
        r"Interval violations: (\d+), Track overlaps: (\d+)", out)
    assert m, "NUTS summary line not found"
    assert int(m.group(2)) == 0, "interval violations"
    assert int(m.group(3)) == 0, "track overlaps"
    lo, hi = _layer_interval(out, "M4")
    assert lo >= 8.0 - 1e-6
    assert hi <= 292.0 + 1e-6
