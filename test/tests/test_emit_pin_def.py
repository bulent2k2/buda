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

"""`emit_pin_def` — the block-side handoff of the LibreLane hierarchical
flow (docs/internal/librelane_hier_flow.md §5, §8 step 3b) — and its
verifier `tools/pin_def_verify.py`.

Two synthetic designs.  A FLAT one in the shape of flow/def's (two blocks,
one 4-bit bus plus scalar nets, hand-declared layers and track patterns)
pins the pin-per-bit geometry, the symmetric-rectangle form, the escaped
spelling, the unrouted spread, the abstract fallback and the refusals.  A
HIER one — a DEF + LEF at `set_import_scale dbu`, two instances of one
macro chained by a bus, the phase-0 toy in miniature — pins UNITS from
lu_per_um, the template semantics (each instance contributes the pins it
routes, they must agree where both do), the LEF-sourced pin set and the
orientation refusal.  The verifier's three verdicts are pinned on a
"final" DEF synthesized the way OpenROAD writes one back: every origin
re-centred, so a check on origins would fail on a perfect result.
"""
import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import buda_cli
from subprocess_env import buda_env

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "tools"))
import pin_def_verify as pdv   # noqa: E402


def _session(cmds):
    s = buda_cli.BudaSession()
    s.no_viz = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in cmds:
            s.do_command(c)
    return s, buf.getvalue()


_PIN = re.compile(
    r"^\s+- (\S+) \+ NET (\S+) \+ DIRECTION (INPUT|OUTPUT|INOUT) \+ USE SIGNAL"
    r" \+ LAYER (\S+) \( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \)"
    r" \+ PLACED \( (-?\d+) (-?\d+) \) N ;$", re.M)


def _pins(text):
    """{name: (net, dir, layer, (x1, y1, x2, y2), (px, py))}, names UNESCAPED."""
    out = {}
    for m in _PIN.finditer(text):
        name = pdv.unescape(m.group(1))
        out[name] = (pdv.unescape(m.group(2)), m.group(3), m.group(4),
                     tuple(int(v) for v in m.group(5, 6, 7, 8)),
                     tuple(int(v) for v in m.group(9, 10)))
    n = int(re.search(r"^PINS (\d+) ;", text, re.M).group(1))
    assert n == len(out), "PINS count disagrees with the entries"
    return out


# ── the flat design ─────────────────────────────────────────────────────────
# Pattern: one 2-wide rail, four 1-wide signal slots at 1 spacing, one rail;
# unit pitch 14, origin 0.5 so the signal tracks sit on WHOLE units — 4, 6,
# 8, 10 (+14k) — which a DEF at the nominal-micron scale (UNITS 1) can hold.
_FLAT = [
    "def_layer 3 M3 H TOP 30",
    "def_layer 4 M4 V TOP 30",
    "def_track_pattern 3 0.5 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 GND 2 1",
    "def_track_pattern 4 0.5 VDD 2 1 _ 1 1 _ 1 1 _ 1 1 _ 1 1 GND 2 1",
    "add_block a 0 0 100 100",
    "add_block b 300 0 400 100",
    "add_bus d[4] a.o b.i",
    "add_net q[0] a.qo b.qi",        # a bracketed name: the escaping case
    "run_bundler STRICT",
    # Declared AFTER bundling: on the blocks per the netlist, on no bus —
    # the clk/rst shape of the phase-0 toy.
    "add_net clk a.c b.c",
    "add_net rst a.r b.r",
    "generate_topologies",
    "run_planner 3",
    "run_nuts",
]
_DNUTS = ["run_detailed_nuts"]


def _flat(tmp_path, tail, extra=None):
    out = tmp_path / "pins.def"
    s, log = _session(_FLAT + tail + [f"emit_pin_def {out} b {extra or ''}"])
    return s, log, out


def _wire_tracks(s, block_x, layer=3):
    """{net: (track y, layer, width)} of the bit-wires ending at x=block_x."""
    names = {w.input.original_bundle.id: list(w.input.original_bundle.get_net_names())
             for w in s.bundles}
    out = {}
    for ns in s.detailed_result.net_segments:
        if ns.layer == layer and abs(ns.span_hi - block_x) < 1e-6:
            out[names[ns.bundle_id][ns.bit_index]] = (ns.track_position, ns.layer,
                                                      ns.width)
    return out


