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

"""Setup-command regressions from the 2026-07 audit (P5-01, P5-02) — the
first direct coverage of the setup_cmds wrapper layer."""
import contextlib
import io

import buda_cli


def _session():
    s = buda_cli.BudaSession()
    s.no_viz = True
    return s


def _run(s, cmd):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s.do_command(cmd)
    return buf.getvalue()


def test_def_layer_rejects_bad_direction_token():
    # Audit P5-01: any token that was not exactly 'H' silently became
    # VERTICAL (typos, swapped args). Must fail fast instead.
    s = _session()
    out = _run(s, "def_layer 4 M4 X TOP 44.44")
    assert "Error" in out and "H or V" in out
    assert s.layers.get_layer_ids_by_dir(__import__("buda").LayerDir.VERTICAL) == []


def test_def_layer_accepts_lowercase_direction():
    s = _session()
    _run(s, "def_layer 4 M4 h TOP 44.44")
    import buda
    assert 4 in list(s.layers.get_layer_ids_by_dir(buda.LayerDir.HORIZONTAL))


def test_add_bus_descending_range_creates_all_nets():
    # Audit P5-02: bus[7:0] made range(lo, hi+1) empty -> ZERO nets created,
    # silently. The Verilog-style descending range must normalize to 0..7.
    s = _session()
    _run(s, "add_block A 0 0 100 100")
    _run(s, "add_block B 300 0 400 100")
    _run(s, "add_bus w[7:0] A.o B.i")
    names = {n for n in s._net_endpoints if n.startswith("w_")}
    assert names == {f"w_{i}" for i in range(8)}, names
