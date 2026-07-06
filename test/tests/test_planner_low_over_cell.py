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

"""Gap A (part 1): a LOW-layer segment must never route over a leaf cell.

A LOW (non-TOP) layer cannot route over a solid leaf cell — those track slots
are keepout, so DetailedNUTS finds zero signal tracks and the bits go unplaced
("insufficient signal tracks (0)").  The planner already zeroes LOW band
capacity over cells, BUT `for_each_band`'s endpoint-face clamp — which drops the
in-cell pin-access tail of a block-attached stub — accumulated across blocks and
silently dropped the WHOLE along-extent when the segment was wholly inside a
cell or crossed one mid-span.  Such a segment then looked *free* on LOW, so when
its TOP layers overflowed the planner escaped to LOW and the bits were lost.

`CongestionPlanner::low_seg_obstructed` now flags those two cases (mid-span
crossing / wholly inside a cell) so the LOW layer is treated as blocked and the
bus routes over-the-cell on a TOP layer, accepting honest overflow rather than a
silent open.

End-to-end repro: flow/big_data_test/big2/big2_noviz.buda (the channel-stress
design).  Pre-fix it emitted 7 "insufficient signal tracks (0)" warnings and
484 unplaced bits; ~228 of those were the LOW-over-cell dumping this fix
removes.  Post-fix that symptom is gone (0 such warnings) and total unplaced
drops well below the pre-fix figure.  (The residual unplaced are TOP-layer
over-subscription — a separate gap, modelled in averaged width vs per-track.)
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
    # Detail (NUTS/planner/…) now lives once in the flow log; the terminal only
    # carries an abstract per-command summary whose headlines would double-count
    # detail lines.  Parse the log (+ stderr for crashes) instead.
    script = Path(script)
    log_path = script.parent / "log" / f"{script.stem}_flow.log"
    log_text = log_path.read_text() if log_path.exists() else ""
    return r.returncode, r.stderr + "\n" + log_text


@pytest.mark.mid
def test_big2_no_low_layer_over_cell_dumping():
    """No bus is dumped onto a LOW layer over a leaf cell (the Gap A symptom)."""
    flow = _ROOT / "flow" / "big_data_test" / "big2" / "big2_noviz.buda"
    rc, out = _run_buda(flow)
    assert rc == 0, f"non-zero exit {rc}\n{out}"

    # The precise LOW-over-cell symptom: a LOW segment over a cell has zero
    # signal tracks.  Pre-fix big2 emitted 7 of these; the fix drives them down.
    #
    # The exact residual is CPU-sensitive: the planner's escape-to-LOW decision
    # keys off overflow amounts computed in double-based NUTS math, which rounds
    # differently across ISAs under -march=native (see -ffp-contract=off in
    # CMakeLists, and the CPU-invariant guards in test_ripup_reroute.py). On the
    # ARM reference host this is 0; on x86 a couple of buses still tip onto LOW.
    # So bound it well under the pre-fix 7 (a full revert is still caught) rather
    # than require exactly 0 on every host.
    LOW_OVER_CELL_MAX = 3   # ref host: 0; x86: 2; pre-fix: 7
    low_over_cell = out.count("insufficient signal tracks (0)")
    assert low_over_cell <= LOW_OVER_CELL_MAX, (
        f"{low_over_cell} LOW-over-cell dumps (> {LOW_OVER_CELL_MAX}; Gap A regressed):\n"
        + "\n".join(l for l in out.splitlines()
                    if "insufficient signal tracks (0)" in l)
    )

    # Net improvement guard: the fix took big2 from 484 to 272 unplaced bits;
    # require it to stay well under the pre-fix figure so a regression that
    # re-dumps onto LOW (without the explicit warning) is still caught.
    import re
    m = re.search(r"(\d+) bits unplaced", out)
    assert m, f"no unplaced-bits report found\n{out}"
    unplaced = int(m.group(1))
    assert unplaced <= 320, f"unplaced bits regressed to {unplaced} (pre-fix was 484)"
