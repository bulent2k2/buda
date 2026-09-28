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

"""`set_leaf_blockage policy`: a leaf blocks only the LOW layers of its band.

By default every solid leaf cell blocks every LOW layer
(`Floorplan::low_layer_keepouts`).  Under `policy` a leaf whose cell carries
a layer band blocks only the LOW layers inside it: a leaf has no
interconnect BUDA routes, so its band is exactly the statement of which
layers its own wiring uses.  The planner, abstract and detailed NUTS,
`check_design` and the judge all read the same per-leaf fact.
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
from subprocess_env import buda_env  # noqa: E402

import buda  # noqa: E402

_FLOW = _ROOT / "flow" / "leaf_blockage.buda"
_JUDGE = _ROOT / "tools" / "independent_audit.py"
_POLICY = ("set_leaf_blockage policy", "set_cell_layer_cap B M2")


def _fp():
    fp = buda.Floorplan()
    fp.add_block("A", 0, 0, 40, 100)
    fp.add_block("B", 40, 0, 60, 100)
    fp.add_block("K", 60, 0, 100, 100)
    fp.set_container("K")
    return fp


def _zones(fp, low):
    return sorted((z.bbox.x1, sorted(z.layer_ids))
                  for z in fp.low_layer_keepouts(low))


def test_keepouts_follow_the_per_leaf_set():
    fp = _fp()
    # Historical model: every leaf blocks every LOW layer, containers none.
    assert _zones(fp, [2, 3, 4]) == [(0, [2, 3, 4]), (40, [2, 3, 4])]
    fp.set_block_blocked_layers("B", [2])
    assert _zones(fp, [2, 3, 4]) == [(0, [2, 3, 4]), (40, [2])]
    assert fp.block_blocks_layer("B", 2) and not fp.block_blocks_layer("B", 3)
    assert fp.block_blocks_layer("A", 4)            # no set: every LOW layer
    assert not fp.block_blocks_layer("K", 2)        # a container: never
    # The set only OPENS layers: a TOP id in it closes nothing, because the
    # callers ask about LOW ids alone.
    fp.set_block_blocked_layers("B", [2, 7])
    assert _zones(fp, [2, 3, 4]) == [(0, [2, 3, 4]), (40, [2])]
    # A set naming no LOW layer yields NO zone -- not an empty layer list,
    # which the engine reads as blocking EVERY layer.
    fp.set_block_blocked_layers("B", [])
    assert _zones(fp, [2, 3, 4]) == [(0, [2, 3, 4])]
    fp.clear_block_blocked_layers()
    assert _zones(fp, [2, 3, 4]) == [(0, [2, 3, 4]), (40, [2, 3, 4])]


def _run(tmp_path, policy=True):
    """Run the vehicle into a checkpoint; the control strips the policy."""
    text = _FLOW.read_text()
    if not policy:
        text = "\n".join(l for l in text.splitlines() if l not in _POLICY)
    flow = tmp_path / "leaf_blockage.buda"
    flow.write_text(text)
    out = str(tmp_path / "ck.bdb")
    r = subprocess.run([sys.executable, str(_ROOT / "src" / "buda_cli.py"),
                        str(flow), "--no-viz"], capture_output=True,
                       text=True, cwd=str(_ROOT),
                       env=buda_env(_ROOT, BUDA_BDB_MEMORY_TO=out))
    # The console carries one summary line per command; the detail is in
    # the flow log beside the flow.
    log = tmp_path / "log" / "leaf_blockage_flow.log"
    text = log.read_text() if log.is_file() else ""
    return r.stdout + r.stderr + text, out


def _judge(path):
    r = subprocess.run([sys.executable, str(_JUDGE), path],
                       capture_output=True, text=True, cwd=str(_ROOT))
    return r.returncode, r.stdout + r.stderr


def test_default_model_strands_the_crossing_bus(tmp_path):
    """The control: with no way around B and no TOP layer, a leaf blocking
    every LOW layer leaves the bus nowhere to go."""
    out, _ = _run(tmp_path, policy=False)
    assert "4 bits unplaced" in out, out[-3000:]
    assert "Success: no violations found" not in out


def test_policy_opens_the_layers_above_the_band(tmp_path):
    out, _ck = _run(tmp_path)
    assert "[LeafBlock] 3 leaves: 1 block M2 (open: M3,M4,M5); " \
           "2 block every LOW layer (no band)" in out, out[-3000:]
    assert "0 bits unplaced" in out and \
           "Success: no violations found" in out, out[-3000:]


def test_band_check_exempts_leaves_only(tmp_path):
    """A leaf's band need not hold both directions (a small leaf using M2
    alone is the ordinary case); a container's still must."""
    text = _FLOW.read_text().replace(
        "add_block C 60 0 100 100",
        "add_block C 60 0 100 100\nadd_block X 0 100 100 120 container\n"
        "set_cell_layer_cap X M2")
    flow = tmp_path / "f.buda"
    flow.write_text(text)
    r = subprocess.run([sys.executable, str(_ROOT / "src" / "buda_cli.py"),
                        str(flow), "--no-viz"], capture_output=True,
                       text=True, cwd=str(_ROOT), env=buda_env(_ROOT))
    out = r.stdout + r.stderr
    assert re.search(r"set_cell_layer_cap X M2 .*band grants no V", out), out
    assert not re.search(r"set_cell_layer_cap B M2 .*Error", out), out


