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
b=[ts for ts in segs if ts.bundle_id==20]
seg1=[t for t in b if t.seg_idx==1][0]; seg2=[t for t in b if t.seg_idx==2][0]
# combined y-span of the whole left trunk
ylo=min(seg1.span_lo,seg2.span_lo); yhi=max(seg1.span_hi,seg2.span_hi)
print(f"B20 left V-trunk: seg1+seg2 both at x={seg1.track_position:.1f}, combined y[{ylo:.0f},{yhi:.0f}], width={seg1.width}")
# Is x~1731 free on M7 for the WHOLE combined span?
def occ_at(xc, ylo, yhi, w):
    half=w/2
    bad=[]
    for o in segs:
        if o.layer!=m7 or not o.placed or o.bundle_id==20: continue
        if o.span_lo<=yhi and ylo<=o.span_hi:  # y overlap
            if abs(o.track_position-xc) < half+o.width/2+1.0:  # x conflict (pitch 1)
                bad.append((o.track_position,o.width,o.bundle_id,o.seg_idx))
    return bad
for xc in (1731, 1800, 2680):
    bad=occ_at(xc, ylo, yhi, 39)
    print(f"  move whole trunk to x={xc}: {'FREE' if not bad else 'BLOCKED by '+str(bad)}")
# WL gain estimate: seg0 H shortening if left end moves from 219.5 to 1731
seg0=[t for t in b if t.seg_idx==0][0]
print(f"\nseg0 H y={seg0.track_position:.0f} x-span[{seg0.span_lo:.0f},{seg0.span_hi:.0f}] (len {seg0.span_hi-seg0.span_lo:.0f})")
print(f"  if trunk slid to x=1731: seg0 would span ~[1731,{seg0.span_hi:.0f}] (len {seg0.span_hi-1731:.0f}) -> save ~{(1731-seg0.span_lo):.0f}")
