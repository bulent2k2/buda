#!/usr/bin/env python3
"""Generate ariane136_l2b.buda — 25 individual depth-2 sram_block groups as BUDA blocks,
CONVERGENT bundling, aggregated rdata buses, hybrid non-overlapping blocks."""

import sqlite3, re

DB  = '/tmp/ariane136_flow.bdb'
OUT = '/Users/ben/src/buda/demo/ariane136_l2b.buda'

db = sqlite3.connect(DB)

rows = db.execute("""
    SELECT p.id, p.name,
           MIN(c.x1), MIN(c.y1), MAX(c.x2), MAX(c.y2),
           AVG((c.x1+c.x2)/2) as cx, AVG((c.y1+c.y2)/2) as cy
    FROM component p
    JOIN component c ON c.parent_id = p.id
    WHERE p.depth=2 AND c.x1 > 0
    GROUP BY p.id
    ORDER BY p.name
""").fetchall()

def short_name(name):
    if 'i_icache' in name and 'data_sram' in name:
        return f"ic_data_{re.search(r'\[(\d+)\]', name).group(1)}"
    if 'i_icache' in name and 'tag_sram' in name:
        return f"ic_tag_{re.search(r'\[(\d+)\]', name).group(1)}"
    if 'i_nbdcache' in name and 'data_sram' in name:
        return f"dc_data_{re.search(r'\[(\d+)\]', name).group(1)}"
    if 'i_nbdcache' in name and 'tag_sram' in name:
        return f"dc_tag_{re.search(r'\[(\d+)\]', name).group(1)}"
    if 'valid_dirty' in name:
        return 'dc_vld'
    return None

raw = {}   # short_name -> (x1, y1, x2, y2, cx, cy)
for pid, name, x1, y1, x2, y2, cx, cy in rows:
    sn = short_name(name)
    if sn:
        raw[sn] = (x1, y1, x2, y2, cx, cy)

BUF = 40

def centroid_strip(members):
    """Non-overlapping strips along dominant centroid axis, centroid bbox + BUF."""
    n = len(members)
    cxs = [raw[m][4] for m in members]; cys = [raw[m][5] for m in members]
    bx1, bx2 = min(cxs)-BUF, max(cxs)+BUF
    by1, by2 = min(cys)-BUF, max(cys)+BUF
    result = {}
    if max(cxs)-min(cxs) >= max(cys)-min(cys):
        ordered = sorted(members, key=lambda m: raw[m][4])
        sw = (bx2-bx1)/n
        for i,m in enumerate(ordered): result[m] = (bx1+i*sw, by1, bx1+(i+1)*sw, by2)
    else:
        ordered = sorted(members, key=lambda m: raw[m][5])
        sh = (by2-by1)/n
        for i,m in enumerate(ordered): result[m] = (bx1, by1+i*sh, bx2, by1+(i+1)*sh)
    return result

ic_data = [f'ic_data_{i}' for i in range(4)]
ic_tag  = [f'ic_tag_{i}'  for i in range(4)]
dc_data = [f'dc_data_{i}' for i in range(8)]
dc_tag  = [f'dc_tag_{i}'  for i in range(8)]
dc_all  = dc_data + dc_tag + ['dc_vld']
ic_all  = ic_data + ic_tag
all_sram = ic_all + dc_all

blocks = {}
blocks.update(centroid_strip(ic_data))
blocks.update(centroid_strip(ic_tag))
blocks.update(centroid_strip(dc_tag))
# dc_data: centroid X × union Y strips — wide enough for 96-unit buses
ic_tag_y_top = max(raw[m][5] for m in ic_tag) + BUF
dc_bx1 = min(raw[m][4] for m in dc_data) - BUF
dc_bx2 = max(raw[m][4] for m in dc_data) + BUF
dc_by1 = ic_tag_y_top + 1
dc_by2 = max(raw[m][3] for m in dc_data)
ordered_dc = sorted(dc_data, key=lambda m: raw[m][5])
sh = (dc_by2 - dc_by1) / 8
for i, m in enumerate(ordered_dc):
    blocks[m] = (dc_bx1, dc_by1 + i*sh, dc_bx2, dc_by1 + (i+1)*sh)
