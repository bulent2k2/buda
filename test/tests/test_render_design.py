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

"""`tools/render_design.py` -- the whole-design headless renderer.

Two things are worth a guard.  The flat and the hierarchical paths draw from
different sources (the session floorplan vs. every BDB level), so each is run.
And the JSON the tool writes beside its pictures is what a caption quotes, so
its numbers are pinned against figures the engine states INDEPENDENTLY of the
renderer: `flow/soc_small.buda`'s header (which `test_soc_buda_snapshots.py`
keeps honest by re-running the flow) says 323 bundles, 9328 bit-wires and a
detailed WL of 1,968,672, and the renderer must read the same session to the
same numbers -- a picture whose caption disagrees with the flow log is worse
than no picture.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_TOOL = _ROOT / "tools/render_design.py"


def _render(tmp_path, flow, **kw):
    prefix = tmp_path / "out"
    cmd = [sys.executable, str(_TOOL), str(flow), "--out", str(prefix), "--dpi", "60"]
    for k, v in kw.items():
        cmd += ["--" + k.replace("_", "-"), str(v)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(_ROOT))
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return prefix, json.loads((tmp_path / "out_meta.json").read_text()), r.stdout


def test_flat_flow_renders_all_three_panels(tmp_path):
    """A flat flow has no BDB: blocks come from the session floorplan and the
    hierarchy is one level, said in the JSON rather than faked."""
    prefix, meta, out = _render(tmp_path, _ROOT / "demo/quickstart.buda")
    for suffix in ("_fp.png", "_nuts.png", "_dnuts.png"):
        p = Path(str(prefix) + suffix)
        assert p.exists() and p.stat().st_size > 4000, suffix
    assert meta["hierarchical"] is False
    assert meta["max_depth"] == 0
    assert meta["components"] == meta["leaves"] > 0
    assert meta["bundles"] > 0 and meta["bit_wires"] > 0
    assert meta["detailed_wl"] > meta["abstract_wl"] > 0
    assert len(meta["panels"]) == 3
    assert "wrote" in out


@pytest.mark.mid
def test_hier_flow_numbers_match_the_flows_own_header(tmp_path):
    """The recorded SoC at NQ=8: every level drawn, and the caption numbers
    are the ones its header states (bundles, bit-wires, detailed WL)."""
    flow = _ROOT / "flow/soc_small.buda"
    head = flow.read_text()
    assert "323 bundles" in head and "9328 bit-wires" in head, "header moved; re-pin"
    prefix, meta, _ = _render(tmp_path, flow, title="soc 8")
    assert meta["hierarchical"] is True
    assert meta["max_depth"] == 3          # quad / cluster / subsystem / leaf
    assert meta["bundles"] == 323
    assert meta["bit_wires"] == 9328
    assert meta["detailed_wl"] == 1968672  # the header's figure, from report_wirelength
    assert meta["overlaps"] == 0 and meta["unplaced"] == 0
    assert meta["verdicts"] and meta["verdicts"][-1].startswith("Success")
    assert "sram_cell" in meta["leaf_cells"] and len(meta["leaf_cells"]) == 11
    for suffix in ("_fp.png", "_nuts.png", "_dnuts.png"):
        assert Path(str(prefix) + suffix).exists(), suffix


def test_a_flow_without_detailed_nuts_skips_that_panel_and_says_so(tmp_path):
    """Truncate quickstart before run_detailed_nuts: two panels, one note."""
    src = (_ROOT / "demo/quickstart.buda").read_text().splitlines()
    cut = []
    for ln in src:
        if ln.strip().startswith("run_detailed_nuts"):
            break
        cut.append(ln)
    flow = tmp_path / "cut.buda"
    flow.write_text("\n".join(cut) + "\n")
    # The demo's relative paths (if any) resolve against the flow's own dir.
    for line in cut:
        assert not line.strip().startswith("source"), "quickstart sources a file; rewrite the cut"
    prefix, meta, out = _render(tmp_path, flow)
    assert Path(str(prefix) + "_nuts.png").exists()
    assert not Path(str(prefix) + "_dnuts.png").exists()
    assert meta["bit_wires"] == 0 and meta["detailed_wl"] is None
    assert "DNUTS panel skipped" in out
