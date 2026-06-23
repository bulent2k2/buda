import os, sys
ROOT = os.path.abspath('.')
for p in ('build','src','tools'): sys.path.insert(0, os.path.join(ROOT,p))
from buda_cli import BudaSession
script = os.path.abspath(sys.argv[1])
os.chdir(os.path.dirname(script))
s = BudaSession()
for line in open(script):
    line = line.strip()
    if not line or line.startswith('#') or line in ('visualize',): continue
    if line.startswith('run_detailed_nuts') or line.startswith('check_connectivity'): continue
    s.do_command(line)
print("OVERLAPS", s.nuts_result.num_overlaps)
for ts in s.nuts_result.segments:
    print((ts.bundle_id, ts.seg_idx), (ts.net_pull, round(ts.interval_lo,1),
        round(ts.interval_hi,1), round(ts.track_position,1)))