_vld = raw['dc_vld']
blocks['dc_vld'] = (_vld[4]-60, _vld[5]-80, _vld[4]+60, _vld[5]+80)

lines = []
def E(*args): lines.append(' '.join(str(a) for a in args))
def NL(): lines.append('')

E('# ariane136_l2b.buda — depth-2 blocks, CONVERGENT, aggregated rdata buses')
E('# 25 sram_block groups + cpu_core = 26 blocks; 0 block overlaps')
E('# Blocks: same hybrid strip assignment as ariane136_l2.buda')
E('# Under CONVERGENT: dc_wdata+dc_rdata merge → 1 bundle; dc_addr+dc_ctrl merge → 1 bundle')
NL()
E('def_layer 4 M4 H TOP 30')
E('def_layer 5 M5 V TOP 30')
E('def_layer 6 M6 H TOP 30')
E('def_layer 7 M7 V TOP 30')
NL()
E('corner_margin dx 20 dy 20')
NL()
E('add_block cpu_core 1300 1150 1700 1600')
NL()
E('# icache: 4 ways × (data_sram + tag_sram)')
for b in ic_data + ic_tag:
    x1, y1, x2, y2 = blocks[b]
    E(f'add_block {b} {x1:.0f} {y1:.0f} {x2:.0f} {y2:.0f}')
NL()
E('# dcache: 8 ways × (data_sram + tag_sram) + valid_dirty')
for b in dc_data + dc_tag + ['dc_vld']:
    x1, y1, x2, y2 = blocks[b]
    E(f'add_block {b} {x1:.0f} {y1:.0f} {x2:.0f} {y2:.0f}')
NL()

# ── Buses ─────────────────────────────────────────────────────────────────
# clk broadcast
E('add_bus clk[1] cpu_core.clk_out', ','.join(f'{b}.clk' for b in all_sram))
NL()
# icache address: cpu_core → all 8 icache blocks
E('add_bus ic_addr[8] cpu_core.ic_addr', ','.join(f'{b}.addr' for b in ic_all))
NL()
# icache rdata (aggregated): cpu_core as driver → 4 ic_data blocks
# BUDA routes topology; direction reversal is fine for abstract planning
E('add_bus ic_rdata[128] cpu_core.ic_rd', ','.join(f'{b}.rdata' for b in ic_data))
NL()
# dcache address: cpu_core → all 17 dcache blocks
E('add_bus dc_addr[8] cpu_core.dc_addr', ','.join(f'{b}.addr' for b in dc_all))
NL()
# dcache ctrl: cpu_core → all 17 dcache blocks (CONVERGENT → merges with dc_addr)
E('add_bus dc_ctrl[4] cpu_core.dc_ctrl', ','.join(f'{b}.ctrl' for b in dc_all))
NL()
# dcache write data: cpu_core → 8 dc_data blocks
E('add_bus dc_wdata[64] cpu_core.dc_wdata', ','.join(f'{b}.wdata' for b in dc_data))
NL()
# dcache rdata (aggregated): cpu_core → 8 dc_data blocks
# Same receiver set as dc_wdata → CONVERGENT merges them into one 128-bit bundle
E('add_bus dc_rdata[64] cpu_core.dc_rd', ','.join(f'{b}.rdata' for b in dc_data))
NL()

E('run_bundler CONVERGENT')
NL()
E('generate_topologies')
NL()
E('set_planner_param kCong 2.0')
E('run_planner 200')
NL()
E('run_nuts')
NL()
E('run_planner post_nuts V 300 1000')
NL()
E('visualize')

with open(OUT, 'w') as f:
    f.write('\n'.join(lines) + '\n')

print(f"Written {OUT}")
print("\nExpected CONVERGENT bundles (5):")
print("  B1: clk          cpu_core → 25 sram blocks        (1 bit)")
print("  B2: ic_addr      cpu_core → 8 icache blocks       (8 bits)")
print("  B3: ic_rdata     cpu_core → 4 ic_data blocks     (128 bits)")
print("  B4: dc_addr+ctrl cpu_core → 17 dcache blocks     (12 bits) ← MERGED")
print("  B5: dc_wdata+rdata cpu_core → 8 dc_data blocks  (128 bits) ← MERGED")
db.close()
