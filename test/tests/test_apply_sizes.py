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

"""`apply_sizes.py` — BUDA's block sizes as emitter knobs (the H+B arm).

The die arithmetic here is transcribed from `flow/tcl/tpu_lib.tcl`, so the
first test is the one that matters: it reproduces the two MEASURED arm-H
dies (§8 step 7d, 2.427 mm² at N=4 and 6.347 mm² at N=8, both at PEPAD
100) from the emitter's parameters alone.  A transcription that drifts
from the emitter would predict a saving nobody can collect.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_T1A = Path(__file__).resolve().parents[2] / "flow" / "librelane" / "tier1a"
sys.path.insert(0, str(_T1A))
import apply_sizes as A                                    # noqa: E402

# tpu_lib.tcl's PEPAD rule: PEW = (PW + WW) * BITPITCH + PEPAD = 32*4 + pad,
# PEH = AW * BITPITCH + PEPAD = 8*4 + pad.
PEPAD100 = (32 * 4 + 100, 8 * 4 + 100)
PEPAD24 = (32 * 4 + 24, 8 * 4 + 24)


def _frag(cell, w, h, area=None, util=46.0, face_w=0.0, face_h=0.0,
          binds=("area", "area")):
    d = {"binds": {"w": binds[0], "h": binds[1]},
         "face_needs": {"w": face_w, "h": face_h}, "face": {}}
    if area is not None:
        d["area"] = {"instance_area": area, "utilization_pct": util}
    return {"cell": cell, "FP_SIZING": "absolute", "DIE_AREA": [0, 0, w, h],
            "derivation": d}


def _sizes_dir(tmp_path, frags):
    d = tmp_path / "out"
    d.mkdir()
    for f in frags:
        (d / f"{f['cell']}.json").write_text(json.dumps(f))
    return d


def _run(*args):
    r = subprocess.run([sys.executable, str(_T1A / "apply_sizes.py"), *map(str, args)],
                       capture_output=True, text=True)
    return r


def test_the_die_arithmetic_reproduces_both_measured_arm_h_dies():
    """§8 step 7d measured arm H at PEPAD 100: 2.427 mm² at N = 4 and 6.347
    at N = 8.  Both fall out of the emitter's parameters here, which is what
    licenses using this to predict a saving before paying for a run."""
    for n, want in ((4, 2.427), (8, 6.347)):
        w, h, _px, _py = A.die(n, *PEPAD100, *PEPAD100)
        assert abs(w * h / 1e6 - want) < 5e-4, (n, w, h)
    # And the checked-in N=8 set (PEPAD 24) is the DEF's own DIEAREA.
    w, h, _px, _py = A.die(8, *PEPAD24, *PEPAD24)
    assert (w, h) == (1896, 1624)
    # The pitch is the emitter's: PPX = PEW + CHAN, PPY = PEH.
    _w, _h, ppx, ppy = A.die(4, 152, 56, 152, 56)
    assert (ppx, ppy) == (152 + A.CHAN, 56)


def test_the_pe_sets_the_knobs_and_the_edge_takes_the_max(tmp_path):
    """The emitter has ONE edge size, so the three edge cells go out at the
    largest — the smallest size that holds all of them — and the cells that
    are thereby oversized are named."""
    d = _sizes_dir(tmp_path, [
        _frag("pe_cell", 178.7, 47.5),
        _frag("acc_cell", 96.0, 51.1, binds=("face", "area")),
        _frag("feed_cell", 44.2, 44.2), _frag("wbuf_cell", 44.2, 44.2)])
    r = _run(d, "--n", "8")
    assert r.returncode == 0, r.stderr
    assert "pe_cell 179 x 48, edge 96 x 52" in r.stdout          # rounded UP
    assert r.stdout.count("emitter has ONE edge cell size") == 2  # feed, wbuf
    assert "acc_cell" in r.stdout and "binds face/area" in r.stdout
    r = _run(d, "--n", "8", "--args")
    assert r.stdout.strip() == "-PEW 179 -PEH 48 -EDGEW 96 -EDGEH 52"


def test_the_baseline_comparison_reads_the_emitted_lef(tmp_path):
    d = _sizes_dir(tmp_path, [_frag("pe_cell", 178.7, 47.5),
                              _frag("acc_cell", 96.0, 51.1)])
    n8 = tmp_path / "n8"
    n8.mkdir()
    (n8 / "tpu.lef").write_text(
        "VERSION 5.8 ;\n"
        "MACRO pe_cell\n  CLASS BLOCK ;\n  SIZE 228 BY 132 ;\nEND pe_cell\n"
        "MACRO feed_cell\n  CLASS BLOCK ;\n  SIZE 228 BY 132 ;\nEND feed_cell\n"
        "END LIBRARY\n")
    r = _run(d, "--n", "8", "--baseline", n8, "--json")
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    assert j["baseline"]["pe"] == {"w": 228.0, "h": 132.0}
    assert abs(j["baseline"]["die"]["mm2"] - 6.347) < 5e-4     # the measured H die
    assert j["die_ratio"] < 0.55                               # the rule roughly halves it
    r = _run(d, "--n", "8", "--baseline", tmp_path)
    assert r.returncode != 0 and "is --baseline an emitted" in r.stderr


def test_a_size_the_placer_would_refuse_is_reported_and_never_recommended(tmp_path):
    """The demand neither `emit_block_size` nor the emitter knows about: the
    placer measures utilization against the CORE — the die minus margins,
    rounded to whole rows — not the die.  A 128 x 67 PE is 8576 um2 of die
    and 5090 of core, so it is 117 % utilised and `harm.py` predicts
    GPL-0301 (Codex #890, which the first cut of this tool recommended)."""
    assert A.usable_core(128, 67) < 128 * 67 * 0.62
    ok, util, core = A.clears_bar("pe_cell", 128, 67)
    assert not ok and util > 100 and abs(core - 5090) < 50
    assert A.clears_bar("pe_cell", 228, 132)[0]           # the PEPAD-100 size
    # The rule's own numbers are reported with the verdict, not silently used.
    d = _sizes_dir(tmp_path, [_frag("pe_cell", 128, 67, area=5964, util=46.0,
                                    face_w=128.0, face_h=34.0)])
    r = _run(d, "--n", "8")
    assert "REFUSED by the placer" in r.stdout and "% utilised" in r.stdout
    assert "above the placer's bar" in r.stdout and "--optimize-aspect" in r.stdout


def test_optimize_aspect_spends_the_free_variable_on_the_array(tmp_path):
    """The rule shapes a block by its own faces; the array spends `PPX` once
    per COLUMN, so a wide PE costs N times its width.  The search is
    constrained by the face floors AND the placer's bar."""
    d = _sizes_dir(tmp_path, [
        _frag("pe_cell", 221, 59, area=5964, util=46.0,
              face_w=128.0, face_h=34.0),
        _frag("acc_cell", 96.0, 51.1), _frag("feed_cell", 44.2, 44.2)])
    opt = json.loads(_run(d, "--n", "8", "--optimize-aspect", "--json").stdout)
    # Every cell it recommends clears the bar -- that is the point.
    assert all(c["clears"] for c in opt["checks"]), opt["checks"]
    assert opt["pe"]["w"] >= 128                       # the face floor binds the width
    assert opt["aspect_note"] and "placer's bar" in opt["aspect_note"]
    # The edge knob is grown for the same reason (acc_cell at 96 x 52 is
    # refused), and says so.
    assert "edge grown" in opt["aspect_note"]
    assert opt["edge"]["h"] > 52
    # It beats the PEPAD-100 die it exists to beat, and by less than the
    # unconstrained geometry would have claimed.
    pepad100 = A.die(8, *PEPAD100, *PEPAD100)
    assert opt["predicted_die"]["mm2"] < pepad100[0] * pepad100[1] / 1e6
    assert opt["predicted_die"]["mm2"] > 3.0           # not the 2.802 of the first cut


def test_the_refusals(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = _run(empty, "--n", "8")
    assert r.returncode != 0 and "no emit_block_size fragment" in r.stderr
    assert "size.buda" in r.stderr                       # the remedy
    d = _sizes_dir(tmp_path, [_frag("acc_cell", 96.0, 51.1)])
    r = _run(d, "--n", "8")
    assert r.returncode != 0 and "no pe_cell fragment" in r.stderr
    r = _run(d, "--n", "1")
    assert r.returncode != 0 and "--n must be at least 2" in r.stderr
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "pe_cell.json").write_text("{not json")
    r = _run(bad, "--n", "8")
    assert r.returncode != 0 and "not JSON" in r.stderr


@pytest.mark.mid
def test_it_runs_on_the_checked_in_arrays_own_fragments(tmp_path):
    """End to end on the real vehicle: size.buda's fragments, the emitted
    LEF as the baseline, and the numbers §8 step 3c records."""
    import buda_cli
    import contextlib
    import io
    out = tmp_path / "out"
    tracks = _T1A.parents[1] / "tracks" / "tracks.buda"
    tpu = _T1A.parents[1] / "tpu"
    flow = ((_T1A / "size.buda").read_text()
            .replace("source ../../tracks/tracks.buda", f"source {tracks}")
            .replace("../../tpu/", f"{tpu}/")
            .replace("out/", f"{out}/"))
    f = tmp_path / "size.buda"
    f.write_text(flow)
    s = buda_cli.BudaSession()
    s.no_viz = True
    with contextlib.redirect_stdout(io.StringIO()):
        s.do_command(f"source {f}")
    assert (out / "pe_cell.json").exists()
    r = _run(out, "--n", "8", "--baseline", _T1A.parents[1] / "tpu", "--json")
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    # From the areas size.buda declares (harm.py's own: pe_cell MEASURED at
    # 5964, the others §7.1's Yosys totals x 1.7).
    assert j["gen_args"] == "-PEW 221 -PEH 59 -EDGEW 96 -EDGEH 53"
    assert abs(j["baseline"]["die"]["mm2"] - 3.079) < 5e-3      # the PEPAD-24 set
