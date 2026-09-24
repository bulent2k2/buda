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

"""`tpu_rtl.v`, the synthesizable twin of `tpu.v` (tier 1a of the LibreLane
study, docs/internal/librelane_hier_flow.md §7.1).

The two files must describe ONE design: the same modules, the same instance
names of the same cells, the same bus widths -- tpu.v is what BUDA plans
against and tpu_rtl.v is what a synthesis flow implements, so a mismatch
would benchmark one design against a plan for another.  Pinned by parsing
both (no tool needed).  When a Yosys is on PATH (the pip-installable
`yowasp-yosys` is what this was developed with) the RTL is also synthesized
generically as a smoke test: it elaborates, has flip-flops, and the
hierarchy check passes -- the property "synthesizable" that the whole tier
rests on and that reading the text cannot establish.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_TPU = _ROOT / "flow" / "tpu"

pytestmark = pytest.mark.mid


def _instances(text):
    """{instance: cell} from every `cell inst (` line, plus the module set."""
    insts = {}
    for m in re.finditer(r"^\s*(\w+)\s+(\w+)\s*\(", text, re.M):
        cell, inst = m.group(1), m.group(2)
        if cell in ("module", "input", "output", "wire", "reg", "assign"):
            continue
        insts[inst] = cell
    mods = set(re.findall(r"^module\s+(\w+)", text, re.M))
    return insts, mods


def _widths(text):
    """{module: {port: width}} for every declared bus port."""
    out, cur = {}, None
    for ln in text.splitlines():
        m = re.match(r"^module\s+(\w+)", ln)
        if m:
            cur = m.group(1)
            out[cur] = {}
            continue
        m = re.match(r"^\s*(?:input|output)\s+(?:wire|reg)?\s*\[(\d+):0\]\s+(\w+)", ln)
        if m and cur:
            out[cur][m.group(2)] = int(m.group(1)) + 1
    return out


def test_the_rtl_twin_has_the_same_modules_instances_and_widths():
    shell = (_TPU / "tpu.v").read_text()
    rtl = (_TPU / "tpu_rtl.v").read_text()
    si, sm = _instances(shell)
    ri, rm = _instances(rtl)
    assert sm == rm == {"pe_cell", "feed_cell", "wbuf_cell", "acc_cell", "row_cell", "tpu_top"}
    assert si == ri                        # every instance, same cell, same name
    # pe_0..7 inside row_cell; at the top: 8 rows, 8 feeds, 8 wbufs, 8 x (acc + 2 pipes)
    assert len(si) == 8 + 8 + 8 + 8 + 8 * 3 == 56
    sw, rw = _widths(shell), _widths(rtl)
    for mod in ("pe_cell", "feed_cell", "wbuf_cell", "acc_cell", "row_cell"):
        for port, w in sw[mod].items():
            assert rw[mod].get(port) == w, (mod, port)
    # The twin adds exactly what synthesis needs and the planner does not
    # read: a clock and reset on every module, and pins on the top.
    for mod in rm:
        assert re.search(rf"^module {mod} \(clk, rst", rtl, re.M), mod
    assert "module tpu_top ();" in shell
    assert re.search(r"^module tpu_top \(clk, rst, a_0", rtl, re.M)


def test_the_twin_is_what_the_emitter_writes(tmp_path):
    """The checked-in file is the generator's output, byte for byte."""
    from wrapper_select import wrapper_command
    btcl = wrapper_command(_ROOT, "btcl")
    subprocess.run([*btcl, str(_ROOT / "flow/tcl/tpu.tcl"), "8", "-emit", str(tmp_path)],
                   check=True, capture_output=True, timeout=600)
    assert (tmp_path / "tpu_rtl.v").read_text() == (_TPU / "tpu_rtl.v").read_text()
    assert (tmp_path / "tpu.v").read_text() == (_TPU / "tpu.v").read_text()


def _emit(tmp_path, *args):
    from wrapper_select import wrapper_command
    btcl = wrapper_command(_ROOT, "btcl")
    subprocess.run([*btcl, str(_ROOT / "flow/tcl/tpu.tcl"), *map(str, args), "-emit", str(tmp_path)],
                   check=True, capture_output=True, timeout=600)
    return (tmp_path / "tpu_rtl.v").read_text()


