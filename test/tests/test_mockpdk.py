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

"""`flow/mockpdk/` — the mock PDK: its metal stack as BUDA declares it
(`stack.buda`) and as a tech LEF (`mock.tech.lef`) must agree on every
layer's direction and signal pitch, and the area model (`mock.pdk`) must
parse.  Two statements of one stack are two things that can drift; this
is what stops them."""
import re
from pathlib import Path

import buda
import hier_floorplan as hf
from slot_groups import expand_slot_groups

_ROOT = Path(__file__).resolve().parents[2]
_DIR = _ROOT / "flow" / "mockpdk"


def _stack_from_buda():
    """{name: (dir, signal pitch)} off stack.buda's own lines."""
    layers, pats = {}, {}
    for line in (_DIR / "stack.buda").read_text().splitlines():
        t = line.split("#", 1)[0].split()
        if not t:
            continue
        if t[0] == "def_layer":
            layers[int(t[1])] = (t[2], t[3])
        elif t[0] == "def_track_pattern":
            slots = expand_slot_groups("def_track_pattern", t[3:])
            pats[int(t[1])] = slots
    out = {}
    for lid, (name, d) in layers.items():
        slots = pats[lid]
        sig = [(float(slots[i + 1]), float(slots[i + 2]))
               for i in range(0, len(slots), 3) if slots[i].upper() == "SIGNAL"]
        assert sig, name
        pitch = {round(w + sp, 6) for w, sp in sig}
        assert len(pitch) == 1, (name, pitch)
        out[name] = (d, pitch.pop(), sig[0][0])
    return out


def _stack_from_lef():
    out = {}
    text = (_DIR / "mock.tech.lef").read_text()
    for m in re.finditer(r"LAYER (\w+)\s+TYPE ROUTING ;\s+DIRECTION (\w+) ;\s+"
                         r"PITCH ([\d.]+) ;\s+WIDTH ([\d.]+) ;", text):
        out[m.group(1)] = ("H" if m.group(2) == "HORIZONTAL" else "V",
                           float(m.group(3)), float(m.group(4)))
    return out


def test_the_tech_lef_is_the_stack_budas_twin():
    a, b = _stack_from_buda(), _stack_from_lef()
    assert set(a) == set(b) == {"M2", "M3", "M4", "M5", "M6", "M7"}
    for name in a:
        assert a[name] == b[name], (name, a[name], b[name])


def test_import_lef_tech_reads_the_mock_stack(tmp_path):
    """The import path: every routing layer lands with the direction and
    the pitch the LEF states."""
    stack = buda.LayerStack()
    import buda_cli  # noqa: F401  (the session is what runs the command)
    s = buda_cli.BudaSession()
    s.run_command(f"import_lef_tech {_DIR / 'mock.tech.lef'}")
    want = _stack_from_lef()
    for name, (d, pitch, _w) in want.items():
        lid = int(name[1:])
        assert s.layers.has_layer(lid), name
        got_dir = s.layers.get_layer_dir(lid)
        assert (got_dir == buda.LayerDir.HORIZONTAL) == (d == "H"), name
        grid = s.routing_grid.get_layer_grid(lid).global_pattern()
        assert abs(grid.unit_pitch() - pitch) < 1e-6, (name, grid.unit_pitch(), pitch)
    del stack


def test_the_area_model_parses_and_sizes_a_cache_bigger_than_its_pins():
    pdk = hf.load_pdk(str(_DIR / "mock.pdk"))
    face = hf.size_leaf(None, "bank_0", [], faces=(32, 0))
    bank = hf.size_leaf(pdk, "bank_0", [], faces=(32, 0))
    assert bank.binding == "area" and bank.w * bank.h > face.w * face.h
    alu = hf.size_leaf(pdk, "alu_cell", [], faces=(32, 32), total_bits=96)
    assert alu.binding == "face"
