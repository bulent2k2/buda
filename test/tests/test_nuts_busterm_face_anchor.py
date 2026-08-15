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
Regression: NUTS / DetailedNUTS must keep a face-tapped segment reaching its face.

A segment that taps a block face (BUSTERM) AND joins another segment (SEG) at the
same end has only its SEG connectivity recorded in rev_conn_map.  When NUTS slides
the connected stub to its charged band, the span-follow set the trunk's end to the
stub's placed track and overwrote the block-face coordinate — a silent open at the
face (check_nuts: "along-span [...] no longer reaches face_coord=...", then
DetailedNUTS: "segment disconnected" / "no pass-through/busterm connection").

This is the big2 bus_077 / blk_12 bug.  The fix records each segment's BUSTERM
face_coord and extends (never contracts) its along-span to include the face in
BOTH the abstract span-adjustment (nuts.cpp) and the per-bit pass (detailed_nuts.cpp).
See flow/big_data_test/big2/big2_b4_b24.buda.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]


def _run_buda(script):
    build_dir = _ROOT / "build"
    tools_dir = _ROOT / "tools"
    ppath = os.environ.get("PYTHONPATH", "")
    new_ppath = f"{build_dir}:{tools_dir}" + (f":{ppath}" if ppath else "")
    env = {**os.environ, "PYTHONPATH": new_ppath}
    r = subprocess.run(
        [sys.executable, str(_ROOT / "src" / "buda_cli.py"), "--no-viz", str(script)],
        capture_output=True, text=True, env=env,
    )
    return r.returncode, r.stdout + r.stderr


@pytest.mark.mid
# ENFORCED on the reference environment, lenient off it — the same rule
# test_nuts_placement_golden.py applies to its HOST_SENSITIVE_FLOWS, keyed on
# the same BUDA_NUTS_GOLDEN_STRICT that ci.md documents CI setting.
#
# The marker used to be unconditional, reading "reference-host-owned golden
# pending regen".  That regen has landed — on 2026-08-13, under CI's pinned
# ISA, `regen_goldens.py --verify` reports ALL OK (10 flows), all 9 placement
# goldens pass under STRICT, and re-running both regens rewrites every golden
# byte-identically (zero diff) — which is why it had started reporting XPASS.
#
# But "the goldens are current" is NOT "this flow is host-stable", and the
# first cut of this change conflated them (Codex #753).  This flow is NOT in
# the golden corpus at all (that is b4_bus_077.buda, a different flow), it
# pins candidates by INDEX (`select_topology 1 4` / `2 10`), and nothing here
# changed those pins or the routing code.  So the documented host-fragile
# placement can still bite a native or Windows build, where dropping the
# guard outright would turn a known environmental failure into a hard one.
#
# Conditional rather than unconditional so the marker cannot go stale in the
# same way twice: under STRICT this is an ordinary test that must pass, so CI
# reports a real regression here instead of swallowing it.
@pytest.mark.xfail(
    not os.environ.get("BUDA_NUTS_GOLDEN_STRICT"),
    strict=False,
    reason="Off the reference environment this pinned flow's select_topology "
           "index pins are host-fragile (bundle 1's pinned candidate can "
           "strand 60 bits); set BUDA_NUTS_GOLDEN_STRICT=1 to enforce. "
           "See docs/internal/hanan_loci_golden_regen.md.")
def test_big2_b4_b24_routes_cleanly():
    """Both pinned-buggy bundles now route with no opens at any stage, 0 unplaced.

    Pins TRUNK_H@y4887 (bundle 1 / blk_12 — the NUTS face-detach) and TRUNK_V@x5485
    (bundle 2 / blk_09 — the generation coverage gap).  Asserts the specific
    symptoms are gone end to end.
    """
    rc, out = _run_buda(_ROOT / "flow" / "big_data_test" / "big2" / "big2_b4_b24.buda")
    assert rc == 0, f"non-zero exit {rc}\n{out}"
    # Bundle 4 (blk_12): the NUTS face-anchor symptom.
    assert "no longer reaches face_coord" not in out, out
    assert "segment disconnected" not in out, out
    # Bundle 24 (blk_09): the generation coverage symptom.
    assert "no pass-through/busterm connection" not in out, out
    assert "no BUSTERM connection" not in out, out
    assert "no pass-through segment" not in out, out
    # And every bit lands on a real track.
    assert "0 bits unplaced" in out, out
