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

"""`src/hier_floorplan.py` — the geometry of a first hierarchical floorplan,
on its own (no compiled extension): the PDK reader, the face rules, the
packers.  Every number here is one the SoC and TPU vehicles measured on
the way to `auto_floorplan` (docs/AUTO_FLOORPLAN.md)."""
import pytest

import hier_floorplan as hf
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PDK = _ROOT / "flow" / "mockpdk" / "mock.pdk"


# ── the PDK ───────────────────────────────────────────────────────────────

def test_the_mock_pdk_parses_and_matches_first_glob_wins():
    pdk = hf.load_pdk(str(_PDK))
    assert pdk.bit_pitch == 4.0 and pdk.pad == 24
    # `bank_*` before `*`: a bank is an SRAM, an unknown cell is logic
    assert pdk.rule_for("bank_3").kind == "sram"
    assert pdk.rule_for("whatever_cell").kind == "logic"
    assert pdk.rule_for("phy_cell").kind == "macro"


@pytest.mark.parametrize("bad, why", [
    ("bogus 1", "unknown key"),
    ("leaf x sram", "needs bits"),
    ("leaf x logic gates_per_bit nope", "wants a number"),
    ("leaf x macro w 10", "needs w and h"),
    ("leaf x logic aspect 0.5", "aspect must be >= 1"),
    ("util 0", "must be positive"),
])
def test_a_malformed_pdk_is_refused_loudly(bad, why):
    with pytest.raises(ValueError, match=why):
        hf.parse_pdk(bad, "t.pdk")


# ── the face rules ────────────────────────────────────────────────────────

def test_face_pair_reads_tpu_libs_pe_rule_off_the_loads():
    # a PE: psum+weight north (32), psum+weight south (32), activation in
    # (8), out (8) -> the heavy pair carries 32, the light pair 8
    assert hf.face_bits_pair([32, 32, 8, 8]) == (32, 8)
    heavy, light = hf.face_pair([32, 32, 8, 8], 4.0, 24)
    assert (heavy, light) == (152, 56)       # PEW / PEH of tpu_lib.tcl


def test_the_light_face_hosts_its_biggest_bundle_not_the_sum():
    # a row of 8 PEs: 256 in, eight 24-bit outputs to eight accumulators —
    # the outputs leave one face together, so the light face reads 24, not
    # 72 (measured: the sum sized the row 312 tall)
    assert hf.face_bits_pair([256] + [24] * 8) == (256, 24)


def test_size_leaf_is_floored_by_the_faces_and_grown_by_the_area():
    pdk = hf.load_pdk(str(_PDK))
    alu = hf.size_leaf(pdk, "alu_cell", [], faces=(32, 32), total_bits=96)
    assert alu.binding == "face" and (alu.w, alu.h) == (152, 152)
    bank = hf.size_leaf(pdk, "l3bank_0", [], faces=(32, 0), total_bits=48)
    assert bank.binding == "area"
    assert bank.w >= 152 and bank.h >= 48 and bank.w * bank.h >= 1048576 * 0.12 * 1.5
    # area-bound: as square as the faces allow, not a 3:1 sliver
    assert max(bank.w, bank.h) <= 1.2 * min(bank.w, bank.h)
    none = hf.size_leaf(None, "x", [], faces=(8, 0))
    assert (none.w, none.h) == (56, 48) and none.binding == "face"


# ── the packers ───────────────────────────────────────────────────────────

def _pes(n=8, w=184, h=88):
    items = [(f"pe_{i}", w, h) for i in range(n)]
    nets = [(8, [f"pe_{i}", f"pe_{i + 1}"]) for i in range(n - 1)]
    return items, nets


def _legal(p, items):
    sizes = {nm: (w, h) for nm, w, h in items}
    boxes = [(nm, p.pos[nm][0], p.pos[nm][1],
              p.pos[nm][0] + sizes[nm][0], p.pos[nm][1] + sizes[nm][1])
             for nm, _w, _h in items]
    for i, (a, ax1, ay1, ax2, ay2) in enumerate(boxes):
        assert ax1 >= 0 and ay1 >= 0 and ax2 <= p.w and ay2 <= p.h, (a, p)
        for b, bx1, by1, bx2, by2 in boxes[i + 1:]:
            assert not (ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2), (a, b)


def test_slice_pack_takes_the_line_its_face_asks_for():
    items, nets = _pes()
    # a row of PEs carrying 256 bits across one face wants a 1048-wide
    # line, which the 2:1 aspect cap used to forbid (padded to 57 %)
    p = hf.slice_pack(items, 16, 16, nets, wl_weight=0.25, floors=(1048, 120))
    p = hf.pad_packing(p, (1048, 120))
    _legal(p, items)
    assert p.h == 120 and p.w == 8 * 184 + 7 * 16 + 32
    assert hf.utilization(p, items) > 0.6


def test_slice_pack_keeps_a_square_when_nothing_asks_otherwise():
    items = [("dec", 152, 152), ("alu", 152, 152), ("mul", 152, 152), ("regf", 160, 160)]
    nets = [(32, ["dec", "alu"]), (32, ["mul", "regf"]), (32, ["regf", "alu"])]
    p = hf.slice_pack(items, 16, 16, nets, wl_weight=0.25)
    _legal(p, items)
    assert max(p.w, p.h) <= 2 * min(p.w, p.h)
    assert hf.utilization(p, items) > 0.7


