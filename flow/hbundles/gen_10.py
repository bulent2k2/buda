#!/usr/bin/env python3
"""Generate flow/hbundles/10_chip_units_blocks_leaf.buda — a 4-level
hierarchy stress flow (chip / units / blocks / leaves) expanding on
08_cross_level.buda. See the generated header for the full design."""

import io
out = io.StringIO()
W = out.write

# ── Geometry ────────────────────────────────────────────────
# leaf_cell  110x130
# blk_cell  250x230   lo@(10,50) hi@(130,50)
# unit_cell 270x500   bb@(10,10) bt@(10,260)
# chip_cell 1680x1120 u0..u4 bottom row y=30, u5..u9 top row y=590
#                     x = 30 + col*330
# left@(0,0)  right@(1880,0): central channel x in [1680,1880]

buses = []   # (name, bits, drv, rcv, comment)

# D3: intra-blk leaf<->leaf, both directions, all 40 blk instances
for c in ("left", "right"):
    for i in range(10):
        for b in ("bt", "bb"):
            tag = f"{c[0]}{i}{b[1]}"
            buses.append((f"b_fw_{tag}", 4, f"{c}/u{i}/{b}/lo.out", f"{c}/u{i}/{b}/hi.in", None))
            buses.append((f"b_bw_{tag}", 4, f"{c}/u{i}/{b}/hi.out", f"{c}/u{i}/{b}/lo.in", None))

# D2: intra-unit blk<->blk (leaf endpoints), both vertical directions, 20 units
for c in ("left", "right"):
    for i in range(10):
        tag = f"{c[0]}{i}"
        buses.append((f"v_dn_{tag}", 6, f"{c}/u{i}/bt/lo.out", f"{c}/u{i}/bb/lo.in", None))
        buses.append((f"v_up_{tag}", 6, f"{c}/u{i}/bb/hi.out", f"{c}/u{i}/bt/hi.in", None))

# D1: intra-chip unit->unit chains (leaf endpoints)
# horizontal chains within each row: u0->u1->..->u4, u5->..->u9
for c in ("left", "right"):
    for i in (0, 1, 2, 3, 5, 6, 7, 8):
        buses.append((f"h_{c[0]}{i}{i+1}", 8,
                      f"{c}/u{i}/bt/hi.out", f"{c}/u{i+1}/bt/lo.in", None))
    # vertical column links: u_i (bottom row) -> u_{i+5} (top row)
    for i in range(5):
        buses.append((f"c_{c[0]}{i}{i+5}", 8,
                      f"{c}/u{i}/bt/lo.out", f"{c}/u{i+5}/bb/lo.in", None))

# D0: cross-chip unit_i(left) -> unit_i(right), top and bottom blk rows
for i in range(10):
    buses.append((f"x_t{i}", 8, f"left/u{i}/bt/hi.out", f"right/u{i}/bt/lo.in", None))
    buses.append((f"x_b{i}", 8, f"left/u{i}/bb/hi.out", f"right/u{i}/bb/lo.in", None))

# Cross-level buses: endpoints at different depths
xl = [
    ("xl_u2l", 4, "left/u4.out",        "right/u0/bt/lo.in",  "unit->leaf  cross-chip"),
    ("xl_l2u", 4, "right/u5/bb/hi.out", "left/u9.in",         "leaf->unit  cross-chip"),
    ("xl_c2l", 4, "left.out",           "right/u2/bb/hi.in",  "chip->leaf  cross-chip"),
    ("xl_l2c", 4, "left/u7/bt/hi.out",  "right.in",           "leaf->chip  cross-chip"),
    ("xl_b2l", 4, "left/u0/bt.out",     "left/u1/bb/lo.in",   "blk->leaf   intra-chip"),
    ("xl_l2b", 4, "left/u3/bb/hi.out",  "left/u2/bt.in",      "leaf->blk   intra-chip"),
    ("xl_u2b", 4, "right/u1.out",       "right/u6/bb.in",     "unit->blk   intra-chip"),
    ("xl_b2u", 4, "left/u8/bb.out",     "left/u7.in",         "blk->unit   intra-chip"),
    ("xl_c2u", 4, "right.out",          "left/u5.in",         "chip->unit  cross-chip"),
    ("xl_uu", 4, "left/u6.out",         "right/u6.in",        "unit->unit  cross-chip (unit-level pins)"),
]
buses += xl

n_buses = len(buses)
n_bits = sum(b[1] for b in buses)

