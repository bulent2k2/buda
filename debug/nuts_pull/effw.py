import os, sys
ROOT=os.path.abspath('.')
for p in ('build','src','tools'): sys.path.insert(0, os.path.join(ROOT,p))
from buda_cli import BudaSession
os.chdir('flow/big_data_test')
s=BudaSession()
for line in open('tc3a_flat.buda'):
    line=line.strip()
    if not line or line.startswith('#'): continue
    if line.startswith(('check_connectivity','visualize','run_detailed_nuts')): continue
    s.do_command(line)
    if line=='run_nuts': break
m7=s._layer_name_map['M7']
ls=s.layer_stack
print("attrs on session:", [a for a in dir(s) if 'grid' in a.lower() or 'layer' in a.lower() or 'stack' in a.lower()])
for bits in (1,8,12):
    try: print(f"eff_bus_width(bits={bits}, base=12) on M7 = {ls.eff_bus_width(bits,12.0,m7):.3f}")
    except Exception as e: print("eff err:", e)
# B20 seg2 width directly
for ts in s.nuts_result.segments:
    if ts.bundle_id==20 and ts.seg_idx==2:
        print(f"B20 seg2 actual width={ts.width}")
