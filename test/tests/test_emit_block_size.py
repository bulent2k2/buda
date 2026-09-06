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

"""`emit_block_size` — the two demands on a hierarchical block's die.

The measurement this exists to move: at N = 4 arm H's die is 8.66x arm F's
(§8 step 7d), because the emitter pads each cell to its bus faces while the
flat arm derives its die from cell AREA.  So the rule takes the max of the
two demands and says WHICH BINDS, which is the number a sizing decision
needs.
"""
import json

import pytest

from test_emit_pin_def import _bus, _hier, _session, _TWO

# Block `b` takes a 4-bit bus on its WEST face and a 6-bit one on its NORTH,
# so BOTH axes carry a face demand -- a block whose bus lands on one pair of
# faces only has no face demand on the other axis, which the writer refuses
# to guess (`test_an_axis_with_no_demand_is_refused`).
_SETUP = [
    "def_layer 3 M3 H TOP 30",
    "def_layer 4 M4 V TOP 30",
    "def_track_pattern 3 0.5 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 GND 2 1",
    "def_track_pattern 4 0.5 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 GND 2 1",
    "add_block a 0 0 100 100",
    "add_block b 300 0 400 100",
    "add_block c 300 300 400 400",
    "add_bus d[4] a.o b.i",
    "add_bus v[6] c.o b.vi",
    "run_bundler STRICT",
    "generate_topologies",
    "run_planner 3",
    "run_nuts",
    "run_detailed_nuts",
]


def _size(tmp_path, tail_target="b", extra="", flat=True, setup=None):
    out = tmp_path / "size.json"
    cmd = f"emit_block_size {out} {tail_target} {extra}".strip()
    s, log = (_session((setup or _SETUP) + [cmd]) if flat else
              _hier(tmp_path, _TWO, _bus("mid", "u0", "u1"), [cmd]))
    return (json.loads(out.read_text()) if out.exists() else None), log


def test_the_face_demand_is_the_bus_width_the_plan_lands(tmp_path):
    """Every bit that reaches a face needs its own signal track there, so
    the face must span `eff_bus_width(bits)` on the layer they arrive on —
    BUDA's own width model, the one the planner charges with."""
    j, log = _size(tmp_path)
    assert j is not None, log
    assert j["FP_SIZING"] == "absolute" and j["DIE_AREA"][:2] == [0, 0]
    d = j["derivation"]
    faces = d["face"]
    assert faces, d
    bits = sum(f["bits"] for f in faces.values())
    assert bits >= 4, faces                      # the 4-bit bus at least
    # A W/E face constrains the HEIGHT, an N/S face the WIDTH.
    for name, f in faces.items():
        axis = "h" if name in ("W", "E") else "w"
        assert d["face_needs"][axis] >= f["needs"] - 1e-6, (name, f)
    # With no area given the size IS the face demand, and it says so.
    w, h = j["DIE_AREA"][2], j["DIE_AREA"][3]
    assert (w, h) == (d["face_needs"]["w"], d["face_needs"]["h"]) or \
        max(d["face_needs"].values()) > 0
    assert d["binds"] == {"w": "face", "h": "face"}
    assert "FACE ONLY, so this is a floor" in log and "no `area`" in log


def test_area_binds_when_the_logic_is_bigger_than_the_faces(tmp_path):
    """The demand the flat arm has and `tpu_lib.tcl` never applies."""
    j, log = _size(tmp_path, extra="area 40000 util 50")
    d = j["derivation"]
    assert d["binds"] == {"w": "area", "h": "area"}, d
    a = d["area"]
    assert a["instance_area"] == 40000 and a["utilization_pct"] == 50
    # core = area/util; the die is at least that.
    # (the JSON rounds to 3 decimals, so compare with a relative tolerance)
    assert j["DIE_AREA"][2] * j["DIE_AREA"][3] >= (40000 / 0.5) * (1 - 1e-6)
    assert "w binds on area, h binds on area" in log
    # A tiny area leaves the faces binding again — the max, per axis.
    j2, _ = _size(tmp_path, extra="area 1 util 50")
    assert j2["derivation"]["binds"] == {"w": "face", "h": "face"}
    assert j2["DIE_AREA"] == j["DIE_AREA"] or j2["DIE_AREA"][2] <= j["DIE_AREA"][2]


def test_utilization_and_aspect_shape_the_area_demand(tmp_path):
    j50, _ = _size(tmp_path, extra="area 40000 util 50")
    j25, _ = _size(tmp_path, extra="area 40000 util 25")
    a50, a25 = (j["DIE_AREA"][2] * j["DIE_AREA"][3] for j in (j50, j25))
    assert a25 > a50 * 1.9, (a50, a25)            # half the utilization, twice the die
    j_sq, _ = _size(tmp_path, extra="area 40000 util 50 aspect 1")
    w, h = j_sq["DIE_AREA"][2], j_sq["DIE_AREA"][3]
    assert abs(w - h) < 1e-3, (w, h)
    j_wide, _ = _size(tmp_path, extra="area 40000 util 50 aspect 4")
    w2, h2 = j_wide["DIE_AREA"][2], j_wide["DIE_AREA"][3]
    assert abs(w2 / h2 - 4) < 1e-4, (w2, h2)
    assert abs(w2 * h2 - w * h) < 1.0             # same area, different shape