W(f"""# ============================================================
# hbundles/10_chip_units_blocks_leaf.buda
#
# 4-level hierarchy stress flow expanding on 08_cross_level.buda:
# one more hierarchy level and ~12x the bus count.
#
# Hierarchy (depths 0..3):
#   chip_cell  1680x1120  (depth 0, 2 instances: left, right)
#   unit_cell   270x500   (depth 1, 10 per chip: u0..u4 bottom row,
#                                   u5..u9 top row, 5 columns x 330)
#   blk_cell    250x230   (depth 2, 2 per unit: bt top, bb bottom)
#   leaf_cell   110x130   (depth 3, 2 per blk: lo left, hi right)
#   => 2 chips, 20 units, 40 blks, 80 leaves
#
# Placement:
#   left  (0,0)-(1680,1120)    right (1880,0)-(3560,1120)
#   central channel x in [1680,1880]; 60-wide channels between
#   unit columns/rows inside each chip.
#
# Buses ({n_buses} total, {n_bits} bits):
#   D3 intra-blk    lo<->hi both dirs, all 40 blks   80 x 4b = 320 bits
#   D2 intra-unit   bt<->bb both dirs, all 20 units  40 x 6b = 240 bits
#   D1 intra-chip   row chains u_i->u_i+1 (8/chip)
#                   + column links u_i->u_i+5 (5/chip) 26 x 8b = 208 bits
#   D0 cross-chip   left/u_i -> right/u_i, bt+bb rows 20 x 8b = 160 bits
#   Cross-level     all depth pairings (see below)    10 x 4b =  40 bits
#
# Cross-level buses:
""")
for name, bits, drv, rcv, cmt in xl:
    W(f"#   {name:<8} {drv:<22}-> {rcv:<22}{cmt}\n")
W("""#
# Pipeline result (observed):
#   176 HBundles (D0:26 D1:30 D2:40 D3:80) — exactly one per bus; the
#   D3 intra-blk templates expand to their 40 instances, giving 176
#   wrappers after expansion (one per physical bus).
#   741 candidates; all 176 bundles planned STRICT (overflow=0, no
#   rip-ups; see the per-level summary).  196 segments placed.
#   check_design nuts: Success — no violations found.
#
#   Residual (~0.8% of bits): 10 NUTS track overlaps and 8 unplaced
#   dnuts bits.  These are known stage-4 packing gaps, not planner
#   errors — see docs/future/nuts_packing_gaps.md:
#     (a) planner band accounting ignores inter-bus pitch, so a band
#         loaded close to capacity may not pack in NUTS;
#     (b) non-TOP face clamping treats ancestor (chip) blocks as
#         endpoint containment, under-pricing low-layer trunks;
#     (c) post-placement span adjustments stretch detour stubs/trunks
#         into bands the planner never charged.
#
# Generated by gen_10.py — edit that script, not this file.
# ============================================================

source ../tracks.buda

corner_margin dx 5 dy 5
set_min_stub_length 2

# ── BDB: cell sizes ────────────────────────────────────────
open_bdb :memory:

add_cell chip_cell  1680 1120
add_cell unit_cell   270  500
add_cell blk_cell    250  230
add_cell leaf_cell   110  130

# ── BDB: cell hierarchy ────────────────────────────────────
""")
for col in range(5):
    W(f"add_inst_to_cell  chip_cell  u{col}   unit_cell  {30 + col*330:4d}   30\n")
for col in range(5):
    W(f"add_inst_to_cell  chip_cell  u{col+5}   unit_cell  {30 + col*330:4d}  590\n")
W("""
add_inst_to_cell  unit_cell  bb    blk_cell   10   10
add_inst_to_cell  unit_cell  bt    blk_cell   10  260

add_inst_to_cell  blk_cell   lo    leaf_cell  10   50
add_inst_to_cell  blk_cell   hi    leaf_cell  130  50

# ── BDB: place top-level instances ────────────────────────
add_inst left   chip_cell  -      0    0
add_inst right  chip_cell  -   1880    0

# ── Derive busterms (all four levels) ──────────────────────
derive_busterms 3

# ── Populate floorplan with blocks at all routing levels ───
add_blocks_from_bdb 0
add_blocks_from_bdb 1 skip
add_blocks_from_bdb 2 skip
add_blocks_from_bdb 3 skip

# ── Buses (written to BDB) ─────────────────────────────────
bdb_net_mode on

""")
last_prefix = None
for name, bits, drv, rcv, cmt in buses:
    prefix = name.split("_")[0]
    if prefix != last_prefix:
        W("\n")
        last_prefix = prefix
    line = f"add_bus {name}[{bits}]  {drv}  {rcv}"
    if cmt:
        line = f"{line:<58}# {cmt}"
    W(line + "\n")
W("""
# ── HBundle pipeline ───────────────────────────────────────
run_hier_bundler depth 3

generate_hier_topologies

run_planner hier 5

run_nuts

check_design nuts

run_detailed_nuts
check_design dnuts

visualize
""")

path = __file__.rsplit("/", 1)[0] + "/10_chip_units_blocks_leaf.buda"
with open(path, "w") as f:
    f.write(out.getvalue())
print(f"wrote {path}: {n_buses} buses, {n_bits} bits")
