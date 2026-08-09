#!/usr/bin/env python3
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

"""Generate the checked-in LEF / DEF / Verilog trio for flow/def/chip.buda.

The three files are COMMITTED — the flow runs without ever invoking this
script.  This exists so they can be regenerated deterministically when the
design changes, the same arrangement `flow/hbundles/gen_10.py` and
`test/tests/data/build_fixtures.py` already use: a hand-edited 36-net DEF
drifts out of agreement with its Verilog the first time someone touches it,
and a DEF whose instance names no longer match the netlist fails in the most
confusing way available (silently dropped connections).

    python3 flow/def/gen_chip.py            # rewrite chip.{lef,def,v}

**Why there is a Verilog file at all.**  A DEF is FLAT: `COMPONENTS` is a
list of leaf instances with no parent, which is why `import_def_lef` writes
every row at `depth=0`.  The hierarchy lives in the instance NAMES
(`u_q0/bl/lo`) and is recovered by elaborating the netlist, which is what
`import_verilog` does — the "DEF + Verilog merge" the BDB reference
describes.  So "a hierarchical DEF" is really a DEF plus the netlist that
gives its names meaning, and both are generated here from ONE description so
they cannot disagree.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# ── the design, described once ─────────────────────────────────────────────
#
#   chip                                    depth -   top module
#     u_q0, u_q1        : quad              depth 0
#       bl, br          : blk               depth 1
#         lo, hi        : LEAF              depth 2   (the only DEF components)
#
# Buses, one per level, 4 bits each:
#
#   D2  lo.Z -> hi.A        inside every blk   (4 instances: a cell-local
#                                               template solved once)
#   D1  bl/hi.Z -> br/lo.A  inside every quad  (2 instances)
#   D0  u_q0/br/hi.Z -> u_q1/bl/lo.A           across the top level
#
#   plus die PORTS: din* -> u_q0/bl/lo.A, u_q1/br/hi.Z -> dout*
#
# Every leaf therefore drives exactly one 4-bit bus and receives one, which
# is what makes each level's bundle a clean 4-net group.

NBITS = 4
DBU = 1000                      # DEF database units per micron

LEAF_W, LEAF_H = 40, 24         # µm
BLK_DX = 50                     # lo -> hi offset inside a blk
QUAD_DY = 45                    # bl -> br offset inside a quad
Q0, Q1 = (10, 10), (160, 10)    # quad origins on the die
DIE_W, DIE_H = 260, 90          # µm

# Routing stack: (name, direction, pitch µm, width µm).  Pitches are real
# ones; the flow imports at DBU scale so a layout unit is 1 DBU and the
# `set_unit_check` absolute test sees 0.2 µm rather than a bare ratio.
LAYERS = [
    ("M1", "HORIZONTAL", 0.19, 0.07),
    ("M2", "VERTICAL",   0.20, 0.07),
    ("M3", "HORIZONTAL", 0.20, 0.07),
    ("M4", "VERTICAL",   0.40, 0.14),
    ("M5", "HORIZONTAL", 0.40, 0.14),
    ("M6", "VERTICAL",   0.80, 0.30),
]


def leaf_origins():
    """Absolute (µm) placement of every leaf, keyed by hierarchical path."""
    out = {}
    for qn, (qx, qy) in (("u_q0", Q0), ("u_q1", Q1)):
        for bn, by in (("bl", 0), ("br", QUAD_DY)):
            for ln, lx in (("lo", 0), ("hi", BLK_DX)):
                out[f"{qn}/{bn}/{ln}"] = (qx + lx, qy + by)
    return out


# ── LEF ────────────────────────────────────────────────────────────────────

def write_lef(path):
    L = ["VERSION 5.8 ;",
         "BUSBITCHARS \"[]\" ;",
         "DIVIDERCHAR \"/\" ;",
         "UNITS",
         f"  DATABASE MICRONS {DBU} ;",
         "END UNITS",
         "MANUFACTURINGGRID 0.005 ;",
         ""]
    for name, direction, pitch, width in LAYERS:
        L += [f"LAYER {name}",
              "  TYPE ROUTING ;",
              f"  DIRECTION {direction} ;",
              f"  PITCH {pitch} ;",
              f"  WIDTH {width} ;",
              f"  SPACING {round(pitch - width, 3)} ;",
              f"END {name}",
              ""]
    # One cut layer between each metal pair, so the file reads like a real
    # technology.  BUDA models no vias yet (deferred with the router path),
    # so these land in the unmodelled census rather than being invented into
    # something they are not.
    for i in range(1, len(LAYERS)):
        L += [f"LAYER VIA{i}",
              "  TYPE CUT ;",
              f"END VIA{i}",
              ""]

    # The one leaf macro.  A* inputs on the west edge, Z* outputs on the
    # east, spread over the cell height so a 4-bit bus lands on 4 distinct
    # rows rather than a single point.
    L += ["MACRO LEAF",
          "  CLASS CORE ;",
          "  ORIGIN 0 0 ;",
          f"  SIZE {LEAF_W} BY {LEAF_H} ;",
          "  SYMMETRY X Y ;"]
    for i in range(NBITS):
        y = 4 + i * 5
        L += [f"  PIN A{i}",
              "    DIRECTION INPUT ;",
              "    USE SIGNAL ;",
              "    PORT",
              "      LAYER M1 ;",
              f"      RECT 0.5 {y - 0.5} 1.5 {y + 0.5} ;",
              "    END",
              f"  END A{i}"]
    for i in range(NBITS):
        y = 4 + i * 5
        L += [f"  PIN Z{i}",
              "    DIRECTION OUTPUT ;",
              "    USE SIGNAL ;",
              "    PORT",
              "      LAYER M1 ;",
              f"      RECT {LEAF_W - 1.5} {y - 0.5} {LEAF_W - 0.5} {y + 0.5} ;",
              "    END",
              f"  END Z{i}"]
    # Power pins: USE POWER/GROUND, so the reader must NOT treat them as
    # signal terminals.
    for nm, use in (("VDD", "POWER"), ("VSS", "GROUND")):
        L += [f"  PIN {nm}",
              "    DIRECTION INOUT ;",
              f"    USE {use} ;",
              "    PORT",
              "      LAYER M1 ;",
              f"      RECT 0 {0 if use == 'GROUND' else LEAF_H - 1} "
              f"{LEAF_W} {1 if use == 'GROUND' else LEAF_H} ;",
              "    END",
              f"  END {nm}"]
    # An obstruction over the middle of the cell: a real macro blocks metal,
    # and this is what makes the OBS -> keepout path (Phase 3c) do something
    # on this vehicle.
    L += ["  OBS",
          "    LAYER M2 ;",
          f"    RECT 4 4 {LEAF_W - 4} {LEAF_H - 4} ;",
          "  END",
          "END LEAF",
          "",
          "END LIBRARY",
          ""]
    with open(path, "w") as f:
        f.write("\n".join(L))


# ── Verilog ────────────────────────────────────────────────────────────────

def _ports(prefix, n=NBITS):
    return ", ".join(f"{prefix}{i}" for i in range(n))


def write_verilog(path):
    V = [
        "// Generated by flow/def/gen_chip.py -- do not edit.",
        "//",
        "// Scalar nets, not vectors, on purpose: BUDA's Verilog reader",
        "// resolves a bit-select to its BASE name, so `.A0(w[0])` and",
        "// `.A1(w[1])` would collapse a 4-bit bus into ONE net and the",
        "// bundler would see a 1-bit bus.  Four wires say what is meant.",
        "//",
        "// LEAF is declared with an EMPTY body.  That is how a netlist",
        "// states a hard macro\'s port directions while leaving its geometry",
        "// to the LEF, and it is also what the reader needs: an instance of",
        "// an *undefined* module is skipped as a standard cell (the",
        "// heuristic that keeps a million gates out of the hierarchy), so a",
        "// blackbox macro would never reach the component table at all.",
        "",
        f"module LEAF ({_ports('A')}, {_ports('Z')});",
        f"  input  {_ports('A')};",
        f"  output {_ports('Z')};",
        "endmodule",
        "",
    ]
    # blk: lo takes the block's inputs and drives the D2 bus; hi takes the
    # D2 bus and drives the block's outputs.
    V += [f"module blk ({_ports('I')}, {_ports('O')});",
          f"  input  {_ports('I')};",
          f"  output {_ports('O')};",
          f"  wire   {_ports('w')};",
          "  LEAF lo (" + ", ".join(
              [f".A{i}(I{i})" for i in range(NBITS)] +
              [f".Z{i}(w{i})" for i in range(NBITS)]) + ");",
          "  LEAF hi (" + ", ".join(
              [f".A{i}(w{i})" for i in range(NBITS)] +
              [f".Z{i}(O{i})" for i in range(NBITS)]) + ");",
          "endmodule",
          ""]
    # quad: bl drives the D1 bus, br consumes it.
    V += [f"module quad ({_ports('I')}, {_ports('O')});",
          f"  input  {_ports('I')};",
          f"  output {_ports('O')};",
          f"  wire   {_ports('m')};",
          "  blk bl (" + ", ".join(
              [f".I{i}(I{i})" for i in range(NBITS)] +
              [f".O{i}(m{i})" for i in range(NBITS)]) + ");",
          "  blk br (" + ", ".join(
              [f".I{i}(m{i})" for i in range(NBITS)] +
              [f".O{i}(O{i})" for i in range(NBITS)]) + ");",
          "endmodule",
          ""]
    # chip: u_q0 drives the D0 bus, u_q1 consumes it; die ports at each end.
    V += [f"module chip ({_ports('din')}, {_ports('dout')});",
          f"  input  {_ports('din')};",
          f"  output {_ports('dout')};",
          f"  wire   {_ports('c')};",
          "  quad u_q0 (" + ", ".join(
              [f".I{i}(din{i})" for i in range(NBITS)] +
              [f".O{i}(c{i})" for i in range(NBITS)]) + ");",
          "  quad u_q1 (" + ", ".join(
              [f".I{i}(c{i})" for i in range(NBITS)] +
              [f".O{i}(dout{i})" for i in range(NBITS)]) + ");",
          "endmodule",
          ""]
    with open(path, "w") as f:
        f.write("\n".join(V))


# ── DEF ────────────────────────────────────────────────────────────────────

def _nets():
    """Every net, as (name, [(inst, pin), ...]) — the SAME names Verilog
    elaboration produces, which is the whole reason both files come from
    one description."""
    out = []
    for i in range(NBITS):
        # die input port -> u_q0/bl/lo
        out.append((f"din{i}", [("PIN", f"din{i}"),
                                ("u_q0/bl/lo", f"A{i}")]))
        # u_q1/br/hi -> die output port
        out.append((f"dout{i}", [("u_q1/br/hi", f"Z{i}"),
                                 ("PIN", f"dout{i}")]))
        # D0: chip-level, u_q0/br/hi -> u_q1/bl/lo
        out.append((f"c{i}", [("u_q0/br/hi", f"Z{i}"),
                              ("u_q1/bl/lo", f"A{i}")]))
    for q in ("u_q0", "u_q1"):
        for i in range(NBITS):
            # D1: quad-level, bl/hi -> br/lo
            out.append((f"{q}/m{i}", [(f"{q}/bl/hi", f"Z{i}"),
                                      (f"{q}/br/lo", f"A{i}")]))
        for b in ("bl", "br"):
            for i in range(NBITS):
                # D2: blk-level, lo -> hi
                out.append((f"{q}/{b}/w{i}", [(f"{q}/{b}/lo", f"Z{i}"),
                                              (f"{q}/{b}/hi", f"A{i}")]))
    return out


def write_def(path):
    origins = leaf_origins()
    nets = _nets()
    u = DBU
    D = ["VERSION 5.8 ;",
         "DIVIDERCHAR \"/\" ;",
         "BUSBITCHARS \"[]\" ;",
         "DESIGN chip ;",
         f"UNITS DISTANCE MICRONS {u} ;",
         f"DIEAREA ( 0 0 ) ( {DIE_W * u} {DIE_H * u} ) ;",
         ""]

    # TRACKS: `X` steps in x, so those tracks carry VERTICAL wires.  The
    # importer installs each statement only on the layer whose own direction
    # matches, so both are emitted per layer exactly as a real DEF does.
    for name, direction, pitch, _w in LAYERS:
        step = int(round(pitch * u))
        for axis, extent in (("X", DIE_W), ("Y", DIE_H)):
            n = int(extent * u // step)
            D.append(f"TRACKS {axis} {step} DO {n} STEP {step} LAYER {name} ;")
    D.append("")
    for axis, extent in (("X", DIE_W), ("Y", DIE_H)):
        D.append(f"GCELLGRID {axis} 0 DO {extent // 10 + 1} STEP {10 * u} ;")
    D.append("")

    D.append(f"COMPONENTS {len(origins)} ;")
    for name in sorted(origins):
        x, y = origins[name]
        # Wrapped across lines on purpose: a `;`-terminated entry may span
        # any number of them, and the reader this exercises is the one that
        # can finally read that.
        D.append(f"  - {name}\n      LEAF\n"
                 f"      + PLACED ( {x * u} {y * u} ) N ;")
    D += ["END COMPONENTS", ""]

    # Ports sit on the ROW of the leaf they talk to — din* beside
    # u_q0/bl/lo on the west edge, dout* beside u_q1/br/hi on the east —
    # so a port net is a short straight run.  Putting them all at one
    # height instead makes the dout nets dive across the die and over
    # u_q1/bl/hi, and a LOW-layer wire over a leaf footprint is a real
    # keepout crossing, not a cosmetic one.
    pin_y = [4 + i * 5 for i in range(NBITS)]          # LEAF pin rows
    ports = [(f"din{i}", "INPUT", 0.5, Q0[1] + pin_y[i]) for i in range(NBITS)]
    ports += [(f"dout{i}", "OUTPUT", DIE_W - 0.5, Q1[1] + QUAD_DY + pin_y[i])
              for i in range(NBITS)]
    D.append(f"PINS {len(ports)} ;")
    for nm, direction, x, y in ports:
        D.append(f"  - {nm} + NET {nm} + DIRECTION {direction} + USE SIGNAL\n"
                 f"      + LAYER M2 ( -250 -250 ) ( 250 250 )\n"
                 f"      + PLACED ( {int(x * u)} {int(y * u)} ) N ;")
    D += ["END PINS", ""]

    # A hard routing blockage in the middle channel: real, and it forces the
    # D0 bus to find its way around rather than straight through.
    D += ["BLOCKAGES 2 ;",
          f"  - LAYER M4 RECT ( {110 * u} {30 * u} ) ( {150 * u} {60 * u} ) ;",
          f"  - PLACEMENT RECT ( {110 * u} {0} ) ( {150 * u} {10 * u} ) ;",
          "END BLOCKAGES", ""]

    # Power straps, which the reader turns into keepouts on their layer.
    D += ["SPECIALNETS 2 ;",
          f"  - VDD ( * VDD ) + ROUTED M6 2000 ( {30 * u} {5 * u} ) "
          f"( * {(DIE_H - 5) * u} )",
          f"      NEW M6 2000 ( {230 * u} {5 * u} ) ( * {(DIE_H - 5) * u} ) "
          "+ USE POWER ;",
          f"  - VSS ( * VSS ) + ROUTED M6 2000 ( {130 * u} {5 * u} ) "
          f"( * {(DIE_H - 5) * u} ) + USE GROUND ;",
          "END SPECIALNETS", ""]

    D.append(f"NETS {len(nets)} ;")
    for nm, conns in nets:
        c = " ".join(f"( {i} {p} )" for i, p in conns)
        D.append(f"  - {nm} {c} + USE SIGNAL ;")
    D += ["END NETS", "", "END DESIGN", ""]

    with open(path, "w") as f:
        f.write("\n".join(D))


def main():
    write_lef(os.path.join(HERE, "chip.lef"))
    write_verilog(os.path.join(HERE, "chip.v"))
    write_def(os.path.join(HERE, "chip.def"))
    print(f"wrote chip.lef, chip.v, chip.def in {HERE}")


if __name__ == "__main__":
    main()
