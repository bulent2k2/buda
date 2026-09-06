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
import os
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
    # What LibreLane 3.0.11 WRITES is HH:MM:SS.mmm (its formatter's docstring
    # says h:m:s:ms, and a parser written to the docstring refused every real
    # run -- measured 2026-09-05); one step keeps the documented form, which
    # is accepted too.
    steps = {"01-verilator-lint": "00:00:01.500", "02-yosys-synthesis": "0:1:2:250",
             "03-openroad-floorplan": "00:00:10.000", "04-openroad-globalplacement": "00:00:30.000",
             "05-openroad-cts": "00:00:20.000", "06-openroad-globalrouting": "00:00:40.000",
             "07-openroad-detailedrouting": "01:00:00.000", "08-magic-streamout": "00:00:05.000",
             "09-checker-lvs": "00:00:01.000"}
    for name, t in steps.items():
        (root / name).mkdir(parents=True)
        (root / name / "runtime.txt").write_text(t)
    (root / "final").mkdir()
    (root / "final" / "metrics.json").write_text(json.dumps({
        "design__instance__area": 273115.69, "design__die__area": 700000.0,
        "timing__setup__ws": 1.23, "power__total": 0.0042, "power__internal__total": 0.0030,
        "power__switching__total": 0.0011, "power__leakage__total": 0.0001,
        "route__wirelength": 1234567, "route__drc_errors": 0}))


def test_runtimes_accounts_a_hierarchical_arms_blocks(tmp_path):
    """An H arm's row carries its blocks: wire per PLACED instance (a cell
    hardened once and placed twice is twice the wire), block time as the
    longest one (parallel) and as the sum, and the top-plus-blocks totals.
    The block-internal wire stays its own column -- it is where the pin
    template's cost shows (+60 % on the phase-0 block)."""
    top, b1, b2 = tmp_path / "top", tmp_path / "b1", tmp_path / "b2"
    for d in (top, b1, b2):
        _fake_run(d)
    (b1 / "final" / "metrics.json").write_text(json.dumps({"route__wirelength": 1000, "route__drc_errors": 0}))
    (b2 / "final" / "metrics.json").write_text(json.dumps({"route__wirelength": 50, "route__drc_errors": 2}))
    (b2 / "07-openroad-detailedrouting" / "runtime.txt").write_text("00:30:00.000")   # b2: 1969.8 s, b1: 3769.8 s
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(top),
                        "--block", f"{b1}:3", "--block", str(b2), "--json"],
                       check=True, capture_output=True, text=True)
    row = json.loads(r.stdout)
    assert row["total_s"] == 3769.8                                  # the top alone, unchanged
    assert [b["instances"] for b in row["blocks"]] == [3, 1]
    assert row["route__wirelength__blocks"] == 3 * 1000 + 50
    assert row["route__wirelength__arm"] == 1234567 + 3050
    assert row["route__drc_errors__blocks"] == 2
    assert row["blocks_wall_s"] == 3769.8 and row["blocks_cpu_s"] == 3769.8 + 1969.8
    assert row["arm_wall_s"] == 2 * 3769.8 and row["arm_cpu_s"] == round(3769.8 * 2 + 1969.8, 1)
    # A block that never finished routing has no wire to account, one that
    # never reached the DRC step has no violation count, and a top without
    # wire would make an arm total out of the blocks alone: all refused --
    # "not measured" must never read as zero (Codex #878).
    (b2 / "final" / "metrics.json").write_text(json.dumps({"route__drc_errors": 2}))
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(top), "--block", str(b2)],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "no route__wirelength" in r.stderr
    (b2 / "final" / "metrics.json").write_text(json.dumps({"route__wirelength": 50}))
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(top), "--block", str(b2)],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "no route__drc_errors" in r.stderr
    (top / "final" / "metrics.json").write_text(json.dumps({"route__drc_errors": 0}))
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(top), "--block", f"{b1}:3"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "no route__wirelength" in r.stderr and str(top) in r.stderr
    _fake_run(top := tmp_path / "top2")                      # a top without --block still reports
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(top), "--block", f"{b1}:two"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "must be an integer" in r.stderr


def test_runtimes_reads_librelane_time_format_and_groups_stages(tmp_path):
    _fake_run(tmp_path)
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(tmp_path), "--json"],
                       check=True, capture_output=True, text=True)
    row = json.loads(r.stdout)
    assert row["steps"] == 9
    assert "N" not in row and os.path.isabs(row["run"])          # outside the repo: absolute
    # A row says which point it is on its own (Codex #881): `--set` puts the
    # benchmark coordinates in, integers as integers, and a malformed one
    # is refused rather than recorded as a key with no value.
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(tmp_path), "--json",
                        "--set", "N=8", "--set", "arm=F"], check=True, capture_output=True, text=True)
    row = json.loads(r.stdout)
    assert row["N"] == 8 and row["arm"] == "F" and list(row)[:2] == ["N", "arm"]
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(tmp_path), "--set", "N"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "expected KEY=VALUE" in r.stderr
    assert row["synth_s"] == 63.8                  # 0:0:1:500 + 0:1:2:250, to 0.1 s
    assert row["floorplan+place_s"] == 40.0
    assert row["cts_s"] == 20.0
    assert row["route_s"] == 3640.0                # 40 s + 1 h
    assert row["signoff_s"] == 6.0
    assert row["other_s"] == 0.0
    assert row["total_s"] == 3769.8
    assert row["design__instance__area"] == 273115.69 and row["route__drc_errors"] == 0
    # The power BREAKDOWN, not just the total: the plan's tables need it.
    assert (row["power__internal__total"], row["power__switching__total"],
            row["power__leakage__total"]) == (0.0030, 0.0011, 0.0001)
    # A runtime.txt in neither shape is an error, not a zero.
    (tmp_path / "03-openroad-floorplan" / "runtime.txt").write_text("10s")
    r = subprocess.run([sys.executable, str(_T1A / "runtimes.py"), str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "not HH:MM:SS.mmm" in r.stderr
