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
segs=s.nuts_result.segments
m7=s._layer_name_map['M7']
print("=== B20 all segments ===")
for ts in segs:
    if ts.bundle_id!=20: continue
    print(f"  seg{ts.seg_idx} L{ts.layer} {'H' if ts.horiz else 'V'} "
          f"span[{ts.span_lo:.0f},{ts.span_hi:.0f}] iv[{ts.interval_lo:.0f},{ts.interval_hi:.0f}] "
          f"width={ts.width:.2f} np={ts.net_pull} pos={ts.track_position:.1f}")
# Focus: B20 seg2 (V on M7). What occupies M7 around x in [200,2700], y in its span?
b20s2=[ts for ts in segs if ts.bundle_id==20 and ts.seg_idx==2][0]
print(f"\n=== M7 occupancy overlapping B20 seg2 span y[{b20s2.span_lo:.0f},{b20s2.span_hi:.0f}] ===")
occ=[]
for o in segs:
    if o.layer!=m7 or not o.placed or o.bundle_id==20: continue
    if o.span_lo<=b20s2.span_hi and b20s2.span_lo<=o.span_hi:
        occ.append((o.track_position, o.width, o.bundle_id, o.seg_idx, o.span_lo, o.span_hi))
occ.sort()
print(f"  {len(occ)} M7 segments overlap B20s2's y-span; their x-tracks (pos,width,bundle):")
for pos,w,b,si,sl,sh in occ:
    if 200<=pos<=3000 or True:
        mark = "  <-- in [200,2700]" if 200<=pos<=2700 else ""
        print(f"    x={pos:7.1f} w={w:5.2f} B{b}s{si} yspan[{sl:.0f},{sh:.0f}]{mark}")