@pytest.fixture(scope="module")
def soc_leafcap(tmp_path_factory):
    """`soc.tcl 1 -leafcap size`, routed to the end into a checkpoint."""
    d = tmp_path_factory.mktemp("soc_leafcap")
    ck = str(d / "soc.bdb")
    r = subprocess.run(["tclsh", str(_ROOT / "flow" / "tcl" / "soc.tcl"), "1",
                        "-leafcap", "size"], capture_output=True,
                       encoding="utf-8", cwd=d, timeout=600,
                       env=buda_env(_ROOT, BUDA_BDB_MEMORY_TO=ck))
    return r.stdout + r.stderr, ck


@pytest.mark.mid
def test_soc_leafcap_size(soc_leafcap):
    """`-leafcap size` grades the leaves by size: the 152-unit cells block
    M2 alone, fifo/tag M2..M3, memctl every LOW layer -- and the design
    routes clean under it."""
    out, _ck = soc_leafcap
    assert re.search(r"^leafcap alu_cell \S+ blocks M2\.\.M2$", out, re.M), out[-3000:]
    assert re.search(r"^leafcap tag_cell \S+ blocks M2\.\.M3$", out, re.M), out[-3000:]
    assert re.search(r"^leafcap memctl_cell \S+ blocks M2\.\.M4$", out, re.M), out[-3000:]
    assert re.search(r"^soc.tcl: clean -- ", out, re.M), out[-3000:]


@pytest.mark.mid
def test_the_judge_reads_the_leaf_policy(soc_leafcap, tmp_path):
    """The judge reads the same per-leaf fact from the checkpoint, and is not
    merely blind to leaves: with the row cleared (every leaf blocks every LOW
    layer again) the same metal lies over leaf footprints."""
    _out, ck = soc_leafcap
    code, jout = _judge(ck)
    assert code == 0, jout
    assert re.search(r"\d+ leaf component\(s\) block only the LOW layers",
                     jout), jout
    con = sqlite3.connect(ck)
    stored = con.execute("SELECT value FROM meta WHERE"
                         " key='leaf_blocked_layers'").fetchone()[0]
    con.close()
    leaf = json.loads(stored)[0]["name"]
    # An id the stored stack does not declare opens every LOW layer of that
    # leaf if read literally; it is refused instead (Codex P1 on #970).
    unknown = json.dumps([{"name": leaf, "layers": [999]}])
    # A TOP id is refused too: the session records LOW layers only, and a
    # row read literally would open every LOW layer of the leaf.
    con = sqlite3.connect(ck)
    stack = json.loads(con.execute(
        "SELECT value FROM meta WHERE key='layer_stack'").fetchone()[0])
    con.close()
    top_id = next(l["id"] for l in stack if l["top"])
    top = json.dumps([{"name": leaf, "layers": [top_id]}])
    for value, want in (("", 1), ('[{"name": "x"}]', 2), (unknown, 2),
                        (top, 2)):
        bad = str(tmp_path / f"t{want}.bdb")
        shutil.copy(ck, bad)
        con = sqlite3.connect(bad)
        con.execute("UPDATE meta SET value=? WHERE key='leaf_blocked_layers'",
                    (value,))
        con.commit()
        con.close()
        code, jout = _judge(bad)
        assert code == want, jout
        if want == 1:
            assert "KEEPOUT" in jout and "lies over the leaf cell" in jout, jout
        else:
            assert "leaf blockage" in jout, jout


def test_a_restored_mode_does_not_outlive_its_bdb(tmp_path):
    """Opening a checkpoint that stored `policy` and then one that stores
    nothing returns the session to `low`: the mode is a fact about the
    design it was read from (Codex P1 on #970)."""
    sys.path.insert(0, str(_ROOT / "tools"))
    import buda_cli  # noqa: E402
    a, b = str(tmp_path / "a.bdb"), str(tmp_path / "b.bdb")
    db = buda.BDB(a)
    db.meta_set("leaf_blockage", "policy")
    del db
    buda.BDB(b)
    s = buda_cli.BudaSession()
    s.no_viz = True
    s.do_command(f"open_bdb {a}")
    assert s._leaf_blockage == "policy"
    s.do_command(f"open_bdb {b}")
    assert s._leaf_blockage == "low"
