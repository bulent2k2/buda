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

"""The tier-1a/1b tooling of the LibreLane study (flow/librelane/tier1*).

No LibreLane here, so this pins what the recipes hand to it: `gen.sh N`
emits a complete design directory with a config that names the files it
emitted, and `runtimes.py` reads a run directory the way LibreLane writes
one -- per-step `runtime.txt` in its `h:m:s:ms` format, `final/metrics.json`
-- and reports the stage totals and PPA metrics the benchmark tabulates.  A
wrong time parser would have made every runtime number in the write-up
wrong by a silent factor.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_T1A = _ROOT / "flow" / "librelane" / "tier1a"

pytestmark = pytest.mark.mid


def test_gen_sh_emits_a_complete_flat_design_at_n(tmp_path):
    r = subprocess.run(["bash", str(_T1A / "gen.sh"), "2"], env={**__import__("os").environ,
                       "T1A_DIR": str(tmp_path)}, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    d = tmp_path / "n2"
    for f in ("tpu_rtl.v", "tpu.v", "tpu.def", "tpu.lef", "config.json"):
        assert (d / f).exists(), f
    cfg = json.loads((d / "config.json").read_text())
    assert cfg["DESIGN_NAME"] == "tpu_top"
    assert cfg["VERILOG_FILES"] == ["dir::tpu_rtl.v"]
    assert cfg["CLOCK_PORT"] == "clk" and cfg["FP_SIZING"] == "relative"
    # N=2: two rows of two PEs.
    assert (d / "tpu_rtl.v").read_text().count("pe_cell pe_") == 2
    assert (d / "tpu_rtl.v").read_text().count("row_cell row_") == 2


def _fake_run(root):
    steps = {"01-verilator-lint": "0:0:1:500", "02-yosys-synthesis": "0:1:2:250",
             "03-openroad-floorplan": "0:0:10:0", "04-openroad-globalplacement": "0:0:30:0",
             "05-openroad-cts": "0:0:20:0", "06-openroad-globalrouting": "0:0:40:0",
             "07-openroad-detailedrouting": "1:0:0:0", "08-magic-streamout": "0:0:5:0",
             "09-checker-lvs": "0:0:1:0"}
    for name, t in steps.items():
        (root / name).mkdir(parents=True)
        (root / name / "runtime.txt").write_text(t)
    (root / "final").mkdir()
    (root / "final" / "metrics.json").write_text(json.dumps({
        "design__instance__area": 273115.69, "design__die__area": 700000.0,
        "timing__setup__ws": 1.23, "power__total": 0.0042, "route__wirelength": 1234567,
        "route__drc_errors": 0}))


def test_runtimes_reads_librelane_time_format_and_groups_stages(tmp_path):
    _fake_run(tmp_path)
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(tmp_path), "--json"],
                       check=True, capture_output=True, text=True)
    row = json.loads(r.stdout)
    assert row["steps"] == 9
    assert row["synth_s"] == 63.8                  # 0:0:1:500 + 0:1:2:250, to 0.1 s
    assert row["floorplan+place_s"] == 40.0
    assert row["cts_s"] == 20.0
    assert row["route_s"] == 3640.0                # 40 s + 1 h
    assert row["signoff_s"] == 6.0
    assert row["other_s"] == 0.0
    assert row["total_s"] == 3769.8
    assert row["design__instance__area"] == 273115.69 and row["route__drc_errors"] == 0
    # A runtime.txt not in LibreLane's format is an error, not a zero.
    (tmp_path / "03-openroad-floorplan" / "runtime.txt").write_text("10s")
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "not h:m:s:ms" in r.stderr
