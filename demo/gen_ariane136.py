#!/usr/bin/env python3
"""Generate ariane136.buda from the ariane136_flow.bdb SQLite database."""

import sqlite3

DB = '/tmp/ariane136_flow.bdb'
OUT = '/Users/ben/src/buda/demo/ariane136.buda'

db = sqlite3.connect(DB)

# ── cluster definitions (parent component IDs) ──────────────────────────────
clusters = {
    'icache_data': [164, 166, 168, 170],
    'icache_tag':  [165, 167, 169, 171],
    'dcache_data': [181, 183, 185, 187, 189, 191, 193, 195],
    'dcache_tag':  [182, 184, 186, 188, 190, 192, 194, 196, 197],
}

def cluster_bbox(parent_ids):
    ph = ','.join('?' for _ in parent_ids)
    row = db.execute(f"""
        SELECT MIN(c.x1), MIN(c.y1), MAX(c.x2), MAX(c.y2)
        FROM component c WHERE c.parent_id IN ({ph}) AND c.x1 > 0
    """, parent_ids).fetchone()
    return row[0], row[1], row[2], row[3]

lines = []
def emit(*args):
    lines.append(' '.join(str(a) for a in args))

# ── layers ────────────────────────────────────────────────────────────────
emit('# ariane136 full-chip BUDA flow')
emit('# 136 fakeram45_256x16 macros, 5 functional blocks, 7 buses')
emit()
emit('def_layer 4 M4 H TOP 30')
emit('def_layer 5 M5 V TOP 30')
emit('def_layer 6 M6 H TOP 30')
emit('def_layer 7 M7 V TOP 30')
emit()

# ── corner margin ─────────────────────────────────────────────────────────
emit('corner_margin dx 40 dy 40')
emit()

# ── blocks ────────────────────────────────────────────────────────────────
# cpu_core: top-right empty area (0 macros)
emit('add_block cpu_core 1300 1150 1700 1600')
emit()

for bname, pids in clusters.items():
    x1, y1, x2, y2 = cluster_bbox(pids)
    emit(f'add_block {bname} {x1:.0f} {y1:.0f} {x2:.0f} {y2:.0f}')
emit()

# ── buses ──────────────────────────────────────────────────────────────────
# 1. clock: cpu_core → all SRAM clusters (multicast)
emit('add_bus clk[1]',
     'cpu_core.clk_out',
     'icache_tag.clk,icache_data.clk,dcache_data.clk,dcache_tag.clk')
emit()

# 2. icache address bus (8-bit SRAM row addr): cpu_core → icache tag + data (multicast)
emit('add_bus icache_addr[8]',
     'cpu_core.icache_addr',
     'icache_tag.addr,icache_data.addr')
emit()

# 3. icache read data (128-bit instruction word): icache_data → cpu_core
emit('add_bus icache_rdata[128]',
     'icache_data.rdata',
     'cpu_core.icache_rdata')
emit()

# 4. dcache address bus (8-bit SRAM row addr): cpu_core → dcache tag + data (multicast)
emit('add_bus dcache_addr[8]',
     'cpu_core.dcache_addr',
     'dcache_tag.addr,dcache_data.addr')
emit()

# 5. dcache write data (64-bit): cpu_core → dcache_data
emit('add_bus dcache_wdata[64]',
     'cpu_core.dcache_wdata',
     'dcache_data.wdata')
emit()

# 6. dcache read data (64-bit): dcache_data → cpu_core
emit('add_bus dcache_rdata[64]',
     'dcache_data.rdata',
     'cpu_core.dcache_rdata')
emit()

# 7. dcache control signals (write-enable + valid): cpu_core → dcache tag + data (multicast)
emit('add_bus dcache_ctrl[4]',
     'cpu_core.dcache_ctrl',
     'dcache_tag.ctrl,dcache_data.ctrl')
emit()

# ── pipeline ─────────────────────────────────────────────────────────────
emit('run_bundler STRICT')
emit()
emit('generate_topologies')
emit()
emit('set_planner_param kCong 2.0')
emit('run_planner 200')
emit()
emit('run_nuts')
emit()
emit('visualize')

# ── write output ─────────────────────────────────────────────────────────
with open(OUT, 'w') as f:
    f.write('\n'.join(lines) + '\n')

print(f"Written {OUT}")
print()
print("Block bboxes:")
print(f"  cpu_core:    (1300,1150)-(1700,1600)")
for bname, pids in clusters.items():
    x1, y1, x2, y2 = cluster_bbox(pids)
    print(f"  {bname:<14}: ({x1:.0f},{y1:.0f})-({x2:.0f},{y2:.0f})")

db.close()