def test_one_pin_per_bus_bit_on_the_bit_wires_track_and_layer(tmp_path):
    s, log, out = _flat(tmp_path, _DNUTS)
    pins = _pins(out.read_text())
    wires = _wire_tracks(s, 300)
    assert set(wires) == {"d_0", "d_1", "d_2", "d_3", "q[0]"}
    for net, (y, lid, _w) in wires.items():
        p = pins[net]
        assert p[2] == "M3" and p[1] == "INPUT" and p[0] == net
        # Centre ON the wire's track (block-local: b's origin is x=300),
        # `depth`/2 in from the WEST face.
        assert p[4] == (1, round(y)), (net, p)
        assert (y - 4) % 2 == pytest.approx(0), "not on a signal track"
    assert "5 from the plan" in log and "2 spread" in log


def test_the_rectangle_is_symmetric_about_the_placed_point(tmp_path):
    """OpenROAD re-centres every pin on write-back; emitting the rectangle
    already centred means the template and the result agree byte for byte
    in the absolute geometry (phase 0, §8 step 3)."""
    _s, _log, out = _flat(tmp_path, _DNUTS, "depth 4")
    for name, (_net, _d, _l, (x1, y1, x2, y2), _p) in _pins(out.read_text()).items():
        assert (x1, y1) == (-x2, -y2), name
        assert x2 > 0 and y2 > 0
    # A W-face pin runs along x: its along-extent is the depth.
    p = _pins(out.read_text())["d_0"]
    assert p[3] == (-2, -1, 2, 1) and p[4][0] == 2


def test_names_are_written_def_escaped(tmp_path):
    _s, _log, out = _flat(tmp_path, _DNUTS)
    text = out.read_text()
    assert re.search(r"^\s+- q\\\[0\\\] \+ NET q\\\[0\\\] ", text, re.M), text
    assert "- q[0] " not in text


def test_units_is_the_sessions_lu_per_um_and_the_die_is_the_block(tmp_path):
    _s, log, out = _flat(tmp_path, _DNUTS)
    text = out.read_text()
    assert "UNITS DISTANCE MICRONS 1 ;" in text          # the nominal default
    assert "DIEAREA ( 0 0 ) ( 100 100 ) ;" in text
    assert "DESIGN b ;" in text
    assert "UNITS DISTANCE MICRONS 1" in log               # and it says so


def test_unrouted_nets_are_spread_on_the_edges_tracks(tmp_path):
    """clk and rst are on block b per the netlist and on no bus: they go
    on the `unrouted` edge (default S, so a V-layer pin), evenly, on
    tracks, and the report counts them separately from the plan."""
    _s, log, out = _flat(tmp_path, _DNUTS)
    pins = _pins(out.read_text())
    for name in ("clk", "rst"):
        net, d, layer, (x1, y1, x2, y2), (px, py) = pins[name]
        assert layer == "M4" and d == "INPUT" and py == 1
        assert (px - 4) % 2 == 0, (name, px)                     # on a track
        assert (x1, y1, x2, y2) == (-1, -1, 1, 1)
    assert pins["clk"][4][0] < pins["rst"][4][0]
    assert "2 spread on edge S on M4" in log
    # The other edge, named: an E pin is an H wire on the H layer.
    out2 = tmp_path / "e.def"
    _s2, log2 = _session(_FLAT + _DNUTS + [f"emit_pin_def {out2} b unrouted E"])
    p2 = _pins(out2.read_text())
    assert p2["clk"][2] == "M3" and p2["clk"][4][0] == 99
    assert "spread on edge E on M3" in log2
    # A wrong-direction layer for the edge is refused.
    _s3, log3 = _session(_FLAT + _DNUTS + [f"emit_pin_def {tmp_path / 'x.def'} b unrouted S M3"])
    assert "Error:" in log3 and "runs horizontally" in log3


def test_the_driver_side_writes_output_pins(tmp_path):
    out = tmp_path / "a.def"
    _s, _log = _session(_FLAT + _DNUTS + [f"emit_pin_def {out} a"])
    pins = _pins(out.read_text())
    assert all(p[1] == "OUTPUT" for n, p in pins.items() if n.startswith(("d_", "q")))
    assert pins["d_0"][4][0] == 99                       # E face of a 100-wide block


def test_without_detailed_nuts_it_falls_back_to_the_bus_position_loudly(tmp_path):
    _s, log, out = _flat(tmp_path, [])
    assert "BUDA-1711: WARNING" in log
    assert "from the plan (abstract" in log
    pins = _pins(out.read_text())
    assert {n for n in pins if n.startswith("d_")} == {"d_0", "d_1", "d_2", "d_3"}
    assert len({p[4] for p in pins.values()}) == len(pins), "bits share a point"


def test_with_neither_nuts_stage_it_refuses_with_the_remedy(tmp_path):
    out = tmp_path / "none.def"
    _s, log = _session(_FLAT[:-1] + [f"emit_pin_def {out} b"])
    assert "Error:" in log and "run_detailed_nuts" in log
    assert not out.exists()


