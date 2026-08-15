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
# The xfail here is GONE, not relaxed.  It read "reference-host-owned golden
# pending regen" — the hanan_loci default flip renumbered big2_b4_b24.buda's
# select_topology index pins, so bundle 1's pinned candidate stranded 60 bits
# off the reference host.  That regen has since landed: on 2026-08-13, under
# CI's pinned ISA (BUDA_ARCH=x86-64-v2), `tools/regen_goldens.py --verify`
# reports ALL OK (10 flows), all 9 NUTS placement goldens pass with
# BUDA_NUTS_GOLDEN_STRICT=1 (including every HOST_SENSITIVE_FLOW), and
# re-running BOTH regens rewrites every golden byte-identically — zero diff.
# The marker was outliving its cause and reporting XPASS; a stale
# strict=False xfail is worse than no marker, because it would swallow a
# genuine regression here exactly as readily as the environmental one it was
# written for.
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
