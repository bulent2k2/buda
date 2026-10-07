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
    assert cells == 35
    # NQ=2 NC=2 NB=2 NB2=4 NB3=4 NL3=2 NMC=2 N=4 PIPE=1 NIO=4:
    # per cluster 4 + 2*(1+NB) + 3 = 13 leaves x 4 clusters = 52;
    # an L2 per quadrant 2 + NB2 = 6 x 2; an L3 slice 2 + NB3 = 6 x 2;
    # mem 2 x 2; the NPU N*N + 3N + PIPE*N + 1 = 33; io 1 + NIO = 5
    # ... plus an L0 (tag + NB0 banks = 2) in each of the 4 cores
    assert leaves == 52 + 8 + 12 + 12 + 4 + 33 + 5 == 126
    assert insts > leaves and buses > 400 and bits > 9000
    big = _run(tmp_path, 8, "-NC", 4, "-N", 16, "-NL3", 4, "-NMC", 4, "-NIO", 16, "-dry")
    assert "898 leaf instances" in big.stdout, big.stdout


def test_the_emitted_verilog_imports_with_every_template_intact(tmp_path):
    v = tmp_path / "soc.v"
    r = _run(tmp_path, 2, "-emit", v)
    assert r.returncode == 0, r.stdout + r.stderr
    text = v.read_text()
    assert text.count("\nmodule ") + text.startswith("module ") == 35
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
    # the NPU is placed by tpu_lib.tcl's own array rule, not the engine
    assert re.search(r"npu_cell\s+\d+\s+\d+\s+\d+\s+\d+x\d+\s+fixed", out), out
    assert re.search(r"pe_cell\s+\d+\s+\d+\s+\d+\s+152x56\s+fixed", out), out
    first = re.search(r"first audit -- (\d+) overlaps, (\d+) unplaced", out)
    final = re.search(r"(?:FAILED|clean) -- (\d+) overlaps, (\d+) unplaced", out)
    assert first and final, out
    f_ovl, f_unpl = map(int, first.groups())
    e_ovl, e_unpl = map(int, final.groups())
    # the verdict is the audit's, exit 1 when dirty (the honest report)
    assert (r.returncode == 0) == ("clean --" in out)
    assert e_ovl + e_unpl <= f_ovl + f_unpl
    assert e_unpl < f_unpl


def test_the_abstract_screen_stops_before_any_bit_is_placed(tmp_path):
    r = _run(tmp_path, *_TINY, "-abstract")
    out = r.stdout + r.stderr
    assert "abstract audit --" in out and "first audit" not in out, out
    assert ("clean (abstract)" in out) == (r.returncode == 0)


def test_the_engine_places_the_array_too_when_asked(tmp_path):
    r = _run(tmp_path, *_TINY, "-abstract", "-npu", "auto")
    out = r.stdout + r.stderr
    assert "abstract audit --" in out, out
    assert not re.search(r"npu_cell\s+\d+\s+\d+\s+\d+\s+\d+x\d+\s+fixed", out)


def test_an_unknown_knob_is_an_error(tmp_path):
    r = _run(tmp_path, 2, "-NOPE", 3, "-dry")
    assert r.returncode != 0 and "unknown parameter 'NOPE'" in r.stdout + r.stderr


def test_a_checkpoint_renders_with_the_overlaps_the_run_reported(tmp_path):
    """`-save FILE` leaves the checkpoint the pipeline wrote through; a
    session reopening it with the floorplan projected and
    `load_pipeline expanded` holds the placed bus segments but no audit
    of them — `compute_nuts_metrics` recounts, and the count is the run's."""
    import contextlib
    import io
    import buda_cli
    ck = tmp_path / "ck.bdb"
    r = _run(tmp_path, *_TINY, "-abstract", "-save", ck)
    out = r.stdout + r.stderr
    m = re.search(r"abstract audit -- (\d+) overlaps", out)
    assert m and ck.exists(), out
    want = int(m.group(1))
    s = buda_cli.BudaSession()
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        for line in (f"source {_ROOT / 'flow' / 'mockpdk' / 'stack.buda'}",
                     f"open_bdb {ck}", "derive_busterms 5",
                     "add_blocks_from_bdb 0", "add_blocks_from_bdb 1 skip",
                     "add_blocks_from_bdb 2 skip", "add_blocks_from_bdb 3 skip",
                     "add_blocks_from_bdb 4 skip", "load_pipeline expanded"):
            s.run_command(line)
    assert s.nuts_result is not None and s.nuts_result.segments
    assert s.nuts_result.num_overlaps == 0 and not s.nuts_result.overlap_details
    buda.compute_nuts_metrics(s.nuts_result)
    assert s.nuts_result.num_overlaps == want
    assert len(s.nuts_result.overlap_details) == want