def test_an_unknown_block_and_a_bad_edge_are_refused(tmp_path):
    out = tmp_path / "x.def"
    _s, log = _session(_FLAT + _DNUTS + [f"emit_pin_def {out} nosuch"])
    assert "Error:" in log and "nosuch" in log and not out.exists()
    _s, log = _session(_FLAT + _DNUTS + [f"emit_pin_def {out} b unrouted Q"])
    assert "Error:" in log and "N/S/E/W" in log
    with pytest.raises(SystemExit):       # an unknown option is fail-fast
        _session(_FLAT + _DNUTS + [f"emit_pin_def {out} b bogus 1"])
    assert not out.exists()


def test_a_pending_dbu_scale_is_refused_with_the_remedy(tmp_path):
    out = tmp_path / "x.def"
    _s, log = _session(["open_bdb :memory:", "set_import_scale dbu"] + _FLAT
                          + _DNUTS + [f"emit_pin_def {out} b"])
    assert "Error:" in log and "set_import_scale" in log and not out.exists()


# ── the hier design: a DEF + LEF at DBU scale ──────────────────────────────

def _lef(pins_geom):
    """A sky130-shaped tech + one BLOCK macro `blk` (80 x 80 um)."""
    layers = (("met1", "HORIZONTAL", 0.34, 0.17, 0.14),
              ("met2", "VERTICAL", 0.46, 0.23, 0.14),
              ("met3", "HORIZONTAL", 0.68, 0.34, 0.30),
              ("met4", "VERTICAL", 0.92, 0.46, 0.30))
    out = ["VERSION 5.8 ;", 'BUSBITCHARS "[]" ;', 'DIVIDERCHAR "/" ;',
           "UNITS", "  DATABASE MICRONS 1000 ;", "END UNITS",
           "MANUFACTURINGGRID 0.005 ;"]
    for n, d, p, o, w in layers:
        out += [f"LAYER {n}", "  TYPE ROUTING ;", f"  DIRECTION {d} ;",
                f"  PITCH {p} ;", f"  OFFSET {o} ;", f"  WIDTH {w} ;", f"END {n}"]
    out += ["MACRO blk", "  CLASS BLOCK ;", "  ORIGIN 0 0 ;", "  SIZE 80 BY 80 ;"]
    for name, d, use, layer, rect in pins_geom:
        out += [f"  PIN {name}", f"    DIRECTION {d} ;", f"    USE {use} ;",
                "    PORT", f"      LAYER {layer} ;", f"        RECT {rect} ;",
                "    END", f"  END {name}"]
    out += ["END blk", "END LIBRARY", ""]
    return "\n".join(out)


def _macro_pins(nbits=4):
    pins = []
    for i in range(nbits):
        pins.append((f"d[{i}]", "INPUT", "SIGNAL", "met3", f"0 {10 + i} 2 {10.3 + i}"))
        pins.append((f"q[{i}]", "OUTPUT", "SIGNAL", "met3", f"78 {10 + i} 80 {10.3 + i}"))
    pins.append(("clk", "INPUT", "CLOCK", "met2", "10 0 10.14 2"))
    pins.append(("rst", "INPUT", "SIGNAL", "met2", "20 0 20.14 2"))
    pins.append(("VPWR", "INOUT", "POWER", "met4", "30 0 32 80"))
    return pins


def _def(instances, nets, units=1000):
    out = ["VERSION 5.8 ;", 'DIVIDERCHAR "/" ;', 'BUSBITCHARS "[]" ;',
           "DESIGN top ;", f"UNITS DISTANCE MICRONS {units} ;",
           "DIEAREA ( 0 0 ) ( 400000 200000 ) ;"]
    for n, off, pitch in (("met1", 170, 340), ("met2", 230, 460),
                          ("met3", 340, 680), ("met4", 460, 920)):
        out.append(f"TRACKS X {off} DO {400000 // pitch} STEP {pitch} LAYER {n} ;")
        out.append(f"TRACKS Y {off} DO {200000 // pitch} STEP {pitch} LAYER {n} ;")
    out.append(f"COMPONENTS {len(instances)} ;")
    for name, x, y, o in instances:
        out.append(f"  - {name} blk + FIXED ( {x} {y} ) {o} ;")
    out += ["END COMPONENTS", f"NETS {len(nets)} ;"]
    for name, conns in nets:
        c = " ".join(f"( {i} {p} )" for i, p in conns)
        out.append(f"  - {name} {c} + USE SIGNAL ;")
    out += ["END NETS", "END DESIGN", ""]
    return "\n".join(out)


