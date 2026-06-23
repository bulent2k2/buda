import os, sys
ROOT = os.path.abspath('.')
for p in ('build','src','tools'): sys.path.insert(0, os.path.join(ROOT,p))
from buda_cli import BudaSession
script = os.path.abspath(sys.argv[1])
os.chdir(os.path.dirname(script))
s = BudaSession()
for line in open(script):
    line=line.strip()
    if not line or line.startswith('#'): continue
    if line.startswith(('check_connectivity','visualize','run_detailed_nuts')): continue
    s.do_command(line)
    if line=='run_nuts': break
segs = s.nuts_result.segments
want = set(int(x) for x in sys.argv[2].split(',')) if len(sys.argv)>2 else None
PITCH = 1.0
def occ_for(ts):
    """occupied (lo,hi) intervals on ts's layer whose span overlaps ts."""
    out=[]
    for o in segs:
        if o is ts or not o.placed or o.layer!=ts.layer: continue
        if o.bundle_id==ts.bundle_id: continue
        if o.span_lo<=ts.span_hi and ts.span_lo<=o.span_hi:
            h=o.width/2.0
            out.append((o.track_position-h, o.track_position+h))
    return sorted(out)
def free_at(center, half, occ):
    lo,hi=center-half,center+half
    for (olo,ohi) in occ:
        if lo < ohi+PITCH and olo-PITCH < hi: return False
    return True
def best_toward_pull(ts):
    half=ts.width/2.0
    c_lo=ts.interval_lo+half; c_hi=ts.interval_hi-half
    if c_lo>c_hi: return ts.track_position
    pull = ts.interval_hi if ts.net_pull>0 else ts.interval_lo
    pull = min(max(pull,c_lo),c_hi)
    occ=occ_for(ts)
    # candidate positions: pull, and just-past each occupied edge, clamped
    cands=[pull, c_lo, c_hi]
    for (olo,ohi) in occ:
        cands += [ohi+PITCH+half, olo-PITCH-half]
    cands=[min(max(c,c_lo),c_hi) for c in cands]
    feas=[c for c in cands if free_at(c,half,occ)]
    if not feas: return ts.track_position
    # closest-to-pull feasible
    return min(feas, key=lambda c: abs(c-pull))
print(f"{'seg':12} {'L':>2} {'np':>3} {'iv':>20} {'pull':>7} {'cur':>8} {'best':>8} {'gain':>7}")
tot_cur=tot_best=0.0
for ts in segs:
    if ts.net_pull==0 or not ts.placed: continue
    pull = ts.interval_hi if ts.net_pull>0 else ts.interval_lo
    cur=abs(ts.track_position-pull)
    bp=best_toward_pull(ts); best=abs(bp-pull)
    tot_cur+=cur; tot_best+=best
    if want is None or ts.bundle_id in want:
        if want is not None or cur-best>50:
            print(f"B{ts.bundle_id} s{ts.seg_idx:<7} {ts.layer:>2} {ts.net_pull:>+3} "
                  f"[{ts.interval_lo:7.0f},{ts.interval_hi:7.0f}] {pull:7.0f} "
                  f"{ts.track_position:8.1f} {bp:8.1f} {cur-best:7.0f}")
print(f"\nTOTAL pulled deviation: current={tot_cur:.0f} achievable-by-greedy-slide={tot_best:.0f} "
      f"recoverable={tot_cur-tot_best:.0f}")
