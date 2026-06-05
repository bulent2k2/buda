import sqlite3, os, sys

sys.path.insert(0, 'buda_system_v3/src')

# ariane136.buda block definitions (in μm)
BLOCKS = [
    ('cpu_core',    1300, 1150, 1700, 1600),
    ('icache_data', 1194,  625, 1434,  785),
    ('icache_tag',   650,  506,  850,  626),
    ('dcache_data',  515, 1208,  755, 1368),
    ('dcache_tag',   440,  142,  640,  262),
]

# (bus_prefix, width, driver_block, driver_pin_suffix, [(rcv_block, rcv_pin_suffix)])
BUSES = [
    ('clk',          1, 'cpu_core',    'clk_out',      [('icache_tag','clk'), ('icache_data','clk'), ('dcache_data','clk'), ('dcache_tag','clk')]),
    ('icache_addr',  8, 'cpu_core',    'icache_addr',  [('icache_tag','addr'), ('icache_data','addr')]),
    ('icache_rdata',128,'icache_data', 'rdata',        [('cpu_core','icache_rdata')]),
    ('dcache_addr',  8, 'cpu_core',    'dcache_addr',  [('dcache_tag','addr'), ('dcache_data','addr')]),
    ('dcache_wdata', 64,'cpu_core',    'dcache_wdata', [('dcache_data','wdata')]),
    ('dcache_rdata', 64,'dcache_data', 'rdata',        [('cpu_core','dcache_rdata')]),
    ('dcache_ctrl',  4, 'cpu_core',    'dcache_ctrl',  [('dcache_tag','ctrl'), ('dcache_data','ctrl')]),
]

out_path = 'chip_designs/ariane136/ariane_buda5.bdb'
if os.path.exists(out_path): os.remove(out_path)

CREATE_SQL = """
CREATE TABLE component (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, cell TEXT, parent_id INTEGER REFERENCES component(id), depth INTEGER DEFAULT 0, x1 REAL, y1 REAL, x2 REAL, y2 REAL, is_leaf INTEGER DEFAULT 1, is_replicated INTEGER DEFAULT 0);
CREATE TABLE net (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE pin (net_id INTEGER REFERENCES net(id), comp_id INTEGER REFERENCES component(id), pin_name TEXT, dir TEXT, px REAL, py REAL, PRIMARY KEY (net_id, comp_id, pin_name));
CREATE TABLE net_props (net_id INTEGER PRIMARY KEY REFERENCES net(id), hpwl REAL, fanout INTEGER, driver_comp TEXT, bus_name TEXT, bit_index INTEGER, bundle_id INTEGER);
CREATE TABLE busterm (id TEXT PRIMARY KEY, comp_id INTEGER REFERENCES component(id), hier_path TEXT NOT NULL, depth INTEGER, x1 REAL, y1 REAL, x2 REAL, y2 REAL, resolution TEXT DEFAULT 'BLOCK', parent_id TEXT REFERENCES busterm(id));
CREATE TABLE bundle (id TEXT PRIMARY KEY, depth INTEGER DEFAULT 0, strategy TEXT, parent_id TEXT REFERENCES bundle(id), is_replicated INTEGER DEFAULT 0);
CREATE TABLE bundle_net (bundle_id TEXT REFERENCES bundle(id), net_id INTEGER REFERENCES net(id), PRIMARY KEY (bundle_id, net_id));
CREATE TABLE bundle_busterm (bundle_id TEXT REFERENCES bundle(id), busterm_id TEXT REFERENCES busterm(id), PRIMARY KEY (bundle_id, busterm_id));
CREATE TABLE grp (id TEXT PRIMARY KEY, name TEXT NOT NULL, color TEXT, parent_id TEXT REFERENCES grp(id));
CREATE TABLE grp_member (grp_id TEXT REFERENCES grp(id), kind TEXT, ref TEXT, PRIMARY KEY (grp_id, kind, ref));
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

db = sqlite3.connect(out_path)
for stmt in CREATE_SQL.strip().split('\n'):
    db.execute(stmt)

# meta
die_w, die_h = 1750.0, 1750.0
db.execute("INSERT INTO meta VALUES ('units','1')")
db.execute("INSERT INTO meta VALUES ('die_w',?)", (str(die_w),))
db.execute("INSERT INTO meta VALUES ('die_h',?)", (str(die_h),))

# components
comp_id = {}
for i, (name, x1, y1, x2, y2) in enumerate(BLOCKS, 1):
    cell = name.upper()
    db.execute("INSERT INTO component (id,name,cell,parent_id,depth,x1,y1,x2,y2,is_leaf,is_replicated) VALUES (?,?,?,NULL,0,?,?,?,?,1,0)",
               (i, name, cell, float(x1), float(y1), float(x2), float(y2)))
    comp_id[name] = i

# nets + pins
net_id_counter = 0
total_nets = 0

for bus_name, width, drv_block, drv_pin_pfx, rcvs in BUSES:
    for bit in range(width):
        net_id_counter += 1
        nid = net_id_counter
        net_name = f'{bus_name}_{bit}' if width > 1 else bus_name
        db.execute("INSERT INTO net (id,name) VALUES (?,?)", (nid, net_name))
        # driver pin
        cid = comp_id[drv_block]
        pin_name = f'{drv_pin_pfx}_{bit}' if width > 1 else drv_pin_pfx
        # pin x/y: center of block face (driver on right edge)
        bx1,by1,bx2,by2 = next((x1,y1,x2,y2) for n,x1,y1,x2,y2 in BLOCKS if n==drv_block)
        px, py = float(bx2), float((by1+by2)/2)
        db.execute("INSERT INTO pin VALUES (?,?,?,?,?,?)", (nid, cid, pin_name, 'OUTPUT', px, py))
        # receiver pins
        for rcv_block, rcv_pin_pfx in rcvs:
            cid2 = comp_id[rcv_block]
            pin2 = f'{rcv_pin_pfx}_{bit}' if width > 1 else rcv_pin_pfx
            bx1,by1,bx2,by2 = next((x1,y1,x2,y2) for n,x1,y1,x2,y2 in BLOCKS if n==rcv_block)
            px2, py2 = float(bx1), float((by1+by2)/2)
            db.execute("INSERT INTO pin VALUES (?,?,?,?,?,?)", (nid, cid2, pin2, 'INPUT', px2, py2))
        # net_props
        db.execute("INSERT INTO net_props (net_id,hpwl,fanout,driver_comp,bus_name,bit_index) VALUES (?,NULL,?,?,?,?)",
                   (nid, len(rcvs), drv_block, bus_name, bit))
        total_nets += 1

db.commit()
db.close()

print(f"Created {out_path}")
print(f"  {len(BLOCKS)} blocks, {total_nets} nets")

# Verify via buda module
import buda
bdb = buda.BDB(out_path)
comps = bdb.all_components()
nets = bdb.all_nets()
pins = bdb.all_pins()
print(f"  BDB verified: {len(comps)} comps, {len(nets)} nets, {len(pins)} pins")
print(f"  die: {bdb.die_w()} x {bdb.die_h()} um")