def _bus(prefix, drv, rcv, nbits=4):
    return [(f"{prefix}\\[{i}\\]", [(drv, f"q\\[{i}\\]"), (rcv, f"d\\[{i}\\]")])
            for i in range(nbits)]


_HIER_SETUP = [
    "open_bdb :memory:",
    "set_import_scale dbu",
    "set_unit_check on",
    "def_layer 1 met1 H LOW 30",
    "def_layer 2 met2 V LOW 30",
    "def_layer 3 met3 H TOP 30",
    "def_layer 4 met4 V TOP 30",
]
_HIER_ROUTE = [
    "derive_busterms",
    "add_blocks_from_bdb 0",
    "run_hier_bundler",
    "generate_hier_topologies",
    "run_planner hier 3",
    "run_nuts",
    "run_detailed_nuts",
]


def _hier(tmp_path, instances, nets, tail, lef_pins=None):
    lef = tmp_path / "blk.lef"
    lef.write_text(_lef(lef_pins or _macro_pins()))
    d = tmp_path / "top.def"
    d.write_text(_def(instances, nets))
    cmds = _HIER_SETUP + [f"import_lef_tech {lef}", f"import_def_lef {d} {lef}"]
    return _session(cmds + _HIER_ROUTE + tail)


# Origins on the TRACK PERIOD of every pin layer (x: met2 460 / met4 920;
# y: met1 340 / met3 680), so a top-frame track is a block-frame track —
# the placement rule the writer checks.  (10000, 20000) is 340 past a met2
# period in x and 280 past a met3 period in y: the phase-0 placement.
_TWO = [("u0", 9200, 20400, "N"), ("u1", 160080, 20400, "N")]
_OFF = [("u0", 10000, 20000, "N"), ("u1", 160000, 20000, "N")]


def test_a_cell_template_merges_what_each_instance_routes(tmp_path):
    """u0 routes only its q side (the bus leaves it) and u1 only its d side
    (the bus enters it); the cell's template has BOTH, each from the
    instance that routed it, in cell-local coordinates — on the same
    track, since the bus is straight — plus clk/rst from the LEF, spread
    on the south edge on met2 tracks.  UNITS is the DEF's (dbu)."""
    out = tmp_path / "blk.def"
    s, log = _hier(tmp_path, _TWO, _bus("mid", "u0", "u1"),
                   [f"emit_pin_def {out} blk"])
    text = out.read_text()
    assert "UNITS DISTANCE MICRONS 1000 ;" in text
    assert "DIEAREA ( 0 0 ) ( 80000 80000 ) ;" in text and "DESIGN blk ;" in text
    pins = _pins(text)
    assert set(pins) == {f"d[{i}]" for i in range(4)} | {f"q[{i}]" for i in range(4)} | {"clk", "rst"}
    for i in range(4):
        d, q = pins[f"d[{i}]"], pins[f"q[{i}]"]
        assert d[2] == q[2] == "met3" and d[1] == "INPUT" and q[1] == "OUTPUT"
        assert d[4][0] == 1000 and q[4][0] == 79000          # W and E faces, depth 2 um
        assert d[4][1] == q[4][1], "the straight bus lands at one local y"
        # On a met3 track of the BLOCK's frame (340 + 680k from ITS origin):
        # the block is hardened alone, so this is the grid its router has.
        assert (d[4][1] - 340) % 680 == 0, "not on a block-frame met3 track"
        assert d[3] == (-1000, -150, 1000, 150)             # met3 min width 0.30
        assert d[4][0] % 5 == 0 and d[4][1] % 5 == 0
    for n in ("clk", "rst"):
        assert pins[n][2] == "met2" and pins[n][4][1] == 1000 and pins[n][1] == "INPUT"
        assert (pins[n][4][0] - 230) % 460 == 0, "not on a block-frame met2 track"
        assert pins[n][3] == (-70, -1000, 70, 1000)
    assert "snapped" not in log
    assert "VPWR" not in text                                 # a power pin is the PDN's
    assert "8 from the plan" in log and "2 spread on edge S on met2" in log
    assert "2 instance(s)" in log
    # A written template re-read by the verifier against itself passes.
    assert pdv.compare(text, text)[0] == []


def test_instances_that_disagree_on_a_pin_are_refused(tmp_path):
    """u1 drives a second bus to a u2 placed higher up, so u1's q lands at a
    different local y than u0's q does: one cell, two answers — refused,
    naming the instances and the pin (an `Error:` line and no file, the
    one convention every refusal of the command follows)."""
    three = _TWO + [("u2", 310040, 90440, "N")]         # on the period too
    nets = _bus("mid", "u0", "u1") + _bus("mid2", "u1", "u2")
    out = tmp_path / "blk.def"
    _s, log = _hier(tmp_path, three, nets, [f"emit_pin_def {out} blk"])
    assert "Error:" in log and "disagree on pin 'q[" in log
    assert "u0 puts it on met3" in log and "u1 on met3" in log
    assert not out.exists()


