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

"""flow/tcl/bigsoc.tcl — the large SoC from a NETLIST and a PDK.

The vehicle starts where a chip team starts: a Verilog netlist (authored
with `hnet.tcl`, emitted, read back with `import_verilog`) and a PDK, with
no coordinate anywhere; `auto_floorplan` makes the first floorplan and the
hier flow routes it.  This pins the chain: the census, the emitted
Verilog's shape, the import keeping every template, and the smallest dial
running end to end with the healers finding room.  The first floorplan is
NOT clean at any dial yet — the numbers live in docs/AUTO_FLOORPLAN.md —
so what is asserted is that the flow runs and reports, and that healing
moves it the right way; a test pinning "clean" here would pin a claim the
vehicle does not make."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import buda

_ROOT = Path(__file__).resolve().parents[2]
_VEHICLE = _ROOT / "flow" / "tcl" / "bigsoc.tcl"

pytestmark = [pytest.mark.mid,
              pytest.mark.skipif(shutil.which("tclsh") is None,
                                 reason="no tclsh on this host")]

_TINY = ["1", "-NC", "1", "-N", "2", "-NL3", "1", "-NMC", "1", "-NB", "1",
         "-NB2", "1", "-NB3", "1", "-NIO", "1"]


def _run(tmp_path, *args, timeout=900):
    return subprocess.run(["tclsh", str(_VEHICLE), *map(str, args)],
                          capture_output=True, encoding="utf-8",
                          errors="replace", cwd=tmp_path, timeout=timeout)


def test_the_census_counts_the_design_not_a_model_of_it(tmp_path):
    r = _run(tmp_path, 2, "-dry")
    assert r.returncode == 0, r.stdout + r.stderr
    m = re.search(r"(\d+) cell types, (\d+) instances, (\d+) leaf instances, "
                  r"(\d+) buses, (\d+) bits", r.stdout)
    assert m, r.stdout
    cells, insts, leaves, buses, bits = map(int, m.groups())
    assert cells == 33
    # NQ=2 NC=2 NB=2 NB2=4 NB3=4 NL3=2 NMC=2 N=4 PIPE=1 NIO=4:
    # per cluster 4 + 2*(1+NB) + 3 = 13 leaves x 4 clusters = 52;
    # an L2 per quadrant 2 + NB2 = 6 x 2; an L3 slice 2 + NB3 = 6 x 2;
    # mem 2 x 2; the NPU N*N + 3N + PIPE*N + 1 = 33; io 1 + NIO = 5
    assert leaves == 52 + 12 + 12 + 4 + 33 + 5 == 118
    assert insts > leaves and buses > 400 and bits > 9000
    big = _run(tmp_path, 8, "-NC", 4, "-N", 16, "-NL3", 4, "-NMC", 4, "-NIO", 16, "-dry")
    assert "834 leaf instances" in big.stdout, big.stdout


def test_the_emitted_verilog_imports_with_every_template_intact(tmp_path):
    v = tmp_path / "soc.v"
    r = _run(tmp_path, 2, "-emit", v)
    assert r.returncode == 0, r.stdout + r.stderr
    text = v.read_text()
    assert text.count("\nmodule ") + text.startswith("module ") == 33
    # an unconnected port is omitted, never written `.port()`
    assert ".p_in_0()" not in text and "()" not in text.replace("soc ()", "")
    db = buda.BDB(":memory:")
    st = db.import_verilog(str(v))
    assert st.top_module == "soc"
    comps = db.all_components()
    by_cell = {}
    for c in comps:
        by_cell[c.cell] = by_cell.get(c.cell, 0) + 1
    # the templates: a cluster four times, a row of PEs four times, a PE
    # sixteen times, an L1 eight times
    assert by_cell["cluster_cell"] == 4 and by_cell["row_cell"] == 4
    assert by_cell["pe_cell"] == 16 and by_cell["l1_cell"] == 8
    assert by_cell["quad_cell"] == 2 and by_cell["npu_cell"] == 1
    assert all(c.x1 < 0 for c in comps), "a netlist places nothing"


def test_the_smallest_dial_runs_end_to_end_and_the_healers_find_room(tmp_path):
    r = _run(tmp_path, *_TINY)
    out = r.stdout + r.stderr
    assert "=== auto_floorplan" in out and "placement audit: clean" in out, out
    first = re.search(r"first audit -- (\d+) overlaps, (\d+) unplaced", out)
    final = re.search(r"(?:FAILED|clean) -- (\d+) overlaps, (\d+) unplaced", out)
    assert first and final, out
    f_ovl, f_unpl = map(int, first.groups())
    e_ovl, e_unpl = map(int, final.groups())
    # the verdict is the audit's, exit 1 when dirty (the honest report)
    assert (r.returncode == 0) == ("clean --" in out)
    assert e_ovl + e_unpl <= f_ovl + f_unpl
    assert e_unpl < f_unpl


def test_an_unknown_knob_is_an_error(tmp_path):
    r = _run(tmp_path, 2, "-NOPE", 3, "-dry")
    assert r.returncode != 0 and "unknown parameter 'NOPE'" in r.stdout + r.stderr
