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
# B20 bit count: how many nets in the bundle / bus width
for bw in s.bundles:
    if bw.input.original_bundle.id==20:
        ob=bw.input.original_bundle
        print("B20 bundle width field:", bw.input.width)
        print("B20 net count:", len(ob.net_names) if hasattr(ob,'net_names') else '?')
        try: print("net_names:", list(ob.net_names))
        except Exception as e: print("net_names n/a", e)
# M7 routing grid pattern
ls=s.layer_stack
print("\nM7 eff_bus_width for bits=1,4,8,12 base=12:")
for bits in (1,4,8,12):
    try: print(f"  bits={bits}: {ls.eff_bus_width(bits, 12.0, m7):.2f}")
    except Exception as e: print("  err", e); break
# track pattern on M7
rg=s.routing_grid
try:
    gp=rg.get_layer_grid(m7).global_pattern
    print(f"\nM7 pattern: unit_pitch={gp.unit_pitch():.2f} signal_density={gp.signal_density():.3f} dilution={gp.dilution_factor():.3f}")
    print("  slots:", [(sl.type, sl.width, sl.space_after) for sl in gp.slots])
    # tracks in [1533,1929] (the free gap around x~1731), y irrelevant for V pattern
    tr=gp.tracks_in_range(1533,1929)
    sig=[t for t in tr]
    print(f"  tracks in x[1533,1929]: {len(tr)} total")
except Exception as e:
    print("pattern err", e)