def test_an_off_period_origin_is_refused_and_snap_is_the_loud_fallback(tmp_path):
    """The phase-0 placement, (10, 20) um: 280 DBU past a met3 period in y
    and 340 past a met2 period in x, so every pin taken from the top's
    tracks misses the BLOCK's tracks (measured: d[0] 400 off, clk 120 off).
    Refused with the residues and the smallest clearing shifts; `snap`
    moves each pin to the nearest block-frame track and says by how much."""
    out = tmp_path / "blk.def"
    _s, log = _hier(tmp_path, _OFF, _bus("mid", "u0", "u1"),
                    [f"emit_pin_def {out} blk"])
    assert "Error:" in log and "not a whole number of track periods" in log
    assert ("u0: origin y on met3 is 280 past a track period of 680 — move "
            "it by -280 or +400") in log
    assert "u1: origin y on met3 is 280" in log
    assert "pass `snap`" in log and not out.exists()
    # (met2 is not a PLANNED pin layer here — clk/rst are spread on the
    # block's own tracks — so only met3 is named.)
    assert "met2" not in log.split("Error: emit_pin_def")[1].split("remedy")[0]
    _s2, log2 = _hier(tmp_path, _OFF, _bus("mid", "u0", "u1"),
                      [f"emit_pin_def {out} blk snap"])
    assert "BUDA-1713: WARNING" in log2 and "largest shift" in log2
    pins = _pins(out.read_text())
    assert len(pins) == 10
    for i in range(4):
        d, q = pins[f"d[{i}]"], pins[f"q[{i}]"]
        assert (d[4][1] - 340) % 680 == 0 and d[4][1] == q[4][1]
    for n in ("clk", "rst"):
        assert (pins[n][4][0] - 230) % 460 == 0
    m = re.search(r"largest shift (\d+)", log2)
    assert m and 0 < int(m.group(1)) <= 340                   # within half a met3 period
    assert "8 snapped" in log2


def test_a_non_n_instance_is_refused_loud(tmp_path):
    out = tmp_path / "blk.def"
    inst = [("u0", 10000, 20000, "N"), ("u1", 160000, 20000, "FN")]
    _s, log = _hier(tmp_path, inst, _bus("mid", "u0", "u1"),
                    [f"emit_pin_def {out} blk"])
    # (The importer stores DEF's FN under the BDB's own token, so the
    # message names the instance and the rule, not the DEF spelling.)
    assert "Error:" in log and "u1 (" in log and "orientation N" in log
    assert not out.exists()


def test_the_lef_option_names_the_pin_set_and_an_instance_is_a_block(tmp_path):
    """`lef <file>` supplies the cell's ports explicitly; naming an INSTANCE
    emits that block alone — what IT routes from the plan (u0: its q side),
    the rest of its cell's ports spread."""
    out = tmp_path / "u0.def"
    s, log = _hier(tmp_path, _TWO, _bus("mid", "u0", "u1"),
                   [f"emit_pin_def {out} u0 lef {tmp_path / 'blk.lef'}"])
    pins = _pins(out.read_text())
    assert "DESIGN u0 ;" in out.read_text()
    assert {n for n, p in pins.items() if p[2] == "met3" and p[4][0] == 79000} == {
        f"q[{i}]" for i in range(4)}
    assert {n for n, p in pins.items() if p[2] == "met2"} == {
        f"d[{i}]" for i in range(4)} | {"clk", "rst"}
    assert "4 from the plan" in log and "6 spread on edge S on met2" in log


# ── the verifier ────────────────────────────────────────────────────────────

def _recentred(template_text):
    """What OpenROAD writes back: the same rectangles with origins moved to
    their centres — here a shift by (+7, -3) of every origin, offsets
    compensated, which a PLACED-point check reports as every pin moved."""
    def fix(m):
        x1, y1, x2, y2, px, py = (int(v) for v in m.group(2, 3, 4, 5, 6, 7))
        return (f"{m.group(1)}( {x1 - 7} {y1 + 3} ) ( {x2 - 7} {y2 + 3} ) "
                f"+ PLACED ( {px + 7} {py - 3} ) N ;")
    return re.sub(r"(\+ LAYER \S+ )\( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \)"
                  r" \+ PLACED \( (-?\d+) (-?\d+) \) N ;", fix, template_text)