def test_the_margin_adds_to_each_face(tmp_path):
    j0, _ = _size(tmp_path)
    j5, _ = _size(tmp_path, extra="margin 5")
    for axis in ("w", "h"):
        assert abs(j5["derivation"]["face_needs"][axis]
                   - j0["derivation"]["face_needs"][axis] - 10.0) < 1e-6


def test_metrics_json_supplies_the_area(tmp_path):
    m = tmp_path / "metrics.json"
    m.write_text(json.dumps({"design__instance__area": 40000.0,
                             "design__die__area": 999.0}))
    j, _ = _size(tmp_path, extra=f"metrics {m} util 50")
    assert j["derivation"]["area"]["instance_area"] == 40000.0
    assert j["derivation"]["binds"] == {"w": "area", "h": "area"}
    # A metrics file that is not one, and the both-given conflict.
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"nothing": 1}))
    _j, log = _size(tmp_path, extra=f"metrics {bad}")
    assert "has no 'design__instance__area'" in log
    _j, log = _size(tmp_path, extra=f"metrics {m} area 5")
    assert "both `area`" in log and "they differ" in log


def test_the_report_compares_against_the_designs_current_size(tmp_path):
    """The 8.66x reading, made per block: how much bigger the design's own
    block is than the rule says it needs to be."""
    j, log = _size(tmp_path)
    cur = j["derivation"]["current"]
    assert cur["w"] == 100.0 and cur["h"] == 100.0          # the fixture's block b
    assert cur["area_ratio"] > 1.0
    assert f"{cur['area_ratio']:.2f}x this area" in log


def test_a_cell_takes_the_worst_face_over_its_instances(tmp_path):
    """One size per CELL: each instance routes what it routes (the bus
    leaves u0 and enters u1), so a face's demand is the largest any
    instance puts on it, and the size holds for every occurrence."""
    j, log = _size(tmp_path, tail_target="blk", extra="area 4000", flat=False)
    assert j is not None, log
    d = j["derivation"]
    assert d["instances"] == ["u0", "u1"] and set(d["face"]) == {"E", "W"}
    assert d["face"]["W"]["bits"] == 4 and d["face"]["E"]["bits"] == 4
    # Only E/W land, so the HEIGHT has a face demand and the WIDTH does not:
    # the width is the area's alone.
    assert d["face_needs"]["h"] > 0 and d["face_needs"]["w"] == 0
    assert d["binds"]["w"] == "area"
    j1, _ = _size(tmp_path, tail_target="blk", extra="inst u0 area 4000", flat=False)
    assert j1["derivation"]["instances"] == ["u0"]
    assert set(j1["derivation"]["face"]) == {"E"}            # u0 only routes its q side
    _j, log = _size(tmp_path, tail_target="blk", extra="inst nope", flat=False)
    assert "'nope' is not a placed instance" in log


def test_the_refusals(tmp_path):
    _j, log = _size(tmp_path, tail_target="nope")
    assert "no cell or block named 'nope'" in log
    for bad, msg in (("util 0", "percentage in (0, 100]"),
                     ("util 101", "percentage in (0, 100]"),
                     ("aspect 0", "aspect must be positive"),
                     ("margin -1", "must not be negative"),
                     ("area abc", "must be a number")):
        _j, log = _size(tmp_path, extra=bad)
        assert msg in log, (bad, log)
    with pytest.raises(SystemExit):
        _size(tmp_path, extra="bogus 1")
    # Before any routing there are no landings to read.
    out = tmp_path / "s.json"
    _s, log = _session([c for c in _SETUP if not c.startswith(("run_nuts", "run_detailed"))]
                       + [f"emit_block_size {out} b"])
    assert "no routed plan to read faces from" in log and not out.exists()


def test_an_axis_with_no_demand_is_refused_rather_than_guessed(tmp_path):
    """Block `a` sends its bus east and takes nothing on its north or south,
    so nothing constrains its WIDTH.  With no `area` the writer has no
    second demand to fall back on and says so, rather than inventing one."""
    _j, log = _size(tmp_path, tail_target="a")
    assert "has no demand on one axis" in log and "give `area`" in log
    j, _log = _size(tmp_path, tail_target="a", extra="area 4000")
    assert j["derivation"]["binds"]["w"] == "area"
    assert j["DIE_AREA"][2] > 0 and j["DIE_AREA"][3] > 0