def test_the_boundary_pull_puts_the_port_carrier_on_an_edge():
    # an L3 slice: the controller carries four 32-bit links out of the
    # slice; without the pull it packs wherever its bank nets are
    # shortest, which can be between the bank rows
    items = [("ctl", 536, 152), ("tag", 280, 152)] + \
        [(f"bank_{b}", 435, 435) for b in range(4)]
    nets = [(48, ["ctl", f"bank_{b}"]) for b in range(4)] + [(48, ["ctl", "tag"])]
    p = hf.slice_pack(items, 16, 16, nets, wl_weight=0.25, ext={"ctl": 256})
    _legal(p, items)
    x, y = p.pos["ctl"]
    on_edge = (x == 16 or y == 16 or x + 536 == p.w - 16 or y + 152 == p.h - 16)
    assert on_edge, p.pos


def test_cluster_pack_handles_many_children_and_is_legal():
    items = [(f"r{i}", 1400, 88) for i in range(8)] + \
        [(f"f{i}", 56, 56) for i in range(8)] + \
        [(f"a{i}", 120, 48) for i in range(8)]
    nets = [(256, [f"r{i}", f"r{i + 1}"]) for i in range(7)] + \
        [(8, [f"f{i}", f"r{i}"]) for i in range(8)] + \
        [(24, [(f"r7", 100 + 170 * i, 44), f"a{i}"]) for i in range(8)]
    p = hf.cluster_pack(items, nets, 16, 16, wl_weight=0.25)
    _legal(p, items)
    assert p.method.startswith("cluster")
    # the rows stack and line up (same x), which is what the psum chains need
    xs = {p.pos[f"r{i}"][0] for i in range(8)}
    assert len(xs) == 1, xs


def test_snap_puts_every_origin_on_the_period():
    items, nets = _pes()
    for p in (hf.grid_pack(items, 16, 16, snap=(18, 32)),
              hf.slice_pack(items, 16, 16, nets, snap=(18, 32)),
              hf.compact_pack(items, {nm: (i * 200.0, (i % 2) * 100.0)
                                      for i, (nm, _w, _h) in enumerate(items)},
                              16, 16, snap=(18, 32))):
        _legal(p, items)
        for x, y in p.pos.values():
            assert x % 18 == 0 and y % 32 == 0, (p.method, x, y)


def test_compact_pack_legalizes_and_aligns_stacked_instances():
    items = [("r0", 300, 40), ("r1", 300, 40), ("f0", 40, 40), ("r2", 300, 40)]
    # annealer-like positions: r1 pushed right by a feeder beside it
    origins = {"r0": (0, 0), "f0": (0, 60), "r1": (50, 60), "r2": (0, 120)}
    p = hf.compact_pack(items, origins, 8, 8, kind={"r0": "row", "r1": "row",
                                                    "r2": "row", "f0": "feed"})
    _legal(p, items)
    assert p.pos["r0"][0] == p.pos["r1"][0] == p.pos["r2"][0]


def test_padded_dims_takes_the_cheaper_orientation():
    assert hf.padded_dims(100, 40, (300, 50)) == (300, 50)
    assert hf.padded_dims(40, 100, (300, 50)) == (50, 300)
    assert hf.padded_dims(400, 60, (300, 50)) == (400, 60)


def test_a_macro_size_is_in_microns_and_scales_with_unit_um():
    """A `macro w h` rule states microns like every PDK length; the logic
    and sram areas scale by unit_um^2 and the macro's sides must scale by
    unit_um (Codex P1 on #973: they were taken as layout units, 1000x
    short under `unit_um 1000`)."""
    pdk = hf.parse_pdk("unit_um 1000\nleaf phy_* macro w 180 h 120\n")
    ls = hf.size_leaf(pdk, "phy_0", [], faces=(0, 0))
    assert (ls.w, ls.h, ls.binding) == (180000, 120000, "macro")
    pdk1 = hf.parse_pdk("unit_um 1\nleaf phy_* macro w 180 h 120\n")
    assert hf.size_leaf(pdk1, "phy_0", [], faces=(0, 0)).w == 180


def test_facepad_is_the_light_face_floor_and_defaults_to_two_pads():
    """The PDK's `facepad` floors the LIGHT face whatever its bundles ask;
    unstated it is two pads, which is what every measured floorplan used
    (Codex P2 on #973: the value was parsed and read by nothing)."""
    assert hf.face_pair([32, 32, 0, 0], 4.0, 24)[1] == 48          # 2 x pad
    assert hf.face_pair([32, 32, 0, 0], 4.0, 24, 100)[1] == 100   # stated
    assert hf.face_pair([32, 32, 8, 8], 4.0, 24, 10)[1] == 56     # bits win
    pdk = hf.parse_pdk("pad 1\nfacepad 100\nleaf * logic gates_per_bit 1 gates_fixed 1\n")
    assert hf.size_leaf(pdk, "x", [], faces=(8, 0)).h >= 100
    assert hf.size_leaf(hf.parse_pdk("pad 1\n"), "x", [], faces=(8, 0)).h == 2