def test_the_verifier_compares_absolute_rectangles_not_origins(tmp_path):
    _s, _log, out = _flat(tmp_path, _DNUTS)
    t = out.read_text()
    f = _recentred(t)
    assert f != t
    assert pdv.compare(t, f) == ([], [], 7, 7)
    (tmp_path / "final.def").write_text(f)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = pdv.main(["pin_def_verify.py", str(out), str(tmp_path / "final.def")])
    assert rc == 0 and buf.getvalue().startswith("PASS: 7 of 7")


def test_the_verifier_names_each_moved_or_missing_pin(tmp_path):
    _s, _log, out = _flat(tmp_path, _DNUTS)
    t = out.read_text()
    moved = re.sub(r"(- d_1 .*PLACED \( )(\d+) (\d+)", lambda m: f"{m.group(1)}{m.group(2)} {int(m.group(3)) + 2}", t)
    gone = "\n".join(ln for ln in moved.splitlines() if "- clk " not in ln)
    gone = gone.replace("PINS 7 ;", "PINS 6 ;")
    mism, _notes, n_ok, n_t = pdv.compare(t, gone)
    assert n_t == 7 and n_ok == 5
    assert any(m.startswith("d_1: template rect") for m in mism), mism
    assert any(m == "clk: absent from the final DEF" for m in mism), mism
    (tmp_path / "final.def").write_text(gone)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = pdv.main(["pin_def_verify.py", str(out), str(tmp_path / "final.def")])
    assert rc == 1 and "FAIL: 2 mismatch(es)" in buf.getvalue()
    assert "MISMATCH: d_1" in buf.getvalue()


