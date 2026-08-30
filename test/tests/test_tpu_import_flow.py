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

"""flow/tpu/ — the systolic array through the IMPORT path.

`flow/tcl/tpu.tcl` builds the array into the BDB (the ENGINE); this imports
it from Verilog + DEF + LEF (the READER).  It is the only vehicle here whose
imported netlist has a REPEATED CELL — every real netlist available is
uniquified, so until this the reader had never seen an array.

Two things are pinned that nothing else can pin:

  * the generated inputs are IN SYNC with the emitter that writes them, and
  * both paths route to the SAME number, which is what makes "authored once,
    elaborated into both" a checkable claim rather than an intention.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_FLOW = _ROOT / "flow" / "tpu"
_TCL = _ROOT / "flow" / "tcl" / "tpu.tcl"
_N = 8                     # the size the checked-in inputs are generated at

pytestmark = [pytest.mark.mid,
              pytest.mark.skipif(shutil.which("tclsh") is None,
                                 reason="no tclsh on this host")]


def _wl(out):
    m = re.search(r"total detailed WL = (\d+)", out)
    assert m, out[-800:]
    return int(m.group(1))


def _bundles(out):
    m = re.search(r"over (\d+) bundle\(s\) / \d+ bit-wire", out)
    return int(m.group(1)) if m else -1


def _run_buda(tmp_path):
    r = subprocess.run([str(_ROOT / "bin" / "buda"), str(_FLOW / "tpu.buda")],
                       capture_output=True, encoding="utf-8",
                       errors="replace", cwd=tmp_path, timeout=900)
    assert r.returncode == 0, r.stdout + r.stderr
    log = (_FLOW / "log" / "tpu_flow.log").read_text(errors="replace")
    return r.stdout + log


def test_the_imported_array_routes_clean(tmp_path):
    out = _run_buda(tmp_path)
    assert "Success: no violations found." in out
    assert "0 bits unplaced" in out


def test_the_reader_sees_the_ARRAY(tmp_path):
    """The point of the vehicle: a repeated cell survives the DEF+Verilog
    merge as cell-local TEMPLATES.  8 rows x 7 activation hops = 56."""
    out = _run_buda(tmp_path)
    kinds = {}
    for ln in out.splitlines():
        if ln.startswith("hb-"):
            kinds[ln.split()[2]] = kinds.get(ln.split()[2], 0) + 1
    # The templating, which IS derivable: 8 rows x 7 activation hops.
    assert kinds.get("cell:row_cell") == _N * (_N - 1), kinds
    # The edge feeds crossing a level boundary: per column/row an activation
    # in, a weight in and a psum out to the accumulator.  Asserted as the
    # shape it is rather than as a formula over the whole design — the
    # inter-row chains land in `cross-block`, and inventing an expression for
    # that count would be reverse-engineering the bundler, not pinning it.
    assert kinds.get("cross-level") == 3 * _N, kinds
    assert kinds.get("cross-block", 0) > 0, kinds


def test_both_representations_route_to_the_same_number(tmp_path):
    """THE anti-drift check.

    `flow/rv` established the rule — author once, elaborate into both — and
    the reason is that a hand-kept netlist and floorplan drift into silently
    dropped connections.  Here the two paths are independent all the way to
    the router, so an emitter that stopped matching the Tcl design shows up
    as a different WL rather than as a design nobody notices is wrong.
    """
    out = _run_buda(tmp_path)
    r = subprocess.run(["tclsh", str(_TCL), str(_N)], capture_output=True,
                       encoding="utf-8", errors="replace", cwd=tmp_path,
                       timeout=900)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _wl(out) == _wl(r.stdout), (
        f"imported {_wl(out)} vs Tcl-built {_wl(r.stdout)} — the emitter and "
        f"the flow have drifted apart")
    # ...and on how the design DECOMPOSED, not just on the total: two
    # different bundlings could in principle reach the same length.
    assert _bundles(out) == _bundles(r.stdout) > 0, (
        f"imported {_bundles(out)} bundles vs Tcl-built "
        f"{_bundles(r.stdout)}")


def test_the_checked_in_inputs_match_the_emitter(tmp_path):
    """Regenerating must be a no-op.

    The three inputs are build products kept in the tree so the flow runs
    from a fresh clone; that is only safe while they are what the emitter
    still produces.  An edit to the geometry that forgets to regenerate is
    caught HERE, byte for byte, rather than as a puzzling WL change later.
    """
    r = subprocess.run(["tclsh", str(_TCL), str(_N), "-emit", str(tmp_path)],
                       capture_output=True, encoding="utf-8",
                       errors="replace", cwd=tmp_path, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    for name in ("tpu.v", "tpu.def", "tpu.lef"):
        fresh = (tmp_path / name).read_text()
        kept = (_FLOW / name).read_text()
        assert fresh == kept, (
            f"{name} differs from what the emitter produces — regenerate: "
            f"btcl flow/tcl/tpu.tcl {_N} -emit flow/tpu")


def test_the_netlist_is_not_uniquified(tmp_path):
    """The property the whole vehicle exists to supply, asserted directly.

    A synthesized netlist has ZERO module types instantiated more than once
    (NVDLA 307/306, ariane 127/125), which is why no imported design here had
    ever exercised an array.  This one must not read that way.
    """
    v = (_FLOW / "tpu.v").read_text()
    defined = set(re.findall(r"^module\s+(\w+)", v, re.M))
    inst = re.findall(r"^\s*(\w+)\s+(\w+)\s*\(", v, re.M)
    counts = {}
    for mod, _ in inst:
        if mod in defined:
            counts[mod] = counts.get(mod, 0) + 1
    repeated = {m: c for m, c in counts.items() if c >= 2}
    assert repeated.get("pe_cell") == _N, counts
    assert repeated.get("row_cell") == _N, counts