@pytest.mark.parametrize("pw, term", [
    (8, "prod[7:0]"),                      # narrower than the product: truncate
    (16, "prod[15:0]"),                    # equal: `{{0{x}}, prod}` is not Verilog either
    (24, "{{8{prod[15]}}, prod}"),         # wider: sign-extend (the default)
])
def test_the_product_fits_the_psum_at_every_width(tmp_path, pw, term):
    """`-PW` below (or equal to) AW+WW used to emit a replication count of
    -8 (or 0), which is not Verilog: the emitter succeeded and synthesis
    failed on the file (Codex #876 P2)."""
    rtl = _emit(tmp_path, 4, "-PW", pw)
    line = re.search(r"p_out <= p_in \+ (.*);", rtl).group(1)
    assert line == term, line
    assert "{{-" not in rtl and "{{0{" not in rtl


_YOSYS = shutil.which("yosys") or shutil.which("yowasp-yosys")


@pytest.mark.skipif(_YOSYS is None, reason="no yosys on PATH (pip install yowasp-yosys)")
@pytest.mark.parametrize("pw", [None, 8, 16])
def test_the_twin_synthesizes(tmp_path, pw):
    # Run from tmp_path with relative paths: the WASM build only sees its cwd.
    if pw is None:
        shutil.copy(_TPU / "tpu_rtl.v", tmp_path / "tpu_rtl.v")
    else:
        _emit(tmp_path, 4, "-PW", pw)          # the truncating widths, end to end
    (tmp_path / "s.ys").write_text(
        "read_verilog tpu_rtl.v\nhierarchy -check -top tpu_top\nsynth -top tpu_top\n"
        "tee -o stat.txt stat\n")
    r = subprocess.run([_YOSYS, "-q", "-s", "s.ys"], cwd=tmp_path,
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ERROR" not in r.stdout + r.stderr
    stat = (tmp_path / "stat.txt").read_text()
    # Yosys prints one line per cell type; the count is the integer on the
    # line, wherever this version puts it relative to the name.
    # Generic synthesis maps a synchronous-reset flop to $_SDFF_* (or
    # $_SDFFE_*), a plain one to $_DFF_*: count every DFF-family cell.
    n_dff = sum(int(m.group(1)) for ln in stat.splitlines() if "DFF" in ln
                for m in [re.search(r"(\d+)", ln)] if m)
    assert n_dff > 0, stat


def _lef_sizes(tmp_path):
    lef = (tmp_path / "tpu.lef").read_text()
    return {m.group(1): (float(m.group(2)), float(m.group(3)))
            for m in re.finditer(r"MACRO (\w+).*?SIZE ([\d.]+) BY ([\d.]+)", lef, re.S)}


def test_the_accumulator_has_its_own_size_and_the_edge_size_is_its_default(tmp_path):
    """`-ACCW/-ACCH` size acc_cell alone: it holds five times feed/wbuf's
    logic (7.5), so one edge size either starves it or leaves the other two
    near-empty.  Unset, it IS the edge size, so every recorded emit is
    unchanged; set, feed/wbuf keep theirs and the die grows by the
    accumulator stack -- PIPE + 1 rows of it."""
    _emit(tmp_path / "a", 4, "-EDGEIN", 1, "-EDGEW", 60, "-EDGEH", 40)
    a = _lef_sizes(tmp_path / "a")
    assert a["acc_cell"] == a["feed_cell"] == a["wbuf_cell"] == (60.0, 40.0)
    _emit(tmp_path / "b", 4, "-EDGEIN", 1, "-EDGEW", 60, "-EDGEH", 40, "-ACCW", 100, "-ACCH", 52)
    b = _lef_sizes(tmp_path / "b")
    assert b["acc_cell"] == (100.0, 52.0) and b["feed_cell"] == b["wbuf_cell"] == (60.0, 40.0)
    die = lambda p: re.search(r"DIEAREA \( 0 0 \) \( (\d+) (\d+) \)", (p / "tpu.def").read_text()).groups()
    wa, ha = map(int, die(tmp_path / "a")); wb, hb = map(int, die(tmp_path / "b"))
    assert wb == wa, "the accumulator's width changes no die width (it sits on the PE pitch)"
    assert hb - ha == 3 * (52 - 40) * 1000, "PIPE (default 2) + 1 accumulator rows, each ACCH - EDGEH taller"


def test_an_accumulator_wider_than_the_pe_is_refused_by_the_emitter(tmp_path):
    """-ACCW above -PEW would put the last column's accumulator outside the
    die and every other one over its neighbour (Codex on #957)."""
    from wrapper_select import wrapper_command
    btcl = wrapper_command(_ROOT, "btcl")
    r = subprocess.run([*btcl, str(_ROOT / "flow/tcl/tpu.tcl"), "4", "-PEW", "100", "-ACCW", "140",
                        "-emit", str(tmp_path)], capture_output=True, encoding="utf-8", timeout=600)
    assert r.returncode != 0 and "ACCW 140 exceeds PEW 100" in (r.stdout + r.stderr)