def test_the_verifier_refuses_a_final_def_with_no_pins(tmp_path):
    _s, _log, out = _flat(tmp_path, _DNUTS)
    (tmp_path / "nopins.def").write_text("VERSION 5.8 ;\nDESIGN b ;\nEND DESIGN\n")
    (tmp_path / "empty.def").write_text("VERSION 5.8 ;\nDESIGN b ;\nPINS 0 ;\nEND PINS\nEND DESIGN\n")
    for f in ("nopins.def", "empty.def"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pdv.main(["pin_def_verify.py", str(out), str(tmp_path / f)])
        assert rc == 2 and buf.getvalue().startswith("REFUSED:"), f


def test_the_verifier_reads_the_measured_openroad_shape():
    """The exact pair of §8 step 3: the template's origin-at-the-edge pin
    and OpenROAD's re-centred write-back are ONE rectangle."""
    t = ("UNITS DISTANCE MICRONS 1000 ;\nPINS 1 ;\n  - d\\[0\\] + NET d\\[0\\] + DIRECTION INPUT + USE SIGNAL"
         " + LAYER met3 ( 0 -150 ) ( 2000 150 ) + PLACED ( 0 8500 ) N ;\nEND PINS\n")
    f = ("UNITS DISTANCE MICRONS 1000 ;\nPINS 1 ;\n  - d\\[0\\] + NET d\\[0\\] + DIRECTION INPUT + USE SIGNAL"
         " + LAYER met3 ( -1000 -150 ) ( 1000 150 ) + PLACED ( 1000 8500 ) N ;\nEND PINS\n")
    assert pdv.compare(t, f) == ([], [], 1, 1)
    assert pdv.read_pins(t, "t") == {"d[0]": {("met3", 0, 8350, 2000, 8650)}}
    # Different units are not comparable, and say so before any pin.
    mism, _n, _ok, _t = pdv.compare(t, f.replace("MICRONS 1000", "MICRONS 2000"))
    assert mism and mism[0].startswith("UNITS differ")


# ── the vehicle: flow/librelane/phase0/two_reg32/pins.buda + prep_pins.py ──
# The tree has no EDA tools, so the phase-0 runs are SYNTHESIZED here in the
# shape LibreLane writes them (a final DEF with std cells, wiring, PINS and
# a PDN; a macro LEF with a USE CLOCK port; a resolved.json naming a tech
# LEF), and the vehicle is run on them verbatim: what its comments call a
# pass is what this asserts.

_P0 = _ROOT / "flow" / "librelane" / "phase0"


def _synth_runs(root, nbits=32):
    tech = root / "pdk" / "sky130_fd_sc_hd__nom.tlef"
    tech.parent.mkdir(parents=True)
    tech.write_text(_lef([]).split("MACRO blk")[0].replace(
        "LAYER met1", "LAYER li1\n  TYPE ROUTING ;\n  DIRECTION VERTICAL ;\n"
        "  PITCH 0.46 ;\n  OFFSET 0.23 ;\n  WIDTH 0.17 ;\nEND li1\nLAYER met1")
        + "LAYER met5\n  TYPE ROUTING ;\n  DIRECTION HORIZONTAL ;\n  PITCH 3.4 ;\n"
          "  OFFSET 1.7 ;\n  WIDTH 1.6 ;\nEND met5\nEND LIBRARY\n")
    pins = _macro_pins(nbits) + [("VGND", "INOUT", "GROUND", "met4", "40 0 42 80")]
    lef = root / "phase0" / "reg32" / "runs" / "phase0" / "final" / "lef" / "reg32.lef"
    lef.parent.mkdir(parents=True)
    lef.write_text(_lef(pins).replace("MACRO blk", "MACRO reg32").replace("END blk", "END reg32"))
    d = ["VERSION 5.8 ;", 'DIVIDERCHAR "/" ;', 'BUSBITCHARS "[]" ;', "DESIGN two_reg32 ;",
         "UNITS DISTANCE MICRONS 1000 ;", "DIEAREA ( 0 0 ) ( 250000 120000 ) ;"]
    for n, o, p in (("li1", 230, 460), ("met1", 170, 340), ("met2", 230, 460),
                    ("met3", 340, 680), ("met4", 460, 920), ("met5", 1700, 3400)):
        d += [f"TRACKS X {o} DO {250000 // p} STEP {p} LAYER {n} ;",
              f"TRACKS Y {o} DO {120000 // p} STEP {p} LAYER {n} ;"]
    d += ["GCELLGRID X 0 DO 37 STEP 6900 ;", "GCELLGRID Y 0 DO 18 STEP 6900 ;",
          "VIAS 1 ;", "  - via2_3 + VIARULE M2M3 ;", "END VIAS", "COMPONENTS 12 ;",
          "  - u0 reg32 + SOURCE DIST + FIXED ( 10000 20000 ) N ;",
          "  - u1 reg32 + SOURCE DIST + FIXED ( 160000 20000 ) N ;"]
    d += [f"  - TAP_{k} sky130_fd_sc_hd__tapvpwrvgnd_1 + FIXED ( {100000 + k * 460} 5000 ) N ;"
          for k in range(10)]
    d += ["END COMPONENTS", f"PINS {2 * nbits + 2} ;"]
    for i in range(nbits):
        d.append(f"  - d\\[{i}\\] + NET d\\[{i}\\] + DIRECTION INPUT + USE SIGNAL"
                 f" + LAYER met3 ( -1000 -150 ) ( 1000 150 ) + PLACED ( 1000 {30000 + i * 680} ) N ;")
        d.append(f"  - q\\[{i}\\] + NET q\\[{i}\\] + DIRECTION OUTPUT + USE SIGNAL"
                 f" + LAYER met3 ( -1000 -150 ) ( 1000 150 ) + PLACED ( 249000 {30000 + i * 680} ) N ;")
    d += ["  - clk + NET clk + DIRECTION INPUT + USE SIGNAL + LAYER met2 ( -70 -1000 ) ( 70 1000 ) + PLACED ( 50230 1000 ) N ;",
          "  - rst + NET rst + DIRECTION INPUT + USE SIGNAL + LAYER met2 ( -70 -1000 ) ( 70 1000 ) + PLACED ( 60230 1000 ) N ;",
          "END PINS", "SPECIALNETS 1 ;",
          "  - VPWR ( * VPWR ) + USE POWER + ROUTED met4 2000 + SHAPE STRIPE ( 5000 0 ) ( * 120000 ) ;",
          "END SPECIALNETS", f"NETS {3 * nbits + 2} ;"]
    for i in range(nbits):
        d.append(f"  - mid\\[{i}\\] ( u0 q\\[{i}\\] ) ( u1 d\\[{i}\\] ) + ROUTED met3 ( 90000 {58820 + i * 680} ) ( 160000 * )\n"
                 f"      NEW met2 ( 90000 {58820 + i * 680} ) ( * 60000 ) via2_3 + USE SIGNAL ;")
        d.append(f"  - d\\[{i}\\] ( PIN d\\[{i}\\] ) ( u0 d\\[{i}\\] ) + USE SIGNAL ;")
        d.append(f"  - q\\[{i}\\] ( PIN q\\[{i}\\] ) ( u1 q\\[{i}\\] ) + USE SIGNAL ;")
    d += ["  - clk ( PIN clk ) ( u0 clk ) ( u1 clk ) ( TAP_0 A ) + USE SIGNAL ;",
          "  - rst ( PIN rst ) ( u0 rst ) ( u1 rst ) + USE SIGNAL ;",
          "END NETS", "END DESIGN", ""]
    top = root / "phase0" / "two_reg32"
    (top / "runs" / "phase0" / "final" / "def").mkdir(parents=True)
    (top / "runs" / "phase0" / "final" / "def" / "two_reg32.def").write_text("\n".join(d))
    (top / "runs" / "phase0" / "resolved.json").write_text(json.dumps({
        "DEFAULT_CORNER": "nom_tt_025C_1v80", "PDK_ROOT": "/elsewhere/.ciel",
        "TECH_LEFS": {"nom_*": "/elsewhere/.ciel/sky130_fd_sc_hd__nom.tlef",
                      "min_*": "/x", "max_*": "/y"}, "DESIGN_NAME": "two_reg32"}))
    for f in ("prep_pins.py", "pins.buda"):
        shutil.copy(_P0 / "two_reg32" / f, top / f)
    return top


def test_the_vehicle_runs_on_the_shape_the_phase0_runs_have(tmp_path):
    top = _synth_runs(tmp_path)
    prep = [sys.executable, str(top / "prep_pins.py")]
    # Without the PDK the tech LEF path from another machine is a loud stop.
    r = subprocess.run(prep, capture_output=True, text=True)
    assert r.returncode == 1 and "prep_pins: FAIL" in r.stderr and "--pdk-root" in r.stderr
    r = subprocess.run(prep + ["--pdk-root", str(tmp_path / "pdk")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.rstrip().endswith("prep_pins: ok")
    assert "2 instance(s) of reg32, 32 mid* net(s), 12 TRACKS" in r.stdout
    assert "GCELLGRID kept" in r.stdout and "copied verbatim" in r.stdout
    fp = (top / "two_reg32_fp.def").read_text()
    assert "TAP_" not in fp and "ROUTED" not in fp and "SPECIALNETS" not in fp
    assert "PINS" not in fp and "SOURCE" not in fp and fp.count("- mid") == 32
    assert fp.count("GCELLGRID") == 2                    # for buda_route.buda too
    assert "( u0 q\\[0\\] ) ( u1 d\\[0\\] ) + USE SIGNAL ;" in fp
    assert "USE CLOCK" in (top / "reg32_macro.lef").read_text()   # verbatim
    # The flow itself, verbatim, on those inputs: the pass its comments name.
    r = subprocess.run([sys.executable, str(_ROOT / "src" / "buda_cli.py"),
                        "--no-viz", str(top / "pins.buda")],
                       capture_output=True, text=True,
                       env=buda_env(_ROOT, "build", "src"))
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    log = (top / "log" / "pins_flow.log").read_text()
    assert ("[PinDEF] " in log and "66 pin(s) for reg32 — 64 from the plan "
            "(detailed; E 32, W 32), 2 spread on edge S on met2" in log), log[-3000:]
    assert log.count("Success: no violations found.") == 2
    assert "0 error line(s)" in r.stdout
    assert "snapped" not in log, "the vehicle's placement is on the period"
    pins = _pins((top / "reg32_pins.def").read_text())
    assert len(pins) == 66
    for i in range(32):
        assert pins[f"d[{i}]"][4][1] == pins[f"q[{i}]"][4][1]     # one y per bit
        assert pins[f"d[{i}]"][2] == pins[f"q[{i}]"][2] == "met3"
        assert (pins[f"d[{i}]"][4][1] - 340) % 680 == 0      # the BLOCK's met3 track
    assert pins["clk"][2] == pins["rst"][2] == "met2"
    for n in ("clk", "rst"):
        assert (pins[n][4][0] - 230) % 460 == 0              # the BLOCK's met2 track
    # The flow's own placement fix: the DEF's (10, 20) um origins are off
    # the pin layers' track periods, so the vehicle moves both macros onto
    # them before routing (without it the writer refuses, tested above).
    vehicle = (_P0 / "two_reg32" / "pins.buda").read_text()
    assert "move_comp u0 9200 20400" in vehicle
    assert "move_comp u1 160080 20400" in vehicle
    # The template checked against itself as OpenROAD would write it back.
    t = (top / "reg32_pins.def").read_text()
    assert pdv.compare(t, _recentred(t)) == ([], [], 66, 66)


def test_the_hardening_config_differs_from_the_hand_one_only_by_the_template():
    a = json.loads((_P0 / "reg32" / "config_pins.json").read_text())
    b = json.loads((_P0 / "reg32" / "config_buda_pins.json").read_text())
    assert set(a) == set(b)
    assert {k for k in a if a[k] != b[k]} == {"FP_DEF_TEMPLATE"}
    assert b["FP_DEF_TEMPLATE"] == "dir::../two_reg32/reg32_pins.def"