def test_the_emitted_top_module_is_last_whatever_the_library_declares_after_it(tmp_path):
    """`import_verilog` takes the LAST module nobody instantiates as the
    top, so the emitter must put the declared top last EXPLICITLY: a
    library cell declared after `hnet::top` and instantiated nowhere used
    to come out after it and be read as the design (Codex P1 on #973)."""
    hnet = _ROOT / "flow" / "tcl" / "hnet.tcl"
    v = tmp_path / "t.v"
    script = tmp_path / "t.tcl"
    script.write_text(f"""
source {{{hnet}}}
hnet::cell leaf
hnet::port leaf a 4 input
hnet::cell soc
hnet::port soc x 4 input
hnet::inst soc u0 leaf
hnet::net soc .x u0.a
hnet::top soc
hnet::cell unused_after_top
hnet::port unused_after_top q 1 output
hnet::emit_verilog {{{v}}}
""")
    r = subprocess.run(["tclsh", str(script)], capture_output=True, encoding="utf-8",
                       cwd=tmp_path, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    mods = re.findall(r"^module (\w+)", v.read_text(), re.M)
    assert mods[-1] == "soc", mods
    db = buda.BDB(":memory:")
    assert db.import_verilog(str(v)).top_module == "soc"


def test_a_generated_wire_name_is_fresh_in_the_modules_namespace(tmp_path):
    """A net touching no own port gets a generated wire name; a port (or an
    instance) already called `w0` would alias an unrelated internal net to
    it on import (Codex P1 on #973, round 4).  The generated name skips
    every port and instance name, and the imported design keeps the two
    nets apart."""
    hnet = _ROOT / "flow" / "tcl" / "hnet.tcl"
    v = tmp_path / "t.v"
    script = tmp_path / "t.tcl"
    script.write_text(f"""
source {{{hnet}}}
hnet::cell leaf
hnet::port leaf a 4 input
hnet::port leaf q 4 output
hnet::cell soc
hnet::port soc w0 4 input
hnet::inst soc w1 leaf
hnet::inst soc u1 leaf
hnet::net soc .w0 w1.a
hnet::net soc w1.q u1.a
hnet::top soc
hnet::emit_verilog {{{v}}}
""")
    r = subprocess.run(["tclsh", str(script)], capture_output=True, encoding="utf-8",
                       cwd=tmp_path, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    text = v.read_text()
    wires = re.findall(r"^\s*wire(?: \[[^\]]*\])? (\w+);", text, re.M)
    assert wires == ["w2"], wires              # not w0 (the port), not w1 (the instance)
    db = buda.BDB(":memory:")
    db.import_verilog(str(v))
    nets = {re.sub(r"\[\d+\]$", "", n.name) for n in db.all_nets()}   # bit nets -> bus names
    assert nets == {"w0", "w2"}, nets                                   # not w1, the instance
    comps = {c.name: c.id for c in db.all_components()}
    by_net = {}
    for cid in comps.values():
        for p in db.pins_by_comp(cid):
            by_net.setdefault(p.net_id, set()).add(re.sub(r"\[\d+\]$", "", p.pin_name))
    # per bit: the port's net reaches w1.a only; the internal net joins w1.q to u1.a
    assert sorted(sorted(v) for v in by_net.values()) == [["a"]] * 4 + [["a", "q"]] * 4, by_net


def test_an_endpoint_on_two_nets_is_refused(tmp_path):
    """The emitter connects a child pin to the net that names it, so a pin
    named by two nets would be wired to the one written last and the other
    net would import with that pin silently missing (Codex P1 on #973,
    round 4): `hnet::net` refuses the reuse at declaration, naming the net
    the endpoint is already on."""
    hnet = _ROOT / "flow" / "tcl" / "hnet.tcl"
    script = tmp_path / "t.tcl"
    script.write_text(f"""
source {{{hnet}}}
hnet::cell leaf
hnet::port leaf p 4 input
hnet::cell soc
hnet::port soc a 4 input
hnet::port soc b 4 input
hnet::inst soc u0 leaf
hnet::net soc .a u0.p
hnet::net soc .b u0.p
""")
    r = subprocess.run(["tclsh", str(script)], capture_output=True, encoding="utf-8",
                       cwd=tmp_path, timeout=60)
    assert r.returncode != 0
    assert "'u0.p' is already on net {.a u0.p}" in r.stderr, r.stderr
    script.write_text(f"""
source {{{hnet}}}
hnet::cell leaf
hnet::port leaf p 4 input
hnet::cell soc
hnet::inst soc u0 leaf
hnet::inst soc u1 leaf
hnet::net soc u0.p u1.p u0.p
""")
    r = subprocess.run(["tclsh", str(script)], capture_output=True, encoding="utf-8",
                       cwd=tmp_path, timeout=60)
    assert r.returncode != 0 and "names 'u0.p' twice" in r.stderr, r.stderr


def test_a_cell_instantiating_the_top_is_omitted_so_the_top_imports_as_the_top(tmp_path):
    """Putting the declared top LAST is not enough when a library cell
    INSTANTIATES it — a wrapper or harness nobody instantiates — because
    the reader excludes every instantiated module and would take the
    wrapper (Codex P1 on #973, round 3).  The emitter writes only the cells
    reachable from the top, says what it left out, and the file imports
    with the declared top as its top."""
    hnet = _ROOT / "flow" / "tcl" / "hnet.tcl"
    v = tmp_path / "t.v"
    script = tmp_path / "t.tcl"
    script.write_text(f"""
source {{{hnet}}}
hnet::cell leaf
hnet::port leaf a 4 input
hnet::cell soc
hnet::port soc x 4 input
hnet::inst soc u0 leaf
hnet::net soc .x u0.a
hnet::cell wrapper
hnet::port wrapper y 4 input
hnet::inst wrapper dut soc
hnet::net wrapper .y dut.x
hnet::cell spare
hnet::port spare q 1 output
hnet::top soc
hnet::emit_verilog {{{v}}}
""")
    r = subprocess.run(["tclsh", str(script)], capture_output=True, encoding="utf-8",
                       cwd=tmp_path, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2 cell(s) not reachable from top 'soc' omitted" in r.stdout, r.stdout
    assert "wrapper" in r.stdout and "spare" in r.stdout
    mods = re.findall(r"^module (\w+)", v.read_text(), re.M)
    assert mods == ["leaf", "soc"], mods
    db = buda.BDB(":memory:")
    assert db.import_verilog(str(v)).top_module == "soc"
    assert {c.name for c in db.all_components()} == {"u0"}


def test_a_net_naming_two_of_the_cells_own_ports_is_refused(tmp_path):
    """Structural Verilog joins two ports of one module only through an
    `assign`, which import_verilog does not read, so such a net would
    import with its second port silently unconnected (Codex P2 on #973):
    `hnet::net` refuses it at declaration."""
    hnet = _ROOT / "flow" / "tcl" / "hnet.tcl"
    script = tmp_path / "t.tcl"
    script.write_text(f"""
source {{{hnet}}}
hnet::cell leaf
hnet::port leaf p 4 input
hnet::cell soc
hnet::port soc a 4 input
hnet::port soc b 4 output
hnet::inst soc u0 leaf
hnet::net soc .a .b u0.p
""")
    r = subprocess.run(["tclsh", str(script)], capture_output=True, encoding="utf-8",
                       cwd=tmp_path, timeout=60)
    assert r.returncode != 0 and "names 2 of its own ports" in r.stderr, r.stdout + r.stderr
