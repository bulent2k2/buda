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

"""Generate the checked-in LEF / DEF / Verilog trio for flow/rv/soc.buda.

A second interchange vehicle beside `flow/def/`, which is deliberately the
SMALLEST design that exercises the path: 4-bit buses, one template per
level, eight leaves.  This one is the other end — a dual-core RV32-shaped
SoC, five levels deep, 32-bit datapath, with the port shapes a real
netlist has and `flow/def/` does not:

  * whole-vector connections at every level (`.a(rs1)`);
  * PART-SELECTS that are not zero-based — `pc[11:2]` onto a 10-bit
    memory address is how a word-addressed memory is actually wired, and
    it is the case where port bit k is net bit k+2;
  * bit-selects off a control bundle (`ctrl[3]`);
  * ports narrower and wider than what is handed to them, so Verilog's
    width adaptation is exercised rather than assumed;
  * a cell instantiated twice (`core`), which is what gives the hier
    pipeline a template to solve once and copy.

**One description, elaborated.**  `flow/def/gen_chip.py` writes its
netlist and its DEF from two hand-kept lists that happen to agree.  That
does not scale past a few dozen nets, and the failure it invites is the
worst one available: a DEF whose instance or pin names no longer match
the netlist merges into silently dropped connections.  Here the design is
described ONCE, as modules and connections, and a small elaborator walks
it to produce the DEF's components and nets.  The two files cannot
disagree because only one of them is authored.

That elaborator is also an independent second opinion on BUDA's own: if
the two resolve a part-select differently, the DEF's net count and the
merged database's disagree, and `import_def_lef` says so.

    python3 flow/rv/gen_soc.py          # rewrite soc.{lef,def,v}
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

DBU = 1000                      # DEF database units per micron
XW = 32                         # datapath width
AW = 10                         # memory address width (word-addressed)
RW = 5                          # register-file address width
CW = 8                          # control bundle width

# Routing stack: (name, direction, pitch µm, width µm).  Real pitches; the
# flow imports at DBU scale so a layout unit is one DBU.
LAYERS = [
    ("M1", "HORIZONTAL", 0.19, 0.07),
    ("M2", "VERTICAL",   0.20, 0.07),
    ("M3", "HORIZONTAL", 0.20, 0.07),
    ("M4", "VERTICAL",   0.40, 0.14),
    ("M5", "HORIZONTAL", 0.40, 0.14),
    ("M6", "VERTICAL",   0.80, 0.30),
    ("M7", "HORIZONTAL", 0.80, 0.30),
]

# ── the cell library ───────────────────────────────────────────────────────
#
# `cls` is the LEF MACRO CLASS, and it is load-bearing: it is what the
# netlist reader asks to tell a hard macro from a standard cell, so the
# memories and the register file must say BLOCK.

IN, OUT = "INPUT", "OUTPUT"

LEAVES = {
    # name        cls      w    h   pins: (name, dir, width)
    "REG32":  ("CORE",    30,  26, [("d", IN, XW), ("q", OUT, XW),
                                    ("en", IN, 1)]),
    "ADD32":  ("CORE",    40,  26, [("a", IN, XW), ("b", IN, XW),
                                    ("y", OUT, XW), ("co", OUT, 1)]),
    "LOG32":  ("CORE",    34,  26, [("a", IN, XW), ("b", IN, XW),
                                    ("op", IN, 2), ("y", OUT, XW)]),
    "MUX32":  ("CORE",    26,  26, [("a", IN, XW), ("b", IN, XW),
                                    ("s", IN, 1), ("y", OUT, XW)]),
    "DEC32":  ("CORE",    40,  26, [("instr", IN, XW), ("ctrl", OUT, CW),
                                    ("rs1", OUT, RW), ("rs2", OUT, RW),
                                    ("rd", OUT, RW)]),
    "IMM32":  ("CORE",    34,  26, [("instr", IN, XW), ("imm", OUT, XW)]),
    "BRU32":  ("CORE",    34,  26, [("a", IN, XW), ("b", IN, XW),
                                    ("op", IN, 3), ("taken", OUT, 1)]),
    "ALN32":  ("CORE",    30,  26, [("din", IN, XW), ("sz", IN, 2),
                                    ("dout", OUT, XW)]),
    # Hard macros.  CLASS BLOCK is what carries them through the
    # DEF+Verilog merge whatever their instance names look like.
    "RF32":   ("BLOCK",   96,  64, [("ra1", IN, RW), ("ra2", IN, RW),
                                    ("wa", IN, RW), ("wd", IN, XW),
                                    ("we", IN, 1),
                                    ("rd1", OUT, XW), ("rd2", OUT, XW)]),
    "SRAM32": ("BLOCK",  120,  84, [("addr", IN, AW), ("wdata", IN, XW),
                                    ("we", IN, 1), ("en", IN, 1),
                                    ("rdata", OUT, XW)]),
}


def leaf_pins(cell):
    return LEAVES[cell][3]


def leaf_size(cell):
    return LEAVES[cell][1], LEAVES[cell][2]


# ── the design, described once ─────────────────────────────────────────────
#
#   soc
#     u_cl                cluster         depth 0
#       u_c0, u_c1        core            depth 1   <- one template, twice
#         u_ifu           ifu             depth 2
#           u_pc  REG32 / u_inc ADD32 / u_nsel MUX32   depth 3   LEAF
#         u_idu           idu             depth 2
#           u_dec DEC32 / u_imm IMM32                  depth 3   LEAF
#         u_exu           exu             depth 2
#           u_alu         alu             depth 3
#             u_add ADD32 / u_log LOG32 / u_ysel MUX32 depth 4   LEAF
#           u_bru BRU32                                depth 3   LEAF
#         u_lsu           lsu             depth 2
#           u_agu ADD32 / u_algn ALN32                 depth 3   LEAF
#         u_rf  RF32                                   depth 2   LEAF (macro)
#       u_xbar            xbar            depth 1
#         u_amux MUX32 / u_dmux MUX32                  depth 2   LEAF
#     u_imem SRAM32 / u_dmem SRAM32                    depth 0   LEAF (macro)
#
# Leaves live at depths 0, 2, 3 and 4 — a routing interface with something
# to do at every level, rather than one bus per level.


class Mod:
    """A module: named ports (dir, width), local wires, child instances."""

    def __init__(self, ports=(), wires=(), insts=()):
        self.ports = {n: (d, w) for n, d, w in ports}
        self.wires = dict(wires)
        self.insts = list(insts)          # (inst_name, cell, {port: expr})


MODULES = {

    # ── fetch ──────────────────────────────────────────────────────────
    "ifu": Mod(
        ports=[("branch_pc", IN, XW), ("take", IN, 1),
               ("iaddr", OUT, AW), ("pc_out", OUT, XW)],
        wires={"pc": XW, "npc": XW, "seq": XW},
        insts=[
            ("u_inc",  "ADD32", {"a": "pc", "b": "pc", "y": "seq"}),
            ("u_nsel", "MUX32", {"a": "seq", "b": "branch_pc",
                                 "s": "take", "y": "npc"}),
            ("u_pc",   "REG32", {"d": "npc", "q": "pc", "en": "take"}),
            # The fetch address: a word-addressed memory does not take the
            # byte address's low two bits, so `pc[11:2]` is what goes to a
            # 10-bit port.  Port bit k is net bit k+2 — the non-zero-based
            # part-select this vehicle exists to carry through the merge.
            ("u_iab",  "ALN32", {"din": "pc[11:2]", "sz": "take",
                                 "dout": "iaddr"}),
            ("u_pcb",  "REG32", {"d": "pc", "q": "pc_out", "en": "take"}),
        ]),

    # ── decode ─────────────────────────────────────────────────────────
    "idu": Mod(
        ports=[("instr", IN, XW), ("ctrl", OUT, CW), ("imm", OUT, XW),
               ("rs1", OUT, RW), ("rs2", OUT, RW), ("rd", OUT, RW)],
        insts=[
            ("u_dec", "DEC32", {"instr": "instr", "ctrl": "ctrl",
                                "rs1": "rs1", "rs2": "rs2", "rd": "rd"}),
            ("u_imm", "IMM32", {"instr": "instr", "imm": "imm"}),
        ]),

    # ── the ALU, a level deeper than anything in flow/def ──────────────
    "alu": Mod(
        ports=[("a", IN, XW), ("b", IN, XW), ("op", IN, 3), ("y", OUT, XW)],
        wires={"sum": XW, "lgc": XW},
        insts=[
            ("u_add",  "ADD32", {"a": "a", "b": "b", "y": "sum"}),
            ("u_log",  "LOG32", {"a": "a", "b": "b", "op": "op[1:0]",
                                 "y": "lgc"}),
            ("u_ysel", "MUX32", {"a": "sum", "b": "lgc", "s": "op[2]",
                                 "y": "y"}),
        ]),

    "exu": Mod(
        ports=[("rs1v", IN, XW), ("rs2v", IN, XW), ("imm", IN, XW),
               ("ctrl", IN, CW), ("res", OUT, XW), ("take", OUT, 1)],
        wires={"opb": XW},
        insts=[
            # ctrl[0] picks register-vs-immediate, ctrl[3:1] is the ALU op,
            # ctrl[6:4] the branch condition: bit- and part-selects off one
            # control bundle, which is how a decoder's output is used.
            ("u_bsel", "MUX32", {"a": "rs2v", "b": "imm", "s": "ctrl[0]",
                                 "y": "opb"}),
            ("u_alu",  "alu",   {"a": "rs1v", "b": "opb", "op": "ctrl[3:1]",
                                 "y": "res"}),
            ("u_bru",  "BRU32", {"a": "rs1v", "b": "rs2v",
                                 "op": "ctrl[6:4]", "taken": "take"}),
        ]),

    # ── load/store ─────────────────────────────────────────────────────
    "lsu": Mod(
        ports=[("base", IN, XW), ("off", IN, XW), ("stdata", IN, XW),
               ("ctrl", IN, CW), ("mrdata", IN, XW),
               ("daddr", OUT, AW), ("wdata", OUT, XW), ("ldata", OUT, XW),
               ("we", OUT, 1)],
        wires={"ea": XW},
        insts=[
            ("u_agu",  "ADD32", {"a": "base", "b": "off", "y": "ea"}),
            # …and the same word-address slice on the data side.
            ("u_dab",  "ALN32", {"din": "ea[11:2]", "sz": "ctrl[5:4]",
                                 "dout": "daddr"}),
            ("u_algn", "ALN32", {"din": "mrdata", "sz": "ctrl[5:4]",
                                 "dout": "ldata"}),
            ("u_st",   "REG32", {"d": "stdata", "q": "wdata",
                                 "en": "ctrl[7]"}),
            # The store-enable comparator sees the whole effective address,
            # which is also what gives `ea`'s bits outside the [11:2] slice
            # a receiver — an adder output going nowhere is legal and would
            # just be 22 nets per core the bundler has nothing to do with.
            ("u_swe",  "BRU32", {"a": "ea", "b": "off",
                                 "op": "ctrl[6:4]", "taken": "we"}),
        ]),

    # ── a core: instantiated twice, so the hier pipeline has a template ─
    "core": Mod(
        ports=[("instr", IN, XW), ("mrdata", IN, XW),
               ("iaddr", OUT, AW), ("daddr", OUT, AW),
               ("wdata", OUT, XW), ("we", OUT, 1), ("pc_out", OUT, XW)],
        wires={"ctrl": CW, "imm": XW, "rs1": RW, "rs2": RW, "rd": RW,
               "rs1v": XW, "rs2v": XW, "res": XW, "ldata": XW, "wb": XW,
               "take": 1},
        insts=[
            ("u_ifu", "ifu", {"branch_pc": "res", "take": "take",
                              "iaddr": "iaddr", "pc_out": "pc_out"}),
            ("u_idu", "idu", {"instr": "instr", "ctrl": "ctrl", "imm": "imm",
                              "rs1": "rs1", "rs2": "rs2", "rd": "rd"}),
            ("u_exu", "exu", {"rs1v": "rs1v", "rs2v": "rs2v", "imm": "imm",
                              "ctrl": "ctrl", "res": "res", "take": "take"}),
            ("u_lsu", "lsu", {"base": "rs1v", "off": "imm", "stdata": "rs2v",
                              "ctrl": "ctrl", "mrdata": "mrdata",
                              "daddr": "daddr", "wdata": "wdata",
                              "ldata": "ldata", "we": "we"}),
            # Writeback picks the ALU result or the loaded word — which is
            # what consumes `ldata`, and what a core actually does.
            ("u_wbsel", "MUX32", {"a": "res", "b": "ldata", "s": "ctrl[2]",
                                  "y": "wb"}),
            # The register file is a hard macro (LEF CLASS BLOCK), reached
            # by 5-bit address buses and 32-bit data.
            ("u_rf", "RF32", {"ra1": "rs1", "ra2": "rs2", "wa": "rd",
                              "wd": "wb", "we": "ctrl[7]",
                              "rd1": "rs1v", "rd2": "rs2v"}),
        ]),

    # ── the crossbar ────────────────────────────────────────────────────
    "xbar": Mod(
        ports=[("a0", IN, AW), ("a1", IN, AW), ("d0", IN, XW), ("d1", IN, XW),
               ("sel", IN, 1), ("maddr", OUT, AW), ("mdata", OUT, XW)],
        insts=[
            # A 10-bit address through a 32-bit mux: the formal is WIDER
            # than the actual on both sides, so 10 bits connect and the
            # upper port bits stay unconnected rather than inventing a[10].
            ("u_amux", "MUX32", {"a": "a0", "b": "a1", "s": "sel",
                                 "y": "maddr"}),
            ("u_dmux", "MUX32", {"a": "d0", "b": "d1", "s": "sel",
                                 "y": "mdata"}),
        ]),

    "cluster": Mod(
        ports=[("instr", IN, XW), ("mrd", IN, XW),
               ("iaddr", OUT, AW), ("maddr", OUT, AW),
               ("mdata", OUT, XW), ("mwe", OUT, 1), ("dbg", OUT, XW)],
        wires={"ia0": AW, "ia1": AW, "da0": AW, "da1": AW,
               "wd0": XW, "wd1": XW, "we1": 1, "pc0": XW, "pc1": XW},
        insts=[
            # Both cores fetch from one instruction memory and share one
            # data port, which is what the two crossbars arbitrate — and
            # what leaves no core output without a consumer.
            ("u_c0", "core", {"instr": "instr", "mrdata": "mrd",
                              "iaddr": "ia0", "daddr": "da0",
                              "wdata": "wd0", "we": "mwe",
                              "pc_out": "pc0"}),
            ("u_c1", "core", {"instr": "instr", "mrdata": "mrd",
                              "iaddr": "ia1", "daddr": "da1",
                              "wdata": "wd1", "we": "we1",
                              "pc_out": "pc1"}),
            ("u_ixb", "xbar", {"a0": "ia0", "a1": "ia1",
                               "d0": "pc0", "d1": "pc1", "sel": "we1",
                               "maddr": "iaddr", "mdata": "dbg"}),
            ("u_dxb", "xbar", {"a0": "da0", "a1": "da1",
                               "d0": "wd0", "d1": "wd1", "sel": "we1",
                               "maddr": "maddr", "mdata": "mdata"}),
        ]),

    # ── the die ────────────────────────────────────────────────────────
    "soc": Mod(
        ports=[("boot", IN, XW), ("dbg", OUT, XW)],
        wires={"instr": XW, "iaddr": AW, "maddr": AW, "mdata": XW,
               "mrd": XW, "mwe": 1},
        insts=[
            ("u_cl", "cluster", {"instr": "instr", "mrd": "mrd",
                                 "iaddr": "iaddr", "maddr": "maddr",
                                 "mdata": "mdata", "mwe": "mwe",
                                 "dbg": "dbg"}),
            ("u_imem", "SRAM32", {"addr": "iaddr", "wdata": "boot",
                                  "we": "mwe", "en": "mwe",
                                  "rdata": "instr"}),
            ("u_dmem", "SRAM32", {"addr": "maddr", "wdata": "mdata",
                                  "we": "mwe", "en": "mwe",
                                  "rdata": "mrd"}),
        ]),
}

TOP = "soc"

# ── the elaborator ─────────────────────────────────────────────────────────
#
# Walks the description from the top, resolving every connection down to
# individual nets, and hands back what the DEF needs: the leaf components
# and, per net, the leaf pins on it.
#
# The resolution rules are Verilog's, and they are the same three BUDA's
# reader applies — which is the point of having a second implementation:
#
#   * a connection is min(formal, actual) bits, aligned at the LOW end;
#   * a port's pins carry its DECLARED indices;
#   * the formal's remaining upper bits connect to NOTHING, rather than to
#     a bit of the actual that does not exist.


def parse_ref(expr):
    """`w` / `w[3]` / `w[11:2]` → (base, lo, width_or_None)."""
    if "[" not in expr:
        return expr, None, None
    base, inner = expr[:expr.index("[")], expr[expr.index("[") + 1:-1]
    if ":" in inner:
        hi, lo = (int(t) for t in inner.split(":"))
        return base, min(hi, lo), abs(hi - lo) + 1
    return base, int(inner), 1


def bit_name(stem, k, width):
    """A width-1 signal has no index in its name; a bus does."""
    return stem if width == 1 else f"{stem}[{k}]"


def elaborate():
    comps = {}                    # leaf path -> cell
    nets = {}                     # net name -> [(leaf path, pin name)]
    containers = []               # (path, module) — for the record

    def walk(mod_name, path, ctx):
        """`ctx` maps this module's own port bit names to global nets
        (None = the bit is connected to nothing)."""
        m = MODULES[mod_name]

        def net_of(base, k):
            """The global net for bit k of local signal `base`."""
            if base in m.ports:
                w = m.ports[base][1]
                return ctx.get(bit_name(base, k, w))
            w = m.wires[base]
            stem = f"{path}/{base}" if path else base
            return bit_name(stem, k, w)

        def sig_width(base):
            return m.ports[base][1] if base in m.ports else m.wires[base]

        for inst, cell, conns in m.insts:
            ipath = f"{path}/{inst}" if path else inst
            is_leaf = cell in LEAVES
            if is_leaf:
                comps[ipath] = cell
                formals = {n: w for n, _d, w in leaf_pins(cell)}
            else:
                containers.append((ipath, cell))
                formals = {n: w for n, (_d, w) in MODULES[cell].ports.items()}

            child = {}
            for port, expr in conns.items():
                fw = formals[port]
                base, sel_lo, sel_w = parse_ref(expr)
                wa = sel_w if sel_w is not None else sig_width(base)
                lo = sel_lo if sel_lo is not None else 0
                n = min(fw, wa)
                for k in range(fw):
                    key = bit_name(port, k, fw)
                    nm = net_of(base, lo + k) if k < n else None
                    child[key] = nm
                    if is_leaf and nm is not None:
                        nets.setdefault(nm, []).append((ipath, key))
            if not is_leaf:
                walk(cell, ipath, child)

    # The top module's own ports are the die's: each bit is a net named
    # after it, which is also what the DEF's PINS section will call them.
    top = MODULES[TOP]
    top_ctx = {}
    for pn, (_d, w) in top.ports.items():
        for k in range(w):
            top_ctx[bit_name(pn, k, w)] = bit_name(pn, k, w)
    walk(TOP, "", top_ctx)
    return comps, nets, containers, top_ctx


# ── geometry ───────────────────────────────────────────────────────────────
#
# A cell is as tall as its pins need.  A 32-bit datapath slice really is
# tens of bit-pitches tall, and inventing a small square for it would put
# 64 pins on a 26 µm edge — geometry the router would then have to believe.

PIN_DY = 1.6                    # µm between adjacent pin centres
EDGE_PAD = 4.0                  # µm of cell above/below the pin stack


def _um(v):
    """A µm length as LEF should carry it: on the manufacturing grid, and
    written the same way every time.  `1.6 * 4 - 0.3` is 6.100000000000001
    in binary floating point, and a file whose bytes depend on which sums
    the writer happened to take cannot be checked in and compared."""
    return f"{round(v, 3):g}"


def cell_geom(cell):
    _cls, w, _h, pins = LEAVES[cell]
    nin = sum(bw for _n, d, bw in pins if d == IN)
    nout = sum(bw for _n, d, bw in pins if d == OUT)
    h = max(nin, nout) * PIN_DY + 2 * EDGE_PAD
    return w, round(h, 3)


def cell_pin_sites(cell):
    """(pin_name, dir, x, y) per BIT, west for inputs and east for outputs —
    the arrangement a datapath slice actually has."""
    _cls, w, _h, pins = LEAVES[cell]
    _cw, h = cell_geom(cell)
    out = []
    for want, x in ((IN, 1.0), (OUT, w - 1.0)):
        n = sum(bw for _nm, d, bw in pins if d == want)
        y = (h - (n - 1) * PIN_DY) / 2.0
        for nm, d, bw in pins:
            if d != want:
                continue
            for k in range(bw):
                out.append((bit_name(nm, k, bw), d, x, round(y, 3)))
                y += PIN_DY
    return out


# Where each leaf sits.  Cells are grouped by the sub-unit that contains
# them, sub-units stack up a column, and the two cores sit side by side —
# a floorplan with the same shape as the hierarchy, which is what makes
# each level's bundles a local thing rather than a scramble.

COL_GAP = 26.0                  # µm between cells in a row
ROW_GAP = 30.0                  # µm between sub-unit rows
CORE_GAP = 90.0                 # µm between the two core columns
MARGIN = 30.0


def _unit_of(path):
    """The sub-unit a leaf belongs to: everything above its own name."""
    return path.rsplit("/", 1)[0] if "/" in path else ""


def leaf_origins():
    comps, _nets, _cont, _tp = elaborate()
    cores = ["u_cl/u_c0", "u_cl/u_c1"]

    # group each core's leaves by sub-unit, in a stable order
    placed = {}
    core_w = []
    for ci, core in enumerate(cores):
        mine = {p: c for p, c in comps.items() if p.startswith(core + "/")}
        units = {}
        for p in sorted(mine):
            units.setdefault(_unit_of(p), []).append(p)
        rows = [units[u] for u in sorted(units)]
        placed[core] = rows
        core_w.append(max(
            sum(cell_geom(mine[p])[0] for p in r) + COL_GAP * (len(r) - 1)
            for r in rows))

    x0 = MARGIN
    core_x, y_top = [], MARGIN
    for ci, core in enumerate(cores):
        core_x.append(x0)
        x0 += core_w[ci] + CORE_GAP

    origins = {}
    core_h = 0.0
    for ci, core in enumerate(cores):
        y = y_top
        for row in placed[core]:
            x = core_x[ci]
            rh = 0.0
            for p in row:
                w, h = cell_geom(comps[p])
                origins[p] = (x, y)
                x += w + COL_GAP
                rh = max(rh, h)
            y += rh + ROW_GAP
        core_h = max(core_h, y - y_top)

    # The crossbar sits between the cores and the memories…
    xb = [p for p in sorted(comps) if "/u_ixb/" in p or "/u_dxb/" in p]
    xw = max(cell_geom(comps[p])[0] for p in xb)
    y = y_top
    for p in xb:
        origins[p] = (x0, y)
        y += cell_geom(comps[p])[1] + ROW_GAP
    x0 += xw + CORE_GAP

    # …and the two memories to their right, stacked.
    y = y_top
    for p in ("u_imem", "u_dmem"):
        origins[p] = (x0, y)
        y += cell_geom(comps[p])[1] + ROW_GAP
    x0 += cell_geom(comps["u_imem"])[0]

    die_w = x0 + MARGIN
    die_h = max(core_h, y - y_top) + 2 * MARGIN
    return origins, comps, round(die_w, 3), round(die_h, 3)


# ── Verilog ────────────────────────────────────────────────────────────────

def _decl(kind, name, width):
    return f"  {kind} {'' if width == 1 else f'[{width - 1}:0] '}{name};"


def write_verilog(path):
    V = ["// Generated by flow/rv/gen_soc.py -- do not edit.",
         "//",
         "// A dual-core RV32-shaped SoC, five levels deep.  It is a WIRING",
         "// vehicle: the structure, port widths and bus connections are those",
         "// of a small core, and the arithmetic is not — `u_inc` adds pc to",
         "// itself where a real fetch unit would add 4, because this design",
         "// is routed, never simulated.  Nothing downstream cares what the",
         "// bits mean; everything downstream cares how many there are and",
         "// where they go.",
         "//",
         "// The leaf modules are declared as blackbox stubs WITH their port",
         "// widths, which is what a synthesis blackbox for a macro looks",
         "// like.  The widths are load-bearing: a port whose width is not",
         "// declared is one bit wide as far as any reader can tell, and a",
         "// 32-bit memory bus wired to it would arrive one bit wide.",
         ""]
    for cell in sorted(LEAVES):
        pins = leaf_pins(cell)
        V.append(f"module {cell} ({', '.join(n for n, _d, _w in pins)});")
        for n, d, w in pins:
            V.append(_decl("input" if d == IN else "output", n, w))
        V += ["endmodule", ""]

    # Hierarchical modules, deepest first so a reader meets a module after
    # the ones it instantiates.
    order = ["alu", "ifu", "idu", "exu", "lsu", "xbar", "core", "cluster",
             "soc"]
    for mn in order:
        m = MODULES[mn]
        V.append(f"module {mn} ({', '.join(m.ports)});")
        for pn, (d, w) in m.ports.items():
            V.append(_decl("input" if d == IN else "output", pn, w))
        for wn, w in sorted(m.wires.items()):
            V.append(_decl("wire", wn, w))
        for inst, cell, conns in m.insts:
            args = ", ".join(f".{p}({e})" for p, e in conns.items())
            V.append(f"  {cell} {inst} ({args});")
        V += ["endmodule", ""]
    # newline="\n": these files are COMMITTED and byte-compared against a
    # regeneration (test_*_hier_flow's match-their-generator tests).  Python's
    # text mode writes os.linesep, so on Windows the regeneration came out
    # CRLF against an LF checkout and the whole file diffed (measured,
    # windows-validate run 31, the mingw lane's forced-LF checkout).  A
    # generator whose output is committed must emit ONE spelling everywhere.
    with open(path, "w", newline="\n") as f:
        f.write("\n".join(V))


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
    for i in range(1, len(LAYERS)):
        L += [f"LAYER VIA{i}", "  TYPE CUT ;", f"END VIA{i}", ""]

    for cell in sorted(LEAVES):
        cls = LEAVES[cell][0]
        w, h = cell_geom(cell)
        L += [f"MACRO {cell}",
              f"  CLASS {cls} ;",
              "  ORIGIN 0 0 ;",
              f"  SIZE {w} BY {h} ;",
              "  SYMMETRY X Y ;"]
        for pn, d, x, y in cell_pin_sites(cell):
            L += [f"  PIN {pn}",
                  f"    DIRECTION {d} ;",
                  "    USE SIGNAL ;",
                  "    PORT",
                  "      LAYER M1 ;",
                  f"      RECT {_um(x - 0.3)} {_um(y - 0.3)}"
                  f" {_um(x + 0.3)} {_um(y + 0.3)} ;",
                  "    END",
                  f"  END {pn}"]
        for nm, use in (("VDD", "POWER"), ("VSS", "GROUND")):
            y0 = 0 if use == "GROUND" else h - 1
            L += [f"  PIN {nm}",
                  "    DIRECTION INOUT ;",
                  f"    USE {use} ;",
                  "    PORT",
                  "      LAYER M1 ;",
                  f"      RECT 0 {y0} {w} {y0 + 1} ;",
                  "    END",
                  f"  END {nm}"]
        # The macros block metal over their whole core; the datapath cells
        # block a middle stripe, as a placed cell does.
        if cls == "BLOCK":
            L += ["  OBS", "    LAYER M2 ;",
                  f"    RECT 2 2 {w - 2} {h - 2} ;", "  END"]
        else:
            L += ["  OBS", "    LAYER M2 ;",
                  f"    RECT 3 {round(h / 3, 3)} {w - 3} {round(2 * h / 3, 3)} ;",
                  "  END"]
        L += [f"END {cell}", ""]
    L += ["END LIBRARY", ""]
    with open(path, "w", newline="\n") as f:
        f.write("\n".join(L))


# ── DEF ────────────────────────────────────────────────────────────────────

def write_def(path):
    origins, comps, DIE_W, DIE_H = leaf_origins()
    _c, nets, _cont, _tp = elaborate()
    u = DBU

    D = ["VERSION 5.8 ;",
         "DIVIDERCHAR \"/\" ;",
         "BUSBITCHARS \"[]\" ;",
         f"DESIGN {TOP} ;",
         f"UNITS DISTANCE MICRONS {u} ;",
         f"DIEAREA ( 0 0 ) ( {int(DIE_W * u)} {int(DIE_H * u)} ) ;",
         ""]

    for name, _dir, pitch, _w in LAYERS:
        step = int(round(pitch * u))
        for axis, extent in (("X", DIE_W), ("Y", DIE_H)):
            n = int(extent * u // step)
            D.append(f"TRACKS {axis} {step} DO {n} STEP {step} LAYER {name} ;")
    D.append("")

    D.append(f"COMPONENTS {len(origins)} ;")
    for name in sorted(origins):
        x, y = origins[name]
        D.append(f"  - {name} {comps[name]}\n"
                 f"      + PLACED ( {int(x * u)} {int(y * u)} ) N ;")
    D += ["END COMPONENTS", ""]

    # Die ports sit beside the block they talk to — the same rule
    # flow/def/ states and for the same reason.  A port stacked at some
    # convenient height instead makes its net dive across the die and over
    # whatever is in the way, and a low-layer wire over a leaf footprint is
    # a real keepout crossing.  Here the partner is looked up rather than
    # assumed: each port bit takes the row of the leaf pin it feeds, on
    # whichever vertical edge is nearer that leaf.
    top = MODULES[TOP]
    partner = {}
    for nm, pins in nets.items():
        if pins:
            partner[nm] = pins[0][0]
    ports = []
    for pn, (d, w) in top.ports.items():
        for k in range(w):
            bn = bit_name(pn, k, w)
            leaf = partner.get(bn)
            if leaf is None:                       # a port with no load
                x, y = (0.5 if d == IN else DIE_W - 0.5), DIE_H / 2
            else:
                lx, ly = origins[leaf]
                lw, lh = cell_geom(comps[leaf])
                x = DIE_W - 0.5 if lx + lw / 2 > DIE_W / 2 else 0.5
                # spread the bits over the partner's height, in bit order
                y = ly + (k + 0.5) * lh / w
            ports.append((bn, "INPUT" if d == IN else "OUTPUT",
                          x, round(min(max(y, 1.0), DIE_H - 1.0), 3)))
    D.append(f"PINS {len(ports)} ;")
    for nm, d, x, y in ports:
        D.append(f"  - {nm} + NET {nm} + DIRECTION {d} + USE SIGNAL\n"
                 f"      + LAYER M2 ( -250 -250 ) ( 250 250 )\n"
                 f"      + PLACED ( {int(x * u)} {int(y * u)} ) N ;")
    D += ["END PINS", ""]

    # A hard blockage in the channel between the cores and the memories,
    # and a placement-density cap — both things a real DEF carries and the
    # reader has to tell apart (only the first is a routing keepout).
    ch_x = origins["u_imem"][0] - CORE_GAP
    D += ["BLOCKAGES 2 ;",
          f"  - LAYER M5 RECT ( {int(ch_x * u)} {int(DIE_H * 0.35 * u)} ) "
          f"( {int((ch_x + 40) * u)} {int(DIE_H * 0.55 * u)} ) ;",
          f"  - PLACEMENT RECT ( {int(ch_x * u)} 0 ) "
          f"( {int((ch_x + 40) * u)} {int(DIE_H * 0.1 * u)} ) ;",
          "END BLOCKAGES", ""]

    # Power straps on the top layer, which the reader turns into keepouts.
    straps = [round(DIE_W * f, 3) for f in (0.18, 0.5, 0.82)]
    D.append(f"SPECIALNETS 2 ;")
    D.append(f"  - VDD ( * VDD ) + ROUTED M6 2000 ( {int(straps[0] * u)} "
             f"{int(5 * u)} ) ( * {int((DIE_H - 5) * u)} )")
    D.append(f"      NEW M6 2000 ( {int(straps[2] * u)} {int(5 * u)} ) "
             f"( * {int((DIE_H - 5) * u)} ) + USE POWER ;")
    D.append(f"  - VSS ( * VSS ) + ROUTED M6 2000 ( {int(straps[1] * u)} "
             f"{int(5 * u)} ) ( * {int((DIE_H - 5) * u)} ) + USE GROUND ;")
    D += ["END SPECIALNETS", ""]

    # The nets, straight off the elaboration: every leaf pin on them, plus
    # the die PIN where the net is a top-level port bit.
    port_bits = {nm for nm, _d, _x, _y in ports}
    D.append(f"NETS {len(nets)} ;")
    for nm in sorted(nets):
        conns = [f"( {i} {p} )" for i, p in sorted(nets[nm])]
        if nm in port_bits:
            conns.insert(0, f"( PIN {nm} )")
        D.append(f"  - {nm} {' '.join(conns)} + USE SIGNAL ;")
    D += ["END NETS", "", "END DESIGN", ""]

    with open(path, "w", newline="\n") as f:
        f.write("\n".join(D))


def main():
    write_lef(os.path.join(HERE, "soc.lef"))
    write_verilog(os.path.join(HERE, "soc.v"))
    write_def(os.path.join(HERE, "soc.def"))
    origins, _c, w, h = leaf_origins()
    _c2, nets, cont, _t = elaborate()
    print(f"wrote soc.lef, soc.v, soc.def in {HERE}")
    print(f"  die {w} x {h} um, {len(origins)} leaves, {len(cont)} containers, "
          f"{len(nets)} nets")


if __name__ == "__main__":
    main()
